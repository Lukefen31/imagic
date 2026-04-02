"""Main application window — single-window step-by-step workflow.

The UI presents a left sidebar with 5 workflow steps:
  1. Import  →  2. Analyse  →  3. Review & Cull  →  4. Edit  →  5. Export

Each step has auto/manual mode and auto-advances to the next step when
complete. No modal dialogs — everything lives in one window.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from imagic.views.export_gallery import ExportGalleryView
from imagic.views.library_view import LibraryView
from imagic.views.photo_editor import PhotoEditorWidget
from imagic.views.processing_view import ProcessingView
from imagic.utils.runtime_paths import resolve_resource
from imagic.views.review_grid import ReviewGridView
from imagic.views.settings_view import SettingsView
from imagic.views.widgets.status_bar import StatusBarWidget
from imagic.views.widgets.ai_loading_modal import AILoadingModal

logger = logging.getLogger(__name__)

# ── Style constants ─────────────────────────────────────────────────
_BG = "#0d0d0d"
_PANEL = "#1a1a1a"
_BORDER = "#333"
_TEXT = "#ddd"
_DIM = "#888"
_ACCENT = "#ff9800"
_GREEN = "#4caf50"

_STEP_BTN = (
    "QPushButton {{ text-align: left; padding: 14px 18px; font-size: 13px; "
    "border: none; border-left: 3px solid transparent; border-radius: 0; "
    "color: {dim}; background: {bg}; }}"
    "QPushButton:hover {{ background: #222; color: {text}; }}"
)
_STEP_BTN_ACTIVE = (
    "QPushButton {{ text-align: left; padding: 14px 18px; font-size: 13px; font-weight: bold; "
    "border: none; border-left: 3px solid {accent}; border-radius: 0; "
    "color: {accent}; background: #1c1c1c; }}"
)
_STEP_BTN_DONE = (
    "QPushButton {{ text-align: left; padding: 14px 18px; font-size: 13px; "
    "border: none; border-left: 3px solid {green}; border-radius: 0; "
    "color: {green}; background: {bg}; }}"
    "QPushButton:hover {{ background: #222; }}"
)

_ACTION_BTN = (
    f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    f"stop:0 #ffb74d, stop:1 #f57c00); color: #111; font-weight: bold; "
    f"border: none; border-radius: 6px; padding: 10px 24px; font-size: 13px; }}"
    f"QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    f"stop:0 #ffc570, stop:1 #ff9800); }}"
    f"QPushButton:pressed {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    f"stop:0 #f57c00, stop:1 #e65100); }}"
    f"QPushButton:disabled {{ background: #333; color: #666; }}"
)
_SECONDARY_BTN = (
    f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    f"stop:0 #3a3a3a, stop:1 #222); color: {_TEXT}; font-weight: bold; "
    f"border: 1px solid #444; border-radius: 6px; padding: 10px 24px; font-size: 13px; }}"
    f"QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    f"stop:0 #444, stop:1 #2a2a2a); border-color: #555; }}"
    f"QPushButton:pressed {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    f"stop:0 #222, stop:1 #1a1a1a); }}"
)


class MainWindow(QMainWindow):
    """Single-window workflow with step-by-step pipeline.

    All signals remain the same for backward compatibility with main.py.
    """

    import_requested = pyqtSignal(str, bool)
    analyse_requested = pyqtSignal()
    export_requested = pyqtSignal()
    duplicate_scan_requested = pyqtSignal()
    generate_thumbnails_requested = pyqtSignal()
    choose_style_requested = pyqtSignal()
    culling_preview_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    settings_changed = pyqtSignal(dict)
    refresh_requested = pyqtSignal()
    clear_library_requested = pyqtSignal()
    open_export_folder_requested = pyqtSignal()
    reexport_broken_requested = pyqtSignal()
    reanalyse_all_requested = pyqtSignal()
    edit_step_entered = pyqtSignal()
    edit_photo_requested = pyqtSignal(int)
    review_status_changed = pyqtSignal(int, str)  # photo_id, new_status
    tutorial_requested = pyqtSignal()

    STEPS = ["1. Import", "2. Analyse", "3. Review", "4. Edit", "5. Export"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Imagic — Photo Orchestrator")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(
            f"QMainWindow {{ background: {_BG}; }}"
            f"QMenuBar {{ background: #141414; color: {_TEXT}; border-bottom: 1px solid {_BORDER}; padding: 2px 0; }}"
            f"QMenuBar::item {{ padding: 6px 12px; border-radius: 4px; }}"
            f"QMenuBar::item:selected {{ background: #2a2a2a; }}"
            f"QMenu {{ background: #1a1a1a; color: {_TEXT}; border: 1px solid {_BORDER}; border-radius: 8px; padding: 4px 0; }}"
            f"QMenu::item {{ padding: 8px 24px 8px 16px; border-radius: 4px; margin: 2px 4px; }}"
            f"QMenu::item:selected {{ background: {_ACCENT}; color: #111; }}"
            f"QMenu::separator {{ height: 1px; background: {_BORDER}; margin: 4px 12px; }}"
        )

        self._current_step = 0
        self._step_done = [False] * 5

        central = QWidget()
        central.setStyleSheet(f"background: {_BG};")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left sidebar ─────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet(f"background: {_PANEL}; border-right: 1px solid {_BORDER};")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 12, 0, 12)
        sb_layout.setSpacing(0)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet("padding: 16px 12px;")
        _wide_svg = resolve_resource("assets", "imagic-wide", "imagic-wide-lightgrey.svg")
        if _wide_svg.is_file():
            _pix = QPixmap(str(_wide_svg))
            if not _pix.isNull():
                _pix = _pix.scaledToWidth(150, Qt.TransformationMode.SmoothTransformation)
                logo.setPixmap(_pix)
        else:
            logo.setText("IMAGIC")
            logo.setStyleSheet(
                f"color: {_ACCENT}; font-size: 16px; font-weight: bold; "
                f"padding: 16px 0; letter-spacing: 4px;"
            )
        sb_layout.addWidget(logo)

        self._step_buttons: list[QPushButton] = []
        for i, label in enumerate(self.STEPS):
            btn = QPushButton(f"  ○  {label}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self.go_to_step(idx))
            sb_layout.addWidget(btn)
            self._step_buttons.append(btn)

        sb_layout.addStretch()

        self._sidebar_progress = QProgressBar()
        self._sidebar_progress.setFixedHeight(4)
        self._sidebar_progress.setTextVisible(False)
        self._sidebar_progress.setStyleSheet(
            f"QProgressBar {{ background: #222; border: none; border-radius: 2px; margin: 0 12px; }}"
            f"QProgressBar::chunk {{ background: {_ACCENT}; border-radius: 2px; }}"
        )
        sb_layout.addWidget(self._sidebar_progress)

        self._sidebar_status = QLabel("Ready")
        self._sidebar_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sidebar_status.setWordWrap(True)
        self._sidebar_status.setStyleSheet(f"color: {_DIM}; font-size: 11px; padding: 8px 12px;")
        sb_layout.addWidget(self._sidebar_status)

        root.addWidget(sidebar)

        # ── Right column: stacked pages + embedded status bar ────
        right_col = QWidget()
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # ── Update banner (hidden until an update is detected) ──────
        self._update_banner = QWidget()
        self._update_banner.setStyleSheet(
            "background: #b45309; border-bottom: 1px solid #92400e;"
        )
        _banner_layout = QHBoxLayout(self._update_banner)
        _banner_layout.setContentsMargins(16, 6, 16, 6)
        self._update_banner_label = QLabel()
        self._update_banner_label.setStyleSheet(
            "color: #fff; font-size: 12px; font-weight: bold; background: transparent;"
        )
        _banner_layout.addWidget(self._update_banner_label, stretch=1)
        self._update_banner_btn = QPushButton("Download Update")
        self._update_banner_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_banner_btn.setStyleSheet(
            "QPushButton { background: #fff; color: #92400e; font-weight: bold; "
            "border: none; border-radius: 4px; padding: 4px 12px; font-size: 12px; } "
            "QPushButton:hover { background: #fef3c7; }"
        )
        _banner_layout.addWidget(self._update_banner_btn)
        self._update_banner.hide()
        right_layout.addWidget(self._update_banner)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {_BG};")

        self._build_import_page()
        self._build_analyse_page()
        self._build_review_page()
        self._build_edit_page()
        self._build_export_page()

        right_layout.addWidget(self._stack, stretch=1)

        self.status_bar = StatusBarWidget()
        self.status_bar._message.hide()  # sidebar already shows status text
        self.status_bar.setStyleSheet(
            f"background: #141414; border-top: 1px solid {_BORDER}; "
            f"color: {_DIM}; font-size: 11px; padding: 2px 8px;"
        )
        right_layout.addWidget(self.status_bar)

        root.addWidget(right_col, stretch=1)
        self.setCentralWidget(central)

        # ── Full-window loading overlay (hidden by default) ──
        self._loading_overlay = AILoadingModal(parent=central)
        self._loading_overlay.hide()

        self.processing_view = ProcessingView()

        self._build_menus()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_requested.emit)
        self._refresh_timer.start(5000)

        self._update_step_buttons()
        logger.info("MainWindow created (single-window workflow).")

    # ==================================================================
    # Step pages
    # ==================================================================

    def _build_import_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        title = QLabel("Import Photos")
        title.setStyleSheet(f"color: {_TEXT}; font-size: 24px; font-weight: bold; letter-spacing: -0.5px;")
        layout.addWidget(title)

        desc = QLabel("Select a folder containing your RAW photos to begin.")
        desc.setStyleSheet(f"color: {_DIM}; font-size: 13px; line-height: 1.5;")
        layout.addWidget(desc)
        layout.addSpacing(12)

        row = QHBoxLayout()
        self._import_dir_edit = QLineEdit()
        self._import_dir_edit.setPlaceholderText("Select a directory…")
        self._import_dir_edit.setStyleSheet(
            f"QLineEdit {{ background: #1a1a1a; color: {_TEXT}; border: 1px solid #444; "
            f"border-radius: 6px; padding: 10px 12px; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {_ACCENT}; }}"
        )
        row.addWidget(self._import_dir_edit, stretch=1)

        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setStyleSheet(_SECONDARY_BTN)
        self._browse_btn.clicked.connect(self._browse_import)
        row.addWidget(self._browse_btn)
        layout.addLayout(row)

        self._import_recursive_cb = QCheckBox("Scan subdirectories")
        self._import_recursive_cb.setChecked(True)
        self._import_recursive_cb.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
        layout.addWidget(self._import_recursive_cb)

        self._import_auto_cb = QCheckBox("Auto: scan and advance to Analyse when done")
        self._import_auto_cb.setChecked(True)
        self._import_auto_cb.setStyleSheet(f"color: {_ACCENT}; font-size: 12px;")
        layout.addWidget(self._import_auto_cb)

        layout.addSpacing(8)

        self._import_btn = QPushButton("  Import Photos  ")
        self._import_btn.setStyleSheet(_ACTION_BTN)
        self._import_btn.clicked.connect(self._do_import)
        layout.addWidget(self._import_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self._import_status = QLabel("")
        self._import_status.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        layout.addWidget(self._import_status)

        # ── Visual how-to guide (shown when no photos imported) ──
        self._import_guide = QWidget()
        guide_layout = QVBoxLayout(self._import_guide)
        guide_layout.setContentsMargins(0, 20, 0, 0)
        guide_layout.setSpacing(0)

        guide_header = QLabel("How it works")
        guide_header.setStyleSheet(
            f"color: {_DIM}; font-size: 11px; font-weight: bold; "
            f"letter-spacing: 2px; text-transform: uppercase; padding-bottom: 16px;"
        )
        guide_layout.addWidget(guide_header)

        steps_row = QHBoxLayout()
        steps_row.setSpacing(20)

        icons_dir = resolve_resource("assets", "icons")
        step_data = [
            (str(icons_dir / "camera.svg"), "1. Organise your photos",
             "Put your images into a folder.\n"
             "Subfolders work too!"),
            (str(icons_dir / "folder-open.svg"), "2. Select the folder",
             "Use Browse above to pick\n"
             "the folder you created."),
            (str(icons_dir / "import-arrow.svg"), "3. Click Import",
             "We'll scan and catalogue\n"
             "every photo automatically."),
        ]

        for icon_path, heading, body in step_data:
            card = QFrame()
            card.setStyleSheet(
                "QFrame { background: #141414; border: 1px solid #2a2a2a; "
                "border-radius: 10px; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(24, 24, 24, 24)
            card_layout.setSpacing(8)

            icon_lbl = QLabel()
            icon_lbl.setStyleSheet("border: none;")
            icon_lbl.setFixedSize(36, 36)
            pix = QPixmap(icon_path)
            if not pix.isNull():
                icon_lbl.setPixmap(pix.scaled(
                    36, 36,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            card_layout.addWidget(icon_lbl)

            head_lbl = QLabel(heading)
            head_lbl.setStyleSheet(
                f"color: {_TEXT}; font-size: 13px; font-weight: bold; border: none;"
            )
            card_layout.addWidget(head_lbl)

            body_lbl = QLabel(body)
            body_lbl.setStyleSheet(
                f"color: {_DIM}; font-size: 12px; line-height: 1.4; border: none;"
            )
            body_lbl.setWordWrap(True)
            card_layout.addWidget(body_lbl)
            card_layout.addStretch()

            steps_row.addWidget(card)

        guide_layout.addLayout(steps_row)

        # Supported formats note
        formats_lbl = QLabel(
            "Supports JPEG, PNG, TIFF — and yes, we support RAW! "
            "(CR2, CR3, NEF, ARW, DNG, ORF, RAF, RW2 and more)"
        )
        formats_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 11px; padding-top: 16px;"
        )
        formats_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        guide_layout.addWidget(formats_lbl)

        layout.addWidget(self._import_guide)

        layout.addStretch()
        self._stack.addWidget(page)

    def _build_analyse_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Analyse Photos")
        title.setStyleSheet(f"color: {_TEXT}; font-size: 24px; font-weight: bold; letter-spacing: -0.5px;")
        header.addWidget(title)
        header.addStretch()

        self._analyse_auto_cb = QCheckBox("Auto")
        self._analyse_auto_cb.setChecked(True)
        self._analyse_auto_cb.setStyleSheet(f"color: {_ACCENT}; font-size: 12px;")
        header.addWidget(self._analyse_auto_cb)

        self._analyse_btn = QPushButton("Run AI Analysis")
        self._analyse_btn.setStyleSheet(_SECONDARY_BTN)
        self._analyse_btn.clicked.connect(self.analyse_requested.emit)
        header.addWidget(self._analyse_btn)

        self._reanalyse_btn = QPushButton("Re-Analyse All")
        self._reanalyse_btn.setStyleSheet(_SECONDARY_BTN)
        self._reanalyse_btn.clicked.connect(self.reanalyse_all_requested.emit)
        header.addWidget(self._reanalyse_btn)

        next_btn = QPushButton("Next →")
        next_btn.setStyleSheet(_ACTION_BTN)
        next_btn.clicked.connect(lambda: self._complete_step(1))
        header.addWidget(next_btn)

        layout.addLayout(header)

        self._analyse_status = QLabel("Import photos first, then run analysis.")
        self._analyse_status.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        layout.addWidget(self._analyse_status)

        self.library_view = LibraryView()
        self.library_view.photo_double_clicked.connect(self.edit_photo_requested.emit)
        layout.addWidget(self.library_view, stretch=1)

        self._stack.addWidget(page)

    def _build_review_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Review & Cull")
        title.setStyleSheet(f"color: {_TEXT}; font-size: 24px; font-weight: bold; letter-spacing: -0.5px;")
        header.addWidget(title)
        header.addStretch()

        self._review_auto_cb = QCheckBox("Auto: accept AI decisions")
        self._review_auto_cb.setChecked(False)
        self._review_auto_cb.setStyleSheet(f"color: {_ACCENT}; font-size: 12px;")
        header.addWidget(self._review_auto_cb)

        cull_btn = QPushButton("Detailed Culling View")
        cull_btn.setStyleSheet(_SECONDARY_BTN)
        cull_btn.clicked.connect(self.culling_preview_requested.emit)
        header.addWidget(cull_btn)

        dup_btn = QPushButton("Find Duplicates")
        dup_btn.setStyleSheet(_SECONDARY_BTN)
        dup_btn.clicked.connect(self.duplicate_scan_requested.emit)
        header.addWidget(dup_btn)

        style_btn = QPushButton("Choose Style")
        style_btn.setStyleSheet(_SECONDARY_BTN)
        style_btn.clicked.connect(self.choose_style_requested.emit)
        header.addWidget(style_btn)

        next_btn = QPushButton("Next →")
        next_btn.setStyleSheet(_ACTION_BTN)
        next_btn.clicked.connect(lambda: self._complete_step(2))
        header.addWidget(next_btn)

        layout.addLayout(header)

        self._review_status = QLabel("Review AI decisions. Double-click any photo to edit.")
        self._review_status.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        layout.addWidget(self._review_status)

        self._review_grid = ReviewGridView()
        self._review_grid.photo_double_clicked.connect(self._on_review_double_click)
        self._review_grid.status_changed.connect(self.review_status_changed.emit)
        layout.addWidget(self._review_grid, stretch=1)

        self._stack.addWidget(page)

    def _build_edit_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(44)
        header.setStyleSheet(f"background: #141414; border-bottom: 1px solid {_BORDER};")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 6, 16, 6)

        lbl = QLabel("Edit Photos")
        lbl.setStyleSheet(f"color: {_TEXT}; font-size: 14px; font-weight: bold;")
        h.addWidget(lbl)
        h.addStretch()

        self._edit_auto_cb = QCheckBox("Auto AI Optimize all")
        self._edit_auto_cb.setChecked(False)
        self._edit_auto_cb.setStyleSheet(f"color: {_ACCENT}; font-size: 11px;")
        h.addWidget(self._edit_auto_cb)

        done_btn = QPushButton("Done Editing →")
        done_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #111; font-weight: bold; "
            f"border: none; border-radius: 6px; padding: 6px 16px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #ffa726; }}"
        )
        done_btn.clicked.connect(lambda: self._complete_step(3))
        h.addWidget(done_btn)
        layout.addWidget(header)

        self.photo_editor = PhotoEditorWidget()
        self.photo_editor.closed.connect(lambda: self._complete_step(3))
        layout.addWidget(self.photo_editor, stretch=1)

        self._stack.addWidget(page)

    def _build_export_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Export")
        title.setStyleSheet(f"color: {_TEXT}; font-size: 24px; font-weight: bold; letter-spacing: -0.5px;")
        header.addWidget(title)
        header.addStretch()

        self._export_auto_cb = QCheckBox("Auto: export immediately")
        self._export_auto_cb.setChecked(True)
        self._export_auto_cb.setStyleSheet(f"color: {_ACCENT}; font-size: 12px;")
        header.addWidget(self._export_auto_cb)

        export_btn = QPushButton("  Export All Kept  ")
        export_btn.setStyleSheet(_ACTION_BTN)
        export_btn.clicked.connect(self.export_requested.emit)
        header.addWidget(export_btn)

        reexport_btn = QPushButton("Re-export Broken")
        reexport_btn.setStyleSheet(_SECONDARY_BTN)
        reexport_btn.setToolTip("Re-export photos that had bad auto-crop or errors")
        reexport_btn.clicked.connect(self.reexport_broken_requested.emit)
        header.addWidget(reexport_btn)

        open_btn = QPushButton("Open Folder")
        open_btn.setStyleSheet(_SECONDARY_BTN)
        open_btn.clicked.connect(self.open_export_folder_requested.emit)
        header.addWidget(open_btn)

        layout.addLayout(header)

        self._export_status = QLabel("Export your finished photos.")
        self._export_status.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        layout.addWidget(self._export_status)

        self.export_gallery = ExportGalleryView()
        layout.addWidget(self.export_gallery, stretch=1)

        self._stack.addWidget(page)

    # ==================================================================
    # Step navigation
    # ==================================================================

    def go_to_step(self, index: int) -> None:
        if 0 <= index < 5:
            self._current_step = index
            self._stack.setCurrentIndex(index)
            self._update_step_buttons()
            if index == 3:
                self.edit_step_entered.emit()

    def _complete_step(self, step_index: int) -> None:
        if 0 <= step_index < 5:
            self._step_done[step_index] = True
        self._update_step_buttons()
        nxt = step_index + 1
        if nxt < 5:
            self.go_to_step(nxt)
            self._auto_run_step(nxt)

    def _auto_run_step(self, step: int) -> None:
        if step == 1 and self._analyse_auto_cb.isChecked():
            QTimer.singleShot(300, self.analyse_requested.emit)
        elif step == 2 and self._review_auto_cb.isChecked():
            QTimer.singleShot(300, lambda: self._complete_step(2))
        elif step == 3 and self._edit_auto_cb.isChecked():
            QTimer.singleShot(300, self._auto_optimize_all)
        elif step == 4 and self._export_auto_cb.isChecked():
            QTimer.singleShot(300, self.export_requested.emit)

    def _auto_optimize_all(self) -> None:
        if hasattr(self.photo_editor, '_ai_optimize_all'):
            self.photo_editor._ai_optimize_all()

    def _update_step_buttons(self) -> None:
        for i, btn in enumerate(self._step_buttons):
            label = self.STEPS[i]
            if i == self._current_step:
                btn.setText(f"  ◉  {label}")
                btn.setStyleSheet(_STEP_BTN_ACTIVE.format(accent=_ACCENT))
            elif self._step_done[i]:
                btn.setText(f"  ✓  {label}")
                btn.setStyleSheet(_STEP_BTN_DONE.format(green=_GREEN, bg=_PANEL))
            else:
                btn.setText(f"  ○  {label}")
                btn.setStyleSheet(_STEP_BTN.format(dim=_DIM, bg=_PANEL, text=_TEXT))

    # ==================================================================
    # Import helpers
    # ==================================================================

    def _browse_import(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Photo Directory")
        if path:
            self._import_dir_edit.setText(path)
            if self._import_auto_cb.isChecked():
                self._do_import()

    def _do_import(self) -> None:
        directory = self._import_dir_edit.text().strip()
        if directory and Path(directory).is_dir():
            self._import_guide.hide()
            self._import_status.setText(f"Scanning {directory}…")
            self._import_btn.setEnabled(False)
            self.import_requested.emit(directory, self._import_recursive_cb.isChecked())
            if self._import_auto_cb.isChecked():
                QTimer.singleShot(2000, lambda: self._complete_step(0))
            else:
                self._step_done[0] = True
                self._update_step_buttons()

    def on_import_complete(self, count: int) -> None:
        self._import_status.setText(f"Imported {count} photos.")
        self._import_btn.setEnabled(True)
        self._step_done[0] = True
        self._update_step_buttons()

    # ==================================================================
    # Review helpers
    # ==================================================================

    def _on_review_double_click(self, photo_id: int) -> None:
        self.edit_photo_requested.emit(photo_id)
        self.go_to_step(3)

    def set_review_photos(self, photos: list) -> None:
        self._review_grid.set_photos(photos)
        kept = sum(1 for p in photos if p.get("status") == "keep")
        trashed = sum(1 for p in photos if p.get("status") == "trash")
        self._review_status.setText(
            f"{len(photos)} photos — {kept} kept, {trashed} trashed. "
            f"Double-click to edit."
        )

    # ==================================================================
    # Edit helpers
    # ==================================================================

    def set_editor_photos(self, photo_list: list, current_index: int = 0) -> None:
        self.photo_editor.set_photos(photo_list, current_index)

    # ==================================================================
    # Status helpers
    # ==================================================================

    def set_step_status(self, step: int, text: str) -> None:
        labels = {
            0: self._import_status,
            1: self._analyse_status,
            2: self._review_status,
            4: self._export_status,
        }
        lbl = labels.get(step)
        if lbl:
            lbl.setText(text)

    def set_progress(self, current: int, total: int) -> None:
        self._sidebar_progress.setRange(0, max(total, 1))
        self._sidebar_progress.setValue(current)

    def set_sidebar_status(self, text: str) -> None:
        self._sidebar_status.setText(text or "Ready")

    # ==================================================================
    # Menus
    # ==================================================================

    def _build_menus(self) -> None:
        menu_bar: QMenuBar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        import_action = QAction("&Import Folder…", self)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.triggered.connect(lambda: self.go_to_step(0))
        file_menu.addAction(import_action)
        file_menu.addSeparator()

        settings_action = QAction("&Settings…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.settings_requested.emit)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()

        clear_action = QAction("&Clear Library", self)
        clear_action.triggered.connect(self.clear_library_requested.emit)
        file_menu.addAction(clear_action)

        open_action = QAction("Open Export &Folder", self)
        open_action.triggered.connect(self.open_export_folder_requested.emit)
        file_menu.addAction(open_action)
        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        pipe_menu = menu_bar.addMenu("&Pipeline")
        for label, slot, shortcut in [
            ("Run AI &Analysis", self.analyse_requested.emit, "Ctrl+A"),
            ("Re-Analyse &All", self.reanalyse_all_requested.emit, "Ctrl+Shift+A"),
            ("Preview &Culling…", self.culling_preview_requested.emit, "Ctrl+P"),
            ("Detect &Duplicates", self.duplicate_scan_requested.emit, ""),
        ]:
            act = QAction(label, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            pipe_menu.addAction(act)
        pipe_menu.addSeparator()
        for label, slot, shortcut in [
            ("Choose Edit &Style…", self.choose_style_requested.emit, "Ctrl+S"),
            ("&Export All Kept", self.export_requested.emit, "Ctrl+E"),
        ]:
            act = QAction(label, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            pipe_menu.addAction(act)

        nav_menu = menu_bar.addMenu("&Navigate")
        for i, label in enumerate(self.STEPS):
            act = QAction(label, self)
            act.setShortcut(QKeySequence(f"Alt+{i + 1}"))
            act.triggered.connect(lambda _, idx=i: self.go_to_step(idx))
            nav_menu.addAction(act)

        # -- Shortcuts menu --
        sc_menu = menu_bar.addMenu("&Shortcuts")
        shortcuts = [
            ("← →", "Navigate photos"),
            ("\\", "Before / After"),
            ("F", "Fit to screen"),
            ("1", "100% zoom"),
            ("2", "200% zoom"),
            ("C", "Toggle crop mode"),
            ("Enter", "Apply crop"),
            ("Del", "Trash current photo"),
            ("Esc", "Cancel crop / Close editor"),
            ("Ctrl+Z", "Undo"),
            ("Ctrl+Shift+Z", "Redo"),
            ("Ctrl+C", "Copy edits"),
            ("Ctrl+V", "Paste edits"),
            ("Ctrl+S", "Save all edits"),
            ("Scroll", "Zoom in / out"),
        ]
        for key, desc in shortcuts:
            act = QAction(f"{key:20s}  {desc}", self)
            act.setEnabled(False)  # display-only
            sc_menu.addAction(act)

        # -- Help menu --
        help_menu = menu_bar.addMenu("&Help")
        tutorial_action = QAction("Start &Tutorial", self)
        tutorial_action.setShortcut(QKeySequence("Ctrl+T"))
        tutorial_action.triggered.connect(self.tutorial_requested.emit)
        help_menu.addAction(tutorial_action)

    # ==================================================================
    # Dialog fallbacks (main.py compat)
    # ==================================================================

    def _show_settings(self) -> None:
        self.settings_changed.emit({})

    def show_settings_dialog(self, current_settings: dict) -> None:
        dialog = SettingsView(current_settings, self)
        dialog.settings_saved.connect(self.settings_changed.emit)
        dialog.exec()

    def _show_import_dialog(self) -> None:
        self.go_to_step(0)

    # ==================================================================
    # Helpers
    # ==================================================================

    def show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def show_update_banner(self, latest_version: str, download_url: str) -> None:
        """Show a persistent banner at the top of the window prompting the user to update."""
        self._update_banner_label.setText(
            f"\u2b06\ufe0f  Update available: v{latest_version} is ready to download."
        )
        try:
            self._update_banner_btn.clicked.disconnect()
        except TypeError:
            pass
        if download_url:
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            self._update_banner_btn.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(download_url))
            )
        self._update_banner.show()

    def show_info(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    # ==================================================================
    # Loading overlay control
    # ==================================================================

    def show_loading(self, title: str = "AI Processing…",
                     subtitle: str = "", total: int = 0) -> None:
        """Show the full-window loading overlay and block interaction."""
        self._loading_overlay.show_message(title, subtitle, total)

    def update_loading(self, current: int, total: int = 0,
                       subtitle: str = "") -> None:
        """Update the loading overlay progress."""
        self._loading_overlay.set_progress(current, total, subtitle)

    def hide_loading(self) -> None:
        """Hide the loading overlay and restore interaction."""
        self._loading_overlay.hide_modal()
