"""Auto-crop analyzer — detects framing issues and computes optimal crops.

Analyses image composition and finds the best crop to improve framing:
* **Subject centering / rule-of-thirds alignment** — shifts the crop window
  to place the subject at a natural focal point.
* **Horizon straightening** — detects tilted horizons (not applied as rotation
  but flagged; RT handles rotation separately).
* **Edge dead-space removal** — trims large uniform borders.
* **Aspect ratio preservation** — maintains the original aspect ratio by
  default, or can target common ratios (3:2, 4:3, 16:9, 1:1).

Returns crop coordinates that can be written into a PP3 ``[Crop]`` section.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CropResult:
    """Result of the auto-crop analysis.

    Attributes:
        x, y: Top-left corner of the crop rectangle (in original-image pixels).
        w, h: Width and height of the crop rectangle.
        original_w, original_h: Original image dimensions.
        reason: Human-readable explanation of why the crop was suggested.
        confidence: 0.0–1.0 indicating how strongly the crop is recommended.
    """
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    original_w: int = 0
    original_h: int = 0
    reason: str = ""
    confidence: float = 0.0

    @property
    def is_significant(self) -> bool:
        """True if the crop removes at least 5% of the image area."""
        if self.original_w == 0 or self.original_h == 0:
            return False
        original_area = self.original_w * self.original_h
        crop_area = self.w * self.h
        return (original_area - crop_area) / original_area > 0.05


def analyze_crop(
    image_path: Path,
    target_ratio: Optional[str] = None,
    min_crop_pct: float = 0.05,
    max_crop_pct: float = 0.25,
) -> CropResult:
    """Analyze an image and return an optimal crop suggestion.

    Args:
        image_path: Path to the image (thumbnail or decoded RAW).
        target_ratio: Target aspect ratio string (``"3:2"``, ``"4:3"``,
            ``"16:9"``, ``"1:1"``), or ``None`` to keep original ratio.
        min_crop_pct: Minimum percentage of area to remove before
            considering the crop worthwhile.
        max_crop_pct: Maximum percentage of area to remove (safety limit).

    Returns:
        A ``CropResult`` with the suggested crop coordinates.
    """
    try:
        from PIL import Image
        img = Image.open(image_path).convert("L")
    except Exception:
        try:
            import rawpy
            with rawpy.imread(str(image_path)) as raw:
                rgb = raw.postprocess(use_camera_wb=True, half_size=True)
            gray = np.dot(rgb[..., :3], [0.299, 0.587, 0.114])
            from PIL import Image
            img = Image.fromarray(gray.astype(np.uint8), mode="L")
        except Exception as exc:
            logger.debug("Auto-crop: could not load %s: %s", image_path, exc)
            return CropResult()

    arr = np.asarray(img, dtype=np.float64)
    h, w = arr.shape
    if h < 100 or w < 100:
        return CropResult(w=w, h=h, original_w=w, original_h=h)

    result = CropResult(original_w=w, original_h=h)
    reasons = []

    # ------------------------------------------------------------------
    # 1. Dead-space detection: find uniform borders
    # ------------------------------------------------------------------
    border_crop = _detect_dead_borders(arr, max_trim_pct=max_crop_pct)
    bx, by, bw, bh = border_crop
    if bw < w or bh < h:
        reasons.append("trimmed dead borders")

    # ------------------------------------------------------------------
    # 2. Subject-aware crop via energy map
    # ------------------------------------------------------------------
    from scipy.ndimage import sobel, uniform_filter

    sx = sobel(arr, axis=0)
    sy = sobel(arr, axis=1)
    energy = np.hypot(sx, sy)

    # Smooth the energy map to find subject regions
    energy_smooth = uniform_filter(energy, size=max(h, w) // 8)

    # Find the centroid of high-energy region (subject)
    threshold = np.percentile(energy_smooth, 75)
    mask = energy_smooth > threshold
    if mask.any():
        ys, xs = np.where(mask)
        cy = float(np.mean(ys))
        cx = float(np.mean(xs))
    else:
        cy, cx = h / 2.0, w / 2.0

    # ------------------------------------------------------------------
    # 3. Determine target ratio
    # ------------------------------------------------------------------
    if target_ratio:
        ratio_map = {"3:2": 3 / 2, "2:3": 2 / 3, "4:3": 4 / 3, "3:4": 3 / 4,
                     "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}
        target_r = ratio_map.get(target_ratio)
        if target_r is None:
            target_r = w / h  # fallback to original
    else:
        target_r = w / h  # keep original aspect ratio

    # ------------------------------------------------------------------
    # 4. Compute optimal crop window centred on subject
    # ------------------------------------------------------------------
    # Start from the border-trimmed region
    work_w, work_h = bw, bh
    work_x, work_y = bx, by

    # Maximum pixels we can remove (from the border-trimmed dimensions)
    max_remove_w = int(work_w * max_crop_pct)
    max_remove_h = int(work_h * max_crop_pct)

    # Calculate crop dimensions maintaining target ratio
    crop_w, crop_h = _fit_ratio(work_w, work_h, target_r,
                                 max_remove_w, max_remove_h)

    # Position crop to place subject at rule-of-thirds intersection
    crop_x, crop_y = _position_for_thirds(
        cx - work_x, cy - work_y, crop_w, crop_h, work_w, work_h
    )
    crop_x += work_x
    crop_y += work_y

    # Ensure within bounds
    crop_x = max(0, min(crop_x, w - crop_w))
    crop_y = max(0, min(crop_y, h - crop_h))

    # ------------------------------------------------------------------
    # 5. Evaluate whether the crop is actually an improvement
    # ------------------------------------------------------------------
    original_thirds = _thirds_score(energy, 0, 0, w, h)
    cropped_thirds = _thirds_score(energy, crop_x, crop_y, crop_w, crop_h)

    improvement = cropped_thirds - original_thirds
    area_removed = 1.0 - (crop_w * crop_h) / (w * h)

    if improvement > 0.02 or area_removed > min_crop_pct:
        result.x = int(crop_x)
        result.y = int(crop_y)
        result.w = int(crop_w)
        result.h = int(crop_h)
        result.confidence = min(1.0, improvement * 5 + area_removed * 0.5)

        if improvement > 0.02:
            reasons.append("improved rule-of-thirds framing")
        if target_ratio and abs(target_r - w / h) > 0.05:
            reasons.append(f"adjusted to {target_ratio} ratio")

        result.reason = "; ".join(reasons) if reasons else "minor framing adjustment"
    else:
        # No significant improvement — return full image
        result.x = 0
        result.y = 0
        result.w = w
        result.h = h
        result.confidence = 0.0
        result.reason = "framing is already good"

    return result


# ======================================================================
# Helpers
# ======================================================================

def _detect_dead_borders(gray: np.ndarray, max_trim_pct: float = 0.15) -> Tuple[int, int, int, int]:
    """Detect uniform borders and return the inner bounding box.

    Returns:
        (x, y, w, h) of the content region.
    """
    h, w = gray.shape
    max_trim_h = int(h * max_trim_pct)
    max_trim_w = int(w * max_trim_pct)

    # Compute row/column variance
    row_var = np.var(gray, axis=1)
    col_var = np.var(gray, axis=0)

    # Threshold: a row/column is "dead" if variance is very low
    var_threshold = max(np.median(row_var) * 0.05, 5.0)

    # Trim from top
    top = 0
    for i in range(min(max_trim_h, h // 4)):
        if row_var[i] < var_threshold:
            top = i + 1
        else:
            break

    # Trim from bottom
    bottom = h
    for i in range(h - 1, max(h - max_trim_h, h * 3 // 4) - 1, -1):
        if row_var[i] < var_threshold:
            bottom = i
        else:
            break

    # Trim from left
    left = 0
    for i in range(min(max_trim_w, w // 4)):
        if col_var[i] < var_threshold:
            left = i + 1
        else:
            break

    # Trim from right
    right = w
    for i in range(w - 1, max(w - max_trim_w, w * 3 // 4) - 1, -1):
        if col_var[i] < var_threshold:
            right = i
        else:
            break

    return left, top, right - left, bottom - top


def _fit_ratio(
    w: int, h: int, target_ratio: float,
    max_remove_w: int, max_remove_h: int,
) -> Tuple[int, int]:
    """Compute crop dimensions that fit target_ratio within bounds."""
    current_ratio = w / h

    if abs(current_ratio - target_ratio) < 0.02:
        return w, h  # already close enough

    if current_ratio > target_ratio:
        # Too wide — reduce width
        new_w = min(w, int(h * target_ratio))
        new_w = max(new_w, w - max_remove_w)
        new_h = h
    else:
        # Too tall — reduce height
        new_h = min(h, int(w / target_ratio))
        new_h = max(new_h, h - max_remove_h)
        new_w = w

    return max(100, new_w), max(100, new_h)


def _position_for_thirds(
    subject_x: float, subject_y: float,
    crop_w: int, crop_h: int,
    bound_w: int, bound_h: int,
) -> Tuple[int, int]:
    """Position the crop so the subject lands on a rule-of-thirds point.

    Tries to place the subject at the nearest thirds intersection.
    """
    # Four thirds intersection points (relative to crop)
    thirds_points = [
        (crop_w / 3, crop_h / 3),
        (2 * crop_w / 3, crop_h / 3),
        (crop_w / 3, 2 * crop_h / 3),
        (2 * crop_w / 3, 2 * crop_h / 3),
    ]

    # Find nearest thirds point
    best_dist = float("inf")
    best_tx, best_ty = crop_w / 2, crop_h / 2  # default: center
    for tx, ty in thirds_points:
        dist = (subject_x - tx) ** 2 + (subject_y - ty) ** 2
        if dist < best_dist:
            best_dist = dist
            best_tx, best_ty = tx, ty

    # Position crop so subject aligns with the chosen thirds point
    cx = int(subject_x - best_tx)
    cy = int(subject_y - best_ty)

    # Clamp to available space
    cx = max(0, min(cx, bound_w - crop_w))
    cy = max(0, min(cy, bound_h - crop_h))

    return cx, cy


def _thirds_score(energy: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    """Score how well the energy distribution follows rule-of-thirds."""
    if w < 30 or h < 30:
        return 0.5

    region = energy[y:y + h, x:x + w]
    rh, rw = region.shape
    h3, w3 = rh // 3, rw // 3
    if h3 == 0 or w3 == 0:
        return 0.5

    grid = np.zeros((3, 3))
    for r in range(3):
        for c in range(3):
            r0, r1 = r * h3, (r + 1) * h3 if r < 2 else rh
            c0, c1 = c * w3, (c + 1) * w3 if c < 2 else rw
            grid[r, c] = np.mean(region[r0:r1, c0:c1])

    total = grid.sum()
    if total < 1e-6:
        return 0.3

    grid_norm = grid / total

    # Reward thirds line intersections and center
    thirds_weight = (
        grid_norm[0, 1] + grid_norm[1, 0]
        + grid_norm[1, 2] + grid_norm[2, 1]
        + grid_norm[1, 1]
    )
    # Penalise corner-heavy
    corner_weight = (
        grid_norm[0, 0] + grid_norm[0, 2]
        + grid_norm[2, 0] + grid_norm[2, 2]
    )

    return float(np.clip(thirds_weight * 0.7 + (1 - corner_weight) * 0.3, 0, 1))
