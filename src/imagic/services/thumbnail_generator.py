"""Thumbnail generator — extracts embedded previews from RAW files.

Uses ``rawpy`` (libraw binding) to pull the embedded JPEG preview and resize
it to a configurable maximum dimension via ``Pillow``.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_RAW_THUMBNAIL_CONCURRENCY = max(1, int(os.environ.get("IMAGIC_RAW_THUMBNAIL_CONCURRENCY", "1") or "1"))
_RAW_THUMBNAIL_SEMAPHORE = threading.BoundedSemaphore(_RAW_THUMBNAIL_CONCURRENCY)


def generate_thumbnail(
    raw_path: Path,
    output_path: Path,
    max_size: Tuple[int, int] = (320, 320),
    quality: int = 85,
    embedded_only: bool = False,
) -> Optional[Path]:
    """Create a JPEG thumbnail for a RAW image.

    The function first attempts to extract the embedded JPEG preview
    (extremely fast).  If that fails it falls back to a full decode +
    resize (slower but reliable).

    Args:
        raw_path: Path to the source RAW file.
        output_path: Destination JPEG path.
        max_size: Maximum (width, height) for the thumbnail.
        quality: JPEG compression quality (1–100).

    Returns:
        The *output_path* on success, or ``None`` on failure.
    """
    try:
        import rawpy
        from PIL import Image

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with _RAW_THUMBNAIL_SEMAPHORE:
            with rawpy.imread(str(raw_path)) as raw:
                try:
                    # Fast path: embedded JPEG thumbnail.
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        img = Image.open(io.BytesIO(thumb.data))
                        img.thumbnail(max_size, Image.LANCZOS)
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
                        logger.debug("Thumbnail (embedded) created: %s", output_path)
                        return output_path
                except Exception:
                    logger.debug("No embedded thumb for %s — falling back to decode.", raw_path)

                if embedded_only:
                    logger.warning("No embedded RAW thumbnail available for %s", raw_path)
                    return None

                # Memory-conscious fallback for web use.
                rgb = raw.postprocess(
                    half_size=True,
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                )

        img = Image.fromarray(rgb)
        img.thumbnail(max_size, Image.LANCZOS)
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
        logger.debug("Thumbnail (decoded) created: %s", output_path)
        return output_path

    except ImportError:
        logger.error("rawpy or Pillow not installed — cannot generate thumbnails.")
        return None
    except Exception as exc:
        logger.error("Failed to generate thumbnail for %s: %s", raw_path, exc)
        return None
