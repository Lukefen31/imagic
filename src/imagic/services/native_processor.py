"""Native RAW processor — Python-only fallback for RawTherapee CLI.

Uses ``rawpy`` (LibRaw) for RAW demosaicing and ``numpy`` / ``Pillow`` /
``scipy`` for all image adjustments.  This module is a drop-in
replacement for the ``rawtherapee-cli`` subprocess path: it reads the
same ``PhotoMetrics`` and ``ColorGrade`` parameters that the PP3
generator produces and applies equivalent processing in pure Python.

Design goals:
* Produce visually faithful results compared to RawTherapee.
* Zero native dependencies beyond pip-installable wheels
  (rawpy, numpy, scipy, Pillow).
* Work identically on desktop, Docker / web server, and CI.

The existing RawTherapee CLI path is **not** removed — this engine
activates only when RT is unavailable.
"""

from __future__ import annotations

import colorsys
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

from imagic.services.pp3_generator import ColorGrade, GRADES, PhotoMetrics

logger = logging.getLogger(__name__)


@dataclass
class NativeResult:
    """Mirrors ``CLIResult`` so the caller doesn't need to branch."""
    success: bool
    return_code: int
    stdout: str
    stderr: str
    command: List[str]
    duration_s: float


class NativeProcessor:
    """Python-native RAW→output processor.

    Applies the same corrections that the PP3 generator would instruct
    RawTherapee to apply, using numpy array operations.
    """

    def __init__(self, jpeg_quality: int = 95) -> None:
        self._jpeg_quality = jpeg_quality

    # ------------------------------------------------------------------
    # Public API — mirrors CLIOrchestrator.rawtherapee_export() signature
    # ------------------------------------------------------------------

    def _has_editor_overrides(self, ov: Dict) -> bool:
        """Check if manual_overrides came from the desktop photo editor.

        The editor's ``_gather_params`` always includes a
        ``color_grade`` key (a string) and ``color_grade_intensity``
        (an int).  Their presence is a reliable signal — the auto-
        pipeline never sets these.  We also fall back to checking
        individual slider keys for safety.
        """
        # The editor always writes these two keys.
        if "color_grade" in ov and "color_grade_intensity" in ov:
            return True
        _EDITOR_KEYS = {
            "exposure", "contrast", "highlights", "shadows", "whites",
            "blacks", "clarity", "dehaze", "vibrance", "saturation",
            "temperature", "tint", "sharp_amount", "sharp_radius",
            "nr_luminance", "vignette_amount", "vignette_midpoint",
            "grain_amount",
        }
        return any(k in _EDITOR_KEYS for k, v in ov.items())

    def process(
        self,
        input_path: Path,
        output_dir: Path,
        metrics: PhotoMetrics,
        color_grade: Optional[ColorGrade] = None,
        output_format: str = "jpg",
        manual_overrides: Optional[Dict] = None,
    ) -> NativeResult:
        """Decode a RAW file and apply full processing.

        When *manual_overrides* come from the desktop photo editor, the
        same ``PreviewEngine`` that produced the live preview is used so
        the exported image matches what the user saw.  Otherwise the
        full NativeProcessor pipeline (with automatic corrections) runs.

        Args:
            input_path: Path to the RAW (or JPEG/TIFF) source.
            output_dir: Directory for the exported file.
            metrics: Per-photo analysis metrics.
            color_grade: Artistic colour grade (defaults to *natural*).
            output_format: ``"jpg"``, ``"tif"``, or ``"png"``.
            manual_overrides: Slider values from the editor (same keys
                the PP3 generator accepts).

        Returns:
            A ``NativeResult`` with ``success=True`` on success.
        """
        start = time.monotonic()
        if color_grade is None:
            color_grade = GRADES.get("natural", ColorGrade(name="Natural", description=""))
        if manual_overrides is None:
            manual_overrides = {}

        output_dir.mkdir(parents=True, exist_ok=True)
        ext_map = {"jpg": ".jpg", "tif": ".tiff", "png": ".png"}
        ext = ext_map.get(output_format.lower(), ".jpg")
        output_path = output_dir / f"{input_path.stem}{ext}"

        try:
            # When the user edited in the desktop editor, use the exact
            # same PreviewEngine so export matches the live preview.
            if manual_overrides and self._has_editor_overrides(manual_overrides):
                from imagic.services.preview_engine import PreviewEngine
                # Decode with the same rawpy settings the editor uses
                # (8-bit, default demosaic, no FBDD) so colours are
                # identical to what the user saw in the live preview.
                arr = self._decode(input_path, match_editor=True)
                arr = PreviewEngine.apply(arr, manual_overrides).astype(np.float32)
                # Apply crop (PreviewEngine doesn't handle it).
                cx = manual_overrides.get("crop_x", 0)
                cy = manual_overrides.get("crop_y", 0)
                cw = manual_overrides.get("crop_w", 0)
                ch = manual_overrides.get("crop_h", 0)
                if cw > 0 and ch > 0:
                    h, w = arr.shape[:2]
                    x1 = max(0, min(cx, w - 1))
                    y1 = max(0, min(cy, h - 1))
                    x2 = min(w, x1 + cw)
                    y2 = min(h, y1 + ch)
                    if x2 > x1 and y2 > y1:
                        arr = arr[y1:y2, x1:x2]
            else:
                arr = self._decode(input_path)
                arr = self._apply_pipeline(arr, metrics, color_grade, manual_overrides)

            self._save(arr, output_path, output_format)

            elapsed = round(time.monotonic() - start, 3)
            logger.info(
                "Native export OK: %s → %s (%.2fs)",
                input_path.name, output_path.name, elapsed,
            )
            return NativeResult(
                success=True, return_code=0,
                stdout=str(output_path), stderr="",
                command=["native_processor", str(input_path)],
                duration_s=elapsed,
            )
        except Exception as exc:
            elapsed = round(time.monotonic() - start, 3)
            logger.exception("Native export FAILED for %s", input_path.name)
            return NativeResult(
                success=False, return_code=1,
                stdout="", stderr=str(exc),
                command=["native_processor", str(input_path)],
                duration_s=elapsed,
            )

    # ------------------------------------------------------------------
    # RAW decoding
    # ------------------------------------------------------------------

    def _decode(self, path: Path, match_editor: bool = False) -> np.ndarray:
        """Decode a RAW file (or load a JPEG/TIFF).

        Args:
            path: Image file to decode.
            match_editor: When *True*, use the exact same rawpy settings
                as the desktop editor (8-bit, default demosaic, no FBDD)
                so colours are identical.  Returns **uint8** [0, 255].
                When *False*, use 16-bit AHD for higher-precision
                pipeline processing.  Returns **float32** [0, 255].
        """
        suffix = path.suffix.lower()

        # Non-RAW files — just open with Pillow.
        if suffix in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
            img = Image.open(path).convert("RGB")
            return np.asarray(img, dtype=np.uint8 if match_editor else np.float32)

        # RAW files — use rawpy (LibRaw).
        import rawpy
        with rawpy.imread(str(path)) as raw:
            if match_editor:
                # Identical to _RawDecodeWorker in photo_editor.py —
                # 8-bit, AHD demosaic, explicit sRGB, no FBDD.
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                    half_size=False,
                    demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                    output_color=rawpy.ColorSpace.sRGB,
                )
                return rgb  # uint8 [0, 255]
            else:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=16,
                    demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                    output_color=rawpy.ColorSpace.sRGB,
                )
        # Convert 16-bit → float32 in [0, 255] range for pipeline.
        return rgb.astype(np.float32) * (255.0 / 65535.0)

    # ------------------------------------------------------------------
    # Full processing pipeline
    # ------------------------------------------------------------------

    def _apply_pipeline(
        self,
        arr: np.ndarray,
        m: PhotoMetrics,
        grade: ColorGrade,
        ov: Dict,
    ) -> np.ndarray:
        """Apply the full chain of corrections + artistic adjustments."""

        # --- Technical corrections (mirrors pp3_generator logic) ---
        arr = self._apply_exposure(arr, m, ov)
        arr = self._apply_highlight_recovery(arr, m)
        arr = self._apply_shadow_lift(arr, m, ov)
        arr = self._apply_contrast(arr, m, grade, ov)
        arr = self._apply_white_balance(arr, ov)

        # --- Noise reduction ---
        arr = self._apply_noise_reduction(arr, m, ov)

        # --- Sharpening ---
        arr = self._apply_sharpening(arr, m, ov)
        arr = self._apply_local_contrast(arr, m, ov)

        # --- Artistic (colour grade) ---
        arr = self._apply_tone_curve(arr, grade)
        arr = self._apply_saturation(arr, grade, ov)
        arr = self._apply_vibrance(arr, grade, ov)
        if grade.split_tone_enabled:
            arr = self._apply_split_tone(arr, grade)
        if grade.is_bw:
            arr = self._apply_bw(arr)
        if grade.soft_light_strength:
            arr = self._apply_soft_light(arr, grade)
        if grade.vignette_amount:
            arr = self._apply_vignette(arr, grade, ov)
        if grade.film_grain_enabled or ov.get("grain_amount", 0) > 0:
            arr = self._apply_grain(arr, grade, ov)

        # --- Crop ---
        if m.crop_enabled and m.crop_w > 0 and m.crop_h > 0:
            arr = self._apply_crop(arr, m)

        return np.clip(arr, 0, 255)

    # ------------------------------------------------------------------
    # Technical corrections
    # ------------------------------------------------------------------

    def _apply_exposure(self, arr: np.ndarray, m: PhotoMetrics, ov: Dict) -> np.ndarray:
        """Exposure compensation in EV, matching pp3_generator logic."""
        iso = m.iso or 100
        brightness_norm = m.mean_brightness / 255.0

        # Dampening for high-ISO (same logic as pp3_generator)
        high_iso = iso >= 1600
        very_high_iso = iso >= 3200
        has_highlights = m.clip_light_pct > 0.5
        noise_clean = m.noise

        dampen = 0.0
        if very_high_iso and has_highlights:
            dampen = 0.75
        elif very_high_iso and noise_clean < 0.5:
            dampen = 0.65
        elif very_high_iso:
            dampen = 0.50
        elif high_iso and has_highlights:
            dampen = 0.40
        elif high_iso and noise_clean < 0.5:
            dampen = 0.35
        elif high_iso:
            dampen = 0.25

        if ov:
            dampen *= 0.30

        # Compute EV shift
        if brightness_norm < 0.25:
            raw_exp = 0.4 + (0.25 - brightness_norm) * 3.0
            ev = round(raw_exp * (1.0 - dampen), 2)
        elif brightness_norm > 0.75:
            ev = round(-0.3 - (brightness_norm - 0.75) * 2.0, 2)
        else:
            ev = round((0.5 - brightness_norm) * 0.6, 2)

        if very_high_iso:
            ev = min(ev, 0.70 if ov else 0.25)
        elif high_iso:
            ev = min(ev, 1.0 if ov else 0.45)

        # Manual override
        ov_exp = ov.get("exposure", 0)
        if ov_exp:
            ev = round(ev + ov_exp / 50.0, 2)

        if abs(ev) > 0.01:
            arr = arr * (2.0 ** ev)

        # Brightness boost for dark images
        raw_bright = max(0, (0.5 - brightness_norm) * 30)
        brightness_adj = max(0, int(raw_bright * (1.0 - dampen)))
        if brightness_adj > 0:
            arr = arr + brightness_adj * 0.5

        return arr

    def _apply_highlight_recovery(self, arr: np.ndarray, m: PhotoMetrics) -> np.ndarray:
        """Soft-knee highlight compression."""
        if m.clip_light_pct < 0.5:
            return arr
        strength = min(0.5, 0.1 + m.clip_light_pct * 0.05)
        threshold = 220.0
        above = np.maximum(arr - threshold, 0)
        return np.where(arr > threshold, threshold + above * (1.0 - strength), arr)

    def _apply_shadow_lift(self, arr: np.ndarray, m: PhotoMetrics, ov: Dict) -> np.ndarray:
        """Lift shadows / adjust black point."""
        brightness_norm = m.mean_brightness / 255.0

        if m.clip_dark_pct > 5 or brightness_norm < 0.3:
            # Shadow lift via a soft curve on dark pixels
            mask = np.clip(1.0 - arr / 128.0, 0, 1)
            lift = 0.1 + m.clip_dark_pct * 0.005
            arr = arr + mask * lift * 60.0

        # Manual shadows override
        ov_sh = ov.get("shadows", 0)
        if ov_sh:
            mask = np.clip(1.0 - arr / 128.0, 0, 1)
            arr = arr + mask * (ov_sh / 100.0) * 40.0

        # Manual blacks override
        ov_blacks = ov.get("blacks", 0)
        if ov_blacks:
            arr = arr + ov_blacks * 0.3

        # Manual whites override
        ov_whites = ov.get("whites", 0)
        if ov_whites:
            mask = np.clip((arr - 180.0) / 75.0, 0, 1)
            arr = arr + mask * (ov_whites / 100.0) * 30.0

        # Manual highlights override
        ov_hl = ov.get("highlights", 0)
        if ov_hl:
            mask = np.clip((arr - 160.0) / 95.0, 0, 1)
            arr = arr - mask * (ov_hl / 100.0) * 35.0

        return arr

    def _apply_contrast(
        self, arr: np.ndarray, m: PhotoMetrics, grade: ColorGrade, ov: Dict,
    ) -> np.ndarray:
        """Contrast adjustment."""
        base = 15
        if m.exposure < 0.3:
            base = 10
        elif m.exposure > 0.7:
            base = 20
        contrast = base + grade.contrast_boost

        ov_con = ov.get("contrast", 0)
        if ov_con:
            contrast = max(-100, min(100, contrast + ov_con))

        # Dehaze also boosts contrast
        ov_dehaze = ov.get("dehaze", 0)
        if ov_dehaze:
            contrast = max(-100, min(100, contrast + ov_dehaze // 4))

        if abs(contrast) > 0:
            factor = 1.0 + contrast / 100.0
            mid = 128.0
            arr = (arr - mid) * factor + mid

        return arr

    def _apply_white_balance(self, arr: np.ndarray, ov: Dict) -> np.ndarray:
        """Apply white balance temperature/tint shift."""
        temp = ov.get("temperature", 0) or ov.get("wb_warmth", 0)
        tint = ov.get("tint", 0)

        if not temp and not tint:
            return arr

        result = arr.copy()
        if temp:
            # Warm: boost red, cut blue. Cool: reverse.
            shift = temp / 100.0  # normalise to roughly ±1
            result[..., 0] = result[..., 0] * (1 + shift * 0.06)   # red
            result[..., 2] = result[..., 2] * (1 - shift * 0.04)   # blue

        if tint:
            shift = tint / 100.0
            result[..., 1] = result[..., 1] * (1 + shift * 0.04)   # green

        return result

    # ------------------------------------------------------------------
    # Noise reduction
    # ------------------------------------------------------------------

    def _apply_noise_reduction(self, arr: np.ndarray, m: PhotoMetrics, ov: Dict) -> np.ndarray:
        """Luminance + chroma noise reduction."""
        iso = m.iso or 100

        # Determine NR strength from ISO level (matches pp3_generator tiers)
        if iso >= 6400 or m.noise < 0.3:
            sigma_lum = 3.0
            sigma_chroma = 4.0
        elif iso >= 3200 or m.noise < 0.5:
            sigma_lum = 2.0
            sigma_chroma = 3.0
        elif iso >= 1600 or m.noise < 0.65:
            sigma_lum = 1.2
            sigma_chroma = 2.0
        elif iso >= 800:
            sigma_lum = 0.6
            sigma_chroma = 1.0
        else:
            sigma_lum = 0.2
            sigma_chroma = 0.5

        # Manual NR override
        ov_nr = ov.get("noise_reduction", 0) or ov.get("nr_luminance", 0)
        if ov_nr > 0:
            sigma_lum = sigma_lum + ov_nr / 30.0

        ov_nr_color = ov.get("nr_color", 0)
        if ov_nr_color > 0:
            sigma_chroma = sigma_chroma + ov_nr_color / 25.0

        # Skip NR for low-ISO clean images to preserve detail
        if sigma_lum < 0.3 and sigma_chroma < 0.6:
            return arr

        try:
            from scipy.ndimage import gaussian_filter
        except ImportError:
            return arr

        # Luminance-only denoising: filter the luminance channel, keep
        # chroma detail.  This avoids the computational cost of NLM while
        # still being effective.
        lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        lum_smooth = gaussian_filter(lum, sigma=sigma_lum)
        # Blend smoothed luminance back
        lum_delta = (lum_smooth - lum)[..., np.newaxis]
        arr = arr + lum_delta * 0.7  # partial blend preserves some texture

        # Chroma smoothing (reduce colour noise)
        if sigma_chroma > 0.5:
            for ch in range(3):
                arr[..., ch] = gaussian_filter(arr[..., ch], sigma=sigma_chroma * 0.3)

        return arr

    # ------------------------------------------------------------------
    # Sharpening
    # ------------------------------------------------------------------

    def _apply_sharpening(self, arr: np.ndarray, m: PhotoMetrics, ov: Dict) -> np.ndarray:
        """Unsharp mask sharpening, mirroring pp3_generator tiers."""
        iso = m.iso or 100

        if m.sharpness < 0.2:
            amount, radius = 200, 0.8
        elif m.sharpness < 0.45:
            amount, radius = 160, 0.6
        elif m.sharpness < 0.7:
            amount, radius = 130, 0.5
        else:
            amount, radius = 100, 0.4

        if iso >= 3200:
            amount = int(amount * 0.7)
            radius = max(0.3, radius - 0.1)

        # Manual override
        ov_sharp = ov.get("sharpness", 100)
        if ov_sharp != 100:
            amount = max(0, int(amount * ov_sharp / 100))

        ov_sharp_amt = ov.get("sharp_amount", 0)
        if ov_sharp_amt > 0:
            amount = max(0, int(amount * (1 + ov_sharp_amt / 100.0)))

        ov_sharp_r = ov.get("sharp_radius", 50)
        if ov_sharp_r != 50:
            radius = max(0.2, round(ov_sharp_r / 50.0, 1))

        if amount < 10:
            return arr

        try:
            from scipy.ndimage import gaussian_filter
        except ImportError:
            return arr

        # Unsharp mask: original + amount * (original - blurred)
        blurred = gaussian_filter(arr, sigma=radius)
        factor = amount / 100.0
        sharpened = arr + factor * (arr - blurred)
        return sharpened

    def _apply_local_contrast(self, arr: np.ndarray, m: PhotoMetrics, ov: Dict) -> np.ndarray:
        """Local contrast enhancement (clarity)."""
        lc = 0.20
        if m.detail < 0.3:
            lc = 0.30
        elif m.detail > 0.6:
            lc = 0.12

        ov_clarity = ov.get("clarity", 0)
        if ov_clarity:
            lc = max(0, min(1.0, lc + ov_clarity / 200.0))

        ov_dehaze = ov.get("dehaze", 0)
        if ov_dehaze:
            lc = max(0, min(1.0, lc + ov_dehaze / 400.0))

        if lc < 0.05:
            return arr

        try:
            from scipy.ndimage import gaussian_filter
        except ImportError:
            return arr

        # Large-radius unsharp mask = local contrast
        blurred = gaussian_filter(arr, sigma=30)
        return arr + lc * (arr - blurred)

    # ------------------------------------------------------------------
    # Artistic colour grade effects
    # ------------------------------------------------------------------

    def _apply_tone_curve(self, arr: np.ndarray, grade: ColorGrade) -> np.ndarray:
        """Parse PP3-style tone curve and apply via LUT."""
        if not grade.tone_curve:
            return arr

        parts = grade.tone_curve.rstrip(";").split(";")
        if len(parts) < 3:
            return arr
        try:
            coords = [float(p) for p in parts[1:]]
        except ValueError:
            return arr
        if len(coords) % 2 != 0:
            return arr

        points = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
        points.sort(key=lambda p: p[0])

        # Build 256-entry LUT via linear interpolation.
        lut = np.zeros(256, dtype=np.float32)
        for i in range(256):
            x = i / 255.0
            y = points[-1][1]
            for j in range(len(points) - 1):
                x0, y0 = points[j]
                x1, y1 = points[j + 1]
                if x0 <= x <= x1:
                    t = (x - x0) / max(x1 - x0, 1e-6)
                    y = y0 + t * (y1 - y0)
                    break
            lut[i] = y * 255.0

        idx = np.clip(arr, 0, 255).astype(np.uint8)
        return lut[idx].astype(np.float32)

    def _apply_saturation(self, arr: np.ndarray, grade: ColorGrade, ov: Dict) -> np.ndarray:
        """Global saturation shift."""
        sat = max(-100, min(100, grade.saturation_shift))
        ov_sat = ov.get("saturation", 0)
        if ov_sat:
            sat = max(-100, min(100, sat + ov_sat))

        if abs(sat) < 1:
            return arr

        factor = 1.0 + sat / 100.0
        lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        lum = lum[..., np.newaxis]
        return lum + (arr - lum) * factor

    def _apply_vibrance(self, arr: np.ndarray, grade: ColorGrade, ov: Dict) -> np.ndarray:
        """Vibrance: boost under-saturated pixels more than already-saturated ones."""
        pastels = max(-100, min(100, 10 + grade.vibrance_pastels))
        saturated = max(-100, min(100, 5 + grade.vibrance_saturated))

        ov_vib = ov.get("vibrance", 0)
        if ov_vib:
            pastels = max(-100, min(100, pastels + ov_vib))
            saturated = max(-100, min(100, saturated + ov_vib // 2))

        combined = (pastels + saturated) / 2.0
        if abs(combined) < 1:
            return arr

        # Vibrance = saturation boost weighted by inverse current saturation.
        lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        lum_3d = lum[..., np.newaxis]
        chroma = np.sqrt(np.mean((arr - lum_3d) ** 2, axis=2, keepdims=True))
        max_chroma = np.max(chroma) + 1e-6
        # Less-saturated pixels get a stronger boost.
        weight = 1.0 - np.clip(chroma / max_chroma, 0, 1)
        factor = 1.0 + (combined / 100.0) * (0.5 + 0.5 * weight)
        return lum_3d + (arr - lum_3d) * factor

    def _apply_split_tone(self, arr: np.ndarray, grade: ColorGrade) -> np.ndarray:
        """Split-toning: tint shadows and highlights."""
        lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        shadow_mask = np.clip(1.0 - lum / 128.0, 0, 1)[..., np.newaxis]
        highlight_mask = np.clip((lum - 128.0) / 128.0, 0, 1)[..., np.newaxis]

        sh_col = self._hue_sat_to_rgb(grade.split_tone_shadow_hue,
                                       grade.split_tone_shadow_sat)
        hi_col = self._hue_sat_to_rgb(grade.split_tone_highlight_hue,
                                       grade.split_tone_highlight_sat)

        arr = arr + shadow_mask * sh_col * 0.5
        arr = arr + highlight_mask * hi_col * 0.5
        return arr

    def _apply_bw(self, arr: np.ndarray) -> np.ndarray:
        """Luminance-weighted greyscale conversion."""
        lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        return np.stack([lum, lum, lum], axis=-1)

    def _apply_soft_light(self, arr: np.ndarray, grade: ColorGrade) -> np.ndarray:
        """Soft-light glow blend."""
        strength = max(0, min(100, grade.soft_light_strength))
        factor = strength / 100.0
        if factor < 0.01:
            return arr

        try:
            from scipy.ndimage import gaussian_filter
        except ImportError:
            return arr

        blurred = gaussian_filter(arr, sigma=8)
        a = np.clip(arr / 255.0, 0, 1)
        b = np.clip(blurred / 255.0, 0, 1)
        result = np.where(
            a < 0.5,
            2 * a * b,
            1.0 - 2 * (1.0 - a) * (1.0 - b),
        ) * 255.0
        return arr * (1 - factor) + result * factor

    def _apply_vignette(self, arr: np.ndarray, grade: ColorGrade, ov: Dict) -> np.ndarray:
        """Radial edge darkening."""
        amount = ov.get("vignette_amount", grade.vignette_amount)
        radius = ov.get("vignette_midpoint", grade.vignette_radius)
        if not amount:
            return arr

        h, w = arr.shape[:2]
        cy, cx = h / 2, w / 2
        Y, X = np.ogrid[:h, :w]
        dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
        max_dist = np.sqrt(cx ** 2 + cy ** 2)
        r = radius / 100.0
        norm = np.clip((dist / max_dist - r) / (1.0 - r + 1e-6), 0, 1)
        factor = 1.0 - (amount / 100.0) * norm[..., np.newaxis]
        return arr * factor

    def _apply_grain(self, arr: np.ndarray, grade: ColorGrade, ov: Dict) -> np.ndarray:
        """Film grain noise."""
        strength = ov.get("grain_amount", 0) or grade.film_grain_strength
        if strength <= 0:
            return arr
        rng = np.random.default_rng(42)
        noise = rng.standard_normal(arr.shape[:2]) * (strength / 100.0) * 25.0
        return arr + noise[..., np.newaxis]

    def _apply_crop(self, arr: np.ndarray, m: PhotoMetrics) -> np.ndarray:
        """Crop to the auto-crop rectangle.

        The crop coordinates in *metrics* reference the full decoded RAW
        pixel space.  The array we have here *is* the full RAW decode, so
        coordinates can be used directly (clamped to bounds).
        """
        h, w = arr.shape[:2]
        x = max(0, min(m.crop_x, w - 1))
        y = max(0, min(m.crop_y, h - 1))
        x2 = min(w, x + m.crop_w)
        y2 = min(h, y + m.crop_h)
        if x2 <= x or y2 <= y:
            return arr
        return arr[y:y2, x:x2]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hue_sat_to_rgb(hue: int, sat: int) -> np.ndarray:
        """Convert hue (0-360) and saturation (0-100) to an RGB offset."""
        h = (hue % 360) / 360.0
        s = min(max(sat, 0), 100) / 100.0
        r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
        return np.array([r * 255, g * 255, b * 255], dtype=np.float32) - 128.0

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    # Standard sRGB ICC profile (v2, 588 bytes).
    _SRGB_PROFILE: bytes | None = None

    @classmethod
    def _get_srgb_profile(cls) -> bytes | None:
        """Return the sRGB ICC profile, caching on first call."""
        if cls._SRGB_PROFILE is None:
            try:
                from PIL.ImageCms import ImageCmsProfile, createProfile
                cls._SRGB_PROFILE = ImageCmsProfile(createProfile("sRGB")).tobytes()
            except Exception:
                pass
        return cls._SRGB_PROFILE

    def _save(self, arr: np.ndarray, path: Path, fmt: str) -> None:
        """Save the processed array to disk with an embedded sRGB profile."""
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        icc = self._get_srgb_profile()
        fmt_lower = fmt.lower()
        if fmt_lower in ("jpg", "jpeg"):
            extra = {"icc_profile": icc} if icc else {}
            img.save(str(path), "JPEG", quality=self._jpeg_quality, optimize=True, **extra)
        elif fmt_lower in ("tif", "tiff"):
            if icc:
                img.info["icc_profile"] = icc
            img.save(str(path), "TIFF")
        elif fmt_lower == "png":
            if icc:
                img.info["icc_profile"] = icc
            img.save(str(path), "PNG", optimize=True)
        else:
            extra = {"icc_profile": icc} if icc else {}
            img.save(str(path), "JPEG", quality=self._jpeg_quality, optimize=True, **extra)
