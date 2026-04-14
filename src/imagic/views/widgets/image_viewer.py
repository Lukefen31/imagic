"""Full-size image viewer with before/after comparison.

Opens as a popup window showing the exported image alongside the original
(decoded from the RAW file at full quality).  Supports keyboard navigation
(left/right arrows) when used with a list of photos.

Also provides a **Re-Edit** button that opens a sidebar with manual editing
controls.  Overrides can be applied and the photo re-exported in-place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Background RAW decoder (same pattern as culling_preview)
# ======================================================================

class _RawDecodeWorker(QThread):
    """Decode a RAW file to a QPixmap in the background."""

    decoded = pyqtSignal(int, QPixmap)  # index, pixmap
    decode_failed = pyqtSignal(int, str)  # index, error message

    def __init__(self, index: int, file_path: str, parent=None):
        super().__init__(parent)
        self._index = index
        self._file_path = file_path

    def run(self) -> None:
        try:
            suffix = Path(self._file_path).suffix.lower()
            if suffix in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
                pix = QPixmap(self._file_path)
                if not pix.isNull():
                    self.decoded.emit(self._index, pix)
                    return

            import rawpy

            with rawpy.imread(self._file_path) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                    half_size=True,
                )
            h, w, _ = rgb.shape
            bytes_per_line = 3 * w
            qimg = QImage(
                rgb.data.tobytes(), w, h, bytes_per_line,
                QImage.Format.Format_RGB888,
            )
            pix = QPixmap.fromImage(qimg)
            if not pix.isNull():
                self.decoded.emit(self._index, pix)
            else:
                self.decode_failed.emit(self._index, "QPixmap conversion returned null")
        except Exception as exc:
            logger.warning("RAW decode failed for viewer (%s): %s", self._file_path, exc)
            self.decode_failed.emit(self._index, str(exc))


# ======================================================================
# Re-Edit Sidebar
# ======================================================================

_SLIDER_STYLE = (
    "QSlider::groove:horizontal { height: 6px; background: #444; border-radius: 3px; }"
    "QSlider::handle:horizontal { width: 14px; margin: -4px 0; background: #4caf50; "
    "border: 2px solid #1a1a1a; border-radius: 7px; }"
    "QSlider::handle:horizontal:hover { background: #66bb6a; }"
    "QSlider::sub-page:horizontal { background: #4caf50; border-radius: 3px; }"
)

_GRADES = [
    "natural", "film_warm", "film_cool", "moody",
    "vibrant", "cinematic", "faded", "bw_classic",
]


class _ReEditSidebar(QFrame):
    """Manual editing controls shown as a sidebar inside the viewer.

    Emits ``apply_requested(photo_id, overrides_dict)`` when the user
    clicks *Apply & Re-Export*.
    """

    apply_requested = pyqtSignal(int, dict)  # photo_id, overrides

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(260)
        self.setStyleSheet(
            "QFrame { background: #141414; border-left: 1px solid #2a2a2a; }"
        )
        self._photo_id: int = 0
        self._current_overrides: Dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("RE-EDIT")
        title.setStyleSheet(
            "color: #ff9800; font-weight: bold; font-size: 14px; border: none; letter-spacing: 2px;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # -- Colour grade picker --
        grade_lbl = QLabel("Colour Grade")
        grade_lbl.setStyleSheet("color: #ccc; font-size: 11px; border: none;")
        layout.addWidget(grade_lbl)
        self._grade_combo = QComboBox()
        self._grade_combo.addItems(_GRADES)
        self._grade_combo.setStyleSheet(
            "QComboBox { background: #2a2a2a; color: #eee; padding: 6px; "
            "border: 1px solid #444; border-radius: 4px; }"
            "QComboBox:hover { border-color: #555; }"
            "QComboBox:focus { border-color: #ff9800; }"
        )
        layout.addWidget(self._grade_combo)

        # -- Sliders --
        self._sliders: Dict[str, QSlider] = {}
        slider_defs = [
            ("exposure", "Exposure", -100, 100, 0),
            ("contrast", "Contrast", -100, 100, 0),
            ("saturation", "Saturation", -100, 100, 0),
            ("sharpness", "Sharpness", 0, 200, 100),
            ("noise_reduction", "Noise Reduction", 0, 100, 0),
            ("wb_warmth", "WB Warmth", -50, 50, 0),
        ]
        for key, label_text, lo, hi, default in slider_defs:
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #ccc; font-size: 11px; border: none;")
            layout.addWidget(lbl)

            row = QHBoxLayout()
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(default)
            slider.setStyleSheet(_SLIDER_STYLE)
            val_lbl = QLabel(str(default))
            val_lbl.setFixedWidth(32)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setStyleSheet("color: #eee; font-size: 11px; border: none;")
            slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(str(v)))
            row.addWidget(slider, stretch=1)
            row.addWidget(val_lbl)
            layout.addLayout(row)
            self._sliders[key] = slider

        layout.addStretch()

        # -- Apply button --
        self._apply_btn = QPushButton("Apply && Re-Export")
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #ff9800; color: #111; font-weight: bold; "
            "border: none; border-radius: 6px; padding: 8px; font-size: 12px; }"
            "QPushButton:hover { background: #ffa726; }"
            "QPushButton:pressed { background: #f57c00; }"
            "QPushButton:disabled { background: #333; color: #666; }"
        )
        self._apply_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self._apply_btn)

        # -- Reset button --
        reset_btn = QPushButton("Reset to Auto")
        reset_btn.setStyleSheet(
            "QPushButton { background: #2a2a2a; color: #ccc; border: 1px solid #444; "
            "border-radius: 6px; padding: 6px; font-size: 11px; }"
            "QPushButton:hover { background: #333; border-color: #555; }"
            "QPushButton:pressed { background: #222; }"
        )
        reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reset_btn.clicked.connect(self._reset_sliders)
        layout.addWidget(reset_btn)

    # ------------------------------------------------------------------

    def load_photo(self, photo_id: int, overrides: Dict) -> None:
        """Populate controls from existing overrides for a photo."""
        self._photo_id = photo_id
        self._current_overrides = overrides or {}

        grade = self._current_overrides.get("color_grade", "natural")
        idx = self._grade_combo.findText(grade)
        if idx >= 0:
            self._grade_combo.setCurrentIndex(idx)

        for key, slider in self._sliders.items():
            val = self._current_overrides.get(key, slider.minimum() + (slider.maximum() - slider.minimum()) // 2
                if key == "sharpness" else 0)
            # Use stored default if key not in overrides
            if key not in self._current_overrides:
                if key == "sharpness":
                    val = 100
                else:
                    val = 0
            slider.setValue(int(val))

    def _gather_overrides(self) -> Dict:
        overrides = {"color_grade": self._grade_combo.currentText()}
        for key, slider in self._sliders.items():
            overrides[key] = slider.value()
        return overrides

    def _on_apply(self) -> None:
        overrides = self._gather_overrides()
        self._apply_btn.setEnabled(False)
        self._apply_btn.setText("Exporting…")
        self.apply_requested.emit(self._photo_id, overrides)

    def finish_apply(self, success: bool) -> None:
        """Called after re-export completes to re-enable the button."""
        self._apply_btn.setEnabled(True)
        self._apply_btn.setText("Apply && Re-Export")

    def _reset_sliders(self) -> None:
        self._grade_combo.setCurrentIndex(0)
        for key, slider in self._sliders.items():
            slider.setValue(100 if key == "sharpness" else 0)


# ======================================================================
# Main Viewer Dialog
# ======================================================================

class ImageViewerDialog(QDialog):
    """Full-size before/after image comparison dialog with re-edit support.

    Emits ``reedit_requested(photo_id, overrides_dict)`` so the host
    application can perform the actual re-export.

    Args:
        photo_list: Full list of photo dicts for navigation.  Each dict
            must include ``file_path`` (RAW), ``export_path``, and
            optionally ``thumbnail_path`` and ``manual_overrides``.
        current_index: Which photo to show initially.
        parent: Parent widget.
    """

    reedit_requested = pyqtSignal(int, dict)  # photo_id, overrides

    def __init__(
        self,
        photo_list: List[dict],
        current_index: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Viewer")
        self.setMinimumSize(1200, 700)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._photos = photo_list
        self._index = current_index
        self._raw_cache: Dict[int, QPixmap] = {}
        self._RAW_CACHE_LIMIT = 5  # Max decoded RAW images in memory
        self._decode_worker: Optional[_RawDecodeWorker] = None
        self._sidebar_visible = False

        # === Outer horizontal layout: viewer area + sidebar ===
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- Left: main viewer ---
        viewer_widget = QWidget()
        layout = QVBoxLayout(viewer_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        self._header = QLabel()
        self._header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._header.setStyleSheet(
            "color: #eee; font-size: 13px; font-weight: bold; "
            "background: #141414; padding: 10px; letter-spacing: 0.5px;"
        )
        layout.addWidget(self._header)

        # Splitter: before (left) | after (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Before panel
        before_panel = QWidget()
        before_layout = QVBoxLayout(before_panel)
        before_layout.setContentsMargins(4, 4, 4, 4)

        before_title = QLabel("BEFORE (Original)")
        before_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        before_title.setStyleSheet("color: #ff9800; font-weight: bold; font-size: 12px;")
        before_layout.addWidget(before_title)

        self._before_label = QLabel()
        self._before_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._before_label.setStyleSheet("background: #111;")
        self._before_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        before_layout.addWidget(self._before_label)

        splitter.addWidget(before_panel)

        # After panel
        after_panel = QWidget()
        after_layout = QVBoxLayout(after_panel)
        after_layout.setContentsMargins(4, 4, 4, 4)

        after_title = QLabel("AFTER (Exported)")
        after_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        after_title.setStyleSheet("color: #4caf50; font-weight: bold; font-size: 12px;")
        after_layout.addWidget(after_title)

        self._after_label = QLabel()
        self._after_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._after_label.setStyleSheet("background: #111;")
        self._after_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        after_layout.addWidget(self._after_label)

        splitter.addWidget(after_panel)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter, stretch=1)

        # Footer with nav + re-edit button
        footer_widget = QWidget()
        footer_widget.setStyleSheet("background: #141414;")
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(10, 4, 10, 4)

        nav_label = QLabel("← → Arrow keys to navigate  |  Esc to close")
        nav_label.setStyleSheet("color: #666;")
        footer_layout.addWidget(nav_label, stretch=1)

        self._reedit_btn = QPushButton("Re-Edit ✏")
        self._reedit_btn.setStyleSheet(
            "QPushButton { background: #ff9800; color: #111; font-weight: bold; "
            "border: none; border-radius: 6px; padding: 6px 16px; font-size: 12px; }"
            "QPushButton:hover { background: #ffa726; }"
            "QPushButton:pressed { background: #f57c00; }"
        )
        self._reedit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._reedit_btn.clicked.connect(self._toggle_sidebar)
        footer_layout.addWidget(self._reedit_btn)

        layout.addWidget(footer_widget)
        outer.addWidget(viewer_widget, stretch=1)

        # --- Right: re-edit sidebar (hidden by default) ---
        self._sidebar = _ReEditSidebar()
        self._sidebar.setVisible(False)
        self._sidebar.apply_requested.connect(self._on_sidebar_apply)
        outer.addWidget(self._sidebar)

        # Load initial image
        self._load_current()

    # ------------------------------------------------------------------
    # Sidebar toggling
    # ------------------------------------------------------------------

    def _toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self._sidebar.setVisible(self._sidebar_visible)
        if self._sidebar_visible:
            p = self._photos[self._index]
            overrides = p.get("manual_overrides") or {}
            if isinstance(overrides, str):
                try:
                    overrides = json.loads(overrides)
                except (json.JSONDecodeError, TypeError):
                    overrides = {}
            self._sidebar.load_photo(p.get("id", 0), overrides)
        self.setFocus()

    def _on_sidebar_apply(self, photo_id: int, overrides: dict) -> None:
        """Forward the re-export request to the host and refresh after."""
        self.reedit_requested.emit(photo_id, overrides)

    def on_reedit_finished(self, success: bool, new_export_path: str = "") -> None:
        """Called by the host after re-export completes."""
        self._sidebar.finish_apply(success)
        if success and new_export_path:
            # Update the local photo dict so the after panel refreshes.
            self._photos[self._index]["export_path"] = new_export_path
            self._show_after()
        self.setFocus()

    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------

    def _load_current(self) -> None:
        """Load the current photo into the viewer."""
        if not self._photos or self._index < 0 or self._index >= len(self._photos):
            return

        p = self._photos[self._index]
        fname = p.get("file_name", "Unknown")
        total = len(self._photos)
        self._header.setText(f"{fname}  ({self._index + 1} / {total})")

        # Before — full-quality RAW decode (thumbnail as placeholder)
        self._show_before_placeholder(p)
        self._start_raw_decode(p)

        # After (exported JPEG — already full quality)
        self._show_after()

        # Update sidebar if visible
        if self._sidebar_visible:
            overrides = p.get("manual_overrides") or {}
            if isinstance(overrides, str):
                try:
                    overrides = json.loads(overrides)
                except (json.JSONDecodeError, TypeError):
                    overrides = {}
            self._sidebar.load_photo(p.get("id", 0), overrides)

    def _show_before_placeholder(self, p: dict) -> None:
        """Show thumbnail as a quick placeholder for the before side."""
        # Check raw cache first
        if self._index in self._raw_cache:
            self._set_scaled_pixmap(self._before_label, pix=self._raw_cache[self._index])
            return

        thumb_path = p.get("thumbnail_path", "")
        if thumb_path and Path(thumb_path).is_file():
            self._set_scaled_pixmap(self._before_label, path=thumb_path)
        else:
            self._before_label.setText("Decoding RAW…")
            self._before_label.setStyleSheet("background: #111; color: #666;")

    def _start_raw_decode(self, p: dict) -> None:
        """Kick off background RAW decode for the before side."""
        if self._index in self._raw_cache:
            return  # Already decoded

        file_path = p.get("file_path", "")
        if not file_path or not Path(file_path).is_file():
            return  # No RAW available; thumbnail stays

        idx = self._index
        self._decode_worker = _RawDecodeWorker(idx, file_path, self)
        self._decode_worker.decoded.connect(self._on_raw_decoded)
        self._decode_worker.decode_failed.connect(self._on_raw_decode_failed)
        self._decode_worker.start()

    def _on_raw_decode_failed(self, index: int, error: str) -> None:
        """Slot: background decode failed — show message instead of hanging."""
        logger.warning("RAW decode failed for viewer index %d: %s", index, error)
        if index == self._index:
            self._before_label.setText("⚠ Could not decode RAW")
            self._before_label.setStyleSheet("background: #111; color: #c62828;")

    def _on_raw_decoded(self, index: int, pix: QPixmap) -> None:
        """Slot: background decode finished — cache and display."""
        self._raw_cache[index] = pix
        # Evict oldest entries to keep memory bounded
        if len(self._raw_cache) > self._RAW_CACHE_LIMIT:
            to_remove = sorted(self._raw_cache.keys())
            for k in to_remove[: len(self._raw_cache) - self._RAW_CACHE_LIMIT]:
                if k != self._index:
                    del self._raw_cache[k]
        if index == self._index:
            self._set_scaled_pixmap(self._before_label, pix=pix)

    def _show_after(self) -> None:
        """Load the exported JPEG into the after panel."""
        p = self._photos[self._index]
        export_path = p.get("export_path", "")
        if export_path and Path(export_path).is_file():
            self._set_scaled_pixmap(self._after_label, path=export_path)
        else:
            self._after_label.setText("Not yet exported")
            self._after_label.setStyleSheet("background: #111; color: #666;")

    def _set_scaled_pixmap(
        self, label: QLabel, path: str = "", pix: QPixmap | None = None,
    ) -> None:
        """Scale a pixmap (from file *or* pre-loaded) to fit the label."""
        if pix is None:
            pix = QPixmap(path)
        if not pix.isNull():
            available = label.size()
            scaled = pix.scaled(
                available,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            label.setPixmap(scaled)
            label.setStyleSheet("background: #111;")
        else:
            label.setText("Failed to load image")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Right:
            if self._index < len(self._photos) - 1:
                self._index += 1
                self._load_current()
        elif event.key() == Qt.Key.Key_Left:
            if self._index > 0:
                self._index -= 1
                self._load_current()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.setFocus()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._rescale_current()

    def _rescale_current(self) -> None:
        """Re-scale the currently displayed images to fit the new size."""
        if not self._photos or self._index < 0 or self._index >= len(self._photos):
            return
        p = self._photos[self._index]

        # Before — use cached RAW pixmap if available
        if self._index in self._raw_cache:
            self._set_scaled_pixmap(self._before_label, pix=self._raw_cache[self._index])
        else:
            thumb = p.get("thumbnail_path", "")
            if thumb and Path(thumb).is_file():
                self._set_scaled_pixmap(self._before_label, path=thumb)

        # After
        export = p.get("export_path", "")
        if export and Path(export).is_file():
            self._set_scaled_pixmap(self._after_label, path=export)
