"""Duplicate cleaner dialog — visual tool for resolving duplicate photo groups.

Displays groups of visually similar photos side-by-side.  For each group
the system auto-suggests the "best" pick based on quality scores (and
learned preferences).  The user can accept the suggestion or manually
pick a different photo.  Rejected photos are marked as TRASH.

The user's manual choices feed the feedback learner so the auto-pick
improves over time.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_CARD_THUMB_SIZE = 220
_CARD_WIDTH = 250

# ======================================================================
# Styles
# ======================================================================

_DIALOG_STYLE = """
QDialog { background: #0d0d0d; }
QScrollArea { background: #0d0d0d; border: none; }
QWidget#scrollContent { background: #0d0d0d; }
"""

_GROUP_FRAME_STYLE = """
QFrame#groupFrame {
    background: #161616;
    border: 1px solid #232323;
    border-radius: 10px;
}
"""

_CARD_NORMAL = """
QFrame#photoCard {
    background: #1e1e1e;
    border: 2px solid #333333;
    border-radius: 8px;
}
QFrame#photoCard:hover {
    border-color: #4a4a4a;
}
"""

_CARD_SELECTED = """
QFrame#photoCard {
    background: #1a321a;
    border: 2px solid #3cb44e;
    border-radius: 8px;
}
"""

_CARD_REJECTED = """
QFrame#photoCard {
    background: #2a1a1a;
    border: 2px solid #c62828;
    border-radius: 8px;
    opacity: 0.7;
}
"""

_PICK_BTN = (
    "QPushButton { background: #3cb44e; color: white; border: none; "
    "border-radius: 5px; padding: 6px 14px; font-weight: 600; font-size: 10px; }"
    "QPushButton:hover { background: #4cc85e; }"
    "QPushButton:pressed { background: #2e9a3e; }"
    "QPushButton:disabled { background: #2e7d32; color: #81c784; }"
)

_AUTO_BTN = (
    "QPushButton { background: #1565c0; color: white; border: none; "
    "border-radius: 5px; padding: 6px 14px; font-weight: 600; font-size: 10px; }"
    "QPushButton:hover { background: #1976d2; }"
    "QPushButton:pressed { background: #0d47a1; }"
)

_APPLY_BTN = (
    "QPushButton { background: #e89530; color: #111; border: none; "
    "border-radius: 6px; padding: 9px 24px; font-weight: 600; font-size: 12px; }"
    "QPushButton:hover { background: #f5ad4a; }"
    "QPushButton:pressed { background: #d07a18; }"
)

_SKIP_BTN = (
    "QPushButton { background: #1e1e1e; color: #b0b0b0; border: 1px solid #333333; "
    "border-radius: 5px; padding: 6px 14px; font-size: 10px; }"
    "QPushButton:hover { background: #282828; border-color: #3e3e3e; }"
)

_SCORE_BEST = "color: #3cb44e; font-weight: 600; font-size: 11px;"
_SCORE_NORMAL = "color: #787878; font-size: 11px;"
_METRIC_GOOD = "#3cb44e"
_METRIC_MED = "#e89530"
_METRIC_BAD = "#ef5350"


def _metric_colour(val: float) -> str:
    if val >= 0.6:
        return _METRIC_GOOD
    if val >= 0.3:
        return _METRIC_MED
    return _METRIC_BAD


# ======================================================================
# Full-image preview dialog
# ======================================================================

class _RawDecodeWorker(QThread):
    """Decode a RAW file to a QPixmap in the background."""

    decoded = pyqtSignal(QPixmap)

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self._file_path = file_path

    def run(self) -> None:
        try:
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
                self.decoded.emit(pix)
        except Exception as exc:
            logger.debug("RAW decode failed (%s): %s", self._file_path, exc)


class _FullPreviewDialog(QDialog):
    """Full-size image preview — loads from thumbnail then upgrades via RAW decode."""

    def __init__(self, photo: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(photo.get("file_name", "Preview"))
        self.setMinimumSize(800, 600)
        self.setStyleSheet("QDialog { background: #111; }")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("color: #555; font-size: 14px;")
        self._image_label.setText("Loading full image…")
        layout.addWidget(self._image_label, stretch=1)

        # Info bar
        info = QLabel(
            f"{photo.get('file_name', '')}    "
            f"Score: {photo.get('quality_score', 0):.3f}    "
            f"(double-click card or press Escape to close)"
        )
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setFixedHeight(30)
        info.setStyleSheet("color: #888; font-size: 11px; background: #1a1a1a;")
        layout.addWidget(info)

        self._worker: Optional[_RawDecodeWorker] = None

        # Immediately show thumbnail if available
        thumb_path = photo.get("thumbnail_path", "")
        if thumb_path and Path(thumb_path).is_file():
            pix = QPixmap(str(thumb_path))
            if not pix.isNull():
                self._show_pixmap(pix)

        # Start RAW decode for full quality
        file_path = photo.get("file_path", "")
        if file_path and Path(file_path).is_file():
            self._worker = _RawDecodeWorker(file_path, self)
            self._worker.decoded.connect(self._on_decoded)
            self._worker.start()

    def _on_decoded(self, pix: QPixmap) -> None:
        self._show_pixmap(pix)

    def _show_pixmap(self, pix: QPixmap) -> None:
        scaled = pix.scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._image_label.setText("")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        pix = self._image_label.pixmap()
        if pix and not pix.isNull():
            self._show_pixmap(pix)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space):
            self.accept()
        else:
            super().keyPressEvent(event)


# ======================================================================
# Background thumbnail loader
# ======================================================================

class _ThumbLoader(QThread):
    """Load thumbnails for all photos in all groups."""

    loaded = pyqtSignal(str, QPixmap)  # file_name, pixmap

    def __init__(self, paths: Dict[str, str], parent=None):
        """paths: file_name → thumbnail_path"""
        super().__init__(parent)
        self._paths = dict(paths)

    def run(self) -> None:
        for fname, thumb_path in self._paths.items():
            if thumb_path and Path(thumb_path).is_file():
                pix = QPixmap(str(thumb_path))
                if not pix.isNull():
                    pix = pix.scaled(
                        QSize(_CARD_THUMB_SIZE, _CARD_THUMB_SIZE),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.loaded.emit(fname, pix)


# ======================================================================
# Photo card widget
# ======================================================================

class _PhotoCard(QFrame):
    """A single photo card within a duplicate group."""

    clicked = pyqtSignal(str)  # file_name
    double_clicked = pyqtSignal(str)  # file_name

    def __init__(self, photo: Dict, is_best: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("photoCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(_CARD_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        self._photo = photo
        self._fname = photo.get("file_name", "?")
        self._is_picked = is_best
        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Thumbnail
        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(_CARD_THUMB_SIZE, _CARD_THUMB_SIZE)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet(
            "background: #111; border-radius: 6px; color: #555; font-size: 11px;"
        )
        self._thumb_label.setText("Loading…")
        layout.addWidget(self._thumb_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Filename
        name_lbl = QLabel(self._fname)
        name_lbl.setStyleSheet("color: #ddd; font-size: 11px; font-weight: bold;")
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setWordWrap(True)
        layout.addWidget(name_lbl)

        # Score
        score = photo.get("quality_score") or 0.0
        score_style = _SCORE_BEST if is_best else _SCORE_NORMAL
        score_lbl = QLabel(f"Score: {score:.3f}")
        score_lbl.setStyleSheet(score_style)
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(score_lbl)

        # Metric mini-bars
        metrics = photo.get("metric_scores", {})
        if metrics:
            metrics_widget = QWidget()
            metrics_layout = QVBoxLayout(metrics_widget)
            metrics_layout.setContentsMargins(4, 2, 4, 2)
            metrics_layout.setSpacing(2)
            for m_name, m_val in metrics.items():
                if m_val is None:
                    continue
                row = QHBoxLayout()
                row.setSpacing(4)
                m_label = QLabel(m_name[:5].title())
                m_label.setFixedWidth(40)
                m_label.setStyleSheet("color: #888; font-size: 9px;")
                row.addWidget(m_label)

                # Mini bar
                bar = QFrame()
                bar.setFixedHeight(6)
                bar_width = max(4, int(m_val * 100))
                bar.setFixedWidth(bar_width)
                bar.setStyleSheet(
                    f"background: {_metric_colour(m_val)}; border-radius: 3px;"
                )
                row.addWidget(bar)

                val_lbl = QLabel(f"{m_val:.2f}")
                val_lbl.setStyleSheet(f"color: {_metric_colour(m_val)}; font-size: 9px;")
                val_lbl.setFixedWidth(30)
                row.addWidget(val_lbl)
                row.addStretch()
                metrics_layout.addLayout(row)
            layout.addWidget(metrics_widget)

        # Pick badge
        self._badge = QLabel()
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFixedHeight(22)
        layout.addWidget(self._badge)
        self._update_badge()

    def set_thumbnail(self, pix: QPixmap) -> None:
        self._thumb_label.setPixmap(pix)
        self._thumb_label.setText("")

    def set_picked(self, picked: bool) -> None:
        self._is_picked = picked
        self._update_style()
        self._update_badge()

    def _update_style(self) -> None:
        if self._is_picked:
            self.setStyleSheet(_CARD_SELECTED)
        else:
            self.setStyleSheet(_CARD_REJECTED)

    def _update_badge(self) -> None:
        if self._is_picked:
            self._badge.setText("✓ KEEP")
            self._badge.setStyleSheet(
                "color: #4caf50; font-weight: bold; font-size: 11px; "
                "background: rgba(76,175,80,0.15); border-radius: 4px;"
            )
        else:
            self._badge.setText("✕ TRASH")
            self._badge.setStyleSheet(
                "color: #ef5350; font-size: 10px; "
                "background: rgba(239,83,80,0.12); border-radius: 4px;"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._fname)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self._fname)
        super().mouseDoubleClickEvent(event)


# ======================================================================
# Group widget — one row of similar photos
# ======================================================================

class _GroupWidget(QFrame):
    """Displays a single group of duplicate/similar photos."""

    selection_changed = pyqtSignal(int)  # group_index
    preview_requested = pyqtSignal(dict)  # photo dict

    def __init__(self, group_index: int, photos: List[Dict],
                 best_fname: str, parent=None):
        super().__init__(parent)
        self.setObjectName("groupFrame")
        self.setStyleSheet(_GROUP_FRAME_STYLE)

        self._group_index = group_index
        self._photos = photos
        self._kept: set[str] = {best_fname}
        self._cards: Dict[str, _PhotoCard] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # Header
        header_row = QHBoxLayout()
        group_lbl = QLabel(f"Group {group_index + 1}  —  {len(photos)} similar photos")
        group_lbl.setStyleSheet(
            "color: #eee; font-size: 13px; font-weight: bold;"
        )
        header_row.addWidget(group_lbl)
        header_row.addStretch()

        auto_btn = QPushButton("⚡ Auto Pick Best")
        auto_btn.setStyleSheet(_AUTO_BTN)
        auto_btn.setFixedHeight(28)
        auto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        auto_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        auto_btn.clicked.connect(self._auto_pick)
        header_row.addWidget(auto_btn)

        layout.addLayout(header_row)

        # Photo cards in a horizontal scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(_CARD_THUMB_SIZE + 190)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:horizontal { height: 8px; background: #333; border-radius: 4px; }"
            "QScrollBar::handle:horizontal { background: #666; border-radius: 4px; min-width: 30px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { width: 0; }"
        )

        cards_widget = QWidget()
        cards_layout = QHBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(12)

        for photo in photos:
            fname = photo.get("file_name", "?")
            is_best = (fname == best_fname)
            card = _PhotoCard(photo, is_best, self)
            card.clicked.connect(self._on_card_clicked)
            card.double_clicked.connect(self._on_card_double_clicked)
            self._cards[fname] = card
            cards_layout.addWidget(card)

        cards_layout.addStretch()
        scroll.setWidget(cards_widget)
        layout.addWidget(scroll)

    def _on_card_clicked(self, fname: str) -> None:
        """Toggle keep/trash on the clicked card (multi-select)."""
        if fname in self._kept:
            self._kept.discard(fname)
        else:
            self._kept.add(fname)
        for fn, card in self._cards.items():
            card.set_picked(fn in self._kept)
        self.selection_changed.emit(self._group_index)

    def _on_card_double_clicked(self, fname: str) -> None:
        """User double-clicked — open full preview."""
        for photo in self._photos:
            if photo.get("file_name") == fname:
                self.preview_requested.emit(photo)
                return

    def _auto_pick(self) -> None:
        """Reset to auto-suggested best photo (single pick)."""
        best = max(self._photos, key=lambda p: p.get("quality_score") or 0)
        fname = best.get("file_name", "")
        self._kept = {fname}
        for fn, card in self._cards.items():
            card.set_picked(fn in self._kept)
        self.selection_changed.emit(self._group_index)

    def set_thumbnail(self, fname: str, pix: QPixmap) -> None:
        card = self._cards.get(fname)
        if card:
            card.set_thumbnail(pix)

    @property
    def kept_fnames(self) -> set[str]:
        return set(self._kept)

    @property
    def photos(self) -> List[Dict]:
        return self._photos


# ======================================================================
# Main dialog
# ======================================================================

class DuplicateCleanerDialog(QDialog):
    """Full-screen duplicate cleaning dialog.

    Emits ``decisions_made(list_of_trash_fnames)`` when the user clicks
    Apply — the caller should mark those photos as TRASH in the DB.
    """

    decisions_made = pyqtSignal(list)  # list of file_names to trash

    def __init__(self, groups: List[List[Dict]], parent=None):
        """
        Args:
            groups: List of groups.  Each group is a list of photo dicts
                with keys: file_name, file_path, thumbnail_path,
                quality_score, metric_scores, exif_iso, status.
        """
        super().__init__(parent)
        self.setWindowTitle("Duplicate Cleaner")
        self.setMinimumSize(1000, 700)
        self.setModal(True)
        self.setStyleSheet(_DIALOG_STYLE)

        self._groups = groups
        self._group_widgets: List[_GroupWidget] = []
        self._thumb_loader: Optional[_ThumbLoader] = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ─── Header bar ───
        header = QFrame()
        header.setFixedHeight(64)
        header.setStyleSheet(
            "QFrame { background: #141414; border-bottom: 1px solid #333; }"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("DUPLICATE CLEANER")
        title.setStyleSheet(
            "color: #ff9800; font-size: 16px; font-weight: bold; letter-spacing: 3px;"
        )
        header_layout.addWidget(title)

        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("color: #aaa; font-size: 12px;")
        header_layout.addWidget(self._summary_label)
        header_layout.addStretch()

        self._auto_all_btn = QPushButton("⚡ Auto Pick All")
        self._auto_all_btn.setStyleSheet(_AUTO_BTN)
        self._auto_all_btn.setFixedHeight(32)
        self._auto_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auto_all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._auto_all_btn.clicked.connect(self._auto_pick_all)
        header_layout.addWidget(self._auto_all_btn)

        main_layout.addWidget(header)

        # ─── Scrollable group list ───
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; min-height: 30px; }"
            "QScrollBar::handle:vertical:hover { background: #555; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
        )

        content = QWidget()
        content.setObjectName("scrollContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(20, 16, 20, 16)
        self._content_layout.setSpacing(16)

        # Build group widgets
        for i, group in enumerate(groups):
            best = self._auto_best(group)
            gw = _GroupWidget(i, group, best, self)
            gw.selection_changed.connect(self._on_selection_changed)
            gw.preview_requested.connect(self._on_preview_requested)
            self._group_widgets.append(gw)
            self._content_layout.addWidget(gw)

        self._content_layout.addStretch()
        scroll.setWidget(content)
        main_layout.addWidget(scroll, stretch=1)

        # ─── Footer bar ───
        footer = QFrame()
        footer.setFixedHeight(64)
        footer.setStyleSheet(
            "QFrame { background: #141414; border-top: 1px solid #333; }"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 0, 24, 0)

        self._trash_count_label = QLabel()
        self._trash_count_label.setStyleSheet("color: #ef5350; font-size: 12px;")
        footer_layout.addWidget(self._trash_count_label)

        footer_layout.addStretch()

        skip_btn = QPushButton("Cancel")
        skip_btn.setStyleSheet(_SKIP_BTN)
        skip_btn.setFixedHeight(36)
        skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        skip_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        skip_btn.clicked.connect(self.reject)
        footer_layout.addWidget(skip_btn)

        apply_btn = QPushButton("Apply && Trash Duplicates")
        apply_btn.setStyleSheet(_APPLY_BTN)
        apply_btn.setFixedHeight(36)
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        apply_btn.clicked.connect(self._apply)
        footer_layout.addWidget(apply_btn)

        main_layout.addWidget(footer)

        # Start loading thumbnails
        self._update_counts()
        self._start_thumb_loading()

    # ------------------------------------------------------------------
    # Auto-pick logic
    # ------------------------------------------------------------------

    def _auto_best(self, group: List[Dict]) -> str:
        """Choose the best photo in a group using score + learned weights."""
        from imagic.ai.feedback_learner import get_learner
        learned = get_learner().get_duplicate_ranking_weights()

        def rank(photo: Dict) -> float:
            score = photo.get("quality_score") or 0.0
            if not learned:
                return score
            # Blend in learned importance for specific metrics
            metrics = photo.get("metric_scores", {})
            bonus = 0.0
            for m, weight in learned.items():
                val = metrics.get(m, 0.5)
                bonus += weight * val * 0.3  # scale factor
            return score + bonus

        best = max(group, key=rank)
        return best.get("file_name", "")

    def _auto_pick_all(self) -> None:
        """Auto-pick the best in every group."""
        for gw in self._group_widgets:
            gw._auto_pick()
        self._update_counts()

    # ------------------------------------------------------------------
    # Thumbnails
    # ------------------------------------------------------------------

    def _start_thumb_loading(self) -> None:
        paths: Dict[str, str] = {}
        for group in self._groups:
            for photo in group:
                fn = photo.get("file_name", "")
                tp = photo.get("thumbnail_path", "")
                if fn and tp:
                    paths[fn] = tp

        self._thumb_loader = _ThumbLoader(paths, self)
        self._thumb_loader.loaded.connect(self._on_thumb_loaded)
        self._thumb_loader.start()

    def _on_thumb_loaded(self, fname: str, pix: QPixmap) -> None:
        for gw in self._group_widgets:
            gw.set_thumbnail(fname, pix)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _on_selection_changed(self, group_index: int) -> None:
        self._update_counts()

    def _on_preview_requested(self, photo: dict) -> None:
        """Open the full-size image preview."""
        preview = _FullPreviewDialog(photo, self)
        preview.exec()

    def _get_trash_list(self) -> List[str]:
        """Get all file_names that will be trashed (not kept)."""
        trash = []
        for gw in self._group_widgets:
            kept = gw.kept_fnames
            for photo in gw.photos:
                fn = photo.get("file_name", "")
                if fn and fn not in kept:
                    trash.append(fn)
        return trash

    def _update_counts(self) -> None:
        trash = self._get_trash_list()
        total = sum(len(gw.photos) for gw in self._group_widgets)
        keep = total - len(trash)
        self._summary_label.setText(
            f"{len(self._group_widgets)} groups  ·  {total} photos  ·  "
            f"keeping {keep}, trashing {len(trash)}"
        )
        self._trash_count_label.setText(
            f"{len(trash)} photo{'s' if len(trash) != 1 else ''} will be marked as TRASH"
        )

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _apply(self) -> None:
        """Record feedback and emit the trash list."""
        from imagic.ai.feedback_learner import get_learner
        learner = get_learner()

        # Record choices for learning
        for gw in self._group_widgets:
            kept = gw.kept_fnames
            kept_photos = []
            rejected_photos = []
            for photo in gw.photos:
                if photo.get("file_name") in kept:
                    kept_photos.append(photo)
                else:
                    rejected_photos.append(photo)

            if kept_photos and rejected_photos:
                # Record each kept photo vs rejected set
                for kp in kept_photos:
                    learner.record_duplicate_choice(
                        kept_metrics=kp.get("metric_scores", {}),
                        rejected_metrics=[
                            r.get("metric_scores", {}) for r in rejected_photos
                        ],
                    )

        trash = self._get_trash_list()
        self.decisions_made.emit(trash)
        self.accept()

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
