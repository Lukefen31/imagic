"""Culling preview dialog — red/green list showing why each photo was kept/trashed.

Features:
* Filter combo to show All / KEEP / TRASH / REVIEW photos.
* Colour-coded rows with per-metric verdicts.
* Manual KEEP / TRASH buttons per row.
* Double-click opens a gallery modal with large image, overlay metrics,
  and manual status selector.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_THUMB_SIZE = 64

# Colour palette for row backgrounds.
_KEEP_BG = QColor(46, 125, 50, 45)       # green tint
_TRASH_BG = QColor(198, 40, 40, 45)      # red tint
_CULLED_BG = QColor(120, 120, 120, 30)   # grey tint

_BTN_STYLE_KEEP = (
    "QPushButton { background: #2e7d32; color: white; border: none; "
    "border-radius: 4px; padding: 4px 10px; font-size: 10px; font-weight: bold; }"
    "QPushButton:hover { background: #388e3c; }"
    "QPushButton:pressed { background: #1b5e20; }"
    "QPushButton:disabled { background: #1b5e20; color: #8bc34a; }"
)
_BTN_STYLE_TRASH = (
    "QPushButton { background: #c62828; color: white; border: none; "
    "border-radius: 4px; padding: 4px 10px; font-size: 10px; font-weight: bold; }"
    "QPushButton:hover { background: #d32f2f; }"
    "QPushButton:pressed { background: #7f1d1d; }"
    "QPushButton:disabled { background: #7f1d1d; color: #ef9a9a; }"
)


class _ThumbLoader(QThread):
    """Loads thumbnails in the background."""

    loaded = pyqtSignal(int, QPixmap)  # row, pixmap

    def __init__(self, items: List[Dict], parent=None):
        super().__init__(parent)
        self._items = items

    def run(self) -> None:
        for idx, item in enumerate(self._items):
            path = item.get("thumbnail_path", "")
            if path and Path(path).is_file():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    pix = pix.scaled(
                        QSize(_THUMB_SIZE, _THUMB_SIZE),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.loaded.emit(idx, pix)


def _decision_for(photo: Dict, keep_t: float, trash_t: float) -> str:
    """Return KEEP / TRASH / REVIEW based on score and status.

    Explicit status (manual override) always takes priority over
    score-based thresholds.
    """
    status = photo.get("status", "")
    # Manual / decision-engine status takes priority
    if status == "keep":
        return "KEEP"
    if status == "trash":
        return "TRASH"
    # No explicit decision yet — use score thresholds
    score = photo.get("quality_score") or 0.0
    if score >= keep_t:
        return "KEEP"
    if score <= trash_t:
        return "TRASH"
    return "REVIEW"


def _format_reasons(raw: str) -> str:
    """Parse the cull_reasons JSON and return a compact summary."""
    if not raw:
        return "No analysis data"
    try:
        reasons = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return "No analysis data"

    parts = []
    for r in reasons:
        metric = r.get("metric", "")
        verdict = r.get("verdict", "")
        score = r.get("score")
        if metric == "Penalties":
            parts.append(f"⚠ {verdict}")
        elif score is not None:
            parts.append(f"{metric}: {verdict} ({score:.2f})")
        else:
            parts.append(f"{metric}: {verdict}")
    return "  |  ".join(parts) if parts else "No analysis data"


def _format_reasons_rich(raw: str) -> str:
    """Return an HTML-formatted multi-line breakdown of cull reasons."""
    if not raw:
        return "<i style='color:#888'>No analysis data</i>"
    try:
        reasons = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return "<i style='color:#888'>No analysis data</i>"

    lines = []
    for r in reasons:
        metric = r.get("metric", "")
        verdict = r.get("verdict", "")
        score = r.get("score")
        detail = r.get("detail", "")
        if metric == "Penalties":
            lines.append(f"<span style='color:#ff9800'>⚠ {verdict}</span>")
        elif score is not None:
            colour = "#4caf50" if score >= 0.6 else "#ff9800" if score >= 0.3 else "#ef5350"
            lines.append(
                f"<b>{metric}</b>: "
                f"<span style='color:{colour}'>{verdict} ({score:.2f})</span>"
            )
            if detail:
                lines.append(f"  <span style='color:#999; font-size:10px'>{detail}</span>")
        else:
            lines.append(f"<b>{metric}</b>: {verdict}")
    return "<br>".join(lines) if lines else "<i style='color:#888'>No analysis data</i>"


# ======================================================================
# Background RAW decoder for gallery
# ======================================================================


class _RawDecodeWorker(QThread):
    """Decode a RAW file to a full-resolution QPixmap in the background."""

    decoded = pyqtSignal(int, QPixmap)  # index, pixmap

    def __init__(self, index: int, file_path: str, parent=None):
        super().__init__(parent)
        self._index = index
        self._file_path = file_path

    def run(self) -> None:
        try:
            import rawpy
            import numpy as np

            with rawpy.imread(self._file_path) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                    half_size=True,  # half-size for speed, still much larger than thumb
                )
            h, w, _ = rgb.shape
            bytes_per_line = 3 * w
            qimg = QImage(rgb.data.tobytes(), w, h, bytes_per_line,
                          QImage.Format.Format_RGB888)
            pix = QPixmap.fromImage(qimg)
            if not pix.isNull():
                self.decoded.emit(self._index, pix)
        except Exception as exc:
            logger.debug("RAW decode failed for gallery (%s): %s", self._file_path, exc)


# ======================================================================
# Gallery Modal — large image with overlays
# ======================================================================

class _CullingGalleryDialog(QDialog):
    """Full-screen-ish modal showing one photo at a time with overlays.

    Bottom-right: metrics overlay.  Bottom-left: manual status selector.
    Arrow keys navigate.  Emits ``status_changed(file_name, new_status)``
    when the user manually overrides the cull decision.
    """

    status_changed = pyqtSignal(str, str)  # file_name, new_status ("keep"/"trash"/"culled")

    def __init__(
        self,
        photos: List[Dict],
        current_index: int,
        keep_threshold: float,
        trash_threshold: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Culling Gallery")
        self.setMinimumSize(900, 600)
        self.setModal(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._photos = photos
        self._index = current_index
        self._keep = keep_threshold
        self._trash = trash_threshold
        self._decode_worker: Optional[_RawDecodeWorker] = None
        self._full_pix_cache: Dict[int, QPixmap] = {}  # index → decoded pixmap
        self._CACHE_LIMIT = 5  # Max decoded RAW images to keep in memory

        # --- Main layout (stacked with overlays) ---
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Container for image + overlays
        self._container = QWidget()
        self._container.setStyleSheet("background: #111;")
        outer.addWidget(self._container, stretch=1)

        # Image label (fills container)
        self._image_label = QLabel(self._container)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background: transparent;")

        # --- Header overlay (top-center) ---
        self._header = QLabel(self._container)
        self._header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._header.setStyleSheet(
            "background: rgba(0,0,0,180); color: #eee; font-size: 13px; "
            "font-weight: bold; padding: 8px 18px; border-radius: 6px;"
        )

        # --- Metrics overlay (bottom-right) ---
        self._metrics_overlay = QLabel(self._container)
        self._metrics_overlay.setTextFormat(Qt.TextFormat.RichText)
        self._metrics_overlay.setWordWrap(True)
        self._metrics_overlay.setStyleSheet(
            "background: rgba(0,0,0,200); color: #ddd; font-size: 11px; "
            "padding: 10px 14px; border-radius: 8px;"
        )
        self._metrics_overlay.setMaximumWidth(320)

        # --- Status overlay (bottom-left) ---
        self._status_overlay = QFrame(self._container)
        self._status_overlay.setStyleSheet(
            "background: rgba(0,0,0,200); border-radius: 8px;"
        )
        status_layout = QVBoxLayout(self._status_overlay)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(6)

        self._decision_label = QLabel()
        self._decision_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._decision_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        status_layout.addWidget(self._decision_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._keep_btn = QPushButton("✓ KEEP")
        self._keep_btn.setStyleSheet(_BTN_STYLE_KEEP)
        self._keep_btn.setFixedHeight(28)
        self._keep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keep_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._keep_btn.clicked.connect(lambda: self._set_status("keep"))
        btn_row.addWidget(self._keep_btn)

        self._trash_btn = QPushButton("✕ TRASH")
        self._trash_btn.setStyleSheet(_BTN_STYLE_TRASH)
        self._trash_btn.setFixedHeight(28)
        self._trash_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._trash_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._trash_btn.clicked.connect(lambda: self._set_status("trash"))
        btn_row.addWidget(self._trash_btn)

        self._review_btn = QPushButton("? REVIEW")
        self._review_btn.setStyleSheet(
            "QPushButton { background: #444; color: white; border: none; "
            "border-radius: 4px; padding: 4px 10px; font-size: 10px; font-weight: bold; }"
            "QPushButton:hover { background: #555; }"
        )
        self._review_btn.setFixedHeight(28)
        self._review_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._review_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._review_btn.clicked.connect(lambda: self._set_status("culled"))
        btn_row.addWidget(self._review_btn)
        status_layout.addLayout(btn_row)

        nav_hint = QLabel(
            "<span style='color:#888'>K</span> Keep  · "
            "<span style='color:#888'>L</span> Trash  · "
            "<span style='color:#888'>H</span> Review  · "
            "<span style='color:#888'>← →</span> Navigate  · "
            "<span style='color:#888'>Esc</span> Close"
        )
        nav_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_hint.setStyleSheet("color: #666; font-size: 10px; letter-spacing: 0.3px;")
        status_layout.addWidget(nav_hint)

        # --- Loading overlay (centered) ---
        self._loading_overlay = QLabel(self._container)
        self._loading_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_overlay.setStyleSheet(
            "background: rgba(0,0,0,160); color: #ccc; font-size: 13px; "
            "border-radius: 8px; padding: 12px 20px;"
        )
        self._loading_overlay.setText("⏳ Decoding RAW…")
        self._loading_overlay.setFixedSize(180, 44)
        self._loading_overlay.hide()

        # Load first image
        self._load_current()

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.setFocus()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_overlays()
        # Re-scale cached full image to new container size
        cached = self._full_pix_cache.get(self._index)
        if cached and not cached.isNull():
            self._display_pixmap(cached)

    def _reposition_overlays(self) -> None:
        """Position all overlays relative to the container."""
        cw, ch = self._container.width(), self._container.height()

        # Image fills the whole area
        self._image_label.setGeometry(0, 0, cw, ch)

        # Header: top center
        hw = min(500, cw - 40)
        self._header.adjustSize()
        hh = self._header.sizeHint().height()
        self._header.setGeometry((cw - hw) // 2, 10, hw, hh)

        # Metrics: bottom-right
        self._metrics_overlay.adjustSize()
        mw = min(320, cw // 3)
        mh = self._metrics_overlay.sizeHint().height()
        self._metrics_overlay.setGeometry(cw - mw - 16, ch - mh - 16, mw, mh)

        # Status: bottom-left
        self._status_overlay.adjustSize()
        sw = self._status_overlay.sizeHint().width()
        sh = self._status_overlay.sizeHint().height()
        self._status_overlay.setGeometry(16, ch - sh - 16, max(sw, 200), sh)

        # Loading: centered
        lw, lh = 180, 44
        self._loading_overlay.setGeometry((cw - lw) // 2, (ch - lh) // 2, lw, lh)
        self._loading_overlay.raise_()

    def _display_pixmap(self, pix: QPixmap) -> None:
        """Scale and display a pixmap in the image label."""
        avail = self._container.size()
        scaled = pix.scaled(
            avail, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def _load_current(self) -> None:
        """Load the current photo into the viewer."""
        if not self._photos:
            return
        self._index = max(0, min(self._index, len(self._photos) - 1))
        photo = self._photos[self._index]

        # Header
        fname = photo.get("file_name", "?")
        score = photo.get("quality_score") or 0.0
        self._header.setText(
            f"{fname}  —  Score: {score:.3f}  "
            f"({self._index + 1} / {len(self._photos)})"
        )

        # Show cached full image or thumbnail as placeholder then decode RAW.
        cached = self._full_pix_cache.get(self._index)
        if cached and not cached.isNull():
            self._display_pixmap(cached)
            self._loading_overlay.hide()
        else:
            # Show thumbnail as quick placeholder.
            thumb = photo.get("thumbnail_path", "")
            if thumb and Path(thumb).is_file():
                pix = QPixmap(str(thumb))
                if not pix.isNull():
                    self._display_pixmap(pix)
                    self._image_label.setText("")
                else:
                    self._image_label.clear()
                    self._image_label.setText("Loading…")
            else:
                self._image_label.clear()
                self._image_label.setText("No image available")

            # Start background RAW decode.
            raw_path = photo.get("file_path", "")
            if raw_path and Path(raw_path).is_file():
                self._loading_overlay.show()
                self._start_decode(self._index, raw_path)
            else:
                self._loading_overlay.hide()

        # Metrics overlay
        self._metrics_overlay.setText(
            _format_reasons_rich(photo.get("cull_reasons", ""))
        )

        # Status overlay
        decision = _decision_for(photo, self._keep, self._trash)
        self._update_decision_display(decision)

        self._reposition_overlays()

    def _start_decode(self, index: int, file_path: str) -> None:
        """Kick off a background RAW decode for the given index."""
        # Disconnect previous worker so stale results go only to cache.
        if self._decode_worker is not None:
            try:
                self._decode_worker.decoded.disconnect(self._on_raw_decoded)
            except (TypeError, RuntimeError):
                pass
            # Let old thread finish on its own — no blocking wait.
        self._decode_worker = _RawDecodeWorker(index, file_path, self)
        self._decode_worker.decoded.connect(self._on_raw_decoded)
        self._decode_worker.start()

    def _on_raw_decoded(self, index: int, pix: QPixmap) -> None:
        """Slot called when background RAW decode finishes."""
        if index == self._index:
            self._loading_overlay.hide()
        self._full_pix_cache[index] = pix
        # Evict oldest entries to keep memory bounded
        if len(self._full_pix_cache) > self._CACHE_LIMIT:
            to_remove = sorted(self._full_pix_cache.keys())
            for k in to_remove[: len(self._full_pix_cache) - self._CACHE_LIMIT]:
                if k != self._index:
                    del self._full_pix_cache[k]
        if index == self._index:
            self._display_pixmap(pix)

    def _update_decision_display(self, decision: str) -> None:
        colours = {"KEEP": "#4caf50", "TRASH": "#ef5350", "REVIEW": "#999"}
        self._decision_label.setText(
            f"<span style='color:{colours.get(decision, '#999')}'>{decision}</span>"
        )
        self._keep_btn.setEnabled(decision != "KEEP")
        self._trash_btn.setEnabled(decision != "TRASH")

    def _set_status(self, new_status: str) -> None:
        """Manually override the cull status for the current photo."""
        photo = self._photos[self._index]
        photo["status"] = new_status
        decision = {"keep": "KEEP", "trash": "TRASH"}.get(new_status, "REVIEW")
        self._update_decision_display(decision)
        self.status_changed.emit(photo.get("file_name", ""), new_status)
        self.setFocus()  # reclaim focus after button click

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_A) and self._index > 0:
            self._index -= 1
            self._load_current()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_D) and self._index < len(self._photos) - 1:
            self._index += 1
            self._load_current()
        elif key == Qt.Key.Key_K:
            self._set_status("keep")
        elif key == Qt.Key.Key_L:
            self._set_status("trash")
        elif key == Qt.Key.Key_H:
            self._set_status("culled")
        elif key == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._decode_worker and self._decode_worker.isRunning():
            self._decode_worker.quit()
            if not self._decode_worker.wait(100):
                self._decode_worker.terminate()
        super().closeEvent(event)


# ======================================================================
# Main Culling Preview Dialog
# ======================================================================

class CullingPreviewDialog(QDialog):
    """Modal dialog listing all analysed photos with colour-coded verdicts.

    Emits ``status_changed(file_name, new_status)`` when the user manually
    overrides a photo's cull decision (from row buttons or gallery modal).
    """

    status_changed = pyqtSignal(str, str)  # file_name, new_status

    def __init__(
        self,
        photos: List[Dict],
        keep_threshold: float = 0.50,
        trash_threshold: float = 0.35,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Culling Preview")
        self.setMinimumSize(1100, 650)
        self.setModal(True)

        self._photos = photos
        self._keep = keep_threshold
        self._trash = trash_threshold
        self._filtered_indices: List[int] = list(range(len(photos)))
        self._thumb_cache: Dict[int, QPixmap] = {}  # src_idx → scaled pixmap

        layout = QVBoxLayout(self)

        # ---- Top bar: header + filter ----
        top_bar = QHBoxLayout()

        header = QLabel(
            f"Culling Preview — {len(photos)} photos   "
            f"(KEEP ≥ {keep_threshold:.2f}  |  TRASH ≤ {trash_threshold:.2f})"
        )
        header.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.setStyleSheet("color: #eee; padding: 8px;")
        top_bar.addWidget(header)

        top_bar.addStretch()

        filter_label = QLabel("Filter:")
        filter_label.setStyleSheet("color: #ccc; font-size: 12px; padding-right: 4px;")
        top_bar.addWidget(filter_label)

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "KEEP", "TRASH", "REVIEW"])
        self._filter_combo.setFixedWidth(110)
        self._filter_combo.setStyleSheet(
            "QComboBox { padding: 4px 8px; font-size: 12px; }"
        )
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        top_bar.addWidget(self._filter_combo)

        layout.addLayout(top_bar)

        # ---- Summary ----
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("padding: 0 8px 6px 8px; font-size: 12px;")
        layout.addWidget(self._summary_label)
        self._update_summary()

        # ---- Table ----
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "", "File", "Score", "Decision", "Reasons", "",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)

        hdr = self._table.horizontalHeader()
        self._table.setColumnWidth(0, _THUMB_SIZE + 12)
        self._table.setColumnWidth(1, 170)
        self._table.setColumnWidth(2, 60)
        self._table.setColumnWidth(3, 80)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(5, 160)

        self._table.setStyleSheet(
            "QTableWidget { background: #0a0a0a; color: #e0e0e0; font-size: 12px; border: none; }"
            "QTableWidget::item { border-bottom: 1px solid #1a1a1a; padding: 4px; }"
            "QHeaderView::section { background: #121212; color: #888888; "
            "font-weight: bold; font-size: 11px; padding: 6px 8px; border: none; "
            "border-bottom: 1px solid #2a2a2a; }"
        )

        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self._table, stretch=1)

        # ---- Bottom bar ----
        btn_layout = QHBoxLayout()
        hint = QLabel("Double-click a row to open gallery view")
        hint.setStyleSheet("color: #666; font-size: 11px; padding-left: 8px;")
        btn_layout.addWidget(hint)
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setMinimumWidth(120)
        close_btn.setStyleSheet(
            "QPushButton { padding: 8px 24px; border-radius: 6px; "
            "background: #2a2a2a; border: 1px solid #444; }"
            "QPushButton:hover { background: #333; border-color: #555; }"
        )
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # Populate
        self._populate_table()

        # Load thumbnails in background
        self._loader = _ThumbLoader(photos, self)
        self._loader.loaded.connect(self._on_thumb_loaded)
        self._loader.start()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def _update_summary(self) -> None:
        keep_count = sum(
            1 for p in self._photos
            if _decision_for(p, self._keep, self._trash) == "KEEP"
        )
        trash_count = sum(
            1 for p in self._photos
            if _decision_for(p, self._keep, self._trash) == "TRASH"
        )
        review_count = len(self._photos) - keep_count - trash_count
        self._summary_label.setText(
            f"<span style='color:#4caf50'>● {keep_count} KEEP</span>"
            f"  &nbsp;  "
            f"<span style='color:#ef5350'>● {trash_count} TRASH</span>"
            f"  &nbsp;  "
            f"<span style='color:#999'>● {review_count} REVIEW</span>"
        )

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------
    def _apply_filter(self, text: str) -> None:
        if text == "All":
            self._filtered_indices = list(range(len(self._photos)))
        else:
            self._filtered_indices = [
                i for i, p in enumerate(self._photos)
                if _decision_for(p, self._keep, self._trash) == text
            ]
        self._populate_table()

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------
    def _populate_table(self) -> None:
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(len(self._filtered_indices))
        for vis_row, src_idx in enumerate(self._filtered_indices):
            self._fill_row(vis_row, src_idx)
        self._table.setUpdatesEnabled(True)

    def _fill_row(self, vis_row: int, src_idx: int) -> None:
        photo = self._photos[src_idx]
        score = photo.get("quality_score") or 0.0
        decision = _decision_for(photo, self._keep, self._trash)

        bg, dec_colour = {
            "KEEP": (_KEEP_BG, "#4caf50"),
            "TRASH": (_TRASH_BG, "#ef5350"),
        }.get(decision, (_CULLED_BG, "#999"))

        self._table.setRowHeight(vis_row, _THUMB_SIZE + 8)

        # Col 0: Thumbnail (from cache — background loader populates it)
        thumb_item = QTableWidgetItem()
        thumb_item.setBackground(bg)
        thumb_item.setData(Qt.ItemDataRole.UserRole, src_idx)
        self._table.setItem(vis_row, 0, thumb_item)

        cached_pix = self._thumb_cache.get(src_idx)
        if cached_pix and not cached_pix.isNull():
            lbl = QLabel()
            lbl.setPixmap(cached_pix)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setCellWidget(vis_row, 0, lbl)

        # Col 1: Filename
        name_item = QTableWidgetItem(photo.get("file_name", "?"))
        name_item.setBackground(bg)
        self._table.setItem(vis_row, 1, name_item)

        # Col 2: Score
        score_item = QTableWidgetItem(f"{score:.3f}")
        score_item.setBackground(bg)
        score_item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._table.setItem(vis_row, 2, score_item)

        # Col 3: Decision
        dec_item = QTableWidgetItem(decision)
        dec_item.setBackground(bg)
        dec_item.setForeground(QColor(dec_colour))
        dec_item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        dec_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(vis_row, 3, dec_item)

        # Col 4: Reasons
        reasons_text = _format_reasons(photo.get("cull_reasons", ""))
        reason_item = QTableWidgetItem(reasons_text)
        reason_item.setBackground(bg)
        reason_item.setToolTip(reasons_text)
        self._table.setItem(vis_row, 4, reason_item)

        # Col 5: Action buttons
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(4, 4, 4, 4)
        actions_layout.setSpacing(4)

        keep_btn = QPushButton("✓")
        keep_btn.setToolTip("Mark as KEEP")
        keep_btn.setFixedSize(28, 24)
        keep_btn.setStyleSheet(_BTN_STYLE_KEEP)
        keep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        keep_btn.setEnabled(decision != "KEEP")
        keep_btn.clicked.connect(lambda _, idx=src_idx: self._set_row_status(idx, "keep"))

        trash_btn = QPushButton("✕")
        trash_btn.setToolTip("Mark as TRASH")
        trash_btn.setFixedSize(28, 24)
        trash_btn.setStyleSheet(_BTN_STYLE_TRASH)
        trash_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        trash_btn.setEnabled(decision != "TRASH")
        trash_btn.clicked.connect(lambda _, idx=src_idx: self._set_row_status(idx, "trash"))

        review_btn = QPushButton("?")
        review_btn.setToolTip("Mark as REVIEW")
        review_btn.setFixedSize(28, 24)
        review_btn.setStyleSheet(
            "QPushButton { background: #444; color: white; border: none; "
            "border-radius: 4px; font-size: 10px; font-weight: bold; }"
            "QPushButton:hover { background: #555; }"
            "QPushButton:pressed { background: #333; }"
        )
        review_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        review_btn.setEnabled(decision != "REVIEW")
        review_btn.clicked.connect(lambda _, idx=src_idx: self._set_row_status(idx, "culled"))

        actions_layout.addWidget(keep_btn)
        actions_layout.addWidget(trash_btn)
        actions_layout.addWidget(review_btn)
        self._table.setCellWidget(vis_row, 5, actions)

    # ------------------------------------------------------------------
    # Status change
    # ------------------------------------------------------------------
    def _set_row_status(self, src_idx: int, new_status: str) -> None:
        photo = self._photos[src_idx]
        photo["status"] = new_status
        self.status_changed.emit(photo.get("file_name", ""), new_status)

        # Refresh only the affected row
        self._set_row_status_ui_only(src_idx)

    def _set_row_status_ui_only(self, src_idx: int) -> None:
        """Update only the UI for a single row after a status change."""
        current_filter = self._filter_combo.currentText()
        if current_filter != "All":
            photo = self._photos[src_idx]
            decision = _decision_for(photo, self._keep, self._trash)
            if decision != current_filter:
                # Photo no longer matches filter — just remove its row
                try:
                    vis_row = self._filtered_indices.index(src_idx)
                    self._filtered_indices.remove(src_idx)
                    self._table.removeRow(vis_row)
                except ValueError:
                    pass
            else:
                # Still matches — update in place
                for vis_row, idx in enumerate(self._filtered_indices):
                    if idx == src_idx:
                        self._fill_row(vis_row, src_idx)
                        break
        else:
            for vis_row, idx in enumerate(self._filtered_indices):
                if idx == src_idx:
                    self._fill_row(vis_row, src_idx)
                    break

        self._update_summary()

    # ------------------------------------------------------------------
    # Gallery
    # ------------------------------------------------------------------
    def _on_row_double_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._filtered_indices):
            return
        # Build filtered photo list for the gallery
        filtered_photos = [self._photos[i] for i in self._filtered_indices]
        gallery = _CullingGalleryDialog(
            filtered_photos, row, self._keep, self._trash, self,
        )
        gallery.status_changed.connect(self._on_gallery_status_changed)
        gallery.exec()

    def _on_gallery_status_changed(self, file_name: str, new_status: str) -> None:
        # Find the source index for this file
        src_idx = None
        for i, p in enumerate(self._photos):
            if p.get("file_name", "") == file_name:
                p["status"] = new_status
                src_idx = i
                break

        self.status_changed.emit(file_name, new_status)

        if src_idx is not None:
            self._set_row_status_ui_only(src_idx)
        else:
            self._update_summary()

    # ------------------------------------------------------------------
    # Thumbnails
    # ------------------------------------------------------------------
    def _on_thumb_loaded(self, src_idx: int, pixmap: QPixmap) -> None:
        """Cache the thumbnail and set it for the matching visible row."""
        self._thumb_cache[src_idx] = pixmap
        for vis_row, idx in enumerate(self._filtered_indices):
            if idx == src_idx:
                label = QLabel()
                label.setPixmap(pixmap)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setCellWidget(vis_row, 0, label)
                break

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._loader.isRunning():
            self._loader.quit()
            if not self._loader.wait(200):
                self._loader.terminate()
        super().closeEvent(event)
