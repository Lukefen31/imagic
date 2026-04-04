"""Export options dialog shown before exporting photos."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

_BG = "#1e1e1e"
_SURFACE = "#2a2a2a"
_BORDER = "#444"
_TEXT = "#e0e0e0"
_TEXT_DIM = "#aaa"
_ACCENT = "#ff9800"
_ACCENT_HOVER = "#ffb74d"


class ExportOptionsDialog(QDialog):
    """Modal dialog for choosing export format, quality, and scope."""

    def __init__(self, batch_size: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Options")
        self.setFixedWidth(380)
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; color: {_TEXT}; }}"
            f"QLabel {{ color: {_TEXT}; }}"
        )
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 20, 24, 20)

        # ─── Title ───
        title = QLabel("Export Options")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {_TEXT};")
        layout.addWidget(title)

        # ─── File Format ───
        fmt_label = QLabel("File Format")
        fmt_label.setStyleSheet(f"font-size: 11px; color: {_TEXT_DIM}; font-weight: bold;")
        layout.addWidget(fmt_label)

        self._format_combo = QComboBox()
        self._format_combo.addItems(["JPEG", "PNG", "TIFF"])
        self._format_combo.setCurrentIndex(0)
        self._format_combo.setStyleSheet(
            f"QComboBox {{ background: {_SURFACE}; color: {_TEXT}; border: 1px solid {_BORDER}; "
            f"border-radius: 6px; padding: 6px 10px; font-size: 12px; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {_SURFACE}; color: {_TEXT}; "
            f"selection-background-color: {_ACCENT}; }}"
        )
        self._format_combo.currentTextChanged.connect(self._on_format_changed)
        layout.addWidget(self._format_combo)

        # ─── Quality Slider ───
        self._quality_label = QLabel("Quality: 95")
        self._quality_label.setStyleSheet(f"font-size: 11px; color: {_TEXT_DIM}; font-weight: bold;")
        layout.addWidget(self._quality_label)

        self._quality_slider = QSlider(Qt.Orientation.Horizontal)
        self._quality_slider.setRange(1, 100)
        self._quality_slider.setValue(95)
        self._quality_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ background: {_SURFACE}; height: 6px; border-radius: 3px; }}"
            f"QSlider::handle:horizontal {{ background: {_ACCENT}; width: 16px; height: 16px; "
            f"margin: -5px 0; border-radius: 8px; }}"
            f"QSlider::sub-page:horizontal {{ background: {_ACCENT}; border-radius: 3px; }}"
        )
        self._quality_slider.valueChanged.connect(
            lambda v: self._quality_label.setText(f"Quality: {v}")
        )
        layout.addWidget(self._quality_slider)

        # ─── Export Scope ───
        scope_label = QLabel("Export Scope")
        scope_label.setStyleSheet(f"font-size: 11px; color: {_TEXT_DIM}; font-weight: bold;")
        layout.addWidget(scope_label)

        scope_row = QHBoxLayout()
        scope_row.setSpacing(16)
        self._radio_single = QRadioButton("This Photo")
        self._radio_batch = QRadioButton(f"All Photos ({batch_size})")
        self._radio_single.setChecked(True)
        for rb in (self._radio_single, self._radio_batch):
            rb.setStyleSheet(
                f"QRadioButton {{ color: {_TEXT}; font-size: 12px; spacing: 6px; }}"
                f"QRadioButton::indicator {{ width: 14px; height: 14px; }}"
                f"QRadioButton::indicator:checked {{ background: {_ACCENT}; border: 2px solid {_ACCENT}; border-radius: 7px; }}"
                f"QRadioButton::indicator:unchecked {{ background: {_SURFACE}; border: 2px solid {_BORDER}; border-radius: 7px; }}"
            )
            scope_row.addWidget(rb)

        self._scope_group = QButtonGroup(self)
        self._scope_group.addButton(self._radio_single, 0)
        self._scope_group.addButton(self._radio_batch, 1)
        layout.addLayout(scope_row)

        # Disable "All Photos" if only one photo
        if batch_size <= 1:
            self._radio_batch.setEnabled(False)
            self._radio_batch.setStyleSheet(
                self._radio_batch.styleSheet()
                + f"QRadioButton:disabled {{ color: #555; }}"
            )

        layout.addSpacing(10)

        # ─── Buttons ───
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {_SURFACE}; color: {_TEXT_DIM}; "
            f"border: 1px solid {_BORDER}; border-radius: 6px; padding: 8px 20px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: #333; color: {_TEXT}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        export_btn = QPushButton("Export")
        export_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #111; font-weight: bold; "
            f"border: none; border-radius: 6px; padding: 8px 28px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {_ACCENT_HOVER}; }}"
            f"QPushButton:pressed {{ background: #f57c00; }}"
        )
        export_btn.clicked.connect(self.accept)
        export_btn.setDefault(True)
        btn_row.addWidget(export_btn)

        layout.addLayout(btn_row)

    # ── Helpers ──────────────────────────────────────────

    def _on_format_changed(self, text: str) -> None:
        is_jpeg = text == "JPEG"
        self._quality_slider.setEnabled(is_jpeg)
        self._quality_label.setEnabled(is_jpeg)
        if not is_jpeg:
            self._quality_label.setText("Quality: N/A")
        else:
            self._quality_label.setText(f"Quality: {self._quality_slider.value()}")

    # ── Public API ───────────────────────────────────────

    def chosen_format(self) -> str:
        """Return lowercase format string: 'jpeg', 'png', or 'tiff'."""
        return self._format_combo.currentText().lower()

    def chosen_quality(self) -> int:
        """Return the JPEG quality value (1-100)."""
        return self._quality_slider.value()

    def export_all(self) -> bool:
        """Return True if the user chose to export all photos."""
        return self._scope_group.checkedId() == 1
