"""Settings dialog — configure CLI tool paths, thresholds, and preferences."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class SettingsView(QDialog):
    """Tabbed settings dialog.

    Signals:
        settings_saved: Emitted with a dict of updated settings when the user
            clicks *Save*.
    """

    settings_saved = pyqtSignal(dict)

    def __init__(self, current: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(550, 400)

        self._current = current
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_cli_tab(), "CLI Tools")
        tabs.addTab(self._build_processing_tab(), "Processing")
        tabs.addTab(self._build_ai_tab(), "AI")
        layout.addWidget(tabs)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btn_layout.addWidget(cancel)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        btn_layout.addWidget(save)
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------
    def _build_cli_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        cli = self._current.get("cli_tools", {})
        self._dt_edit = self._path_row(form, "darktable-cli:", cli.get("darktable_cli", ""))
        self._rt_edit = self._path_row(form, "rawtherapee-cli:", cli.get("rawtherapee_cli", ""))
        self._exif_edit = self._path_row(form, "exiftool:", cli.get("exiftool", ""))

        return tab

    def _build_processing_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        proc = self._current.get("processing", {})
        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 32)
        self._workers_spin.setValue(proc.get("max_workers", 4))
        form.addRow("Max workers:", self._workers_spin)

        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(1, 100)
        self._quality_spin.setValue(proc.get("jpeg_quality", 95))
        form.addRow("JPEG quality:", self._quality_spin)

        self._max_size_spin = QSpinBox()
        self._max_size_spin.setRange(0, 100000)
        self._max_size_spin.setSingleStep(100)
        self._max_size_spin.setSuffix(" KB")
        self._max_size_spin.setSpecialValueText("No limit")
        self._max_size_spin.setValue(proc.get("max_file_size_kb", 0))
        form.addRow("Max file size:", self._max_size_spin)

        self._output_edit = QLineEdit(proc.get("output_directory", ""))
        form.addRow("Output directory:", self._output_edit)

        return tab

    def _build_ai_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        ai = self._current.get("ai", {})
        self._ai_enabled = QCheckBox("Enable AI analysis")
        self._ai_enabled.setChecked(ai.get("enabled", True))
        form.addRow(self._ai_enabled)

        self._keep_spin = QDoubleSpinBox()
        self._keep_spin.setRange(0.0, 1.0)
        self._keep_spin.setSingleStep(0.05)
        self._keep_spin.setValue(ai.get("keep_threshold", 0.50))
        form.addRow("Keep threshold (≥):", self._keep_spin)

        self._trash_spin = QDoubleSpinBox()
        self._trash_spin.setRange(0.0, 1.0)
        self._trash_spin.setSingleStep(0.05)
        self._trash_spin.setValue(ai.get("trash_threshold", 0.35))
        form.addRow("Trash threshold (≤):", self._trash_spin)

        return tab

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _path_row(self, form: QFormLayout, label: str, value: str) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(value)
        row.addWidget(edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse_file(edit))
        row.addWidget(browse)
        form.addRow(label, row)
        return edit

    @staticmethod
    def _browse_file(target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(None, "Select executable")
        if path:
            target.setText(path)

    def _on_save(self) -> None:
        result = {
            "cli_tools": {
                "darktable_cli": self._dt_edit.text().strip(),
                "rawtherapee_cli": self._rt_edit.text().strip(),
                "exiftool": self._exif_edit.text().strip(),
            },
            "processing": {
                "max_workers": self._workers_spin.value(),
                "jpeg_quality": self._quality_spin.value(),
                "max_file_size_kb": self._max_size_spin.value(),
                "output_directory": self._output_edit.text().strip(),
            },
            "ai": {
                "enabled": self._ai_enabled.isChecked(),
                "keep_threshold": self._keep_spin.value(),
                "trash_threshold": self._trash_spin.value(),
            },
        }
        self.settings_saved.emit(result)
        self.accept()
