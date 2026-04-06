"""AI-powered super resolution / detail enhancement.

Upscales images using real-ESRGAN or a fast bicubic+sharpening pipeline.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class SuperResResult:
    """Result of a super resolution operation."""

    __slots__ = ("image", "scale_factor", "method")

    def __init__(self, image: np.ndarray, scale_factor: int, method: str) -> None:
        self.image = image  # uint8 RGB (H*scale, W*scale, 3)
        self.scale_factor = scale_factor
        self.method = method


def enhance_resolution(
    img: np.ndarray,
    scale: int = 2,
) -> SuperResResult:
    """Enhance image resolution.

    Tries Real-ESRGAN first, falls back to OpenCV super-resolution,
    then to sharp bicubic upscaling.

    Args:
        img: uint8 RGB (H, W, 3).
        scale: Upscale factor (2 or 4).

    Returns:
        SuperResResult with upscaled image.
    """
    scale = max(2, min(4, scale))

    # Try Real-ESRGAN
    result = _try_realesrgan(img, scale)
    if result is not None:
        return result

    # Try OpenCV DNN super-resolution
    result = _try_opencv_sr(img, scale)
    if result is not None:
        return result

    # Fallback: sharp bicubic
    return _bicubic_sharpen(img, scale)


def _try_realesrgan(img: np.ndarray, scale: int) -> Optional[SuperResResult]:
    """Try Real-ESRGAN for super resolution."""
    try:
        from pathlib import Path as _Path
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer

        # Locate pretrained model weights
        model_dir = _Path.home() / ".imagic" / "models"
        model_path = model_dir / f"RealESRGAN_x{scale}plus.pth"
        if not model_path.is_file():
            logger.debug("Real-ESRGAN model not found at %s", model_path)
            return None

        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=scale)
        upsampler = RealESRGANer(
            scale=scale,
            model_path=str(model_path),
            model=model,
            half=False,
        )
        output, _ = upsampler.enhance(img, outscale=scale)
        return SuperResResult(output, scale, "real-esrgan")

    except (ImportError, Exception) as e:
        logger.debug("Real-ESRGAN unavailable: %s", e)
        return None


def _try_opencv_sr(img: np.ndarray, scale: int) -> Optional[SuperResResult]:
    """Try OpenCV's DNN super-resolution module."""
    try:
        import cv2

        sr = cv2.dnn_superres.DnnSuperResImpl_create()
        # Check for EDSR model
        model_path = f"EDSR_x{scale}.pb"
        sr.readModel(model_path)
        sr.setModel("edsr", scale)
        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        result = sr.upsample(bgr)
        result_rgb = cv2.cvtColor(result, cv2.COLOR_BGR2RGB)
        return SuperResResult(result_rgb, scale, "opencv-edsr")

    except (ImportError, AttributeError, Exception) as e:
        logger.debug("OpenCV SR unavailable: %s", e)
        return None


def _bicubic_sharpen(img: np.ndarray, scale: int) -> SuperResResult:
    """Fallback: bicubic upscale with unsharp mask enhancement."""
    try:
        import cv2
        h, w = img.shape[:2]
        new_h, new_w = h * scale, w * scale
        upscaled = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

        # Apply unsharp mask for detail recovery
        blurred = cv2.GaussianBlur(upscaled, (0, 0), 1.5)
        sharpened = cv2.addWeighted(upscaled, 1.5, blurred, -0.5, 0)
        sharpened = np.clip(sharpened, 0, 255).astype(np.uint8)

        return SuperResResult(sharpened, scale, "bicubic-sharpen")

    except ImportError:
        # Pure numpy fallback
        h, w = img.shape[:2]
        upscaled = np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)
        return SuperResResult(upscaled, scale, "nearest-neighbor")


def enhance_details(img: np.ndarray) -> np.ndarray:
    """Enhance fine details in an image without upscaling.

    Uses multi-scale detail decomposition and enhancement.

    Args:
        img: uint8 RGB (H, W, 3).

    Returns:
        Detail-enhanced uint8 RGB image.
    """
    try:
        import cv2
        img_f = img.astype(np.float32) / 255.0

        # Multi-scale detail decomposition
        base = cv2.GaussianBlur(img_f, (0, 0), 3.0)
        detail_coarse = cv2.GaussianBlur(img_f, (0, 0), 1.0) - base
        detail_fine = img_f - cv2.GaussianBlur(img_f, (0, 0), 1.0)

        # Enhance details at each scale
        enhanced = base + detail_coarse * 1.3 + detail_fine * 1.8
        enhanced = np.clip(enhanced * 255, 0, 255).astype(np.uint8)
        return enhanced

    except ImportError:
        return img
