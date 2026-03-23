"""Custom status bar with task-queue progress indicator."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import QLabel, QProgressBar, QStatusBar, QWidget


class StatusBarWidget(QStatusBar):
    """Application status bar showing pipeline stats and task progress.

    Sections (left → right):
    * Message label — general status text.
    * Pipeline counts — photos in each stage.
    * Progress bar — active task queue progress.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._message = QLabel("Ready")
        self._pipeline = QLabel("")
        self._progress = QProgressBar()
        self._progress.setFixedWidth(200)
        self._progress.setTextVisible(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()

        self.addWidget(self._message, stretch=1)
        self.addPermanentWidget(self._pipeline)
        self.addPermanentWidget(self._progress)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    @pyqtSlot(str)
    def set_status(self, text: str) -> None:
        """Update the main message label."""
        self._message.setText(text)

    @pyqtSlot(dict)
    def set_pipeline_counts(self, counts: dict[str, int]) -> None:
        """Update the pipeline count display.

        Args:
            counts: Mapping of status → count (from ``AppController.get_pipeline_summary``).
        """
        keep = counts.get("keep", 0)
        exported = counts.get("exported", 0)
        trash = counts.get("trash", 0)
        review = counts.get("culled", 0)
        parts = []
        if keep and exported:
            parts.append(f"Keep: {keep} ({exported} exported)")
        elif keep:
            parts.append(f"Keep: {keep}")
        elif exported:
            parts.append(f"Exported: {exported}")
        if trash:
            parts.append(f"Trash: {trash}")
        if review:
            parts.append(f"Review: {review}")
        self._pipeline.setText("  |  ".join(parts))

    @pyqtSlot(int, int)
    def set_progress(self, current: int, total: int) -> None:
        """Show / update the progress bar.

        Args:
            current: Items completed.
            total: Total items (0 to hide the bar).
        """
        if total <= 0:
            self._progress.hide()
            return
        self._progress.show()
        self._progress.setRange(0, total)
        self._progress.setValue(current)

    @pyqtSlot()
    def hide_progress(self) -> None:
        """Hide the progress bar."""
        self._progress.hide()
