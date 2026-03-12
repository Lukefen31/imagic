"""Startup dialog for account sign-in and desktop license activation."""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ActivationDialog(QDialog):
    def __init__(
        self,
        license_key: str = "",
        message: str = "Enter the product key from your purchase email to activate this device.",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Activate imagic Desktop")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setStyleSheet(
            "QDialog { background: #111; color: #ddd; }"
            "QLabel { color: #ddd; }"
            "QLineEdit { background: #1a1a1a; color: #eee; border: 1px solid #333; border-radius: 6px; padding: 8px; }"
            "QLineEdit:focus { border-color: #ff9800; }"
            "QPushButton { border-radius: 6px; padding: 10px 18px; font-weight: bold; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel("Desktop Activation Required")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ff9800;")
        root.addWidget(title)

        subtitle = QLabel(message)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #aaa;")
        root.addWidget(subtitle)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._license_key = QLineEdit(license_key)
        self._license_key.setPlaceholderText("IMAGIC-DESK-XXXX-XXXX-XXXX-XXXX")
        form.addWidget(QLabel("Product key"), 0, 0)
        form.addWidget(self._license_key, 0, 1)
        root.addLayout(form)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #f28b82;")
        root.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel_btn = QPushButton("Exit")
        cancel_btn.setStyleSheet("QPushButton { background: #2a2a2a; color: #ddd; border: 1px solid #444; }")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        self._activate_btn = QPushButton("Activate")
        self._activate_btn.setDefault(True)
        self._activate_btn.setStyleSheet("QPushButton { background: #ff9800; color: #111; border: none; }")
        self._activate_btn.clicked.connect(self.accept)
        buttons.addWidget(self._activate_btn)
        root.addLayout(buttons)

    def credentials(self) -> str:
        return self._license_key.text().strip().upper()

    def set_error(self, message: str) -> None:
        self._status.setText(message)

    def set_busy(self, busy: bool, message: Optional[str] = None) -> None:
        self._activate_btn.setEnabled(not busy)
        if busy:
            self._activate_btn.setText("Activating…")
        else:
            self._activate_btn.setText("Activate")
        if message is not None:
            self._status.setText(message)

    def accept(self) -> None:  # type: ignore[override]
        license_key = self.credentials()
        if not license_key:
            self.set_error("A product key is required.")
            return
        super().accept()