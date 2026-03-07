"""Library view — scrollable grid of photo thumbnails."""

from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from imagic.views.widgets.filter_bar import FilterBar
from imagic.views.widgets.thumbnail_widget import ThumbnailWidget

logger = logging.getLogger(__name__)


class LibraryView(QWidget):
    """Scrollable thumbnail grid with filtering.

    Signals:
        photo_selected: Emitted when a thumbnail is clicked (``photo_id``).
    """

    photo_selected = pyqtSignal(int)

    def __init__(self, columns: int = 5, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = columns
        self._thumbnails: List[ThumbnailWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Filter bar
        self.filter_bar = FilterBar()
        layout.addWidget(self.filter_bar)

        # Scroll area with grid
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(6)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._scroll.setWidget(self._grid_widget)

        layout.addWidget(self._scroll)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def set_photos(
        self,
        photos: List[dict],
    ) -> None:
        """Populate the grid with photo data.

        Args:
            photos: List of dicts with keys ``id``, ``thumbnail_path``,
                ``file_name``, ``quality_score``, ``status``.
        """
        self.clear()
        for idx, p in enumerate(photos):
            thumb = ThumbnailWidget(
                photo_id=p["id"],
                thumb_path=p.get("thumbnail_path"),
                file_name=p.get("file_name", ""),
                score=p.get("quality_score"),
                status=p.get("status", ""),
            )
            thumb.clicked.connect(self.photo_selected.emit)
            row, col = divmod(idx, self._columns)
            self._grid_layout.addWidget(thumb, row, col)
            self._thumbnails.append(thumb)

    def clear(self) -> None:
        """Remove all thumbnails from the grid."""
        for thumb in self._thumbnails:
            self._grid_layout.removeWidget(thumb)
            thumb.deleteLater()
        self._thumbnails.clear()
