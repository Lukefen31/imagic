"""Style chooser dialog — two-tab picker for edit styles and colour grades.

**Tab 1 — Edit Style**: The original 5 scene-based PP3 presets (low_light,
bright_outdoor, high_contrast, portrait, balanced) shown as a grid of sample
photos × preset with live rawpy-based previews.

**Tab 2 — Colour Grade**: The 8 artistic colour grades from the per-photo
PP3 generator, with live thumbnail-based previews.

Both tabs emit ``style_chosen(str)`` — the export service already accepts
either a legacy preset name or a colour-grade key.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from imagic.services.pp3_generator import GRADES
from imagic.services.style_preview import (
    PRESET_INFO,
    PhotoPreviews,
    generate_style_previews,
    pick_sample_photos,
)

logger = logging.getLogger(__name__)

_PREVIEW_SIZE = 200
_COLUMN_ORDER = [
    "original", "low_light", "bright_outdoor", "high_contrast", "portrait", "balanced",
]
_GRADE_ORDER = list(GRADES.keys())

# ======================================================================
# Workers
# ======================================================================


class _StylePreviewWorker(QThread):
    """Generate rawpy-based style previews for the Edit Style tab."""

    preview_ready = pyqtSignal(object)  # PhotoPreviews
    finished_all = pyqtSignal()

    def __init__(
        self, photos: List[dict], preview_dir: Path, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._photos = photos
        self._preview_dir = preview_dir

    def run(self) -> None:
        for p in self._photos:
            raw_path = Path(p["file_path"])
            thumb_path = Path(p["thumbnail_path"]) if p.get("thumbnail_path") else None
            result = generate_style_previews(
                raw_path=raw_path,
                photo_id=p["id"],
                file_name=p["file_name"],
                preview_dir=self._preview_dir,
                thumbnail_path=thumb_path,
            )
            self.preview_ready.emit(result)
        self.finished_all.emit()


class _GradePreviewWorker(QThread):
    """Render colour-grade previews from thumbnails in the background."""

    # Emits (grade_key, row_index, QPixmap)
    preview_ready = pyqtSignal(str, int, object)
    finished_all = pyqtSignal()

    def __init__(
        self, thumbnails: List[Path], parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._thumbnails = thumbnails

    def run(self) -> None:
        from imagic.services.grade_preview import render_grade_preview

        for row_idx, thumb in enumerate(self._thumbnails):
            for key in _GRADE_ORDER:
                grade = GRADES[key]
                pil = render_grade_preview(thumb, grade, size=_PREVIEW_SIZE)
                if pil is not None:
                    # Convert PIL → QPixmap via QImage.
                    data = pil.tobytes("raw", "RGB")
                    qimg = QImage(
                        data, pil.width, pil.height,
                        3 * pil.width, QImage.Format.Format_RGB888,
                    )
                    pix = QPixmap.fromImage(qimg.copy())  # copy to detach buffer
                    self.preview_ready.emit(key, row_idx, pix)
        self.finished_all.emit()


# ======================================================================
# Shared clickable image widget
# ======================================================================


class _ClickableImage(QLabel):
    """Label that acts as a selectable preview image."""

    clicked = pyqtSignal(str)

    def __init__(
        self, key: str, size: int = _PREVIEW_SIZE, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self._selected = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("…")
        self._refresh()

    def set_pixmap(self, pix: QPixmap) -> None:
        scaled = pix.scaled(
            QSize(self.width() - 4, self.height() - 4),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._refresh()

    def _refresh(self) -> None:
        if self._selected:
            self.setStyleSheet(
                "border: 3px solid #4fc3f7; border-radius: 4px; background: #1a1a2e;"
            )
        else:
            self.setStyleSheet(
                "border: 2px solid #333; border-radius: 4px; background: #1e1e1e;"
            )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self._key)


# ======================================================================
# Tab 1 — Edit Style (legacy PP3 presets)
# ======================================================================


class _EditStyleTab(QWidget):
    """Grid of sample photos × 5 PP3 presets + original."""

    style_clicked = pyqtSignal(str)

    def __init__(
        self, sample_photos: List[dict], preview_dir: Path, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._images: List[_ClickableImage] = []
        self._rows_added = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Column headers
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        for preset in _COLUMN_ORDER:
            label = "Camera Default" if preset == "original" else \
                PRESET_INFO.get(preset, (preset.replace("_", " ").title(), ""))[0]
            lbl = QLabel(label)
            lbl.setFixedWidth(_PREVIEW_SIZE)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            lbl.setStyleSheet("color: #ccc;")
            header_layout.addWidget(lbl)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(8)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(self._grid_widget)
        layout.addWidget(scroll, stretch=1)

        # Progress
        self._progress = QProgressBar()
        self._progress.setMaximum(len(sample_photos))
        self._progress.setFormat("Generating previews… %v / %m")
        self._progress.setStyleSheet("QProgressBar { text-align: center; }")
        layout.addWidget(self._progress)

        # Start worker
        self._worker = _StylePreviewWorker(sample_photos, preview_dir, self)
        self._worker.preview_ready.connect(self._on_preview_ready)
        self._worker.finished_all.connect(lambda: self._progress.hide())
        self._worker.start()

    @property
    def image_widgets(self) -> List[_ClickableImage]:
        return self._images

    def _on_preview_ready(self, previews: PhotoPreviews) -> None:
        row = self._rows_added
        for col_idx, preset in enumerate(_COLUMN_ORDER):
            sp = previews.styles.get(preset)
            widget = _ClickableImage(preset, _PREVIEW_SIZE)
            if sp and sp.path and sp.path.is_file():
                pix = QPixmap(str(sp.path))
                if not pix.isNull():
                    widget.set_pixmap(pix)
            widget.clicked.connect(self._on_clicked)
            self._images.append(widget)
            self._grid_layout.addWidget(widget, row, col_idx)
        self._rows_added += 1
        self._progress.setValue(self._rows_added)

    def _on_clicked(self, key: str) -> None:
        if key != "original":
            self.style_clicked.emit(key)

    def stop(self) -> None:
        if self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)


# ======================================================================
# Tab 2 — Colour Grade
# ======================================================================


class _ColourGradeTab(QWidget):
    """Grid of sample thumbnails × 8 colour grades."""

    grade_clicked = pyqtSignal(str)

    def __init__(
        self, sample_thumbnails: List[Path], parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._images: List[_ClickableImage] = []
        # Map (grade_key, row) → _ClickableImage for later pixmap updates.
        self._image_map: Dict[tuple, _ClickableImage] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Column headers
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        for key in _GRADE_ORDER:
            grade = GRADES[key]
            lbl = QLabel(grade.name)
            lbl.setFixedWidth(_PREVIEW_SIZE)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            lbl.setStyleSheet("color: #ccc;")
            header_layout.addWidget(lbl)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Scroll area with pre-built grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        grid_widget = QWidget()
        self._grid_layout = QGridLayout(grid_widget)
        self._grid_layout.setSpacing(8)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        for row_idx in range(len(sample_thumbnails)):
            for col_idx, key in enumerate(_GRADE_ORDER):
                widget = _ClickableImage(key, _PREVIEW_SIZE)
                widget.clicked.connect(self._on_clicked)
                self._images.append(widget)
                self._image_map[(key, row_idx)] = widget
                self._grid_layout.addWidget(widget, row_idx, col_idx)

        scroll.setWidget(grid_widget)
        layout.addWidget(scroll, stretch=1)

        # Progress
        self._progress = QProgressBar()
        total = len(sample_thumbnails) * len(_GRADE_ORDER)
        self._progress.setMaximum(total)
        self._progress.setFormat("Rendering grades… %v / %m")
        self._progress.setStyleSheet("QProgressBar { text-align: center; }")
        self._count = 0
        layout.addWidget(self._progress)

        # Worker
        self._worker = _GradePreviewWorker(sample_thumbnails, self)
        self._worker.preview_ready.connect(self._on_preview_ready)
        self._worker.finished_all.connect(lambda: self._progress.hide())
        self._worker.start()

    @property
    def image_widgets(self) -> List[_ClickableImage]:
        return self._images

    def _on_preview_ready(self, key: str, row_idx: int, pix: QPixmap) -> None:
        widget = self._image_map.get((key, row_idx))
        if widget and pix and not pix.isNull():
            widget.set_pixmap(pix)
        self._count += 1
        self._progress.setValue(self._count)

    def _on_clicked(self, key: str) -> None:
        self.grade_clicked.emit(key)

    def stop(self) -> None:
        if self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)


# ======================================================================
# Main dialog
# ======================================================================


class StyleChooserDialog(QDialog):
    """Modal dialog with two tabs: Edit Style and Colour Grade.

    Signals:
        style_chosen: Emitted with the preset / grade key when user confirms.
    """

    style_chosen = pyqtSignal(str)

    def __init__(
        self,
        sample_photos: List[dict],
        preview_dir: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose Your Edit Style")
        self.setMinimumSize(1300, 700)
        self.setModal(True)

        self._selected: Optional[str] = None
        self._all_images: List[_ClickableImage] = []

        main_layout = QVBoxLayout(self)

        # Header
        header = QLabel("Choose how your photos will look")
        header.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        header.setStyleSheet("color: #eee; padding: 8px;")
        main_layout.addWidget(header)

        subtitle = QLabel(
            "Edit Style applies a full processing preset. "
            "Colour Grade layers artistic colour on top of per-photo optimisation."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #999; padding: 0 8px 8px 8px;")
        main_layout.addWidget(subtitle)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabBar::tab { padding: 8px 24px; font-size: 11pt; }"
        )

        # Tab 1 — Edit Style
        self._style_tab = _EditStyleTab(sample_photos, preview_dir)
        self._style_tab.style_clicked.connect(self._on_selection)
        self._tabs.addTab(self._style_tab, "Edit Style")

        # Tab 2 — Colour Grade
        thumbnails = [
            Path(p["thumbnail_path"])
            for p in sample_photos
            if p.get("thumbnail_path") and Path(p["thumbnail_path"]).is_file()
        ]
        self._grade_tab = _ColourGradeTab(thumbnails)
        self._grade_tab.grade_clicked.connect(self._on_selection)
        self._tabs.addTab(self._grade_tab, "Colour Grade")

        main_layout.addWidget(self._tabs, stretch=1)

        # Selection info
        self._selection_label = QLabel("No style selected yet")
        self._selection_label.setFont(QFont("Segoe UI", 11))
        self._selection_label.setStyleSheet("color: #4fc3f7; padding: 6px;")
        main_layout.addWidget(self._selection_label)

        self._desc_label = QLabel("")
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color: #999; padding: 0 6px 6px 6px;")
        main_layout.addWidget(self._desc_label)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setMinimumWidth(160)
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #4fc3f7; color: #111; font-weight: bold; "
            "padding: 8px 24px; border-radius: 4px; } "
            "QPushButton:disabled { background: #555; color: #888; }"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(self._apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(100)
        cancel_btn.setStyleSheet("QPushButton { padding: 8px 24px; border-radius: 4px; }")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        main_layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_selection(self, key: str) -> None:
        self._selected = key

        # Update highlights across both tabs.
        for w in self._style_tab.image_widgets:
            w.set_selected(w._key == key)
        for w in self._grade_tab.image_widgets:
            w.set_selected(w._key == key)

        # Look up display info.
        grade = GRADES.get(key)
        if grade:
            self._selection_label.setText(f"Selected: {grade.name}")
            self._desc_label.setText(grade.description)
        else:
            label, desc = PRESET_INFO.get(key, (key.replace("_", " ").title(), ""))
            self._selection_label.setText(f"Selected: {label}")
            self._desc_label.setText(desc)

        self._apply_btn.setEnabled(True)

    def _on_apply(self) -> None:
        if self._selected:
            self.style_chosen.emit(self._selected)
            self.accept()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._style_tab.stop()
        self._grade_tab.stop()
        super().closeEvent(event)
