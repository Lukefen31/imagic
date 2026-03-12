"""Export gallery view — browse exported photos with before/after comparison.

Displays a scrollable grid of exported JPEGs.  Clicking an image opens
a full-size before/after comparison overlay.

Uses a single background worker thread with a queue to avoid spawning
hundreds of threads when many exports exist.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_THUMB_SIZE = 180


class _BatchImageLoader(QThread):
    """Single worker thread that loads thumbnails from a queue."""

    loaded = pyqtSignal(int, QPixmap)  # index, pixmap

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: List[tuple] = []  # (index, path, size)
        self._running = True

    def enqueue(self, index: int, path: str, size: int) -> None:
        self._queue.append((index, path, size))

    def run(self) -> None:
        while self._running and self._queue:
            idx, path, size = self._queue.pop(0)
            try:
                pix = QPixmap(path)
                if not pix.isNull():
                    pix = pix.scaled(
                        QSize(size, size),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.loaded.emit(idx, pix)
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        self._queue.clear()


class _ExportTile(QWidget):
    """Single export thumbnail tile (no thread — loaded externally)."""

    clicked = pyqtSignal(int)  # index
    double_clicked = pyqtSignal(int)  # index

    def __init__(
        self,
        index: int,
        file_name: str,
        status: str = "",
        size: int = _THUMB_SIZE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self.setFixedSize(size + 8, size + 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 2)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(size, size)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 4px;")
        self._image_label.setText("Loading…")
        layout.addWidget(self._image_label)

        name_label = QLabel(file_name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("color: #ccc; font-size: 10px;")
        name_label.setMaximumWidth(size)
        layout.addWidget(name_label)

        if status:
            badge_colors = {
                "exported": "#4caf50",
                "error": "#f44336",
                "processing": "#ff9800",
            }
            color = badge_colors.get(status.lower(), "#666")
            name_label.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: bold;")

    def set_pixmap(self, pixmap: QPixmap) -> None:
        if not pixmap.isNull():
            self._image_label.setPixmap(pixmap)
        else:
            self._image_label.setText("No image")

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self._index)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.double_clicked.emit(self._index)


class ExportGalleryView(QWidget):
    """Scrollable grid showing exported photos.

    Signals:
        photo_clicked: Emitted with ``(photo_id, export_path, thumbnail_path)``
            when a tile is clicked.
    """

    photo_clicked = pyqtSignal(int, str, str)  # photo_id, export_path, thumb_path
    photo_double_clicked = pyqtSignal(int, str, str)  # photo_id — open editor

    def __init__(self, columns: int = 5, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = columns
        self._tiles: List[_ExportTile] = []
        self._photo_data: List[dict] = []
        self._loader: Optional[_BatchImageLoader] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QLabel("  Exported Images")
        header.setStyleSheet(
            "color: #eee; font-size: 13px; font-weight: bold; "
            "padding: 10px; background: #141414; letter-spacing: 0.5px;"
        )
        layout.addWidget(header)

        # Scroll area with grid
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(6)
        self._grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._scroll.setWidget(self._grid_widget)
        layout.addWidget(self._scroll)

        # Empty state
        self._empty_label = QLabel("No exported images yet.\nRun the pipeline to see results here.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #555; font-size: 14px; padding: 40px;")
        layout.addWidget(self._empty_label)

    def set_exports(self, photos: List[dict]) -> None:
        """Populate the gallery with export data.

        Args:
            photos: List of dicts with keys ``id``, ``file_name``,
                ``export_path``, ``thumbnail_path``, ``status``.
        """
        self.clear()
        self._photo_data = photos

        if not photos:
            self._empty_label.show()
            self._scroll.hide()
            return

        self._empty_label.hide()
        self._scroll.show()

        # Create tiles (without loading images yet).
        for idx, p in enumerate(photos):
            export_path = p.get("export_path", "")
            if not export_path or not Path(export_path).is_file():
                continue

            tile = _ExportTile(
                index=idx,
                file_name=p.get("file_name", ""),
                status=p.get("status", ""),
            )
            tile.clicked.connect(self._on_tile_clicked)
            tile.double_clicked.connect(self._on_tile_double_clicked)
            row, col = divmod(len(self._tiles), self._columns)
            self._grid_layout.addWidget(tile, row, col)
            self._tiles.append(tile)

        # Load images via a single background thread.
        self._loader = _BatchImageLoader(self)
        self._loader.loaded.connect(self._on_image_loaded)

        for idx, p in enumerate(photos):
            export_path = p.get("export_path", "")
            if export_path and Path(export_path).is_file():
                self._loader.enqueue(idx, export_path, _THUMB_SIZE)

        self._loader.start()

    def _on_image_loaded(self, index: int, pixmap: QPixmap) -> None:
        if 0 <= index < len(self._tiles):
            self._tiles[index].set_pixmap(pixmap)

    def _on_tile_clicked(self, index: int) -> None:
        if 0 <= index < len(self._photo_data):
            p = self._photo_data[index]
            self.photo_clicked.emit(
                p.get("id", 0),
                p.get("export_path", ""),
                p.get("thumbnail_path", ""),
            )

    def _on_tile_double_clicked(self, index: int) -> None:
        if 0 <= index < len(self._photo_data):
            p = self._photo_data[index]
            self.photo_double_clicked.emit(
                p.get("id", 0),
                p.get("export_path", ""),
                p.get("thumbnail_path", ""),
            )

    def clear(self) -> None:
        if self._loader and self._loader.isRunning():
            self._loader.stop()
            self._loader.quit()
            self._loader.wait(2000)
            self._loader = None
        for tile in self._tiles:
            self._grid_layout.removeWidget(tile)
            tile.deleteLater()
        self._tiles.clear()
        self._photo_data.clear()
