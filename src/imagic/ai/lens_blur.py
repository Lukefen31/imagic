"""AI-powered lens blur (synthetic bokeh / depth-of-field).

Uses depth estimation to create a depth map, then applies
variable-radius gaussian blur based on depth — simulating
shallow depth-of-field regardless of the lens used.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class LensBlurResult:
    """Result of a lens blur operation."""

    __slots__ = ("image", "depth_map")

    def __init__(self, image: np.ndarray, depth_map: np.ndarray) -> None:
        self.image = image  # uint8 RGB (H, W, 3)
        self.depth_map = depth_map  # float32 [0, 1] (H, W)


def estimate_depth(img: np.ndarray) -> Optional[np.ndarray]:
    """Estimate a monocular depth map from an RGB image.

    Tries MiDaS (via torch hub) first, falls back to a simple
    gradient-based heuristic if unavailable.

    Returns:
        float32 depth map (H, W) normalized to [0, 1], where 1 = closest.
    """
    # Try MiDaS small model
    try:
        import torch
        midas = torch.hub.load("intel-isl/MiDaS", "MiDaS_small", trust_repo=True)
        midas.eval()

        midas_transforms = torch.hub.load("intel-isl/MiDaS", "transforms", trust_repo=True)
        transform = midas_transforms.small_transform

        input_batch = transform(img)
        if torch.cuda.is_available():
            midas = midas.cuda()
            input_batch = input_batch.cuda()

        with torch.no_grad():
            prediction = midas(input_batch)
            prediction = torch.nn.functional.interpolate(
                prediction.unsqueeze(1),
                size=img.shape[:2],
                mode="bicubic",
                align_corners=False,
            ).squeeze()

        depth = prediction.cpu().numpy()
        depth = (depth - depth.min()) / (depth.max() - depth.min() + 1e-8)
        return depth.astype(np.float32)

    except (ImportError, Exception) as e:
        logger.info("MiDaS unavailable (%s), using heuristic depth estimation", e)

    # Fallback: heuristic depth based on position + edge density
    return _heuristic_depth(img)


def _heuristic_depth(img: np.ndarray) -> np.ndarray:
    """Simple depth estimation heuristic.

    Assumes: center subjects are closer, edges are further,
    lower parts of the image are closer (ground), upper is further (sky).
    """
    h, w = img.shape[:2]

    # Radial distance from center (closer to center = closer to camera)
    y, x = np.ogrid[:h, :w]
    cy, cx = h / 2.0, w / 2.0
    r_max = np.sqrt(cx ** 2 + cy ** 2)
    radial = 1.0 - np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / r_max

    # Vertical gradient (bottom = closer)
    vertical = np.linspace(0.7, 0.3, h)[:, np.newaxis]
    vertical = np.broadcast_to(vertical, (h, w))

    # Edge density (sharp areas = in-focus subjects = closer)
    try:
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
        edges = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
        edges = cv2.GaussianBlur(edges, (31, 31), 0)
        edges = edges / (edges.max() + 1e-8)
    except ImportError:
        edges = np.zeros((h, w), dtype=np.float32)

    depth = radial * 0.4 + vertical * 0.3 + edges * 0.3
    depth = (depth - depth.min()) / (depth.max() - depth.min() + 1e-8)
    return depth.astype(np.float32)


def apply_lens_blur(
    img: np.ndarray,
    blur_amount: int = 50,
    focus_point: Optional[Tuple[float, float]] = None,
    depth_map: Optional[np.ndarray] = None,
) -> LensBlurResult:
    """Apply depth-aware lens blur to simulate bokeh.

    Args:
        img: uint8 RGB (H, W, 3).
        blur_amount: Blur strength 0–100.
        focus_point: Optional (x_ratio, y_ratio) in [0, 1] for focus center.
        depth_map: Optional pre-computed depth map.

    Returns:
        LensBlurResult with blurred image and depth map.
    """
    if depth_map is None:
        depth_map = estimate_depth(img)
        if depth_map is None:
            depth_map = _heuristic_depth(img)

    # If focus point specified, adjust depth map so that point is "closest"
    if focus_point is not None:
        h, w = depth_map.shape
        fx, fy = int(focus_point[0] * w), int(focus_point[1] * h)
        fx = max(0, min(fx, w - 1))
        fy = max(0, min(fy, h - 1))
        focus_depth = depth_map[fy, fx]
        # Remap: pixels at same depth as focus = near, others = far
        depth_map = np.abs(depth_map - focus_depth)
        depth_map = depth_map / (depth_map.max() + 1e-8)

    # Invert so that far = high blur
    blur_map = depth_map  # 0 = focus, 1 = max blur

    # Apply variable blur using multiple blur passes at different radii
    max_radius = int(blur_amount * 0.4) + 1

    try:
        import cv2
        result = img.copy().astype(np.float32)
        # Use 4 discrete blur levels for performance
        levels = 4
        for i in range(1, levels + 1):
            radius = max(1, int(max_radius * i / levels))
            ksize = radius * 2 + 1
            blurred = cv2.GaussianBlur(img.astype(np.float32), (ksize, ksize), 0)
            lo = (i - 1) / levels
            hi = i / levels
            mask = np.clip((blur_map - lo) / (hi - lo + 1e-8), 0, 1)[:, :, np.newaxis]
            result = result * (1 - mask) + blurred * mask

        result = np.clip(result, 0, 255).astype(np.uint8)
    except ImportError:
        result = img  # No OpenCV = no blur

    return LensBlurResult(result, depth_map)
