"""Filter bar — combo boxes for status and score range filtering."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from imagic.models.enums import PhotoStatus


class FilterBar(QWidget):
    """Horizontal toolbar with status and score filters.

    Signals:
        filters_changed: Emitted whenever any filter value changes.
            Payload is ``(status_filter: str, min_score: float, max_score: float)``.
    """

    filters_changed = pyqtSignal(str, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Status filter
        layout.addWidget(QLabel("Status:"))
        self._status_combo = QComboBox()
        self._status_combo.addItem("All", "")
        for s in PhotoStatus:
            self._status_combo.addItem(s.value.capitalize(), s.value)
        self._status_combo.currentIndexChanged.connect(self._emit)
        layout.addWidget(self._status_combo)

        layout.addSpacing(16)

        # Min score
        layout.addWidget(QLabel("Score ≥"))
        self._min_score = QDoubleSpinBox()
        self._min_score.setRange(0.0, 1.0)
        self._min_score.setSingleStep(0.05)
        self._min_score.setValue(0.0)
        self._min_score.valueChanged.connect(self._emit)
        layout.addWidget(self._min_score)

        # Max score
        layout.addWidget(QLabel("≤"))
        self._max_score = QDoubleSpinBox()
        self._max_score.setRange(0.0, 1.0)
        self._max_score.setSingleStep(0.05)
        self._max_score.setValue(1.0)
        self._max_score.valueChanged.connect(self._emit)
        layout.addWidget(self._max_score)

        layout.addStretch()

    def _emit(self) -> None:
        self.filters_changed.emit(
            self._status_combo.currentData() or "",
            self._min_score.value(),
            self._max_score.value(),
        )
