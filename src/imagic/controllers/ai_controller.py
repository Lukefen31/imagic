"""AI controller — orchestrates automatic culling and duplicate detection.

Implements the 4-stage pipeline's "AI Analyst" and "Decision Engine" stages:
1. Score each ``PENDING`` photo via ``QualityScorer``.
2. Compute perceptual hashes via ``DuplicateDetector``.
3. Apply keep / trash thresholds to produce ``KEEP`` or ``TRASH`` decisions.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import List, Optional

from imagic.ai.base_analyzer import BaseAnalyzer
from imagic.ai.duplicate_detector import DuplicateDetector
from imagic.ai.image_describer import ImageDescriptionAnalyzer
from imagic.ai.perceptual_scorer import PerceptualScorer
from imagic.ai.quality_scorer import QualityScorer
from imagic.models.database import DatabaseManager
from imagic.models.enums import PhotoStatus
from imagic.models.photo import Photo
from imagic.services.auto_crop import analyze_crop
from imagic.services.metadata_reader import read_metadata_pillow, read_metadata_rawpy
from imagic.services.pp3_generator import analyze_photo_histogram
from imagic.services.profile_selector import classify_scene
from imagic.services.task_queue import TaskItem, TaskQueue
from imagic.services.thumbnail_generator import generate_thumbnail

logger = logging.getLogger(__name__)


class AIController:
    """Runs AI analysis and applies the decision engine.

    Args:
        task_queue: Background task queue.
        quality_scorer: Scorer instance (defaults to built-in).
        duplicate_detector: Detector instance (defaults to built-in).
        keep_threshold: Minimum score to auto-mark a photo as ``KEEP``.
        trash_threshold: Maximum score below which a photo is ``TRASH``.
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        quality_scorer: Optional[BaseAnalyzer] = None,
        duplicate_detector: Optional[DuplicateDetector] = None,
        perceptual_scorer: Optional[PerceptualScorer] = None,
        image_describer: Optional[ImageDescriptionAnalyzer] = None,
        keep_threshold: float = 0.50,
        trash_threshold: float = 0.35,
        duplicate_hash_threshold: int = 10,
    ) -> None:
        self._queue = task_queue
        self._scorer = quality_scorer or QualityScorer()
        self._dup_detector = duplicate_detector or DuplicateDetector(
            threshold=duplicate_hash_threshold,
        )
        self._perceptual = perceptual_scorer or PerceptualScorer()
        self._describer = image_describer or ImageDescriptionAnalyzer()
        self._keep = keep_threshold
        self._trash = trash_threshold

        # Thread-safe progress state (polled by UI timer).
        self._progress_lock = threading.Lock()
        self._progress_current = 0
        self._progress_total = 0
        self._progress_file = ""
        self._progress_phase = ""  # "thumbnails", "models", "scoring", "done"

    def get_progress(self) -> dict:
        """Return a snapshot of the current analysis progress (thread-safe)."""
        with self._progress_lock:
            return {
                "current": self._progress_current,
                "total": self._progress_total,
                "file": self._progress_file,
                "phase": self._progress_phase,
            }

    def _set_progress(self, current: int = 0, total: int = 0,
                      file: str = "", phase: str = "") -> None:
        with self._progress_lock:
            self._progress_current = current
            self._progress_total = total
            if file:
                self._progress_file = file
            if phase:
                self._progress_phase = phase

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def analyse_pending(self, strictness: float = 0.0) -> TaskItem:
        """Score all ``PENDING`` photos and apply the decision engine.

        Args:
            strictness: Extra threshold tightening (0.0 = default,
                each +0.05 raises keep and trash thresholds).

        Returns:
            ``TaskItem`` tracking the background analysis.
        """
        return self._queue.submit(
            self._analyse_and_decide,
            strictness,
            name="AI analysis (pending)",
        )

    def detect_duplicates(self) -> TaskItem:
        """Run duplicate detection across the entire library.

        Returns:
            ``TaskItem`` tracking the background scan.
        """
        return self._queue.submit(
            self._duplicate_scan,
            name="Duplicate detection",
        )

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------
    def _analyse_and_decide(self, strictness: float = 0.0) -> dict:
        """Score PENDING photos and classify them (worker thread)."""
        db = DatabaseManager.get()
        session = db.get_session()
        stats = {"analysed": 0, "keep": 0, "trash": 0, "errors": 0}
        try:
            photos: List[Photo] = (
                session.query(Photo)
                .filter(Photo.status.in_([
                    PhotoStatus.PENDING.value,
                    PhotoStatus.ERROR.value,
                ]))
                .all()
            )
            if not photos:
                logger.info("No pending photos to analyse.")
                return stats

            logger.info("Analysing %d pending photos…", len(photos))
            self._set_progress(0, len(photos), phase="thumbnails")

            # Ensure thumbnails exist before scoring — avoids Pillow
            # failing on RAW files and reduces processing time.
            thumb_dir = Path.home() / ".imagic" / "thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumbs_generated = 0
            for photo in photos:
                if not photo.thumbnail_path or not Path(photo.thumbnail_path).is_file():
                    raw = Path(photo.file_path)
                    thumb_path = thumb_dir / f"{raw.stem}_thumb.jpg"
                    result = generate_thumbnail(raw, thumb_path)
                    if result:
                        photo.thumbnail_path = str(result)
                        thumbs_generated += 1
            if thumbs_generated:
                session.commit()
                logger.info("Auto-generated %d thumbnails before analysis.", thumbs_generated)

            for photo in photos:
                photo.status = PhotoStatus.ANALYZING.value
            session.commit()

            # Pre-load all models once before the per-photo loop.
            self._set_progress(phase="models")
            self._scorer.ensure_model()
            try:
                self._perceptual.ensure_model()
            except Exception:
                logger.warning("PerceptualScorer model load failed — skipping perceptual scoring.")
            try:
                self._describer.ensure_model()
            except Exception:
                logger.warning("ImageDescriptionAnalyzer model load failed — skipping descriptions.")

            self._set_progress(0, len(photos), phase="scoring")
            for idx, photo in enumerate(photos):
                # Prefer thumbnail for speed; fall back to raw.
                image_path = (
                    Path(photo.thumbnail_path)
                    if photo.thumbnail_path and Path(photo.thumbnail_path).is_file()
                    else Path(photo.file_path)
                )

                self._set_progress(idx, len(photos), file=photo.file_name)

                result = self._scorer.analyse(image_path)

                if result.ok and result.score is not None:
                    photo.quality_score = result.score
                    photo.status = PhotoStatus.CULLED.value

                    # Store per-metric cull reasons as JSON.
                    cull_reasons = result.labels.get("cull_reasons")
                    if cull_reasons:
                        photo.cull_reasons = json.dumps(cull_reasons)

                    # --- Perceptual quality (pyiqa) ---
                    try:
                        perc = self._perceptual.analyse(image_path)
                        if perc.ok and perc.score is not None:
                            photo.perceptual_score = perc.score
                            # Blend: 70 % hand-tuned + 30 % perceptual.
                            photo.quality_score = round(
                                0.70 * result.score + 0.30 * perc.score, 4,
                            )
                    except Exception:
                        logger.debug("Perceptual scoring skipped for %s", photo.file_name)

                    # --- Image description (Florence-2) ---
                    try:
                        desc = self._describer.analyse(image_path)
                        if desc.ok and desc.labels:
                            caption = desc.labels.get("detailed_caption") or desc.labels.get("caption")
                            if caption:
                                photo.ai_caption = caption
                    except Exception:
                        logger.debug("Image description skipped for %s", photo.file_name)

                    # Read EXIF metadata from RAW file (or thumbnail fallback).
                    try:
                        meta = read_metadata_rawpy(Path(photo.file_path))
                        if meta is None:
                            meta = read_metadata_pillow(image_path)
                        if meta:
                            photo.exif_make = meta.make
                            photo.exif_model = meta.model
                            photo.exif_date_taken = meta.date_taken
                            photo.exif_iso = meta.iso
                            photo.exif_focal_length = meta.focal_length
                            photo.exif_aperture = meta.aperture
                            photo.exif_shutter_speed = meta.shutter_speed
                    except Exception:
                        logger.debug("EXIF read failed for %s", photo.file_name)

                    # Auto-classify scene and assign a processing preset.
                    try:
                        scene = classify_scene(
                            iso=photo.exif_iso,
                            aperture=photo.exif_aperture,
                            focal_length=photo.exif_focal_length,
                            shutter_speed=photo.exif_shutter_speed,
                            thumbnail_path=(
                                Path(photo.thumbnail_path)
                                if photo.thumbnail_path
                                else None
                            ),
                        )
                        photo.scene_preset = scene.preset
                        logger.info(
                            "Scene for %s: %s (%s)",
                            photo.file_name, scene.preset, scene.reason,
                        )
                    except Exception:
                        logger.debug("Scene classification failed for %s", photo.file_name)

                    # Per-photo histogram analysis (for PP3 generator).
                    try:
                        from imagic.services.pp3_generator import PhotoMetrics
                        pm = PhotoMetrics()
                        if photo.thumbnail_path and Path(photo.thumbnail_path).is_file():
                            analyze_photo_histogram(
                                Path(photo.thumbnail_path), pm,
                            )
                    except Exception:
                        logger.debug("Histogram analysis failed for %s", photo.file_name)

                    # Auto-crop analysis.
                    try:
                        crop_path = (
                            Path(photo.thumbnail_path)
                            if photo.thumbnail_path and Path(photo.thumbnail_path).is_file()
                            else Path(photo.file_path)
                        )
                        crop_result = analyze_crop(crop_path)
                        if crop_result.is_significant and crop_result.confidence > 0.1:
                            photo.auto_crop_data = json.dumps({
                                "x": crop_result.x,
                                "y": crop_result.y,
                                "w": crop_result.w,
                                "h": crop_result.h,
                                "original_w": crop_result.original_w,
                                "original_h": crop_result.original_h,
                                "reason": crop_result.reason,
                                "confidence": round(crop_result.confidence, 3),
                            })
                            logger.info(
                                "Auto-crop for %s: %dx%d → %dx%d (%.0f%% conf: %s)",
                                photo.file_name,
                                crop_result.original_w, crop_result.original_h,
                                crop_result.w, crop_result.h,
                                crop_result.confidence * 100,
                                crop_result.reason,
                            )
                    except Exception:
                        logger.debug("Auto-crop analysis failed for %s", photo.file_name)

                    # Decision engine — apply learned threshold adjustments.
                    from imagic.ai.feedback_learner import get_learner
                    adj = get_learner().get_score_adjustments()
                    effective_keep = self._keep + adj.get("keep_shift", 0.0) + strictness
                    effective_trash = self._trash + adj.get("trash_shift", 0.0) + strictness
                    # Clamp so they stay sensible
                    effective_keep = min(effective_keep, 0.98)
                    effective_trash = min(effective_trash, effective_keep - 0.05)
                    if adj.get("sample_count", 0) >= 5 or strictness > 0:
                        logger.debug(
                            "Learned thresholds: keep=%.3f trash=%.3f (strictness=%.2f, %d samples)",
                            effective_keep, effective_trash, strictness, adj.get("sample_count", 0),
                        )

                    # Hard-reject images with critical quality defects
                    # regardless of score or learned threshold shifts.
                    _force_trash = False
                    cull_reasons = result.labels.get("cull_reasons") or []
                    for entry in cull_reasons:
                        verdict = (entry.get("verdict") or "").lower()
                        if "severe blur" in verdict or "significant blur" in verdict:
                            _force_trash = True
                            break
                        if "extremely dark" in verdict or "very dark" in verdict:
                            _force_trash = True
                            break

                    if _force_trash:
                        photo.status = PhotoStatus.TRASH.value
                        stats["trash"] += 1
                    elif result.score >= effective_keep:
                        photo.status = PhotoStatus.KEEP.value
                        stats["keep"] += 1
                    elif result.score <= effective_trash:
                        photo.status = PhotoStatus.TRASH.value
                        stats["trash"] += 1
                    stats["analysed"] += 1
                else:
                    photo.status = PhotoStatus.ERROR.value
                    photo.error_message = result.error or "Unknown analysis error"
                    stats["errors"] += 1
                    logger.warning("Analysis error for %s: %s", photo.file_name, result.error)

            session.commit()
            self._set_progress(len(photos), len(photos), phase="done")
            logger.info("AI analysis complete: %s", stats)
        except Exception:
            session.rollback()
            logger.exception("AI analyse batch failed.")
        finally:
            session.close()
        return stats

    def _duplicate_scan(self) -> dict:
        """Build perceptual hashes and find duplicate groups."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photos: List[Photo] = session.query(Photo).filter(
                Photo.status.notin_([
                    PhotoStatus.TRASH.value,
                    PhotoStatus.ERROR.value,
                ])
            ).all()

            hash_map: dict[str, str] = {}
            time_map: dict[str, object] = {}
            for photo in photos:
                image_path = (
                    Path(photo.thumbnail_path)
                    if photo.thumbnail_path and Path(photo.thumbnail_path).is_file()
                    else Path(photo.file_path)
                )
                result = self._dup_detector.analyse(image_path)
                if result.ok and "phash" in result.labels:
                    photo.perceptual_hash = result.labels["phash"]
                    hash_map[photo.file_path] = result.labels["phash"]
                    time_map[photo.file_path] = photo.exif_date_taken

            session.commit()

            groups = self._dup_detector.group_duplicates(hash_map, time_map=time_map)

            # Rank each group by quality so the best shot is first.
            score_map: dict[str, float] = {}
            for photo in photos:
                if photo.quality_score is not None:
                    score_map[photo.file_path] = photo.quality_score
            ranked_groups = [
                DuplicateDetector.rank_burst_group(g, score_map) for g in groups
            ]

            logger.info("Duplicate scan: %d groups found.", len(ranked_groups))
            return {
                "groups": ranked_groups,
                "total_duplicates": sum(len(g) for g in ranked_groups),
            }
        except Exception:
            session.rollback()
            logger.exception("Duplicate scan failed.")
            return {"groups": [], "total_duplicates": 0}
        finally:
            session.close()
