"""Thumbnail widget with lazy-loading support.

Displays a single photo thumbnail with an overlay showing the quality score
and status badge.  Images are loaded asynchronously via ``QThread`` to avoid
blocking the UI when scrolling through hundreds of thumbnails.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

_LOAD_SIZE = 1024  # load at high res for sharp display at any zoom level


class _ImageLoader(QThread):
    """Background thread that loads a pixmap from disk."""

    loaded = pyqtSignal(QPixmap)

    def __init__(self, path: str, size: QSize, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = path
        self._size = size

    def run(self) -> None:
        pix = QPixmap(self._path)
        if not pix.isNull():
            pix = pix.scaled(self._size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.loaded.emit(pix)


class ThumbnailWidget(QWidget):
    """Clickable thumbnail tile for the library grid.

    Attributes:
        photo_id: Database ID of the associated ``Photo``.
    """

    clicked = pyqtSignal(int)  # emits photo_id
    double_clicked = pyqtSignal(int)  # emits photo_id

    def __init__(
        self,
        photo_id: int,
        thumb_path: Optional[str],
        file_name: str,
        score: Optional[float] = None,
        status: str = "",
        size: int = 180,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.photo_id = photo_id
        self._size = size
        self._score = score
        self._status = status
        self._source_pixmap: Optional[QPixmap] = None  # hi-res loaded copy

        self.setFixedSize(size + 8, size + 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 2)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(size, size)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 4px;")
        layout.addWidget(self._image_label)

        self._name_label = QLabel(file_name)
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setStyleSheet("color: #ccc; font-size: 10px;")
        self._name_label.setMaximumWidth(size)
        layout.addWidget(self._name_label)

        # Lazy load the thumbnail at high resolution.
        if thumb_path and Path(thumb_path).is_file():
            self._loader = _ImageLoader(thumb_path, QSize(_LOAD_SIZE, _LOAD_SIZE))
            self._loader.loaded.connect(self._on_loaded)
            self._loader.start()
        else:
            self._image_label.setText("No thumb")
            self._image_label.setStyleSheet(
                "background: #1a1a1a; color: #555; border: 1px solid #2a2a2a; border-radius: 4px;"
            )

    def _on_loaded(self, pixmap: QPixmap) -> None:
        if not pixmap.isNull():
            self._source_pixmap = pixmap
            display = pixmap.scaled(
                QSize(self._size, self._size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.setPixmap(display)

    def set_size(self, size: int) -> None:
        """Resize the tile dynamically (called by LibraryView on relayout)."""
        if size == self._size:
            return
        self._size = size
        self.setFixedSize(size + 8, size + 28)
        self._image_label.setFixedSize(size, size)
        self._name_label.setMaximumWidth(size)
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

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if self._score is not None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # Score badge (top-right).
            badge_color = (
                QColor("#4caf50") if self._score >= 0.8
                else QColor("#ff9800") if self._score >= 0.3
                else QColor("#f44336")
            )
            painter.setBrush(badge_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(self._size - 30, 6, 34, 18, 6, 6)

            painter.setPen(QColor("white"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.drawText(self._size - 28, 6, 30, 18, Qt.AlignmentFlag.AlignCenter, f"{self._score:.0%}")
            painter.end()
