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
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    QFont,
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
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
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
    QSplitter,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# ======================================================================
# Style constants
# ======================================================================

_BG = "#0d0d0d"
_PANEL_BG = "#1a1a1a"
_SECTION_BG = "#222"
_BORDER = "#333"
_TEXT = "#ddd"
_TEXT_DIM = "#888"
_ACCENT = "#ff9800"
_ACCENT_HOVER = "#ffa726"
_GREEN = "#4caf50"
_SLIDER_GROOVE = "#444"
_SLIDER_HANDLE = "#ff9800"
_SLIDER_SUB = "#ff9800"

_SLIDER_STYLE = (
    f"QSlider::groove:horizontal {{ height: 4px; background: {_SLIDER_GROOVE}; border-radius: 2px; }}"
    f"QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; "
    f"background: {_SLIDER_HANDLE}; border: 2px solid {_PANEL_BG}; border-radius: 7px; }}"
    f"QSlider::handle:horizontal:hover {{ background: {_ACCENT_HOVER}; }}"
    f"QSlider::sub-page:horizontal {{ background: {_SLIDER_SUB}; border-radius: 2px; }}"
)

_GRADES = [
    "natural", "film_warm", "film_cool", "moody",
    "vibrant", "cinematic", "faded", "bw_classic",
]

# -- Colour-grade engine (shared with native export) ----------------------
from imagic.services.preview_engine import (
    GRADE_LUT as _GRADE_LUT,
    GRADE_NULL as _GRADE_NULL,
    PreviewEngine,
    apply_color_grade as _apply_color_grade,
)
from imagic.services.editor_style_presets import get_editor_style_overrides



# ======================================================================
# Background RAW decoder
# ======================================================================


class _BatchOptimizeWorker(QThread):
    """Decode and AI-optimize all photos in the background."""

    # (index, suggestions_dict, rgb_array)
    photo_optimized = pyqtSignal(int, dict, object)
    finished_all = pyqtSignal()

    def __init__(self, photos: List[dict], rgb_cache: Dict[int, np.ndarray],
                 user_style: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self._photos = photos
        self._rgb_cache = rgb_cache
        self._user_style = user_style  # averaged edits from first N photos

    def run(self) -> None:
        import rawpy

        for i, p in enumerate(self._photos):
            try:
                # Use cache if available, otherwise decode
                if i in self._rgb_cache:
                    rgb = self._rgb_cache[i]
                else:
                    file_path = p.get("file_path", "")
                    if not file_path or not Path(file_path).is_file():
                        continue
                    with rawpy.imread(file_path) as raw:
                        rgb = raw.postprocess(
                            use_camera_wb=True,
                            no_auto_bright=True,
                            output_bps=8,
                            half_size=False,
                            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
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
                            suggestions["sharp_amount"] = 35
                        elif lap_var < 0.001:
                            suggestions["sharp_amount"] = 20
                        else:
                            suggestions["sharp_amount"] = 12
                        suggestions["sharp_radius"] = 45
                    except ImportError:
                        suggestions["sharp_amount"] = 20
                        suggestions["sharp_radius"] = 45
                else:
                    suggestions["sharp_amount"] = 10
                    suggestions["sharp_radius"] = 40

                # Blend user style from first N edited photos
                if self._user_style:
                    for k, v in self._user_style.items():
                        if k in suggestions:
                            # Weighted blend: 60% AI, 40% user style
                            suggestions[k] = int(suggestions[k] * 0.6 + v * 0.4)
                        else:
                            suggestions[k] = int(v)

                # Visual refinement: render a preview and verify quality
                suggestions = ai_visual_refine(rgb, suggestions)

                self.photo_optimized.emit(i, suggestions, rgb)
            except Exception as exc:
                logger.debug("Batch optimize failed for %s: %s", p.get("file_name", "?"), exc)

        self.finished_all.emit()

class _RawDecodeWorker(QThread):
    """Decode a RAW file to a numpy RGB array in the background."""

    decoded = pyqtSignal(int, object)  # index, numpy array (H, W, 3) uint8

    def __init__(self, index: int, file_path: str, half_size: bool = True, parent=None):
        super().__init__(parent)
        self._index = index
        self._file_path = file_path
        self._half_size = half_size

    def run(self) -> None:
        try:
            import rawpy
            with rawpy.imread(self._file_path) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                    half_size=self._half_size,
                    demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                    output_color=rawpy.ColorSpace.sRGB,
                )
            self.decoded.emit(self._index, rgb)
        except Exception as exc:
            logger.debug("RAW decode failed: %s", exc)


class _ThumbDecodeWorker(QThread):
    """Decode multiple thumbnail images for the film strip."""

    thumb_ready = pyqtSignal(int, QPixmap)  # index, pixmap

    def __init__(self, items: List[Tuple[int, str]], size: int = 80, parent=None):
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
    median_lum = float(np.median(lum))
    p99 = float(np.percentile(lum, 99))

    # Any image with mean luminance below 0.25 is "dark" — be conservative.
    # Most intentional dark scenes (clubs, concerts, night) fall here.
    is_dark = mean_lum < 0.25

    if is_dark:
        # Gentle lift: aim to improve visibility without destroying mood.
        # Scale the target based on how dark the image is.
        # Very dark (mean 0.02) → target ~0.06 (3x)
        # Moderately dark (mean 0.13) → target ~0.18 (1.4x)
        target = mean_lum + min(0.06, mean_lum * 0.5)

        if mean_lum < target - 0.01 and mean_lum > 0.001:
            ev_need = (target - mean_lum) / mean_lum
            params["exposure"] = int(np.clip(ev_need * 50, 0, 25))

        # Highlights recovery — protect any existing bright areas
        clip_hi = float(np.sum(lum > 0.92) / lum.size)
        if clip_hi > 0.005:
            params["highlights"] = int(max(-50, -clip_hi * 800))

        # Very gentle shadow lift only if extremely crushed
        clip_lo = float(np.sum(lum < 0.03) / lum.size)
        if clip_lo > 0.4:
            params["shadows"] = int(min(12, clip_lo * 15))

        # Minimal contrast for very flat dark images
        if std_lum < 0.08:
            params["contrast"] = 8

        # Dehaze to combat faded/hazy look in low-light
        params["dehaze"] = 15

    else:
        # Normal/bright image handling
        target = 0.42
        if mean_lum < 0.35:
            diff = target - mean_lum
            params["exposure"] = int(np.clip(diff * 100, 0, 30))
        elif mean_lum > 0.60:
            params["exposure"] = int(max(-35, (0.45 - mean_lum) * 100))

        if std_lum < 0.15:
            params["contrast"] = int(min(20, (0.18 - std_lum) * 150))
        elif std_lum > 0.28:
            params["contrast"] = int(max(-15, (0.22 - std_lum) * 100))

        clip_hi = float(np.sum(lum > 0.95) / lum.size)
        if clip_hi > 0.01:
            params["highlights"] = int(max(-50, -clip_hi * 800))

        clip_lo = float(np.sum(lum < 0.05) / lum.size)
        if clip_lo > 0.10:
            params["shadows"] = int(min(25, clip_lo * 100))

        # Vibrance for desaturated images
        mx = np.max(img, axis=2)
        mn = np.min(img, axis=2)
        sat = np.where(mx > 0, (mx - mn) / (mx + 1e-6), 0)
        mean_sat = float(np.mean(sat))
        if mean_sat < 0.15:
            params["vibrance"] = int(min(25, (0.18 - mean_sat) * 150))

        if std_lum < 0.18:
            params["clarity"] = 10

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
        self, label: str, lo: int, hi: int, default: int = 0,
        suffix: str = "", parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._default = default
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(6)

        self._label = QLabel(label)
        self._label.setFixedWidth(100)
        self._label.setStyleSheet(f"color: {_TEXT}; font-size: 11px;")
        layout.addWidget(self._label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(lo, hi)
        self._slider.setValue(default)
        self._slider.setStyleSheet(_SLIDER_STYLE)
        layout.addWidget(self._slider, stretch=1)

        self._val = QLabel(str(default))
        self._val.setFixedWidth(36)
        self._val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val.setStyleSheet(f"color: {_TEXT}; font-size: 11px;")
        layout.addWidget(self._val)

        self._slider.valueChanged.connect(self._on_changed)
        self._slider.sliderReleased.connect(self._on_released)

    def _on_changed(self, v: int) -> None:
        self._val.setText(str(v))
        self.value_changed.emit()

    def _on_released(self) -> None:
        self.released.emit()

    def value(self) -> int:
        return self._slider.value()

    def set_value(self, v: int) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(v)
        self._val.setText(str(v))
        self._slider.blockSignals(False)

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

        # Header
        self._header = QPushButton(f"▼  {title}")
        self._header.setStyleSheet(
            f"QPushButton {{ background: {_SECTION_BG}; color: {_ACCENT}; "
            f"font-weight: bold; font-size: 11px; text-align: left; "
            f"padding: 8px 12px; border: none; border-bottom: 1px solid #2a2a2a; "
            f"letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: #282828; }}"
        )
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.clicked.connect(self._toggle)
        self._title = title
        layout.addWidget(self._header)

        # Content container
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(12, 8, 12, 8)
        self._content_layout.setSpacing(3)
        layout.addWidget(self._content)

    def add_widget(self, w: QWidget) -> None:
        self._content_layout.addWidget(w)

    def add_layout(self, lay: QLayout) -> None:
        self._content_layout.addLayout(lay)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        arrow = "▼" if self._expanded else "▶"
        self._header.setText(f"{arrow}  {self._title}")


class _HistogramWidget(QWidget):
    """Live RGB histogram display."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(100)
        self.setMinimumWidth(180)
        self._data: Optional[np.ndarray] = None

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
        p.fillRect(self.rect(), QColor("#111"))

        if self._data is None:
            p.setPen(QColor(_TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data")
            p.end()
            return

        w, h = self.width(), self.height()
        colors = [(QColor(220, 60, 60, 100), 0), (QColor(60, 180, 60, 100), 1), (QColor(60, 100, 220, 100), 2)]

        max_val = 1
        hists = []
        for _, ch in colors:
            hist, _ = np.histogram(self._data[:, :, ch].ravel(), bins=128, range=(0, 255))
            hists.append(hist)
            max_val = max(max_val, hist.max())

        for (color, _), hist in zip(colors, hists):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            bin_w = w / 128
            for i, count in enumerate(hist):
                bar_h = int(count / max_val * (h - 4))
                x = int(i * bin_w)
                p.drawRect(x, h - bar_h, max(1, int(bin_w)), bar_h)

        # Luminance overlay
        lum = (0.2126 * self._data[:, :, 0] + 0.7152 * self._data[:, :, 1] + 0.0722 * self._data[:, :, 2]).ravel()
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background: {_BG};")

        self._pixmap: Optional[QPixmap] = None
        self._zoom: float = 0  # 0 = fit
        self._pan_offset = QPoint(0, 0)
        self._pan_start: Optional[QPoint] = None
        self._pan_start_offset = QPoint(0, 0)

        # Crop state
        self._crop_mode = False
        self._crop_rect: Optional[QRect] = None  # in image coordinates
        self._crop_dragging = False
        self._crop_start: Optional[QPoint] = None

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

    def get_crop_rect(self) -> Optional[QRect]:
        return self._crop_rect

    def clear_crop(self) -> None:
        self._crop_rect = None
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

        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._crop_mode:
                self._crop_dragging = True
                self._crop_start = self._widget_to_image(event.pos())
                self._crop_rect = None
                self.update()
            elif self._zoom != 0:  # Pan mode when zoomed in
                self._pan_start = event.pos()
                self._pan_start_offset = QPoint(self._pan_offset)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._crop_dragging and self._crop_start is not None:
            current = self._widget_to_image(event.pos())
            self._crop_rect = QRect(self._crop_start, current).normalized()
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
                    self.setCursor(Qt.CursorShape.OpenHandCursor if self._zoom != 0 else Qt.CursorShape.ArrowCursor)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta > 0:
            new_zoom = min(4.0, (self._zoom or 0.5) * 1.15)
        else:
            new_zoom = max(0.1, (self._zoom or 0.5) / 1.15)
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
        self._img_label.setStyleSheet("background: #1a1a1a; border: 1px solid #444; border-radius: 4px;")
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
        border = _ACCENT if selected else "#444"
        width = 2 if selected else 1
        self._img_label.setStyleSheet(
            f"background: #1a1a1a; border: {width}px solid {border}; border-radius: 4px;"
        )

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._index)


class _FilmStrip(QWidget):
    """Horizontal scrollable film strip for photo navigation."""

    photo_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(100)
        self.setStyleSheet(f"background: {_BG}; border-top: 1px solid {_BORDER};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:horizontal { height: 6px; background: transparent; }"
            f"QScrollBar::handle:horizontal {{ background: #444; border-radius: 3px; min-width: 30px; }}"
            f"QScrollBar::handle:horizontal:hover {{ background: #555; }}"
        )
        layout.addWidget(self._scroll)

        self._container = QWidget()
        self._strip_layout = QHBoxLayout(self._container)
        self._strip_layout.setContentsMargins(0, 0, 0, 0)
        self._strip_layout.setSpacing(3)
        self._strip_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._scroll.setWidget(self._container)

        self._items: List[_FilmStripItem] = []
        self._current = -1
        self._thumb_worker: Optional[_ThumbDecodeWorker] = None

    def set_photos(self, photos: List[dict]) -> None:
        """Populate the strip with photo list."""
        # Clear
        for item in self._items:
            self._strip_layout.removeWidget(item)
            item.deleteLater()
        self._items.clear()

        thumb_tasks: List[Tuple[int, str]] = []
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
    edits_saved = pyqtSignal(list)  # list of (photo_id, overrides_dict)
    photo_trashed = pyqtSignal(int)  # photo_id
    closed = pyqtSignal()

    def __init__(
        self,
        photo_list: List[dict] | None = None,
        current_index: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {_BG};")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._photos = photo_list or []
        self._index = current_index
        self._raw_rgb: Optional[np.ndarray] = None  # full-res decoded RAW
        self._raw_rgb_preview: Optional[np.ndarray] = None  # downscaled for fast preview
        self._preview_pix: Optional[QPixmap] = None
        self._show_before = False
        self._decode_worker: Optional[_RawDecodeWorker] = None
        self._rgb_cache: Dict[int, np.ndarray] = {}

        self._crop_rect: Optional[QRect] = None

        # Undo / Redo stacks
        self._undo_stack: List[dict] = []
        self._redo_stack: List[dict] = []
        self._last_committed_params: Optional[dict] = None

        # Clipboard for copy/paste edits
        self._clipboard_params: Optional[dict] = None

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
        self._batch_worker: Optional[_BatchOptimizeWorker] = None

        self._build_ui()
        if self._photos:
            self._load_photo(self._index)

    def set_photos(self, photo_list: List[dict], current_index: int = 0) -> None:
        """Update the photo list (used when embedding in single-window workflow)."""
        self._photos = photo_list
        self._rgb_cache.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._last_committed_params = None
        self._filmstrip.set_photos(photo_list)
        if photo_list:
            self._load_photo(current_index)

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
            f"QSplitter::handle:hover {{ background: {_ACCENT}; }}"
        )

        # Left sidebar: histogram
        left = QWidget()
        left.setFixedWidth(220)
        left.setStyleSheet(f"background: {_PANEL_BG};")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 10, 8, 10)
        left_layout.setSpacing(10)

        hist_label = QLabel("HISTOGRAM")
        hist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hist_label.setStyleSheet(f"color: {_ACCENT}; font-weight: bold; font-size: 10px; letter-spacing: 2px;")
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
        zoom_lbl.setStyleSheet(f"color: {_ACCENT}; font-weight: bold; font-size: 10px; letter-spacing: 2px;")
        zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(zoom_lbl)

        zoom_row = QHBoxLayout()
        for label, factor in [("Fit", 0), ("50%", 0.5), ("100%", 1.0), ("200%", 2.0)]:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                f"QPushButton {{ background: #222; color: {_TEXT}; "
                f"border: 1px solid #444; border-radius: 4px; font-size: 10px; padding: 2px 8px; }}"
                f"QPushButton:hover {{ background: #2a2a2a; border-color: #555; }}"
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
        shortcuts_lbl.setStyleSheet(f"color: {_ACCENT}; font-weight: bold; font-size: 10px; letter-spacing: 2px;")
        left_layout.addWidget(shortcuts_lbl)

        shortcuts_text = QLabel(
            "← →  Navigate photos\n"
            "\\     Before / After\n"
            "F     Fit to screen\n"
            "1     100% zoom\n"
            "2     200% zoom\n"
            "C     Toggle crop\n"
            "Enter Apply crop\n"
            "Del   Trash photo\n"
            "Ctrl+Z/Y  Undo/Redo\n"
            "Ctrl+C/V  Copy/Paste\n"
            "Ctrl+S  Save all edits\n"
            "Scroll  Zoom in/out\n"
            "Esc   Close editor"
        )
        shortcuts_text.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px; line-height: 1.4;")
        left_layout.addWidget(shortcuts_text)

        main_splitter.addWidget(left)

        # Center: image preview canvas with pan/drag and crop support
        self._canvas = _PreviewCanvas()
        self._canvas.crop_changed.connect(self._on_crop_changed)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
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
            f"QScrollArea {{ border: none; background: {_PANEL_BG}; }}"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            f"QScrollBar::handle:vertical {{ background: #444; border-radius: 3px; min-height: 30px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: #555; }}"
        )

        panels_widget = QWidget()
        self._panels_layout = QVBoxLayout(panels_widget)
        self._panels_layout.setContentsMargins(0, 0, 0, 0)
        self._panels_layout.setSpacing(0)

        self._sliders: Dict[str, _SliderRow] = {}
        self._build_panels()

        self._panels_layout.addStretch()
        scroll.setWidget(panels_widget)
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
        toolbar.setFixedHeight(46)
        toolbar.setStyleSheet(
            f"background: #141414; border-bottom: 1px solid {_BORDER};"
        )
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        # Nav buttons
        btn_style = (
            f"QPushButton {{ background: #222; color: {_TEXT}; "
            f"border: 1px solid #444; border-radius: 6px; padding: 5px 14px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; border-color: #555; }}"
        )

        self._prev_btn = QPushButton("◀ Prev")
        self._prev_btn.setStyleSheet(btn_style)
        self._prev_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._prev_btn.clicked.connect(lambda: self._navigate(-1))
        layout.addWidget(self._prev_btn)

        self._title_label = QLabel()
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setStyleSheet(f"color: {_TEXT}; font-size: 12px; font-weight: bold;")
        layout.addWidget(self._title_label, stretch=1)

        self._next_btn = QPushButton("Next ▶")
        self._next_btn.setStyleSheet(btn_style)
        self._next_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._next_btn.clicked.connect(lambda: self._navigate(1))
        layout.addWidget(self._next_btn)

        layout.addSpacing(20)

        # AI Optimize-All button
        self._optimize_btn = QPushButton("⚡ AI Optimize All")
        self._optimize_btn.setStyleSheet(
            f"QPushButton {{ background: {_GREEN}; color: #fff; font-weight: bold; "
            f"border: none; border-radius: 6px; padding: 5px 16px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #66bb6a; }}"
            f"QPushButton:pressed {{ background: #388e3c; }}"
        )
        self._optimize_btn.setToolTip("One-click AI: auto-enhance, white balance, denoise, sharpen")
        self._optimize_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._optimize_btn.clicked.connect(self._ai_optimize_all)
        layout.addWidget(self._optimize_btn)

        # Crop tool toggle
        self._crop_btn = QPushButton("✂ Crop")
        self._crop_btn.setCheckable(True)
        self._crop_btn.setStyleSheet(
            f"QPushButton {{ background: #222; color: {_TEXT}; "
            f"border: 1px solid #444; border-radius: 6px; padding: 5px 14px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; border-color: #555; }}"
            f"QPushButton:checked {{ background: {_ACCENT}; color: #111; font-weight: bold; border-color: {_ACCENT}; }}"
        )
        self._crop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._crop_btn.toggled.connect(self._toggle_crop_mode)
        layout.addWidget(self._crop_btn)

        # Apply Crop button (hidden until crop mode enabled)
        self._apply_crop_btn = QPushButton("✓ Apply Crop")
        self._apply_crop_btn.setStyleSheet(
            f"QPushButton {{ background: {_GREEN}; color: #fff; font-weight: bold; "
            f"border: none; border-radius: 6px; padding: 5px 14px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #66bb6a; }}"
        )
        self._apply_crop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_crop_btn.clicked.connect(self._apply_crop)
        self._apply_crop_btn.setVisible(False)
        layout.addWidget(self._apply_crop_btn)

        # Before/After toggle
        self._ba_btn = QPushButton("Before / After  [\\]")
        self._ba_btn.setStyleSheet(
            f"QPushButton {{ background: #222; color: {_TEXT}; "
            f"border: 1px solid #444; border-radius: 6px; padding: 5px 14px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; border-color: #555; }}"
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
            f"QPushButton {{ background: #222; color: {_TEXT_DIM}; "
            f"border: 1px solid #444; border-radius: 6px; padding: 5px 14px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; color: {_TEXT}; border-color: #555; }}"
        )
        reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reset_btn.clicked.connect(self._reset_all)
        layout.addWidget(reset_btn)

        layout.addSpacing(20)

        # Save All Edits (persist to DB without exporting)
        self._save_btn = QPushButton("💾 Save All Edits")
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {_GREEN}; color: #fff; font-weight: bold; "
            f"border: none; border-radius: 6px; padding: 7px 16px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #66bb6a; }}"
            f"QPushButton:pressed {{ background: #388e3c; }}"
            f"QPushButton:disabled {{ background: #333; color: #666; }}"
        )
        self._save_btn.setToolTip("Save all edits to database (Ctrl+S)")
        self._save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._save_btn.clicked.connect(self._save_all_edits)
        layout.addWidget(self._save_btn)

        # Apply & Export
        self._apply_btn = QPushButton("  Apply && Export  ")
        self._apply_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #111; font-weight: bold; "
            f"border: none; border-radius: 6px; padding: 7px 22px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {_ACCENT_HOVER}; }}"
            f"QPushButton:pressed {{ background: #f57c00; }}"
            f"QPushButton:disabled {{ background: #333; color: #666; }}"
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
            f"QScrollArea {{ border: none; background: transparent; }}"
            f"QScrollArea > QWidget > QWidget {{ background: transparent; }}"
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
            f"QPushButton {{ background: #222; color: {_TEXT}; "
            f"border: 1px solid #444; border-radius: 4px; padding: 4px 10px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; border-color: #555; }}"
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
            f"border: none; border-radius: 4px; padding: 4px 10px; font-size: 10px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #66bb6a; }}"
        )
        self._grade_apply_all.setToolTip("Apply current color grade + intensity to all photos in batch")
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
        # Saturation sub-group
        sat_lbl = QLabel("Saturation")
        sat_lbl.setStyleSheet(f"color: {_ACCENT}; font-size: 10px; font-weight: bold;")
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
        lum_lbl.setStyleSheet(f"color: {_ACCENT}; font-size: 10px; font-weight: bold;")
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

        # ═══════════ AI TOOLS ═══════════
        sec = _CollapsibleSection("AI TOOLS")

        ai_btn_style = (
            f"QPushButton {{ background: #2a2a2a; color: {_TEXT}; "
            f"border: 1px solid {_BORDER}; border-radius: 4px; padding: 6px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {_ACCENT}; color: #111; }}"
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

        self._panels_layout.addWidget(sec)

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
            thumb = p.get("thumbnail_path", "")
            if thumb and Path(thumb).is_file():
                pix = QPixmap(thumb)
                if not pix.isNull():
                    self._set_preview_pixmap(pix)

            # Start full-resolution decode
            file_path = p.get("file_path", "")
            if file_path and Path(file_path).is_file():
                self._decode_worker = _RawDecodeWorker(index, file_path, half_size=False, parent=self)
                self._decode_worker.decoded.connect(self._on_raw_decoded)
                self._decode_worker.start()

    @staticmethod
    def _make_preview_proxy(rgb: np.ndarray, max_dim: int = 1800) -> np.ndarray:
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

    def _on_raw_decoded(self, index: int, rgb: np.ndarray) -> None:
        """RAW decode complete — cache and show preview."""
        self._rgb_cache[index] = rgb
        if index == self._index:
            self._raw_rgb = rgb
            self._raw_rgb_preview = self._make_preview_proxy(rgb)
            self._optimize_btn.setEnabled(True)
            self._optimize_btn.setText("⚡ AI Optimize All")
            self._rebuild_grade_thumbnails()
            self._update_preview()

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
                rendered.data.tobytes(), thumb_w, thumb_h,
                3 * thumb_w, QImage.Format.Format_RGB888,
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
            name_label.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 8px; background: transparent;")
            name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            btn_layout.addWidget(name_label)

            is_selected = (name == current_grade)
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
                f"QPushButton {{ background: #333; border: 2px solid {_ACCENT}; border-radius: 4px; }}"
                f"QPushButton:hover {{ background: #3a3a3a; }}"
            )
        return (
            f"QPushButton {{ background: #222; border: 1px solid {_BORDER}; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: #333; }}"
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
        self._grade_apply_one.setText("✓ Saved!")
        QTimer.singleShot(1500, lambda: self._grade_apply_one.setText("Apply to This Photo"))

    def _grade_save_all(self) -> None:
        """Apply current color grade + intensity to all photos in the batch."""
        grade = self._grade_combo.currentText()
        intensity = self._grade_intensity.value()
        for p in self._photos:
            raw = p.get("manual_overrides", "{}")
            try:
                ov = json.loads(raw) if isinstance(raw, str) else (raw if raw else {})
            except (json.JSONDecodeError, TypeError):
                ov = {}
            ov["color_grade"] = grade
            ov["color_grade_intensity"] = intensity
            p["manual_overrides"] = json.dumps(ov)
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

        grade = overrides.get("color_grade", "natural")
        idx = self._grade_combo.findText(grade)
        if idx >= 0:
            self._grade_combo.blockSignals(True)
            self._grade_combo.setCurrentIndex(idx)
            self._grade_combo.blockSignals(False)
        intensity = overrides.get("color_grade_intensity", 100)
        self._grade_intensity.set_value(int(intensity))
        self._highlight_selected_grade(grade)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _gather_params(self) -> dict:
        """Collect all slider values into a params dict."""
        params = {}
        for key, slider in self._sliders.items():
            params[key] = slider.value()
        params["color_grade"] = self._grade_combo.currentText()
        params["color_grade_intensity"] = self._grade_intensity.value()
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

    def _update_preview(self) -> None:
        """Apply current adjustments to the RAW data and display."""
        if self._raw_rgb is None:
            return

        source = self._raw_rgb

        if self._show_before:
            result = source
        else:
            params = self._gather_params()
            result = PreviewEngine.apply(source, params)

        # Update histogram
        self._histogram.update_histogram(result)

        # Convert to QPixmap
        h, w = result.shape[:2]
        bytes_per_line = 3 * w
        qimg = QImage(
            result.data.tobytes(), w, h, bytes_per_line,
            QImage.Format.Format_RGB888,
        )
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
                f"QPushButton {{ background: {_ACCENT}; color: #111; font-weight: bold; "
                f"border: none; border-radius: 6px; padding: 5px 14px; font-size: 11px; }}"
            )
        else:
            self._ba_btn.setText("Before / After  [\\]")
            self._ba_btn.setStyleSheet(
                f"QPushButton {{ background: #2a2a2a; color: {_TEXT}; "
                f"border: 1px solid {_BORDER}; border-radius: 6px; padding: 5px 14px; font-size: 11px; }}"
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
        if self._crop_rect and self._crop_rect.width() > 0:
            params["crop_x"] = self._crop_rect.x()
            params["crop_y"] = self._crop_rect.y()
            params["crop_w"] = self._crop_rect.width()
            params["crop_h"] = self._crop_rect.height()
        self._photos[self._index]["manual_overrides"] = json.dumps(params)

    def _save_all_edits(self) -> None:
        """Save all edits to the database without exporting."""
        self._save_current_to_dict()
        batch = []
        for p in self._photos:
            pid = p.get("id", 0)
            raw = p.get("manual_overrides", "")
            if not raw:
                continue
            try:
                overrides = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            # Only save if user actually changed something
            if any(v != 0 for k, v in overrides.items() if k != "color_grade"):
                batch.append((pid, overrides))
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
        """Emit the current overrides for export."""
        params = self._gather_params()
        photo = self._photos[self._index]
        photo_id = photo.get("id", 0)

        # Include crop data if set
        if self._crop_rect and self._crop_rect.width() > 0:
            params["crop_x"] = self._crop_rect.x()
            params["crop_y"] = self._crop_rect.y()
            params["crop_w"] = self._crop_rect.width()
            params["crop_h"] = self._crop_rect.height()

        # Store locally so navigation remembers the edit
        photo["manual_overrides"] = json.dumps(params)

        self._apply_btn.setEnabled(False)
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
        if self._crop_btn.isChecked():
            self._crop_btn.setChecked(False)
        self._schedule_preview()
        self._commit_undo_state()

    # ------------------------------------------------------------------
    # Crop tool
    # ------------------------------------------------------------------

    def _toggle_crop_mode(self, enabled: bool) -> None:
        self._canvas.set_crop_mode(enabled)
        self._apply_crop_btn.setVisible(enabled)

    def _on_crop_changed(self, rect: QRect) -> None:
        """Store crop rectangle for export."""
        self._crop_rect = rect

    def _apply_crop(self) -> None:
        """Apply current crop, update preview, and exit crop mode."""
        if self._crop_btn.isChecked():
            self._crop_btn.setChecked(False)
        self._schedule_preview()
        self._commit_undo_state()
        self.setFocus()

    # ------------------------------------------------------------------
    # AI Optimize-All
    # ------------------------------------------------------------------

    def _compute_user_style(self) -> Optional[dict]:
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

        # Save current photo's params before starting batch
        if self._raw_rgb is not None:
            current_params = self._gather_params()
            self._photos[self._index]["manual_overrides"] = json.dumps(current_params)

        # Learn from first 10 manually-edited photos
        user_style = self._compute_user_style()

        self._optimize_btn.setEnabled(False)
        self._optimize_btn.setText("⏳ Optimizing all…")
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

        # If this is the currently displayed photo, update live
        if index == self._index:
            self._raw_rgb = rgb
            self._raw_rgb_preview = self._make_preview_proxy(rgb)
            self._apply_params(params)
            self._schedule_preview()
            self._commit_undo_state()

    def _on_batch_finished(self) -> None:
        """All photos have been optimized."""
        self._optimize_btn.setEnabled(True)
        self._optimize_btn.setText("⚡ AI Optimize All")
        self._batch_worker = None
        logger.info("Batch AI optimize complete for %d photos", len(self._photos))

    # ------------------------------------------------------------------
    # AI Tools
    # ------------------------------------------------------------------

    def _ai_auto_enhance(self) -> None:
        """Auto-enhance using AI analysis of the image."""
        if self._raw_rgb is None:
            return
        suggestions = ai_auto_enhance(self._raw_rgb)
        self._apply_ai_suggestions(suggestions)

    def _ai_auto_wb(self) -> None:
        """Auto white balance using grey-world assumption."""
        if self._raw_rgb is None:
            return
        suggestions = ai_auto_wb(self._raw_rgb)
        self._apply_ai_suggestions(suggestions)

    def _ai_denoise(self) -> None:
        """Aggressive AI denoise preset."""
        self._apply_ai_suggestions({
            "nr_luminance": 60,
            "nr_color": 50,
            "sharp_amount": 30,  # compensate for softening
        })

    def _ai_sharpen(self) -> None:
        """Smart sharpening based on image content."""
        if self._raw_rgb is None:
            return
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
        self._apply_ai_suggestions({
            "sharp_amount": amount,
            "sharp_radius": 60,
        })

    def _ai_bw(self) -> None:
        """AI black & white conversion with optimal tonal adjustment."""
        if self._raw_rgb is None:
            return
        self._grade_combo.setCurrentText("bw_classic")
        self._apply_ai_suggestions({
            "saturation": -100,
            "contrast": 20,
            "clarity": 15,
        })

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
            f"QMenu {{ background: #2a2a2a; color: {_TEXT}; border: 1px solid {_BORDER}; "
            f"padding: 4px; font-size: 11px; }}"
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

        menu.exec(self._preset_btn.mapToGlobal(
            QPoint(0, self._preset_btn.height())
        ))

    def _save_preset(self) -> None:
        """Save current edit parameters as a named preset."""
        name, ok = QInputDialog.getText(
            self, "Save Preset", "Preset name:",
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
        new_cache: Dict[int, np.ndarray] = {}
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
