"""Quality scorer — rates image technical quality (sharpness, exposure, noise).

This is the built-in "lightweight" analyser that does **not** require a GPU
or large ML model download.  It uses ``scipy`` / ``Pillow`` / ``numpy`` to
compute:

* **Laplacian variance** — proxy for sharpness / blur detection.
* **Histogram spread** — proxy for exposure quality.
* **Edge density** — proxy for detail richness.
* **Noise estimate** — high-frequency energy ratio.
* **Composition** — visual weight distribution (rule-of-thirds heuristic).
* **Face quality** — optional face detection via OpenCV Haar cascades.

Each sub-score produces a human-readable verdict.  The per-metric verdicts
are collected as *cull reasons* so the user can understand **why** a photo
was kept or trashed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from imagic.ai.base_analyzer import AnalysisResult, BaseAnalyzer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------


def _verdict(score: float, thresholds: List[Tuple[float, str]]) -> str:
    """Return the first label whose threshold is met (descending order)."""
    for t, label in thresholds:
        if score >= t:
            return label
    return thresholds[-1][1]


# ---------------------------------------------------------------------------
# Face detection (optional — requires opencv-python-headless)
# ---------------------------------------------------------------------------
_FACE_CASCADE = None


def _get_face_cascade():
    """Lazy-load the Haar cascade for frontal face detection."""
    global _FACE_CASCADE
    if _FACE_CASCADE is not None:
        return _FACE_CASCADE
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _FACE_CASCADE = cv2.CascadeClassifier(cascade_path)
        if _FACE_CASCADE.empty():
            _FACE_CASCADE = None
    except Exception:
        _FACE_CASCADE = None
    return _FACE_CASCADE


def _analyse_faces(gray: np.ndarray) -> Dict:
    """Detect faces and return a quality assessment.

    Returns a dict with keys ``score``, ``verdict``, ``detail``, ``count``.
    """
    cascade = _get_face_cascade()
    if cascade is None:
        return {"score": None, "verdict": "n/a", "detail": "OpenCV not available", "count": 0}

    import cv2

    h, w = gray.shape[:2]
    # Resize for speed — 480px wide is enough for face detection.
    scale = min(1.0, 480.0 / max(w, 1))
    if scale < 1.0:
        small = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        small = gray

    faces = cascade.detectMultiScale(
        small, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30),
    )

    if len(faces) == 0:
        return {"score": 0.5, "verdict": "no faces", "detail": "No faces detected", "count": 0}

    # Check face sizes relative to image.
    img_area = small.shape[0] * small.shape[1]
    face_areas = [(fw * fh) / img_area for (_, _, fw, fh) in faces]
    max_face = max(face_areas)
    avg_face = sum(face_areas) / len(face_areas)

    # Tiny faces (< 1% of image) are likely obstructed or far away.
    if max_face < 0.01:
        return {
            "score": 0.25,
            "verdict": "tiny/obstructed faces",
            "detail": f"{len(faces)} face(s) detected but very small ({max_face:.1%} of image)",
            "count": len(faces),
        }

    # Partial face: if the face box is clipped at the image edge.
    edge_clipped = 0
    sh, sw = small.shape[:2]
    for (fx, fy, fw, fh) in faces:
        if fx <= 2 or fy <= 2 or (fx + fw) >= sw - 2 or (fy + fh) >= sh - 2:
            edge_clipped += 1

    if edge_clipped > 0:
        ratio = edge_clipped / len(faces)
        score = 0.4 if ratio > 0.5 else 0.6
        return {
            "score": score,
            "verdict": "partially cropped faces",
            "detail": f"{edge_clipped}/{len(faces)} face(s) cropped at image edge",
            "count": len(faces),
        }

    # Good faces — score based on size.
    if max_face > 0.05:
        return {"score": 0.9, "verdict": "clear face(s)", "detail": f"{len(faces)} well-framed face(s)", "count": len(faces)}
    else:
        return {"score": 0.7, "verdict": "small face(s)", "detail": f"{len(faces)} face(s), relatively small", "count": len(faces)}


class QualityScorer(BaseAnalyzer):
    """Comprehensive image quality scorer with per-metric verdicts.

    Produces a final 0.0–1.0 score plus a detailed ``cull_reasons`` dict
    explaining each sub-score.

    Args:
        sharpness_weight: Weight for the Laplacian-variance sub-score.
        exposure_weight: Weight for the histogram sub-score.
        detail_weight: Weight for the edge-density sub-score.
        noise_weight: Weight for the noise estimation sub-score.
        composition_weight: Weight for the composition sub-score.
        face_weight: Weight for face quality (0 if OpenCV unavailable).
        blur_hard_threshold: Sharpness below this triggers a harsh penalty.
    """

    def __init__(
        self,
        sharpness_weight: float = 0.30,
        exposure_weight: float = 0.20,
        detail_weight: float = 0.10,
        noise_weight: float = 0.10,
        composition_weight: float = 0.10,
        face_weight: float = 0.20,
        blur_hard_threshold: float = 0.20,
    ) -> None:
        self._sw = sharpness_weight
        self._ew = exposure_weight
        self._dw = detail_weight
        self._nw = noise_weight
        self._cw = composition_weight
        self._fw = face_weight
        self._blur_hard = blur_hard_threshold

    @property
    def name(self) -> str:  # noqa: D401
        return "QualityScorer"

    @staticmethod
    def _load_grayscale(file_path: Path) -> np.ndarray:
        """Load an image as a float64 grayscale array.

        Tries Pillow first; falls back to rawpy for RAW formats that
        Pillow cannot decode (e.g. .ARW, .CR2, .NEF).
        """
        try:
            from PIL import Image
            img = Image.open(file_path).convert("L")
            return np.asarray(img, dtype=np.float64)
        except Exception:
            import rawpy
            with rawpy.imread(str(file_path)) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True, half_size=True, no_auto_bright=False,
                )
            gray = np.dot(rgb[..., :3], [0.299, 0.587, 0.114])
            return gray.astype(np.float64)

    def analyse(self, file_path: Path) -> AnalysisResult:
        """Score a single image with detailed per-metric reasons.

        Args:
            file_path: Path to the image (JPEG thumbnail or decoded RAW).

        Returns:
            ``AnalysisResult`` with ``score`` in [0, 1] and ``labels``
            containing a ``cull_reasons`` list of per-metric verdicts.
        """
        self.ensure_model()
        try:
            arr = self._load_grayscale(file_path)

            reasons: List[Dict] = []

            # --- Sharpness ---
            sharpness = self._laplacian_variance(arr)
            sharp_verdict = _verdict(sharpness, [
                (0.7, "Very sharp"),
                (0.45, "Sharp"),
                (0.25, "Slightly soft"),
                (0.12, "Blurry"),
                (0.0, "Very blurry"),
            ])
            reasons.append({
                "metric": "Sharpness",
                "score": round(sharpness, 3),
                "verdict": sharp_verdict,
            })

            # --- Exposure ---
            exposure = self._histogram_score(arr)
            exp_verdict = _verdict(exposure, [
                (0.7, "Well exposed"),
                (0.4, "Acceptable exposure"),
                (0.2, "Poor exposure"),
                (0.0, "Very poor exposure"),
            ])
            reasons.append({
                "metric": "Exposure",
                "score": round(exposure, 3),
                "verdict": exp_verdict,
            })

            # --- Detail richness ---
            detail = self._edge_density(arr)
            det_verdict = _verdict(detail, [
                (0.6, "Rich detail"),
                (0.3, "Moderate detail"),
                (0.1, "Low detail"),
                (0.0, "Very low detail"),
            ])
            reasons.append({
                "metric": "Detail",
                "score": round(detail, 3),
                "verdict": det_verdict,
            })

            # --- Noise ---
            noise = self._noise_estimate(arr)
            noise_verdict = _verdict(noise, [
                (0.7, "Clean"),
                (0.4, "Moderate noise"),
                (0.15, "Noisy"),
                (0.0, "Very noisy"),
            ])
            reasons.append({
                "metric": "Noise",
                "score": round(noise, 3),
                "verdict": noise_verdict,
            })

            # --- Composition ---
            composition = self._composition_score(arr)
            comp_verdict = _verdict(composition, [
                (0.7, "Good composition"),
                (0.4, "Fair composition"),
                (0.2, "Poor framing"),
                (0.0, "Very poor framing"),
            ])
            reasons.append({
                "metric": "Composition",
                "score": round(composition, 3),
                "verdict": comp_verdict,
            })

            # --- Face quality (optional) ---
            face_info = _analyse_faces(arr.astype(np.uint8))
            face_score = face_info["score"]
            has_faces = face_score is not None
            if has_faces:
                reasons.append({
                    "metric": "Faces",
                    "score": round(face_score, 3),
                    "verdict": face_info["verdict"],
                    "detail": face_info["detail"],
                })

            # --- Weighted combination ---
            # Re-normalise weights if face detection unavailable.
            if has_faces:
                total_w = self._sw + self._ew + self._dw + self._nw + self._cw + self._fw
                raw = (
                    self._sw * sharpness
                    + self._ew * exposure
                    + self._dw * detail
                    + self._nw * noise
                    + self._cw * composition
                    + self._fw * face_score
                ) / total_w
            else:
                total_w = self._sw + self._ew + self._dw + self._nw + self._cw
                raw = (
                    self._sw * sharpness
                    + self._ew * exposure
                    + self._dw * detail
                    + self._nw * noise
                    + self._cw * composition
                ) / total_w

            # --- Hard penalties ---
            penalty_reasons: List[str] = []

            # Severe blur penalty.
            if sharpness < self._blur_hard:
                penalty = 0.6 if sharpness < 0.10 else 0.75
                raw *= penalty
                penalty_reasons.append(f"Blur penalty ({sharp_verdict})")

            # Very dark images.
            mean_brightness = float(np.mean(arr)) / 255.0
            if mean_brightness < 0.08:
                raw *= 0.7
                penalty_reasons.append("Very dark image")

            # Heavy clipping (>10% pixels at 0 or 255).
            clip_ratio = (np.sum(arr < 1) + np.sum(arr > 254)) / arr.size
            if clip_ratio > 0.10:
                raw *= 0.8
                penalty_reasons.append(f"Heavy clipping ({clip_ratio:.0%} of pixels)")

            if penalty_reasons:
                reasons.append({
                    "metric": "Penalties",
                    "score": None,
                    "verdict": "; ".join(penalty_reasons),
                })

            score = float(np.clip(raw, 0.0, 1.0))

            return AnalysisResult(
                file_path=file_path,
                score=round(score, 4),
                labels={
                    "sharpness": round(sharpness, 4),
                    "exposure": round(exposure, 4),
                    "detail": round(detail, 4),
                    "noise": round(noise, 4),
                    "composition": round(composition, 4),
                    "face_score": round(face_score, 4) if face_score is not None else None,
                    "cull_reasons": reasons,
                },
            )
        except Exception as exc:
            logger.error("QualityScorer failed for %s: %s", file_path, exc)
            return AnalysisResult(file_path=file_path, error=str(exc))

    # ------------------------------------------------------------------
    # Sub-scores
    # ------------------------------------------------------------------
    @staticmethod
    def _laplacian_variance(gray: np.ndarray) -> float:
        """Compute normalised Laplacian variance (sharpness proxy).

        Stricter normalisation: values above ~700 are considered very sharp.
        """
        from scipy.ndimage import laplace

        lap = laplace(gray)
        var = float(np.var(lap))
        return float(np.clip(var / 700.0, 0.0, 1.0))

    @staticmethod
    def _histogram_score(gray: np.ndarray) -> float:
        """Score exposure by measuring histogram spread and clipping."""
        hist, _ = np.histogram(gray.ravel(), bins=256, range=(0, 256))
        hist = hist.astype(np.float64) / hist.sum()

        # Penalise clipping at both ends.
        clip_penalty = hist[0] + hist[-1]

        # Reward spread (std of non-zero bins).
        non_zero = np.nonzero(hist)[0]
        spread = float(np.std(non_zero)) / 128.0 if len(non_zero) > 1 else 0.0

        # Penalise very dark or very bright mean.
        mean_bin = float(np.average(np.arange(256), weights=hist))
        center_penalty = abs(mean_bin - 128) / 128.0  # 0 = perfect center

        score = spread * (1.0 - clip_penalty * 5.0) * (1.0 - center_penalty * 0.3)
        return float(np.clip(score, 0.0, 1.0))

    @staticmethod
    def _edge_density(gray: np.ndarray) -> float:
        """Fraction of pixels that belong to an edge (detail proxy)."""
        from scipy.ndimage import sobel

        sx = sobel(gray, axis=0)
        sy = sobel(gray, axis=1)
        magnitude = np.hypot(sx, sy)
        threshold = np.percentile(magnitude, 90)
        edge_ratio = float(np.mean(magnitude > threshold))
        return float(np.clip(edge_ratio * 10.0, 0.0, 1.0))

    @staticmethod
    def _noise_estimate(gray: np.ndarray) -> float:
        """Estimate noise level via high-frequency energy ratio.

        Lower noise = higher score. Uses the ratio of local variance in
        smooth regions vs total variance.
        """
        from scipy.ndimage import uniform_filter

        local_mean = uniform_filter(gray, size=5)
        local_sq = uniform_filter(gray ** 2, size=5)
        local_var = np.maximum(local_sq - local_mean ** 2, 0)

        # Smooth regions have low local variance — noise shows as high
        # local variance even in "should-be-smooth" areas.
        median_var = float(np.median(local_var))

        # Normalise: empirically, median local_var < 20 is clean, > 200 is very noisy.
        noise_ratio = min(median_var / 200.0, 1.0)
        return float(np.clip(1.0 - noise_ratio, 0.0, 1.0))

    @staticmethod
    def _composition_score(gray: np.ndarray) -> float:
        """Rate composition using visual weight distribution.

        Checks rule-of-thirds: images with interesting content (edges)
        near the thirds intersections score higher. Also penalises images
        where all content is crammed into corners or edges.
        """
        from scipy.ndimage import sobel

        h, w = gray.shape
        sx = sobel(gray, axis=0)
        sy = sobel(gray, axis=1)
        energy = np.hypot(sx, sy)

        # Divide into 3×3 grid and compute energy distribution.
        h3, w3 = h // 3, w // 3
        if h3 == 0 or w3 == 0:
            return 0.5

        grid = np.zeros((3, 3))
        for r in range(3):
            for c in range(3):
                r0, r1 = r * h3, (r + 1) * h3 if r < 2 else h
                c0, c1 = c * w3, (c + 1) * w3 if c < 2 else w
                grid[r, c] = np.mean(energy[r0:r1, c0:c1])

        total_energy = grid.sum()
        if total_energy < 1e-6:
            return 0.3  # blank/flat image

        grid_norm = grid / total_energy

        # Reward energy near rule-of-thirds intersections (grid cells
        # [0,1], [1,0], [1,2], [2,1] and center [1,1]).
        thirds_weight = (
            grid_norm[0, 1] + grid_norm[1, 0]
            + grid_norm[1, 2] + grid_norm[2, 1]
            + grid_norm[1, 1]
        )
        # Penalise corners-only composition.
        corner_weight = (
            grid_norm[0, 0] + grid_norm[0, 2]
            + grid_norm[2, 0] + grid_norm[2, 2]
        )

        # Reward even spread (low std = uniform, but not ideal).
        spread = 1.0 - float(np.std(grid_norm)) * 3.0

        score = thirds_weight * 0.6 + spread * 0.3 + (1.0 - corner_weight) * 0.1
        return float(np.clip(score, 0.0, 1.0))
