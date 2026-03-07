"""Imagic — application entry point.

Bootstraps the application:
1. Parse CLI arguments.
2. Initialise settings, logging, database.
3. Create the ``AppController`` (which owns all sub-controllers).
4. Launch the PyQt6 event loop.
5. On exit, perform graceful shutdown.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed ``Namespace``.
    """
    parser = argparse.ArgumentParser(
        prog="imagic",
        description="Imagic — Professional Photo Editing Orchestrator",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=None,
        help="Path to a YAML configuration file.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless mode (no GUI). Useful for CLI-only batch processing.",
    )
    parser.add_argument(
        "--scan",
        type=Path,
        default=None,
        help="(Headless) Scan this directory on startup.",
    )
    parser.add_argument(
        "--analyse",
        action="store_true",
        help="(Headless) Run AI analysis after scanning.",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="(Headless) Export all KEEP photos after analysis.",
    )
    return parser.parse_args(argv)


def run_headless(args: argparse.Namespace) -> int:
    """Execute the pipeline without a GUI.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (0 = success).
    """
    from imagic.controllers.app_controller import AppController
    from imagic.controllers.ai_controller import AIController
    from imagic.controllers.library_controller import LibraryController
    from imagic.controllers.processing_controller import ProcessingController

    app = AppController(config_path=args.config)
    app.resume_pending_work()

    lib_ctrl = LibraryController(task_queue=app.task_queue)
    ai_ctrl = AIController(
        task_queue=app.task_queue,
        keep_threshold=float(app.settings.get_nested("ai", "keep_threshold", default=0.8)),
        trash_threshold=float(app.settings.get_nested("ai", "trash_threshold", default=0.3)),
    )
    proc_ctrl = ProcessingController(
        task_queue=app.task_queue,
        export_service=app.export_service,
    )

    if args.scan:
        logger.info("Headless: scanning %s", args.scan)
        task = lib_ctrl.scan_directory(args.scan)
        task.future.result()  # block until done

    if args.analyse:
        logger.info("Headless: running AI analysis…")
        task = ai_ctrl.analyse_pending()
        task.future.result()

    if args.export:
        logger.info("Headless: exporting kept photos…")
        task = proc_ctrl.export_all_kept()
        task.future.result()

    summary = app.get_pipeline_summary()
    logger.info("Pipeline summary: %s", summary)
    app.shutdown()
    return 0


def run_gui(args: argparse.Namespace) -> int:
    """Launch the full PyQt6 GUI.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code from the Qt event loop.
    """
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from imagic.controllers.app_controller import AppController
    from imagic.controllers.ai_controller import AIController
    from imagic.controllers.library_controller import LibraryController
    from imagic.controllers.processing_controller import ProcessingController
    from imagic.models.database import DatabaseManager
    from imagic.models.enums import PhotoStatus
    from imagic.models.photo import Photo
    from imagic.services.style_preview import pick_sample_photos
    from imagic.views.culling_preview import CullingPreviewDialog
    from imagic.views.main_window import MainWindow
    from imagic.views.style_chooser import StyleChooserDialog
    from imagic.views.widgets.image_viewer import ImageViewerDialog

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Imagic")

    # Bootstrap
    app = AppController(config_path=args.config)
    app.resume_pending_work()

    lib_ctrl = LibraryController(
        task_queue=app.task_queue,
        thumbnail_dir=Path(app.settings.get_nested("processing", "thumbnail_directory", default="thumbnails")),
    )
    ai_ctrl = AIController(
        task_queue=app.task_queue,
        keep_threshold=float(app.settings.get_nested("ai", "keep_threshold", default=0.8)),
        trash_threshold=float(app.settings.get_nested("ai", "trash_threshold", default=0.3)),
    )
    proc_ctrl = ProcessingController(
        task_queue=app.task_queue,
        export_service=app.export_service,
    )

    # Create main window
    window = MainWindow()

    # ------------------------------------------------------------------
    # Wire signals → controllers
    # ------------------------------------------------------------------
    def _on_import(directory: str, recursive: bool) -> None:
        lib_ctrl.scan_directory(directory)
        window.status_bar.set_status(f"Scanning {directory}…")

    def _on_analyse() -> None:
        ai_ctrl.analyse_pending()
        window.status_bar.set_status("AI analysis started…")

    def _on_export() -> None:
        proc_ctrl.export_all_kept()
        window.status_bar.set_status("Batch export started…")

    def _on_duplicates() -> None:
        ai_ctrl.detect_duplicates()
        window.status_bar.set_status("Duplicate scan started…")

    def _on_settings_changed(new_settings: dict) -> None:
        for section, values in new_settings.items():
            if isinstance(values, dict):
                for k, v in values.items():
                    app.settings.update(section, k, v)
        app.settings.save()
        # Apply live settings to export service.
        proc = new_settings.get("processing", {})
        if "max_file_size_kb" in proc:
            app.export_service.set_max_file_size(int(proc["max_file_size_kb"]))
        window.status_bar.set_status("Settings saved.")

    _last_photo_fingerprint = []  # mutable cache for change detection

    def _refresh_library() -> None:
        """Reload photo list from DB and push it to the view."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photos = session.query(Photo).order_by(Photo.created_at.desc()).limit(500).all()
            data = [
                {
                    "id": p.id,
                    "file_name": p.file_name,
                    "thumbnail_path": p.thumbnail_path,
                    "quality_score": p.quality_score,
                    "status": p.status,
                }
                for p in photos
            ]

            # Only rebuild the expensive grid if photo data actually changed.
            fingerprint = [
                (d["id"], d["thumbnail_path"], d["quality_score"], d["status"])
                for d in data
            ]
            if fingerprint != _last_photo_fingerprint:
                _last_photo_fingerprint.clear()
                _last_photo_fingerprint.extend(fingerprint)
                window.library_view.set_photos(data)

            counts = app.get_pipeline_summary()
            window.status_bar.set_pipeline_counts(counts)

            # Update processing view with task list.
            tasks = [
                {
                    "name": t.name,
                    "status": t.status.value,
                    "duration_s": (
                        (t.completed_at or 0) - (t.started_at or t.submitted_at)
                        if t.started_at else None
                    ),
                    "error": t.error or "",
                }
                for t in app.task_queue.all_tasks()
            ]
            window.processing_view.update_tasks(tasks)
        finally:
            session.close()

    def _on_generate_thumbnails() -> None:
        lib_ctrl.generate_thumbnails()
        window.status_bar.set_status("Generating thumbnails…")

    # ------------------------------------------------------------------
    # Style chooser handler
    # ------------------------------------------------------------------
    _preview_dir = Path.home() / ".imagic" / "previews"

    def _on_choose_style() -> None:
        """Open the style chooser dialog with sample photos."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photos = (
                session.query(Photo)
                .filter(Photo.thumbnail_path.isnot(None))
                .order_by(Photo.created_at.desc())
                .limit(200)
                .all()
            )
            records = [
                {
                    "id": p.id,
                    "file_name": p.file_name,
                    "file_path": p.file_path,
                    "thumbnail_path": p.thumbnail_path,
                }
                for p in photos
                if p.file_path and p.thumbnail_path
            ]
        finally:
            session.close()

        if not records:
            QMessageBox.warning(
                window,
                "No Photos",
                "Import and generate thumbnails for some photos first.",
            )
            return

        samples = pick_sample_photos(records, count=3)

        dialog = StyleChooserDialog(samples, _preview_dir, window)

        def _apply_style(preset: str) -> None:
            app.export_service.set_forced_preset(preset)
            window.status_bar.set_status(
                f"Edit style set to '{preset}'. Next export will use this style."
            )

        dialog.style_chosen.connect(_apply_style)
        dialog.exec()

    # ------------------------------------------------------------------
    # Culling preview handler
    # ------------------------------------------------------------------
    def _on_culling_preview() -> None:
        """Show the red/green culling preview with per-metric reasons."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photos = (
                session.query(Photo)
                .filter(Photo.quality_score.isnot(None))
                .order_by(Photo.quality_score.asc())
                .limit(600)
                .all()
            )
            data = [
                {
                    "file_name": p.file_name,
                    "file_path": p.file_path or "",
                    "thumbnail_path": p.thumbnail_path or "",
                    "quality_score": p.quality_score,
                    "status": p.status,
                    "cull_reasons": p.cull_reasons or "",
                }
                for p in photos
            ]
        finally:
            session.close()

        if not data:
            QMessageBox.information(
                window,
                "No Analysis Data",
                "Run AI Analysis first to generate culling data.",
            )
            return

        keep_t = float(app.settings.get_nested("ai", "keep_threshold", default=0.55))
        trash_t = float(app.settings.get_nested("ai", "trash_threshold", default=0.40))
        dialog = CullingPreviewDialog(data, keep_t, trash_t, window)
        dialog.status_changed.connect(_on_culling_status_changed)
        dialog.exec()

    def _on_culling_status_changed(file_name: str, new_status: str) -> None:
        """Persist a manual cull-status override to the database."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photo = session.query(Photo).filter(Photo.file_name == file_name).first()
            if photo:
                photo.status = new_status
                session.commit()
                logger.info("Manual cull override: %s → %s", file_name, new_status)
        except Exception:
            session.rollback()
            logger.exception("Failed to update cull status for %s", file_name)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Export gallery / image viewer handlers
    # ------------------------------------------------------------------
    _last_export_fingerprint: list = []

    def _on_clear_library() -> None:
        """Delete all photos from the database and clear the UI."""
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            window,
            "Clear Library",
            "This will remove all imported photos from the database.\n"
            "Original files on disk will NOT be deleted.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        db = DatabaseManager.get()
        session = db.get_session()
        try:
            count = session.query(Photo).delete()
            session.commit()
            logger.info("Cleared library: %d photos removed", count)
        except Exception:
            session.rollback()
            logger.exception("Failed to clear library")
        finally:
            session.close()

        _last_photo_fingerprint.clear()
        _last_export_fingerprint.clear()
        window.library_view.clear()
        window.export_gallery.clear()
        _refresh_library()
        _refresh_exports()
        window.status_bar.set_status(f"Library cleared ({count} photos removed)")

    def _on_open_export_folder() -> None:
        """Open the export output directory in the system file manager."""
        import os
        import subprocess

        export_dir = Path(
            app.settings.get_nested("processing", "output_directory", default="exports")
        )
        if not export_dir.is_absolute():
            export_dir = Path.home() / ".imagic" / export_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(export_dir)])

    def _refresh_exports() -> None:
        """Populate the export gallery tab with exported photos."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            exported = (
                session.query(Photo)
                .filter(Photo.status == PhotoStatus.EXPORTED.value)
                .filter(Photo.export_path.isnot(None))
                .order_by(Photo.created_at.desc())
                .limit(500)
                .all()
            )
            data = [
                {
                    "id": p.id,
                    "file_name": p.file_name,
                    "export_path": p.export_path,
                    "thumbnail_path": p.thumbnail_path or "",
                    "file_path": p.file_path,
                    "status": "exported",
                }
                for p in exported
            ]
            fp = [(d["id"], d["export_path"]) for d in data]
            if fp != _last_export_fingerprint:
                _last_export_fingerprint.clear()
                _last_export_fingerprint.extend(fp)
                window.export_gallery.set_exports(data)
        finally:
            session.close()

    def _on_export_tile_clicked(photo_id: int, export_path: str, thumb_path: str) -> None:
        """Open the before/after image viewer for a clicked export tile."""
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            exported = (
                session.query(Photo)
                .filter(Photo.status == PhotoStatus.EXPORTED.value)
                .filter(Photo.export_path.isnot(None))
                .order_by(Photo.created_at.desc())
                .limit(500)
                .all()
            )
            photo_list = [
                {
                    "id": p.id,
                    "file_name": p.file_name,
                    "export_path": p.export_path or "",
                    "thumbnail_path": p.thumbnail_path or "",
                    "file_path": p.file_path,
                    "manual_overrides": p.manual_overrides or "",
                }
                for p in exported
            ]
        finally:
            session.close()

        # Find the index of the clicked photo.
        current_idx = 0
        for i, p in enumerate(photo_list):
            if p["id"] == photo_id:
                current_idx = i
                break

        if photo_list:
            viewer = ImageViewerDialog(photo_list, current_idx, window)

            def _handle_reedit(pid: int, overrides: dict) -> None:
                """Apply manual overrides and re-export the photo."""
                db2 = DatabaseManager.get()
                s2 = db2.get_session()
                try:
                    photo_obj = s2.get(Photo, pid)
                    if photo_obj is None:
                        viewer.on_reedit_finished(False)
                        return

                    # Persist overrides
                    if overrides.get("color_grade"):
                        photo_obj.color_grade = overrides["color_grade"]
                    photo_obj.manual_overrides = json.dumps(overrides)

                    # Delete old export so RT writes fresh
                    old_export = Path(photo_obj.export_path) if photo_obj.export_path else None
                    if old_export and old_export.is_file():
                        old_export.unlink()

                    photo_obj.status = PhotoStatus.ANALYSED.value
                    s2.commit()
                finally:
                    s2.close()

                # Re-export
                result = app.export_service.export_photo(pid)

                # Read new path
                s3 = db2.get_session()
                try:
                    refreshed = s3.get(Photo, pid)
                    new_path = refreshed.export_path or "" if refreshed else ""
                finally:
                    s3.close()

                viewer.on_reedit_finished(result.success, new_path)
                _refresh_exports()

            viewer.reedit_requested.connect(_handle_reedit)
            viewer.exec()

    window.import_requested.connect(_on_import)
    window.analyse_requested.connect(_on_analyse)
    window.export_requested.connect(_on_export)
    window.duplicate_scan_requested.connect(_on_duplicates)
    window.generate_thumbnails_requested.connect(_on_generate_thumbnails)
    window.choose_style_requested.connect(_on_choose_style)
    window.culling_preview_requested.connect(_on_culling_preview)
    window.export_gallery.photo_clicked.connect(_on_export_tile_clicked)
    window.settings_changed.connect(_on_settings_changed)
    window.refresh_requested.connect(_refresh_library)
    window.refresh_requested.connect(_refresh_exports)
    window.clear_library_requested.connect(_on_clear_library)
    window.open_export_folder_requested.connect(_on_open_export_folder)

    # Override settings dialog to inject current config.
    original_show_settings = window._show_settings

    def _settings_with_data() -> None:
        window.show_settings_dialog(app.settings.data)

    window._show_settings = _settings_with_data  # type: ignore[assignment]

    window.show()
    _refresh_library()   # initial load
    _refresh_exports()   # populate export gallery

    exit_code = qt_app.exec()
    app.shutdown()
    return exit_code


def main() -> None:
    """Application entry point."""
    args = parse_args()

    if args.headless:
        sys.exit(run_headless(args))
    else:
        sys.exit(run_gui(args))


if __name__ == "__main__":
    main()
