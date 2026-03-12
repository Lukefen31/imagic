"""Review grid view — responsive grid of enriched review tiles."""

from __future__ import annotations

import logging
from typing import List

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from imagic.views.widgets.review_thumbnail import ReviewThumbnailWidget

logger = logging.getLogger(__name__)

_CELL_PAD = 8
_MIN_COLS = 2
_MAX_COLS = 6


class ReviewGridView(QWidget):
    """Scrollable review grid with enriched tiles showing metrics + cull buttons.

    Zoom slider (and Ctrl+Scroll) controls how many columns to show (1–10).
    Tiles expand to fill the available width for the chosen column count.
    """

    photo_selected = pyqtSignal(int)
    photo_double_clicked = pyqtSignal(int)
    status_changed = pyqtSignal(int, str)  # photo_id, new_status

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = 0
        self._target_cols = 0  # 0 = auto (set on first layout)
        self._thumb_size = 120
        self._tiles: List[ReviewThumbnailWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Zoom slider row
        zoom_row = QHBoxLayout()
        zoom_row.setContentsMargins(4, 4, 4, 0)
        zoom_row.addStretch()

        zoom_out_lbl = QLabel("\u2796")
        zoom_out_lbl.setStyleSheet("color: #888; font-size: 11px; padding: 0 2px;")
        zoom_row.addWidget(zoom_out_lbl)

        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(_MIN_COLS, _MAX_COLS)
        self._zoom_slider.setValue(5)
        self._zoom_slider.setFixedWidth(120)
        self._zoom_slider.setInvertedAppearance(True)  # left = more cols (small), right = fewer cols (big)
        self._zoom_slider.setStyleSheet(
            "QSlider::groove:horizontal { background: #2a2a2a; height: 4px; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #ff9800; width: 12px; margin: -4px 0; border-radius: 6px; }"
        )
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self._zoom_slider)

        zoom_in_lbl = QLabel("\u2795")
        zoom_in_lbl.setStyleSheet("color: #888; font-size: 11px; padding: 0 2px;")
        zoom_row.addWidget(zoom_in_lbl)

        layout.addLayout(zoom_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(8)
        self._grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._scroll.setWidget(self._grid_widget)
        layout.addWidget(self._scroll)

        # Intercept Ctrl+Wheel on the scroll viewport
        self._scroll.viewport().installEventFilter(self)

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = -1 if event.angleDelta().y() > 0 else 1  # scroll up = zoom in = fewer cols
                new_val = max(_MIN_COLS, min(_MAX_COLS, self._zoom_slider.value() + delta))
                self._zoom_slider.setValue(new_val)
                return True  # consume the event
        return super().eventFilter(obj, event)

    def _on_zoom_changed(self, value: int) -> None:
        self._target_cols = value
        self._columns = 0
        self._relayout()

    # ------------------------------------------------------------------
    # Responsive layout
    # ------------------------------------------------------------------
    def _relayout(self) -> None:
        vp = self._scroll.viewport()
        if vp is None:
            return
        w = vp.width()
        spacing = self._grid_layout.spacing()
        cols = max(_MIN_COLS, min(_MAX_COLS, self._target_cols or 5))

        cell_w = (w - (cols - 1) * spacing) // cols
        usable = cell_w - _CELL_PAD

        # Dynamic info panel: ~35% of usable width, hidden when too small
        info_w = max(0, min(400, int(usable * 0.35)))
        if info_w < 80:
            info_w = 0
        thumb = max(40, usable - info_w - (12 if info_w else 0))

        if cols == self._columns and thumb == self._thumb_size and self._tiles:
            return
        self._columns = cols
        self._thumb_size = thumb

        for idx, tw in enumerate(self._tiles):
            tw.set_size(thumb, info_w)
            row, col = divmod(idx, cols)
            self._grid_layout.addWidget(tw, row, col)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._tiles:
            self._relayout()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._tiles:
            self._relayout()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def set_photos(self, photos: List[dict]) -> None:
        """Populate the grid with enriched photo data.

        Each dict should include: id, thumbnail_path, file_name,
        quality_score, status, cull_reasons (JSON str).
        """
        self.clear()
        for p in photos:
            tile = ReviewThumbnailWidget(
                photo_id=p["id"],
                thumb_path=p.get("thumbnail_path"),
                file_name=p.get("file_name", ""),
                score=p.get("quality_score"),
                status=p.get("status", ""),
                cull_reasons=p.get("cull_reasons"),
                size=self._thumb_size,
            )
            tile.clicked.connect(self.photo_selected.emit)
            tile.double_clicked.connect(self.photo_double_clicked.emit)
            tile.status_changed.connect(self.status_changed.emit)
            self._tiles.append(tile)
        self._columns = 0
        self._relayout()

    def clear(self) -> None:
        for tile in self._tiles:
            self._grid_layout.removeWidget(tile)
            tile.deleteLater()
        self._tiles.clear()
