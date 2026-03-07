"""Processing view — panel showing batch processing status and task log."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ProcessingView(QWidget):
    """Panel showing active / completed tasks with progress indicators."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # Summary group
        summary_group = QGroupBox("Pipeline Summary")
        summary_layout = QHBoxLayout(summary_group)
        self._summary_label = QLabel("No active tasks.")
        summary_layout.addWidget(self._summary_label)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        summary_layout.addWidget(self._progress)
        layout.addWidget(summary_group)

        # Task table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Task", "Status", "Duration", "Error"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._clear_btn = QPushButton("Clear Completed")
        self._clear_btn.clicked.connect(self._clear_completed)
        btn_layout.addWidget(self._clear_btn)
        layout.addLayout(btn_layout)

    @pyqtSlot(list)
    def update_tasks(self, tasks: list[dict]) -> None:
        """Refresh the task table.

        Args:
            tasks: List of dicts with ``name``, ``status``, ``duration_s``, ``error``.
        """
        self._table.setRowCount(len(tasks))
        for row, t in enumerate(tasks):
            self._table.setItem(row, 0, QTableWidgetItem(t.get("name", "")))
            self._table.setItem(row, 1, QTableWidgetItem(t.get("status", "")))
            dur = t.get("duration_s")
            self._table.setItem(row, 2, QTableWidgetItem(f"{dur:.1f}s" if dur else "—"))
            self._table.setItem(row, 3, QTableWidgetItem(t.get("error", "")))

    @pyqtSlot(str, int, int)
    def set_summary(self, text: str, current: int, total: int) -> None:
        """Update the summary label and progress bar."""
        self._summary_label.setText(text)
        self._progress.setRange(0, max(total, 1))
        self._progress.setValue(current)

    def _clear_completed(self) -> None:
        """Remove completed rows from the table."""
        rows_to_remove = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item and item.text() in ("completed", "failed", "cancelled"):
                rows_to_remove.append(row)
        for row in reversed(rows_to_remove):
            self._table.removeRow(row)
