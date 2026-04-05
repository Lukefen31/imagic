"""Three-way color wheels widget for shadow / midtone / highlight grading.

Each wheel lets the user drag a point inside a circle to pick a hue and
saturation. A luminance slider sits below each wheel. The widget emits
``wheels_changed`` whenever any value changes.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QConicalGradient,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

# Style constants (match photo_editor theme)
_BG = "#12121a"
_RING_BG = "#1a1a28"
_TEXT = "#e0e0e8"
_TEXT_DIM = "#8888a0"
_ACCENT = "#ff9800"
_SLIDER_STYLE = (
    "QSlider::groove:horizontal { height: 4px; background: #3a3a4a; border-radius: 2px; }"
    "QSlider::handle:horizontal { width: 12px; height: 12px; margin: -4px 0; "
    f"background: {_ACCENT}; border-radius: 6px; }}"
    f"QSlider::sub-page:horizontal {{ background: {_ACCENT}; border-radius: 2px; }}"
)

_WHEEL_SIZE = 100  # diameter in pixels
_KNOB_RADIUS = 5


class _SingleWheel(QWidget):
    """One color wheel with a draggable knob and luminance slider."""

    changed = pyqtSignal()

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        # Normalised knob position (-1..1, -1..1) from center
        self._kx = 0.0
        self._ky = 0.0
        self._dragging = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 9px; font-weight: bold;")
        layout.addWidget(lbl)

        # Canvas area for wheel
        self._canvas = QWidget()
        self._canvas.setFixedSize(_WHEEL_SIZE, _WHEEL_SIZE)
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        layout.addWidget(self._canvas, alignment=Qt.AlignmentFlag.AlignCenter)

        # Luminance slider
        self._lum_slider = QSlider(Qt.Orientation.Horizontal)
        self._lum_slider.setRange(-100, 100)
        self._lum_slider.setValue(0)
        self._lum_slider.setFixedWidth(_WHEEL_SIZE)
        self._lum_slider.setStyleSheet(_SLIDER_STYLE)
        self._lum_slider.valueChanged.connect(lambda _: self.changed.emit())
        layout.addWidget(self._lum_slider, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setFixedWidth(_WHEEL_SIZE + 16)

    # ── Accessors ────────────────────────────────────────

    def hue(self) -> float:
        """Return hue in degrees (0–360)."""
        angle = math.degrees(math.atan2(-self._ky, self._kx)) % 360
        return angle

    def saturation(self) -> float:
        """Return saturation 0–100."""
        dist = math.hypot(self._kx, self._ky)
        return min(dist, 1.0) * 100.0

    def luminance(self) -> int:
        return self._lum_slider.value()

    def set_values(self, hue: float, sat: float, lum: int) -> None:
        """Set wheel position from hue/sat/lum values."""
        rad = math.radians(hue)
        r = min(sat / 100.0, 1.0)
        self._kx = r * math.cos(rad)
        self._ky = -r * math.sin(rad)
        self._lum_slider.blockSignals(True)
        self._lum_slider.setValue(lum)
        self._lum_slider.blockSignals(False)
        self.update()

    def reset(self) -> None:
        self._kx = 0.0
        self._ky = 0.0
        self._lum_slider.blockSignals(True)
        self._lum_slider.setValue(0)
        self._lum_slider.blockSignals(False)
        self.update()

    # ── Painting ─────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Compute wheel rect in widget coordinates
        canvas_geom = self._canvas.geometry()
        cx = canvas_geom.x() + _WHEEL_SIZE / 2
        cy = canvas_geom.y() + _WHEEL_SIZE / 2
        radius = _WHEEL_SIZE / 2 - 2

        # Draw dark circle background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_RING_BG))
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Rainbow hue ring
        gradient = QConicalGradient(cx, cy, 0)
        for i in range(13):
            h = (i * 30) % 360
            gradient.setColorAt(i / 12.0, QColor.fromHsv(h, 180, 200))
        p.setPen(QPen(QColor(0, 0, 0, 0)))
        p.setBrush(gradient)
        p.setOpacity(0.35)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Fade to center (radial gradient overlay)
        fade = QRadialGradient(cx, cy, radius)
        fade.setColorAt(0.0, QColor(30, 30, 30, 255))
        fade.setColorAt(0.6, QColor(30, 30, 30, 180))
        fade.setColorAt(1.0, QColor(30, 30, 30, 0))
        p.setOpacity(1.0)
        p.setBrush(fade)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Crosshair
        p.setOpacity(0.3)
        pen = QPen(QColor(_TEXT_DIM), 1)
        p.setPen(pen)
        p.drawLine(QPointF(cx - radius, cy), QPointF(cx + radius, cy))
        p.drawLine(QPointF(cx, cy - radius), QPointF(cx, cy + radius))

        # Ring outline
        p.setOpacity(0.5)
        p.setPen(QPen(QColor("#555"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Knob
        kx_px = cx + self._kx * radius
        ky_px = cy + self._ky * radius
        p.setOpacity(1.0)
        p.setPen(QPen(QColor("#fff"), 1.5))
        p.setBrush(QColor(_ACCENT))
        p.drawEllipse(QPointF(kx_px, ky_px), _KNOB_RADIUS, _KNOB_RADIUS)

        p.end()

    # ── Mouse interaction ────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._update_knob(event.position())
            self._dragging = True
        elif event.button() == Qt.MouseButton.RightButton:
            self.reset()
            self.changed.emit()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            self._update_knob(event.position())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.reset()
        self.changed.emit()

    def _update_knob(self, pos: QPointF) -> None:
        canvas_geom = self._canvas.geometry()
        cx = canvas_geom.x() + _WHEEL_SIZE / 2
        cy = canvas_geom.y() + _WHEEL_SIZE / 2
        radius = _WHEEL_SIZE / 2 - 2

        dx = (pos.x() - cx) / radius
        dy = (pos.y() - cy) / radius
        dist = math.hypot(dx, dy)
        if dist > 1.0:
            dx /= dist
            dy /= dist
        self._kx = dx
        self._ky = dy
        self.update()
        self.changed.emit()


class ColorWheelsWidget(QWidget):
    """Three color wheels (Shadows / Midtones / Highlights) in a row."""

    wheels_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._shadows = _SingleWheel("Shadows")
        self._midtones = _SingleWheel("Midtones")
        self._highlights = _SingleWheel("Highlights")

        for w in (self._shadows, self._midtones, self._highlights):
            w.changed.connect(self.wheels_changed.emit)
            layout.addWidget(w)

    # ── Public API ───────────────────────────────────────

    def get_values(self) -> Dict[str, Tuple[float, float, int]]:
        """Return dict with hue, saturation, luminance for each zone."""
        result = {}
        for name, w in [("shadows", self._shadows),
                        ("midtones", self._midtones),
                        ("highlights", self._highlights)]:
            result[name] = (w.hue(), w.saturation(), w.luminance())
        return result

    def set_values(self, values: Dict[str, Tuple[float, float, int]]) -> None:
        for name, w in [("shadows", self._shadows),
                        ("midtones", self._midtones),
                        ("highlights", self._highlights)]:
            if name in values:
                h, s, l = values[name]
                w.set_values(h, s, l)

    def reset(self) -> None:
        self._shadows.reset()
        self._midtones.reset()
        self._highlights.reset()

    def get_params(self) -> dict:
        """Return flat dict of params for preview engine."""
        vals = self.get_values()
        params = {}
        for zone in ("shadows", "midtones", "highlights"):
            h, s, l = vals[zone]
            params[f"cw_{zone}_hue"] = round(h, 1)
            params[f"cw_{zone}_sat"] = round(s, 1)
            params[f"cw_{zone}_lum"] = l
        return params

    def set_from_params(self, params: dict) -> None:
        """Restore state from flat param dict."""
        for zone in ("shadows", "midtones", "highlights"):
            h = params.get(f"cw_{zone}_hue", 0.0)
            s = params.get(f"cw_{zone}_sat", 0.0)
            l = params.get(f"cw_{zone}_lum", 0)
            w = getattr(self, f"_{zone}")
            w.set_values(float(h), float(s), int(l))
