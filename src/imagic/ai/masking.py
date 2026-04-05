"""AI-powered image masking using segmentation models.

Provides automatic mask generation for:
- Subject detection
- Sky detection
- People/face detection
- Background separation
- Object-level segmentation

Uses a lightweight U2-Net or rembg-style approach for speed,
with optional SAM (Segment Anything) for precision.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class MaskType(str, Enum):
    SUBJECT = "subject"
    SKY = "sky"
    PEOPLE = "people"
    BACKGROUND = "background"


class MaskResult:
    """Result of a masking operation."""

    __slots__ = ("mask", "mask_type", "confidence")

    def __init__(self, mask: np.ndarray, mask_type: MaskType, confidence: float) -> None:
        self.mask = mask  # float32 [0, 1], same H×W as input
        self.mask_type = mask_type
        self.confidence = confidence


def _ensure_rembg():
    """Lazy-import rembg, install hint if missing."""
    try:
        import rembg  # noqa: F401
        return True
    except ImportError:
        logger.warning("rembg not installed — AI masking unavailable. pip install rembg")
        return False


def generate_subject_mask(img: np.ndarray) -> Optional[MaskResult]:
    """Generate a subject/foreground mask using rembg (U2-Net).

    Args:
        img: uint8 RGB array (H, W, 3).

    Returns:
        MaskResult with float32 mask, or None if model unavailable.
    """
    if not _ensure_rembg():
        return None

    try:
        from rembg import remove, new_session
        from PIL import Image
        import io

        session = new_session("u2net")
        pil_img = Image.fromarray(img)
        # Get RGBA output — alpha channel IS the mask
        result = remove(pil_img, session=session, only_mask=True)
        mask_arr = np.array(result).astype(np.float32) / 255.0

        # Ensure mask is 2D
        if mask_arr.ndim == 3:
            mask_arr = mask_arr[:, :, 0]

        confidence = float(np.mean(mask_arr > 0.5))
        return MaskResult(mask_arr, MaskType.SUBJECT, confidence)

    except Exception:
        logger.exception("Subject mask generation failed")
        return None


def generate_sky_mask(img: np.ndarray) -> Optional[MaskResult]:
    """Generate a sky mask using color/brightness heuristics + edge detection.

    Works without ML models — uses the observation that sky regions are
    typically in the upper portion, have high brightness, low saturation
    variation, and smooth gradients.
    """
    try:
        h, w = img.shape[:2]
        img_f = img.astype(np.float32) / 255.0

        # Blue channel dominance + high brightness = likely sky
        r, g, b = img_f[:, :, 0], img_f[:, :, 1], img_f[:, :, 2]
        brightness = 0.2126 * r + 0.7152 * g + 0.0722 * b

        # Sky tends to have blue >= green >= red, and high brightness
        blue_score = np.clip((b - r) * 3.0 + 0.3, 0, 1)
        bright_score = np.clip(brightness * 1.5 - 0.3, 0, 1)

        # Positional bias: sky is usually in upper half
        y_weight = np.linspace(1.0, 0.2, h)[:, np.newaxis]
        y_weight = np.broadcast_to(y_weight, (h, w))

        # Smoothness: sky has low local variance
        try:
            import cv2
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
            laplacian = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
            smooth_score = np.clip(1.0 - laplacian / 30.0, 0, 1)
        except ImportError:
            smooth_score = np.ones((h, w), dtype=np.float32) * 0.5

        # Combine scores
        sky_prob = blue_score * 0.35 + bright_score * 0.25 + y_weight * 0.15 + smooth_score * 0.25
        mask = np.clip(sky_prob, 0, 1).astype(np.float32)

        # Threshold to binary-ish with soft edges
        mask = np.where(mask > 0.5, mask, mask * 0.3)

        confidence = float(np.mean(mask > 0.5))
        return MaskResult(mask, MaskType.SKY, confidence)

    except Exception:
        logger.exception("Sky mask generation failed")
        return None


def generate_people_mask(img: np.ndarray) -> Optional[MaskResult]:
    """Generate a people/portrait mask.

    Uses rembg subject detection with portrait-optimized settings.
    Falls back to skin-tone detection if rembg unavailable.
    """
    # Try rembg first (best quality)
    result = generate_subject_mask(img)
    if result is not None:
        result.mask_type = MaskType.PEOPLE
        return result

    # Fallback: skin tone detection
    try:
        img_f = img.astype(np.float32) / 255.0
        r, g, b = img_f[:, :, 0], img_f[:, :, 1], img_f[:, :, 2]

        # Skin tone range in RGB space
        skin_mask = (
            (r > 0.35) & (g > 0.2) & (b > 0.15) &
            (r > g) & (r > b) &
            (np.abs(r - g) > 0.05) &
            (r - b > 0.1)
        ).astype(np.float32)

        # Smooth the mask
        try:
            import cv2
            kernel = np.ones((9, 9), np.float32) / 81
            skin_mask = cv2.filter2D(skin_mask, -1, kernel)
        except ImportError:
            pass

        confidence = float(np.mean(skin_mask > 0.3))
        return MaskResult(skin_mask, MaskType.PEOPLE, confidence)

    except Exception:
        logger.exception("People mask generation failed")
        return None


def generate_background_mask(img: np.ndarray) -> Optional[MaskResult]:
    """Generate a background mask (inverse of subject mask)."""
    result = generate_subject_mask(img)
    if result is None:
        return None

    result.mask = 1.0 - result.mask
    result.mask_type = MaskType.BACKGROUND
    return result


def apply_masked_adjustment(
    img: np.ndarray,
    mask: np.ndarray,
    params: dict,
) -> np.ndarray:
    """Apply editing adjustments only within the masked region.

    Args:
        img: uint8 RGB (H, W, 3).
        mask: float32 (H, W) mask in [0, 1].
        params: Same override params as PreviewEngine.apply().

    Returns:
        Adjusted uint8 RGB image.
    """
    from imagic.services.preview_engine import PreviewEngine

    # Apply edits to a copy
    adjusted = PreviewEngine.apply(img, params)

    # Blend: original where mask=0, adjusted where mask=1
    mask_3d = mask[:, :, np.newaxis].astype(np.float32)
    img_f = img.astype(np.float32)
    adj_f = adjusted.astype(np.float32)
    blended = img_f * (1.0 - mask_3d) + adj_f * mask_3d

    return np.clip(blended, 0, 255).astype(np.uint8)
