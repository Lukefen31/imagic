"""Animated AI loading overlay — displays a pulsing spinner with status text.

Shown as a semi-transparent overlay on top of the parent widget whenever
an AI process is running (batch optimize, auto-enhance, analysis, etc.).
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QWidget

_ACCENT = QColor("#ff9800")
_ACCENT_DIM = QColor(255, 152, 0, 60)
_BG_OVERLAY = QColor(10, 10, 10, 200)
_TEXT_COLOR = QColor("#e0e0e0")
_SUB_COLOR = QColor("#999")
_CARD_BG = QColor(26, 26, 26, 240)
_CARD_BORDER = QColor(60, 60, 60)


class AILoadingModal(QWidget):
    """Semi-transparent overlay with an animated spinner and status text.

    Usage::

        modal = AILoadingModal(parent=self)
        modal.show_message("Optimizing photos…", "Processing 1 / 12")
        # later…
        modal.set_progress(5, 12, "Enhancing photo_005.CR2")
        # done
        modal.hide_modal()

    The widget automatically resizes to cover its parent.
    """

    cancelled = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowFlags(Qt.WindowType.Widget)

        # Animation state
        self._angle = 0.0
        self._pulse = 0.0
        self._pulse_dir = 1

        # Text state
        self._title = "AI Processing…"
        self._subtitle = ""
        self._progress_current = 0
        self._progress_total = 0

        # Dots animation
        self._dot_count = 0

        # Animation timer — 60 fps
        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60fps
        self._timer.timeout.connect(self._tick)

        # Dot timer — cycles the trailing dots
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(400)
        self._dot_timer.timeout.connect(self._tick_dots)

        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_message(
        self,
        title: str = "AI Processing…",
        subtitle: str = "",
        total: int = 0,
    ) -> None:
        """Show the overlay with the given message."""
        self._title = title
        self._subtitle = subtitle
        self._progress_current = 0
        self._progress_total = total
        self._angle = 0.0
        self._pulse = 0.0
        self._dot_count = 0
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self._timer.start()
        self._dot_timer.start()

    def set_progress(
        self,
        current: int,
        total: int = 0,
        subtitle: str = "",
    ) -> None:
        """Update the progress counter and subtitle."""
        self._progress_current = current
        if total > 0:
            self._progress_total = total
        if subtitle:
            self._subtitle = subtitle
        self.update()

    def set_title(self, title: str) -> None:
        self._title = title
        self.update()

    def hide_modal(self) -> None:
        """Gracefully dismiss the overlay."""
        self._timer.stop()
        self._dot_timer.stop()
        self.hide()

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self._angle = (self._angle + 4.0) % 360.0
        self._pulse += 0.03 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse_dir = -1
        elif self._pulse <= 0.0:
            self._pulse_dir = 1
        self.update()

    def _tick_dots(self) -> None:
        self._dot_count = (self._dot_count + 1) % 4

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 1. Semi-transparent backdrop
        painter.fillRect(self.rect(), _BG_OVERLAY)

        # 2. Central card
        card_w, card_h = 320, 220
        if self._progress_total > 0:
            card_h = 260  # extra room for progress bar
        cx = (w - card_w) // 2
        cy = (h - card_h) // 2

        card_rect = QRectF(cx, cy, card_w, card_h)
        path = QPainterPath()
        path.addRoundedRect(card_rect, 16, 16)

        painter.setPen(QPen(_CARD_BORDER, 1))
        painter.setBrush(QBrush(_CARD_BG))
        painter.drawPath(path)

        # 3. Spinner (centered in card upper area)
        spinner_cx = cx + card_w / 2
        spinner_cy = cy + 65
        spinner_r = 28

        # Outer glow ring (pulsing)
        glow_alpha = int(30 + 40 * self._pulse)
        glow_color = QColor(255, 152, 0, glow_alpha)
        glow_pen = QPen(glow_color, 6)
        glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        painter.drawEllipse(
            QRectF(spinner_cx - spinner_r - 2, spinner_cy - spinner_r - 2,
                   (spinner_r + 2) * 2, (spinner_r + 2) * 2)
        )

        # Background track
        track_pen = QPen(QColor(60, 60, 60), 4)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawEllipse(
            QRectF(spinner_cx - spinner_r, spinner_cy - spinner_r,
                   spinner_r * 2, spinner_r * 2)
        )

        # Spinning arc — conical gradient for smooth tail
        arc_rect = QRectF(
            spinner_cx - spinner_r, spinner_cy - spinner_r,
            spinner_r * 2, spinner_r * 2,
        )

        grad = QConicalGradient(spinner_cx, spinner_cy, -self._angle)
        grad.setColorAt(0.0, _ACCENT)
        grad.setColorAt(0.35, QColor(255, 152, 0, 180))
        grad.setColorAt(0.7, QColor(255, 152, 0, 0))
        grad.setColorAt(1.0, QColor(255, 152, 0, 0))

        arc_pen = QPen(QBrush(grad), 4)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.drawEllipse(arc_rect)

        # Leading dot
        dot_angle_rad = math.radians(-self._angle)
        dot_x = spinner_cx + spinner_r * math.cos(dot_angle_rad)
        dot_y = spinner_cy + spinner_r * math.sin(dot_angle_rad)
        painter.setPen(Qt.PenStyle.NoPen)
        dot_glow = QColor(255, 180, 50, int(160 + 60 * self._pulse))
        painter.setBrush(QBrush(dot_glow))
        painter.drawEllipse(QRectF(dot_x - 4, dot_y - 4, 8, 8))
        painter.setBrush(QBrush(_ACCENT))
        painter.drawEllipse(QRectF(dot_x - 3, dot_y - 3, 6, 6))

        # 4. Title text
        dots = "." * self._dot_count
        title_font = QFont("Segoe UI", 14, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QPen(_TEXT_COLOR))
        title_rect = QRectF(cx + 16, spinner_cy + spinner_r + 16, card_w - 32, 28)
        painter.drawText(
            title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            f"{self._title}{dots}",
        )

        # 5. Subtitle / file name
        if self._subtitle:
            sub_font = QFont("Segoe UI", 10)
            painter.setFont(sub_font)
            painter.setPen(QPen(_SUB_COLOR))
            sub_rect = QRectF(cx + 16, spinner_cy + spinner_r + 46, card_w - 32, 22)
            painter.drawText(
                sub_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self._subtitle,
            )

        # 6. Progress bar (if total > 0)
        if self._progress_total > 0:
            bar_y = cy + card_h - 50
            bar_x = cx + 30
            bar_w = card_w - 60
            bar_h = 6

            # Track
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(50, 50, 50)))
            track_path = QPainterPath()
            track_path.addRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 3, 3)
            painter.drawPath(track_path)

            # Fill
            ratio = min(self._progress_current / max(self._progress_total, 1), 1.0)
            fill_w = bar_w * ratio
            if fill_w > 0:
                fill_path = QPainterPath()
                fill_path.addRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 3, 3)
                # Gradient fill
                painter.setBrush(QBrush(_ACCENT))
                painter.drawPath(fill_path)

                # Shimmer highlight
                shimmer_x = bar_x + fill_w - 20
                shimmer_color = QColor(255, 200, 100, int(80 + 50 * self._pulse))
                painter.setBrush(QBrush(shimmer_color))
                shimmer_path = QPainterPath()
                shimmer_path.addRoundedRect(
                    QRectF(max(bar_x, shimmer_x), bar_y, min(20, fill_w), bar_h), 3, 3
                )
                painter.drawPath(shimmer_path)

            # Progress text
            prog_font = QFont("Segoe UI", 10)
            painter.setFont(prog_font)
            painter.setPen(QPen(_SUB_COLOR))
            prog_text = f"{self._progress_current} / {self._progress_total}"
            prog_rect = QRectF(bar_x, bar_y + bar_h + 4, bar_w, 20)
            painter.drawText(
                prog_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                prog_text,
            )

        painter.end()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self.parent():
            self.setGeometry(self.parent().rect())
