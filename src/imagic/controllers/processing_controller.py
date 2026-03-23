"""Processing controller — batch export pipeline.

Iterates over photos marked ``KEEP``, submits each to the ``ExportService``
via the ``TaskQueue``, and tracks progress.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional

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

        # Thread-safe export progress state
        self._progress_lock = threading.Lock()
        self._progress: Dict[str, object] = {
            "current": 0,
            "total": 0,
            "phase": "idle",
            "current_file": "",
        }

    # ------------------------------------------------------------------
    # Progress tracking (thread-safe)
    # ------------------------------------------------------------------
    def get_export_progress(self) -> Dict[str, object]:
        """Return a snapshot of export progress (safe to call from any thread)."""
        with self._progress_lock:
            return dict(self._progress)

    def _set_export_progress(
        self, current: int, total: int, phase: str = "exporting",
        current_file: str = "",
    ) -> None:
        with self._progress_lock:
            self._progress["current"] = current
            self._progress["total"] = total
            self._progress["phase"] = phase
            self._progress["current_file"] = current_file

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
        """Export all KEEP and EXPORTED photos (runs in worker thread)."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photos: List[Photo] = (
                session.query(Photo)
                .filter(
                    Photo.status.in_([
                        PhotoStatus.KEEP.value,
                        PhotoStatus.EXPORTED.value,
                    ])
                )
                .all()
            )
            ids = [p.id for p in photos]
            # Build a quick name lookup for progress reporting
            self._export_name_map = {p.id: p.file_name for p in photos}
        finally:
            session.close()

        return self._export_ids(ids)

    def _export_ids(self, photo_ids: List[int]) -> dict:
        """Export a list of photos by ID."""
        total = len(photo_ids)
        results = {"exported": 0, "failed": 0, "errors": []}
        self._set_export_progress(0, total, "exporting")

        for i, pid in enumerate(photo_ids):
            fname = getattr(self, "_export_name_map", {}).get(pid, f"photo {pid}")
            self._set_export_progress(i, total, "exporting", fname)

            # Yield CPU between exports so the editor stays responsive
            time.sleep(0.05)
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
        self._set_export_progress(total, total, "done")
        return results
