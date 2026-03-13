"""Speech-bubble tooltip and guided-tutorial overlay.

Provides two public classes:

* **SpeechBubble** — a single floating callout bubble with a pointer
  triangle, rich text, and optional action button.
* **TutorialOverlay** — a full-window dim overlay that steps through a
  sequence of ``TutorialStep`` items, highlighting the target widget and
  showing a speech bubble next to it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, List, Optional

from PyQt6.QtCore import QPoint, QPropertyAnimation, QRect, QEasingCurve, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QBrush, QRegion
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# ── Style tokens ────────────────────────────────────────────────────
_BG = QColor(30, 30, 30, 245)
_BORDER = QColor(70, 70, 70)
_ACCENT = QColor("#ff9800")
_TEXT = QColor("#e0e0e0")
_DIM = QColor("#999")
_OVERLAY_DIM = QColor(0, 0, 0, 160)
_HIGHLIGHT = QColor(255, 152, 0, 50)

_RADIUS = 12
_POINTER_SIZE = 12
_PADDING = 16
_MAX_WIDTH = 340


class PointerSide(Enum):
    """Which edge of the bubble the pointer triangle sits on."""
    TOP = auto()
    BOTTOM = auto()
    LEFT = auto()
    RIGHT = auto()


# =====================================================================
# SpeechBubble
# =====================================================================

class SpeechBubble(QWidget):
    """Floating callout bubble with pointer triangle.

    The bubble is shown as a child of a top-level window and positions
    itself relative to a *target* widget.  It supports:

    - Title + body text
    - Optional action button (e.g. "Got it", "Next")
    - Pointer on any edge
    - Auto-dismiss timer
    - Fade-in animation

    Usage::

        bubble = SpeechBubble(parent=main_window)
        bubble.show_at(
            target_widget,
            title="Tip",
            body="Press again for a stricter result!",
            pointer=PointerSide.TOP,
            button_text="Got it",
        )
    """

    dismissed = pyqtSignal()
    action_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._pointer = PointerSide.TOP
        self._pointer_offset = 0.5  # fraction along the edge (0..1)

        self._title = ""
        self._body = ""
        self._btn_text = ""
        self._auto_hide_ms = 0

        self._dismiss_timer: Optional[QTimer] = None

        self._build_ui()
        self.hide()

    # ── UI ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(
            _PADDING + 2, _PADDING + _POINTER_SIZE + 2,
            _PADDING + 2, _PADDING + 2,
        )
        self._root.setSpacing(6)

        self._title_lbl = QLabel()
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet(
            "color: #ff9800; font-size: 13px; font-weight: bold; background: transparent;"
        )
        self._root.addWidget(self._title_lbl)

        self._body_lbl = QLabel()
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setStyleSheet(
            "color: #ddd; font-size: 12px; line-height: 1.4; background: transparent;"
        )
        self._root.addWidget(self._body_lbl)

        self._btn_row = QHBoxLayout()
        self._btn_row.setContentsMargins(0, 4, 0, 0)
        self._btn_row.addStretch()

        self._action_btn = QPushButton()
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setStyleSheet(
            "QPushButton { background: #ff9800; color: #111; font-weight: bold; "
            "border: none; border-radius: 4px; padding: 6px 18px; font-size: 11px; }"
            "QPushButton:hover { background: #ffa726; }"
        )
        self._action_btn.clicked.connect(self._on_action)
        self._btn_row.addWidget(self._action_btn)
        self._root.addLayout(self._btn_row)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

    # ── Public API ────────────────────────────────────────────────
    def show_at(
        self,
        target: QWidget,
        *,
        title: str = "",
        body: str = "",
        pointer: PointerSide = PointerSide.TOP,
        pointer_offset: float = 0.5,
        button_text: str = "Got it",
        auto_hide_ms: int = 0,
        on_action: Optional[Callable] = None,
    ) -> None:
        """Position the bubble relative to *target* and show it."""
        self._pointer = pointer
        self._pointer_offset = pointer_offset
        self._title = title
        self._body = body
        self._btn_text = button_text

        self._title_lbl.setText(title)
        self._title_lbl.setVisible(bool(title))
        self._body_lbl.setText(body)
        self._action_btn.setText(button_text)
        self._action_btn.setVisible(bool(button_text))

        if on_action:
            # Disconnect any previous one-shot
            try:
                self.action_clicked.disconnect()
            except TypeError:
                pass
            self.action_clicked.connect(on_action)

        # Update margins for pointer side
        m = _PADDING + 2
        pm = _POINTER_SIZE + m
        if pointer == PointerSide.TOP:
            self._root.setContentsMargins(m, pm, m, m)
        elif pointer == PointerSide.BOTTOM:
            self._root.setContentsMargins(m, m, m, pm)
        elif pointer == PointerSide.LEFT:
            self._root.setContentsMargins(pm, m, m, m)
        elif pointer == PointerSide.RIGHT:
            self._root.setContentsMargins(m, m, pm, m)

        self.setMaximumWidth(_MAX_WIDTH)
        self.adjustSize()

        # Position relative to target
        self._position_near(target)
        self.show()
        self.raise_()

        if auto_hide_ms > 0:
            if self._dismiss_timer is None:
                self._dismiss_timer = QTimer(self)
                self._dismiss_timer.setSingleShot(True)
                self._dismiss_timer.timeout.connect(self.dismiss)
            self._dismiss_timer.start(auto_hide_ms)

    def dismiss(self) -> None:
        if self._dismiss_timer:
            self._dismiss_timer.stop()
        self.hide()
        self.dismissed.emit()

    # ── Internal ──────────────────────────────────────────────────
    def _on_action(self) -> None:
        self.action_clicked.emit()
        self.dismiss()

    def _position_near(self, target: QWidget) -> None:
        """Place the bubble adjacent to *target* on the pointer side."""
        parent = self.parentWidget()
        if parent is None or target is None:
            return

        # Target centre in parent coordinates
        target_rect = QRect(
            target.mapTo(parent, QPoint(0, 0)),
            target.size(),
        )
        bw, bh = self.sizeHint().width(), self.sizeHint().height()
        gap = 6

        if self._pointer == PointerSide.TOP:
            # Bubble below target
            x = target_rect.center().x() - int(bw * self._pointer_offset)
            y = target_rect.bottom() + gap
        elif self._pointer == PointerSide.BOTTOM:
            x = target_rect.center().x() - int(bw * self._pointer_offset)
            y = target_rect.top() - bh - gap
        elif self._pointer == PointerSide.LEFT:
            x = target_rect.right() + gap
            y = target_rect.center().y() - int(bh * self._pointer_offset)
        else:  # RIGHT
            x = target_rect.left() - bw - gap
            y = target_rect.center().y() - int(bh * self._pointer_offset)

        # Clamp to parent bounds
        pw, ph = parent.width(), parent.height()
        x = max(8, min(x, pw - bw - 8))
        y = max(8, min(y, ph - bh - 8))

        self.move(x, y)

    # ── Paint ─────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Compute the body rect (excluding the pointer area)
        ps = _POINTER_SIZE
        if self._pointer == PointerSide.TOP:
            body = QRect(0, ps, w, h - ps)
        elif self._pointer == PointerSide.BOTTOM:
            body = QRect(0, 0, w, h - ps)
        elif self._pointer == PointerSide.LEFT:
            body = QRect(ps, 0, w - ps, h)
        else:
            body = QRect(0, 0, w - ps, h)

        # Rounded rect body
        path = QPainterPath()
        path.addRoundedRect(body.x(), body.y(), body.width(), body.height(), _RADIUS, _RADIUS)

        painter.setPen(QPen(_BORDER, 1))
        painter.setBrush(QBrush(_BG))
        painter.drawPath(path)

        # Pointer triangle
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_BG))

        tri = QPainterPath()
        offset_frac = max(0.15, min(0.85, self._pointer_offset))

        if self._pointer == PointerSide.TOP:
            cx = int(w * offset_frac)
            tri.moveTo(cx - ps, ps)
            tri.lineTo(cx, 0)
            tri.lineTo(cx + ps, ps)
        elif self._pointer == PointerSide.BOTTOM:
            cx = int(w * offset_frac)
            tri.moveTo(cx - ps, h - ps)
            tri.lineTo(cx, h)
            tri.lineTo(cx + ps, h - ps)
        elif self._pointer == PointerSide.LEFT:
            cy = int(h * offset_frac)
            tri.moveTo(ps, cy - ps)
            tri.lineTo(0, cy)
            tri.lineTo(ps, cy + ps)
        else:  # RIGHT
            cy = int(h * offset_frac)
            tri.moveTo(w - ps, cy - ps)
            tri.lineTo(w, cy)
            tri.lineTo(w - ps, cy + ps)

        painter.drawPath(tri)

        # Re-draw the border line along the triangle
        painter.setPen(QPen(_BORDER, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(tri)

        painter.end()


# =====================================================================
# Tutorial system
# =====================================================================

@dataclass
class TutorialStep:
    """One step in a guided tutorial."""
    title: str
    body: str
    target_name: str  # attribute name on MainWindow to point at
    pointer: PointerSide = PointerSide.TOP
    pointer_offset: float = 0.5


class TutorialOverlay(QWidget):
    """Full-window overlay that dims everything except the highlighted widget.

    Steps through a list of ``TutorialStep`` items, showing a speech
    bubble next to each target and a translucent dim over the rest.

    Parameters:
        steps:  Ordered tutorial steps.
        host:   The widget to overlay (usually the central widget).
        target_root:  The object on which target names are resolved
                      (defaults to *host*).
    """

    finished = pyqtSignal()

    def __init__(
        self,
        steps: List[TutorialStep],
        host: QWidget,
        target_root: Optional[QWidget] = None,
    ) -> None:
        super().__init__(host)
        self._steps = steps
        self._host = host
        self._target_root = target_root or host
        self._current = 0

        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self._bubble = SpeechBubble(parent=self)

        # Step counter label
        self._counter = QLabel(self)
        self._counter.setStyleSheet(
            "color: #888; font-size: 11px; background: transparent;"
        )
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Nav buttons
        self._nav = QWidget(self)
        nav_layout = QHBoxLayout(self._nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(8)

        self._skip_btn = QPushButton("Skip tour")
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; "
            "border: 1px solid #555; border-radius: 4px; padding: 6px 14px; font-size: 11px; }"
            "QPushButton:hover { color: #ddd; border-color: #888; }"
        )
        self._skip_btn.clicked.connect(self._finish)
        nav_layout.addWidget(self._skip_btn)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #ddd; "
            "border: 1px solid #555; border-radius: 4px; padding: 6px 14px; font-size: 11px; }"
            "QPushButton:hover { border-color: #888; }"
        )
        self._back_btn.clicked.connect(self._prev)
        nav_layout.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet(
            "QPushButton { background: #ff9800; color: #111; font-weight: bold; "
            "border: none; border-radius: 4px; padding: 6px 18px; font-size: 11px; }"
            "QPushButton:hover { background: #ffa726; }"
        )
        self._next_btn.clicked.connect(self._next)
        nav_layout.addWidget(self._next_btn)

        self.hide()

    # ── Public ────────────────────────────────────────────────────
    def start(self) -> None:
        if not self._steps:
            return
        self._current = 0
        self.setGeometry(self._host.rect())
        self.show()
        self.raise_()
        self._show_step()

    # ── Navigation ────────────────────────────────────────────────
    def _next(self) -> None:
        if self._current < len(self._steps) - 1:
            self._current += 1
            self._show_step()
        else:
            self._finish()

    def _prev(self) -> None:
        if self._current > 0:
            self._current -= 1
            self._show_step()

    def _finish(self) -> None:
        self._bubble.dismiss()
        self.hide()
        self.finished.emit()

    # ── Rendering ─────────────────────────────────────────────────
    def _show_step(self) -> None:
        step = self._steps[self._current]
        n = len(self._steps)

        target = self._resolve_target(step.target_name)
        if target is None:
            logger.warning("Tutorial target '%s' not found, skipping.", step.target_name)
            self._next()
            return

        self._back_btn.setVisible(self._current > 0)
        self._next_btn.setText("Finish" if self._current == n - 1 else "Next →")

        self._counter.setText(f"Step {self._current + 1} of {n}")
        self._counter.adjustSize()

        # Position bubble near target
        self._bubble.show_at(
            target,
            title=step.title,
            body=step.body,
            pointer=step.pointer,
            pointer_offset=step.pointer_offset,
            button_text="",  # nav buttons handle progression
        )

        # Position nav below the bubble
        bx, by = self._bubble.x(), self._bubble.y()
        bw, bh = self._bubble.width(), self._bubble.height()

        self._counter.move(bx + (bw - self._counter.width()) // 2, by + bh + 4)
        self._nav.adjustSize()
        self._nav.move(bx + (bw - self._nav.width()) // 2, by + bh + 22)
        self._nav.show()
        self._nav.raise_()
        self._counter.show()
        self._counter.raise_()

        self.update()

    def _resolve_target(self, name: str) -> Optional[QWidget]:
        """Look up a widget on the target root by dotted attribute path.

        Supports list indexing via ``name.N`` where *N* is an integer,
        e.g. ``_step_buttons.0`` returns the first element.
        """
        obj = self._target_root
        for part in name.split("."):
            if isinstance(obj, (list, tuple)):
                try:
                    obj = obj[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                obj = getattr(obj, part, None)
            if obj is None:
                return None
        if isinstance(obj, QWidget):
            return obj
        return None

    # ── Paint (dim overlay with cut-out for target) ───────────────
    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Full dim
        painter.fillRect(self.rect(), _OVERLAY_DIM)

        # Cut out target widget with a highlight glow
        if self._current < len(self._steps):
            step = self._steps[self._current]
            target = self._resolve_target(step.target_name)
            if target is not None:
                tl = target.mapTo(self, QPoint(0, 0))
                tr = QRect(tl, target.size()).adjusted(-6, -6, 6, 6)

                # Clear the cut-out
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                cut = QPainterPath()
                cut.addRoundedRect(tr.x(), tr.y(), tr.width(), tr.height(), 8, 8)
                painter.drawPath(cut)

                # Draw highlight border
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                painter.setPen(QPen(_ACCENT, 2))
                painter.setBrush(QBrush(_HIGHLIGHT))
                painter.drawRoundedRect(tr, 8, 8)

        painter.end()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._host:
            self.setGeometry(self._host.rect())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # Consume clicks so they don't pass through the overlay
        event.accept()
