"""Review thumbnail widget — enriched tile with metrics and cull buttons."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from imagic.views.widgets.thumbnail_widget import _ImageLoader, _LOAD_SIZE

logger = logging.getLogger(__name__)

_METRIC_COLORS = {
    "good": "#4caf50",
    "ok": "#ff9800",
    "bad": "#f44336",
}


def _metric_color(score: float) -> str:
    if score >= 0.6:
        return _METRIC_COLORS["good"]
    if score >= 0.3:
        return _METRIC_COLORS["ok"]
    return _METRIC_COLORS["bad"]


def _status_label(status: str) -> tuple[str, str]:
    """Return (display_text, color) for a photo status."""
    s = status.lower()
    if s == "keep":
        return "KEEP", "#4caf50"
    if s == "trash":
        return "TRASH", "#f44336"
    if s == "culled":
        return "REVIEW", "#888"
    return status.upper(), "#888"


class ReviewThumbnailWidget(QWidget):
    """Enriched thumbnail tile for the review grid.

    Shows the image, filename, score badge, per-metric bars,
    status label, and keep/trash action buttons.
    """

    clicked = pyqtSignal(int)
    double_clicked = pyqtSignal(int)
    status_changed = pyqtSignal(int, str)  # photo_id, new_status

    def __init__(
        self,
        photo_id: int,
        thumb_path: Optional[str],
        file_name: str,
        score: Optional[float] = None,
        status: str = "",
        cull_reasons: Optional[str] = None,
        size: int = 180,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.photo_id = photo_id
        self._size = size
        self._score = score
        self._status = status
        self._source_pixmap: Optional[QPixmap] = None

        # The card is thumbnail width + info panel
        self._info_width = 200
        self._update_fixed_size()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: #141414; border: 1px solid #2a2a2a; border-radius: 6px;")

        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        # --- Left: image + filename ---
        left = QVBoxLayout()
        left.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(size, size)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet(
            "background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 4px;"
        )
        left.addWidget(self._image_label)

        self._name_label = QLabel(file_name)
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setStyleSheet("color: #ccc; font-size: 10px; border: none;")
        self._name_label.setMaximumWidth(size)
        left.addWidget(self._name_label)
        left.addStretch()

        root.addLayout(left)

        # --- Right: info panel (wrapped in widget for show/hide) ---
        self._info_widget = QWidget()
        self._info_widget.setStyleSheet("background: transparent; border: none;")
        right = QVBoxLayout(self._info_widget)
        right.setSpacing(4)
        right.setContentsMargins(0, 2, 2, 2)

        # Score
        score_text = f"{score:.0%}" if score is not None else "—"
        score_color = _metric_color(score) if score is not None else "#888"
        self._score_label = QLabel(f"Score: {score_text}")
        self._score_label.setStyleSheet(
            f"color: {score_color}; font-size: 13px; font-weight: bold; border: none;"
        )
        right.addWidget(self._score_label)

        # Status badge
        status_text, status_color = _status_label(status)
        self._status_badge = QLabel(status_text)
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_badge.setFixedHeight(20)
        self._status_badge.setStyleSheet(
            f"background: {status_color}; color: white; font-size: 10px; "
            f"font-weight: bold; border-radius: 3px; padding: 1px 6px; border: none;"
        )
        right.addWidget(self._status_badge, alignment=Qt.AlignmentFlag.AlignLeft)

        # Metric bars
        self._metric_rows = []
        self._metrics_container = QVBoxLayout()
        self._metrics_container.setSpacing(2)
        self._build_metric_bars(cull_reasons)
        right.addLayout(self._metrics_container)

        right.addStretch()

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self._keep_btn = QPushButton("✓ Keep")
        self._keep_btn.setFixedHeight(24)
        self._keep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keep_btn.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white; border: none; "
            "border-radius: 3px; font-size: 10px; font-weight: bold; padding: 2px 8px; }"
            "QPushButton:hover { background: #388e3c; }"
        )
        self._keep_btn.clicked.connect(lambda: self._set_status("keep"))
        btn_row.addWidget(self._keep_btn)

        self._trash_btn = QPushButton("✕ Trash")
        self._trash_btn.setFixedHeight(24)
        self._trash_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._trash_btn.setStyleSheet(
            "QPushButton { background: #c62828; color: white; border: none; "
            "border-radius: 3px; font-size: 10px; font-weight: bold; padding: 2px 8px; }"
            "QPushButton:hover { background: #d32f2f; }"
        )
        self._trash_btn.clicked.connect(lambda: self._set_status("trash"))
        btn_row.addWidget(self._trash_btn)

        right.addLayout(btn_row)

        root.addWidget(self._info_widget)

        # Highlight active state
        self._apply_status_highlight()

        # Lazy load thumbnail
        if thumb_path and Path(thumb_path).is_file():
            self._loader = _ImageLoader(thumb_path, QSize(_LOAD_SIZE, _LOAD_SIZE))
            self._loader.loaded.connect(self._on_loaded)
            self._loader.start()
        else:
            self._image_label.setText("No thumb")
            self._image_label.setStyleSheet(
                "background: #1a1a1a; color: #555; border: 1px solid #2a2a2a; border-radius: 4px;"
            )

    # ------------------------------------------------------------------
    def _build_metric_bars(self, cull_reasons: Optional[str]) -> None:
        """Parse cull_reasons JSON and create compact metric lines."""
        if not cull_reasons:
            return
        try:
            reasons = json.loads(cull_reasons) if isinstance(cull_reasons, str) else cull_reasons
        except (json.JSONDecodeError, TypeError):
            return

        for r in reasons:
            metric = r.get("metric", "")
            score_val = r.get("score")
            verdict = r.get("verdict", "")
            if not metric or score_val is None:
                continue

            row = QHBoxLayout()
            row.setSpacing(4)
            row.setContentsMargins(0, 0, 0, 0)

            name_lbl = QLabel(metric[:4])
            name_lbl.setFixedWidth(30)
            name_lbl.setStyleSheet("color: #888; font-size: 9px; border: none;")
            row.addWidget(name_lbl)

            # Stretchy progress bar
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(score_val * 100))
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            color = _metric_color(score_val)
            bar.setStyleSheet(
                f"QProgressBar {{ background: #2a2a2a; border: none; border-radius: 4px; }}"
                f"QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}"
            )
            row.addWidget(bar, stretch=1)

            self._metric_rows.append((name_lbl, bar))
            self._metrics_container.addLayout(row)

    def _apply_status_highlight(self) -> None:
        """Tint the card border based on current status."""
        s = self._status.lower()
        if s == "keep":
            border = "#4caf50"
        elif s == "trash":
            border = "#f44336"
        else:
            border = "#2a2a2a"
        self.setStyleSheet(
            f"background: #141414; border: 1px solid {border}; border-radius: 6px;"
        )

    def _set_status(self, new_status: str) -> None:
        self._status = new_status
        status_text, status_color = _status_label(new_status)
        self._status_badge.setText(status_text)
        self._status_badge.setStyleSheet(
            f"background: {status_color}; color: white; font-size: 10px; "
            f"font-weight: bold; border-radius: 3px; padding: 1px 6px; border: none;"
        )
        self._apply_status_highlight()
        self.status_changed.emit(self.photo_id, new_status)

    def _on_loaded(self, pixmap: QPixmap) -> None:
        if not pixmap.isNull():
            self._source_pixmap = pixmap
            display = pixmap.scaled(
                QSize(self._size, self._size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.setPixmap(display)

    def _update_fixed_size(self) -> None:
        extra = (self._info_width + 20) if self._info_width > 0 else 8
        self.setFixedSize(self._size + extra, self._size + 32)

    def set_size(self, size: int, info_width: int = 200) -> None:
        """Resize the tile and info panel dynamically."""
        if size == self._size and info_width == self._info_width:
            return
        self._size = size
        self._info_width = info_width
        self._update_fixed_size()
        self._image_label.setFixedSize(size, size)
        self._name_label.setMaximumWidth(size)

        if info_width > 0:
            self._info_widget.show()
            self._info_widget.setFixedWidth(info_width)
            # Scale elements proportionally (200px = baseline)
            s = info_width / 200
            score_fs = max(10, min(18, int(13 * s)))
            badge_h = max(16, min(26, int(20 * s)))
            badge_fs = max(8, min(12, int(10 * s)))
            bar_h = max(4, min(14, int(8 * s)))
            metric_w = max(18, min(45, int(30 * s)))
            metric_fs = max(7, min(11, int(9 * s)))
            btn_h = max(16, min(30, int(24 * s)))
            btn_fs = max(8, min(12, int(10 * s)))

            score_color = _metric_color(self._score) if self._score is not None else "#888"
            self._score_label.setStyleSheet(
                f"color: {score_color}; font-size: {score_fs}px; font-weight: bold; border: none;"
            )
            self._status_badge.setFixedHeight(badge_h)
            status_text, status_color = _status_label(self._status)
            self._status_badge.setStyleSheet(
                f"background: {status_color}; color: white; font-size: {badge_fs}px; "
                f"font-weight: bold; border-radius: 3px; padding: 1px 6px; border: none;"
            )
            for name_lbl, bar in self._metric_rows:
                name_lbl.setFixedWidth(metric_w)
                name_lbl.setStyleSheet(f"color: #888; font-size: {metric_fs}px; border: none;")
                bar.setFixedHeight(bar_h)
            self._keep_btn.setFixedHeight(btn_h)
            self._keep_btn.setStyleSheet(
                f"QPushButton {{ background: #2e7d32; color: white; border: none; "
                f"border-radius: 3px; font-size: {btn_fs}px; font-weight: bold; padding: 2px 8px; }}"
                f"QPushButton:hover {{ background: #388e3c; }}"
            )
            self._trash_btn.setFixedHeight(btn_h)
            self._trash_btn.setStyleSheet(
                f"QPushButton {{ background: #c62828; color: white; border: none; "
                f"border-radius: 3px; font-size: {btn_fs}px; font-weight: bold; padding: 2px 8px; }}"
                f"QPushButton:hover {{ background: #d32f2f; }}"
            )
        else:
            self._info_widget.hide()

        if self._source_pixmap and not self._source_pixmap.isNull():
            display = self._source_pixmap.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.setPixmap(display)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self.photo_id)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.double_clicked.emit(self.photo_id)
