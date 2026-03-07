"""Thumbnail generator — extracts embedded previews from RAW files.

Uses ``rawpy`` (libraw binding) to pull the embedded JPEG preview and resize
it to a configurable maximum dimension via ``Pillow``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def generate_thumbnail(
    raw_path: Path,
    output_path: Path,
    max_size: Tuple[int, int] = (320, 320),
    quality: int = 85,
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

        with rawpy.imread(str(raw_path)) as raw:
            try:
                # Fast path: embedded JPEG thumbnail.
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    # Write raw JPEG bytes, then resize.
                    temp = output_path.with_suffix(".tmp.jpg")
                    temp.write_bytes(thumb.data)
                    img = Image.open(temp)
                    img.thumbnail(max_size, Image.LANCZOS)
                    img.save(str(output_path), "JPEG", quality=quality)
                    temp.unlink(missing_ok=True)
                    logger.debug("Thumbnail (embedded) created: %s", output_path)
                    return output_path
            except Exception:
                logger.debug("No embedded thumb for %s — falling back to full decode.", raw_path)

            # Slow path: full demosaic → resize.
            rgb = raw.postprocess()

        img = Image.fromarray(rgb)
        img.thumbnail(max_size, Image.LANCZOS)
        img.save(str(output_path), "JPEG", quality=quality)
        logger.debug("Thumbnail (decoded) created: %s", output_path)
        return output_path

    except ImportError:
        logger.error("rawpy or Pillow not installed — cannot generate thumbnails.")
        return None
    except Exception as exc:
        logger.error("Failed to generate thumbnail for %s: %s", raw_path, exc)
        return None
