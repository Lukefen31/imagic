"""Grade preview renderer — applies colour-grade effects to thumbnails.

Uses numpy + Pillow to approximate the RawTherapee look of each colour
grade so the user can compare them side-by-side before exporting.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from imagic.services.pp3_generator import ColorGrade, GRADES

logger = logging.getLogger(__name__)

_PREVIEW_SIZE = 300  # square preview dimension


def render_grade_preview(
    thumbnail_path: Path,
    grade: ColorGrade,
    size: int = _PREVIEW_SIZE,
) -> Optional[Image.Image]:
    """Apply *grade* effects to a thumbnail and return the result.

    Returns ``None`` on any error so callers can gracefully skip.
    """
    try:
        img = Image.open(thumbnail_path).convert("RGB")
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        arr = np.asarray(img, dtype=np.float32)  # (H, W, 3) 0-255

        # 1. Tone curve  — lift blacks and compress highlights
        arr = _apply_tone_curve(arr, grade)

        # 2. Contrast boost
        if grade.contrast_boost:
            arr = _apply_contrast(arr, grade.contrast_boost)

        # 3. Saturation / vibrance
        arr = _apply_saturation(arr, grade.saturation_shift,
                                grade.vibrance_pastels,
                                grade.vibrance_saturated)

        # 4. Split toning
        if grade.split_tone_enabled:
            arr = _apply_split_tone(arr, grade)

        # 5. Black & white
        if grade.is_bw:
            arr = _apply_bw(arr)

        # 6. Soft-light glow
        if grade.soft_light_strength:
            arr = _apply_soft_light(arr, grade.soft_light_strength)

        # 7. Vignette
        if grade.vignette_amount:
            arr = _apply_vignette(arr, grade.vignette_amount,
                                  grade.vignette_radius)

        # 8. Film grain
        if grade.film_grain_enabled:
            arr = _apply_grain(arr, grade.film_grain_strength)

        arr = np.clip(arr, 0, 255).astype(np.uint8)
        return Image.fromarray(arr)
    except Exception:
        logger.exception("Failed to render grade preview for %s", grade.name)
        return None


# ------------------------------------------------------------------
# Effect helpers
# ------------------------------------------------------------------

def _apply_tone_curve(arr: np.ndarray, grade: ColorGrade) -> np.ndarray:
    """Parse the PP3-style tone curve string and apply via a LUT."""
    if not grade.tone_curve:
        return arr
    parts = grade.tone_curve.rstrip(";").split(";")
    if len(parts) < 3:
        return arr
    # Skip the first element (curve type indicator), then read x,y pairs.
    try:
        coords = [float(p) for p in parts[1:]]
    except ValueError:
        return arr
    if len(coords) % 2 != 0:
        return arr
    points = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
    points.sort(key=lambda p: p[0])

    # Build a 256-entry LUT via linear interpolation between control points.
    lut = np.zeros(256, dtype=np.float32)
    for i in range(256):
        x = i / 255.0
        # Find surrounding control points.
        y = points[-1][1]  # default to last
        for j in range(len(points) - 1):
            x0, y0 = points[j]
            x1, y1 = points[j + 1]
            if x0 <= x <= x1:
                t = (x - x0) / max(x1 - x0, 1e-6)
                y = y0 + t * (y1 - y0)
                break
        lut[i] = y * 255.0

    idx = np.clip(arr, 0, 255).astype(np.uint8)
    return lut[idx].astype(np.float32)


def _apply_contrast(arr: np.ndarray, boost: int) -> np.ndarray:
    """Apply contrast boost (-100..+100 scale)."""
    factor = 1.0 + boost / 100.0
    mid = 128.0
    return (arr - mid) * factor + mid


def _apply_saturation(
    arr: np.ndarray,
    sat_shift: int,
    vib_pastels: int,
    vib_saturated: int,
) -> np.ndarray:
    """Shift saturation with a simple luminance-based approach."""
    combined = sat_shift + (vib_pastels + vib_saturated) / 4.0
    if abs(combined) < 1:
        return arr
    factor = 1.0 + combined / 100.0
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    lum = lum[..., np.newaxis]
    return lum + (arr - lum) * factor


def _apply_split_tone(arr: np.ndarray, grade: ColorGrade) -> np.ndarray:
    """Tint shadows and highlights with split-toning colours."""
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]

    # Shadow mask: strong where dark, zero where bright.
    shadow_mask = np.clip(1.0 - lum / 128.0, 0, 1)[..., np.newaxis]
    # Highlight mask: strong where bright, zero where dark.
    highlight_mask = np.clip((lum - 128.0) / 128.0, 0, 1)[..., np.newaxis]

    sh_col = _hue_sat_to_rgb(grade.split_tone_shadow_hue,
                              grade.split_tone_shadow_sat)
    hi_col = _hue_sat_to_rgb(grade.split_tone_highlight_hue,
                              grade.split_tone_highlight_sat)

    arr = arr + shadow_mask * sh_col * 0.5
    arr = arr + highlight_mask * hi_col * 0.5
    return arr


def _hue_sat_to_rgb(hue: int, sat: int) -> np.ndarray:
    """Convert hue (0-360) and saturation (0-100) to an RGB offset array."""
    import colorsys
    h = (hue % 360) / 360.0
    s = min(max(sat, 0), 100) / 100.0
    r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
    return np.array([r * 255, g * 255, b * 255], dtype=np.float32) - 128.0


def _apply_bw(arr: np.ndarray) -> np.ndarray:
    """Convert to luminance-weighted greyscale."""
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    return np.stack([lum, lum, lum], axis=-1)


def _apply_soft_light(arr: np.ndarray, strength: int) -> np.ndarray:
    """Simulate a soft-light / glow blend."""
    from scipy.ndimage import gaussian_filter
    factor = strength / 100.0
    blurred = gaussian_filter(arr, sigma=8)
    # Soft light blend: 2*a*b / 255 where dark, else 255 - 2*(255-a)*(255-b)/255
    a = arr / 255.0
    b = blurred / 255.0
    result = np.where(
        a < 0.5,
        2 * a * b,
        1.0 - 2 * (1.0 - a) * (1.0 - b),
    ) * 255.0
    return arr * (1 - factor) + result * factor


def _apply_vignette(arr: np.ndarray, amount: int, radius: int) -> np.ndarray:
    """Darken edges with a radial vignette."""
    h, w = arr.shape[:2]
    cy, cx = h / 2, w / 2
    Y, X = np.ogrid[:h, :w]
    dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    r = radius / 100.0
    norm = np.clip((dist / max_dist - r) / (1.0 - r + 1e-6), 0, 1)
    factor = 1.0 - (amount / 100.0) * norm[..., np.newaxis]
    return arr * factor


def _apply_grain(arr: np.ndarray, strength: int) -> np.ndarray:
    """Add monochrome film grain noise."""
    rng = np.random.default_rng(42)  # fixed seed for stable previews
    noise = rng.standard_normal(arr.shape[:2]) * (strength / 100.0) * 25.0
    return arr + noise[..., np.newaxis]
