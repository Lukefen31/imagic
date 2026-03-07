"""Unit tests for the TaskQueue."""

import time
from concurrent.futures import Future

from imagic.models.enums import TaskStatus
from imagic.services.task_queue import TaskItem, TaskQueue


class TestTaskQueue:
    """Tests for ``TaskQueue``."""

    def test_submit_and_complete(self) -> None:
        """A simple task should run and reach COMPLETED status."""
        queue = TaskQueue(max_workers=2)
        try:
            item = queue.submit(lambda: 42, name="test-task")
            assert isinstance(item, TaskItem)
            result = item.future.result(timeout=5)
            assert result == 42
            assert item.status == TaskStatus.COMPLETED
        finally:
            queue.shutdown()

    def test_failed_task(self) -> None:
        """A task that raises should be marked FAILED with error text."""
        queue = TaskQueue(max_workers=1)
        try:
            def bad():
                raise ValueError("boom")

            item = queue.submit(bad, name="fail-task")
            try:
                item.future.result(timeout=5)
            except ValueError:
                pass
            assert item.status == TaskStatus.FAILED
            assert "boom" in (item.error or "")
        finally:
            queue.shutdown()

    def test_pending_count(self) -> None:
        """``pending_count`` should reflect queued + running tasks."""
        queue = TaskQueue(max_workers=1)
        try:
            import threading
            barrier = threading.Event()

            def blocker():
                barrier.wait(timeout=5)

            item1 = queue.submit(blocker, name="blocker")
            item2 = queue.submit(lambda: 1, name="quick")

            # At least one should be queued or running.
            assert queue.pending_count() >= 1
            barrier.set()
            item1.future.result(timeout=5)
            item2.future.result(timeout=5)
        finally:
            queue.shutdown()

    def test_callbacks_fire(self) -> None:
        """on_start and on_complete callbacks should be invoked."""
        started = []
        completed = []

        queue = TaskQueue(
            max_workers=1,
            on_start=lambda t: started.append(t.task_id),
            on_complete=lambda t: completed.append(t.task_id),
        )
        try:
            item = queue.submit(lambda: "ok", name="cb-test")
            item.future.result(timeout=5)
            assert item.task_id in started
            assert item.task_id in completed
        finally:
            queue.shutdown()

    def test_all_tasks(self) -> None:
        """``all_tasks`` should return every submitted task."""
        queue = TaskQueue(max_workers=2)
        try:
            items = [queue.submit(lambda: i, name=f"t{i}") for i in range(5)]
            for it in items:
                it.future.result(timeout=5)
            assert len(queue.all_tasks()) == 5
        finally:
            queue.shutdown()
