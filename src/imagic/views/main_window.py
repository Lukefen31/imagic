"""Main application window — shell that hosts all views and wires signals.

The ``MainWindow`` assembles the library grid, processing panel, filter bar,
menus, and toolbar.  It communicates with the controllers exclusively through
Qt signals/slots so the view layer remains fully decoupled from business logic.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QTabWidget,
    QToolBar,
    QWidget,
)

from imagic.views.export_gallery import ExportGalleryView
from imagic.views.import_view import ImportView
from imagic.views.library_view import LibraryView
from imagic.views.processing_view import ProcessingView
from imagic.views.settings_view import SettingsView
from imagic.views.widgets.status_bar import StatusBarWidget

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Top-level application window.

    Signals:
        import_requested: ``(directory: str, recursive: bool)``
        analyse_requested: Triggers AI analysis of pending photos.
        export_requested: Triggers batch export of KEEP photos.
        settings_changed: Emitted with the updated settings dict.
        refresh_requested: Asks the controller to reload library data.
    """

    import_requested = pyqtSignal(str, bool)
    analyse_requested = pyqtSignal()
    export_requested = pyqtSignal()
    duplicate_scan_requested = pyqtSignal()
    generate_thumbnails_requested = pyqtSignal()
    choose_style_requested = pyqtSignal()
    culling_preview_requested = pyqtSignal()
    settings_changed = pyqtSignal(dict)
    refresh_requested = pyqtSignal()
    clear_library_requested = pyqtSignal()
    open_export_folder_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Imagic — Photo Orchestrator")
        self.setMinimumSize(1024, 700)

        # Tabbed central area: Library | Exports
        self._tabs = QTabWidget()
        self.library_view = LibraryView()
        self.export_gallery = ExportGalleryView()
        self._tabs.addTab(self.library_view, "Library")
        self._tabs.addTab(self.export_gallery, "Exports")
        self.setCentralWidget(self._tabs)

        # Dockable processing panel
        self.processing_view = ProcessingView()
        dock = QDockWidget("Processing", self)
        dock.setWidget(self.processing_view)
        self.addDockWidget(self._bottom_area(), dock)

        # Status bar
        self.status_bar = StatusBarWidget()
        self.setStatusBar(self.status_bar)

        # Menus & toolbar
        self._build_menus()
        self._build_toolbar()

        # Periodic UI refresh timer (every 5 s)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_requested.emit)
        self._refresh_timer.start(5000)

        logger.info("MainWindow created.")

    # ------------------------------------------------------------------
    # Menu / Toolbar
    # ------------------------------------------------------------------
    def _build_menus(self) -> None:
        menu_bar: QMenuBar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("&File")

        import_action = QAction("&Import Folder…", self)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.triggered.connect(self._show_import_dialog)
        file_menu.addAction(import_action)

        thumbs_action = QAction("Generate &Thumbnails", self)
        thumbs_action.setShortcut(QKeySequence("Ctrl+T"))
        thumbs_action.triggered.connect(self.generate_thumbnails_requested.emit)
        file_menu.addAction(thumbs_action)

        file_menu.addSeparator()

        settings_action = QAction("&Settings…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._show_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        clear_action = QAction("&Clear Library", self)
        clear_action.triggered.connect(self.clear_library_requested.emit)
        file_menu.addAction(clear_action)

        open_exports_action = QAction("Open Export &Folder", self)
        open_exports_action.triggered.connect(self.open_export_folder_requested.emit)
        file_menu.addAction(open_exports_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # Pipeline menu
        pipeline_menu = menu_bar.addMenu("&Pipeline")

        analyse_action = QAction("Run AI &Analysis", self)
        analyse_action.setShortcut(QKeySequence("Ctrl+A"))
        analyse_action.triggered.connect(self.analyse_requested.emit)
        pipeline_menu.addAction(analyse_action)

        preview_action = QAction("Preview &Culling…", self)
        preview_action.setShortcut(QKeySequence("Ctrl+P"))
        preview_action.triggered.connect(self.culling_preview_requested.emit)
        pipeline_menu.addAction(preview_action)

        dup_action = QAction("Detect &Duplicates", self)
        dup_action.triggered.connect(self.duplicate_scan_requested.emit)
        pipeline_menu.addAction(dup_action)

        pipeline_menu.addSeparator()

        style_action = QAction("Choose Edit &Style…", self)
        style_action.setShortcut(QKeySequence("Ctrl+S"))
        style_action.triggered.connect(self.choose_style_requested.emit)
        pipeline_menu.addAction(style_action)

        export_action = QAction("&Export All Kept", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self.export_requested.emit)
        pipeline_menu.addAction(export_action)

    def _build_toolbar(self) -> None:
        toolbar: QToolBar = self.addToolBar("Main")
        toolbar.setMovable(False)

        toolbar.addAction("Import", self._show_import_dialog)
        toolbar.addAction("Analyse", self.analyse_requested.emit)
        toolbar.addAction("Culling ⊘", self.culling_preview_requested.emit)
        toolbar.addAction("Style…", self.choose_style_requested.emit)
        toolbar.addAction("Export", self.export_requested.emit)
        toolbar.addSeparator()
        toolbar.addAction("Exports ↗", lambda: self._tabs.setCurrentIndex(1))
        toolbar.addAction("Open Folder", self.open_export_folder_requested.emit)
        toolbar.addSeparator()
        toolbar.addAction("Clear Library", self.clear_library_requested.emit)
        toolbar.addAction("Refresh", self.refresh_requested.emit)

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------
    def _show_import_dialog(self) -> None:
        dialog = ImportView(self)
        dialog.import_requested.connect(self.import_requested.emit)
        dialog.exec()

    def _show_settings(self) -> None:
        """Must be called with current settings dict injected externally."""
        # The controller will connect this and provide the dict.
        self.settings_changed.emit({})  # placeholder — controller overrides

    def show_settings_dialog(self, current_settings: dict) -> None:
        """Open the settings dialog pre-filled with *current_settings*."""
        dialog = SettingsView(current_settings, self)
        dialog.settings_saved.connect(self.settings_changed.emit)
        dialog.exec()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _bottom_area():
        from PyQt6.QtCore import Qt
        return Qt.DockWidgetArea.BottomDockWidgetArea

    def show_error(self, title: str, message: str) -> None:
        """Show an error message box."""
        QMessageBox.critical(self, title, message)

    def show_info(self, title: str, message: str) -> None:
        """Show an informational message box."""
        QMessageBox.information(self, title, message)
