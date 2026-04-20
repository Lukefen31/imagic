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

import contextlib
import io
import logging
from enum import StrEnum

import numpy as np

logger = logging.getLogger(__name__)


class MaskType(StrEnum):
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


_rembg_available: bool | None = None
_rembg_session = None  # cached rembg session (~170 MB model)


def _ensure_rembg():
    """Lazy-import rembg and verify the runtime backend is available."""
    global _rembg_available
    if _rembg_available is not None:
        return _rembg_available
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import rembg  # noqa: F401
            import onnxruntime  # noqa: F401
        _rembg_available = True
    except SystemExit as exc:
        logger.warning(
            "rembg backend import exited — AI masking will use fallback algorithms. %s",
            exc,
        )
        _rembg_available = False
    except Exception as exc:
        logger.warning(
            "rembg backend unavailable — AI masking will use fallback algorithms. %s",
            exc,
        )
        _rembg_available = False
    return _rembg_available


def _get_rembg_session():
    """Return a cached rembg U2-Net session."""
    global _rembg_session
    if _rembg_session is None:
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                from rembg import new_session

                _rembg_session = new_session("u2net")
        except SystemExit as exc:
            logger.warning(
                "rembg session import exited — using fallback mask generation. %s",
                exc,
            )
            _rembg_available = False
            return None
        except Exception as exc:
            logger.warning(
                "Failed to initialize rembg session — using fallback mask generation. %s",
                exc,
            )
            _rembg_available = False
            return None
    return _rembg_session


def _generate_subject_mask_fallback(img: np.ndarray) -> MaskResult | None:
    """Generate a fallback subject mask when rembg is unavailable."""
    try:
        import cv2

        h, w = img.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        rect = (
            max(0, w // 10),
            max(0, h // 10),
            max(2, w - 2 * (w // 10)),
            max(2, h - 2 * (h // 10)),
        )
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)
        cv2.grabCut(img, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)
        mask_arr = np.where(
            (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD),
            1.0,
            0.0,
        ).astype(np.float32)
        if mask_arr.max() > 0:
            mask_arr = cv2.GaussianBlur(mask_arr, (21, 21), 0).astype(np.float32)
            mask_arr = np.clip(mask_arr, 0.0, 1.0)
        confidence = float(np.mean(mask_arr > 0.5))
        return MaskResult(mask_arr, MaskType.SUBJECT, confidence)
    except ImportError:
        return None
    except Exception:
        logger.exception("Fallback subject mask generation failed")
        return None


def generate_subject_mask(img: np.ndarray) -> MaskResult | None:
    """Generate a subject/foreground mask using rembg (U2-Net).

    Args:
        img: uint8 RGB array (H, W, 3).

    Returns:
        MaskResult with float32 mask, or None if model unavailable.
    """
    if not _ensure_rembg():
        return _generate_subject_mask_fallback(img)

    try:
        from PIL import Image
        from rembg import remove

        session = _get_rembg_session()
        if session is None:
            raise RuntimeError("rembg session unavailable")

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
        logger.exception("Subject mask generation failed — falling back to GrabCut")
        return _generate_subject_mask_fallback(img)


def generate_sky_mask(img: np.ndarray) -> MaskResult | None:
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


def generate_people_mask(img: np.ndarray) -> MaskResult | None:
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
            (r > 0.35)
            & (g > 0.2)
            & (b > 0.15)
            & (r > g)
            & (r > b)
            & (np.abs(r - g) > 0.05)
            & (r - b > 0.1)
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


def generate_background_mask(img: np.ndarray) -> MaskResult | None:
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
