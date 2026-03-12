"""Export pipeline management.

Coordinates the full export workflow for a batch of photos:
1. Read the photo record from the database.
2. Resolve the XMP sidecar (if any).
3. Call the CLI tool (darktable-cli / rawtherapee-cli).
4. Update the database status (``EXPORTED`` or ``ERROR``).
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Optional

from imagic.models.database import DatabaseManager
from imagic.models.enums import ExportFormat, PhotoStatus
from imagic.models.photo import Photo
from imagic.services.cli_orchestrator import CLIOrchestrator, CLIResult
from imagic.services.editor_style_presets import get_editor_style_overrides, merge_editor_overrides
from imagic.services.native_processor import NativeProcessor, NativeResult
from imagic.services.pp3_generator import (
    GRADES,
    PhotoMetrics,
    analyze_photo_histogram,
    generate_pp3,
    metrics_from_photo,
)
from imagic.services.profile_selector import get_preset_path
from imagic.utils.path_utils import find_sidecar

logger = logging.getLogger(__name__)


class ExportService:
    """Handles exporting images through external CLI tools.

    Args:
        cli: A configured ``CLIOrchestrator``.
        output_dir: Root directory for exported files.
        export_format: Desired output format.
        jpeg_quality: Quality setting for JPEG exports.
        default_pp3: Optional path to a default RawTherapee PP3 profile.
    """

    def __init__(
        self,
        cli: CLIOrchestrator,
        output_dir: Path,
        export_format: ExportFormat = ExportFormat.JPEG,
        jpeg_quality: int = 95,
        default_pp3: Optional[Path] = None,
        max_file_size_kb: int = 0,
    ) -> None:
        self._cli = cli
        self._output_dir = output_dir
        self._export_format = export_format
        self._jpeg_quality = jpeg_quality
        self._default_pp3 = default_pp3
        self._forced_preset: Optional[str] = None
        self._max_file_size_kb = max_file_size_kb
        self._native = NativeProcessor(jpeg_quality=jpeg_quality)

    def set_forced_preset(self, preset: Optional[str]) -> None:
        """Override the auto-selected preset for all future exports.

        Accepts either a legacy scene preset name (e.g. ``"low_light"``)
        or a color grade name (e.g. ``"cinematic"``).  Setting ``None``
        reverts to per-photo auto-generation.
        """
        self._forced_preset = preset
        if preset:
            logger.info("Forced export preset set to: %s", preset)
        else:
            logger.info("Export preset reset to per-photo auto-generation.")

    def set_max_file_size(self, kb: int) -> None:
        """Set the maximum exported file size in KB (0 = no limit)."""
        self._max_file_size_kb = max(0, kb)
        if kb > 0:
            logger.info("Max file size set to %d KB", kb)
        else:
            logger.info("Max file size limit removed.")

    @staticmethod
    def _compress_to_size(path: Path, max_kb: int) -> None:
        """Re-compress a JPEG to fit within *max_kb* using binary search.

        Overwrites the file in-place. Only applies to JPEG files.
        """
        if path.suffix.lower() not in (".jpg", ".jpeg"):
            return
        current_kb = path.stat().st_size / 1024
        if current_kb <= max_kb:
            return

        from PIL import Image

        img = Image.open(path)
        icc_profile = img.info.get("icc_profile")

        # Binary search for the right quality.
        lo, hi = 10, 95
        best_buf = None
        while lo <= hi:
            mid = (lo + hi) // 2
            buf = io.BytesIO()
            save_kwargs = {"format": "JPEG", "quality": mid, "optimize": True}
            if icc_profile:
                save_kwargs["icc_profile"] = icc_profile
            img.save(buf, **save_kwargs)
            size_kb = buf.tell() / 1024
            if size_kb <= max_kb:
                best_buf = buf
                lo = mid + 1  # try higher quality
            else:
                hi = mid - 1  # need lower quality

        if best_buf is not None:
            path.write_bytes(best_buf.getvalue())
            final_kb = path.stat().st_size / 1024
            logger.info(
                "Compressed %s: %.0f KB → %.0f KB (target ≤ %d KB)",
                path.name, current_kb, final_kb, max_kb,
            )
        else:
            # Even quality=10 is too large — save at minimum.
            buf = io.BytesIO()
            save_kwargs = {"format": "JPEG", "quality": 10, "optimize": True}
            if icc_profile:
                save_kwargs["icc_profile"] = icc_profile
            img.save(buf, **save_kwargs)
            path.write_bytes(buf.getvalue())
            final_kb = path.stat().st_size / 1024
            logger.warning(
                "Compressed %s to minimum quality: %.0f KB (target was ≤ %d KB)",
                path.name, final_kb, max_kb,
            )

    def export_photo(self, photo_id: int) -> CLIResult:
        """Export a single photo by its database ID.

        Args:
            photo_id: Primary key of the ``Photo`` record.

        Returns:
            The ``CLIResult`` from the export tool.

        Raises:
            ValueError: If the photo does not exist.
        """
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photo: Optional[Photo] = session.get(Photo, photo_id)
            if photo is None:
                raise ValueError(f"Photo with id={photo_id} not found.")

            raw_path = Path(photo.file_path)
            sidecar = find_sidecar(raw_path)

            ext_map = {
                ExportFormat.JPEG: ".jpg",
                ExportFormat.TIFF: ".tiff",
                ExportFormat.PNG: ".png",
            }
            out_ext = ext_map.get(self._export_format, ".jpg")
            output_path = self._output_dir / f"{raw_path.stem}{out_ext}"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Mark as processing.
            photo.status = PhotoStatus.PROCESSING.value
            session.commit()

            logger.info("Exporting %s → %s", raw_path.name, output_path)

            metrics, grade, manual_ov = self._build_native_params(
                photo, raw_path, sidecar,
            )
            use_editor_pipeline = self._native._has_editor_overrides(manual_ov)

            # Edited photos must use the same renderer as the editor
            # preview, otherwise the export will not be WYSIWYG.
            if use_editor_pipeline:
                fmt_str = {ExportFormat.JPEG: "jpg", ExportFormat.TIFF: "tif",
                           ExportFormat.PNG: "png"}.get(self._export_format, "jpg")
                logger.info(
                    "Using native editor-matching export for %s (bypassing CLI tools)",
                    raw_path.name,
                )
                native_res = self._native.process(
                    input_path=raw_path,
                    output_dir=output_path.parent,
                    metrics=metrics,
                    color_grade=grade,
                    output_format=fmt_str,
                    manual_overrides=manual_ov,
                )
                result = CLIResult(
                    success=native_res.success,
                    return_code=native_res.return_code,
                    stdout=native_res.stdout,
                    stderr=native_res.stderr,
                    command=native_res.command,
                    duration_s=native_res.duration_s,
                )
            # Choose the right CLI tool for non-editor exports.
            elif self._cli.darktable_cli:
                result = self._cli.darktable_export(
                    input_path=raw_path,
                    output_path=output_path,
                    xmp_path=sidecar,
                )
            elif self._cli.rawtherapee_cli:
                # Generate a per-photo tailored PP3 profile.
                pp3 = self._generate_per_photo_pp3(photo, raw_path, sidecar)
                result = self._cli.rawtherapee_export(
                    input_path=raw_path,
                    output_dir=output_path.parent,
                    pp3_path=pp3,
                )
            else:
                # Native Python fallback — no external CLI needed.
                fmt_str = {ExportFormat.JPEG: "jpg", ExportFormat.TIFF: "tif",
                           ExportFormat.PNG: "png"}.get(self._export_format, "jpg")
                native_res = self._native.process(
                    input_path=raw_path,
                    output_dir=output_path.parent,
                    metrics=metrics,
                    color_grade=grade,
                    output_format=fmt_str,
                    manual_overrides=manual_ov,
                )
                result = CLIResult(
                    success=native_res.success,
                    return_code=native_res.return_code,
                    stdout=native_res.stdout,
                    stderr=native_res.stderr,
                    command=native_res.command,
                    duration_s=native_res.duration_s,
                )

            # Update database based on result.
            if result.success:
                # Apply file size limit compression if configured.
                if self._max_file_size_kb > 0 and output_path.is_file():
                    self._compress_to_size(output_path, self._max_file_size_kb)

                photo.status = PhotoStatus.EXPORTED.value
                photo.export_path = str(output_path)
                photo.error_message = None
                logger.info("Export succeeded: %s", output_path)
            else:
                photo.status = PhotoStatus.ERROR.value
                photo.error_message = result.stderr[:2048]
                logger.error("Export failed for %s: %s", raw_path.name, result.stderr)

            session.commit()
            return result

        except Exception as exc:
            # Ensure the photo is not stuck in PROCESSING.
            try:
                if photo is not None:
                    photo.status = PhotoStatus.ERROR.value
                    photo.error_message = str(exc)[:2048]
                    session.commit()
            except Exception:
                logger.exception("Failed to mark photo as ERROR.")
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Per-photo PP3 generation
    # ------------------------------------------------------------------
    def _generate_per_photo_pp3(
        self, photo: "Photo", raw_path: Path, sidecar: Optional[Path],
    ) -> Optional[Path]:
        """Generate a per-photo tailored PP3 profile.

        Priority:
        1. User-supplied PP3 sidecar on disk → use as-is.
        2. Generate a dynamic PP3 from analysis metrics + color grade.

        Returns:
            Path to the PP3 file, or ``None`` if generation fails.
        """
        # 1. If there's a manually-placed PP3 sidecar, honour it.
        if sidecar and sidecar.suffix.lower() == ".pp3":
            logger.info("Using user sidecar PP3 for %s", raw_path.name)
            return sidecar

        # 2. Build metrics from stored analysis data.
        photo_dict = {
            "cull_reasons": photo.cull_reasons or "",
            "exif_iso": photo.exif_iso,
            "exif_aperture": photo.exif_aperture,
            "exif_focal_length": photo.exif_focal_length,
            "exif_shutter_speed": photo.exif_shutter_speed,
        }
        metrics = metrics_from_photo(photo_dict)

        # Histogram analysis from thumbnail.
        thumb = photo.thumbnail_path
        if thumb and Path(thumb).is_file():
            analyze_photo_histogram(Path(thumb), metrics)

        # Load auto-crop data if available.
        if photo.auto_crop_data:
            try:
                crop = json.loads(photo.auto_crop_data)
                # Scale crop from thumbnail to full RAW dimensions.
                # The crop was computed on the thumbnail; we need to
                # scale to the full RAW image.  RT crops operate on
                # the decoded RAW pixel space after demosaicing.
                if crop.get("original_w") and crop.get("original_h"):
                    thumb_w = crop["original_w"]
                    thumb_h = crop["original_h"]

                    # Get actual RAW output dimensions.
                    try:
                        import rawpy
                        with rawpy.imread(str(raw_path)) as _raw:
                            raw_w = _raw.sizes.width
                            raw_h = _raw.sizes.height
                    except Exception:
                        # Fallback: Sony A7III typical
                        raw_w, raw_h = 6024, 4024

                    scale_x = raw_w / thumb_w
                    scale_y = raw_h / thumb_h

                    metrics.crop_enabled = True
                    metrics.crop_x = int(crop["x"] * scale_x)
                    metrics.crop_y = int(crop["y"] * scale_y)
                    metrics.crop_w = int(crop["w"] * scale_x)
                    metrics.crop_h = int(crop["h"] * scale_y)
                    metrics.image_w = raw_w
                    metrics.image_h = raw_h
            except (json.JSONDecodeError, KeyError):
                pass

        # Load manual overrides first — they contain the actual color grade.
        manual_ov: dict = {}
        if photo.manual_overrides:
            try:
                manual_ov = json.loads(photo.manual_overrides)
            except (json.JSONDecodeError, TypeError):
                pass
        if not manual_ov and photo.scene_preset:
            manual_ov = merge_editor_overrides(
                get_editor_style_overrides(photo.scene_preset),
                manual_ov,
            )

        # Resolve color grade: forced preset > manual override > DB column > natural.
        grade_name = (
            self._forced_preset
            or manual_ov.get("color_grade")
            or photo.scene_preset
            or photo.color_grade
            or "natural"
        )
        grade = GRADES.get(grade_name)

        # If the forced preset is a legacy scene name, fall back to
        # using the old static PP3 file for backwards compatibility.
        if grade is None:
            legacy_pp3 = get_preset_path(grade_name)
            if legacy_pp3:
                logger.info(
                    "Using legacy '%s' preset for %s",
                    grade_name, raw_path.name,
                )
                return legacy_pp3
            grade = GRADES["natural"]

        # Generate the PP3.
        pp3_dir = Path.home() / ".imagic" / "pp3_cache"
        pp3_path = pp3_dir / f"{raw_path.stem}.pp3"

        generate_pp3(metrics, grade, pp3_path, manual_overrides=manual_ov)
        logger.info(
            "Per-photo PP3 for %s: grade=%s, exp=%.1f, ISO=%s, sharp=%.2f",
            raw_path.name, grade.name,
            metrics.mean_brightness / 255.0,
            metrics.iso or "?",
            metrics.sharpness,
        )
        return pp3_path

    # ------------------------------------------------------------------
    # Native fallback parameter extraction
    # ------------------------------------------------------------------
    def _build_native_params(
        self, photo: "Photo", raw_path: Path, sidecar: Optional[Path],
    ) -> tuple:
        """Build (metrics, grade, manual_overrides) for the native processor.

        Mirrors the logic in ``_generate_per_photo_pp3`` but returns
        Python objects instead of writing a PP3 file.
        """
        photo_dict = {
            "cull_reasons": photo.cull_reasons or "",
            "exif_iso": photo.exif_iso,
            "exif_aperture": photo.exif_aperture,
            "exif_focal_length": photo.exif_focal_length,
            "exif_shutter_speed": photo.exif_shutter_speed,
        }
        metrics = metrics_from_photo(photo_dict)

        thumb = photo.thumbnail_path
        if thumb and Path(thumb).is_file():
            analyze_photo_histogram(Path(thumb), metrics)

        if photo.auto_crop_data:
            try:
                crop = json.loads(photo.auto_crop_data)
                if crop.get("original_w") and crop.get("original_h"):
                    thumb_w = crop["original_w"]
                    thumb_h = crop["original_h"]
                    try:
                        import rawpy
                        with rawpy.imread(str(raw_path)) as _raw:
                            raw_w = _raw.sizes.width
                            raw_h = _raw.sizes.height
                    except Exception:
                        raw_w, raw_h = 6024, 4024
                    scale_x = raw_w / thumb_w
                    scale_y = raw_h / thumb_h
                    metrics.crop_enabled = True
                    metrics.crop_x = int(crop["x"] * scale_x)
                    metrics.crop_y = int(crop["y"] * scale_y)
                    metrics.crop_w = int(crop["w"] * scale_x)
                    metrics.crop_h = int(crop["h"] * scale_y)
                    metrics.image_w = raw_w
                    metrics.image_h = raw_h
            except (json.JSONDecodeError, KeyError):
                pass

        manual_ov: dict = {}
        if photo.manual_overrides:
            try:
                manual_ov = json.loads(photo.manual_overrides)
            except (json.JSONDecodeError, TypeError):
                pass
        if not manual_ov and photo.scene_preset:
            manual_ov = merge_editor_overrides(
                get_editor_style_overrides(photo.scene_preset),
                manual_ov,
            )

        grade_name = (
            self._forced_preset
            or manual_ov.get("color_grade")
            or photo.scene_preset
            or photo.color_grade
            or "natural"
        )
        grade = GRADES.get(grade_name, GRADES["natural"])

        return metrics, grade, manual_ov
