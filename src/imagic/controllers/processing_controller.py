"""Processing controller — batch export pipeline.

Iterates over photos marked ``KEEP``, submits each to the ``ExportService``
via the ``TaskQueue``, and tracks progress.
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from imagic.models.database import DatabaseManager
from imagic.models.enums import PhotoStatus
from imagic.models.photo import Photo
from imagic.services.export_service import ExportService
from imagic.services.task_queue import TaskItem, TaskQueue

logger = logging.getLogger(__name__)


class ProcessingController:
    """Manages the batch export pipeline.

    Args:
        task_queue: Shared background task queue.
        export_service: Configured ``ExportService``.
        on_photo_exported: Optional callback invoked per exported photo.
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        export_service: ExportService,
        on_photo_exported: Optional[Callable[[int, bool], None]] = None,
    ) -> None:
        self._queue = task_queue
        self._export = export_service
        self._on_exported = on_photo_exported

    def export_all_kept(self) -> TaskItem:
        """Queue export tasks for every photo with status ``KEEP``.

        Returns:
            ``TaskItem`` tracking the batch.
        """
        return self._queue.submit(
            self._export_batch,
            name="Batch export (KEEP)",
        )

    def export_by_ids(self, photo_ids: List[int]) -> TaskItem:
        """Queue export tasks for specific photo IDs.

        Args:
            photo_ids: Primary keys of ``Photo`` records to export.

        Returns:
            ``TaskItem`` tracking the batch.
        """
        return self._queue.submit(
            self._export_ids,
            photo_ids,
            name=f"Export {len(photo_ids)} photos",
        )

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------
    def _export_batch(self) -> dict:
        """Export all KEEP photos (runs in worker thread)."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photos: List[Photo] = (
                session.query(Photo)
                .filter(Photo.status == PhotoStatus.KEEP.value)
                .all()
            )
            ids = [p.id for p in photos]
        finally:
            session.close()

        return self._export_ids(ids)

    def _export_ids(self, photo_ids: List[int]) -> dict:
        """Export a list of photos by ID."""
        results = {"exported": 0, "failed": 0, "errors": []}

        for pid in photo_ids:
            try:
                cli_result = self._export.export_photo(pid)
                if cli_result.success:
                    results["exported"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(
                        {"photo_id": pid, "stderr": cli_result.stderr[:512]}
                    )

                if self._on_exported:
                    try:
                        self._on_exported(pid, cli_result.success)
                    except Exception:
                        logger.exception("on_photo_exported callback error")

            except Exception as exc:
                results["failed"] += 1
                results["errors"].append({"photo_id": pid, "error": str(exc)[:512]})
                logger.error("Export error for photo %d: %s", pid, exc)

        logger.info(
            "Batch export complete: %d exported, %d failed.",
            results["exported"],
            results["failed"],
        )
        return results
