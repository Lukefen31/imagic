"""EXIF / XMP metadata reader.

Uses ``exiftool`` (via the CLI orchestrator) or falls back to ``Pillow``'s
built-in EXIF parser for the most common tags.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PhotoMetadata:
    """Subset of EXIF fields relevant to the Imagic workflow.

    Attributes:
        make: Camera manufacturer.
        model: Camera model name.
        date_taken: Date and time the image was captured.
        iso: ISO sensitivity.
        focal_length: Focal length in mm.
        aperture: F-number.
        shutter_speed: Shutter speed as a human-readable string.
        width: Image width in pixels.
        height: Image height in pixels.
    """

    make: Optional[str] = None
    model: Optional[str] = None
    date_taken: Optional[datetime.datetime] = None
    iso: Optional[int] = None
    focal_length: Optional[float] = None
    aperture: Optional[float] = None
    shutter_speed: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


def read_metadata_exiftool(
    file_path: Path,
    cli_orchestrator: "CLIOrchestrator",  # noqa: F821 — type: ignore
) -> Optional[PhotoMetadata]:
    """Read metadata using exiftool via ``CLIOrchestrator``.

    Args:
        file_path: Path to the image.
        cli_orchestrator: A configured ``CLIOrchestrator`` instance.

    Returns:
        ``PhotoMetadata`` on success, ``None`` on failure.
    """
    try:
        result = cli_orchestrator.read_exif(file_path)
        if not result.success:
            logger.warning("exiftool failed for %s: %s", file_path, result.stderr)
            return None

        data_list = json.loads(result.stdout)
        if not data_list:
            return None
        data = data_list[0]

        date_str = data.get("DateTimeOriginal") or data.get("CreateDate")
        date_taken = None
        if date_str:
            for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    date_taken = datetime.datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

        return PhotoMetadata(
            make=data.get("Make"),
            model=data.get("Model"),
            date_taken=date_taken,
            iso=_safe_int(data.get("ISO")),
            focal_length=_safe_float(data.get("FocalLength")),
            aperture=_safe_float(data.get("FNumber")),
            shutter_speed=str(data.get("ExposureTime", "")),
            width=_safe_int(data.get("ImageWidth")),
            height=_safe_int(data.get("ImageHeight")),
        )
    except Exception as exc:
        logger.error("Metadata read error for %s: %s", file_path, exc)
        return None


def read_metadata_pillow(file_path: Path) -> Optional[PhotoMetadata]:
    """Fallback: read basic EXIF via Pillow.

    Args:
        file_path: Path to the image (must be a format Pillow can open).

    Returns:
        ``PhotoMetadata`` on success, ``None`` on failure.
    """
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        img = Image.open(file_path)
        exif_raw = img.getexif()
        if not exif_raw:
            return None

        exif: dict = {}
        for tag_id, value in exif_raw.items():
            tag_name = TAGS.get(tag_id, tag_id)
            exif[tag_name] = value

        date_str = exif.get("DateTimeOriginal") or exif.get("DateTime")
        date_taken = None
        if date_str and isinstance(date_str, str):
            try:
                date_taken = datetime.datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass

        return PhotoMetadata(
            make=exif.get("Make"),
            model=exif.get("Model"),
            date_taken=date_taken,
            iso=_safe_int(exif.get("ISOSpeedRatings")),
            focal_length=_safe_float(exif.get("FocalLength")),
            aperture=_safe_float(exif.get("FNumber")),
            shutter_speed=str(exif.get("ExposureTime", "")),
            width=img.width,
            height=img.height,
        )
    except Exception as exc:
        logger.error("Pillow metadata read error for %s: %s", file_path, exc)
        return None


def read_metadata_rawpy(file_path: Path) -> Optional[PhotoMetadata]:
    """Read basic EXIF data from a RAW file via rawpy + Pillow.

    Extracts the embedded JPEG thumb, then reads its EXIF tags.

    Args:
        file_path: Path to the RAW file.

    Returns:
        ``PhotoMetadata`` on success, ``None`` on failure.
    """
    try:
        import io
        import rawpy
        from PIL import Image
        from PIL.ExifTags import TAGS, IFD

        with rawpy.imread(str(file_path)) as raw:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(io.BytesIO(thumb.data))
            else:
                return None

        exif_raw = img.getexif()
        if not exif_raw:
            return None

        exif: dict = {}
        for tag_id, value in exif_raw.items():
            tag_name = TAGS.get(tag_id, tag_id)
            exif[tag_name] = value

        # Also read EXIF IFD sub-tags (ISO, aperture, focal length, etc.).
        try:
            ifd = exif_raw.get_ifd(IFD.Exif)
            for tag_id, value in ifd.items():
                tag_name = TAGS.get(tag_id, tag_id)
                exif[tag_name] = value
        except Exception:
            pass

        date_str = exif.get("DateTimeOriginal") or exif.get("DateTime")
        date_taken = None
        if date_str:
            for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    date_taken = datetime.datetime.strptime(str(date_str), fmt)
                    break
                except ValueError:
                    continue

        # Format shutter speed as a fraction string if it's a float < 1.
        raw_ss = exif.get("ExposureTime", "")
        if isinstance(raw_ss, (int, float)) and 0 < raw_ss < 1:
            ss_str = f"1/{round(1.0 / raw_ss)}"
        else:
            ss_str = str(raw_ss) if raw_ss else ""

        return PhotoMetadata(
            make=exif.get("Make"),
            model=exif.get("Model"),
            date_taken=date_taken,
            iso=_safe_int(exif.get("ISOSpeedRatings")),
            focal_length=_safe_float(exif.get("FocalLength")),
            aperture=_safe_float(exif.get("FNumber")),
            shutter_speed=ss_str,
            width=img.width,
            height=img.height,
        )
    except Exception as exc:
        logger.error("rawpy metadata read error for %s: %s", file_path, exc)
        return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _safe_int(value: object) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
