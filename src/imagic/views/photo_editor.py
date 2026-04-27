"""Professional photo editor — Lightroom-style editing with live preview.

Full-featured editing dialog with:
- Real-time preview using numpy-based image processing
- Collapsible editing panels: Basic, Tone, Color, Detail, Effects, AI
- RGB histogram
- Film-strip navigation
- Before/After comparison
- AI auto-enhance features
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from PyQt6.QtCore import (
    QPoint,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# ======================================================================
# Style constants
# ======================================================================

_BG = "#0d0d0d"
_PANEL_BG = "rgba(18, 18, 18, 240)"
_PANEL_BG_SOLID = "#121212"
_SECTION_BG = "#1a1a1a"
_BORDER = "#232323"
_TEXT = "#d6d6d6"
_TEXT_DIM = "#787878"
_TEXT_MUTED = "#505050"
_ACCENT = "#e89530"
_ACCENT_HOVER = "#f5ad4a"
_ACCENT_DIM = "rgba(232, 149, 48, 0.12)"
_GREEN = "#3cb44e"
_GREEN_HOVER = "#4cc85e"
_GREEN_PRESSED = "#2e9a3e"
_SLIDER_GROOVE = "#282828"
_SLIDER_HANDLE = "#e89530"
_SLIDER_SUB = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b87200, stop:1 #e89530)"
_BTN_BG = "#1e1e1e"
_BTN_BORDER = "#333333"
_BTN_HOVER = "#282828"

_SLIDER_STYLE = (
    f"QSlider::groove:horizontal {{ height: 2px; background: {_SLIDER_GROOVE}; border-radius: 1px; }}"
    f"QSlider::handle:horizontal {{ width: 11px; height: 11px; margin: -5px 0; "
    f"background: {_SLIDER_HANDLE}; border: 1.5px solid {_PANEL_BG_SOLID}; border-radius: 6px; }}"
    f"QSlider::handle:horizontal:hover {{ background: {_ACCENT_HOVER}; }}"
    f"QSlider::sub-page:horizontal {{ background: {_SLIDER_SUB}; border-radius: 1px; }}"
)

_GRADES = [
    "natural",
    "film_warm",
    "film_cool",
    "moody",
    "vibrant",
    "cinematic",
    "faded",
    "bw_classic",
]

# -- Colour-grade engine (shared with native export) ----------------------
from imagic.services.editor_style_presets import get_editor_style_overrides
from imagic.services.preview_engine import (
    GRADE_LUT as _GRADE_LUT,
)
from imagic.services.preview_engine import (
    PreviewEngine,
)
from imagic.services.preview_engine import (
    apply_color_grade as _apply_color_grade,
)
from imagic.views.widgets.ai_loading_modal import AILoadingModal
from imagic.views.widgets.color_wheels import ColorWheelsWidget
from imagic.views.widgets.tone_curve import ToneCurveWidget

# ======================================================================
# Background RAW decoder
# ======================================================================


class _BatchOptimizeWorker(QThread):
    """Decode and AI-optimize all photos in the background."""

    # (index, suggestions_dict, rgb_array)
    photo_optimized = pyqtSignal(int, dict, object)
    finished_all = pyqtSignal(int)  # number of errors

    def __init__(
        self,
        photos: list[dict],
        rgb_cache: dict[int, np.ndarray],
        user_style: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._photos = photos
        self._rgb_cache = rgb_cache
        self._user_style = user_style  # averaged edits from first N photos

    def run(self) -> None:
        import rawpy

        error_count = 0
        for i, p in enumerate(self._photos):
            try:
                # Use cache if available, otherwise decode
                if i in self._rgb_cache:
                    rgb = self._rgb_cache[i]
                else:
                    file_path = p.get("file_path", "")
                    if not file_path or not Path(file_path).is_file():
                        continue
                    suffix = Path(file_path).suffix.lower()
                    if suffix in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
                        from PIL import Image

                        img = Image.open(file_path).convert("RGB")
                        rgb = np.asarray(img, dtype=np.uint8)
                    else:
                        with rawpy.imread(file_path) as raw:
                            rgb = raw.postprocess(
                                use_camera_wb=True,
                                no_auto_bright=True,
                                output_bps=8,
                                half_size=True,
                                demosaic_algorithm=rawpy.DemosaicAlgorithm.DHT,
                                output_color=rawpy.ColorSpace.sRGB,
                            )

                # Compute suggestions (same logic as _ai_optimize_all)
                img = rgb.astype(np.float32) / 255.0
                lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
                mean_lum = float(np.mean(lum))
                is_dark = mean_lum < 0.25

                suggestions = ai_auto_enhance(rgb)

                wb = ai_auto_wb(rgb)
                if is_dark:
                    wb = {k: v // 2 for k, v in wb.items()}
                suggestions.update(wb)

                iso = p.get("exif_iso")
                if iso and int(iso) > 1600:
                    suggestions["nr_luminance"] = min(40, int(iso) // 150)
                    suggestions["nr_color"] = min(30, int(iso) // 200)
                else:
                    suggestions["nr_luminance"] = 15
                    suggestions["nr_color"] = 10

                if mean_lum > 0.12:
                    try:
                        from scipy.ndimage import laplace

                        gray = np.mean(img, axis=2)
                        lap_var = float(np.var(laplace(gray)))
                        if lap_var < 0.0003:
                            suggestions["sharp_amount"] = 22
                        elif lap_var < 0.001:
                            suggestions["sharp_amount"] = 14
                        else:
                            suggestions["sharp_amount"] = 8
                        suggestions["sharp_radius"] = 40
                    except ImportError:
                        suggestions["sharp_amount"] = 14
                        suggestions["sharp_radius"] = 40
                else:
                    suggestions["sharp_amount"] = 6
                    suggestions["sharp_radius"] = 35

                # Blend user style from first N edited photos
                if self._user_style:
                    for k, v in self._user_style.items():
                        if k in suggestions:
                            # Weighted blend: 75% per-photo AI, 25% user style
                            # (lower user weight keeps photos visually distinct)
                            suggestions[k] = int(suggestions[k] * 0.75 + v * 0.25)
                        else:
                            suggestions[k] = int(v * 0.5)

                # Visual refinement: render a preview and verify quality
                suggestions = ai_visual_refine(rgb, suggestions)

                self.photo_optimized.emit(i, suggestions, rgb)
            except Exception as exc:
                error_count += 1
                logger.warning("Batch optimize failed for %s: %s", p.get("file_name", "?"), exc)

        self.finished_all.emit(error_count)


class _AITaskWorker(QThread):
    """Generic worker for running AI tasks off the main thread."""

    finished = pyqtSignal(object)  # result object
    error = pyqtSignal(str)  # error message

    def __init__(self, fn, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(result)
        except SystemExit as exc:
            self.error.emit(f"AI task exited unexpectedly: {exc}")
        except Exception as exc:
            self.error.emit(str(exc))


class _RawDecodeWorker(QThread):
    """Decode a RAW file to a numpy RGB array in the background."""

    decoded = pyqtSignal(int, object)  # index, numpy array (H, W, 3) uint8
    decode_failed = pyqtSignal(int, str)  # index, error message

    def __init__(self, index: int, file_path: str, half_size: bool = True, parent=None):
        super().__init__(parent)
        self._index = index
        self._file_path = file_path
        self._half_size = half_size

    def run(self) -> None:
        try:
            suffix = Path(self._file_path).suffix.lower()
            # Non-RAW files — load with Pillow instead of rawpy
            if suffix in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
                from PIL import Image

                img = Image.open(self._file_path).convert("RGB")
                rgb = np.asarray(img, dtype=np.uint8)
                self.decoded.emit(self._index, rgb)
                return

            import rawpy

            with rawpy.imread(self._file_path) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                    half_size=self._half_size,
                    # DHT is faster than AHD with near-identical quality for preview
                    demosaic_algorithm=rawpy.DemosaicAlgorithm.DHT
                    if self._half_size
                    else rawpy.DemosaicAlgorithm.AHD,
                    output_color=rawpy.ColorSpace.sRGB,
                )
            self.decoded.emit(self._index, rgb)
        except Exception as exc:
            logger.warning("RAW decode failed for %s: %s", self._file_path, exc)
            self.decode_failed.emit(self._index, str(exc))


class _ThumbDecodeWorker(QThread):
    """Decode multiple thumbnail images for the film strip."""

    thumb_ready = pyqtSignal(int, QPixmap)  # index, pixmap

    def __init__(self, items: list[tuple[int, str]], size: int = 80, parent=None):
        super().__init__(parent)
        self._items = items
        self._size = size

    def run(self) -> None:
        for idx, path in self._items:
            try:
                pix = QPixmap(path)
                if not pix.isNull():
                    pix = pix.scaled(
                        QSize(self._size, self._size),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.thumb_ready.emit(idx, pix)
            except Exception:
                pass


# ======================================================================
# AI Auto-Enhance
# ======================================================================


def ai_auto_enhance(rgb: np.ndarray) -> dict:
    """Analyse the image and return suggested adjustment parameters."""
    img = rgb.astype(np.float32) / 255.0
    params: dict = {}

    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
    mean_lum = float(np.mean(lum))
    std_lum = float(np.std(lum))
    float(np.median(lum))
    float(np.percentile(lum, 99))

    # Any image with mean luminance below 0.25 is "dark" — be conservative.
    # Most intentional dark scenes (clubs, concerts, night) fall here.
    is_dark = mean_lum < 0.25

    if is_dark:
        # Gentle lift: aim to improve visibility without destroying mood.
        # Scale the target based on how dark the image is.
        # Very dark (mean 0.02) → target ~0.06 (3x)
        # Moderately dark (mean 0.13) → target ~0.18 (1.4x)
        target = mean_lum + min(0.05, mean_lum * 0.4)

        if mean_lum < target - 0.01 and mean_lum > 0.001:
            ev_need = (target - mean_lum) / mean_lum
            params["exposure"] = int(np.clip(ev_need * 40, 0, 18))

        # Highlights recovery — protect any existing bright areas
        clip_hi = float(np.sum(lum > 0.92) / lum.size)
        if clip_hi > 0.005:
            params["highlights"] = int(max(-40, -clip_hi * 600))

        # Very gentle shadow lift only if extremely crushed
        clip_lo = float(np.sum(lum < 0.03) / lum.size)
        if clip_lo > 0.4:
            params["shadows"] = int(min(10, clip_lo * 12))

        # Minimal contrast for very flat dark images
        if std_lum < 0.08:
            params["contrast"] = 6

        # Dehaze to combat faded/hazy look in low-light
        params["dehaze"] = 8

    else:
        # Normal/bright image handling
        target = 0.42
        if mean_lum < 0.35:
            diff = target - mean_lum
            params["exposure"] = int(np.clip(diff * 70, 0, 20))
        elif mean_lum > 0.60:
            params["exposure"] = int(max(-25, (0.45 - mean_lum) * 75))

        if std_lum < 0.15:
            params["contrast"] = int(min(14, (0.18 - std_lum) * 110))
        elif std_lum > 0.28:
            params["contrast"] = int(max(-12, (0.22 - std_lum) * 80))

        clip_hi = float(np.sum(lum > 0.95) / lum.size)
        if clip_hi > 0.01:
            params["highlights"] = int(max(-40, -clip_hi * 600))

        clip_lo = float(np.sum(lum < 0.05) / lum.size)
        if clip_lo > 0.10:
            params["shadows"] = int(min(18, clip_lo * 70))

        # Vibrance for desaturated images
        mx = np.max(img, axis=2)
        mn = np.min(img, axis=2)
        sat = np.where(mx > 0, (mx - mn) / (mx + 1e-6), 0)
        mean_sat = float(np.mean(sat))
        if mean_sat < 0.15:
            params["vibrance"] = int(min(18, (0.18 - mean_sat) * 110))

        if std_lum < 0.18:
            params["clarity"] = 6

    return params


def ai_auto_wb(rgb: np.ndarray) -> dict:
    """Estimate white balance corrections using grey-world assumption."""
    img = rgb.astype(np.float32)
    avg_r = float(np.mean(img[:, :, 0]))
    avg_g = float(np.mean(img[:, :, 1]))
    avg_b = float(np.mean(img[:, :, 2]))
    avg_all = (avg_r + avg_g + avg_b) / 3.0

    temp_shift = (avg_r - avg_b) / max(avg_all, 1) * -30
    tint_shift = (avg_g - (avg_r + avg_b) / 2) / max(avg_all, 1) * -20

    return {
        "temperature": int(np.clip(temp_shift, -50, 50)),
        "tint": int(np.clip(tint_shift, -50, 50)),
    }


# ======================================================================
# AI Variation Engine
# ======================================================================

# How much each parameter type is allowed to vary (absolute range, per-side).
# Conservative for tonal params, more generous for creative ones.
_VARIATION_RANGES: dict[str, int] = {
    "exposure": 8,
    "contrast": 10,
    "highlights": 12,
    "shadows": 12,
    "whites": 8,
    "blacks": 8,
    "temperature": 8,
    "tint": 6,
    "clarity": 10,
    "dehaze": 8,
    "vibrance": 12,
    "saturation": 10,
    "sharp_amount": 15,
    "sharp_radius": 10,
    "nr_luminance": 10,
    "nr_color": 8,
    "vignette_amount": 10,
    "grain_amount": 5,
}

# Curated "flavour" offsets — the first re-run picks flavour 0, second
# picks flavour 1, etc.  After exhausting flavours, seeded jitter is used.
_FLAVOURS: list[dict[str, int]] = [
    {"contrast": 8, "clarity": 6, "vibrance": 5, "shadows": 5},  # punchier
    {"contrast": -6, "exposure": 4, "highlights": -8, "dehaze": 6},  # softer / lifted
    {"vibrance": 12, "saturation": 6, "temperature": -5},  # cooler & vivid
    {"temperature": 6, "tint": 3, "vibrance": -5, "contrast": 5},  # warmer & muted
    {"clarity": -10, "contrast": -4, "shadows": 10, "grain_amount": 8},  # dreamy / film
    {"dehaze": 12, "contrast": 6, "blacks": -6, "clarity": 8},  # crisp & bold
]


def vary_suggestions(base: dict, run: int, slider_ranges: dict[str, tuple] | None = None) -> dict:
    """Return a varied copy of *base* suggestions for the given *run* index.

    Run 0 returns the original (unmodified) suggestions.  Runs 1-6 blend in
    curated flavour offsets.  Runs 7+ use seeded pseudo-random jitter so that
    re-runs are deterministic per *run* number but different from each other.

    Args:
        base: The deterministic AI suggestions dict.
        run: How many times the user has already triggered this AI tool on
             the current photo (0 = first time).
        slider_ranges: Optional mapping of key → (min, max) to clamp values.

    Returns:
        A new dict with varied parameter values.
    """
    if run == 0:
        return dict(base)

    import random

    result = dict(base)

    if run <= len(_FLAVOURS):
        flavour = _FLAVOURS[run - 1]
        for key, offset in flavour.items():
            if key in result:
                result[key] = result[key] + offset
            else:
                result[key] = offset
    else:
        rng = random.Random(run * 7919)  # deterministic per run
        for key, value in base.items():
            max_delta = _VARIATION_RANGES.get(key, 8)
            jitter = rng.randint(-max_delta, max_delta)
            result[key] = value + jitter

    # Clamp to slider bounds if provided
    if slider_ranges:
        for key in result:
            if key in slider_ranges:
                lo, hi = slider_ranges[key]
                result[key] = max(lo, min(hi, result[key]))

    return result


def ai_visual_refine(rgb: np.ndarray, suggestions: dict) -> dict:
    """Compare original vs optimized image and refine suggestions.

    Renders a small proxy of the optimized result, analyses perceptual
    quality differences, and nudges parameters back if the edit made
    things worse (blown highlights, crushed blacks, colour cast, over-
    saturation, or excessive brightness shift).
    """
    # Work on a small proxy for speed (~500px max)
    h, w = rgb.shape[:2]
    scale = min(1.0, 500.0 / max(h, w))
    if scale < 1.0:
        sh, sw = int(h * scale), int(w * scale)
        try:
            import cv2

            small = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA)
        except ImportError:
            # stride-based downsample
            step_h = max(1, h // sh)
            step_w = max(1, w // sw)
            small = rgb[::step_h, ::step_w]
    else:
        small = rgb

    # Original stats
    orig = small.astype(np.float32) / 255.0
    orig_lum = 0.2126 * orig[:, :, 0] + 0.7152 * orig[:, :, 1] + 0.0722 * orig[:, :, 2]
    orig_mean = float(np.mean(orig_lum))
    orig_std = float(np.std(orig_lum))
    orig_clip_hi = float(np.sum(orig_lum > 0.95) / orig_lum.size)
    orig_clip_lo = float(np.sum(orig_lum < 0.03) / orig_lum.size)

    # Render with current suggestions
    optimized = PreviewEngine.apply(small, suggestions)
    opt = optimized.astype(np.float32) / 255.0
    opt_lum = 0.2126 * opt[:, :, 0] + 0.7152 * opt[:, :, 1] + 0.0722 * opt[:, :, 2]
    opt_mean = float(np.mean(opt_lum))
    opt_std = float(np.std(opt_lum))
    opt_clip_hi = float(np.sum(opt_lum > 0.97) / opt_lum.size)
    opt_clip_lo = float(np.sum(opt_lum < 0.02) / opt_lum.size)

    refined = dict(suggestions)

    # --- Check 1: Blown highlights ---
    # If optimized has significantly more clipped highlights than original
    hi_increase = opt_clip_hi - orig_clip_hi
    if hi_increase > 0.02:
        # Pull back exposure
        cur_exp = refined.get("exposure", 0)
        pullback = int(min(cur_exp * 0.4, hi_increase * 300))
        if pullback > 0 and cur_exp > 0:
            refined["exposure"] = max(0, cur_exp - pullback)
        # Add highlight recovery
        cur_hl = refined.get("highlights", 0)
        refined["highlights"] = max(-60, cur_hl - int(hi_increase * 500))

    # --- Check 2: Crushed blacks ---
    lo_increase = opt_clip_lo - orig_clip_lo
    if lo_increase > 0.03:
        # Lift shadows / reduce contrast
        cur_shadows = refined.get("shadows", 0)
        refined["shadows"] = min(30, cur_shadows + int(lo_increase * 200))
        cur_contrast = refined.get("contrast", 0)
        if cur_contrast > 5:
            refined["contrast"] = max(0, cur_contrast - int(lo_increase * 150))

    # --- Check 3: Over-brightened ---
    brightness_ratio = opt_mean / max(orig_mean, 0.001)
    if brightness_ratio > 2.5 and orig_mean > 0.05:
        # Image got way too bright — dial exposure back
        cur_exp = refined.get("exposure", 0)
        refined["exposure"] = max(0, int(cur_exp * 0.5))
    elif brightness_ratio > 1.8 and orig_mean > 0.15:
        cur_exp = refined.get("exposure", 0)
        refined["exposure"] = max(0, int(cur_exp * 0.7))

    # --- Check 4: Lost contrast (became too flat) ---
    if opt_std < orig_std * 0.7 and orig_std > 0.08:
        cur_contrast = refined.get("contrast", 0)
        refined["contrast"] = min(25, cur_contrast + 8)
        refined["clarity"] = max(refined.get("clarity", 0), 8)

    # --- Check 5: Over-saturated ---
    orig_mx = np.max(orig, axis=2)
    orig_mn = np.min(orig, axis=2)
    orig_sat = float(np.mean(np.where(orig_mx > 0, (orig_mx - orig_mn) / (orig_mx + 1e-6), 0)))
    opt_mx = np.max(opt, axis=2)
    opt_mn = np.min(opt, axis=2)
    opt_sat = float(np.mean(np.where(opt_mx > 0, (opt_mx - opt_mn) / (opt_mx + 1e-6), 0)))

    if opt_sat > orig_sat * 1.5 and opt_sat > 0.25:
        # Over-saturated — pull vibrance/saturation back
        cur_vib = refined.get("vibrance", 0)
        if cur_vib > 5:
            refined["vibrance"] = max(0, int(cur_vib * 0.5))
        cur_sat = refined.get("saturation", 0)
        if cur_sat > 5:
            refined["saturation"] = max(0, int(cur_sat * 0.5))

    # --- Check 6: Colour cast introduced ---
    opt_r = float(np.mean(opt[:, :, 0]))
    opt_g = float(np.mean(opt[:, :, 1]))
    opt_b = float(np.mean(opt[:, :, 2]))
    opt_avg = (opt_r + opt_g + opt_b) / 3.0
    if opt_avg > 0.01:
        r_dev = abs(opt_r - opt_avg) / opt_avg
        b_dev = abs(opt_b - opt_avg) / opt_avg
        # If strong colour cast appeared after editing, halve the WB correction
        if r_dev > 0.15 or b_dev > 0.15:
            cur_temp = refined.get("temperature", 0)
            cur_tint = refined.get("tint", 0)
            if abs(cur_temp) > 5:
                refined["temperature"] = int(cur_temp * 0.5)
            if abs(cur_tint) > 5:
                refined["tint"] = int(cur_tint * 0.5)

    # --- Check 7: Over-sharpened / noisy detail ---
    try:
        from scipy.ndimage import laplace

        orig_gray = np.mean(orig, axis=2)
        opt_gray = np.mean(opt, axis=2)
        orig_lap = float(np.var(laplace(orig_gray)))
        opt_lap = float(np.var(laplace(opt_gray)))
        # If high-freq energy increased a lot, reduce sharpening
        if orig_lap > 0 and opt_lap / max(orig_lap, 1e-8) > 2.5:
            cur_sharp = refined.get("sharp_amount", 0)
            if cur_sharp > 10:
                refined["sharp_amount"] = max(8, int(cur_sharp * 0.6))
    except ImportError:
        pass

    # --- Check 8: Dehaze made it worse (washed out highlights) ---
    cur_dehaze = refined.get("dehaze", 0)
    if cur_dehaze > 0 and opt_clip_hi > orig_clip_hi + 0.04:
        refined["dehaze"] = max(0, int(cur_dehaze * 0.4))

    return refined


# ======================================================================
# Custom widgets
# ======================================================================


class _SliderRow(QWidget):
    """A labeled slider with value display. Emits value_changed(int)."""

    value_changed = pyqtSignal()
    released = pyqtSignal()  # emitted when user finishes dragging

    def __init__(
        self,
        label: str,
        lo: int,
        hi: int,
        default: int = 0,
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._default = default
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self._label = QLabel(label)
        self._label.setFixedWidth(96)
        self._label.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px;")
        layout.addWidget(self._label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(lo, hi)
        self._slider.setValue(default)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        self._slider.setStyleSheet(_SLIDER_STYLE)
        self._slider.setFixedHeight(18)
        # Click on the groove to jump to that position (instead of pageStep)
        self._slider.mousePressEvent = self._slider_mouse_press  # type: ignore[method-assign]
        layout.addWidget(self._slider, stretch=1)

        self._val = QSpinBox()
        self._val.setRange(lo, hi)
        self._val.setValue(default)
        self._val.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val.setFixedWidth(46)
        self._val.setFixedHeight(20)
        self._val.setKeyboardTracking(False)
        self._val.setStyleSheet(
            f"QSpinBox {{ background: {_SECTION_BG}; color: {_TEXT}; "
            f"border: 1px solid {_BORDER}; border-radius: 3px; padding: 1px 4px; "
            f"font-size: 10px; font-family: 'Consolas','SF Mono',monospace; }}"
            f"QSpinBox:focus {{ border-color: {_ACCENT}; }}"
        )
        layout.addWidget(self._val)

        self._slider.valueChanged.connect(self._on_changed)
        self._slider.sliderReleased.connect(self._on_released)
        self._val.editingFinished.connect(self._on_spin_committed)

    def _slider_mouse_press(self, event: QMouseEvent) -> None:
        """Click on slider track jumps the handle to that point."""
        if event.button() == Qt.MouseButton.LeftButton:
            slider = self._slider
            lo, hi = slider.minimum(), slider.maximum()
            pos = event.position().x() if hasattr(event, "position") else event.x()
            ratio = max(0.0, min(1.0, pos / max(1, slider.width())))
            new_val = int(round(lo + ratio * (hi - lo)))
            slider.setValue(new_val)
            event.accept()
        QSlider.mousePressEvent(self._slider, event)

    def _on_changed(self, v: int) -> None:
        self._val.blockSignals(True)
        self._val.setValue(v)
        self._val.blockSignals(False)
        self.value_changed.emit()

    def _on_spin_committed(self) -> None:
        v = self._val.value()
        if v != self._slider.value():
            self._slider.blockSignals(True)
            self._slider.setValue(v)
            self._slider.blockSignals(False)
            self.value_changed.emit()
        self.released.emit()

    def _on_released(self) -> None:
        self.released.emit()

    def value(self) -> int:
        return self._slider.value()

    def set_value(self, v: int) -> None:
        self._slider.blockSignals(True)
        self._val.blockSignals(True)
        self._slider.setValue(v)
        self._val.setValue(v)
        self._slider.blockSignals(False)
        self._val.blockSignals(False)

    def reset(self) -> None:
        self.set_value(self._default)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Double-click to reset to default."""
        self.reset()
        self.value_changed.emit()
        self.released.emit()
        super().mouseDoubleClickEvent(event)


class _CollapsibleSection(QWidget):
    """A section with a clickable header that collapses/expands content."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header — refined section header with subtle left accent
        self._header = QPushButton(f"▾  {title}")
        self._header.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_TEXT}; "
            f"font-weight: 600; font-size: 10px; text-align: left; "
            f"padding: 9px 14px 9px 12px; border: none; "
            f"border-bottom: 1px solid {_BORDER}; "
            f"border-left: 2px solid {_ACCENT}; "
            f"letter-spacing: 1.2px; }}"
            f"QPushButton:hover {{ background: {_ACCENT_DIM}; }}"
        )
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.clicked.connect(self._toggle)
        self._title = title
        layout.addWidget(self._header)

        # Content container
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(14, 10, 14, 10)
        self._content_layout.setSpacing(2)
        layout.addWidget(self._content)

    def add_widget(self, w: QWidget) -> None:
        self._content_layout.addWidget(w)

    def add_layout(self, lay: QLayout) -> None:
        self._content_layout.addLayout(lay)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        arrow = "▾" if self._expanded else "▸"
        self._header.setText(f"{arrow}  {self._title}")


class _HistogramWidget(QWidget):
    """Live RGB histogram display."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(100)
        self.setMinimumWidth(180)
        self._data: np.ndarray | None = None

    def update_histogram(self, rgb: np.ndarray) -> None:
        """Compute histogram from the RGB array."""
        # Downsample for speed
        step = max(1, rgb.shape[0] * rgb.shape[1] // 50000)
        sampled = rgb[::step, ::step]
        self._data = sampled
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0a0a0a"))

        if self._data is None:
            p.setPen(QColor(_TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data")
            p.end()
            return

        w, h = self.width(), self.height()
        colors = [
            (QColor(220, 60, 60, 100), 0),
            (QColor(60, 180, 60, 100), 1),
            (QColor(60, 100, 220, 100), 2),
        ]

        max_val = 1
        hists = []
        for _, ch in colors:
            hist, _ = np.histogram(self._data[:, :, ch].ravel(), bins=128, range=(0, 255))
            hists.append(hist)
            max_val = max(max_val, hist.max())

        for (color, _), hist in zip(colors, hists, strict=False):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            bin_w = w / 128
            for i, count in enumerate(hist):
                bar_h = int(count / max_val * (h - 4))
                x = int(i * bin_w)
                p.drawRect(x, h - bar_h, max(1, int(bin_w)), bar_h)

        # Luminance overlay
        lum = (
            0.2126 * self._data[:, :, 0]
            + 0.7152 * self._data[:, :, 1]
            + 0.0722 * self._data[:, :, 2]
        ).ravel()
        hist_l, _ = np.histogram(lum, bins=128, range=(0, 255))
        p.setPen(QPen(QColor(200, 200, 200, 140), 1))
        points = []
        for i, count in enumerate(hist_l):
            x = int(i * w / 128)
            y = h - int(count / max_val * (h - 4))
            points.append(QPoint(x, y))
        for i in range(len(points) - 1):
            p.drawLine(points[i], points[i + 1])

        p.end()


class _PreviewCanvas(QWidget):
    """Custom canvas widget that handles image display, pan/drag, crop overlay."""

    crop_changed = pyqtSignal(QRect)  # emitted when crop rect finalised
    zoom_changed = pyqtSignal()  # emitted on scroll-wheel zoom
    rotation_changed = pyqtSignal(float)  # emitted during right-drag rotation
    rotation_released = pyqtSignal()  # emitted when rotation drag ends

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background: {_BG};")

        self._pixmap: QPixmap | None = None
        self._zoom: float = 0  # 0 = fit
        self._pan_offset = QPoint(0, 0)
        self._pan_start: QPoint | None = None
        self._pan_start_offset = QPoint(0, 0)

        # Crop state
        self._crop_mode = False
        self._crop_rect: QRect | None = None  # in image coordinates
        self._crop_dragging = False
        self._crop_start: QPoint | None = None
        self._crop_forced_ratio: float = 0  # 0 = free crop
        self._crop_rotate_mode = False
        self._crop_rotate_start: QPoint | None = None
        self._crop_rotation_base: float = 0.0
        self._rotation_value: float = 0.0
        self._rotate_sensitivity: float = 0.35  # degrees per pixel drag

        # Mask painting state
        self._mask_paint_mode = False
        self._mask_brush_size = 40  # pixels in image space
        self._mask_feather = 10  # feather radius in image space
        self._mask_erasing = False  # right-click erases
        self._mask_canvas: np.ndarray | None = None  # float32 H×W [0,1]
        self._mask_strokes: list = []  # list of (x, y) tuples in image coords
        self._mask_lasso_points: list = []  # for lasso mode
        self._mask_tool = "brush"  # "brush" | "lasso"

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self.update()

    def set_zoom(self, factor: float) -> None:
        self._zoom = factor
        self._pan_offset = QPoint(0, 0)
        self.update()

    def get_zoom(self) -> float:
        return self._zoom

    def set_crop_mode(self, enabled: bool) -> None:
        self._crop_mode = enabled
        if not enabled:
            self._crop_rect = None
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def get_crop_rect(self) -> QRect | None:
        return self._crop_rect

    def clear_crop(self) -> None:
        self._crop_rect = None
        self.update()

    def set_crop_rect(self, rect: QRect) -> None:
        """Set the crop rect from outside (e.g. restoring saved crop or AI suggestion)."""
        self._crop_rect = rect
        self.update()

    def set_crop_aspect_ratio(self, ratio: float) -> None:
        """Set the forced aspect ratio for crop dragging. 0 = free."""
        self._crop_forced_ratio = ratio

    def set_rotation_value(self, angle: float) -> None:
        """Keep the current rotation state in sync with the editor slider."""
        self._rotation_value = float(angle)

    # -- Mask painting API --

    mask_updated = pyqtSignal()  # emitted when mask strokes change

    def set_mask_paint_mode(self, enabled: bool, tool: str = "brush") -> None:
        """Enable/disable manual mask painting."""
        self._mask_paint_mode = enabled
        self._mask_tool = tool
        if enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def init_mask_canvas(self, h: int, w: int) -> None:
        """Create a blank mask canvas of given dimensions."""
        import numpy as np

        self._mask_canvas = np.zeros((h, w), dtype=np.float32)
        self._mask_lasso_points = []
        self._mask_strokes = []

    def get_mask_canvas(self):
        return self._mask_canvas

    def clear_mask_canvas(self) -> None:
        if self._mask_canvas is not None:
            self._mask_canvas[:] = 0
            self._mask_lasso_points = []
            self._mask_strokes = []
            self.mask_updated.emit()

    def _paint_brush_stroke(self, img_pos, erase: bool = False) -> None:
        """Paint or erase a brush stroke at the given image position."""
        import cv2

        if self._mask_canvas is None:
            return
        x, y = img_pos.x(), img_pos.y()
        h, w = self._mask_canvas.shape[:2]
        if x < 0 or y < 0 or x >= w or y >= h:
            return
        radius = max(1, self._mask_brush_size // 2)
        if erase:
            cv2.circle(self._mask_canvas, (x, y), radius, 0.0, -1)
        else:
            cv2.circle(self._mask_canvas, (x, y), radius, 1.0, -1)
        # Apply feathering via blur on modified region
        if self._mask_feather > 0:
            k = self._mask_feather * 2 + 1
            region_y1 = max(0, y - radius - self._mask_feather * 2)
            region_y2 = min(h, y + radius + self._mask_feather * 2)
            region_x1 = max(0, x - radius - self._mask_feather * 2)
            region_x2 = min(w, x + radius + self._mask_feather * 2)
            roi = self._mask_canvas[region_y1:region_y2, region_x1:region_x2]
            if roi.size > 0:
                blurred = cv2.GaussianBlur(roi, (k, k), 0)
                self._mask_canvas[region_y1:region_y2, region_x1:region_x2] = blurred

    def _commit_lasso(self) -> None:
        """Fill the lasso polygon and commit to mask canvas."""
        import cv2
        import numpy as np

        if self._mask_canvas is None or len(self._mask_lasso_points) < 3:
            self._mask_lasso_points = []
            return
        pts = np.array([(p.x(), p.y()) for p in self._mask_lasso_points], dtype=np.int32)
        if self._mask_erasing:
            cv2.fillPoly(self._mask_canvas, [pts], 0.0)
        else:
            cv2.fillPoly(self._mask_canvas, [pts], 1.0)
        if self._mask_feather > 0:
            k = self._mask_feather * 2 + 1
            self._mask_canvas = cv2.GaussianBlur(self._mask_canvas, (k, k), 0)
        self._mask_lasso_points = []
        self.mask_updated.emit()
        self.update()

    def _image_rect(self) -> QRect:
        """Calculate where the image is drawn on the widget."""
        if self._pixmap is None:
            return QRect()
        available = self.size()
        pix = self._pixmap
        if self._zoom == 0:
            scaled = pix.size().scaled(available, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            scaled = QSize(int(pix.width() * self._zoom), int(pix.height() * self._zoom))
        x = (available.width() - scaled.width()) // 2 + self._pan_offset.x()
        y = (available.height() - scaled.height()) // 2 + self._pan_offset.y()
        return QRect(x, y, scaled.width(), scaled.height())

    def _widget_to_image(self, pos: QPoint) -> QPoint:
        """Convert widget coordinates to image pixel coordinates."""
        r = self._image_rect()
        if r.width() <= 0 or r.height() <= 0 or self._pixmap is None:
            return QPoint()
        ix = int((pos.x() - r.x()) / r.width() * self._pixmap.width())
        iy = int((pos.y() - r.y()) / r.height() * self._pixmap.height())
        return QPoint(ix, iy)

    def _image_to_widget(self, pos: QPoint) -> QPoint:
        """Convert image coordinates to widget coordinates."""
        r = self._image_rect()
        if self._pixmap is None or self._pixmap.width() == 0:
            return QPoint()
        wx = int(pos.x() / self._pixmap.width() * r.width()) + r.x()
        wy = int(pos.y() / self._pixmap.height() * r.height()) + r.y()
        return QPoint(wx, wy)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(_BG))

        if self._pixmap is None:
            p.setPen(QColor(_TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No image loaded")
            p.end()
            return

        r = self._image_rect()
        p.drawPixmap(r, self._pixmap)

        # Draw crop overlay
        if self._crop_mode and self._crop_rect is not None and self._pixmap is not None:
            tl = self._image_to_widget(self._crop_rect.topLeft())
            br = self._image_to_widget(self._crop_rect.bottomRight())
            crop_w = QRect(tl, br).normalized()

            # Darken outside crop
            p.setBrush(QColor(0, 0, 0, 140))
            p.setPen(Qt.PenStyle.NoPen)
            # Top
            p.drawRect(r.x(), r.y(), r.width(), crop_w.y() - r.y())
            # Bottom
            p.drawRect(r.x(), crop_w.bottom(), r.width(), r.bottom() - crop_w.bottom())
            # Left
            p.drawRect(r.x(), crop_w.y(), crop_w.x() - r.x(), crop_w.height())
            # Right
            p.drawRect(crop_w.right(), crop_w.y(), r.right() - crop_w.right(), crop_w.height())

            # Crop border
            p.setPen(QPen(QColor(_ACCENT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(crop_w)

            # Rule of thirds
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            for i in range(1, 3):
                x = crop_w.x() + crop_w.width() * i // 3
                p.drawLine(x, crop_w.y(), x, crop_w.bottom())
                y = crop_w.y() + crop_w.height() * i // 3
                p.drawLine(crop_w.x(), y, crop_w.right(), y)

        # Draw mask overlay
        if self._mask_paint_mode and self._mask_canvas is not None:
            mc = self._mask_canvas
            if mc.max() > 0:
                h, w = mc.shape[:2]
                rgba = np.zeros((h, w, 4), dtype=np.uint8)
                rgba[..., 0] = 255  # Red channel
                rgba[..., 3] = (mc * 120).clip(0, 255).astype(np.uint8)  # Alpha from mask
                # Ensure the QImage data remains valid while constructing the pixmap.
                buf = bytes(rgba)
                mask_img = QImage(buf, w, h, w * 4, QImage.Format.Format_RGBA8888)
                if not mask_img.isNull():
                    mask_pix = QPixmap.fromImage(mask_img.copy())
                    p.drawPixmap(r, mask_pix)
                else:
                    logger.warning("Mask overlay QImage creation failed")
            # Draw lasso path in progress
            if self._mask_lasso_points and len(self._mask_lasso_points) > 1:
                p.setPen(QPen(QColor(255, 255, 0, 180), 2, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                for i in range(1, len(self._mask_lasso_points)):
                    w1 = self._image_to_widget(self._mask_lasso_points[i - 1])
                    w2 = self._image_to_widget(self._mask_lasso_points[i])
                    p.drawLine(w1, w2)
            # Draw brush cursor
            if self._mask_tool == "brush" and self.underMouse():
                cursor_pos = self.mapFromGlobal(QCursor.pos())
                brush_widget_radius = max(
                    2,
                    int(
                        self._mask_brush_size
                        * r.width()
                        / (self._pixmap.width() if self._pixmap else 1)
                    ),
                )
                p.setPen(QPen(QColor(255, 255, 255, 160), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(cursor_pos, brush_widget_radius, brush_widget_radius)

        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._mask_paint_mode:
                img_pos = self._widget_to_image(event.pos())
                if self._mask_tool == "brush":
                    self._paint_brush_stroke(img_pos, erase=False)
                    self.update()
                elif self._mask_tool == "lasso":
                    self._mask_lasso_points = [img_pos]
                return
            if self._crop_mode:
                self._crop_dragging = True
                self._crop_start = self._widget_to_image(event.pos())
                self._crop_rect = None
                self.update()
            elif self._zoom != 0:  # Pan mode when zoomed in
                self._pan_start = event.pos()
                self._pan_start_offset = QPoint(self._pan_offset)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.RightButton:
            if self._crop_mode and not self._mask_paint_mode:
                self._crop_rotate_mode = True
                self._crop_rotate_start = event.pos()
                self._crop_rotation_base = self._rotation_value
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return
            if self._mask_paint_mode and self._mask_tool == "brush":
                img_pos = self._widget_to_image(event.pos())
                self._mask_erasing = True
                self._paint_brush_stroke(img_pos, erase=True)
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._mask_paint_mode:
            img_pos = self._widget_to_image(event.pos())
            if event.buttons() & Qt.MouseButton.LeftButton:
                if self._mask_tool == "brush":
                    self._paint_brush_stroke(img_pos, erase=False)
                    self.update()
                elif self._mask_tool == "lasso":
                    self._mask_lasso_points.append(img_pos)
                    self.update()
            elif event.buttons() & Qt.MouseButton.RightButton:
                if self._mask_tool == "brush":
                    self._paint_brush_stroke(img_pos, erase=True)
                    self.update()
            return
        if self._crop_rotate_mode and self._crop_rotate_start is not None:
            dx = event.pos().x() - self._crop_rotate_start.x()
            new_angle = self._crop_rotation_base + dx * self._rotate_sensitivity
            new_angle = max(-45.0, min(45.0, new_angle))
            self._rotation_value = new_angle
            self.rotation_changed.emit(new_angle)
            return
        if self._crop_dragging and self._crop_start is not None:
            current = self._widget_to_image(event.pos())
            self._crop_rect = QRect(self._crop_start, current).normalized()
            # Enforce aspect ratio if set
            if self._crop_forced_ratio > 0 and self._crop_rect.height() > 0:
                target_r = self._crop_forced_ratio
                cw, ch = self._crop_rect.width(), self._crop_rect.height()
                current_r = cw / ch if ch > 0 else 1.0
                if current_r > target_r:
                    new_w = int(ch * target_r)
                    self._crop_rect.setWidth(new_w)
                else:
                    new_h = int(cw / target_r)
                    self._crop_rect.setHeight(new_h)
            # Clamp to image bounds
            if self._pixmap:
                img_rect = QRect(0, 0, self._pixmap.width(), self._pixmap.height())
                self._crop_rect = self._crop_rect.intersected(img_rect)
            self.update()
        elif self._pan_start is not None:
            delta = event.pos() - self._pan_start
            self._pan_offset = self._pan_start_offset + delta
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._mask_paint_mode:
                if self._mask_tool == "brush":
                    self.mask_updated.emit()
                elif self._mask_tool == "lasso":
                    self._commit_lasso()
                return
            if self._crop_dragging:
                self._crop_dragging = False
                if self._crop_rect and self._crop_rect.width() > 5 and self._crop_rect.height() > 5:
                    self.crop_changed.emit(self._crop_rect)
                self.update()
            elif self._pan_start is not None:
                self._pan_start = None
                if self._crop_mode:
                    self.setCursor(Qt.CursorShape.CrossCursor)
                else:
                    self.setCursor(
                        Qt.CursorShape.OpenHandCursor
                        if self._zoom != 0
                        else Qt.CursorShape.ArrowCursor
                    )
        elif event.button() == Qt.MouseButton.RightButton:
            if self._crop_rotate_mode:
                self._crop_rotate_mode = False
                self._crop_rotate_start = None
                self.rotation_released.emit()
                self.setCursor(Qt.CursorShape.CrossCursor if self._crop_mode else Qt.CursorShape.ArrowCursor)
                return
            if self._mask_paint_mode and self._mask_erasing:
                self._mask_erasing = False
                self.mask_updated.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        # When in fit mode (_zoom == 0), start zooming from 1.0 (100%)
        base = self._zoom if self._zoom > 0 else 1.0
        if delta > 0:
            new_zoom = min(4.0, base * 1.15)
        else:
            new_zoom = base / 1.15
            if new_zoom < 0.15:
                # Snap back to fit mode and reset pan offset
                new_zoom = 0
                self._pan_offset = QPoint(0, 0)
        self._zoom = new_zoom
        self.update()
        self.zoom_changed.emit()

    def get_zoom_text(self) -> str:
        if self._zoom == 0:
            return "Fit"
        return f"{int(self._zoom * 100)}%"


class _FilmStripItem(QWidget):
    """Single thumbnail in the bottom film strip."""

    clicked = pyqtSignal(int)

    def __init__(self, index: int, file_name: str, size: int = 64, parent=None):
        super().__init__(parent)
        self._index = index
        self._size = size
        self._selected = False
        self.setFixedSize(size + 6, size + 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 0)
        layout.setSpacing(2)

        self._img_label = QLabel()
        self._img_label.setFixedSize(size, size)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet(
            f"background: {_PANEL_BG}; border: 1px solid {_BORDER}; border-radius: 4px;"
        )
        layout.addWidget(self._img_label)

        short = file_name[:8] + "…" if len(file_name) > 9 else file_name
        lbl = QLabel(short)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 9px;")
        lbl.setMaximumWidth(size)
        layout.addWidget(lbl)

    def set_pixmap(self, pix: QPixmap) -> None:
        self._img_label.setPixmap(pix)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        border = _ACCENT if selected else _BORDER
        width = 2 if selected else 1
        self._img_label.setStyleSheet(
            f"background: {_PANEL_BG}; border: {width}px solid {border}; border-radius: 4px;"
        )

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._index)


class _FilmStripScrollArea(QScrollArea):
    """Scroll area that converts vertical wheel events to horizontal scrolling."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta != 0:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            event.accept()
        else:
            super().wheelEvent(event)


class _FilmStrip(QWidget):
    """Horizontal scrollable film strip for photo navigation."""

    photo_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumb_size = 64
        self._item_height = self._thumb_size + 20
        self.setFixedHeight(self._item_height + 10)
        self.setStyleSheet(f"background: {_BG}; border-top: 1px solid {_BORDER};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(0)

        self._scroll = _FilmStripScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:horizontal { height: 4px; background: transparent; }"
            "QScrollBar::handle:horizontal { background: rgba(100, 100, 100, 0.3); border-radius: 2px; min-width: 30px; }"
            "QScrollBar::handle:horizontal:hover { background: rgba(100, 100, 100, 0.6); }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
        )
        layout.addWidget(self._scroll)

        self._container = QWidget()
        self._container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._container.setFixedHeight(self._item_height)
        self._strip_layout = QHBoxLayout(self._container)
        self._strip_layout.setContentsMargins(0, 0, 0, 0)
        self._strip_layout.setSpacing(3)
        self._strip_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._scroll.setWidget(self._container)

        self._items: list[_FilmStripItem] = []
        self._current = -1
        self._thumb_worker: _ThumbDecodeWorker | None = None

    def set_photos(self, photos: list[dict]) -> None:
        """Populate the strip with photo list."""
        # Clear
        for item in self._items:
            self._strip_layout.removeWidget(item)
            item.deleteLater()
        self._items.clear()

        thumb_tasks: list[tuple[int, str]] = []
        for i, p in enumerate(photos):
            item = _FilmStripItem(i, p.get("file_name", ""))
            item.clicked.connect(self._on_item_clicked)
            self._strip_layout.addWidget(item)
            self._items.append(item)

            thumb = p.get("thumbnail_path", "")
            if thumb and Path(thumb).is_file():
                thumb_tasks.append((i, thumb))

        # Load thumbnails in background
        if thumb_tasks:
            self._thumb_worker = _ThumbDecodeWorker(thumb_tasks, size=64, parent=self)
            self._thumb_worker.thumb_ready.connect(self._on_thumb)
            self._thumb_worker.start()

    def _on_thumb(self, idx: int, pix: QPixmap) -> None:
        if 0 <= idx < len(self._items):
            self._items[idx].set_pixmap(pix)

    def set_current(self, index: int) -> None:
        if 0 <= self._current < len(self._items):
            self._items[self._current].set_selected(False)
        self._current = index
        if 0 <= index < len(self._items):
            self._items[index].set_selected(True)
            self._scroll.ensureWidgetVisible(self._items[index])

    def _on_item_clicked(self, index: int) -> None:
        self.set_current(index)
        self.photo_selected.emit(index)


# ======================================================================
# Main Photo Editor Dialog
# ======================================================================


class PhotoEditorWidget(QWidget):
    """Professional Lightroom-style photo editor with live preview.

    Can be embedded in a parent layout or used standalone.
    Emits ``edit_applied(photo_id, overrides_dict)`` when the user
    applies edits and requests re-export.
    """

    edit_applied = pyqtSignal(int, dict)  # photo_id, overrides
    batch_export_all = pyqtSignal(list, str, int)  # [(pid, overrides), ...], format, quality
    edits_saved = pyqtSignal(list)  # list of (photo_id, overrides_dict)
    photo_trashed = pyqtSignal(int)  # photo_id
    closed = pyqtSignal()

    def __init__(
        self,
        photo_list: list[dict] | None = None,
        current_index: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {_BG};")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._photos = photo_list or []
        self._index = current_index
        self._raw_rgb: np.ndarray | None = None  # full-res decoded RAW
        self._raw_rgb_preview: np.ndarray | None = None  # downscaled for fast preview
        self._preview_pix: QPixmap | None = None
        self._show_before = False
        self._decode_worker: _RawDecodeWorker | None = None
        self._rgb_cache: dict[int, np.ndarray] = {}
        self._RGB_CACHE_LIMIT = 5  # Max decoded RAW arrays in memory
        self._prefetch_worker: _RawDecodeWorker | None = None

        self._crop_rect: QRect | None = None

        # Crop aspect ratio setting
        self._crop_aspect_ratio: str = "Free"

        # Undo / Redo stacks
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._last_committed_params: dict | None = None

        # Clipboard for copy/paste edits
        self._clipboard_params: dict | None = None

        # Presets directory
        self._presets_dir = Path.home() / ".imagic" / "presets"
        self._presets_dir.mkdir(parents=True, exist_ok=True)

        # Debounce timer for preview updates (fast for live dragging)
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(60)
        self._preview_timer.timeout.connect(self._update_preview)

        # Flag: user requested AI optimize while RAW was still decoding
        self._pending_ai_optimize = False
        self._batch_worker: _BatchOptimizeWorker | None = None

        # Track how many times each AI tool has been run per photo so
        # re-runs produce varied results.  Key = (photo_index, tool_name).
        self._ai_run_counter: dict[tuple, int] = {}
        self._batch_optimize_run: int = 0

        # Active AI mask for selective editing
        self._active_mask: np.ndarray | None = None
        self._active_mask_type = None

        # Mask segment tab state
        self._mask_global_params: dict | None = None   # global slider snapshot
        self._mask_area_params: dict = {}              # masked-area slider state
        self._mask_active_tab: int = 0                 # 0=Global, 1=Masked Area
        self._mask_tab_bar: QTabBar | None = None

        # Currently running AI task worker (prevent GC + allow cancellation)
        self._ai_worker: _AITaskWorker | None = None

        # AI loading overlay (created after _build_ui so it sits on top)
        self._ai_modal: AILoadingModal | None = None

        self._build_ui()

        # Lazy-create the modal so it overlays the full widget
        self._ai_modal = AILoadingModal(parent=self)
        self._ai_modal.cancelled.connect(self._abort_ai_task)
        if self._photos:
            self._load_photo(self._index)

    def set_photos(self, photo_list: list[dict], current_index: int = 0) -> None:
        """Update the photo list (used when embedding in single-window workflow)."""
        self._photos = photo_list
        self._rgb_cache.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._last_committed_params = None
        self._ai_run_counter.clear()
        self._filmstrip.set_photos(photo_list)
        if photo_list:
            self._load_photo(current_index)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._ai_modal and self._ai_modal.isVisible():
            self._ai_modal.setGeometry(self.rect())

    def _stop_ai_worker(self) -> None:
        """Safely stop any running AI worker thread."""
        if self._ai_worker is not None and self._ai_worker.isRunning():
            self._ai_worker.requestInterruption()
            self._ai_worker.quit()
            self._ai_worker.wait(3000)
            if self._ai_worker.isRunning():
                self._ai_worker.terminate()
                self._ai_worker.wait(2000)
        self._ai_worker = None

    def _abort_ai_task(self) -> None:
        """Abort a running AI task when the loading modal is cancelled."""
        was_running = self._ai_worker is not None and self._ai_worker.isRunning()
        if self._ai_modal:
            self._ai_modal.hide_modal()
        self._stop_ai_worker()
        if was_running:
            QMessageBox.information(self, "AI Masking", "AI masking was cancelled.")

    def closeEvent(self, event) -> None:
        """Clean up threads before closing."""
        self._stop_ai_worker()
        if self._decode_worker and self._decode_worker.isRunning():
            self._decode_worker.quit()
            self._decode_worker.wait(2000)
        if self._prefetch_worker and self._prefetch_worker.isRunning():
            self._prefetch_worker.quit()
            self._prefetch_worker.wait(1000)
        if self._batch_worker and self._batch_worker.isRunning():
            self._batch_worker.quit()
            self._batch_worker.wait(2000)

        batch = self._collect_manual_edit_batch()
        if batch:
            self.edits_saved.emit(batch)

        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Top toolbar ---
        self._toolbar = self._build_toolbar()
        root.addWidget(self._toolbar)

        # --- Main area: left info | center preview | right panels ---
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {_BORDER}; width: 1px; }}"
            f"QSplitter::handle:hover {{ background: rgba(232, 149, 48, 0.4); }}"
        )

        # Left sidebar: histogram
        left = QWidget()
        left.setFixedWidth(220)
        left.setStyleSheet(f"background: {_PANEL_BG}; border-right: 1px solid {_BORDER};")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 12, 10, 12)
        left_layout.setSpacing(8)

        hist_label = QLabel("HISTOGRAM")
        hist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hist_label.setStyleSheet(
            f"color: {_TEXT_DIM}; font-weight: 600; font-size: 9px; letter-spacing: 1.5px;"
        )
        left_layout.addWidget(hist_label)

        self._histogram = _HistogramWidget()
        left_layout.addWidget(self._histogram)

        # Info panel
        self._info_label = QLabel()
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px;")
        left_layout.addWidget(self._info_label)

        left_layout.addStretch()

        # Zoom controls
        zoom_lbl = QLabel("ZOOM")
        zoom_lbl.setStyleSheet(
            f"color: {_TEXT_DIM}; font-weight: 600; font-size: 9px; letter-spacing: 1.5px;"
        )
        zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(zoom_lbl)

        zoom_row = QHBoxLayout()
        for label, factor in [("Fit", 0), ("50%", 0.5), ("100%", 1.0), ("200%", 2.0)]:
            btn = QPushButton(label)
            btn.setFixedHeight(24)
            btn.setStyleSheet(
                f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
                f"border: 1px solid {_BORDER}; border-radius: 4px; font-size: 10px; padding: 2px 6px; }}"
                f"QPushButton:hover {{ background: {_BTN_HOVER}; color: {_TEXT}; }}"
            )
            btn.clicked.connect(lambda _, z=factor: self._set_zoom(z))
            zoom_row.addWidget(btn)
        left_layout.addLayout(zoom_row)

        self._zoom_label = QLabel("Fit")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px;")
        left_layout.addWidget(self._zoom_label)

        left_layout.addSpacing(12)

        # Keyboard shortcuts
        shortcuts_lbl = QLabel("SHORTCUTS")
        shortcuts_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shortcuts_lbl.setStyleSheet(
            f"color: {_TEXT_DIM}; font-weight: 600; font-size: 9px; letter-spacing: 1.5px;"
        )
        left_layout.addWidget(shortcuts_lbl)

        shortcuts_text = QLabel(
            "← →  Navigate photos\n"
            "\\     Before / After\n"
            "F     Fit to screen\n"
            "1     100% zoom\n"
            "2     200% zoom\n"
            "C     Toggle crop\n"
            "Right-drag Rotate while cropping\n"
            "Enter Apply crop\n"
            "Del   Trash photo\n"
            "Ctrl+Z/Y  Undo/Redo\n"
            "Ctrl+C/V  Copy/Paste\n"
            "Ctrl+S  Save all edits\n"
            "Scroll  Zoom in/out\n"
            "Esc   Close editor"
        )
        shortcuts_text.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 10px; line-height: 1.6;")
        left_layout.addWidget(shortcuts_text)

        main_splitter.addWidget(left)

        # Center: image preview canvas with pan/drag and crop support
        self._canvas = _PreviewCanvas()
        self._canvas.crop_changed.connect(self._on_crop_changed)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.rotation_changed.connect(self._on_rotate_changed)
        self._canvas.rotation_released.connect(self._commit_undo_state)
        main_splitter.addWidget(self._canvas)

        # Right sidebar: editing panels (scrollable)
        right = QWidget()
        right.setFixedWidth(320)
        right.setStyleSheet(f"background: {_PANEL_BG}; border-left: 1px solid {_BORDER};")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { width: 5px; background: transparent; margin: 2px 0; }"
            "QScrollBar::handle:vertical { background: rgba(100, 100, 100, 0.3); border-radius: 2px; min-height: 40px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(100, 100, 100, 0.6); }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        )

        panels_widget = QWidget()
        self._panels_layout = QVBoxLayout(panels_widget)
        self._panels_layout.setContentsMargins(0, 0, 0, 0)
        self._panels_layout.setSpacing(0)

        self._sliders: dict[str, _SliderRow] = {}
        self._build_panels()

        self._panels_layout.addStretch()
        scroll.setWidget(panels_widget)

        # Mask segment tab bar — hidden until a mask is set
        self._mask_tab_bar = QTabBar()
        self._mask_tab_bar.addTab("Global")
        self._mask_tab_bar.addTab("✦ Masked Area")
        self._mask_tab_bar.setStyleSheet(
            f"QTabBar::tab {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
            f"padding: 7px 16px; border: 1px solid {_BORDER}; "
            f"border-bottom: none; font-size: 10px; }}"
            f"QTabBar::tab:selected {{ background: {_ACCENT}; color: #111; font-weight: 600; }}"
            f"QTabBar::tab:hover:!selected {{ background: {_BTN_HOVER}; color: {_TEXT}; }}"
        )
        self._mask_tab_bar.hide()
        self._mask_tab_bar.currentChanged.connect(self._on_mask_tab_changed)
        right_layout.addWidget(self._mask_tab_bar)

        right_layout.addWidget(scroll)

        main_splitter.addWidget(right)
        main_splitter.setSizes([220, 700, 320])

        root.addWidget(main_splitter, stretch=1)

        # --- Bottom: film strip ---
        self._filmstrip = _FilmStrip()
        self._filmstrip.photo_selected.connect(self._on_filmstrip_nav)
        self._filmstrip.set_photos(self._photos)
        root.addWidget(self._filmstrip)

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setFixedHeight(44)
        toolbar.setStyleSheet(f"background: {_PANEL_BG}; border-bottom: 1px solid {_BORDER};")
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        # Nav buttons
        btn_style = (
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 4px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_BTN_HOVER}; color: {_TEXT}; border-color: {_BTN_BORDER}; }}"
        )

        self._prev_btn = QPushButton("◀ Prev")
        self._prev_btn.setStyleSheet(btn_style)
        self._prev_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._prev_btn.clicked.connect(lambda: self._navigate(-1))
        layout.addWidget(self._prev_btn)

        self._title_label = QLabel()
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setStyleSheet(f"color: {_TEXT}; font-size: 11px; font-weight: 600;")
        layout.addWidget(self._title_label, stretch=1)

        self._next_btn = QPushButton("Next ▶")
        self._next_btn.setStyleSheet(btn_style)
        self._next_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._next_btn.clicked.connect(lambda: self._navigate(1))
        layout.addWidget(self._next_btn)

        layout.addSpacing(12)

        # AI Optimize-All button
        self._optimize_btn = QPushButton("⚡ AI Optimize All")
        self._optimize_btn.setStyleSheet(
            f"QPushButton {{ background: {_GREEN}; color: #fff; font-weight: 600; "
            f"border: none; border-radius: 5px; padding: 4px 14px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_GREEN_HOVER}; }}"
            f"QPushButton:pressed {{ background: {_GREEN_PRESSED}; }}"
        )
        self._optimize_btn.setToolTip("One-click AI: auto-enhance, white balance, denoise, sharpen")
        self._optimize_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._optimize_btn.clicked.connect(self._ai_optimize_all)
        layout.addWidget(self._optimize_btn)

        # Crop tool toggle
        self._crop_btn = QPushButton("✂ Crop")
        self._crop_btn.setCheckable(True)
        self._crop_btn.setStyleSheet(
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 4px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_BTN_HOVER}; color: {_TEXT}; }}"
            f"QPushButton:checked {{ background: {_ACCENT}; color: #111; font-weight: 600; border-color: {_ACCENT}; }}"
        )
        self._crop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._crop_btn.toggled.connect(self._toggle_crop_mode)
        layout.addWidget(self._crop_btn)

        # Apply Crop button (hidden until crop mode enabled)
        self._apply_crop_btn = QPushButton("✓ Apply Crop")
        self._apply_crop_btn.setStyleSheet(
            f"QPushButton {{ background: {_GREEN}; color: #fff; font-weight: 600; "
            f"border: none; border-radius: 5px; padding: 4px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_GREEN_HOVER}; }}"
        )
        self._apply_crop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_crop_btn.clicked.connect(self._apply_crop)
        self._apply_crop_btn.setVisible(False)
        layout.addWidget(self._apply_crop_btn)

        # AI Suggest Crop button (hidden until crop mode enabled)
        self._ai_crop_btn = QPushButton("🤖 AI Crop")
        self._ai_crop_btn.setStyleSheet(
            "QPushButton { background: rgba(50, 40, 30, 0.6); color: #e8a050; font-weight: 600; "
            "border: 1px solid rgba(232, 149, 48, 0.3); border-radius: 5px; padding: 4px 12px; font-size: 10px; }"
            "QPushButton:hover { background: rgba(60, 48, 30, 0.8); }"
        )
        self._ai_crop_btn.setToolTip("Let AI suggest the best crop for this photo")
        self._ai_crop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._ai_crop_btn.clicked.connect(self._ai_suggest_crop)
        self._ai_crop_btn.setVisible(False)
        layout.addWidget(self._ai_crop_btn)

        # Aspect ratio combo (hidden until crop mode enabled)
        self._crop_ratio_combo = QComboBox()
        self._crop_ratio_combo.addItems(
            ["Free", "Original", "1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"]
        )
        self._crop_ratio_combo.setStyleSheet(
            f"QComboBox {{ background: {_BTN_BG}; color: {_TEXT}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 3px 8px; font-size: 10px; }}"
        )
        self._crop_ratio_combo.setToolTip("Lock crop to a specific aspect ratio")
        self._crop_ratio_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._crop_ratio_combo.setVisible(False)
        self._crop_ratio_combo.currentTextChanged.connect(self._on_crop_ratio_changed)
        layout.addWidget(self._crop_ratio_combo)

        # Clear Crop button (hidden until crop mode enabled)
        self._clear_crop_btn = QPushButton("✕ Clear")
        self._clear_crop_btn.setStyleSheet(
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_MUTED}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 4px 8px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_BTN_HOVER}; color: {_TEXT}; }}"
        )
        self._clear_crop_btn.setToolTip("Clear crop and show full image")
        self._clear_crop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_crop_btn.clicked.connect(self._clear_crop)
        self._clear_crop_btn.setVisible(False)
        layout.addWidget(self._clear_crop_btn)

        # Before/After toggle
        self._ba_btn = QPushButton("Before / After  [\\]")
        self._ba_btn.setStyleSheet(
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 4px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_BTN_HOVER}; color: {_TEXT}; }}"
        )
        self._ba_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._ba_btn.clicked.connect(self._toggle_before_after)
        layout.addWidget(self._ba_btn)

        # Copy edits
        copy_btn = QPushButton("📋 Copy")
        copy_btn.setStyleSheet(btn_style)
        copy_btn.setToolTip("Copy current edits (Ctrl+C)")
        copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        copy_btn.clicked.connect(self._copy_edits)
        layout.addWidget(copy_btn)

        # Paste edits
        paste_btn = QPushButton("📌 Paste")
        paste_btn.setStyleSheet(btn_style)
        paste_btn.setToolTip("Paste copied edits (Ctrl+V)")
        paste_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        paste_btn.clicked.connect(self._paste_edits)
        layout.addWidget(paste_btn)

        # Presets menu button
        self._preset_btn = QPushButton("💾 Presets ▾")
        self._preset_btn.setStyleSheet(btn_style)
        self._preset_btn.setToolTip("Save or load edit presets")
        self._preset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._preset_btn.clicked.connect(self._show_preset_menu)
        layout.addWidget(self._preset_btn)

        # Reset all
        reset_btn = QPushButton("Reset All")
        reset_btn.setStyleSheet(
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_MUTED}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 4px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_BTN_HOVER}; color: {_TEXT}; }}"
        )
        reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reset_btn.clicked.connect(self._reset_all)
        layout.addWidget(reset_btn)

        # Expert mode toggle
        self._expert_btn = QPushButton("🔧 Expert")
        self._expert_btn.setCheckable(True)
        self._expert_btn.setStyleSheet(
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_MUTED}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 4px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_BTN_HOVER}; color: {_TEXT}; }}"
            f"QPushButton:checked {{ background: rgba(232, 149, 48, 0.15); color: #f5ad4a; "
            f"font-weight: 600; border-color: rgba(232, 149, 48, 0.35); }}"
        )
        self._expert_btn.setToolTip("Toggle expert editing panels (advanced RawTherapee controls)")
        self._expert_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._expert_btn.toggled.connect(self._toggle_expert_mode)
        layout.addWidget(self._expert_btn)

        layout.addSpacing(12)

        # Save All Edits (persist to DB without exporting)
        self._save_btn = QPushButton("💾 Save All Edits")
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {_GREEN}; color: #fff; font-weight: 600; "
            f"border: none; border-radius: 5px; padding: 5px 14px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_GREEN_HOVER}; }}"
            f"QPushButton:pressed {{ background: {_GREEN_PRESSED}; }}"
            f"QPushButton:disabled {{ background: #222; color: #555; }}"
        )
        self._save_btn.setToolTip("Save all edits to database (Ctrl+S)")
        self._save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._save_btn.clicked.connect(self._save_all_edits)
        layout.addWidget(self._save_btn)

        # Apply & Export
        self._apply_btn = QPushButton("  Apply && Export  ")
        self._apply_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #111; font-weight: 600; "
            f"border: none; border-radius: 5px; padding: 5px 18px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {_ACCENT_HOVER}; }}"
            f"QPushButton:pressed {{ background: #d07a18; }}"
            f"QPushButton:disabled {{ background: #222; color: #555; }}"
        )
        self._apply_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self._apply_btn)

        return toolbar

    def _build_panels(self) -> None:
        """Build all editing panels."""

        # ═══════════ COLOR GRADE ═══════════
        sec = _CollapsibleSection("COLOR GRADE")

        # Keep a hidden combo for compat with gather/load; driven by the grid
        self._grade_combo = QComboBox()
        self._grade_combo.addItems(list(_GRADE_LUT.keys()))
        self._grade_combo.hide()

        # Thumbnail preview grid (populated when a photo loads)
        self._grade_grid_scroll = QScrollArea()
        self._grade_grid_scroll.setWidgetResizable(True)
        self._grade_grid_scroll.setFixedHeight(220)
        self._grade_grid_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self._grade_grid_widget = QWidget()
        self._grade_grid_layout = QGridLayout(self._grade_grid_widget)
        self._grade_grid_layout.setContentsMargins(2, 2, 2, 2)
        self._grade_grid_layout.setSpacing(4)
        self._grade_grid_scroll.setWidget(self._grade_grid_widget)
        self._grade_thumb_btns: list[QPushButton] = []
        self._grade_names: list[str] = list(_GRADE_LUT.keys())
        sec.add_widget(self._grade_grid_scroll)

        # Intensity slider
        self._grade_intensity = _SliderRow("Intensity", 0, 100, 100)
        self._grade_intensity.value_changed.connect(self._schedule_preview)
        self._grade_intensity.released.connect(self._commit_undo_state)
        sec.add_widget(self._grade_intensity)

        # Apply buttons row
        btn_row = QHBoxLayout()
        btn_style_sm = (
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
            f"border: 1px solid {_BORDER}; border-radius: 4px; padding: 4px 10px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_BTN_HOVER}; color: {_TEXT}; }}"
        )
        self._grade_apply_one = QPushButton("Apply to This Photo")
        self._grade_apply_one.setStyleSheet(btn_style_sm)
        self._grade_apply_one.setToolTip("Save current color grade for this photo only")
        self._grade_apply_one.clicked.connect(self._grade_save_one)
        self._grade_apply_one.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_row.addWidget(self._grade_apply_one)
        self._grade_apply_all = QPushButton("Apply to All Photos")
        self._grade_apply_all.setStyleSheet(
            f"QPushButton {{ background: {_GREEN}; color: #fff; "
            f"border: none; border-radius: 4px; padding: 4px 10px; font-size: 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {_GREEN_HOVER}; }}"
        )
        self._grade_apply_all.setToolTip(
            "Apply current color grade + intensity to all photos in batch"
        )
        self._grade_apply_all.clicked.connect(self._grade_save_all)
        self._grade_apply_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_row.addWidget(self._grade_apply_all)
        sec.add_layout(btn_row)
        self._panels_layout.addWidget(sec)

        # ═══════════ BASIC ═══════════
        sec = _CollapsibleSection("BASIC")
        basic_sliders = [
            ("temperature", "Temperature", -100, 100, 0),
            ("tint", "Tint", -100, 100, 0),
            ("exposure", "Exposure", -100, 100, 0),
            ("contrast", "Contrast", -100, 100, 0),
            ("highlights", "Highlights", -100, 100, 0),
            ("shadows", "Shadows", -100, 100, 0),
            ("whites", "Whites", -100, 100, 0),
            ("blacks", "Blacks", -100, 100, 0),
        ]
        for key, label, lo, hi, default in basic_sliders:
            s = _SliderRow(label, lo, hi, default)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        self._panels_layout.addWidget(sec)

        # ═══════════ TONE / PRESENCE ═══════════
        sec = _CollapsibleSection("PRESENCE")
        presence_sliders = [
            ("texture", "Texture", -100, 100, 0),
            ("clarity", "Clarity", -100, 100, 0),
            ("dehaze", "Dehaze", -100, 100, 0),
            ("vibrance", "Vibrance", -100, 100, 0),
            ("saturation", "Saturation", -100, 100, 0),
        ]
        for key, label, lo, hi, default in presence_sliders:
            s = _SliderRow(label, lo, hi, default)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        self._panels_layout.addWidget(sec)

        # ═══════════ HSL / COLOR ═══════════
        sec = _CollapsibleSection("HSL / COLOR")
        sec._toggle()  # start collapsed — lots of sliders
        channels = ["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
        # Hue sub-group
        hue_lbl = QLabel("Hue")
        hue_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px; font-weight: 600;")
        sec.add_widget(hue_lbl)
        for ch in channels:
            key = f"hsl_hue_{ch}"
            s = _SliderRow(ch.capitalize(), -100, 100, 0)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        # Saturation sub-group
        sat_lbl = QLabel("Saturation")
        sat_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px; font-weight: 600;")
        sec.add_widget(sat_lbl)
        for ch in channels:
            key = f"hsl_sat_{ch}"
            s = _SliderRow(ch.capitalize(), -100, 100, 0)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        # Luminance sub-group
        lum_lbl = QLabel("Luminance")
        lum_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px; font-weight: 600;")
        sec.add_widget(lum_lbl)
        for ch in channels:
            key = f"hsl_lum_{ch}"
            s = _SliderRow(ch.capitalize(), -100, 100, 0)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        self._panels_layout.addWidget(sec)

        # ═══════════ DETAIL ═══════════
        sec = _CollapsibleSection("DETAIL")
        detail_sliders = [
            ("sharp_amount", "Sharpen Amount", 0, 150, 0),
            ("sharp_radius", "Sharpen Radius", 10, 200, 50),
            ("nr_luminance", "NR Luminance", 0, 100, 0),
            ("nr_color", "NR Color", 0, 100, 0),
        ]
        for key, label, lo, hi, default in detail_sliders:
            s = _SliderRow(label, lo, hi, default)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        self._panels_layout.addWidget(sec)

        # ═══════════ COLOR GRADING (Split Toning) ═══════════
        sec = _CollapsibleSection("COLOR GRADING")
        sec._toggle()  # start collapsed
        split_sliders = [
            ("split_shadow_hue", "Shadow Hue", 0, 360, 0),
            ("split_shadow_sat", "Shadow Sat", 0, 100, 0),
            ("split_highlight_hue", "Highlight Hue", 0, 360, 0),
            ("split_highlight_sat", "Highlight Sat", 0, 100, 0),
            ("split_balance", "Balance", -100, 100, 0),
        ]
        for key, label, lo, hi, default in split_sliders:
            s = _SliderRow(label, lo, hi, default)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        self._panels_layout.addWidget(sec)

        # ═══════════ EFFECTS ═══════════
        sec = _CollapsibleSection("EFFECTS")
        effects_sliders = [
            ("vignette_amount", "Vignette", -100, 100, 0),
            ("vignette_midpoint", "Vig. Midpoint", 10, 90, 50),
            ("grain_amount", "Grain Amount", 0, 100, 0),
        ]
        for key, label, lo, hi, default in effects_sliders:
            s = _SliderRow(label, lo, hi, default)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        self._panels_layout.addWidget(sec)

        # ═══════════ TONE CURVE ═══════════
        sec = _CollapsibleSection("TONE CURVE")
        sec._toggle()  # start collapsed
        self._tone_curve_widget = ToneCurveWidget()
        self._tone_curve_widget.curve_changed.connect(self._schedule_preview)
        self._tone_curve_widget.curve_changed.connect(self._commit_undo_state)
        sec.add_widget(self._tone_curve_widget)

        curve_info = QLabel(
            "Click to add • Drag to move • Right-click to remove • Double-click to reset"
        )
        curve_info.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 9px;")
        curve_info.setWordWrap(True)
        sec.add_widget(curve_info)

        # Channel selector for tone curve
        ch_row = QHBoxLayout()
        ch_lbl = QLabel("Channel:")
        ch_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px;")
        ch_row.addWidget(ch_lbl)
        self._curve_channel = QComboBox()
        self._curve_channel.addItems(["Luminance", "Red", "Green", "Blue"])
        self._curve_channel.setFixedHeight(24)
        self._curve_channel.setStyleSheet(
            f"QComboBox {{ background: {_BTN_BG}; color: {_TEXT}; border: 1px solid {_BORDER}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 10px; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {_SECTION_BG}; color: {_TEXT}; "
            f"selection-background-color: {_ACCENT}; }}"
        )
        self._curve_channel.currentIndexChanged.connect(self._on_curve_channel_changed)
        ch_row.addWidget(self._curve_channel)
        ch_row.addStretch()
        sec.add_layout(ch_row)

        self._panels_layout.addWidget(sec)

        # Store per-channel curves: {channel_name: [(x,y), ...]}
        self._curve_data = {
            "Luminance": [(0.0, 0.0), (1.0, 1.0)],
            "Red": [(0.0, 0.0), (1.0, 1.0)],
            "Green": [(0.0, 0.0), (1.0, 1.0)],
            "Blue": [(0.0, 0.0), (1.0, 1.0)],
        }
        self._current_curve_channel = "Luminance"

        # ═══════════ COLOR WHEELS (3-way) ═══════════
        sec = _CollapsibleSection("COLOR WHEELS")
        sec._toggle()  # start collapsed
        self._color_wheels = ColorWheelsWidget()
        self._color_wheels.wheels_changed.connect(self._schedule_preview)
        self._color_wheels.wheels_changed.connect(self._commit_undo_state)
        sec.add_widget(self._color_wheels)
        self._panels_layout.addWidget(sec)

        # ═══════════ LENS & GEOMETRY ═══════════
        sec = _CollapsibleSection("LENS & GEOMETRY")
        sec._toggle()  # start collapsed

        # Auto lens profile checkbox
        self._auto_lens_cb = QCheckBox("Auto Lens Profile")
        self._auto_lens_cb.setChecked(True)
        self._auto_lens_cb.setStyleSheet(
            f"QCheckBox {{ color: {_TEXT_DIM}; font-size: 11px; spacing: 6px; }}"
            f"QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {_BTN_BORDER}; "
            f"border-radius: 3px; background: {_BTN_BG}; }}"
            f"QCheckBox::indicator:checked {{ background: {_ACCENT}; border-color: {_ACCENT}; }}"
        )
        sec.add_widget(self._auto_lens_cb)

        # Rotation slider
        rot_s = _SliderRow("Rotation", -45, 45, 0)
        rot_s.value_changed.connect(self._schedule_preview)
        rot_s.value_changed.connect(lambda slider=rot_s: self._canvas.set_rotation_value(float(slider.value())))
        rot_s.released.connect(self._commit_undo_state)
        sec.add_widget(rot_s)
        self._sliders["rotation"] = rot_s

        lens_geo_sliders = [
            ("distortion", "Distortion", -100, 100, 0),
            ("perspective_h", "Perspective Horiz", -45, 45, 0),
            ("perspective_v", "Perspective Vert", -45, 45, 0),
        ]
        for key, label, lo, hi, default in lens_geo_sliders:
            s = _SliderRow(label, lo, hi, default)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s
        self._panels_layout.addWidget(sec)

        # ═══════════ TOOLS ═══════════
        sec = _CollapsibleSection("TOOLS")

        tool_btn_style = (
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 7px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_ACCENT_DIM}; color: {_TEXT}; "
            f"border-color: rgba(232, 149, 48, 0.25); }}"
        )

        manual_mask_btn = QPushButton("✏️ Manual Masking")
        manual_mask_btn.setStyleSheet(tool_btn_style)
        manual_mask_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        manual_mask_btn.clicked.connect(self._manual_masking)
        sec.add_widget(manual_mask_btn)

        self._panels_layout.addWidget(sec)

        # ═══════════ AI TOOLS ═══════════
        sec = _CollapsibleSection("AI TOOLS")
        sec._toggle()  # start collapsed

        ai_btn_style = (
            f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
            f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 7px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {_ACCENT_DIM}; color: {_TEXT}; "
            f"border-color: rgba(232, 149, 48, 0.25); }}"
        )

        ai_enhance_btn = QPushButton("✨ AI Auto-Enhance")
        ai_enhance_btn.setStyleSheet(ai_btn_style)
        ai_enhance_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_enhance_btn.clicked.connect(self._ai_auto_enhance)
        sec.add_widget(ai_enhance_btn)

        ai_wb_btn = QPushButton("🎨 AI Auto White Balance")
        ai_wb_btn.setStyleSheet(ai_btn_style)
        ai_wb_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_wb_btn.clicked.connect(self._ai_auto_wb)
        sec.add_widget(ai_wb_btn)

        ai_denoise_btn = QPushButton("🔇 AI Denoise")
        ai_denoise_btn.setStyleSheet(ai_btn_style)
        ai_denoise_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_denoise_btn.clicked.connect(self._ai_denoise)
        sec.add_widget(ai_denoise_btn)

        ai_sharpen_btn = QPushButton("🔍 AI Smart Sharpen")
        ai_sharpen_btn.setStyleSheet(ai_btn_style)
        ai_sharpen_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_sharpen_btn.clicked.connect(self._ai_sharpen)
        sec.add_widget(ai_sharpen_btn)

        ai_bw_btn = QPushButton("⚫ AI B&&W Conversion")
        ai_bw_btn.setStyleSheet(ai_btn_style)
        ai_bw_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_bw_btn.clicked.connect(self._ai_bw)
        sec.add_widget(ai_bw_btn)

        # --- Separator ---
        _sep = QLabel("─── Advanced AI ───")
        _sep.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 9px; padding: 6px 0 2px 0;")
        _sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sec.add_widget(_sep)

        ai_mask_btn = QPushButton("🎭 AI Masking (Select Subject)")
        ai_mask_btn.setStyleSheet(ai_btn_style)
        ai_mask_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_mask_btn.clicked.connect(self._ai_masking)
        sec.add_widget(ai_mask_btn)

        ai_blur_btn = QPushButton("📷 AI Lens Blur (Bokeh)")
        ai_blur_btn.setStyleSheet(ai_btn_style)
        ai_blur_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_blur_btn.clicked.connect(self._ai_lens_blur)
        sec.add_widget(ai_blur_btn)

        ai_face_btn = QPushButton("👤 AI Face Detection")
        ai_face_btn.setStyleSheet(ai_btn_style)
        ai_face_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_face_btn.clicked.connect(self._ai_face_detect)
        sec.add_widget(ai_face_btn)

        ai_sr_btn = QPushButton("🔎 AI Super Resolution")
        ai_sr_btn.setStyleSheet(ai_btn_style)
        ai_sr_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_sr_btn.clicked.connect(self._ai_super_resolution)
        sec.add_widget(ai_sr_btn)

        ai_detail_btn = QPushButton("🔬 AI Enhance Details")
        ai_detail_btn.setStyleSheet(ai_btn_style)
        ai_detail_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_detail_btn.clicked.connect(self._ai_enhance_details)
        sec.add_widget(ai_detail_btn)

        ai_adaptive_btn = QPushButton("🧠 AI Adaptive Preset")
        ai_adaptive_btn.setStyleSheet(ai_btn_style)
        ai_adaptive_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_adaptive_btn.clicked.connect(self._ai_adaptive_preset)
        sec.add_widget(ai_adaptive_btn)

        self._panels_layout.addWidget(sec)

        # ==============================================================
        # EXPERT MODE PANELS (hidden by default)
        # ==============================================================
        self._expert_sections: list[QWidget] = []

        # ═══════════ ADVANCED TONE ═══════════
        sec = _CollapsibleSection("ADVANCED TONE")
        sec._toggle()  # start collapsed
        adv_tone_sliders = [
            ("hl_compression", "Highlight Compression", 0, 500, 80),
            ("shadow_compression", "Shadow Compression", 0, 100, 60),
            ("lc_radius", "Local Contrast Radius", 20, 200, 80),
            ("lc_darkness", "LC Darkness", 0, 100, 50),
            ("lc_lightness", "LC Lightness", 0, 100, 50),
            ("soft_light", "Soft Light / Glow", 0, 100, 0),
        ]
        for key, label, lo, hi, default in adv_tone_sliders:
            s = _SliderRow(label, lo, hi, default)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s

        # Highlight recovery method
        hr_row = QHBoxLayout()
        hr_lbl = QLabel("HL Recovery:")
        hr_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px;")
        hr_row.addWidget(hr_lbl)
        self._hl_recovery_combo = QComboBox()
        self._hl_recovery_combo.addItems(["Blend", "Luminance", "CIELab"])
        self._hl_recovery_combo.setFixedHeight(24)
        self._hl_recovery_combo.setStyleSheet(
            f"QComboBox {{ background: {_BTN_BG}; color: {_TEXT}; border: 1px solid {_BORDER}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 10px; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {_SECTION_BG}; color: {_TEXT}; "
            f"selection-background-color: {_ACCENT}; }}"
        )
        hr_row.addWidget(self._hl_recovery_combo)
        hr_row.addStretch()
        sec.add_layout(hr_row)

        sec.setVisible(False)
        self._expert_sections.append(sec)
        self._panels_layout.addWidget(sec)

        # ═══════════ ADVANCED DETAIL ═══════════
        sec = _CollapsibleSection("ADVANCED DETAIL")
        sec._toggle()  # start collapsed
        adv_detail_sliders = [
            ("micro_sharp_strength", "Micro Sharpen", 0, 100, 0),
            ("micro_sharp_contrast", "Micro Contrast", 0, 100, 20),
            ("sharp_threshold", "Sharpen Threshold", 0, 100, 20),
            ("halo_control", "Halo Control", 0, 100, 85),
            ("defringe_radius", "Defringe Radius", 5, 50, 20),
            ("defringe_threshold", "Defringe Threshold", 0, 50, 13),
        ]
        for key, label, lo, hi, default in adv_detail_sliders:
            s = _SliderRow(label, lo, hi, default)
            s.value_changed.connect(self._schedule_preview)
            s.released.connect(self._commit_undo_state)
            sec.add_widget(s)
            self._sliders[key] = s

        sec.setVisible(False)
        self._expert_sections.append(sec)
        self._panels_layout.addWidget(sec)

        # ═══════════ RAW ENGINE ═══════════
        sec = _CollapsibleSection("RAW ENGINE")
        sec._toggle()  # start collapsed

        export_note = QLabel("These settings affect final export only (not preview)")
        export_note.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 9px; font-style: italic;")
        export_note.setWordWrap(True)
        sec.add_widget(export_note)

        combo_style = (
            f"QComboBox {{ background: {_BTN_BG}; color: {_TEXT}; border: 1px solid {_BORDER}; "
            f"border-radius: 4px; padding: 4px 8px; font-size: 10px; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {_SECTION_BG}; color: {_TEXT}; "
            f"selection-background-color: {_ACCENT}; }}"
        )

        # Demosaic method
        dm_row = QHBoxLayout()
        dm_lbl = QLabel("Demosaic:")
        dm_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 10px;")
        dm_row.addWidget(dm_lbl)
        self._demosaic_combo = QComboBox()
        self._demosaic_combo.addItems(["amaze", "rcd", "igv", "lmmse", "dcb", "vng4"])
        self._demosaic_combo.setFixedHeight(26)
        self._demosaic_combo.setStyleSheet(combo_style)
        dm_row.addWidget(self._demosaic_combo)
        dm_row.addStretch()
        sec.add_layout(dm_row)

        # Working profile
        wp_row = QHBoxLayout()
        wp_lbl = QLabel("Working:")
        wp_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 10px;")
        wp_row.addWidget(wp_lbl)
        self._working_profile_combo = QComboBox()
        self._working_profile_combo.addItems(
            ["ProPhoto", "sRGB", "Adobe RGB", "WideGamut", "BestRGB", "Rec2020"]
        )
        self._working_profile_combo.setFixedHeight(26)
        self._working_profile_combo.setStyleSheet(combo_style)
        wp_row.addWidget(self._working_profile_combo)
        wp_row.addStretch()
        sec.add_layout(wp_row)

        # Output profile
        op_row = QHBoxLayout()
        op_lbl = QLabel("Output:")
        op_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 10px;")
        op_row.addWidget(op_lbl)
        self._output_profile_combo = QComboBox()
        self._output_profile_combo.addItems(
            ["RTv4_sRGB", "RTv4_AdobeRGB", "RTv4_ProPhoto", "RTv4_Rec2020", "RTv4_Large"]
        )
        self._output_profile_combo.setFixedHeight(26)
        self._output_profile_combo.setStyleSheet(combo_style)
        op_row.addWidget(self._output_profile_combo)
        op_row.addStretch()
        sec.add_layout(op_row)

        sec.setVisible(False)
        self._expert_sections.append(sec)
        self._panels_layout.addWidget(sec)

    # ------------------------------------------------------------------
    # Expert mode toggle
    # ------------------------------------------------------------------

    def _toggle_expert_mode(self, enabled: bool) -> None:
        """Show or hide expert editing panels."""
        for sec in self._expert_sections:
            sec.setVisible(enabled)

    def _on_curve_channel_changed(self, index: int) -> None:
        """Switch between luminance/R/G/B curve channels."""
        # Save current channel's points
        self._curve_data[self._current_curve_channel] = list(self._tone_curve_widget.get_points())
        # Load new channel
        channel = self._curve_channel.currentText()
        self._current_curve_channel = channel
        self._tone_curve_widget.set_points(self._curve_data[channel])

    # ------------------------------------------------------------------
    # Photo loading
    # ------------------------------------------------------------------

    def _load_photo(self, index: int) -> None:
        """Load and decode a photo at the given index."""
        if index < 0 or index >= len(self._photos):
            return

        self._index = index
        p = self._photos[index]
        fname = p.get("file_name", "Unknown")
        total = len(self._photos)
        self._title_label.setText(f"{fname}  ({index + 1} / {total})")
        self._filmstrip.set_current(index)

        # Update info label
        info_parts = [f"File: {fname}"]
        if p.get("exif_iso"):
            info_parts.append(f"ISO: {p['exif_iso']}")
        if p.get("exif_aperture"):
            info_parts.append(f"f/{p['exif_aperture']}")
        if p.get("exif_shutter_speed"):
            info_parts.append(f"SS: {p['exif_shutter_speed']}")
        if p.get("quality_score") is not None:
            info_parts.append(f"Score: {p['quality_score']:.0%}")
        self._info_label.setText("\n".join(info_parts))

        # Load existing overrides into sliders
        self._load_overrides(p)

        # Snapshot params for undo after loading overrides
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._last_committed_params = self._gather_params()

        # Check cache
        if index in self._rgb_cache:
            self._raw_rgb = self._rgb_cache[index]
            self._raw_rgb_preview = self._make_preview_proxy(self._raw_rgb)
            self._optimize_btn.setEnabled(True)
            self._optimize_btn.setText("⚡ AI Optimize All")
            self._rebuild_grade_thumbnails()
            self._update_preview()
        else:
            # Show placeholder while decoding
            self._raw_rgb = None
            self._raw_rgb_preview = None
            self._optimize_btn.setEnabled(False)
            self._optimize_btn.setText("⏳ Decoding…")
            if self._ai_modal:
                self._ai_modal.show_message("Decoding RAW", fname)
            thumb = p.get("thumbnail_path", "")
            if thumb and Path(thumb).is_file():
                pix = QPixmap(thumb)
                if not pix.isNull():
                    self._set_preview_pixmap(pix)

            # Start half-resolution decode (fast preview — full res not needed for editing)
            file_path = p.get("file_path", "")
            if file_path and Path(file_path).is_file():
                self._decode_worker = _RawDecodeWorker(
                    index, file_path, half_size=True, parent=self
                )
                self._decode_worker.decoded.connect(self._on_raw_decoded)
                self._decode_worker.decode_failed.connect(self._on_raw_decode_failed)
                self._decode_worker.start()

    @staticmethod
    def _make_preview_proxy(rgb: np.ndarray, max_dim: int = 1100) -> np.ndarray:
        """Downscale for fast preview if the image is larger than max_dim."""
        h, w = rgb.shape[:2]
        if max(h, w) <= max_dim:
            return rgb
        scale = max_dim / max(h, w)
        new_w = int(w * scale)
        new_h = int(h * scale)
        try:
            import cv2

            return cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
        except ImportError:
            from PIL import Image

            pil = Image.fromarray(rgb)
            pil = pil.resize((new_w, new_h), Image.LANCZOS)
            return np.array(pil)

    def _on_raw_decode_failed(self, index: int, error: str) -> None:
        """RAW decode failed — dismiss overlay so the UI isn't stuck."""
        logger.warning("RAW decode failed for index %d: %s", index, error)
        if index == self._index:
            self._optimize_btn.setEnabled(False)
            self._optimize_btn.setText("⚠ Decode failed")
            if self._ai_modal and not self._batch_worker:
                self._ai_modal.hide_modal()

    def _on_raw_decoded(self, index: int, rgb: np.ndarray) -> None:
        """RAW decode complete — cache and show preview."""
        self._rgb_cache[index] = rgb
        # Evict oldest entries to keep memory bounded
        if len(self._rgb_cache) >= self._RGB_CACHE_LIMIT:
            to_remove = sorted(self._rgb_cache.keys())
            for k in to_remove[: len(self._rgb_cache) - self._RGB_CACHE_LIMIT + 1]:
                if k != self._index:
                    del self._rgb_cache[k]
        if index == self._index:
            self._raw_rgb = rgb
            self._raw_rgb_preview = self._make_preview_proxy(rgb)
            self._optimize_btn.setEnabled(True)
            self._optimize_btn.setText("⚡ AI Optimize All")
            # Dismiss decode overlay (only if batch optimize isn't running)
            if self._ai_modal and not self._batch_worker:
                self._ai_modal.hide_modal()
            self._rebuild_grade_thumbnails()
            self._update_preview()
            # Prefetch adjacent photos in the background
            self._prefetch_adjacent(index)

    def _prefetch_adjacent(self, current: int) -> None:
        """Start background decode of the next (and previous) photo if not cached."""
        for candidate in (current + 1, current - 1):
            if 0 <= candidate < len(self._photos) and candidate not in self._rgb_cache:
                p = self._photos[candidate]
                fpath = p.get("file_path", "")
                if fpath and Path(fpath).is_file():
                    self._prefetch_worker = _RawDecodeWorker(
                        candidate,
                        fpath,
                        half_size=True,
                        parent=self,
                    )
                    self._prefetch_worker.decoded.connect(self._on_prefetch_decoded)
                    self._prefetch_worker.start()
                    return  # Only prefetch one at a time

    def _on_prefetch_decoded(self, index: int, rgb: np.ndarray) -> None:
        """Prefetched RAW decode complete — just cache it."""
        self._rgb_cache[index] = rgb
        if len(self._rgb_cache) >= self._RGB_CACHE_LIMIT:
            to_remove = sorted(self._rgb_cache.keys())
            for k in to_remove[: len(self._rgb_cache) - self._RGB_CACHE_LIMIT + 1]:
                if k != self._index:
                    del self._rgb_cache[k]

    # ------------------------------------------------------------------
    # Color grade thumbnail grid
    # ------------------------------------------------------------------

    def _rebuild_grade_thumbnails(self) -> None:
        """Generate tiny preview thumbnails for each color grade."""
        # Clear old buttons
        for btn in self._grade_thumb_btns:
            btn.setParent(None)
            btn.deleteLater()
        self._grade_thumb_btns.clear()

        # Need raw data to render thumbnails
        if self._raw_rgb is None:
            return

        # Build a tiny proxy (80px wide) for speed
        h, w = self._raw_rgb.shape[:2]
        thumb_w = 80
        scale = thumb_w / max(w, 1)
        thumb_h = max(int(h * scale), 50)
        try:
            import cv2

            tiny = cv2.resize(self._raw_rgb, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        except ImportError:
            from PIL import Image

            pil = Image.fromarray(self._raw_rgb)
            pil = pil.resize((thumb_w, thumb_h), Image.LANCZOS)
            tiny = np.array(pil)

        cols = 3
        current_grade = self._grade_combo.currentText()

        for i, name in enumerate(self._grade_names):
            grade_lut = _GRADE_LUT[name]
            # Render thumbnail with this grade at full intensity
            if name == "natural":
                rendered = tiny
            else:
                fimg = tiny.astype(np.float32) / 255.0
                graded = _apply_color_grade(fimg, grade_lut, 1.0)
                rendered = np.clip(graded * 255, 0, 255).astype(np.uint8)

            # Convert to QPixmap
            qimg = QImage(
                rendered.data.tobytes(),
                thumb_w,
                thumb_h,
                3 * thumb_w,
                QImage.Format.Format_RGB888,
            )
            pix = QPixmap.fromImage(qimg)

            btn = QPushButton()
            btn.setFixedSize(thumb_w + 6, thumb_h + 18)
            # Use a label overlay approach for the thumbnail
            btn_layout = QVBoxLayout(btn)
            btn_layout.setContentsMargins(2, 2, 2, 0)
            btn_layout.setSpacing(0)
            img_label = QLabel()
            img_label.setPixmap(pix)
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            btn_layout.addWidget(img_label)
            name_label = QLabel(name.replace("_", " ").title())
            name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_label.setStyleSheet(
                f"color: {_TEXT_DIM}; font-size: 8px; background: transparent;"
            )
            name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            btn_layout.addWidget(name_label)

            is_selected = name == current_grade
            btn.setStyleSheet(self._grade_btn_style(is_selected))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setProperty("grade_name", name)
            btn.clicked.connect(lambda checked=False, n=name: self._on_grade_selected(n))
            self._grade_grid_layout.addWidget(btn, i // cols, i % cols)
            self._grade_thumb_btns.append(btn)

    @staticmethod
    def _grade_btn_style(selected: bool) -> str:
        if selected:
            return (
                f"QPushButton {{ background: {_SECTION_BG}; border: 2px solid {_ACCENT}; border-radius: 4px; }}"
                f"QPushButton:hover {{ background: {_BTN_HOVER}; }}"
            )
        return (
            f"QPushButton {{ background: {_BTN_BG}; border: 1px solid {_BORDER}; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {_BTN_HOVER}; border-color: {_BTN_BORDER}; }}"
        )

    def _highlight_selected_grade(self, grade_name: str) -> None:
        """Update border highlight on grade thumbnails."""
        for btn in self._grade_thumb_btns:
            is_sel = btn.property("grade_name") == grade_name
            btn.setStyleSheet(self._grade_btn_style(is_sel))

    def _on_grade_selected(self, name: str) -> None:
        """User clicked a grade thumbnail."""
        idx = self._grade_combo.findText(name)
        if idx >= 0:
            self._grade_combo.setCurrentIndex(idx)
        self._highlight_selected_grade(name)
        self._schedule_preview()
        self._commit_undo_state()

    def _grade_save_one(self) -> None:
        """Persist the current color grade to this photo's overrides."""
        self._save_current_to_dict()
        photo = self._photos[self._index]
        pid = photo.get("id", 0)
        raw = photo.get("manual_overrides", "{}")
        try:
            ov = json.loads(raw) if isinstance(raw, str) else (raw if raw else {})
        except (json.JSONDecodeError, TypeError):
            ov = {}
        ov["_editor_touched"] = True
        photo["manual_overrides"] = json.dumps(ov)
        if pid:
            self.edits_saved.emit([(pid, ov)])
        self._grade_apply_one.setText("✓ Saved!")
        QTimer.singleShot(1500, lambda: self._grade_apply_one.setText("Apply to This Photo"))

    def _grade_save_all(self) -> None:
        """Apply current color grade + intensity to all photos in the batch and persist."""
        grade = self._grade_combo.currentText()
        intensity = self._grade_intensity.value()
        batch = []
        for p in self._photos:
            raw = p.get("manual_overrides", "{}")
            try:
                ov = json.loads(raw) if isinstance(raw, str) else (raw if raw else {})
            except (json.JSONDecodeError, TypeError):
                ov = {}
            ov["color_grade"] = grade
            ov["color_grade_intensity"] = intensity
            ov["_editor_touched"] = True
            p["manual_overrides"] = json.dumps(ov)
            pid = p.get("id", 0)
            if pid:
                batch.append((pid, ov))
        if batch:
            self.edits_saved.emit(batch)
        self._grade_apply_all.setText(f"✓ Applied to {len(self._photos)}!")
        QTimer.singleShot(1500, lambda: self._grade_apply_all.setText("Apply to All Photos"))

    # ------------------------------------------------------------------
    # Load overrides
    # ------------------------------------------------------------------

    def _load_overrides(self, photo: dict) -> None:
        """Populate sliders from stored manual overrides."""
        overrides = photo.get("manual_overrides", {})
        if isinstance(overrides, str):
            try:
                overrides = json.loads(overrides) if overrides else {}
            except (json.JSONDecodeError, TypeError):
                overrides = {}

        if not overrides and photo.get("scene_preset"):
            overrides = get_editor_style_overrides(photo["scene_preset"])

        if "color_grade" not in overrides and photo.get("color_grade"):
            overrides["color_grade"] = photo["color_grade"]
        if "color_grade_intensity" not in overrides:
            overrides["color_grade_intensity"] = 100

        # Block signals during bulk load
        for key, slider in self._sliders.items():
            if key in overrides:
                slider.set_value(int(overrides[key]))
            else:
                slider.reset()

        # Make sure the canvas rotation baseline matches the restored slider value.
        if self._canvas is not None and "rotation" in self._sliders:
            self._canvas.set_rotation_value(float(self._sliders["rotation"].value()))

        grade = overrides.get("color_grade", "natural")
        idx = self._grade_combo.findText(grade)
        if idx >= 0:
            self._grade_combo.blockSignals(True)
            self._grade_combo.setCurrentIndex(idx)
            self._grade_combo.blockSignals(False)
        intensity = overrides.get("color_grade_intensity", 100)
        self._grade_intensity.set_value(int(intensity))
        self._highlight_selected_grade(grade)

        # Expert mode: restore tone curves
        for ch in ("Luminance", "Red", "Green", "Blue"):
            key = f"tone_curve_{ch.lower()}"
            if key in overrides:
                pts = overrides[key]
                if isinstance(pts, list) and len(pts) >= 2:
                    self._curve_data[ch] = [(p[0], p[1]) for p in pts]
                else:
                    self._curve_data[ch] = [(0.0, 0.0), (1.0, 1.0)]
            else:
                self._curve_data[ch] = [(0.0, 0.0), (1.0, 1.0)]
        self._tone_curve_widget.set_points(self._curve_data[self._current_curve_channel])

        # Restore color wheels
        self._color_wheels.set_from_params(overrides)

        # Restore crop from overrides
        cx = overrides.get("crop_x", 0)
        cy = overrides.get("crop_y", 0)
        cw = overrides.get("crop_w", 0)
        ch_val = overrides.get("crop_h", 0)
        if cw > 0 and ch_val > 0:
            self._crop_rect = QRect(int(cx), int(cy), int(cw), int(ch_val))
            self._canvas.set_crop_rect(self._crop_rect)
        else:
            self._crop_rect = None
            self._canvas.clear_crop()

        # Expert combos
        if "hl_recovery_method" in overrides:
            idx = self._hl_recovery_combo.findText(overrides["hl_recovery_method"])
            if idx >= 0:
                self._hl_recovery_combo.setCurrentIndex(idx)
        if "demosaic_method" in overrides:
            idx = self._demosaic_combo.findText(overrides["demosaic_method"])
            if idx >= 0:
                self._demosaic_combo.setCurrentIndex(idx)
        if "working_profile" in overrides:
            idx = self._working_profile_combo.findText(overrides["working_profile"])
            if idx >= 0:
                self._working_profile_combo.setCurrentIndex(idx)
        if "output_profile" in overrides:
            idx = self._output_profile_combo.findText(overrides["output_profile"])
            if idx >= 0:
                self._output_profile_combo.setCurrentIndex(idx)
        if "auto_lens_profile" in overrides:
            self._auto_lens_cb.setChecked(bool(overrides["auto_lens_profile"]))

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _crop_scale_factors(self) -> tuple[float, float]:
        """Return (sx, sy) to convert preview-proxy crop coords to full-res."""
        if self._raw_rgb is None:
            return (1.0, 1.0)
        preview_src = self._raw_rgb_preview if self._raw_rgb_preview is not None else self._raw_rgb
        full_h, full_w = self._raw_rgb.shape[:2]
        prev_h, prev_w = preview_src.shape[:2]
        return (
            full_w / prev_w if prev_w > 0 else 1.0,
            full_h / prev_h if prev_h > 0 else 1.0,
        )

    def _gather_params(self) -> dict:
        """Collect all slider values into a params dict."""
        params = {}
        for key, slider in self._sliders.items():
            params[key] = slider.value()
        params["color_grade"] = self._grade_combo.currentText()
        params["color_grade_intensity"] = self._grade_intensity.value()

        # Include crop data scaled to _raw_rgb coordinate space.
        # Also store the source image dimensions so the exporter can scale
        # the crop correctly regardless of how it decodes the RAW file.
        if self._crop_rect and self._crop_rect.width() > 0 and self._crop_rect.height() > 0:
            sx, sy = self._crop_scale_factors()
            params["crop_x"] = int(self._crop_rect.x() * sx)
            params["crop_y"] = int(self._crop_rect.y() * sy)
            params["crop_w"] = int(self._crop_rect.width() * sx)
            params["crop_h"] = int(self._crop_rect.height() * sy)
            if self._raw_rgb is not None:
                params["crop_src_w"] = int(self._raw_rgb.shape[1])
                params["crop_src_h"] = int(self._raw_rgb.shape[0])

        # Tone curve data (always gathered — main panel)
        self._curve_data[self._current_curve_channel] = list(self._tone_curve_widget.get_points())
        for ch, pts in self._curve_data.items():
            if len(pts) > 2 or (len(pts) == 2 and (pts[0] != (0.0, 0.0) or pts[1] != (1.0, 1.0))):
                params[f"tone_curve_{ch.lower()}"] = pts

        # Color wheels data
        params.update(self._color_wheels.get_params())

        # Auto lens profile
        params["auto_lens_profile"] = self._auto_lens_cb.isChecked()

        # Expert mode combos
        if self._expert_btn.isChecked():
            params["hl_recovery_method"] = self._hl_recovery_combo.currentText()
            params["demosaic_method"] = self._demosaic_combo.currentText()
            params["working_profile"] = self._working_profile_combo.currentText()
            params["output_profile"] = self._output_profile_combo.currentText()

        return params

    def _schedule_preview(self) -> None:
        """Debounced preview update — fires live during slider drag."""
        self._preview_timer.start()

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def _commit_undo_state(self) -> None:
        """Save the current slider state to the undo stack (called on slider release)."""
        current = self._gather_params()
        if self._last_committed_params is not None and current == self._last_committed_params:
            return
        if self._last_committed_params is not None:
            self._undo_stack.append(self._last_committed_params)
            # Cap undo stack at 50 entries
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._last_committed_params = current

    def _undo(self) -> None:
        """Restore the previous slider state."""
        if not self._undo_stack:
            return
        current = self._gather_params()
        self._redo_stack.append(current)
        prev = self._undo_stack.pop()
        self._apply_params(prev)
        self._last_committed_params = prev
        self._schedule_preview()

    def _redo(self) -> None:
        """Restore the next slider state."""
        if not self._redo_stack:
            return
        current = self._gather_params()
        self._undo_stack.append(current)
        nxt = self._redo_stack.pop()
        self._apply_params(nxt)
        self._last_committed_params = nxt
        self._schedule_preview()

    def _apply_params(self, params: dict) -> None:
        """Set all sliders from a params dict without triggering preview."""
        for key, slider in self._sliders.items():
            if key in params:
                slider.set_value(int(params[key]))
            else:
                slider.reset()
        grade = params.get("color_grade", "natural")
        idx = self._grade_combo.findText(grade)
        if idx >= 0:
            self._grade_combo.blockSignals(True)
            self._grade_combo.setCurrentIndex(idx)
            self._grade_combo.blockSignals(False)
        intensity = params.get("color_grade_intensity", 100)
        self._grade_intensity.set_value(int(intensity))
        self._highlight_selected_grade(grade)

        # Restore expert tone curves
        for ch in ("Luminance", "Red", "Green", "Blue"):
            key = f"tone_curve_{ch.lower()}"
            if key in params:
                self._curve_data[ch] = [(p[0], p[1]) for p in params[key]]
            else:
                self._curve_data[ch] = [(0.0, 0.0), (1.0, 1.0)]
        self._tone_curve_widget.set_points(self._curve_data[self._current_curve_channel])

        # Restore color wheels
        self._color_wheels.set_from_params(params)

        # Restore crop state for undo/redo
        cx = params.get("crop_x", 0)
        cy = params.get("crop_y", 0)
        cw = params.get("crop_w", 0)
        ch_val = params.get("crop_h", 0)
        if cw > 0 and ch_val > 0:
            # Params store full-res coords; convert back to preview-proxy space
            sx, sy = self._crop_scale_factors()
            isx = 1.0 / sx if sx > 0 else 1.0
            isy = 1.0 / sy if sy > 0 else 1.0
            self._crop_rect = QRect(int(cx * isx), int(cy * isy), int(cw * isx), int(ch_val * isy))
            self._canvas.set_crop_rect(self._crop_rect)
        else:
            self._crop_rect = None
            self._canvas.clear_crop()

    def _update_preview(self) -> None:
        """Apply current adjustments to the RAW data and display."""
        if self._raw_rgb is None:
            return

        # Use the smaller preview proxy for editing — same visual quality
        # on screen but ~3-4x fewer pixels to process.
        source = self._raw_rgb_preview if self._raw_rgb_preview is not None else self._raw_rgb

        if self._show_before:
            result = source
        else:
            params = self._gather_params()

            # Masked Area tab: composite global + masked-region edits
            if (
                self._mask_active_tab == 1
                and self._active_mask is not None
                and self._mask_global_params is not None
            ):
                import cv2

                base = PreviewEngine.apply(source, self._mask_global_params)
                masked = PreviewEngine.apply(source, params)
                mask = self._active_mask
                if mask.shape[:2] != base.shape[:2]:
                    mask = cv2.resize(
                        mask, (base.shape[1], base.shape[0]), interpolation=cv2.INTER_LINEAR
                    )
                m = np.clip(mask, 0.0, 1.0)[:, :, np.newaxis]
                result = np.clip(
                    base.astype(np.float32) * (1.0 - m) + masked.astype(np.float32) * m,
                    0,
                    255,
                ).astype(np.uint8)
            else:
                result = PreviewEngine.apply(source, params)

            # Apply crop to preview when not in crop mode (crop already applied)
            crop_rect = self._crop_rect
            if crop_rect and not self._crop_btn.isChecked():
                rh, rw = result.shape[:2]
                x1 = max(0, min(crop_rect.x(), rw - 1))
                y1 = max(0, min(crop_rect.y(), rh - 1))
                x2 = min(rw, crop_rect.x() + crop_rect.width())
                y2 = min(rh, crop_rect.y() + crop_rect.height())
                if x2 > x1 + 5 and y2 > y1 + 5:
                    result = result[y1:y2, x1:x2]

        # Update histogram
        self._histogram.update_histogram(result)

        # Convert to QPixmap — use contiguous buffer directly to avoid a copy
        result = np.ascontiguousarray(result)
        h, w = result.shape[:2]
        bytes_per_line = 3 * w
        qimg = QImage(
            result.data,
            w,
            h,
            bytes_per_line,
            QImage.Format.Format_RGB888,
        )
        # Must keep a reference to the array so the buffer stays alive
        qimg._numpy_ref = result  # prevent GC
        pix = QPixmap.fromImage(qimg)
        self._preview_pix = pix
        self._set_preview_pixmap(pix)

    def _set_preview_pixmap(self, pix: QPixmap) -> None:
        """Display the pixmap in the preview canvas."""
        if pix.isNull():
            return
        self._canvas.set_pixmap(pix)
        self._zoom_label.setText(self._canvas.get_zoom_text())

    def _set_zoom(self, factor: float) -> None:
        self._canvas.set_zoom(factor)
        self._zoom_label.setText(self._canvas.get_zoom_text())
        if self._preview_pix:
            self._canvas.set_pixmap(self._preview_pix)

    def _on_zoom_changed(self) -> None:
        """Scroll-wheel zoom — just update label, no re-render needed."""
        self._zoom_label.setText(self._canvas.get_zoom_text())

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate(self, delta: int) -> None:
        new_idx = self._index + delta
        if 0 <= new_idx < len(self._photos):
            self._save_current_to_dict()
            self._load_photo(new_idx)

    def _on_filmstrip_nav(self, index: int) -> None:
        if index != self._index:
            self._save_current_to_dict()
            self._load_photo(index)

    # ------------------------------------------------------------------
    # Before / After
    # ------------------------------------------------------------------

    def _toggle_before_after(self) -> None:
        self._show_before = not self._show_before
        if self._show_before:
            self._ba_btn.setText("◀ SHOWING BEFORE  [\\]")
            self._ba_btn.setStyleSheet(
                f"QPushButton {{ background: {_ACCENT}; color: #111; font-weight: 600; "
                f"border: none; border-radius: 5px; padding: 4px 12px; font-size: 10px; }}"
            )
        else:
            self._ba_btn.setText("Before / After  [\\]")
            self._ba_btn.setStyleSheet(
                f"QPushButton {{ background: {_BTN_BG}; color: {_TEXT_DIM}; "
                f"border: 1px solid {_BORDER}; border-radius: 5px; padding: 4px 12px; font-size: 10px; }}"
            )
        self._update_preview()

    # ------------------------------------------------------------------
    # Save / Apply / Reset
    # ------------------------------------------------------------------

    def _save_current_to_dict(self) -> None:
        """Persist current slider state into the in-memory photo dict."""
        if not self._photos or self._raw_rgb is None:
            return
        params = self._gather_params()
        params["_editor_touched"] = True
        self._photos[self._index]["manual_overrides"] = json.dumps(params)

    def _collect_manual_edit_batch(self) -> list[tuple[int, dict]]:
        """Gather all edited photos that should be persisted to the database."""
        self._save_current_to_dict()
        batch: list[tuple[int, dict]] = []
        for p in self._photos:
            pid = p.get("id", 0)
            raw = p.get("manual_overrides", "")
            if not pid or not raw:
                continue
            try:
                overrides = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(overrides, dict):
                continue
            if any(v != 0 for k, v in overrides.items() if k != "color_grade"):
                batch.append((pid, overrides))
        return batch

    def _save_all_edits(self) -> None:
        """Save all edits to the database without exporting."""
        batch = self._collect_manual_edit_batch()
        if batch:
            self._save_btn.setEnabled(False)
            self._save_btn.setText("💾 Saving…")
            self.edits_saved.emit(batch)
        else:
            self._save_btn.setText("💾 Nothing to save")
            QTimer.singleShot(1500, lambda: self._save_btn.setText("💾 Save All Edits"))

    def on_edits_saved(self, count: int) -> None:
        """Called by host after edits are persisted."""
        self._save_btn.setEnabled(True)
        self._save_btn.setText(f"💾 Saved {count} edits!")
        QTimer.singleShot(2000, lambda: self._save_btn.setText("💾 Save All Edits"))
        self.setFocus()

    def _on_apply(self) -> None:
        """Show export options dialog, then emit export signal(s)."""
        from imagic.views.export_dialog import ExportOptionsDialog

        dlg = ExportOptionsDialog(batch_size=len(self._photos), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        fmt = dlg.chosen_format()
        quality = dlg.chosen_quality()
        export_all = dlg.export_all()

        # Always save the current photo's edits first
        params = self._gather_params()
        photo = self._photos[self._index]
        photo_id = photo.get("id", 0)

        # Store locally so navigation remembers the edit
        photo["manual_overrides"] = json.dumps(params)

        # Attach export options to overrides for single-photo export
        params["_export_format"] = fmt
        params["_export_quality"] = quality

        self._apply_btn.setEnabled(False)

        if export_all:
            # Collect all photos' current overrides
            batch = []
            for i, p in enumerate(self._photos):
                pid = p.get("id", 0)
                if i == self._index:
                    batch.append((pid, params))
                else:
                    raw = p.get("manual_overrides")
                    if raw:
                        try:
                            ov = json.loads(raw) if isinstance(raw, str) else raw
                        except (json.JSONDecodeError, TypeError):
                            ov = {}
                    else:
                        ov = {}
                    ov["_export_format"] = fmt
                    ov["_export_quality"] = quality
                    batch.append((pid, ov))
            self._apply_btn.setText(f"Exporting {len(batch)} photos…")
            self.batch_export_all.emit(batch, fmt, quality)
        else:
            self._apply_btn.setText("Exporting…")
            self.edit_applied.emit(photo_id, params)

    def on_export_finished(self, success: bool, new_export_path: str = "") -> None:
        """Called by host after re-export completes."""
        self._apply_btn.setEnabled(True)
        self._apply_btn.setText("  Apply && Export  ")
        if success and new_export_path:
            self._photos[self._index]["export_path"] = new_export_path
        self.setFocus()

    def _reset_all(self) -> None:
        """Reset all sliders to defaults."""
        for slider in self._sliders.values():
            slider.reset()
        self._grade_combo.setCurrentIndex(0)
        self._canvas.clear_crop()
        self._crop_rect = None
        if self._crop_btn.isChecked():
            self._crop_btn.setChecked(False)
        # Reset expert tone curves
        for ch in self._curve_data:
            self._curve_data[ch] = [(0.0, 0.0), (1.0, 1.0)]
        self._tone_curve_widget.reset()
        # Reset color wheels
        self._color_wheels.reset()
        # Reset expert combos
        self._hl_recovery_combo.setCurrentIndex(0)
        self._demosaic_combo.setCurrentIndex(0)
        self._working_profile_combo.setCurrentIndex(0)
        self._output_profile_combo.setCurrentIndex(0)
        self._auto_lens_cb.setChecked(True)
        self._schedule_preview()
        self._commit_undo_state()

    # ------------------------------------------------------------------
    # Crop tool
    # ------------------------------------------------------------------

    def _toggle_crop_mode(self, enabled: bool) -> None:
        self._canvas.set_crop_mode(enabled)
        self._apply_crop_btn.setVisible(enabled)
        self._ai_crop_btn.setVisible(enabled)
        self._crop_ratio_combo.setVisible(enabled)
        self._clear_crop_btn.setVisible(enabled)
        if enabled and self._crop_rect:
            # Re-show existing crop rect on canvas when re-entering crop mode
            self._canvas.set_crop_rect(self._crop_rect)
            self._canvas._crop_mode = True
            self._canvas.update()

    def _on_crop_changed(self, rect: QRect) -> None:
        """Store crop rectangle for export."""
        self._crop_rect = rect

    def _on_rotate_changed(self, angle: float) -> None:
        """Update the rotation slider while the user drags in crop mode."""
        if "rotation" in self._sliders:
            self._sliders["rotation"].set_value(int(round(angle)))
            self._schedule_preview()

    def _apply_crop(self) -> None:
        """Apply current crop, update preview, and exit crop mode."""
        if self._crop_btn.isChecked():
            self._crop_btn.setChecked(False)
        self._schedule_preview()
        self._commit_undo_state()
        self.setFocus()

    def _clear_crop(self) -> None:
        """Remove the current crop and return to full image."""
        self._crop_rect = None
        self._canvas.clear_crop()
        if self._crop_btn.isChecked():
            self._crop_btn.setChecked(False)
        self._schedule_preview()
        self._commit_undo_state()
        self.setFocus()

    def _on_crop_ratio_changed(self, text: str) -> None:
        """Update canvas crop constraint when aspect ratio changes."""
        self._crop_aspect_ratio = text
        ratio_map = {
            "1:1": 1.0,
            "3:2": 3 / 2,
            "2:3": 2 / 3,
            "4:3": 4 / 3,
            "3:4": 3 / 4,
            "16:9": 16 / 9,
            "9:16": 9 / 16,
        }
        if text == "Original" and self._raw_rgb_preview is not None:
            h, w = self._raw_rgb_preview.shape[:2]
            forced = w / h if h > 0 else 0
        else:
            forced = ratio_map.get(text, 0)
        self._canvas.set_crop_aspect_ratio(forced)
        # If there's already a crop and user changes ratio, re-crop with new ratio
        if self._crop_rect and self._crop_rect.width() > 0:
            self._apply_ratio_to_crop(text)

    def _apply_ratio_to_crop(self, ratio_text: str) -> None:
        """Adjust the existing crop rect to match the chosen aspect ratio."""
        if not self._crop_rect or ratio_text == "Free":
            return
        cx, cy = self._crop_rect.x(), self._crop_rect.y()
        cw, ch = self._crop_rect.width(), self._crop_rect.height()
        if ratio_text == "Original" and self._raw_rgb_preview is not None:
            h, w = self._raw_rgb_preview.shape[:2]
            target = w / h if h > 0 else 1.0
        else:
            ratio_map = {
                "1:1": 1.0,
                "3:2": 3 / 2,
                "2:3": 2 / 3,
                "4:3": 4 / 3,
                "3:4": 3 / 4,
                "16:9": 16 / 9,
                "9:16": 9 / 16,
            }
            target = ratio_map.get(ratio_text, 0)
            if target == 0:
                return
        # Fit new ratio inside the current crop area
        current_ratio = cw / ch if ch > 0 else 1.0
        if current_ratio > target:
            new_w = int(ch * target)
            new_h = ch
        else:
            new_w = cw
            new_h = int(cw / target)
        # Center the new crop within the old one
        new_x = cx + (cw - new_w) // 2
        new_y = cy + (ch - new_h) // 2
        self._crop_rect = QRect(new_x, new_y, new_w, new_h)
        self._canvas.set_crop_rect(self._crop_rect)
        self._canvas.crop_changed.emit(self._crop_rect)

    def _ai_suggest_crop(self) -> None:
        """Run the AI auto-crop analyzer and apply the suggestion."""
        if not self._photos:
            return
        photo = self._photos[self._index]

        # Determine which ratio to target (from combo)
        ratio_text = self._crop_ratio_combo.currentText()
        target_ratio = None
        if ratio_text not in ("Free", "Original"):
            target_ratio = ratio_text

        # Try thumbnail first, fall back to file_path
        image_path = photo.get("thumbnail_path", "") or photo.get("file_path", "")
        if not image_path or not Path(image_path).is_file():
            return

        self._ai_crop_btn.setEnabled(False)
        self._ai_crop_btn.setText("⏳ Analyzing…")

        try:
            from imagic.services.auto_crop import analyze_crop

            result = analyze_crop(Path(image_path), target_ratio=target_ratio)

            if result.w > 0 and result.h > 0 and result.confidence > 0:
                # Scale crop from thumbnail/source dimensions to preview dimensions
                if self._raw_rgb_preview is not None:
                    ph, pw = self._raw_rgb_preview.shape[:2]
                elif self._raw_rgb is not None:
                    ph, pw = self._raw_rgb.shape[:2]
                else:
                    ph, pw = result.original_h, result.original_w
                sx = pw / result.original_w if result.original_w > 0 else 1.0
                sy = ph / result.original_h if result.original_h > 0 else 1.0
                scaled_rect = QRect(
                    int(result.x * sx),
                    int(result.y * sy),
                    int(result.w * sx),
                    int(result.h * sy),
                )
                self._crop_rect = scaled_rect
                self._canvas.set_crop_rect(scaled_rect)
                self._canvas._crop_mode = True
                self._canvas.update()
                self._canvas.crop_changed.emit(scaled_rect)
            else:
                # No significant crop suggested
                self._ai_crop_btn.setText("✓ No crop needed")
                QTimer.singleShot(2000, lambda: self._ai_crop_btn.setText("🤖 AI Crop"))
                self._ai_crop_btn.setEnabled(True)
                return
        except Exception as exc:
            logger.warning("AI crop failed: %s", exc)
            self._ai_crop_btn.setText("✗ Failed")
            QTimer.singleShot(2000, lambda: self._ai_crop_btn.setText("🤖 AI Crop"))
            self._ai_crop_btn.setEnabled(True)
            return

        self._ai_crop_btn.setText("🤖 AI Crop")
        self._ai_crop_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # AI Optimize-All
    # ------------------------------------------------------------------

    def _compute_user_style(self) -> dict | None:
        """Average the slider values from the first 10 manually-edited photos."""
        edited = []
        for p in self._photos[:10]:
            raw = p.get("manual_overrides", "")
            if not raw:
                continue
            try:
                overrides = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            # Only count if user actually changed something from defaults
            if any(v != 0 for k, v in overrides.items() if k != "color_grade"):
                edited.append(overrides)
        if not edited:
            return None
        # Average numeric slider values across edited photos
        avg: dict = {}
        keys = {k for d in edited for k in d if k != "color_grade"}
        for k in keys:
            vals = [d.get(k, 0) for d in edited if isinstance(d.get(k, 0), (int, float))]
            if vals:
                avg[k] = int(sum(vals) / len(vals))
        return avg if avg else None

    def _ai_optimize_all(self) -> None:
        """One-click AI optimization for ALL photos in the editor."""
        if not self._photos:
            return

        run = self._next_ai_run("optimize_all")

        # Save current photo's params before starting batch
        if self._raw_rgb is not None:
            current_params = self._gather_params()
            self._photos[self._index]["manual_overrides"] = json.dumps(current_params)

        # Learn from first 10 manually-edited photos
        user_style = self._compute_user_style()

        self._optimize_btn.setEnabled(False)
        self._optimize_btn.setText("⏳ Optimizing all…")

        # Show the AI loading overlay
        n = len(self._photos)
        if self._ai_modal:
            title = "AI Optimizing Photos" if run == 0 else f"AI Optimizing (variation {run})"
            self._ai_modal.show_message(title, f"Preparing {n} photos…", total=n)

        self._batch_optimize_run = run  # stash for _on_batch_photo_done
        self._batch_worker = _BatchOptimizeWorker(
            self._photos, self._rgb_cache, user_style=user_style, parent=self
        )
        self._batch_worker.photo_optimized.connect(self._on_batch_photo_done)
        self._batch_worker.finished_all.connect(self._on_batch_finished)
        self._batch_worker.start()

    def _on_batch_photo_done(self, index: int, suggestions: dict, rgb: np.ndarray) -> None:
        """A single photo was optimized by the batch worker."""
        # Cache the decoded RGB
        self._rgb_cache[index] = rgb

        # Apply variation if this is a re-run
        run = getattr(self, "_batch_optimize_run", 0)
        if run > 0:
            suggestions = vary_suggestions(suggestions, run, self._get_slider_bounds())

        # Build full params: start from defaults, apply suggestions
        params = {}
        for key in self._sliders:
            params[key] = 0  # default
        params["color_grade"] = "natural"
        for key, value in suggestions.items():
            if key in self._sliders:
                params[key] = int(value)

        # Store as overrides on the photo dict
        self._photos[index]["manual_overrides"] = json.dumps(params)

        n = len(self._photos)
        self._optimize_btn.setText(f"⏳ {index + 1}/{n}…")

        # Update loading overlay progress
        fname = self._photos[index].get("file_name", "")
        if self._ai_modal:
            self._ai_modal.set_progress(index + 1, n, fname)

        # If this is the currently displayed photo, update live
        if index == self._index:
            self._raw_rgb = rgb
            self._raw_rgb_preview = self._make_preview_proxy(rgb)
            self._apply_params(params)
            self._schedule_preview()
            self._commit_undo_state()

    def _on_batch_finished(self, error_count: int = 0) -> None:
        """All photos have been optimized."""
        self._optimize_btn.setEnabled(True)
        self._optimize_btn.setText("⚡ AI Optimize All")
        self._batch_worker = None
        if self._ai_modal:
            self._ai_modal.hide_modal()
        logger.info("Batch AI optimize complete for %d photos", len(self._photos))

        if error_count > 0:
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "Batch Optimize",
                f"Finished with {error_count} error(s). "
                f"Some photos could not be optimized \u2014 check the log for details.",
            )

    # ------------------------------------------------------------------
    # AI Tools
    # ------------------------------------------------------------------

    def _ai_auto_enhance(self) -> None:
        """Auto-enhance using AI analysis of the image."""
        if self._raw_rgb is None:
            return
        run = self._next_ai_run("enhance")
        if self._ai_modal:
            label = "AI Auto-Enhance" if run == 0 else f"AI Auto-Enhance (variation {run})"
            self._ai_modal.show_message(label, "Analysing image…")
        suggestions = ai_auto_enhance(self._raw_rgb)
        suggestions = vary_suggestions(suggestions, run, self._get_slider_bounds())
        self._apply_ai_suggestions(suggestions)
        if self._ai_modal:
            self._ai_modal.hide_modal()

    def _ai_auto_wb(self) -> None:
        """Auto white balance using grey-world assumption."""
        if self._raw_rgb is None:
            return
        run = self._next_ai_run("wb")
        if self._ai_modal:
            label = "AI White Balance" if run == 0 else f"AI White Balance (variation {run})"
            self._ai_modal.show_message(label, "Calculating colour correction…")
        suggestions = ai_auto_wb(self._raw_rgb)
        suggestions = vary_suggestions(suggestions, run, self._get_slider_bounds())
        self._apply_ai_suggestions(suggestions)
        if self._ai_modal:
            self._ai_modal.hide_modal()

    def _ai_denoise(self) -> None:
        """Aggressive AI denoise preset."""
        run = self._next_ai_run("denoise")
        if self._ai_modal:
            label = "AI Denoise" if run == 0 else f"AI Denoise (variation {run})"
            self._ai_modal.show_message(label, "Applying noise reduction…")
        base = {
            "nr_luminance": 60,
            "nr_color": 50,
            "sharp_amount": 30,
        }
        suggestions = vary_suggestions(base, run, self._get_slider_bounds())
        self._apply_ai_suggestions(suggestions)
        if self._ai_modal:
            self._ai_modal.hide_modal()

    def _ai_sharpen(self) -> None:
        """Smart sharpening based on image content."""
        if self._raw_rgb is None:
            return
        run = self._next_ai_run("sharpen")
        if self._ai_modal:
            label = "AI Smart Sharpen" if run == 0 else f"AI Smart Sharpen (variation {run})"
            self._ai_modal.show_message(label, "Analysing sharpness…")
        # Analyze sharpness
        try:
            from scipy.ndimage import laplace

            gray = np.mean(self._raw_rgb.astype(np.float32) / 255.0, axis=2)
            lap_var = float(np.var(laplace(gray)))
            if lap_var < 0.0003:
                amount = 100  # very soft
            elif lap_var < 0.001:
                amount = 60
            else:
                amount = 30
        except ImportError:
            amount = 50
        base = {"sharp_amount": amount, "sharp_radius": 60}
        suggestions = vary_suggestions(base, run, self._get_slider_bounds())
        self._apply_ai_suggestions(suggestions)
        if self._ai_modal:
            self._ai_modal.hide_modal()

    def _ai_bw(self) -> None:
        """AI black & white conversion with optimal tonal adjustment."""
        if self._raw_rgb is None:
            return
        run = self._next_ai_run("bw")
        if self._ai_modal:
            label = "AI B\u200a&\u200aW" if run == 0 else f"AI B\u200a&\u200aW (variation {run})"
            self._ai_modal.show_message(label, "Converting to black & white…")
        self._grade_combo.setCurrentText("bw_classic")
        base = {"saturation": -100, "contrast": 20, "clarity": 15}
        suggestions = vary_suggestions(base, run, self._get_slider_bounds())
        # Always keep saturation pinned to -100 for true B&W
        suggestions["saturation"] = -100
        self._apply_ai_suggestions(suggestions)
        if self._ai_modal:
            self._ai_modal.hide_modal()

    # ------------------------------------------------------------------
    # Advanced AI tools
    # ------------------------------------------------------------------

    def _ai_masking(self) -> None:
        """AI masking — select subject/sky/people/background."""
        if self._raw_rgb is None:
            return
        items = ["Subject", "Sky", "People", "Background"]
        choice, ok = QInputDialog.getItem(
            self,
            "AI Masking",
            "Select mask target:",
            items,
            0,
            False,
        )
        if not ok:
            return
        from imagic.ai.masking import _ensure_rembg

        if choice in ("Subject", "People", "Background") and not _ensure_rembg():
            QMessageBox.information(
                self,
                "AI Masking",
                "rembg is installed without a runtime backend, so masking will use fallback "
                "algorithms. Install rembg[cpu] for faster, higher-quality results.",
            )

        if self._ai_modal:
            self._ai_modal.show_message("AI Masking", f"Generating {choice.lower()} mask…")

        def _do_masking(rgb, target):
            from imagic.ai.masking import (
                MaskType,
                generate_background_mask,
                generate_people_mask,
                generate_sky_mask,
                generate_subject_mask,
            )

            mask_map = {
                "Subject": (MaskType.SUBJECT, generate_subject_mask),
                "Sky": (MaskType.SKY, generate_sky_mask),
                "People": (MaskType.PEOPLE, generate_people_mask),
                "Background": (MaskType.BACKGROUND, generate_background_mask),
            }
            mask_type, gen_fn = mask_map[target]
            result = gen_fn(rgb)
            if result is None:
                raise RuntimeError(
                    f"{target} mask generation failed — the required AI model "
                    f"is not available. Please install 'rembg' for subject/people/background masks."
                )
            # Build overlay preview
            overlay = rgb.copy()
            mask_3ch = np.stack([result.mask] * 3, axis=2)
            tint = np.zeros_like(overlay, dtype=np.float32)
            tint[:, :, 0] = 255  # red tint
            overlay = (
                (overlay.astype(np.float32) * (1 - mask_3ch * 0.4) + tint * mask_3ch * 0.4)
                .clip(0, 255)
                .astype(np.uint8)
            )
            return {
                "mask": result.mask,
                "mask_type": mask_type,
                "overlay": overlay,
                "confidence": result.confidence,
                "target": target,
            }

        def _on_masking_done(result_data):
            self._active_mask = result_data["mask"]
            self._active_mask_type = result_data["mask_type"]
            overlay = result_data["overlay"]
            h, w = overlay.shape[:2]
            qimg = QImage(overlay.data, w, h, 3 * w, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)
            self._canvas.set_pixmap(pixmap)
            if self._ai_modal:
                self._ai_modal.hide_modal()
            self._show_mask_tabs()
            QMessageBox.information(
                self,
                "AI Masking",
                f"{result_data['target']} mask generated (confidence: {result_data['confidence']:.0%}).\n"
                f"The red overlay shows the selected area.\n"
                f"Use the 'Masked Area' tab in the sidebar to edit the selected region.",
            )

        def _on_masking_error(msg):
            logger.error("AI masking failed: %s", msg)
            if self._ai_modal:
                self._ai_modal.hide_modal()
            QMessageBox.warning(self, "AI Masking", f"Masking failed: {msg}")

        worker = _AITaskWorker(_do_masking, self._raw_rgb.copy(), choice, parent=self)
        worker.finished.connect(_on_masking_done)
        worker.error.connect(_on_masking_error)
        self._stop_ai_worker()
        self._ai_worker = worker
        worker.start()

    # ------------------------------------------------------------------
    # Manual masking
    # ------------------------------------------------------------------

    def _manual_masking(self) -> None:
        """Open manual masking mode with brush/lasso/auto-detect tools."""
        if self._raw_rgb is None:
            return

        if not self._ensure_opencv_available("Manual Masking"):
            return

        # If already in mask paint mode, exit it
        if self._canvas._mask_paint_mode:
            self._finish_manual_masking()
            return

        # Use preview pixmap dimensions so widget↔image coordinate conversions
        # map correctly onto the mask canvas (raw image may be 4-8× larger).
        pix = self._canvas._pixmap
        if pix is not None and not pix.isNull():
            h, w = pix.height(), pix.width()
        else:
            h, w = self._raw_rgb.shape[:2]
        self._canvas.init_mask_canvas(h, w)
        self._canvas.set_mask_paint_mode(True, "brush")
        self._canvas.mask_updated.connect(self._on_manual_mask_updated)

        # Build floating toolbar
        self._mask_toolbar = QWidget(self)
        self._mask_toolbar.setStyleSheet(
            f"background: {_PANEL_BG_SOLID}; border: 1px solid {_BORDER}; border-radius: 6px; padding: 6px;"
        )
        tb_layout = QHBoxLayout(self._mask_toolbar)
        tb_layout.setContentsMargins(8, 4, 8, 4)
        tb_layout.setSpacing(6)

        # Tool buttons
        brush_btn = QPushButton("\U0001f58c Brush")
        brush_btn.setCheckable(True)
        brush_btn.setChecked(True)
        brush_btn.setStyleSheet(f"color: {_TEXT}; padding: 4px 8px;")
        lasso_btn = QPushButton("\u2702 Lasso")
        lasso_btn.setCheckable(True)
        lasso_btn.setStyleSheet(f"color: {_TEXT}; padding: 4px 8px;")
        auto_btn = QPushButton("\U0001f50d Auto-Detect")
        auto_btn.setStyleSheet(f"color: {_TEXT}; padding: 4px 8px;")

        def _set_brush():
            brush_btn.setChecked(True)
            lasso_btn.setChecked(False)
            self._canvas.set_mask_paint_mode(True, "brush")

        def _set_lasso():
            brush_btn.setChecked(False)
            lasso_btn.setChecked(True)
            self._canvas.set_mask_paint_mode(True, "lasso")

        brush_btn.clicked.connect(_set_brush)
        lasso_btn.clicked.connect(_set_lasso)
        auto_btn.clicked.connect(self._auto_detect_mask)

        tb_layout.addWidget(brush_btn)
        tb_layout.addWidget(lasso_btn)
        tb_layout.addWidget(auto_btn)

        # Separator
        sep = QLabel("|")
        sep.setStyleSheet(f"color: {_TEXT_DIM};")
        tb_layout.addWidget(sep)

        # Brush size
        size_label = QLabel("Size:")
        size_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
        tb_layout.addWidget(size_label)
        size_slider = QSlider(Qt.Orientation.Horizontal)
        size_slider.setRange(2, 200)
        size_slider.setValue(self._canvas._mask_brush_size)
        size_slider.setFixedWidth(80)
        size_slider.valueChanged.connect(lambda v: setattr(self._canvas, "_mask_brush_size", v))
        tb_layout.addWidget(size_slider)

        # Feather
        feather_label = QLabel("Feather:")
        feather_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
        tb_layout.addWidget(feather_label)
        feather_slider = QSlider(Qt.Orientation.Horizontal)
        feather_slider.setRange(0, 50)
        feather_slider.setValue(self._canvas._mask_feather)
        feather_slider.setFixedWidth(60)
        feather_slider.valueChanged.connect(lambda v: setattr(self._canvas, "_mask_feather", v))
        tb_layout.addWidget(feather_slider)

        # Separator
        sep2 = QLabel("|")
        sep2.setStyleSheet(f"color: {_TEXT_DIM};")
        tb_layout.addWidget(sep2)

        # Clear & Done buttons
        clear_btn = QPushButton("\U0001f5d1 Clear")
        clear_btn.setStyleSheet(f"color: {_TEXT}; padding: 4px 8px;")
        clear_btn.clicked.connect(lambda: (self._canvas.clear_mask_canvas(), self._canvas.update()))
        tb_layout.addWidget(clear_btn)

        done_btn = QPushButton("\u2705 Done")
        done_btn.setStyleSheet(f"color: {_ACCENT}; font-weight: bold; padding: 4px 10px;")
        done_btn.clicked.connect(self._finish_manual_masking)
        tb_layout.addWidget(done_btn)

        cancel_btn = QPushButton("\u2716 Cancel")
        cancel_btn.setStyleSheet(f"color: {_TEXT_DIM}; padding: 4px 8px;")
        cancel_btn.clicked.connect(self._cancel_manual_masking)
        tb_layout.addWidget(cancel_btn)

        # Position at top-center of canvas
        self._mask_toolbar.adjustSize()
        canvas_geo = self._canvas.geometry()
        toolbar_x = canvas_geo.x() + (canvas_geo.width() - self._mask_toolbar.width()) // 2
        toolbar_y = canvas_geo.y() + 10
        self._mask_toolbar.move(toolbar_x, toolbar_y)
        self._mask_toolbar.show()
        self._mask_toolbar.raise_()

    def _on_manual_mask_updated(self) -> None:
        """Update _active_mask from canvas paint."""
        mask = self._canvas.get_mask_canvas()
        if mask is not None:
            self._active_mask = mask.copy()
            self._active_mask_type = "manual"

    def _finish_manual_masking(self) -> None:
        """Exit mask paint mode and apply painted mask."""
        mask = self._canvas.get_mask_canvas()
        if mask is not None and mask.max() > 0:
            self._active_mask = mask.copy()
            self._active_mask_type = "manual"
            QMessageBox.information(
                self,
                "Manual Masking",
                "Mask applied. Use any edit slider to adjust the masked area.\n"
                "Right-click was eraser. The red overlay shows the selected region.",
            )
        self._canvas.set_mask_paint_mode(False)
        try:
            self._canvas.mask_updated.disconnect(self._on_manual_mask_updated)
        except TypeError:
            pass
        if hasattr(self, "_mask_toolbar") and self._mask_toolbar:
            self._mask_toolbar.hide()
            self._mask_toolbar.deleteLater()
            self._mask_toolbar = None
        if self._active_mask is not None:
            self._show_mask_tabs()
        # Refresh preview to remove overlay
        self._update_preview()

    def _cancel_manual_masking(self) -> None:
        """Cancel manual masking without applying."""
        self._canvas.clear_mask_canvas()
        self._canvas.set_mask_paint_mode(False)
        try:
            self._canvas.mask_updated.disconnect(self._on_manual_mask_updated)
        except TypeError:
            pass
        if hasattr(self, "_mask_toolbar") and self._mask_toolbar:
            self._mask_toolbar.hide()
            self._mask_toolbar.deleteLater()
            self._mask_toolbar = None
        self._update_preview()

    # ------------------------------------------------------------------
    # Mask segment tabs
    # ------------------------------------------------------------------

    def _show_mask_tabs(self) -> None:
        """Show the Global / Masked-Area tab bar when a mask becomes active."""
        if self._mask_tab_bar is None:
            return
        self._mask_global_params = self._gather_params()
        self._mask_area_params = {}
        self._mask_active_tab = 0
        self._mask_tab_bar.blockSignals(True)
        self._mask_tab_bar.setCurrentIndex(0)
        self._mask_tab_bar.blockSignals(False)
        self._mask_tab_bar.show()

    def _hide_mask_tabs(self) -> None:
        """Hide the tab bar and clear mask state."""
        if self._mask_tab_bar is not None:
            self._mask_tab_bar.hide()
        self._mask_global_params = None
        self._mask_area_params = {}
        self._mask_active_tab = 0

    def _on_mask_tab_changed(self, idx: int) -> None:
        """Switch between Global and Masked-Area edit contexts."""
        prev = self._mask_active_tab
        self._mask_active_tab = idx
        if prev == 0 and idx == 1:
            # Entering Masked Area — snapshot global params, load mask-area params
            self._mask_global_params = self._gather_params()
            if self._mask_area_params:
                self._apply_params(self._mask_area_params)
            else:
                for slider in self._sliders.values():
                    slider.reset()
        elif prev == 1 and idx == 0:
            # Returning to Global — snapshot mask-area params, restore global
            self._mask_area_params = self._gather_params()
            if self._mask_global_params:
                self._apply_params(self._mask_global_params)
        self._schedule_preview()

    def _auto_detect_mask(self) -> None:
        """Use GrabCut for auto-detect masking."""
        if self._raw_rgb is None:
            return
        if not self._ensure_opencv_available("Auto-Detect"):
            return
        import cv2

        h, w = self._raw_rgb.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        # Use a margin-based rect (exclude 5% borders)
        margin_x, margin_y = max(1, w // 20), max(1, h // 20)
        rect = (margin_x, margin_y, w - 2 * margin_x, h - 2 * margin_y)
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(self._raw_rgb, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)
        except cv2.error as e:
            QMessageBox.warning(self, "Auto-Detect", f"GrabCut failed: {e}")
            return
        # Convert GrabCut mask to float: foreground/probable foreground = 1
        result_mask = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1.0, 0.0).astype(
            np.float32
        )
        # Scale result to pixmap dimensions so it aligns with the canvas
        pix = self._canvas._pixmap
        if pix is not None and not pix.isNull():
            result_mask = cv2.resize(
                result_mask, (pix.width(), pix.height()), interpolation=cv2.INTER_LINEAR
            )
        # Apply to canvas if in mask paint mode
        if self._canvas._mask_paint_mode:
            self._canvas._mask_canvas = result_mask
            self._canvas.mask_updated.emit()
            self._canvas.update()
        else:
            self._active_mask = result_mask
            self._active_mask_type = "auto_detect"
            self._show_mask_tabs()

    def _ai_lens_blur(self) -> None:
        """AI lens blur / bokeh effect."""
        if self._raw_rgb is None:
            return
        amount, ok = QInputDialog.getInt(
            self,
            "AI Lens Blur",
            "Blur amount (1-100):",
            50,
            1,
            100,
        )
        if not ok:
            return
        if self._ai_modal:
            self._ai_modal.show_message("AI Lens Blur", "Estimating depth & applying bokeh…")

        def _do_blur(rgb, blur_amount):
            from imagic.ai.lens_blur import apply_lens_blur

            return apply_lens_blur(rgb, blur_amount=blur_amount)

        def _on_blur_done(result):
            self._raw_rgb = result.image
            self._schedule_preview()
            self._commit_undo_state()
            if self._ai_modal:
                self._ai_modal.hide_modal()

        def _on_blur_error(msg):
            logger.error("AI lens blur failed: %s", msg)
            if self._ai_modal:
                self._ai_modal.hide_modal()
            QMessageBox.warning(self, "AI Lens Blur", f"Lens blur failed: {msg}")

        worker = _AITaskWorker(_do_blur, self._raw_rgb.copy(), amount, parent=self)
        worker.finished.connect(_on_blur_done)
        worker.error.connect(_on_blur_error)
        self._stop_ai_worker()
        self._ai_worker = worker
        worker.start()

    def _ensure_opencv_available(self, feature: str) -> bool:
        try:
            import cv2  # noqa: F401
            return True
        except ImportError:
            QMessageBox.warning(
                self,
                feature,
                "OpenCV is required for this feature. Install opencv-python and restart imagic.",
            )
            return False

    def _ensure_face_detection_available(self) -> bool:
        from imagic.ai.face_detection import is_face_detection_available

        if is_face_detection_available():
            return True
        QMessageBox.warning(
            self,
            "Face Detection",
            "Face detection is unavailable. Install opencv-python and ensure OpenCV can "
            "access its Haar cascade files.",
        )
        return False

    def _ai_face_detect(self) -> None:
        """Detect faces and draw bounding boxes."""
        if self._raw_rgb is None:
            return
        if not self._ensure_face_detection_available():
            return
        if self._ai_modal:
            self._ai_modal.show_message("AI Face Detection", "Scanning for faces…")

        def _do_detect(rgb):
            from imagic.ai.face_detection import detect_faces

            result = detect_faces(rgb)
            if not result.faces:
                return {"faces": [], "overlay": None}
            overlay = rgb.copy()
            for face in result.faces:
                x, y, fw, fh = face.x, face.y, face.width, face.height
                # Draw green rectangle (2px thick)
                y1, y2 = max(0, y), min(rgb.shape[0], y + fh)
                x1, x2 = max(0, x), min(rgb.shape[1], x + fw)
                if y2 - y1 > 4 and x2 - x1 > 4:
                    overlay[y1 : y1 + 2, x1:x2] = [0, 255, 0]
                    overlay[y2 - 2 : y2, x1:x2] = [0, 255, 0]
                    overlay[y1:y2, x1 : x1 + 2] = [0, 255, 0]
                    overlay[y1:y2, x2 - 2 : x2] = [0, 255, 0]
            return {"faces": result.faces, "overlay": overlay}

        def _on_detect_done(result_data):
            if self._ai_modal:
                self._ai_modal.hide_modal()
            if not result_data["faces"]:
                QMessageBox.information(self, "Face Detection", "No faces detected.")
                return
            overlay = result_data["overlay"]
            oh, ow = overlay.shape[:2]
            qimg = QImage(overlay.data, ow, oh, 3 * ow, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)
            self._canvas.set_pixmap(pixmap)
            QMessageBox.information(
                self,
                "Face Detection",
                f"Detected {len(result_data['faces'])} face(s) — highlighted in green.",
            )

        def _on_detect_error(msg):
            logger.error("Face detection failed: %s", msg)
            if self._ai_modal:
                self._ai_modal.hide_modal()
            QMessageBox.warning(self, "Face Detection", f"Face detection failed: {msg}")

        worker = _AITaskWorker(_do_detect, self._raw_rgb.copy(), parent=self)
        worker.finished.connect(_on_detect_done)
        worker.error.connect(_on_detect_error)
        self._stop_ai_worker()
        self._ai_worker = worker
        worker.start()

    def _ai_super_resolution(self) -> None:
        """Upscale image using AI super resolution."""
        if self._raw_rgb is None:
            return
        scale, ok = QInputDialog.getItem(
            self,
            "AI Super Resolution",
            "Upscale factor:",
            ["2x", "4x"],
            0,
            False,
        )
        if not ok:
            return
        factor = int(scale[0])
        if self._ai_modal:
            self._ai_modal.show_message("AI Super Resolution", f"Upscaling {factor}x…")

        def _do_sr(rgb, scale_factor):
            from imagic.ai.super_resolution import enhance_resolution

            return enhance_resolution(rgb, scale=scale_factor)

        def _on_sr_done(result):
            self._raw_rgb = result.image
            self._schedule_preview()
            self._commit_undo_state()
            if self._ai_modal:
                self._ai_modal.hide_modal()
            QMessageBox.information(
                self,
                "Super Resolution",
                f"Upscaled {factor}x using {result.method}.\n"
                f"New size: {result.image.shape[1]}×{result.image.shape[0]}",
            )

        def _on_sr_error(msg):
            logger.error("Super resolution failed: %s", msg)
            if self._ai_modal:
                self._ai_modal.hide_modal()
            QMessageBox.warning(self, "Super Resolution", f"Super resolution failed: {msg}")

        worker = _AITaskWorker(_do_sr, self._raw_rgb.copy(), factor, parent=self)
        worker.finished.connect(_on_sr_done)
        worker.error.connect(_on_sr_error)
        self._stop_ai_worker()
        self._ai_worker = worker
        worker.start()

    def _ai_enhance_details(self) -> None:
        """Enhance fine details without upscaling."""
        if self._raw_rgb is None:
            return
        if self._ai_modal:
            self._ai_modal.show_message("AI Enhance Details", "Enhancing fine details…")

        def _do_enhance(rgb):
            from imagic.ai.super_resolution import enhance_details

            return enhance_details(rgb)

        def _on_enhance_done(result):
            self._raw_rgb = result
            self._schedule_preview()
            self._commit_undo_state()
            if self._ai_modal:
                self._ai_modal.hide_modal()

        def _on_enhance_error(msg):
            logger.error("Detail enhancement failed: %s", msg)
            if self._ai_modal:
                self._ai_modal.hide_modal()
            QMessageBox.warning(self, "Enhance Details", f"Detail enhancement failed: {msg}")

        worker = _AITaskWorker(_do_enhance, self._raw_rgb.copy(), parent=self)
        worker.finished.connect(_on_enhance_done)
        worker.error.connect(_on_enhance_error)
        self._stop_ai_worker()
        self._ai_worker = worker
        worker.start()

    def _ai_adaptive_preset(self) -> None:
        """Apply an AI adaptive preset based on scene detection."""
        if self._raw_rgb is None:
            return
        if self._ai_modal:
            self._ai_modal.show_message("AI Adaptive Preset", "Analysing scene…")

        def _do_adaptive(rgb):
            from imagic.ai.adaptive_presets import detect_scene, get_adaptive_preset

            scene = detect_scene(rgb)
            preset = get_adaptive_preset(scene)
            return {"preset": preset, "scene": scene}

        def _on_adaptive_done(result_data):
            preset = result_data["preset"]
            scene = result_data["scene"]
            self._apply_ai_suggestions(preset.global_params)
            if self._ai_modal:
                self._ai_modal.hide_modal()
            QMessageBox.information(
                self,
                "Adaptive Preset",
                f"Applied: {preset.name}\nScene detected: {scene.value}\n\n{preset.description}",
            )

        def _on_adaptive_error(msg):
            logger.error("Adaptive preset failed: %s", msg)
            if self._ai_modal:
                self._ai_modal.hide_modal()
            QMessageBox.warning(self, "Adaptive Preset", f"Adaptive preset failed: {msg}")

        worker = _AITaskWorker(_do_adaptive, self._raw_rgb.copy(), parent=self)
        worker.finished.connect(_on_adaptive_done)
        worker.error.connect(_on_adaptive_error)
        self._stop_ai_worker()
        self._ai_worker = worker
        worker.start()

    def _get_slider_bounds(self) -> dict[str, tuple]:
        """Return {key: (min, max)} for every registered slider."""
        return {key: (s._slider.minimum(), s._slider.maximum()) for key, s in self._sliders.items()}

    def _next_ai_run(self, tool: str) -> int:
        """Increment and return the run counter for *tool* on the current photo."""
        key = (self._index, tool)
        n = self._ai_run_counter.get(key, 0)
        self._ai_run_counter[key] = n + 1
        return n

    def _apply_ai_suggestions(self, suggestions: dict) -> None:
        """Apply AI-suggested values to sliders."""
        for key, value in suggestions.items():
            if key in self._sliders:
                self._sliders[key].set_value(int(value))
        self._schedule_preview()
        self._commit_undo_state()

    # ------------------------------------------------------------------
    # Copy / Paste edits
    # ------------------------------------------------------------------

    def _copy_edits(self) -> None:
        """Copy current slider parameters to clipboard."""
        if self._raw_rgb is None:
            return
        self._clipboard_params = self._gather_params()
        logger.info("Copied edit parameters to clipboard")

    def _paste_edits(self) -> None:
        """Paste previously copied parameters onto current image."""
        if self._clipboard_params is None or self._raw_rgb is None:
            return
        self._apply_params(self._clipboard_params)
        self._schedule_preview()
        self._commit_undo_state()
        logger.info("Pasted edit parameters from clipboard")

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def _show_preset_menu(self) -> None:
        """Show a dropdown menu with Save Preset + list of saved presets."""
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {_SECTION_BG}; color: {_TEXT}; border: 1px solid {_BORDER}; "
            f"border-radius: 6px; padding: 4px; font-size: 10px; }}"
            f"QMenu::item {{ padding: 6px 16px; border-radius: 3px; margin: 1px 3px; }}"
            f"QMenu::item:selected {{ background: {_ACCENT}; color: #111; }}"
            f"QMenu::separator {{ height: 1px; background: {_BORDER}; margin: 4px 8px; }}"
        )
        save_action = menu.addAction("💾  Save current as preset…")
        save_action.triggered.connect(self._save_preset)

        # List existing presets
        presets = sorted(self._presets_dir.glob("*.json"))
        if presets:
            menu.addSeparator()
            for p in presets:
                name = p.stem
                act = menu.addAction(f"  {name}")
                act.triggered.connect(lambda checked, path=p: self._load_preset(path))
            menu.addSeparator()
            del_menu = menu.addMenu("🗑  Delete preset")
            del_menu.setStyleSheet(menu.styleSheet())
            for p in presets:
                name = p.stem
                act = del_menu.addAction(name)
                act.triggered.connect(lambda checked, path=p: self._delete_preset(path))

        menu.exec(self._preset_btn.mapToGlobal(QPoint(0, self._preset_btn.height())))

    def _save_preset(self) -> None:
        """Save current edit parameters as a named preset."""
        name, ok = QInputDialog.getText(
            self,
            "Save Preset",
            "Preset name:",
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        # Sanitize filename
        safe_name = "".join(c for c in name if c.isalnum() or c in " _-").strip()
        if not safe_name:
            return
        path = self._presets_dir / f"{safe_name}.json"
        params = self._gather_params()
        path.write_text(json.dumps(params, indent=2), encoding="utf-8")
        logger.info("Saved preset: %s", safe_name)

    def _load_preset(self, path: Path) -> None:
        """Load a preset from disk and apply to current image."""
        if self._raw_rgb is None:
            return
        try:
            params = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load preset %s: %s", path.name, exc)
            return
        self._apply_params(params)
        self._schedule_preview()
        self._commit_undo_state()
        logger.info("Loaded preset: %s", path.stem)

    def _delete_preset(self, path: Path) -> None:
        """Delete a saved preset file."""
        try:
            path.unlink()
            logger.info("Deleted preset: %s", path.stem)
        except OSError as exc:
            logger.warning("Failed to delete preset %s: %s", path.name, exc)

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Z:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self._redo()
            else:
                self._undo()
        elif mods & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_C:
            self._copy_edits()
        elif mods & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_V:
            self._paste_edits()
        elif mods & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_S:
            self._save_all_edits()
        elif key == Qt.Key.Key_Right:
            self._navigate(1)
        elif key == Qt.Key.Key_Left:
            self._navigate(-1)
        elif key == Qt.Key.Key_Delete:
            self._trash_current()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._crop_btn.isChecked():
                self._apply_crop()
        elif key == Qt.Key.Key_Backslash:
            self._toggle_before_after()
        elif key == Qt.Key.Key_F:
            self._set_zoom(0)
        elif key == Qt.Key.Key_1:
            self._set_zoom(1.0)
        elif key == Qt.Key.Key_2:
            self._set_zoom(2.0)
        elif key == Qt.Key.Key_C:
            self._crop_btn.toggle()
        elif key == Qt.Key.Key_Escape:
            if self._crop_btn.isChecked():
                self._crop_btn.setChecked(False)
            else:
                self.closed.emit()
        else:
            super().keyPressEvent(event)

    def _trash_current(self) -> None:
        """Trash the current photo and remove it from the editor."""
        if not self._photos:
            return
        photo = self._photos[self._index]
        photo_id = photo.get("id", 0)
        photo["status"] = "trash"
        self.photo_trashed.emit(photo_id)

        # Remove from list
        self._photos.pop(self._index)
        if self._index in self._rgb_cache:
            del self._rgb_cache[self._index]
        # Re-key cache for indices after removed one
        new_cache: dict[int, np.ndarray] = {}
        for k, v in self._rgb_cache.items():
            if k > self._index:
                new_cache[k - 1] = v
            elif k < self._index:
                new_cache[k] = v
        self._rgb_cache = new_cache

        # Update filmstrip
        self._filmstrip.set_photos(self._photos)

        if not self._photos:
            self._title_label.setText("No photos")
            self._canvas.set_pixmap(QPixmap())
            return

        # Clamp index
        if self._index >= len(self._photos):
            self._index = len(self._photos) - 1
        self._load_photo(self._index)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._preview_pix:
            self._canvas.set_pixmap(self._preview_pix)

    # PhotoEditorDialog kept as a thin wrapper for backward compatibility


PhotoEditorDialog = PhotoEditorWidget
