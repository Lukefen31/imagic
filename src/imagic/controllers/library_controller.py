"""Library controller — orchestrates directory scanning and photo ingestion.

This controller ties the ``LibraryScanner``, ``ThumbnailGenerator``, and the
database together.  All heavy I/O is dispatched to the ``TaskQueue``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional, Set

from imagic.models.database import DatabaseManager
from imagic.models.enums import PhotoStatus
from imagic.models.photo import Photo
from imagic.services.scanner import LibraryScanner, ScanResult
from imagic.services.task_queue import TaskItem, TaskQueue
from imagic.services.thumbnail_generator import generate_thumbnail
from imagic.utils.path_utils import find_sidecar, normalise

logger = logging.getLogger(__name__)


class LibraryController:
    """Manages the photo library: scanning, ingesting, and thumbnail creation.

    Args:
        task_queue: Background task queue (shared with other controllers).
        scanner: ``LibraryScanner`` instance.
        thumbnail_dir: Directory where thumbnails are stored.
        thumbnail_size: ``(width, height)`` tuple for thumbnail generation.
        on_scan_complete: Optional callback invoked with the ``ScanResult``.
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        scanner: Optional[LibraryScanner] = None,
        thumbnail_dir: Path = Path("thumbnails"),
        thumbnail_size: tuple[int, int] = (320, 320),
        on_scan_complete: Optional[Callable[[ScanResult], None]] = None,
    ) -> None:
        self._queue = task_queue
        self._scanner = scanner or LibraryScanner()
        self._thumb_dir = normalise(thumbnail_dir)
        self._thumb_size = thumbnail_size
        self._on_scan_complete = on_scan_complete

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def scan_directory(self, directory: str | Path) -> TaskItem:
        """Submit a directory scan to the task queue.

        Args:
            directory: Root directory to scan.

        Returns:
            ``TaskItem`` tracking the background scan.
        """
        return self._queue.submit(
            self._scan_and_ingest,
            directory,
            name=f"Scan {Path(directory).name}",
        )

    def _scan_and_ingest(self, directory: str | Path) -> ScanResult:
        """Perform the scan and ingest new files (runs in worker thread)."""
        known = self._known_paths()
        result = self._scanner.scan(directory, known)
        ingested = self._ingest_files(result.new_files)
        logger.info("Ingested %d new photos from %s.", ingested, directory)

        if self._on_scan_complete:
            try:
                self._on_scan_complete(result)
            except Exception:
                logger.exception("on_scan_complete callback error")

        # Auto-generate thumbnails for newly ingested files.
        if ingested > 0:
            logger.info("Auto-generating thumbnails for %d new photos…", ingested)
            try:
                generated = self._generate_thumbnails_worker(None)
                logger.info("Auto-generated %d thumbnails.", generated)
            except Exception:
                logger.exception("Auto-thumbnail generation failed.")

        return result

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    def _ingest_files(self, file_paths: List[Path]) -> int:
        """Insert new photo records into the database.

        Args:
            file_paths: Absolute paths to new RAW files.

        Returns:
            Number of records successfully inserted.
        """
        db = DatabaseManager.get()
        session = db.get_session()
        count = 0
        try:
            for fp in file_paths:
                try:
                    sidecar = find_sidecar(fp)
                    photo = Photo(
                        file_path=str(fp),
                        file_name=fp.name,
                        file_extension=fp.suffix.lower(),
                        file_size_bytes=fp.stat().st_size,
                        directory=str(fp.parent),
                        status=PhotoStatus.PENDING.value,
                        sidecar_path=str(sidecar) if sidecar else None,
                    )
                    session.add(photo)
                    count += 1
                except Exception as exc:
                    logger.warning("Failed to ingest %s: %s", fp, exc)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Batch ingest failed — rolled back.")
        finally:
            session.close()
        return count

    # ------------------------------------------------------------------
    # Thumbnails
    # ------------------------------------------------------------------
    def generate_thumbnails(self, photo_ids: Optional[List[int]] = None) -> TaskItem:
        """Submit thumbnail generation for photos that lack one.

        Args:
            photo_ids: Specific IDs to process.  If ``None``, all photos
                without a thumbnail are selected.

        Returns:
            ``TaskItem`` tracking the background work.
        """
        return self._queue.submit(
            self._generate_thumbnails_worker,
            photo_ids,
            name="Generate thumbnails",
        )

    def _generate_thumbnails_worker(self, photo_ids: Optional[List[int]]) -> int:
        db = DatabaseManager.get()
        session = db.get_session()
        generated = 0
        try:
            query = session.query(Photo).filter(Photo.thumbnail_path.is_(None))
            if photo_ids:
                query = query.filter(Photo.id.in_(photo_ids))
            photos: List[Photo] = query.all()

            for photo in photos:
                raw = Path(photo.file_path)
                thumb_path = self._thumb_dir / f"{raw.stem}_thumb.jpg"
                result = generate_thumbnail(raw, thumb_path, max_size=self._thumb_size)
                if result:
                    photo.thumbnail_path = str(result)
                    generated += 1

            session.commit()
            logger.info("Generated %d thumbnails.", generated)
        except Exception:
            session.rollback()
            logger.exception("Thumbnail batch failed.")
        finally:
            session.close()
        return generated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _known_paths(self) -> Set[str]:
        """Return all file paths already in the database."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            rows = session.query(Photo.file_path).all()
            return {r[0] for r in rows}
        finally:
            session.close()
