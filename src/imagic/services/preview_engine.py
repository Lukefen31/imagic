"""Shared preview engine — applies editing adjustments to decoded RAW arrays.

This module is used by both the desktop photo editor (for live preview)
and the native export processor (for WYSIWYG export).  It has NO PyQt6
dependency so it can run on headless servers.

All internal operations work on float32 [0, 1].  The public
``PreviewEngine.apply`` method accepts uint8 [0, 255] input and
returns uint8 [0, 255] output.
"""

from __future__ import annotations

import math
from typing import Dict

import numpy as np


# ======================================================================
# Fast blur helpers — prefer OpenCV (SIMD-optimised), fall back to scipy
# ======================================================================

def _cv2_blur(arr2d: np.ndarray, ksize: int) -> np.ndarray:
    """Box blur a 2-D float32 array (for clarity)."""
    try:
        import cv2
        return cv2.blur(arr2d, (ksize, ksize)).astype(np.float32)
    except ImportError:
        from scipy.ndimage import uniform_filter
        return uniform_filter(arr2d, size=ksize)


def _cv2_gaussian(img: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur a 3-channel float32 image per-channel."""
    try:
        import cv2
        ksize = max(3, int(sigma * 6) | 1)  # ensure odd
        return cv2.GaussianBlur(img, (ksize, ksize), sigma).astype(np.float32)
    except ImportError:
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(img, sigma=[sigma, sigma, 0])


def _cv2_gaussian_2d(arr2d: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur a single 2-D float32 channel."""
    try:
        import cv2
        ksize = max(3, int(sigma * 6) | 1)
        return cv2.GaussianBlur(arr2d, (ksize, ksize), sigma).astype(np.float32)
    except ImportError:
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(arr2d, sigma=sigma)


# ======================================================================
# Colour-grade look-up table
# ======================================================================

# Each grade maps to a dict of adjustments applied *on top of* user sliders.
# Keys: lift_r/g/b (shadows tint 0-1), gamma_r/g/b (mids tint 0-1),
#        gain_r/g/b (highlights tint 0-1), contrast, saturation,
#        fade (lifted blacks 0-1), tone_curve (list of (in,out) pairs 0-1)
GRADE_NULL = dict(
    lift_r=0, lift_g=0, lift_b=0,
    gamma_r=0, gamma_g=0, gamma_b=0,
    gain_r=0, gain_g=0, gain_b=0,
    contrast=0, saturation=0, fade=0, tone_curve=None,
)

GRADE_LUT: Dict[str, dict] = {
    # ---------- NATURAL / CLEAN ----------
    "natural": {**GRADE_NULL},
    "clean": {**GRADE_NULL, "contrast": 5, "saturation": 5},
    "soft_light": {**GRADE_NULL, "gamma_r": 0.02, "gamma_g": 0.01, "contrast": -5, "saturation": -5, "fade": 0.03},

    # ---------- FILM EMULATIONS ----------
    "film_warm": {**GRADE_NULL, "lift_r": 0.03, "lift_g": 0.015, "gain_r": 0.04, "gain_g": 0.02, "saturation": -8, "fade": 0.04,
                  "tone_curve": [(0, 0.03), (0.25, 0.28), (0.5, 0.53), (0.75, 0.77), (1, 0.98)]},
    "film_cool": {**GRADE_NULL, "lift_b": 0.035, "lift_g": 0.01, "gain_b": 0.025, "saturation": -10, "fade": 0.04,
                  "tone_curve": [(0, 0.03), (0.25, 0.27), (0.5, 0.52), (0.75, 0.76), (1, 0.97)]},
    "kodak_portra": {**GRADE_NULL, "lift_r": 0.025, "lift_g": 0.02, "lift_b": 0.01, "gain_r": 0.03, "saturation": -12, "fade": 0.05, "contrast": -3},
    "kodak_ektar": {**GRADE_NULL, "gain_r": 0.04, "gain_g": 0.01, "saturation": 15, "contrast": 10},
    "fuji_pro400h": {**GRADE_NULL, "lift_g": 0.02, "lift_b": 0.015, "gamma_g": 0.01, "saturation": -8, "fade": 0.03},
    "fuji_velvia": {**GRADE_NULL, "gain_r": 0.03, "gain_b": 0.02, "saturation": 25, "contrast": 12},
    "agfa_vista": {**GRADE_NULL, "lift_r": 0.02, "gain_g": 0.03, "saturation": 8, "contrast": 5, "fade": 0.02},
    "ilford_hp5": {**GRADE_NULL, "saturation": -100, "contrast": 15, "fade": 0.02,
                    "tone_curve": [(0, 0.02), (0.2, 0.22), (0.5, 0.52), (0.8, 0.82), (1, 0.98)]},

    # ---------- CINEMATIC ----------
    "cinematic": {**GRADE_NULL, "lift_b": 0.04, "lift_g": 0.02, "gain_r": 0.05, "gain_g": 0.02, "saturation": -10, "contrast": 8, "fade": 0.03},
    "teal_orange": {**GRADE_NULL, "lift_b": 0.05, "lift_g": 0.025, "gain_r": 0.06, "gain_g": 0.03, "saturation": -5, "contrast": 10},
    "blockbuster": {**GRADE_NULL, "lift_b": 0.06, "gain_r": 0.05, "contrast": 15, "saturation": -15, "fade": 0.02},
    "noir": {**GRADE_NULL, "saturation": -100, "contrast": 25, "fade": 0.0,
             "tone_curve": [(0, 0.0), (0.15, 0.08), (0.5, 0.50), (0.85, 0.92), (1, 1.0)]},
    "golden_hour": {**GRADE_NULL, "gamma_r": 0.04, "gamma_g": 0.02, "gain_r": 0.05, "gain_g": 0.03, "saturation": 5, "contrast": 3},

    # ---------- MOODY / DARK ----------
    "moody": {**GRADE_NULL, "lift_b": 0.03, "lift_g": 0.01, "gamma_b": 0.02, "saturation": -15, "contrast": 12, "fade": 0.06},
    "dark_moody": {**GRADE_NULL, "lift_b": 0.04, "gamma_b": 0.03, "contrast": 18, "saturation": -20, "fade": 0.04},
    "matte": {**GRADE_NULL, "saturation": -10, "contrast": -5, "fade": 0.10},
    "forest": {**GRADE_NULL, "lift_g": 0.03, "gamma_g": 0.02, "lift_b": 0.01, "saturation": -8, "contrast": 8, "fade": 0.04},

    # ---------- VIBRANT / POP ----------
    "vibrant": {**GRADE_NULL, "saturation": 20, "contrast": 10},
    "punch": {**GRADE_NULL, "saturation": 15, "contrast": 18,
              "tone_curve": [(0, 0.0), (0.2, 0.15), (0.5, 0.55), (0.8, 0.88), (1, 1.0)]},
    "summer": {**GRADE_NULL, "gamma_r": 0.02, "gamma_g": 0.02, "gain_r": 0.02, "saturation": 10, "contrast": 5, "fade": 0.02},
    "tropical": {**GRADE_NULL, "gain_r": 0.03, "gain_g": 0.04, "saturation": 18, "contrast": 8},

    # ---------- VINTAGE / RETRO ----------
    "faded": {**GRADE_NULL, "saturation": -20, "contrast": -8, "fade": 0.12,
              "tone_curve": [(0, 0.08), (0.25, 0.30), (0.5, 0.52), (0.75, 0.74), (1, 0.92)]},
    "vintage": {**GRADE_NULL, "lift_r": 0.03, "lift_g": 0.02, "gamma_r": 0.02, "saturation": -18, "fade": 0.08, "contrast": -3},
    "polaroid": {**GRADE_NULL, "lift_r": 0.04, "lift_g": 0.03, "lift_b": 0.01, "saturation": -12, "fade": 0.06, "contrast": 5,
                 "gain_r": 0.02, "gain_g": 0.01},
    "sepia": {**GRADE_NULL, "saturation": -100, "lift_r": 0.08, "lift_g": 0.05, "lift_b": 0.02, "gamma_r": 0.04, "gamma_g": 0.02, "fade": 0.03},
    "lomo": {**GRADE_NULL, "lift_b": 0.03, "gain_r": 0.04, "saturation": 12, "contrast": 15, "fade": 0.04},
    "cross_process": {**GRADE_NULL, "lift_b": 0.06, "gain_r": 0.05, "gain_g": 0.06, "gamma_b": -0.03, "saturation": 10, "contrast": 12},

    # ---------- B&W STYLES ----------
    "bw_classic": {**GRADE_NULL, "saturation": -100, "contrast": 12, "fade": 0.02},
    "bw_high_contrast": {**GRADE_NULL, "saturation": -100, "contrast": 30,
                          "tone_curve": [(0, 0.0), (0.15, 0.05), (0.5, 0.55), (0.85, 0.95), (1, 1.0)]},
    "bw_low_key": {**GRADE_NULL, "saturation": -100, "contrast": 20, "fade": 0.0,
                    "tone_curve": [(0, 0.0), (0.3, 0.18), (0.6, 0.50), (1, 0.90)]},
    "bw_faded": {**GRADE_NULL, "saturation": -100, "contrast": 5, "fade": 0.10},
    "bw_warm": {**GRADE_NULL, "saturation": -100, "lift_r": 0.04, "lift_g": 0.025, "lift_b": 0.01, "contrast": 10, "fade": 0.03},
    "bw_cool": {**GRADE_NULL, "saturation": -100, "lift_b": 0.04, "lift_g": 0.015, "contrast": 10, "fade": 0.03},

    # ---------- SPECIAL ----------
    "bleach_bypass": {**GRADE_NULL, "saturation": -30, "contrast": 25, "fade": 0.02},
    "orange_teal": {**GRADE_NULL, "lift_b": 0.04, "lift_g": 0.02, "gain_r": 0.06, "gain_g": 0.025, "saturation": 5, "contrast": 8},
    "pastel": {**GRADE_NULL, "saturation": -15, "contrast": -10, "fade": 0.08, "gamma_r": 0.02, "gamma_g": 0.02, "gamma_b": 0.02},
    "sunset": {**GRADE_NULL, "gain_r": 0.06, "gamma_r": 0.03, "gamma_g": 0.01, "saturation": 8, "contrast": 5, "fade": 0.02},
    "cyberpunk": {**GRADE_NULL, "lift_b": 0.05, "lift_r": 0.03, "gain_g": -0.02, "saturation": 10, "contrast": 15},
    "arctic": {**GRADE_NULL, "lift_b": 0.04, "gamma_b": 0.03, "gamma_g": 0.01, "saturation": -12, "contrast": 5, "fade": 0.03},
    "autumn": {**GRADE_NULL, "gain_r": 0.05, "gamma_r": 0.02, "gamma_g": 0.01, "lift_b": -0.01, "saturation": 5, "contrast": 8},
    "desert": {**GRADE_NULL, "gain_r": 0.04, "gain_g": 0.03, "gamma_r": 0.02, "saturation": -5, "contrast": 6, "fade": 0.04},
}


# ======================================================================
# Preview Engine
# ======================================================================

class PreviewEngine:
    """Applies editing adjustments to a decoded RAW numpy array.

    All operations work on float32 [0, 1] and convert back to uint8
    at the end. This gives instant preview without RawTherapee.
    """

    @staticmethod
    def apply(rgb: np.ndarray, params: dict) -> np.ndarray:
        """Apply all adjustments and return the result as uint8 RGB."""
        img = rgb.astype(np.float32) / 255.0

        # -- White Balance (Temperature + Tint) --
        temp = params.get("temperature", 0)
        tint = params.get("tint", 0)
        if temp != 0:
            factor = temp * 0.004
            img[:, :, 0] = img[:, :, 0] * (1 + factor)   # warm → red up
            img[:, :, 2] = img[:, :, 2] * (1 - factor)   # warm → blue down
        if tint != 0:
            factor = tint * 0.004
            img[:, :, 1] = img[:, :, 1] * (1 + factor)   # tint → green shift

        # -- Exposure --
        ev = params.get("exposure", 0) / 50.0  # maps -100..100 → -2..+2 EV
        if ev != 0:
            img = img * (2.0 ** ev)

        # -- Contrast --
        contrast = params.get("contrast", 0) / 100.0
        if contrast != 0:
            mid = np.mean(img)
            img = (img - mid) * (1.0 + contrast) + mid

        # -- Shared luminance for tonal adjustments --
        # Compute once and reuse for highlights/shadows/whites/blacks/clarity
        highlights = params.get("highlights", 0) / 100.0
        shadows = params.get("shadows", 0) / 100.0
        whites = params.get("whites", 0) / 200.0
        blacks = params.get("blacks", 0) / 200.0
        clarity = params.get("clarity", 0) / 100.0

        need_lum = highlights != 0 or shadows != 0 or whites != 0 or blacks != 0 or clarity != 0
        if need_lum:
            lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]

            if highlights != 0:
                mask = np.clip((lum - 0.5) * 2.0, 0, 1)
                img += (-highlights * 0.5 * mask)[:, :, np.newaxis]

            if shadows != 0:
                mask = np.clip(1.0 - lum * 2.0, 0, 1)
                img += (shadows * 0.5 * mask)[:, :, np.newaxis]

            if whites != 0:
                mask = np.clip((lum - 0.7) * 3.33, 0, 1)
                img += (whites * mask)[:, :, np.newaxis]

            if blacks != 0:
                mask = np.clip(1.0 - lum * 3.33, 0, 1)
                img += (blacks * mask)[:, :, np.newaxis]

            if clarity != 0:
                k = max(3, int(min(lum.shape) * 0.03)) | 1  # ensure odd
                blurred = _cv2_blur(lum, k)
                img += ((lum - blurred) * clarity * 0.8)[:, :, np.newaxis]

        # -- Dehaze --
        dehaze = params.get("dehaze", 0) / 100.0
        if dehaze != 0:
            dark = np.min(img, axis=2)
            atmo = np.percentile(dark, 99.9)
            t = 1.0 - dehaze * 0.6 * (dark / max(atmo, 0.01))
            t = np.clip(t, 0.2, 1.0)
            img = (img - atmo * (1 - t[:, :, np.newaxis])) / np.maximum(t[:, :, np.newaxis], 0.2)

        # -- Vibrance (boost low-sat pixels more) --
        vibrance = params.get("vibrance", 0) / 100.0
        if vibrance != 0:
            mx = np.max(img, axis=2)
            mn = np.min(img, axis=2)
            sat = np.where(mx > 0, (mx - mn) / (mx + 1e-6), 0)
            boost = (1.0 - sat) * vibrance * 0.5
            mean_c = np.mean(img, axis=2, keepdims=True)
            img = img + (img - mean_c) * boost[:, :, np.newaxis]

        # -- Saturation --
        saturation = params.get("saturation", 0) / 100.0
        if saturation != 0:
            lum = (0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2])
            img = lum[:, :, np.newaxis] + (img - lum[:, :, np.newaxis]) * (1.0 + saturation)

        # -- HSL adjustments --
        _apply_hsl(img, params)

        # -- Split Toning --
        _apply_split_toning(img, params)

        # -- Sharpening (unsharp mask, OpenCV fast path) --
        sharp_amount = params.get("sharp_amount", 0) / 100.0
        if sharp_amount > 0:
            radius = max(0.3, params.get("sharp_radius", 50) / 50.0)
            blurred = _cv2_gaussian(img, radius)
            img = img + (img - blurred) * sharp_amount * 2.0

        # -- Noise Reduction (OpenCV gaussian blur) --
        nr_lum = params.get("nr_luminance", 0) / 100.0
        if nr_lum > 0:
            img = _cv2_gaussian(img, nr_lum * 2.0)

        # -- Vignette --
        vig_amount = params.get("vignette_amount", 0)
        if vig_amount != 0:
            h, w = img.shape[:2]
            Y, X = np.ogrid[:h, :w]
            cy, cx = h / 2.0, w / 2.0
            r_max = math.sqrt(cx ** 2 + cy ** 2)
            midpoint = params.get("vignette_midpoint", 50) / 100.0
            dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2) / r_max
            mask = np.clip((dist - midpoint) / max(1.0 - midpoint, 0.01), 0, 1) ** 1.5
            strength = vig_amount / 100.0
            if strength > 0:
                img = img * (1.0 - mask[:, :, np.newaxis] * strength * 0.7)
            else:
                img = img * (1.0 + mask[:, :, np.newaxis] * abs(strength) * 0.3)

        # -- Grain --
        grain_amount = params.get("grain_amount", 0) / 100.0
        if grain_amount > 0:
            noise = np.random.normal(0, grain_amount * 0.08, img.shape).astype(np.float32)
            img = img + noise

        # -- Color Grade --
        grade_name = params.get("color_grade", "natural")
        grade_intensity = params.get("color_grade_intensity", 100) / 100.0
        grade = GRADE_LUT.get(grade_name, GRADE_NULL)
        if grade_name != "natural" and grade_intensity > 0:
            img = apply_color_grade(img, grade, grade_intensity)

        # ==============================================================
        # Expert mode adjustments
        # ==============================================================

        # -- Tone Curve (per-channel or luminance) --
        img = _apply_tone_curves(img, params)

        # -- Soft Light / Glow --
        soft_light = params.get("soft_light", 0) / 100.0
        if soft_light > 0:
            screen = 1.0 - (1.0 - img) * (1.0 - img)
            img = img * (1.0 - soft_light) + screen * soft_light

        # -- Micro Sharpening --
        micro_strength = params.get("micro_sharp_strength", 0) / 100.0
        if micro_strength > 0:
            micro_contrast = params.get("micro_sharp_contrast", 20) / 100.0
            blurred = _cv2_gaussian(img, 0.5)
            detail = img - blurred
            detail_mag = np.abs(detail)
            mask = np.clip(detail_mag / max(micro_contrast * 0.3, 0.01), 0, 1)
            img = img + detail * micro_strength * 1.5 * mask

        # -- Defringe (simple chromatic aberration reduction) --
        defringe_radius = params.get("defringe_radius", 20)
        defringe_threshold = params.get("defringe_threshold", 13)
        if defringe_radius != 20 or defringe_threshold != 13:
            radius = defringe_radius / 10.0
            lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
            for ch in range(3):
                diff = img[:, :, ch] - lum
                fringe_mask = np.abs(diff) > (defringe_threshold / 100.0)
                if np.any(fringe_mask):
                    smoothed = _cv2_gaussian_2d(img[:, :, ch], radius)
                    img[:, :, ch] = np.where(fringe_mask, smoothed, img[:, :, ch])

        # -- Perspective Correction --
        persp_h = params.get("perspective_h", 0)
        persp_v = params.get("perspective_v", 0)
        if persp_h != 0 or persp_v != 0:
            img = _apply_perspective(img, persp_h, persp_v)

        # -- Distortion Correction --
        distortion = params.get("distortion", 0)
        if distortion != 0:
            img = _apply_distortion(img, distortion / 100.0)

        # -- Final clip and convert --
        return np.clip(img * 255, 0, 255).astype(np.uint8)


# ======================================================================
# Helper functions
# ======================================================================

def _apply_hsl(img: np.ndarray, params: dict) -> None:
    """Apply HSL (Hue/Saturation/Luminance) per-channel adjustments in-place."""
    channels = ["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
    has_hsl = any(
        params.get(f"hsl_{t}_{c}", 0) != 0
        for t in ("hue", "sat", "lum")
        for c in channels
    )
    if not has_hsl:
        return

    mx = np.max(img, axis=2)
    mn = np.min(img, axis=2)
    delta = mx - mn + 1e-7

    hue = np.zeros_like(mx)
    r, g, b = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    mask_r = mx == r
    mask_g = (mx == g) & ~mask_r
    mask_b = ~mask_r & ~mask_g
    hue[mask_r] = (60 * ((g[mask_r] - b[mask_r]) / delta[mask_r])) % 360
    hue[mask_g] = 60 * ((b[mask_g] - r[mask_g]) / delta[mask_g]) + 120
    hue[mask_b] = 60 * ((r[mask_b] - g[mask_b]) / delta[mask_b]) + 240
    hue = hue % 360

    hue_ranges = {
        "red": (0, 30), "orange": (30, 20), "yellow": (55, 20),
        "green": (120, 40), "aqua": (180, 30), "blue": (230, 30),
        "purple": (275, 25), "magenta": (320, 30),
    }

    for ch_name in channels:
        sat_adj = params.get(f"hsl_sat_{ch_name}", 0) / 100.0
        lum_adj = params.get(f"hsl_lum_{ch_name}", 0) / 100.0
        if sat_adj == 0 and lum_adj == 0:
            continue

        center, width = hue_ranges[ch_name]
        diff = np.abs(hue - center)
        diff = np.minimum(diff, 360 - diff)
        mask = np.clip(1.0 - diff / width, 0, 1) ** 2

        if sat_adj != 0:
            lum_val = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
            chroma = img - lum_val[:, :, np.newaxis]
            img[:] = img + chroma * sat_adj * mask[:, :, np.newaxis]

        if lum_adj != 0:
            img[:] = img + lum_adj * 0.3 * mask[:, :, np.newaxis]


def _apply_split_toning(img: np.ndarray, params: dict) -> None:
    """Apply split-toning (shadow/highlight color grading) in-place."""
    sh_hue = params.get("split_shadow_hue", 0)
    sh_sat = params.get("split_shadow_sat", 0) / 100.0
    hl_hue = params.get("split_highlight_hue", 0)
    hl_sat = params.get("split_highlight_sat", 0) / 100.0

    if sh_sat == 0 and hl_sat == 0:
        return

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
    balance = params.get("split_balance", 0) / 100.0
    mid = 0.5 + balance * 0.3

    if sh_sat > 0:
        shadow_mask = np.clip(1.0 - lum / mid, 0, 1)
        r = math.cos(math.radians(sh_hue)) * sh_sat * 0.15
        g = math.cos(math.radians(sh_hue - 120)) * sh_sat * 0.15
        b = math.cos(math.radians(sh_hue - 240)) * sh_sat * 0.15
        img[:, :, 0] += r * shadow_mask
        img[:, :, 1] += g * shadow_mask
        img[:, :, 2] += b * shadow_mask

    if hl_sat > 0:
        hl_mask = np.clip((lum - mid) / (1.0 - mid + 1e-6), 0, 1)
        r = math.cos(math.radians(hl_hue)) * hl_sat * 0.15
        g = math.cos(math.radians(hl_hue - 120)) * hl_sat * 0.15
        b = math.cos(math.radians(hl_hue - 240)) * hl_sat * 0.15
        img[:, :, 0] += r * hl_mask
        img[:, :, 1] += g * hl_mask
        img[:, :, 2] += b * hl_mask


def apply_color_grade(img: np.ndarray, grade: dict, intensity: float) -> np.ndarray:
    """Apply color-grade LUT adjustments to a float32 [0,1] image.

    Uses ASC-CDL style lift/gamma/gain per channel, plus tone curve,
    fade (lifted blacks), and overall saturation/contrast shift.
    Intensity blends between original and graded result.
    """
    graded = img.copy()

    # -- Tone curve (applied first) --
    tc = grade.get("tone_curve")
    if tc:
        from scipy.interpolate import interp1d
        xs, ys = zip(*tc)
        curve_fn = interp1d(xs, ys, kind="linear", bounds_error=False, fill_value=(ys[0], ys[-1]))
        lum = 0.2126 * graded[:, :, 0] + 0.7152 * graded[:, :, 1] + 0.0722 * graded[:, :, 2]
        lum_new = curve_fn(np.clip(lum, 0, 1)).astype(np.float32)
        ratio = lum_new / (lum + 1e-7)
        graded = graded * ratio[:, :, np.newaxis]

    # -- Lift (shadows tint) --
    lr, lg, lb = grade.get("lift_r", 0), grade.get("lift_g", 0), grade.get("lift_b", 0)
    if lr or lg or lb:
        shadow = 1.0 - graded
        graded[:, :, 0] += lr * shadow[:, :, 0]
        graded[:, :, 1] += lg * shadow[:, :, 1]
        graded[:, :, 2] += lb * shadow[:, :, 2]

    # -- Gamma (midtones tint) --
    gr, gg, gb = grade.get("gamma_r", 0), grade.get("gamma_g", 0), grade.get("gamma_b", 0)
    if gr or gg or gb:
        mid = 1.0 - np.abs(graded - 0.5) * 2.0
        graded[:, :, 0] += gr * mid[:, :, 0]
        graded[:, :, 1] += gg * mid[:, :, 1]
        graded[:, :, 2] += gb * mid[:, :, 2]

    # -- Gain (highlights tint) --
    gnr, gng, gnb = grade.get("gain_r", 0), grade.get("gain_g", 0), grade.get("gain_b", 0)
    if gnr or gng or gnb:
        graded[:, :, 0] += gnr * graded[:, :, 0]
        graded[:, :, 1] += gng * graded[:, :, 1]
        graded[:, :, 2] += gnb * graded[:, :, 2]

    # -- Fade (lifted blacks) --
    fade = grade.get("fade", 0)
    if fade > 0:
        graded = graded * (1.0 - fade) + fade

    # -- Saturation shift --
    sat = grade.get("saturation", 0) / 100.0
    if sat != 0:
        lum = 0.2126 * graded[:, :, 0] + 0.7152 * graded[:, :, 1] + 0.0722 * graded[:, :, 2]
        if sat <= -1.0:
            graded[:, :, 0] = lum
            graded[:, :, 1] = lum
            graded[:, :, 2] = lum
        else:
            chroma = graded - lum[:, :, np.newaxis]
            graded = lum[:, :, np.newaxis] + chroma * (1.0 + sat)

    # -- Contrast shift --
    con = grade.get("contrast", 0) / 100.0
    if con != 0:
        graded = (graded - 0.5) * (1.0 + con) + 0.5

    # -- Blend with original by intensity --
    graded = np.clip(graded, 0, 1)
    if intensity < 1.0:
        graded = img * (1.0 - intensity) + graded * intensity

    return graded


# ======================================================================
# Expert mode helpers
# ======================================================================

def _apply_tone_curves(img: np.ndarray, params: dict) -> np.ndarray:
    """Apply user-drawn tone curves (luminance and/or per-channel).

    Curve data is stored as ``tone_curve_luminance``, ``tone_curve_red``,
    ``tone_curve_green``, ``tone_curve_blue`` — each a list of (x, y)
    control points in [0, 1].
    """
    has_curve = any(f"tone_curve_{ch}" in params for ch in ("luminance", "red", "green", "blue"))
    if not has_curve:
        return img

    def _build_lut(points, size=256):
        """Monotone cubic interpolation from control points to LUT."""
        if len(points) < 2:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        # Check if it's just the identity line
        if len(points) == 2 and abs(xs[0]) < 0.001 and abs(ys[0]) < 0.001 and abs(xs[1] - 1) < 0.001 and abs(ys[1] - 1) < 0.001:
            return None
        n = len(xs)
        if n == 2:
            # Linear interpolation
            lut = np.linspace(ys[0], ys[1], size).astype(np.float32)
            return np.clip(lut, 0, 1)

        # Hermite spline interpolation
        deltas = [(ys[i + 1] - ys[i]) / max(xs[i + 1] - xs[i], 1e-6) for i in range(n - 1)]
        ms = [0.0] * n
        ms[0] = deltas[0]
        ms[-1] = deltas[-1]
        for i in range(1, n - 1):
            if deltas[i - 1] * deltas[i] <= 0:
                ms[i] = 0
            else:
                ms[i] = (deltas[i - 1] + deltas[i]) / 2
        for i in range(n - 1):
            if abs(deltas[i]) < 1e-10:
                ms[i] = 0
                ms[i + 1] = 0
            else:
                alpha = ms[i] / deltas[i]
                beta = ms[i + 1] / deltas[i]
                s = alpha * alpha + beta * beta
                if s > 9:
                    tau = 3.0 / (s ** 0.5)
                    ms[i] = tau * alpha * deltas[i]
                    ms[i + 1] = tau * beta * deltas[i]

        lut = np.zeros(size, dtype=np.float32)
        for j in range(size):
            x = j / (size - 1)
            seg = 0
            for i in range(n - 1):
                if x >= xs[i]:
                    seg = i
            if seg >= n - 1:
                seg = n - 2
            h = xs[seg + 1] - xs[seg]
            if h < 1e-10:
                lut[j] = ys[seg]
                continue
            t = (x - xs[seg]) / h
            h00 = 2 * t ** 3 - 3 * t ** 2 + 1
            h10 = t ** 3 - 2 * t ** 2 + t
            h01 = -2 * t ** 3 + 3 * t ** 2
            h11 = t ** 3 - t ** 2
            lut[j] = h00 * ys[seg] + h10 * h * ms[seg] + h01 * ys[seg + 1] + h11 * h * ms[seg + 1]
        return np.clip(lut, 0, 1)

    # Luminance curve
    lum_pts = params.get("tone_curve_luminance")
    if lum_pts and len(lum_pts) >= 2:
        lut = _build_lut(lum_pts)
        if lut is not None:
            lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
            lum_clipped = np.clip(lum, 0, 1)
            indices = (lum_clipped * 255).astype(np.int32)
            indices = np.clip(indices, 0, 255)
            lum_new = lut[indices]
            ratio = lum_new / (lum + 1e-7)
            img = img * ratio[:, :, np.newaxis]
            img = np.clip(img, 0, 1)

    # Per-channel curves
    for ch_idx, ch_name in enumerate(("red", "green", "blue")):
        pts = params.get(f"tone_curve_{ch_name}")
        if pts and len(pts) >= 2:
            lut = _build_lut(pts)
            if lut is not None:
                ch = np.clip(img[:, :, ch_idx], 0, 1)
                indices = (ch * 255).astype(np.int32)
                indices = np.clip(indices, 0, 255)
                img[:, :, ch_idx] = lut[indices]

    return img


def _apply_perspective(img: np.ndarray, h_angle: float, v_angle: float) -> np.ndarray:
    """Apply perspective correction using affine approximation.

    h_angle and v_angle are in degrees (-45 to 45).
    """
    if abs(h_angle) < 0.5 and abs(v_angle) < 0.5:
        return img

    try:
        import cv2
        rows, cols = img.shape[:2]
        # Convert angles to perspective transform
        h_rad = math.radians(h_angle * 0.5)
        v_rad = math.radians(v_angle * 0.5)

        # Source corners
        src = np.float32([
            [0, 0], [cols, 0],
            [cols, rows], [0, rows],
        ])

        # Destination with perspective shift
        h_shift = math.tan(h_rad) * rows * 0.3
        v_shift = math.tan(v_rad) * cols * 0.3

        dst = np.float32([
            [0 + h_shift, 0 + v_shift],
            [cols - h_shift, 0 - v_shift],
            [cols + h_shift, rows + v_shift],
            [0 - h_shift, rows - v_shift],
        ])

        M = cv2.getPerspectiveTransform(src, dst)
        result = cv2.warpPerspective(
            img, M, (cols, rows),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101,
        )
        return result
    except ImportError:
        return img


def _apply_distortion(img: np.ndarray, amount: float) -> np.ndarray:
    """Apply barrel/pincushion distortion correction.

    amount > 0 = barrel correction (counteracts pincushion)
    amount < 0 = pincushion correction (counteracts barrel)
    """
    if abs(amount) < 0.005:
        return img

    try:
        import cv2
        rows, cols = img.shape[:2]
        # Camera matrix (centered)
        fx = fy = max(rows, cols)
        cx, cy = cols / 2.0, rows / 2.0
        camera_matrix = np.array([
            [fx, 0, cx],
            [0, fy, cy],
            [0, 0, 1],
        ], dtype=np.float64)

        # Distortion coefficients: k1 controls barrel/pincushion
        k1 = -amount * 0.5
        dist_coeffs = np.array([k1, 0, 0, 0, 0], dtype=np.float64)

        result = cv2.undistort(img, camera_matrix, dist_coeffs)
        return result
    except ImportError:
        return img
