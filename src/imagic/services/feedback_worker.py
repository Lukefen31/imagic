"""Background worker for recording culling feedback without blocking the UI.

This module provides a QThread-based worker that processes culling feedback
updates asynchronously, keeping the main UI thread responsive during the
expensive model recomputation and file I/O operations.
"""

import logging
from typing import Dict, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, QMetaObject, Qt

logger = logging.getLogger(__name__)


class FeedbackWorker(QObject):
    """Worker that processes cull feedback in a background thread."""

    # Signals emitted by the worker
    finished = pyqtSignal()  # Emitted when processing completes
    error = pyqtSignal(str)  # Emitted if an error occurs

    def __init__(self):
        super().__init__()
        self._file_name: str = ""
        self._auto_decision: str = ""
        self._user_decision: str = ""
        self._quality_score: float = 0.0
        self._metric_scores: Dict[str, float] = {}
        self._iso: Optional[int] = None
        self._mean_brightness: float = 128.0
        self._thumbnail_path: Optional[str] = None

    def set_feedback_data(
        self,
        file_name: str,
        auto_decision: str,
        user_decision: str,
        quality_score: float,
        metric_scores: Dict[str, float],
        iso: Optional[int] = None,
        mean_brightness: float = 128.0,
        thumbnail_path: Optional[str] = None,
    ) -> None:
        """Set the feedback data to process."""
        self._file_name = file_name
        self._auto_decision = auto_decision
        self._user_decision = user_decision
        self._quality_score = quality_score
        self._metric_scores = metric_scores
        self._iso = iso
        self._mean_brightness = mean_brightness
        self._thumbnail_path = thumbnail_path

    @pyqtSlot()
    def process(self) -> None:
        """Process the feedback in the background."""
        try:
            # Compute brightness from thumbnail in the background thread
            brightness = self._mean_brightness
            if self._thumbnail_path:
                try:
                    from PIL import Image as _PILImage, ImageStat as _PILStat
                    _tb = _PILImage.open(self._thumbnail_path).convert("L")
                    brightness = _PILStat.Stat(_tb).mean[0]
                    _tb.close()
                except Exception:
                    logger.debug("Could not compute brightness from %s", self._thumbnail_path)

            from imagic.ai.feedback_learner import get_learner

            learner = get_learner()
            learner.record_cull_feedback(
                file_name=self._file_name,
                auto_decision=self._auto_decision,
                user_decision=self._user_decision,
                quality_score=self._quality_score,
                metric_scores=self._metric_scores,
                iso=self._iso,
                mean_brightness=brightness,
            )
            self.finished.emit()
        except Exception as e:
            logger.exception("Error processing cull feedback in worker")
            self.error.emit(str(e))


class FeedbackThreadPool:
    """Manages a pool of worker threads for non-blocking feedback recording."""

    def __init__(self, num_threads: int = 2):
        self._threads = [QThread() for _ in range(num_threads)]
        self._workers = [FeedbackWorker() for _ in range(num_threads)]
        self._current_thread_idx = 0
        self._active = True

        # Move workers to their threads and connect signals
        for worker, thread in zip(self._workers, self._threads):
            worker.moveToThread(thread)
            # Don't quit immediately - reuse threads
            worker.finished.connect(self._on_worker_finished)
            worker.error.connect(self._handle_error)
            thread.start()

    def record_feedback_async(
        self,
        file_name: str,
        auto_decision: str,
        user_decision: str,
        quality_score: float,
        metric_scores: Dict[str, float],
        iso: Optional[int] = None,
        mean_brightness: float = 128.0,
        thumbnail_path: Optional[str] = None,
    ) -> None:
        """Submit feedback for processing in a background thread."""
        if not self._active:
            logger.warning("Feedback thread pool is shutting down, discarding feedback")
            return

        worker = self._workers[self._current_thread_idx]

        # Round-robin to next thread for next call
        self._current_thread_idx = (self._current_thread_idx + 1) % len(self._workers)

        # Configure and queue the worker
        worker.set_feedback_data(
            file_name=file_name,
            auto_decision=auto_decision,
            user_decision=user_decision,
            quality_score=quality_score,
            metric_scores=metric_scores,
            iso=iso,
            mean_brightness=mean_brightness,
            thumbnail_path=thumbnail_path,
        )

        # Queue the process call to run on the thread
        QMetaObject.invokeMethod(
            worker, "process", Qt.ConnectionType.QueuedConnection
        )

    def _on_worker_finished(self) -> None:
        """Called when a worker finishes processing."""
        pass  # Threads are reused; no action needed

    def _handle_error(self, error_msg: str) -> None:
        """Handle errors from worker threads."""
        logger.error("Feedback worker error: %s", error_msg)

    def shutdown(self) -> None:
        """Cleanly shutdown all worker threads."""
        self._active = False
        for thread in self._threads:
            thread.quit()
            thread.wait(5000)  # 5 second timeout


# Global thread pool instance
_pool: Optional[FeedbackThreadPool] = None


def get_feedback_pool() -> FeedbackThreadPool:
    """Get or create the global feedback thread pool."""
    global _pool
    if _pool is None:
        _pool = FeedbackThreadPool(num_threads=2)
    return _pool

