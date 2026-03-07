"""Application controller — top-level lifecycle management.

Responsible for:
* Initialising the database, settings, logging, and task queue.
* Wiring controllers together.
* Handling application startup, resume-on-crash, and shutdown.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from imagic.config.settings import Settings
from imagic.models.database import DatabaseManager
from imagic.models.enums import ExportFormat, PhotoStatus
from imagic.models.photo import Photo
from imagic.services.cli_orchestrator import CLIOrchestrator
from imagic.services.export_service import ExportService
from imagic.services.task_queue import TaskQueue
from imagic.utils.logger import setup_logging

logger = logging.getLogger(__name__)


class AppController:
    """Master controller that owns the application lifecycle.

    Attributes:
        settings: Global ``Settings`` instance.
        db: ``DatabaseManager`` singleton.
        task_queue: Background ``TaskQueue``.
        cli: ``CLIOrchestrator`` for external tool calls.
        export_service: Export pipeline manager.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        # 1. Settings
        self.settings = Settings.init(config_path)

        # 2. Logging
        log_file = Path(self.settings.get_nested("app", "log_file", default="imagic.log"))
        log_level = self.settings.get_nested("app", "log_level", default="INFO")
        logging_yaml = Path(__file__).resolve().parents[3] / "config" / "logging.yaml"
        setup_logging(
            config_path=logging_yaml if logging_yaml.is_file() else None,
            log_file=log_file,
            level=log_level,
        )
        logger.info("=== Imagic starting ===")

        # 3. Database
        db_path = Path(self.settings.get_nested("app", "database", default="imagic.db"))
        self.db = DatabaseManager.init(db_path)

        # 4. Task queue
        max_workers = int(self.settings.get_nested("processing", "max_workers", default=4))
        self.task_queue = TaskQueue(max_workers=max_workers)

        # 5. CLI orchestrator
        cli_cfg = self.settings["cli_tools"]
        self.cli = CLIOrchestrator(
            darktable_cli=cli_cfg.get("darktable_cli", ""),
            rawtherapee_cli=cli_cfg.get("rawtherapee_cli", ""),
            exiftool=cli_cfg.get("exiftool", ""),
        )

        # 6. Export service
        output_dir = Path(
            self.settings.get_nested("processing", "output_directory", default="exports")
        )
        fmt = self.settings.get_nested("processing", "export_format", default="jpeg")

        # Resolve default PP3 processing profile.
        pp3_str = self.settings.get_nested("processing", "default_pp3", default="")
        default_pp3 = Path(pp3_str) if pp3_str else None
        # Auto-detect bundled profile if none configured.
        if not default_pp3 or not default_pp3.is_file():
            bundled = Path(__file__).resolve().parent.parent.parent.parent / "config" / "default_profile.pp3"
            if bundled.is_file():
                default_pp3 = bundled

        self.export_service = ExportService(
            cli=self.cli,
            output_dir=output_dir,
            export_format=ExportFormat(fmt),
            jpeg_quality=int(
                self.settings.get_nested("processing", "jpeg_quality", default=95)
            ),
            default_pp3=default_pp3,
            max_file_size_kb=int(
                self.settings.get_nested("processing", "max_file_size_kb", default=0)
            ),
        )

        logger.info("AppController initialised.")

    # ------------------------------------------------------------------
    # Resume logic — "bullet-proof" crash recovery
    # ------------------------------------------------------------------
    def resume_pending_work(self) -> int:
        """Re-queue any photos stuck in transitional states.

        If the application crashed mid-pipeline, photos may be left in
        ``ANALYZING`` or ``PROCESSING``.  This method resets them to the
        previous safe state so the pipeline can retry.

        Returns:
            Number of photos that were reset.
        """
        session = self.db.get_session()
        try:
            stuck_statuses = [PhotoStatus.ANALYZING.value, PhotoStatus.PROCESSING.value]
            stuck = (
                session.query(Photo)
                .filter(Photo.status.in_(stuck_statuses))
                .all()
            )
            for photo in stuck:
                previous = (
                    PhotoStatus.PENDING.value
                    if photo.status == PhotoStatus.ANALYZING.value
                    else PhotoStatus.KEEP.value
                )
                logger.warning(
                    "Resetting stuck photo %s from %s → %s",
                    photo.file_name, photo.status, previous,
                )
                photo.status = previous
                photo.error_message = "Reset after crash recovery."
            session.commit()
            if stuck:
                logger.info("Resume: reset %d stuck photos.", len(stuck))
            return len(stuck)
        finally:
            session.close()

    def get_pipeline_summary(self) -> dict[str, int]:
        """Return a count of photos in each status.

        Returns:
            dict mapping status name → count.
        """
        session = self.db.get_session()
        try:
            counts: dict[str, int] = {}
            for status in PhotoStatus:
                count = (
                    session.query(Photo)
                    .filter(Photo.status == status.value)
                    .count()
                )
                counts[status.value] = count
            return counts
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """Gracefully shut down all subsystems."""
        logger.info("Shutting down…")
        self.task_queue.shutdown(wait=True)
        self.db.close()
        logger.info("=== Imagic stopped ===")
