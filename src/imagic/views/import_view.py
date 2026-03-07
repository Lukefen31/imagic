"""Import / ingest dialog — lets the user pick a folder and start scanning."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ImportView(QDialog):
    """Dialog for importing a new folder of photos.

    Signals:
        import_requested: Emitted with ``(directory_path, recursive)`` when
            the user clicks *Import*.
    """

    import_requested = pyqtSignal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Photos")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        # Directory picker
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Folder:"))
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("Select a directory…")
        dir_layout.addWidget(self._dir_edit)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        dir_layout.addWidget(browse_btn)
        layout.addLayout(dir_layout)

        # Options
        self._recursive_cb = QCheckBox("Scan subdirectories")
        self._recursive_cb.setChecked(True)
        layout.addWidget(self._recursive_cb)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        import_btn = QPushButton("Import")
        import_btn.setDefault(True)
        import_btn.clicked.connect(self._on_import)
        btn_layout.addWidget(import_btn)

        layout.addLayout(btn_layout)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Photo Directory")
        if path:
            self._dir_edit.setText(path)

    def _on_import(self) -> None:
        directory = self._dir_edit.text().strip()
        if directory and Path(directory).is_dir():
            self.import_requested.emit(directory, self._recursive_cb.isChecked())
            self.accept()
