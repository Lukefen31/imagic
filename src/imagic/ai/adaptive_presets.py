"""Adaptive AI presets — scene-aware automatic adjustments.

Analyzes image content and applies targeted adjustments per region/scene type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class SceneType(Enum):
    """Detected scene categories."""
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    NIGHT = "night"
    INDOOR = "indoor"
    MACRO = "macro"
    UNKNOWN = "unknown"


@dataclass
class AdaptivePreset:
    """A preset with region-targeted adjustments."""
    name: str
    scene: SceneType
    global_params: Dict[str, float] = field(default_factory=dict)
    masked_adjustments: List[Dict] = field(default_factory=list)
    description: str = ""


def detect_scene(img: np.ndarray) -> SceneType:
    """Classify scene type from image content.

    Uses color/brightness statistics and optional face detection heuristics.

    Args:
        img: uint8 RGB (H, W, 3).

    Returns:
        SceneType classification.
    """
    h, w = img.shape[:2]
    img_f = img.astype(np.float32) / 255.0

    mean_brightness = np.mean(img_f)
    top_third = img_f[: h // 3, :, :]
    bottom_two_thirds = img_f[h // 3 :, :, :]

    # Sky heuristic: top has high blue relative to rest
    top_blue_ratio = np.mean(top_third[:, :, 2]) - np.mean(top_third[:, :, 1])
    has_sky = top_blue_ratio > 0.03 and np.mean(top_third) > 0.4

    # Night detection
    if mean_brightness < 0.2:
        return SceneType.NIGHT

    # Landscape: sky + green vegetation in lower portion
    green_ratio = np.mean(bottom_two_thirds[:, :, 1]) - (
        np.mean(bottom_two_thirds[:, :, 0]) + np.mean(bottom_two_thirds[:, :, 2])
    ) / 2
    if has_sky and green_ratio > 0.01:
        return SceneType.LANDSCAPE

    # Portrait: check for face-like skin tones in center
    center_crop = img_f[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4]
    r, g, b = center_crop[:, :, 0], center_crop[:, :, 1], center_crop[:, :, 2]
    skin_mask = (r > 0.3) & (g > 0.2) & (r > g) & (r > b) & (np.abs(r - g) > 0.05)
    skin_ratio = np.mean(skin_mask.astype(np.float32))
    if skin_ratio > 0.15:
        return SceneType.PORTRAIT

    # Macro: high detail variance in small area
    try:
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if laplacian_var > 1500:
            return SceneType.MACRO
    except ImportError:
        pass

    if not has_sky and mean_brightness > 0.25:
        return SceneType.INDOOR

    return SceneType.UNKNOWN


def get_adaptive_preset(scene: SceneType) -> AdaptivePreset:
    """Get the best adaptive preset for a scene type."""
    presets = {
        SceneType.PORTRAIT: AdaptivePreset(
            name="Adaptive Portrait",
            scene=SceneType.PORTRAIT,
            global_params={
                "exposure": 25,
                "contrast": 8,
                "shadows": 20,
                "clarity": -10,  # soften slightly
                "vibrance": 10,
                "saturation": -5,
            },
            masked_adjustments=[
                {
                    "mask_type": "people",
                    "params": {
                        "exposure": 20,
                        "clarity": -15,
                        "sharp_amount": 15,
                    },
                },
                {
                    "mask_type": "background",
                    "params": {
                        "clarity": 10,
                        "saturation": -10,
                    },
                },
            ],
            description="Enhances skin tones, softens skin texture, sharpens eyes, blurs background slightly.",
        ),
        SceneType.LANDSCAPE: AdaptivePreset(
            name="Adaptive Landscape",
            scene=SceneType.LANDSCAPE,
            global_params={
                "exposure": 15,
                "contrast": 10,
                "shadows": 15,
                "clarity": 20,
                "vibrance": 15,
                "dehaze": 10,
                "saturation": 5,
            },
            masked_adjustments=[
                {
                    "mask_type": "sky",
                    "params": {
                        "exposure": -10,
                        "contrast": 10,
                        "vibrance": 15,
                        "hsl_sat_blue": 15,
                    },
                },
            ],
            description="Boosts clarity and color across the scene, darkens and enriches sky.",
        ),
        SceneType.NIGHT: AdaptivePreset(
            name="Adaptive Night",
            scene=SceneType.NIGHT,
            global_params={
                "exposure": 50,
                "contrast": 15,
                "shadows": 40,
                "nr_luminance": 30,
                "vibrance": 10,
                "clarity": 10,
            },
            description="Lifts shadows, reduces noise, enhances contrast for low-light shots.",
        ),
        SceneType.INDOOR: AdaptivePreset(
            name="Adaptive Indoor",
            scene=SceneType.INDOOR,
            global_params={
                "exposure": 20,
                "temperature": 15,
                "contrast": 8,
                "shadows": 25,
                "clarity": 10,
                "nr_luminance": 15,
                "vibrance": 8,
            },
            description="Warms tones, lifts shadows, mild noise reduction for indoor lighting.",
        ),
        SceneType.MACRO: AdaptivePreset(
            name="Adaptive Macro",
            scene=SceneType.MACRO,
            global_params={
                "contrast": 10,
                "clarity": 25,
                "sharp_amount": 20,
                "vibrance": 15,
                "saturation": 5,
            },
            description="Maximizes fine detail sharpness and micro-contrast for close-up shots.",
        ),
        SceneType.UNKNOWN: AdaptivePreset(
            name="Adaptive Auto",
            scene=SceneType.UNKNOWN,
            global_params={
                "exposure": 18,
                "contrast": 8,
                "clarity": 10,
                "vibrance": 10,
                "shadows": 20,
            },
            description="Balanced adaptive adjustments for general photography.",
        ),
    }
    return presets.get(scene, presets[SceneType.UNKNOWN])


def apply_adaptive_preset(
    img: np.ndarray,
    preset: Optional[AdaptivePreset] = None,
) -> tuple[AdaptivePreset, Dict[str, float]]:
    """Detect scene and return parameters for the adaptive preset.

    Does NOT apply edits directly — returns the parameter dict so the
    normal editing pipeline can apply them.

    Args:
        img: uint8 RGB (H, W, 3).
        preset: Optionally supply a preset; otherwise auto-detect.

    Returns:
        Tuple of (chosen preset, flat params dict for the editor).
    """
    if preset is None:
        scene = detect_scene(img)
        logger.info("Detected scene: %s", scene.value)
        preset = get_adaptive_preset(scene)

    return preset, preset.global_params.copy()
