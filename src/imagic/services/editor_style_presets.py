"""Bridge legacy scene presets into editor-compatible override values."""

from __future__ import annotations

from typing import Dict

LEGACY_STYLE_PRESETS: Dict[str, dict] = {
    "low_light": {
        "temperature": 8,
        "exposure": 30,
        "highlights": -25,
        "shadows": 24,
        "contrast": 10,
        "dehaze": 5,
        "saturation": 5,
        "nr_luminance": 24,
        "nr_color": 14,
        "sharp_amount": 10,
        "sharp_radius": 45,
    },
    "bright_outdoor": {
        "temperature": 5,
        "contrast": 24,
        "highlights": -16,
        "whites": -6,
        "dehaze": 8,
        "vibrance": 18,
        "saturation": 18,
        "sharp_amount": 35,
        "sharp_radius": 55,
    },
    "high_contrast": {
        "exposure": 15,
        "contrast": 34,
        "highlights": -28,
        "shadows": 20,
        "dehaze": 6,
        "saturation": 10,
        "sharp_amount": 24,
        "sharp_radius": 50,
    },
    "portrait": {
        "temperature": 4,
        "exposure": 6,
        "contrast": 8,
        "shadows": 10,
        "clarity": -5,
        "saturation": 5,
        "nr_luminance": 8,
        "sharp_amount": 8,
        "sharp_radius": 40,
    },
    "balanced": {
        "exposure": 10,
        "contrast": 15,
        "highlights": -18,
        "shadows": 10,
        "saturation": 8,
        "sharp_amount": 18,
        "sharp_radius": 50,
    },
}


def get_editor_style_overrides(preset: str) -> dict:
    """Return editor-compatible overrides for a legacy scene preset."""
    return dict(LEGACY_STYLE_PRESETS.get(preset, {}))


def merge_editor_overrides(base: dict, extra: dict) -> dict:
    """Merge two editor override dicts, adding numeric values where possible."""
    merged = dict(base or {})
    for key, value in (extra or {}).items():
        current = merged.get(key)
        if isinstance(current, (int, float)) and isinstance(value, (int, float)):
            merged[key] = int(current + value)
        else:
            merged[key] = value
    return merged
