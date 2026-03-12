"""Processing bridge — wraps imagic AI/services for the web API.

Provides thin wrappers around the existing imagic modules so the FastAPI
routes don't need to know about internal class details.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

RAW_EXTENSIONS = {
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf",
    ".rw2", ".raf", ".pef", ".srw", ".raw", ".3fr",
    ".iiq", ".rwl", ".mrw", ".x3f",
}

# Ensure the imagic source package is importable.
_SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---------------------------------------------------------------------------
# Quality analysis
# ---------------------------------------------------------------------------


def analyse_quality(image_path: Path) -> Dict[str, Any]:
    """Run the imagic QualityScorer on a single image.

    Returns a dict with ``overall_score``, ``metrics`` list, and ``verdict``.
    """
    try:
        from imagic.ai.quality_scorer import QualityScorer

        scorer = QualityScorer()
        result = scorer.analyse(image_path)

        if result.error:
            return {
                "overall_score": 0.0,
                "metrics": [],
                "verdict": "error",
                "error": result.error,
            }

        cull_reasons = result.labels.get("cull_reasons", [])
        overall = round(result.score, 3)
        verdict = (
            "keep" if overall >= 0.55 else "review" if overall >= 0.35 else "trash"
        )

        return {
            "overall_score": overall,
            "metrics": cull_reasons,
            "verdict": verdict,
        }
    except Exception as exc:
        logger.exception("Quality analysis failed for %s", image_path)
        return {
            "overall_score": 0.0,
            "metrics": [],
            "verdict": "error",
            "error": str(exc),
        }


def prepare_analysis_source(image_path: Path, max_size: tuple[int, int] = (1600, 1600)) -> Path:
    """Create a lightweight cached JPEG for analysis and return its path.

    Large originals and RAWs are expensive to analyse directly in a constrained
    web worker. A cached JPEG keeps analysis cheaper and repeatable.
    """
    cache_dir = image_path.parent / ".analysis_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{image_path.stem}.jpg"

    if cache_path.exists() and cache_path.stat().st_mtime >= image_path.stat().st_mtime:
        return cache_path

    if image_path.suffix.lower() in RAW_EXTENSIONS:
        generated = generate_display_thumbnail(
            image_path,
            cache_path,
            max_size=max_size,
            quality=82,
            raw_embedded_only=True,
        )
        if not generated:
            generated = generate_display_thumbnail(
                image_path,
                cache_path,
                max_size=max_size,
                quality=82,
                raw_embedded_only=False,
            )
        return cache_path if generated and cache_path.exists() else image_path

    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            with tempfile.NamedTemporaryFile(
                dir=cache_path.parent,
                suffix=".jpg",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
            try:
                img.save(str(temp_path), "JPEG", quality=82, optimize=True)
                temp_path.replace(cache_path)
            finally:
                temp_path.unlink(missing_ok=True)
    except Exception:
        logger.exception("Analysis source preparation failed for %s", image_path)
        return image_path

    return cache_path if cache_path.exists() else image_path


def load_cached_analysis_result(image_path: Path) -> Optional[Dict[str, Any]]:
    """Load a cached analysis result for an image if it is still fresh."""
    cache_dir = image_path.parent / ".analysis_cache"
    result_path = cache_dir / f"{image_path.stem}.json"
    if not result_path.exists():
        return None

    try:
        if result_path.stat().st_mtime < image_path.stat().st_mtime:
            return None
        return json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Analysis cache read failed for %s", image_path)
        return None


def save_cached_analysis_result(image_path: Path, result: Dict[str, Any]) -> None:
    """Persist a per-image analysis result for reuse across refresh/retry."""
    cache_dir = image_path.parent / ".analysis_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    result_path = cache_dir / f"{image_path.stem}.json"
    try:
        result_path.write_text(json.dumps(result), encoding="utf-8")
    except Exception:
        logger.exception("Analysis cache write failed for %s", image_path)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def detect_duplicates(image_paths: List[Path]) -> List[List[str]]:
    """Find duplicate groups among the given images.

    Returns a list of groups, where each group is a list of file stems
    that are visually similar.
    """
    if len(image_paths) < 2:
        return []

    try:
        import imagehash

        hashes: Dict[str, Any] = {}
        for path in image_paths:
            try:
                img = Image.open(path)
                h = imagehash.phash(img, hash_size=8)
                hashes[path.stem] = h
            except Exception:
                continue

        # Group by Hamming distance ≤ 10
        threshold = 10
        stems = list(hashes.keys())
        parent = {s: s for s in stems}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(len(stems)):
            for j in range(i + 1, len(stems)):
                if hashes[stems[i]] - hashes[stems[j]] <= threshold:
                    union(stems[i], stems[j])

        groups: Dict[str, List[str]] = {}
        for s in stems:
            root = find(s)
            groups.setdefault(root, []).append(s)

        # Only return groups with 2+ members
        return [g for g in groups.values() if len(g) > 1]

    except Exception as exc:
        logger.exception("Duplicate detection failed")
        return []


# ---------------------------------------------------------------------------
# Auto-crop
# ---------------------------------------------------------------------------


def suggest_crop(image_path: Path) -> Dict[str, Any]:
    """Get auto-crop suggestion for an image.

    Returns a dict with crop coordinates, reason, and confidence.
    """
    try:
        from imagic.services.auto_crop import analyze_crop

        result = analyze_crop(image_path)
        return {
            "crop": {
                "x": result.x,
                "y": result.y,
                "w": result.w,
                "h": result.h,
            },
            "original": {
                "w": result.original_w,
                "h": result.original_h,
            },
            "reason": result.reason,
            "confidence": round(result.confidence, 3),
            "is_significant": result.is_significant,
        }
    except Exception as exc:
        logger.exception("Auto-crop failed for %s", image_path)
        return {
            "crop": {"x": 0, "y": 0, "w": 0, "h": 0},
            "original": {"w": 0, "h": 0},
            "reason": f"Error: {exc}",
            "confidence": 0.0,
            "is_significant": False,
        }


# ---------------------------------------------------------------------------
# Grade previews
# ---------------------------------------------------------------------------

# Grade names used in the web app
GRADE_NAMES = [
    "natural",
    "film_warm",
    "film_cool",
    "moody",
    "vibrant",
    "cinematic",
]


def generate_grade_previews(
    image_path: Path, session_id: str
) -> List[Dict[str, str]]:
    """Generate colour-grade preview thumbnails for an image.

    Saves preview images to the session directory and returns a list of
    dicts with grade name and download URL.
    """
    try:
        from imagic.services.grade_preview import render_grade_preview
        from imagic.services.pp3_generator import GRADES

        session_dir = image_path.parent
        file_stem = image_path.stem
        previews = []

        for grade_name in GRADE_NAMES:
            grade = GRADES.get(grade_name)
            if grade is None:
                continue

            preview_img = render_grade_preview(image_path, grade)
            if preview_img is None:
                continue

            preview_filename = f"{file_stem}_grade_{grade_name}.jpg"
            preview_path = session_dir / preview_filename
            preview_img.save(preview_path, "JPEG", quality=85)

            previews.append({
                "grade": grade_name,
                "url": f"/api/download/{session_id}/{preview_filename}",
            })

        return previews

    except Exception as exc:
        logger.exception("Grade preview generation failed for %s", image_path)
        return []


def generate_display_thumbnail(
    image_path: Path,
    output_path: Path,
    max_size: tuple[int, int] = (1200, 1200),
    quality: int = 85,
    raw_embedded_only: bool = False,
) -> bool:
    """Generate a browser-friendly JPEG thumbnail for standard or RAW images."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if image_path.suffix.lower() in RAW_EXTENSIONS:
            from imagic.services.thumbnail_generator import generate_thumbnail

            return generate_thumbnail(
                image_path,
                output_path,
                max_size=max_size,
                quality=quality,
                embedded_only=raw_embedded_only,
            ) is not None

        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            with tempfile.NamedTemporaryFile(
                dir=output_path.parent,
                suffix=".jpg",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
            try:
                img.save(str(temp_path), "JPEG", quality=quality, optimize=True)
                temp_path.replace(output_path)
            finally:
                temp_path.unlink(missing_ok=True)
        return True
    except Exception:
        logger.exception("Display thumbnail generation failed for %s", image_path)
        return False


# ---------------------------------------------------------------------------
# Native export (server-side — no RawTherapee needed)
# ---------------------------------------------------------------------------


def native_export(
    image_path: Path,
    session_id: str,
    grade_name: str = "natural",
    quality_data: Optional[Dict[str, Any]] = None,
    manual_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Export a photo using the Python-native processing engine.

    Returns a dict with the download URL on success, or an error message.
    """
    try:
        from imagic.services.native_processor import NativeProcessor
        from imagic.services.pp3_generator import GRADES, PhotoMetrics

        processor = NativeProcessor(jpeg_quality=95)
        session_dir = image_path.parent
        output_dir = session_dir / "exports"

        # Build metrics from quality analysis data (if available).
        metrics = PhotoMetrics()
        if quality_data:
            for m in quality_data.get("metrics", []):
                metric = m.get("metric", "").lower()
                score = m.get("score")
                if score is None:
                    continue
                if metric == "sharpness":
                    metrics.sharpness = score
                elif metric == "exposure":
                    metrics.exposure = score
                elif metric == "noise":
                    metrics.noise = score
                elif metric == "detail":
                    metrics.detail = score
                elif metric == "composition":
                    metrics.composition = score

        grade = GRADES.get(grade_name, GRADES.get("natural"))
        overrides = manual_overrides or {}

        result = processor.process(
            input_path=image_path,
            output_dir=output_dir,
            metrics=metrics,
            color_grade=grade,
            output_format="jpg",
            manual_overrides=overrides,
        )

        if result.success:
            export_filename = f"{image_path.stem}.jpg"
            return {
                "success": True,
                "url": f"/api/download/{session_id}/exports/{export_filename}",
                "duration_s": result.duration_s,
            }
        else:
            return {"success": False, "error": result.stderr}

    except Exception as exc:
        logger.exception("Native export failed for %s", image_path)
        return {"success": False, "error": str(exc)}
