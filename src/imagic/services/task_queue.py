"""Non-blocking Task Queue with worker threads.

The ``TaskQueue`` ensures that heavy operations (CLI exports, AI scoring, etc.)
run in background threads so the PyQt6 UI never freezes.  Tasks are tracked
with status and result callbacks that emit Qt signals.

Architecture
------------
* A ``concurrent.futures.ThreadPoolExecutor`` provides the worker pool.
* Each submitted callable is wrapped in a ``TaskItem`` that records status,
  timestamps, and error information.
* Optional PyQt6 signals (``task_started``, ``task_completed``, ``task_failed``)
  keep the GUI informed without tight coupling — the queue works perfectly
  fine without a running event loop (unit-testable).
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from imagic.models.enums import TaskStatus

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data class that wraps each submitted task
# ------------------------------------------------------------------
@dataclass
class TaskItem:
    """Metadata wrapper around a queued callable.

    Attributes:
        task_id: Unique identifier.
        name: Human-readable label shown in the UI.
        status: Current ``TaskStatus``.
        submitted_at: Unix timestamp of submission.
        started_at: Unix timestamp when execution began.
        completed_at: Unix timestamp when execution finished.
        result: Return value of the callable (on success).
        error: Exception text (on failure).
        future: The ``Future`` returned by the executor.
    """

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    status: TaskStatus = TaskStatus.QUEUED
    submitted_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    future: Optional[Future] = None  # type: ignore[type-arg]


# ------------------------------------------------------------------
# Callback type aliases
# ------------------------------------------------------------------
OnTaskStart = Callable[[TaskItem], None]
OnTaskComplete = Callable[[TaskItem], None]
OnTaskFailed = Callable[[TaskItem], None]


class TaskQueue:
    """Thread-pool backed, non-blocking task queue.

    Args:
        max_workers: Number of concurrent worker threads.
        on_start: Optional callback fired when a task begins executing.
        on_complete: Optional callback fired when a task succeeds.
        on_failed: Optional callback fired when a task raises.
    """

    def __init__(
        self,
        max_workers: int = 4,
        on_start: Optional[OnTaskStart] = None,
        on_complete: Optional[OnTaskComplete] = None,
        on_failed: Optional[OnTaskFailed] = None,
    ) -> None:
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._tasks: Dict[str, TaskItem] = {}

        self._on_start = on_start
        self._on_complete = on_complete
        self._on_failed = on_failed

        logger.info("TaskQueue initialised with %d workers.", max_workers)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        name: str = "",
        **kwargs: Any,
    ) -> TaskItem:
        """Submit a callable to the worker pool.

        Args:
            fn: The function to run in a background thread.
            *args: Positional arguments forwarded to *fn*.
            name: Human-readable label for UI display.
            **kwargs: Keyword arguments forwarded to *fn*.

        Returns:
            A ``TaskItem`` that can be polled for status.
        """
        item = TaskItem(name=name or fn.__name__)
        self._tasks[item.task_id] = item

        future = self._executor.submit(self._run_task, item, fn, *args, **kwargs)
        item.future = future
        logger.debug("Task submitted: %s (%s)", item.name, item.task_id)
        return item

    def cancel(self, task_id: str) -> bool:
        """Attempt to cancel a queued task.

        Args:
            task_id: The ``TaskItem.task_id`` to cancel.

        Returns:
            ``True`` if the task was successfully cancelled.
        """
        item = self._tasks.get(task_id)
        if item and item.future and item.future.cancel():
            item.status = TaskStatus.CANCELLED
            logger.info("Task cancelled: %s (%s)", item.name, task_id)
            return True
        return False

    def get_task(self, task_id: str) -> Optional[TaskItem]:
        """Retrieve a task by its ID."""
        return self._tasks.get(task_id)

    def pending_count(self) -> int:
        """Return the number of tasks that are queued or running."""
        return sum(
            1 for t in self._tasks.values()
            if t.status in (TaskStatus.QUEUED, TaskStatus.RUNNING)
        )

    def all_tasks(self) -> List[TaskItem]:
        """Return a snapshot of all tracked tasks."""
        return list(self._tasks.values())

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the thread pool.

        Args:
            wait: Block until all running tasks have finished.
        """
        logger.info("TaskQueue shutting down (wait=%s)...", wait)
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _run_task(
        self, item: TaskItem, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Wrapper executed inside the worker thread."""
        item.status = TaskStatus.RUNNING
        item.started_at = time.time()

        if self._on_start:
            try:
                self._on_start(item)
            except Exception:
                logger.exception("on_start callback error for %s", item.task_id)

        try:
            result = fn(*args, **kwargs)
            item.result = result
            item.status = TaskStatus.COMPLETED
            item.completed_at = time.time()
            logger.info(
                "Task completed: %s (%s) in %.2fs",
                item.name, item.task_id,
                item.completed_at - (item.started_at or item.submitted_at),
            )
            if self._on_complete:
                try:
                    self._on_complete(item)
                except Exception:
                    logger.exception("on_complete callback error for %s", item.task_id)
            return result

        except Exception as exc:
            item.status = TaskStatus.FAILED
            item.error = str(exc)
            item.completed_at = time.time()
            logger.error(
                "Task FAILED: %s (%s) — %s", item.name, item.task_id, exc, exc_info=True
            )
            if self._on_failed:
                try:
                    self._on_failed(item)
                except Exception:
                    logger.exception("on_failed callback error for %s", item.task_id)
            raise
