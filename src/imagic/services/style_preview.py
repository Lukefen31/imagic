"""Style preview generator — fast approximate previews of each PP3 preset.

Uses ``rawpy`` for demosaicing and ``numpy`` / ``Pillow`` for quick colour
adjustments that approximate the look of each RawTherapee preset.  This
allows instant (~1 s) preview generation without invoking RawTherapee CLI,
so the style-chooser dialog feels snappy.

The final export still uses RawTherapee with the real PP3 — these previews
are just for visual comparison and direction-picking.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from imagic.services.editor_style_presets import LEGACY_STYLE_PRESETS, get_editor_style_overrides
from imagic.services.preview_engine import PreviewEngine

logger = logging.getLogger(__name__)

# Preview size (long edge), kept small for speed.
_PREVIEW_LONG_EDGE = 800


@dataclass
class StylePreview:
    """A single style preview image.

    Attributes:
        preset: Preset name (e.g. ``"low_light"``).
        label: Human-friendly display label.
        description: Short explanation of what the preset does.
        path: Path to the generated preview JPEG.
    """

    preset: str
    label: str
    description: str
    path: Optional[Path] = None


@dataclass
class PhotoPreviews:
    """All style previews for a single photo.

    Attributes:
        photo_id: Database ID.
        file_name: Original filename.
        original_thumb: Path to the original thumbnail (for comparison).
        styles: Dict mapping preset name → ``StylePreview``.
    """

    photo_id: int
    file_name: str
    original_thumb: Optional[Path] = None
    styles: Dict[str, StylePreview] = field(default_factory=dict)


# ------------------------------------------------------------------
# Preset definitions (label + description + adjustment parameters)
# ------------------------------------------------------------------

PRESET_INFO: Dict[str, Tuple[str, str]] = {
    "low_light": (
        "Low Light",
        "Shadow recovery, aggressive noise reduction, soft sharpening",
    ),
    "bright_outdoor": (
        "Bright & Vibrant",
        "Punchy contrast, strong sharpening, vibrance boost",
    ),
    "high_contrast": (
        "High Contrast",
        "Deep blacks, highlight control, dramatic look",
    ),
    "portrait": (
        "Portrait / Soft",
        "Gentle processing, skin-friendly, subtle tones",
    ),
    "balanced": (
        "Balanced",
        "Moderate processing — the all-around default",
    ),
}


# ------------------------------------------------------------------
# Numpy-based style approximations
# ------------------------------------------------------------------

def _to_float(arr: np.ndarray) -> np.ndarray:
    """Convert uint8/uint16 array to float64 in [0, 1]."""
    if arr.dtype == np.uint8:
        return arr.astype(np.float64) / 255.0
    elif arr.dtype == np.uint16:
        return arr.astype(np.float64) / 65535.0
    return arr.astype(np.float64)


def _to_uint8(arr: np.ndarray) -> np.ndarray:
    """Clip and convert float64 [0, 1] array to uint8."""
    return np.clip(arr * 255, 0, 255).astype(np.uint8)


def _adjust_exposure(img: np.ndarray, ev: float) -> np.ndarray:
    """Apply exposure compensation in EV stops."""
    return img * (2.0 ** ev)


def _adjust_contrast(img: np.ndarray, factor: float) -> np.ndarray:
    """Adjust contrast around the midpoint (0.5)."""
    return (img - 0.5) * factor + 0.5


def _adjust_saturation(img: np.ndarray, factor: float) -> np.ndarray:
    """Adjust saturation. factor=1.0 is unchanged, >1 more saturated."""
    gray = np.mean(img, axis=2, keepdims=True)
    return gray + (img - gray) * factor


def _lift_shadows(img: np.ndarray, amount: float) -> np.ndarray:
    """Lift shadow regions by blending a brightened version into dark areas."""
    mask = 1.0 - np.clip(img * 2.0, 0.0, 1.0)  # strongest in shadows
    return img + mask * amount


def _compress_highlights(img: np.ndarray, strength: float) -> np.ndarray:
    """Gently compress highlights to recover blown areas."""
    # Apply soft knee compression above 0.7
    threshold = 0.7
    above = np.maximum(img - threshold, 0)
    compressed = threshold + above * (1.0 - strength)
    return np.where(img > threshold, compressed, img)


def _soft_blur(img: np.ndarray, radius: int = 2) -> np.ndarray:
    """Very simple box blur for noise reduction approximation."""
    from PIL import Image, ImageFilter

    pil = Image.fromarray(_to_uint8(img))
    pil = pil.filter(ImageFilter.GaussianBlur(radius=radius))
    return _to_float(np.asarray(pil))


def _sharpen(img: np.ndarray, amount: float = 1.0) -> np.ndarray:
    """Unsharp-mask-style sharpening."""
    from PIL import Image, ImageFilter

    pil = Image.fromarray(_to_uint8(img))
    sharp = pil.filter(ImageFilter.UnsharpMask(radius=1, percent=int(amount * 100), threshold=3))
    return _to_float(np.asarray(sharp))


def _warm_shift(img: np.ndarray, amount: float) -> np.ndarray:
    """Subtle warm/cool white balance shift. Positive = warmer."""
    result = img.copy()
    result[:, :, 0] = result[:, :, 0] * (1 + amount * 0.05)  # red
    result[:, :, 2] = result[:, :, 2] * (1 - amount * 0.03)  # blue
    return result


# ------------------------------------------------------------------
# Style application functions
# ------------------------------------------------------------------

def _apply_low_light(img: np.ndarray) -> np.ndarray:
    """Approximate the low_light PP3 preset."""
    img = _adjust_exposure(img, 0.6)
    img = _lift_shadows(img, 0.15)
    img = _compress_highlights(img, 0.4)
    img = _soft_blur(img, radius=1)       # mild NR
    img = _adjust_contrast(img, 1.1)
    img = _adjust_saturation(img, 1.05)
    img = _warm_shift(img, 0.5)
    return img


def _apply_bright_outdoor(img: np.ndarray) -> np.ndarray:
    """Approximate the bright_outdoor PP3 preset."""
    img = _adjust_contrast(img, 1.25)
    img = _adjust_saturation(img, 1.25)
    img = _sharpen(img, 2.0)
    img = _compress_highlights(img, 0.3)
    img = _warm_shift(img, 0.3)
    return img


def _apply_high_contrast(img: np.ndarray) -> np.ndarray:
    """Approximate the high_contrast PP3 preset."""
    img = _adjust_exposure(img, 0.3)
    img = _lift_shadows(img, 0.12)
    img = _compress_highlights(img, 0.5)
    img = _adjust_contrast(img, 1.35)
    img = _adjust_saturation(img, 1.1)
    img = _sharpen(img, 1.5)
    return img


def _apply_portrait(img: np.ndarray) -> np.ndarray:
    """Approximate the portrait PP3 preset."""
    img = _adjust_exposure(img, 0.1)
    img = _lift_shadows(img, 0.08)
    img = _soft_blur(img, radius=1)       # skin smoothing hint
    img = _adjust_contrast(img, 1.08)
    img = _adjust_saturation(img, 1.05)
    img = _warm_shift(img, 0.2)
    return img


def _apply_balanced(img: np.ndarray) -> np.ndarray:
    """Approximate the balanced PP3 preset."""
    img = _adjust_exposure(img, 0.2)
    img = _lift_shadows(img, 0.10)
    img = _compress_highlights(img, 0.35)
    img = _adjust_contrast(img, 1.15)
    img = _adjust_saturation(img, 1.1)
    img = _sharpen(img, 1.2)
    return img


_STYLE_FUNCTIONS = {
    "low_light": _apply_low_light,
    "bright_outdoor": _apply_bright_outdoor,
    "high_contrast": _apply_high_contrast,
    "portrait": _apply_portrait,
    "balanced": _apply_balanced,
}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def pick_sample_photos(
    photo_records: List[dict],
    count: int = 3,
) -> List[dict]:
    """Select a random subset of photos for preview generation.

    Prefers photos that already have thumbnails.

    Args:
        photo_records: List of photo dicts from the database query.
        count: Number of samples to pick.

    Returns:
        Up to *count* photo dicts.
    """
    with_thumbs = [p for p in photo_records if p.get("thumbnail_path")]
    pool = with_thumbs if with_thumbs else photo_records
    return random.sample(pool, min(count, len(pool)))


def generate_style_previews(
    raw_path: Path,
    photo_id: int,
    file_name: str,
    preview_dir: Path,
    thumbnail_path: Optional[Path] = None,
) -> PhotoPreviews:
    """Generate approximate style previews for one photo.

    Uses ``rawpy`` to decode the RAW file once, then applies each preset's
    look with fast numpy adjustments and saves preview JPEGs.

    Args:
        raw_path: Path to the RAW file.
        photo_id: Database photo ID.
        file_name: Display filename.
        preview_dir: Directory to write preview JPEGs into.
        thumbnail_path: Existing thumbnail for the "original" reference.

    Returns:
        A ``PhotoPreviews`` containing all generated previews.
    """
    from PIL import Image

    previews = PhotoPreviews(
        photo_id=photo_id,
        file_name=file_name,
        original_thumb=thumbnail_path,
    )

    # Decode the RAW once.
    try:
        import rawpy

        with rawpy.imread(str(raw_path)) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_bps=8,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                output_color=rawpy.ColorSpace.sRGB,
            )
    except Exception as exc:
        logger.error("Failed to decode %s for previews: %s", raw_path, exc)
        return previews

    # Resize for speed.
    h, w = rgb.shape[:2]
    scale = _PREVIEW_LONG_EDGE / max(h, w)
    if scale < 1.0:
        new_w, new_h = int(w * scale), int(h * scale)
        pil_img = Image.fromarray(rgb).resize((new_w, new_h), Image.LANCZOS)
        rgb = np.asarray(pil_img)

    preview_dir.mkdir(parents=True, exist_ok=True)
    stem = raw_path.stem

    # Save an "original" (camera auto) preview for reference.
    orig_path = preview_dir / f"{stem}_original.jpg"
    Image.fromarray(rgb).save(str(orig_path), "JPEG", quality=88)
    previews.styles["original"] = StylePreview(
        preset="original",
        label="Camera Default",
        description="Straight RAW decode with camera white balance",
        path=orig_path,
    )

    for preset_name in LEGACY_STYLE_PRESETS:
        try:
            out = PreviewEngine.apply(rgb, get_editor_style_overrides(preset_name))
            out_path = preview_dir / f"{stem}_{preset_name}.jpg"
            Image.fromarray(out).save(str(out_path), "JPEG", quality=88)

            label, desc = PRESET_INFO.get(preset_name, (preset_name, ""))
            previews.styles[preset_name] = StylePreview(
                preset=preset_name,
                label=label,
                description=desc,
                path=out_path,
            )
            logger.debug("Preview generated: %s → %s", preset_name, out_path)
        except Exception as exc:
            logger.error("Preview gen failed for %s/%s: %s", file_name, preset_name, exc)

    return previews
