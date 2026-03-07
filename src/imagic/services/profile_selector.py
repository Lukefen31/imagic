"""Automatic profile selector — picks the best PP3 preset per image.

Analyses image characteristics (EXIF metadata + thumbnail histogram) to
classify each photo into one of five scene categories, then returns the
corresponding RawTherapee PP3 processing profile.

Scene categories
~~~~~~~~~~~~~~~~
- **low_light** — high ISO (>=3200) or dark histogram; aggressive NR & shadow lift.
- **bright_outdoor** — low ISO, bright histogram; strong sharpening, vibrance & contrast.
- **high_contrast** — wide dynamic range; shadow recovery + highlight compression.
- **portrait** — moderate focal length + wide aperture; soft sharpening, skin protection.
- **balanced** — everything else; moderate processing across the board.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Directory containing the bundled PP3 presets (next to this file's package root).
_PROFILES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "profiles"


@dataclass
class SceneClassification:
    """Result of the automatic scene analysis.

    Attributes:
        preset: Name of the selected preset (e.g. ``"low_light"``).
        pp3_path: Resolved path to the matching PP3 file.
        reason: Human-readable explanation of why this preset was chosen.
        brightness: Mean brightness of the thumbnail (0–255).
        contrast: Histogram standard deviation (0–128).
    """

    preset: str
    pp3_path: Path
    reason: str
    brightness: float = 0.0
    contrast: float = 0.0


# ------------------------------------------------------------------
# Profile directory helpers
# ------------------------------------------------------------------

def available_presets() -> dict[str, Path]:
    """Return a mapping of preset name → PP3 path for all bundled presets."""
    presets: dict[str, Path] = {}
    if _PROFILES_DIR.is_dir():
        for pp3 in sorted(_PROFILES_DIR.glob("*.pp3")):
            presets[pp3.stem] = pp3
    return presets


def get_preset_path(name: str) -> Optional[Path]:
    """Return the PP3 path for a named preset, or ``None`` if not found."""
    p = _PROFILES_DIR / f"{name}.pp3"
    return p if p.is_file() else None


# ------------------------------------------------------------------
# Image analysis helpers
# ------------------------------------------------------------------

def _analyse_thumbnail(thumb_path: Path) -> tuple[float, float, float]:
    """Compute brightness, contrast, and dynamic-range ratio from a thumbnail.

    Returns:
        ``(mean_brightness, std_dev, dynamic_range_ratio)``
        where dynamic_range_ratio = (p95 - p5) / 255.
    """
    try:
        from PIL import Image

        img = Image.open(thumb_path).convert("L")
        arr = np.asarray(img, dtype=np.float64)
        mean_brightness = float(np.mean(arr))
        std_dev = float(np.std(arr))
        p5, p95 = float(np.percentile(arr, 5)), float(np.percentile(arr, 95))
        dr_ratio = (p95 - p5) / 255.0
        return mean_brightness, std_dev, dr_ratio
    except Exception as exc:
        logger.debug("Thumbnail analysis failed for %s: %s", thumb_path, exc)
        return 128.0, 50.0, 0.5  # safe defaults → balanced


def _parse_shutter_speed(ss_str: Optional[str]) -> Optional[float]:
    """Convert a shutter speed string like ``1/250`` to seconds."""
    if not ss_str:
        return None
    try:
        if "/" in ss_str:
            num, den = ss_str.split("/")
            return float(num) / float(den)
        return float(ss_str)
    except (ValueError, ZeroDivisionError):
        return None


# ------------------------------------------------------------------
# Main classifier
# ------------------------------------------------------------------

def classify_scene(
    *,
    iso: Optional[int] = None,
    aperture: Optional[float] = None,
    focal_length: Optional[float] = None,
    shutter_speed: Optional[str] = None,
    thumbnail_path: Optional[Path] = None,
) -> SceneClassification:
    """Classify a photo's scene and return the best-matching PP3 preset.

    The classifier uses a priority waterfall:

    1. **Low light** — ISO >= 3200, *or* mean brightness < 70.
    2. **Portrait** — focal length 35–135 mm *and* aperture <= 2.8.
    3. **High contrast** — dynamic range ratio > 0.75 *and* std_dev > 60.
    4. **Bright outdoor** — mean brightness > 160 *and* ISO < 800.
    5. **Balanced** — fallback for everything else.

    Args:
        iso: EXIF ISO value (``None`` if unknown).
        aperture: EXIF f-number (``None`` if unknown).
        focal_length: EXIF focal length in mm (``None`` if unknown).
        shutter_speed: EXIF shutter speed as string, e.g. ``"1/250"``.
        thumbnail_path: Path to the thumbnail JPEG for histogram analysis.

    Returns:
        A ``SceneClassification`` with the chosen preset and explanation.
    """
    # Analyse the thumbnail (if available).
    brightness, contrast, dr_ratio = 128.0, 50.0, 0.5
    if thumbnail_path and Path(thumbnail_path).is_file():
        brightness, contrast, dr_ratio = _analyse_thumbnail(Path(thumbnail_path))

    ss_seconds = _parse_shutter_speed(shutter_speed)

    # --- 1. Low light ---
    low_light_reasons = []
    if iso is not None and iso >= 3200:
        low_light_reasons.append(f"high ISO ({iso})")
    if brightness < 70:
        low_light_reasons.append(f"dark image (brightness={brightness:.0f})")
    if ss_seconds is not None and ss_seconds >= 1 / 30:
        low_light_reasons.append(f"slow shutter ({shutter_speed})")

    if len(low_light_reasons) >= 1 and (iso is None or iso >= 1600 or brightness < 70):
        pp3 = get_preset_path("low_light")
        if pp3:
            return SceneClassification(
                preset="low_light",
                pp3_path=pp3,
                reason="Low light: " + ", ".join(low_light_reasons),
                brightness=brightness,
                contrast=contrast,
            )

    # --- 2. Portrait ---
    if (
        focal_length is not None
        and 35 <= focal_length <= 135
        and aperture is not None
        and aperture <= 2.8
    ):
        pp3 = get_preset_path("portrait")
        if pp3:
            return SceneClassification(
                preset="portrait",
                pp3_path=pp3,
                reason=f"Portrait: {focal_length:.0f}mm f/{aperture:.1f}",
                brightness=brightness,
                contrast=contrast,
            )

    # --- 3. High contrast ---
    if dr_ratio > 0.75 and contrast > 60:
        pp3 = get_preset_path("high_contrast")
        if pp3:
            return SceneClassification(
                preset="high_contrast",
                pp3_path=pp3,
                reason=f"High contrast scene (DR={dr_ratio:.2f}, σ={contrast:.0f})",
                brightness=brightness,
                contrast=contrast,
            )

    # --- 4. Bright outdoor ---
    if brightness > 160 and (iso is None or iso < 800):
        pp3 = get_preset_path("bright_outdoor")
        if pp3:
            return SceneClassification(
                preset="bright_outdoor",
                pp3_path=pp3,
                reason=f"Bright outdoor (brightness={brightness:.0f}, ISO={iso or '?'})",
                brightness=brightness,
                contrast=contrast,
            )

    # --- 5. Balanced (fallback) ---
    pp3 = get_preset_path("balanced") or get_preset_path("default_profile")
    return SceneClassification(
        preset="balanced",
        pp3_path=pp3 or _PROFILES_DIR / "balanced.pp3",
        reason="Balanced (no strong scene indicators)",
        brightness=brightness,
        contrast=contrast,
    )
