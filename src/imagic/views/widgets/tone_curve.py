"""Interactive tone curve editor widget for the photo editor.

Provides a click-and-drag curve editor similar to Lightroom/Photoshop.
Users can add, move, and remove control points to reshape the tonal
response. The widget emits ``curve_changed`` whenever points are modified.
"""

from __future__ import annotations

from typing import List, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

# Style constants (match photo_editor theme)
_BG = "#1a1a1a"
_GRID = "#2a2a2a"
_GRID_MID = "#333"
_CURVE_COLOR = "#ff9800"
_POINT_COLOR = "#ff9800"
_POINT_HOVER = "#ffa726"
_DIAGONAL = "#444"
_HIST_COLOR = QColor(80, 80, 80, 60)

_POINT_RADIUS = 5
_HIT_RADIUS = 10  # pixel radius for click detection


class ToneCurveWidget(QWidget):
    """Interactive tone curve editor.

    Control points are stored as (x, y) tuples in [0, 1] range.
    The start (0, 0) and end (1, 1) anchors are always present.
    """

    curve_changed = pyqtSignal()  # emitted on any point change

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        # Control points: always sorted by x, anchors at 0 and 1
        self._points: List[List[float]] = [[0.0, 0.0], [1.0, 1.0]]
        self._dragging: int | None = None
        self._hover: int | None = None
        self._histogram_data: list | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_points(self) -> List[Tuple[float, float]]:
        """Return current control points as [(x, y), ...]."""
        return [(p[0], p[1]) for p in self._points]

    def set_points(self, points: List[Tuple[float, float]]) -> None:
        """Set control points (must include anchors at 0 and 1)."""
        if not points or len(points) < 2:
            self._points = [[0.0, 0.0], [1.0, 1.0]]
        else:
            self._points = [[p[0], p[1]] for p in sorted(points, key=lambda p: p[0])]
            # Ensure anchors
            if self._points[0][0] != 0.0:
                self._points.insert(0, [0.0, 0.0])
            if self._points[-1][0] != 1.0:
                self._points.append([1.0, 1.0])
        self.update()

    def reset(self) -> None:
        """Reset to linear (diagonal) curve."""
        self._points = [[0.0, 0.0], [1.0, 1.0]]
        self._dragging = None
        self._hover = None
        self.update()
        self.curve_changed.emit()

    def set_histogram(self, data: list | None) -> None:
        """Set background histogram data (256-element list)."""
        self._histogram_data = data
        self.update()

    def is_default(self) -> bool:
        """Check if curve is at default (linear)."""
        return len(self._points) == 2

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------

    def _to_widget(self, x: float, y: float) -> QPointF:
        """Convert normalised (0-1) to widget pixel coords."""
        margin = 8
        w = self.width() - 2 * margin
        h = self.height() - 2 * margin
        return QPointF(margin + x * w, margin + (1.0 - y) * h)

    def _from_widget(self, px: float, py: float) -> Tuple[float, float]:
        """Convert widget pixel coords to normalised (0-1)."""
        margin = 8
        w = self.width() - 2 * margin
        h = self.height() - 2 * margin
        x = max(0.0, min(1.0, (px - margin) / w))
        y = max(0.0, min(1.0, 1.0 - (py - margin) / h))
        return x, y

    # ------------------------------------------------------------------
    # Hit testing
    # ------------------------------------------------------------------

    def _hit_test(self, pos: QPointF) -> int | None:
        """Return index of point near pos, or None."""
        for i, p in enumerate(self._points):
            wp = self._to_widget(p[0], p[1])
            dx = pos.x() - wp.x()
            dy = pos.y() - wp.y()
            if dx * dx + dy * dy < _HIT_RADIUS * _HIT_RADIUS:
                return i
        return None

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._hit_test(event.position())
            if hit is not None:
                self._dragging = hit
            else:
                # Add new point
                x, y = self._from_widget(event.position().x(), event.position().y())
                # Find insertion position (keep sorted by x)
                idx = 0
                for i, p in enumerate(self._points):
                    if p[0] < x:
                        idx = i + 1
                self._points.insert(idx, [x, y])
                self._dragging = idx
                self.update()
                self.curve_changed.emit()

        elif event.button() == Qt.MouseButton.RightButton:
            # Remove point (not anchors)
            hit = self._hit_test(event.position())
            if hit is not None and hit != 0 and hit != len(self._points) - 1:
                self._points.pop(hit)
                self._dragging = None
                self.update()
                self.curve_changed.emit()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging is not None:
            x, y = self._from_widget(event.position().x(), event.position().y())
            idx = self._dragging

            # Anchors: only move y, not x
            if idx == 0:
                self._points[idx][1] = y
            elif idx == len(self._points) - 1:
                self._points[idx][1] = y
            else:
                # Clamp x between neighbours
                x_min = self._points[idx - 1][0] + 0.01
                x_max = self._points[idx + 1][0] - 0.01
                self._points[idx][0] = max(x_min, min(x_max, x))
                self._points[idx][1] = y

            self.update()
            self.curve_changed.emit()
        else:
            # Hover highlight
            hit = self._hit_test(event.position())
            if hit != self._hover:
                self._hover = hit
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Double-click to reset curve."""
        self.reset()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = 8
        area = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)

        # Background
        p.fillRect(self.rect(), QColor(_BG))

        # Optional histogram backdrop
        if self._histogram_data and len(self._histogram_data) == 256:
            max_val = max(self._histogram_data) or 1
            bar_w = area.width() / 256
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_HIST_COLOR)
            for i, val in enumerate(self._histogram_data):
                bar_h = (val / max_val) * area.height() * 0.8
                x = area.left() + i * bar_w
                p.drawRect(QRectF(x, area.bottom() - bar_h, bar_w + 0.5, bar_h))

        # Grid lines
        p.setPen(QPen(QColor(_GRID), 1))
        for i in range(1, 4):
            frac = i / 4.0
            x = area.left() + frac * area.width()
            y = area.top() + frac * area.height()
            p.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

        # Middle grid lines slightly brighter
        mid_x = area.left() + 0.5 * area.width()
        mid_y = area.top() + 0.5 * area.height()
        p.setPen(QPen(QColor(_GRID_MID), 1))
        p.drawLine(QPointF(mid_x, area.top()), QPointF(mid_x, area.bottom()))
        p.drawLine(QPointF(area.left(), mid_y), QPointF(area.right(), mid_y))

        # Border
        p.setPen(QPen(QColor(_GRID_MID), 1))
        p.drawRect(area)

        # Diagonal reference
        p.setPen(QPen(QColor(_DIAGONAL), 1, Qt.PenStyle.DashLine))
        p.drawLine(
            self._to_widget(0, 0),
            self._to_widget(1, 1),
        )

        # Interpolated curve
        curve_points = self._interpolate_curve(64)
        if len(curve_points) >= 2:
            path = QPainterPath()
            first = self._to_widget(curve_points[0][0], curve_points[0][1])
            path.moveTo(first)
            for cx, cy in curve_points[1:]:
                path.lineTo(self._to_widget(cx, cy))
            p.setPen(QPen(QColor(_CURVE_COLOR), 2))
            p.drawPath(path)

        # Control points
        for i, pt in enumerate(self._points):
            wp = self._to_widget(pt[0], pt[1])
            is_hover = (i == self._hover or i == self._dragging)
            color = QColor(_POINT_HOVER) if is_hover else QColor(_POINT_COLOR)
            r = _POINT_RADIUS + (2 if is_hover else 0)
            p.setPen(QPen(QColor("#111"), 1))
            p.setBrush(color)
            p.drawEllipse(wp, r, r)

        p.end()

    # ------------------------------------------------------------------
    # Curve interpolation
    # ------------------------------------------------------------------

    def _interpolate_curve(self, num_points: int = 64) -> List[Tuple[float, float]]:
        """Interpolate a smooth curve through control points."""
        pts = self._points
        if len(pts) < 2:
            return [(0, 0), (1, 1)]

        if len(pts) == 2:
            # Linear between two points
            result = []
            for i in range(num_points + 1):
                t = i / num_points
                x = pts[0][0] + t * (pts[1][0] - pts[0][0])
                y = pts[0][1] + t * (pts[1][1] - pts[0][1])
                result.append((x, max(0.0, min(1.0, y))))
            return result

        # Monotone cubic interpolation (Fritsch-Carlson) for smooth curves
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        n = len(xs)

        # Compute slopes
        deltas = [(ys[i + 1] - ys[i]) / max(xs[i + 1] - xs[i], 1e-6) for i in range(n - 1)]

        # Compute tangents
        ms = [0.0] * n
        ms[0] = deltas[0]
        ms[-1] = deltas[-1]
        for i in range(1, n - 1):
            if deltas[i - 1] * deltas[i] <= 0:
                ms[i] = 0
            else:
                ms[i] = (deltas[i - 1] + deltas[i]) / 2

        # Monotonicity enforcement
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

        # Sample the curve
        result = []
        for j in range(num_points + 1):
            x = j / num_points
            # Find segment
            seg = 0
            for i in range(n - 1):
                if x >= xs[i]:
                    seg = i
            if seg >= n - 1:
                seg = n - 2

            h = xs[seg + 1] - xs[seg]
            if h < 1e-10:
                result.append((x, ys[seg]))
                continue

            t = (x - xs[seg]) / h
            h00 = 2 * t ** 3 - 3 * t ** 2 + 1
            h10 = t ** 3 - 2 * t ** 2 + t
            h01 = -2 * t ** 3 + 3 * t ** 2
            h11 = t ** 3 - t ** 2

            y = h00 * ys[seg] + h10 * h * ms[seg] + h01 * ys[seg + 1] + h11 * h * ms[seg + 1]
            result.append((x, max(0.0, min(1.0, y))))

        return result

    def get_curve_lut(self, size: int = 256) -> list:
        """Return a lookup table of *size* values mapping input → output [0, 1]."""
        pts = self._interpolate_curve(size - 1)
        return [p[1] for p in pts]
