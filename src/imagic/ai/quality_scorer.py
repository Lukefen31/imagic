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
    """Detect faces and assess quality: size, framing, sharpness, and eye visibility.

    Returns a dict with keys ``score``, ``verdict``, ``detail``, ``count``,
    and ``face_sharpness`` (average Laplacian variance across face regions).
    """
    cascade = _get_face_cascade()
    if cascade is None:
        return {"score": None, "verdict": "n/a", "detail": "OpenCV not available", "count": 0}

    import cv2
    from scipy.ndimage import laplace

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

    sh, sw = small.shape[:2]
    img_area = sh * sw
    small_f = small.astype(np.float64)

    # --- Per-face analysis ---
    face_metrics = []
    for (fx, fy, fw, fh) in faces:
        area_ratio = (fw * fh) / img_area

        # Face sharpness — Laplacian variance of face region
        face_roi = small_f[fy: fy + fh, fx: fx + fw]
        if face_roi.size > 0:
            lap = laplace(face_roi)
            face_sharp = float(np.var(lap))
        else:
            face_sharp = 0.0

        # Edge clipping check
        clipped = (
            fx <= 2 or fy <= 2
            or (fx + fw) >= sw - 2
            or (fy + fh) >= sh - 2
        )

        # Eye detection — check if eyes are visible within face ROI
        eyes_found = 0
        try:
            eye_cascade_path = str(
                Path(cv2.data.haarcascades) / "haarcascade_eye.xml"  # type: ignore[attr-defined]
            )
            eye_cascade = cv2.CascadeClassifier(eye_cascade_path)
            face_gray = small[fy: fy + fh, fx: fx + fw]
            eyes = eye_cascade.detectMultiScale(
                face_gray, scaleFactor=1.1, minNeighbors=3, minSize=(10, 10),
            )
            eyes_found = len(eyes)
        except Exception:
            pass  # Eye cascade unavailable — skip

        face_metrics.append({
            "area_ratio": area_ratio,
            "sharpness": face_sharp,
            "clipped": clipped,
            "eyes": eyes_found,
        })

    # --- Aggregate metrics ---
    count = len(face_metrics)
    max_area = max(f["area_ratio"] for f in face_metrics)
    avg_sharpness = sum(f["sharpness"] for f in face_metrics) / count
    clipped_count = sum(1 for f in face_metrics if f["clipped"])
    total_eyes = sum(f["eyes"] for f in face_metrics)
    best_eyes = max(f["eyes"] for f in face_metrics)

    details = []

    # --- Scoring ---
    score = 0.5  # baseline

    # 1. Size scoring
    if max_area < 0.01:
        score = 0.20
        details.append(f"tiny face(s) ({max_area:.1%} of frame)")
    elif max_area < 0.03:
        score = 0.45
        details.append(f"small face(s) ({max_area:.1%})")
    elif max_area < 0.08:
        score = 0.65
        details.append(f"{count} face(s), moderate size")
    else:
        score = 0.80
        details.append(f"{count} well-framed face(s)")

    # 2. Face sharpness — is the face in focus?
    # On a 480px-wide thumbnail, a well-focused face has lap_var > 100.
    # Soft/blurry face: < 40. Motion-blurred: < 20.
    if avg_sharpness < 15:
        score *= 0.45
        details.append("faces severely out of focus")
    elif avg_sharpness < 40:
        score *= 0.65
        details.append("faces slightly soft/unfocused")
    elif avg_sharpness > 150:
        score = min(1.0, score * 1.10)
        details.append("faces very sharp")

    # 3. Edge clipping penalty
    if clipped_count > 0:
        clip_ratio = clipped_count / count
        if clip_ratio > 0.5:
            score *= 0.60
            details.append(f"{clipped_count}/{count} face(s) cropped at edge")
        else:
            score *= 0.80
            details.append(f"{clipped_count}/{count} face(s) partially cropped")

    # 4. Eye visibility — strong quality signal
    if best_eyes >= 2:
        score = min(1.0, score * 1.08)
        details.append("eyes clearly visible")
    elif best_eyes == 1:
        # Profile shot or partially occluded
        pass
    elif count > 0 and total_eyes == 0 and max_area > 0.02:
        # Faces detected but no eyes — likely looking away, closed eyes, or occluded
        score *= 0.80
        details.append("no eyes detected (turned away or closed)")

    score = float(np.clip(score, 0.0, 1.0))

    # Build verdict
    if score >= 0.75:
        verdict = "clear, sharp face(s)"
    elif score >= 0.55:
        verdict = "acceptable face(s)"
    elif score >= 0.35:
        verdict = "poor face quality"
    else:
        verdict = "very poor face quality"

    return {
        "score": score,
        "verdict": verdict,
        "detail": "; ".join(details),
        "count": count,
        "face_sharpness": round(avg_sharpness, 1),
    }


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
        subject_focus_weight: Weight for subject-in-focus tracking.
        blur_hard_threshold: Sharpness below this triggers a harsh penalty.
    """

    def __init__(
        self,
        sharpness_weight: float = 0.30,
        exposure_weight: float = 0.18,
        detail_weight: float = 0.08,
        noise_weight: float = 0.09,
        composition_weight: float = 0.08,
        face_weight: float = 0.15,
        subject_focus_weight: float = 0.12,
        blur_hard_threshold: float = 0.35,
    ) -> None:
        self._sw = sharpness_weight
        self._ew = exposure_weight
        self._dw = detail_weight
        self._nw = noise_weight
        self._cw = composition_weight
        self._fw = face_weight
        self._sfw = subject_focus_weight
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
                face_detail = {"detail": face_info["detail"]}
                if "face_sharpness" in face_info:
                    face_detail["face_sharpness"] = face_info["face_sharpness"]
                reasons.append({
                    "metric": "Faces",
                    "score": round(face_score, 3),
                    "verdict": face_info["verdict"],
                    **face_detail,
                })

            # --- Subject focus tracking ---
            subject_focus = self._subject_focus_score(arr)
            sf_verdict = _verdict(subject_focus, [
                (0.7, "Subject in sharp focus"),
                (0.5, "Subject reasonably focused"),
                (0.3, "Subject slightly soft"),
                (0.15, "Subject out of focus"),
                (0.0, "No clear subject / defocused"),
            ])
            reasons.append({
                "metric": "Subject Focus",
                "score": round(subject_focus, 3),
                "verdict": sf_verdict,
            })

            # --- Weighted combination ---
            # Apply learned weight adjustments from user feedback.
            sw, ew, dw, nw, cw, fw, sfw = (
                self._sw, self._ew, self._dw, self._nw, self._cw,
                self._fw, self._sfw,
            )
            try:
                from imagic.ai.feedback_learner import get_learner
                adj = get_learner().get_score_adjustments()
                ws = adj.get("weight_shifts", {})
                if ws:
                    sw = max(0.02, sw + ws.get("sharpness", 0))
                    ew = max(0.02, ew + ws.get("exposure", 0))
                    dw = max(0.02, dw + ws.get("detail", 0))
                    nw = max(0.02, nw + ws.get("noise", 0))
                    cw = max(0.02, cw + ws.get("composition", 0))
                    fw = max(0.02, fw + ws.get("faces", 0))
                    sfw = max(0.02, sfw + ws.get("subject_focus", 0))
            except Exception:
                pass  # learner unavailable is fine

            # Re-normalise weights if face detection unavailable.
            if has_faces:
                total_w = sw + ew + dw + nw + cw + fw + sfw
                raw = (
                    sw * sharpness
                    + ew * exposure
                    + dw * detail
                    + nw * noise
                    + cw * composition
                    + fw * face_score
                    + sfw * subject_focus
                ) / total_w
            else:
                total_w = sw + ew + dw + nw + cw + sfw
                raw = (
                    sw * sharpness
                    + ew * exposure
                    + dw * detail
                    + nw * noise
                    + cw * composition
                    + sfw * subject_focus
                ) / total_w

            # --- Hard penalties ---
            penalty_reasons: List[str] = []

            # Blur penalty — graduated, kicks in sooner.
            if sharpness < 0.15:
                raw *= 0.45
                penalty_reasons.append(f"Severe blur ({sharp_verdict})")
            elif sharpness < 0.25:
                raw *= 0.60
                penalty_reasons.append(f"Significant blur ({sharp_verdict})")
            elif sharpness < self._blur_hard:
                raw *= 0.75
                penalty_reasons.append(f"Soft focus ({sharp_verdict})")

            # Exposure penalties — dark or blown.
            mean_brightness = float(np.mean(arr)) / 255.0
            if mean_brightness < 0.05:
                raw *= 0.50
                penalty_reasons.append("Extremely dark image")
            elif mean_brightness < 0.12:
                raw *= 0.70
                penalty_reasons.append("Very dark image")
            elif mean_brightness > 0.92:
                raw *= 0.65
                penalty_reasons.append("Severely overexposed")

            # Clipping penalty — graduated.
            clip_ratio = (np.sum(arr < 1) + np.sum(arr > 254)) / arr.size
            if clip_ratio > 0.20:
                raw *= 0.60
                penalty_reasons.append(f"Extreme clipping ({clip_ratio:.0%})")
            elif clip_ratio > 0.08:
                raw *= 0.75
                penalty_reasons.append(f"Heavy clipping ({clip_ratio:.0%})")

            # Noise penalty.
            if noise < 0.20:
                raw *= 0.70
                penalty_reasons.append("Heavy noise")
            elif noise < 0.35:
                raw *= 0.85
                penalty_reasons.append("Visible noise")

            # Subject out of focus penalty.
            if subject_focus < 0.15:
                raw *= 0.65
                penalty_reasons.append("Subject completely defocused")
            elif subject_focus < 0.25 and sharpness < 0.40:
                raw *= 0.80
                penalty_reasons.append("Subject likely out of focus")

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
                    "subject_focus": round(subject_focus, 4),
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
    def _subject_focus_score(gray: np.ndarray) -> float:
        """Evaluate whether the likely subject is in focus.

        Strategy: identify the "subject region" as the area with the
        highest edge energy (gradient magnitude), then measure whether
        that region is sharp compared to the rest of the image.

        A well-focused image has its subject region significantly sharper
        than the background. An out-of-focus image has uniform (low)
        sharpness everywhere.
        """
        from scipy.ndimage import laplace, sobel, uniform_filter

        h, w = gray.shape
        if h < 16 or w < 16:
            return 0.3

        # Compute gradient magnitude as a proxy for "interesting content"
        sx = sobel(gray, axis=0)
        sy = sobel(gray, axis=1)
        energy = np.hypot(sx, sy)

        # Smooth the energy map to find the subject region
        smoothed = uniform_filter(energy, size=max(3, min(h, w) // 8))

        # Identify subject region: top 20% of smoothed energy
        threshold = float(np.percentile(smoothed, 80))
        if threshold < 1e-6:
            return 0.15  # flat image — no clear subject

        subject_mask = smoothed >= threshold
        bg_mask = ~subject_mask

        # Measure sharpness (Laplacian variance) in subject vs background
        lap = laplace(gray)

        subject_pixels = lap[subject_mask]
        bg_pixels = lap[bg_mask]

        if subject_pixels.size < 10 or bg_pixels.size < 10:
            return 0.3

        subject_var = float(np.var(subject_pixels))
        bg_var = float(np.var(bg_pixels))

        # --- Score components ---

        # 1. Absolute subject sharpness — is the subject actually sharp?
        # On thumbnails (~320px), subject_var > 200 is sharp, < 30 is blurry.
        abs_sharpness = float(np.clip((subject_var - 15.0) / 400.0, 0.0, 1.0))

        # 2. Relative sharpness — is the subject sharper than background?
        # Good: subject is 1.5-5x sharper than bg (intentional focus).
        # Bad: both are equally blurry, or bg is sharper (missed focus).
        if bg_var > 1e-6:
            focus_ratio = subject_var / bg_var
        else:
            focus_ratio = 5.0 if subject_var > 10 else 1.0

        if focus_ratio > 2.5:
            relative_score = 1.0  # strong subject isolation
        elif focus_ratio > 1.5:
            relative_score = 0.6 + (focus_ratio - 1.5) * 0.4
        elif focus_ratio > 1.0:
            relative_score = 0.4 + (focus_ratio - 1.0) * 0.4
        elif focus_ratio > 0.7:
            # Subject is slightly *less* sharp than background — missed focus
            relative_score = 0.15
        else:
            # Background is significantly sharper — badly missed focus
            relative_score = 0.05

        # 3. Subject coherence — is the subject sharpness uniform?
        # Split subject region into quadrants and check variance consistency.
        subject_coords = np.argwhere(subject_mask)
        if len(subject_coords) > 20:
            center_r = float(np.mean(subject_coords[:, 0]))
            center_c = float(np.mean(subject_coords[:, 1]))
            # Check if subject region is reasonably compact
            spread_r = float(np.std(subject_coords[:, 0])) / h
            spread_c = float(np.std(subject_coords[:, 1])) / w
            compactness = 1.0 - min(1.0, (spread_r + spread_c))
        else:
            compactness = 0.3

        score = (
            abs_sharpness * 0.45
            + relative_score * 0.35
            + compactness * 0.20
        )
        return float(np.clip(score, 0.0, 1.0))

    @staticmethod
    def _laplacian_variance(gray: np.ndarray) -> float:
        """Compute sharpness score combining Laplacian variance,
        directional gradient analysis, and local contrast.

        Calibrated for thumbnails (~320px): scores spread across [0, 1]
        with truly sharp images scoring 0.7+ and blurry images < 0.3.
        """
        from scipy.ndimage import laplace, sobel

        # Classic Laplacian variance — primary sharpness signal
        lap = laplace(gray)
        lap_var = float(np.var(lap))
        # Use a sigmoid-like mapping for better spread across the range.
        # lap_var < 50 → very blurry, 50-200 → soft, 200-600 → sharp, 600+ → very sharp
        lap_score = float(np.clip((lap_var - 30.0) / 500.0, 0.0, 1.0))

        # Local sharpness consistency — penalise images that are only
        # sharp in a small region (e.g. missed focus, only background sharp).
        h, w = gray.shape
        block_h, block_w = max(1, h // 4), max(1, w // 4)
        block_vars = []
        for r in range(4):
            for c in range(4):
                r0, r1 = r * block_h, min((r + 1) * block_h, h)
                c0, c1 = c * block_w, min((c + 1) * block_w, w)
                block = gray[r0:r1, c0:c1]
                if block.size > 0:
                    blap = laplace(block)
                    block_vars.append(float(np.var(blap)))
        if block_vars:
            # If the sharpest block is much sharper than the median,
            # overall sharpness is inconsistent (partial focus).
            median_block = float(np.median(block_vars))
            max_block = max(block_vars)
            if max_block > 1e-6:
                consistency = median_block / max_block  # 0 = only one block sharp, 1 = uniform
                # Penalise low consistency — weight: up to -0.15
                if consistency < 0.3:
                    lap_score *= (0.7 + consistency)

        # Directional gradient analysis — detect motion blur
        gx = sobel(gray, axis=1)
        gy = sobel(gray, axis=0)
        energy_x = float(np.mean(gx ** 2))
        energy_y = float(np.mean(gy ** 2))
        total_energy = energy_x + energy_y

        if total_energy > 1e-6:
            ratio = min(energy_x, energy_y) / max(energy_x, energy_y)
            mag = np.hypot(gx, gy)
            mag_mean = float(np.mean(mag))
            mag_std = float(np.std(mag))
            coherence_penalty = 0.0
            if mag_mean > 1e-6:
                cv = mag_std / mag_mean
                if cv > 1.8 and ratio < 0.55:
                    coherence_penalty = min(0.45, (cv - 1.8) * 0.2 + (0.55 - ratio) * 0.4)
                elif ratio < 0.4:
                    coherence_penalty = min(0.35, (0.4 - ratio) * 0.6)
            lap_score = max(0.0, lap_score - coherence_penalty)

        return lap_score

    @staticmethod
    def _histogram_score(gray: np.ndarray) -> float:
        """Score exposure quality with stricter calibration.

        Evaluates: dynamic range utilisation, shadow/highlight clipping,
        tonal distribution balance, and mean brightness deviation.
        """
        hist, _ = np.histogram(gray.ravel(), bins=256, range=(0, 256))
        hist_f = hist.astype(np.float64) / hist.sum()

        # 1. Clipping penalty — crushed shadows and blown highlights
        shadow_clip = float(np.sum(hist_f[:5]))   # pixels in [0-4]
        highlight_clip = float(np.sum(hist_f[251:]))  # pixels in [251-255]
        clip_penalty = min(1.0, (shadow_clip + highlight_clip) * 4.0)

        # 2. Dynamic range — what percentage of the tonal range is used
        non_zero = np.nonzero(hist)[0]
        if len(non_zero) < 2:
            return 0.05
        tonal_range = (non_zero[-1] - non_zero[0]) / 255.0
        # Reward wide range but diminishing returns above 0.7
        range_score = float(np.clip(tonal_range / 0.8, 0.0, 1.0))

        # 3. Mean brightness — penalise very dark or very bright
        mean_val = float(np.average(np.arange(256), weights=hist_f))
        # Ideal range: 80-180. Penalise outside that.
        if mean_val < 40:
            brightness_score = mean_val / 40.0 * 0.4   # very dark → max 0.4
        elif mean_val < 80:
            brightness_score = 0.4 + (mean_val - 40) / 40.0 * 0.4
        elif mean_val <= 180:
            brightness_score = 0.8 + (1.0 - abs(mean_val - 130) / 50.0) * 0.2
        elif mean_val <= 220:
            brightness_score = 0.8 - (mean_val - 180) / 40.0 * 0.3
        else:
            brightness_score = max(0.1, 0.5 - (mean_val - 220) / 35.0 * 0.4)

        # 4. Tonal distribution — penalise heavy concentration in one area
        # Split into shadows (0-85), mids (86-170), highlights (171-255)
        shadows = float(np.sum(hist_f[:86]))
        mids = float(np.sum(hist_f[86:171]))
        highlights = float(np.sum(hist_f[171:]))
        # Good images have reasonable mid-tone content
        balance_score = min(1.0, mids * 2.5)  # mids > 0.4 → full score

        score = (
            range_score * 0.25
            + brightness_score * 0.35
            + balance_score * 0.20
            + (1.0 - clip_penalty) * 0.20
        )
        return float(np.clip(score, 0.0, 1.0))

    @staticmethod
    def _edge_density(gray: np.ndarray) -> float:
        """Score detail richness using gradient magnitude distribution.

        Calibrated to spread scores: flat/soft images < 0.3,
        moderately detailed 0.3-0.6, richly detailed 0.6+.
        """
        from scipy.ndimage import sobel

        sx = sobel(gray, axis=0)
        sy = sobel(gray, axis=1)
        magnitude = np.hypot(sx, sy)

        # Use mean gradient magnitude normalised against image intensity range
        intensity_range = float(np.percentile(gray, 98) - np.percentile(gray, 2))
        if intensity_range < 1.0:
            return 0.05  # essentially blank image

        # Normalise gradient magnitude by intensity range
        mean_grad = float(np.mean(magnitude)) / intensity_range
        # Typical range: 0.02 (soft/flat) to 0.20+ (very detailed)
        detail_score = float(np.clip(mean_grad / 0.15, 0.0, 1.0))

        # Also check gradient richness — how much of the image has edges
        threshold = float(np.percentile(magnitude, 75))
        if threshold > 1e-6:
            strong_edge_ratio = float(np.mean(magnitude > threshold * 1.5))
            # Bonus for images with edges spread throughout
            detail_score = detail_score * 0.7 + strong_edge_ratio * 3.0 * 0.3

        return float(np.clip(detail_score, 0.0, 1.0))

    @staticmethod
    def _noise_estimate(gray: np.ndarray) -> float:
        """Estimate noise level via high-frequency energy in smooth regions.

        Stricter calibration: median_var > 50 starts penalising,
        > 150 is heavily noisy.
        """
        from scipy.ndimage import uniform_filter

        local_mean = uniform_filter(gray, size=5)
        local_sq = uniform_filter(gray ** 2, size=5)
        local_var = np.maximum(local_sq - local_mean ** 2, 0)

        # Focus on the smoothest 30% of the image — noise is most visible there
        flat_var = local_var.ravel()
        p30 = float(np.percentile(flat_var, 30))

        # Stricter normalisation: 0-10 = very clean, 10-50 = ok, 50-150 = noisy, 150+ = very noisy
        if p30 < 5:
            score = 1.0
        elif p30 < 30:
            score = 1.0 - (p30 - 5) / 25.0 * 0.3   # 1.0 → 0.7
        elif p30 < 80:
            score = 0.7 - (p30 - 30) / 50.0 * 0.35  # 0.7 → 0.35
        else:
            score = max(0.05, 0.35 - (p30 - 80) / 120.0 * 0.3)  # 0.35 → 0.05

        return float(np.clip(score, 0.0, 1.0))

    @staticmethod
    def _composition_score(gray: np.ndarray) -> float:
        """Rate composition using visual weight distribution and subject placement.

        Evaluates rule-of-thirds adherence, subject isolation, and
        energy distribution balance. Calibrated to be more discriminating.
        """
        from scipy.ndimage import sobel

        h, w = gray.shape
        sx = sobel(gray, axis=0)
        sy = sobel(gray, axis=1)
        energy = np.hypot(sx, sy)

        # Divide into 3×3 grid
        h3, w3 = h // 3, w // 3
        if h3 == 0 or w3 == 0:
            return 0.3

        grid = np.zeros((3, 3))
        for r in range(3):
            for c in range(3):
                r0, r1 = r * h3, (r + 1) * h3 if r < 2 else h
                c0, c1 = c * w3, (c + 1) * w3 if c < 2 else w
                grid[r, c] = float(np.mean(energy[r0:r1, c0:c1]))

        total_energy = grid.sum()
        if total_energy < 1e-6:
            return 0.15  # blank/flat image — poor composition

        grid_norm = grid / total_energy

        # 1. Rule of thirds — reward energy at intersection cells
        #    Intersections: [0,1], [1,0], [1,2], [2,1] — not center
        thirds_cells = (
            grid_norm[0, 1] + grid_norm[1, 0]
            + grid_norm[1, 2] + grid_norm[2, 1]
        )
        # Center gets partial credit
        center = grid_norm[1, 1]
        thirds_score = min(1.0, thirds_cells * 1.5 + center * 0.5)

        # 2. Corner penalty — energy crammed in corners is bad framing
        corner_weight = (
            grid_norm[0, 0] + grid_norm[0, 2]
            + grid_norm[2, 0] + grid_norm[2, 2]
        )
        corner_penalty = corner_weight * 0.8  # up to ~0.35 penalty

        # 3. Subject isolation — is there a clear focal point?
        # Good: one or two cells dominate. Bad: energy spread perfectly evenly.
        grid_flat = grid_norm.ravel()
        entropy = -float(np.sum(
            grid_flat[grid_flat > 0] * np.log2(grid_flat[grid_flat > 0])
        ))
        # Max entropy for 9 cells = log2(9) ≈ 3.17
        # Low entropy = clear subject, high = uniform/chaotic
        isolation_score = max(0.0, 1.0 - entropy / 3.17)

        # 4. Edge-of-frame check — penalise if the strongest energy
        #    is right at the image border (cropped subject)
        border_energy = (
            float(np.mean(energy[:max(1, h // 10), :]))
            + float(np.mean(energy[-(h // 10):, :]))
            + float(np.mean(energy[:, :max(1, w // 10)]))
            + float(np.mean(energy[:, -(w // 10):]))
        ) / 4.0
        center_region = energy[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
        center_energy = float(np.mean(center_region)) if center_region.size > 0 else 1e-6
        if center_energy > 1e-6:
            border_ratio = border_energy / center_energy
            edge_penalty = max(0.0, (border_ratio - 1.0) * 0.3)
        else:
            edge_penalty = 0.0

        score = (
            thirds_score * 0.35
            + isolation_score * 0.25
            + (1.0 - corner_penalty) * 0.20
            + (1.0 - min(1.0, edge_penalty)) * 0.20
        )
        return float(np.clip(score, 0.0, 1.0))
