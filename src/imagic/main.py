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


def _resolve_desktop_icon_path() -> Path | None:
    candidate = Path(__file__).resolve().parents[2] / "assets" / "icons" / "imagic-app-icon.svg"
    return candidate if candidate.is_file() else None


def _ensure_desktop_activation(qt_app, app_controller) -> bool:
    from PyQt6.QtWidgets import QMessageBox

    from imagic.services.license_client import DesktopLicenseClient, LicenseClientError
    from imagic.views.activation_dialog import ActivationDialog

    require_activation = bool(
        app_controller.settings.get_nested("security", "require_activation", default=False)
    )
    if not require_activation:
        return True

    base_url = str(
        app_controller.settings.get_nested("security", "license_api_base_url", default="")
    ).strip()
    client = DesktopLicenseClient(base_url)
    if not client.enabled:
        QMessageBox.critical(
            None,
            "Activation Unavailable",
            "Desktop activation is enabled, but no license API URL is configured.",
        )
        return False

    saved_token = str(
        app_controller.settings.get_nested("security", "activation_token", default="")
    ).strip()
    if saved_token:
        try:
            client.validate(saved_token)
            return True
        except LicenseClientError:
            logger.warning("Stored activation token is no longer valid.")

    license_key = str(app_controller.settings.get_nested("security", "license_key", default="") or "")
    dialog = ActivationDialog(license_key=license_key)

    while dialog.exec() == dialog.DialogCode.Accepted:
        license_key = dialog.credentials()
        dialog.set_busy(True, "Contacting license server…")
        qt_app.processEvents()
        try:
            result = client.activate(license_key)
        except LicenseClientError as exc:
            dialog.set_busy(False, str(exc))
            continue

        app_controller.settings.update("security", "license_email", result.get("email", ""))
        app_controller.settings.update("security", "license_key", result.get("license_key", license_key))
        app_controller.settings.update("security", "activation_token", result.get("activation_token", ""))
        app_controller.settings.save()
        return True

    return False


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
    if not _ensure_headless_activation(app):
        app.shutdown()
        return 1
    app.resume_pending_work()

    lib_ctrl = LibraryController(task_queue=app.task_queue)
    ai_ctrl = AIController(
        task_queue=app.task_queue,
        keep_threshold=float(app.settings.get_nested("ai", "keep_threshold", default=0.8)),
        trash_threshold=float(app.settings.get_nested("ai", "trash_threshold", default=0.3)),
        duplicate_hash_threshold=int(app.settings.get_nested("ai", "duplicate_hash_threshold", default=10)),
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
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QIcon
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
    from imagic.views.photo_editor import PhotoEditorWidget
    from imagic.views.style_chooser import StyleChooserDialog
    from imagic.views.widgets.ai_loading_modal import AILoadingModal
    from imagic.views.widgets.speech_bubble import (
        SpeechBubble,
        PointerSide,
        TutorialOverlay,
        TutorialStep,
    )

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Imagic")
    icon_path = _resolve_desktop_icon_path()
    if icon_path is not None:
        qt_app.setWindowIcon(QIcon(str(icon_path)))

    # Bootstrap
    app = AppController(config_path=args.config)
    if not _ensure_desktop_activation(qt_app, app):
        app.shutdown()
        return 1
    app.resume_pending_work()

    lib_ctrl = LibraryController(
        task_queue=app.task_queue,
        thumbnail_dir=Path(app.settings.get_nested("processing", "thumbnail_directory", default="thumbnails")),
    )
    ai_ctrl = AIController(
        task_queue=app.task_queue,
        keep_threshold=float(app.settings.get_nested("ai", "keep_threshold", default=0.8)),
        trash_threshold=float(app.settings.get_nested("ai", "trash_threshold", default=0.3)),
        duplicate_hash_threshold=int(app.settings.get_nested("ai", "duplicate_hash_threshold", default=10)),
    )
    proc_ctrl = ProcessingController(
        task_queue=app.task_queue,
        export_service=app.export_service,
    )

    # Create main window
    window = MainWindow()
    if icon_path is not None:
        window.setWindowIcon(QIcon(str(icon_path)))

    # AI loading overlay on the main window
    _main_ai_modal = AILoadingModal(parent=window)

    # Speech bubble for post-analysis tips
    _tip_bubble = SpeechBubble(parent=window.centralWidget())
    _first_analysis_done = False  # only show the tip once per session

    # ------------------------------------------------------------------
    # Wire signals → controllers
    # ------------------------------------------------------------------
    def _on_import(directory: str, recursive: bool) -> None:
        lib_ctrl.scan_directory(directory)
        window.status_bar.set_status(f"Scanning {directory}…")
        window.set_sidebar_status(f"Scanning…")

    _analyse_poll_timer: Optional[QTimer] = None  # type: ignore[assignment]
    _analyse_task = None
    _reanalyse_count = 0  # tracks how many times re-analyse has been pressed

    def _on_analyse() -> None:
        nonlocal _analyse_poll_timer, _analyse_task
        _analyse_task = ai_ctrl.analyse_pending()
        window.status_bar.set_status("AI analysis started…")
        window.set_sidebar_status("Analysing…")
        window.set_step_status(1, "AI analysis in progress…")
        _main_ai_modal.show_message("AI Analysis", "Scoring and classifying photos…")

        if _analyse_poll_timer is None:
            _analyse_poll_timer = QTimer()
            _analyse_poll_timer.setInterval(300)
            _analyse_poll_timer.timeout.connect(_on_analyse_poll)
        _analyse_poll_timer.start()

    def _on_analyse_poll() -> None:
        nonlocal _analyse_poll_timer, _analyse_task
        if _analyse_task is None or _analyse_task.future is None:
            if _analyse_poll_timer:
                _analyse_poll_timer.stop()
            _main_ai_modal.hide_modal()
            return
        if not _analyse_task.future.done():
            return  # still running
        _analyse_poll_timer.stop()
        _main_ai_modal.hide_modal()
        result = _analyse_task.result
        _analyse_task = None
        # Update status bar with results
        if isinstance(result, dict):
            kept = result.get("keep", 0)
            trashed = result.get("trash", 0)
            errors = result.get("errors", 0)
            total = result.get("analysed", 0)
            strict_note = ""
            if _reanalyse_count > 0:
                strict_note = f" (strictness level {_reanalyse_count})"
            window.status_bar.set_status(
                f"Analysis complete — {total} photos: {kept} kept, {trashed} trashed, {errors} errors{strict_note}"
            )
            window.set_step_status(1, f"Done — {total} analysed")
        else:
            window.status_bar.set_status("AI analysis complete.")
        window.set_sidebar_status("")
        _refresh_library()

        # Show tip bubble after first analysis completes
        nonlocal _first_analysis_done
        if not _first_analysis_done and _reanalyse_count == 0:
            _first_analysis_done = True
            # Delay slightly so the UI settles before positioning the bubble
            QTimer.singleShot(600, _show_reanalyse_tip)

    def _show_reanalyse_tip() -> None:
        """Show a speech bubble near the Re-Analyse button."""
        if not hasattr(window, '_reanalyse_btn') or not window._reanalyse_btn.isVisible():
            return
        _tip_bubble.show_at(
            window._reanalyse_btn,
            title="💡 Not happy with the results?",
            body="Press <b>Re-Analyse All</b> to run the AI again with stricter thresholds. "
                 "Each press makes it pickier!",
            pointer=PointerSide.TOP,
            pointer_offset=0.5,
            button_text="Got it!",
            auto_hide_ms=12000,
        )

    def _on_reanalyse_all() -> None:
        """Reset all photos to PENDING and re-run AI analysis.

        Each subsequent press increases strictness — the AI becomes
        pickier about which photos to keep.
        """
        nonlocal _analyse_poll_timer, _analyse_task, _reanalyse_count
        _reanalyse_count += 1
        strictness = _reanalyse_count * 0.05  # +5% per re-run
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            count = (
                session.query(Photo)
                .filter(Photo.status.notin_([
                    PhotoStatus.PENDING.value,
                    PhotoStatus.ANALYZING.value,
                ]))
                .update(
                    {Photo.status: PhotoStatus.PENDING.value},
                    synchronize_session="fetch",
                )
            )
            session.commit()
            logger.info("Reset %d photos to PENDING for re-analysis (strictness=%.2f)", count, strictness)
        except Exception:
            session.rollback()
            logger.exception("Failed to reset photo statuses")
        finally:
            session.close()

        _analyse_task = ai_ctrl.analyse_pending(strictness=strictness)
        strict_pct = int(strictness * 100)
        window.status_bar.set_status(
            f"Re-analysing {count} photos (strictness +{strict_pct}%)\u2026"
        )
        level_label = f"Stricter (level {_reanalyse_count})" if _reanalyse_count > 0 else "Re-Analysing"
        _main_ai_modal.show_message(
            f"Re-Analysing All — {level_label}",
            f"Scoring {count} photos with tighter thresholds\u2026",
        )
        if _analyse_poll_timer is None:
            _analyse_poll_timer = QTimer()
            _analyse_poll_timer.setInterval(300)
            _analyse_poll_timer.timeout.connect(_on_analyse_poll)
        _analyse_poll_timer.start()
    def _on_export() -> None:
        proc_ctrl.export_all_kept()
        window.status_bar.set_status("Batch export started…")

    _dup_poll_timer: Optional[QTimer] = None  # type: ignore[assignment]
    _dup_task = None

    def _on_duplicates() -> None:
        """Run duplicate scan and open the duplicate cleaner dialog."""
        nonlocal _dup_poll_timer, _dup_task
        from imagic.views.duplicate_cleaner import DuplicateCleanerDialog

        window.status_bar.set_status("Scanning for duplicates…")
        _dup_task = ai_ctrl.detect_duplicates()
        _main_ai_modal.show_message("Duplicate Detection", "Computing perceptual hashes…")

        # Poll every 200ms until the task completes
        if _dup_poll_timer is None:
            _dup_poll_timer = QTimer()
            _dup_poll_timer.setInterval(200)
            _dup_poll_timer.timeout.connect(_on_dup_poll)
        _dup_poll_timer.start()

    def _on_dup_poll() -> None:
        """Check if the duplicate scan task has finished."""
        nonlocal _dup_poll_timer, _dup_task
        from imagic.views.duplicate_cleaner import DuplicateCleanerDialog

        if _dup_task is None or _dup_task.future is None:
            if _dup_poll_timer:
                _dup_poll_timer.stop()
            _main_ai_modal.hide_modal()
            return

        if not _dup_task.future.done():
            return  # still running, check again next tick

        _dup_poll_timer.stop()
        _main_ai_modal.hide_modal()
        result = _dup_task.result
        _dup_task = None

        groups_raw = []
        if isinstance(result, dict):
            groups_raw = result.get("groups", [])

        if not groups_raw:
            QMessageBox.information(
                window,
                "No Duplicates",
                "No duplicate or near-duplicate photos found.",
            )
            window.status_bar.set_status("No duplicates found.")
            return

        # Enrich groups with DB data
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            enriched_groups: list[list[dict]] = []
            for group_paths in groups_raw:
                photos_in_group: list[dict] = []
                for fpath in group_paths:
                    photo = (
                        session.query(Photo)
                        .filter(Photo.file_path == fpath)
                        .first()
                    )
                    if not photo:
                        continue
                    metric_scores: dict = {}
                    if photo.cull_reasons:
                        try:
                            reasons = json.loads(photo.cull_reasons)
                            for r in reasons:
                                m = r.get("metric", "").lower()
                                s = r.get("score")
                                if m and s is not None:
                                    metric_scores[m] = s
                        except (json.JSONDecodeError, TypeError):
                            pass
                    photos_in_group.append({
                        "file_name": photo.file_name,
                        "file_path": photo.file_path,
                        "thumbnail_path": photo.thumbnail_path or "",
                        "quality_score": photo.quality_score or 0.0,
                        "metric_scores": metric_scores,
                        "exif_iso": photo.exif_iso,
                        "exif_date_taken": str(photo.exif_date_taken) if photo.exif_date_taken else "",
                        "status": photo.status,
                    })
                if len(photos_in_group) >= 2:
                    # Sort photos within group by timestamp
                    photos_in_group.sort(key=lambda p: p["exif_date_taken"] or "")
                    enriched_groups.append(photos_in_group)
            # Sort groups by earliest timestamp
            enriched_groups.sort(
                key=lambda g: min((p["exif_date_taken"] for p in g if p["exif_date_taken"]), default="")
            )
        finally:
            session.close()

        if not enriched_groups:
            QMessageBox.information(
                window,
                "No Duplicates",
                "No duplicate groups with enough data found.",
            )
            window.status_bar.set_status("No duplicates found.")
            return

        window.status_bar.set_status(
            f"Found {len(enriched_groups)} duplicate groups — opening cleaner…"
        )

        dialog = DuplicateCleanerDialog(enriched_groups, window)

        def _apply_trash(trash_fnames: list) -> None:
            db2 = DatabaseManager.get()
            s2 = db2.get_session()
            try:
                trashed = 0
                for fname in trash_fnames:
                    photo = s2.query(Photo).filter(Photo.file_name == fname).first()
                    if photo:
                        photo.status = PhotoStatus.TRASH.value
                        trashed += 1
                s2.commit()
                window.status_bar.set_status(
                    f"Duplicate cleaning done — trashed {trashed} photos."
                )
                _refresh_library()
            except Exception:
                s2.rollback()
                logger.exception("Failed to apply duplicate cleaning")
            finally:
                s2.close()

        dialog.decisions_made.connect(_apply_trash)
        dialog.exec()

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
                    "cull_reasons": p.cull_reasons,
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
                # Also update the review page with enriched data
                window.set_review_photos(data)

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
        from imagic.services.editor_style_presets import (
            LEGACY_STYLE_PRESETS,
            get_editor_style_overrides,
            merge_editor_overrides,
        )
        from imagic.services.pp3_generator import GRADES

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
            s2 = db.get_session()
            updated = 0
            try:
                targets = (
                    s2.query(Photo)
                    .filter(Photo.status != PhotoStatus.TRASH.value)
                    .order_by(Photo.created_at.desc())
                    .limit(500)
                    .all()
                )
                for photo in targets:
                    raw = photo.manual_overrides or ""
                    try:
                        overrides = json.loads(raw) if raw else {}
                    except (json.JSONDecodeError, TypeError):
                        overrides = {}

                    if preset in LEGACY_STYLE_PRESETS:
                        photo.scene_preset = preset
                        overrides = merge_editor_overrides(
                            overrides,
                            get_editor_style_overrides(preset),
                        )
                    elif preset in GRADES:
                        photo.color_grade = preset
                        overrides["color_grade"] = preset
                        overrides.setdefault("color_grade_intensity", 100)
                    else:
                        continue

                    photo.manual_overrides = json.dumps(overrides)
                    updated += 1
                s2.commit()
            finally:
                s2.close()

            app.export_service.set_forced_preset(None)
            _refresh_library()
            window.status_bar.set_status(
                f"Applied '{preset}' to {updated} photos. Editor and export will use the same edit stack."
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
        """Persist a manual cull-status override and record feedback."""
        from imagic.ai.feedback_learner import get_learner

        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photo = session.query(Photo).filter(Photo.file_name == file_name).first()
            if photo:
                old_status = photo.status
                photo.status = new_status
                session.commit()
                logger.info("Manual cull override: %s → %s", file_name, new_status)

                # Record feedback for the learner
                metric_scores = {}
                if photo.cull_reasons:
                    try:
                        reasons = json.loads(photo.cull_reasons)
                        for r in reasons:
                            m = r.get("metric", "").lower()
                            s = r.get("score")
                            if s is not None:
                                metric_scores[m] = s
                    except (json.JSONDecodeError, TypeError):
                        pass

                learner = get_learner()
                learner.record_cull_feedback(
                    file_name=file_name,
                    auto_decision=old_status,
                    user_decision=new_status,
                    quality_score=photo.quality_score or 0.0,
                    metric_scores=metric_scores,
                    iso=photo.exif_iso,
                    mean_brightness=128.0,
                )
        except Exception:
            session.rollback()
            logger.exception("Failed to update cull status for %s", file_name)
        finally:
            session.close()

    def _on_review_status_changed(photo_id: int, new_status: str) -> None:
        """Persist a keep/trash decision from the review grid."""
        from imagic.ai.feedback_learner import get_learner

        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photo = session.get(Photo, photo_id)
            if photo:
                old_status = photo.status
                photo.status = new_status
                session.commit()
                logger.info("Review override: photo %d → %s", photo_id, new_status)

                metric_scores = {}
                if photo.cull_reasons:
                    try:
                        reasons = json.loads(photo.cull_reasons)
                        for r in reasons:
                            m = r.get("metric", "").lower()
                            s = r.get("score")
                            if s is not None:
                                metric_scores[m] = s
                    except (json.JSONDecodeError, TypeError):
                        pass

                learner = get_learner()
                learner.record_cull_feedback(
                    file_name=photo.file_name,
                    auto_decision=old_status,
                    user_decision=new_status,
                    quality_score=photo.quality_score or 0.0,
                    metric_scores=metric_scores,
                    iso=photo.exif_iso,
                    mean_brightness=128.0,
                )
        except Exception:
            session.rollback()
            logger.exception("Failed to update review status for photo %d", photo_id)
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

        count = 0
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            count = session.query(Photo).delete(synchronize_session=False)
            session.commit()
            logger.info("Cleared library: %d photos removed", count)
        except Exception:
            session.rollback()
            logger.exception("Failed to clear library")
            window.status_bar.set_status("Error: failed to clear library")
            return
        finally:
            session.close()

        _last_photo_fingerprint.clear()
        _last_export_fingerprint.clear()
        window.library_view.clear()
        window.export_gallery.clear()
        window.set_review_photos([])
        _refresh_library()
        _refresh_exports()
        window.status_bar.set_status(f"Library cleared ({count} photos removed)")

    def _on_open_export_folder() -> None:
        """Open the export output directory in the system file manager."""
        from imagic.utils.path_utils import open_file_manager

        export_dir = Path(
            app.settings.get_nested("processing", "output_directory", default="exports")
        )
        if not export_dir.is_absolute():
            export_dir = Path.home() / ".imagic" / export_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        open_file_manager(export_dir)

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
            fp = []
            for d in data:
                export_path = d["export_path"]
                try:
                    stat = Path(export_path).stat() if export_path else None
                except OSError:
                    stat = None
                fp.append(
                    (
                        d["id"],
                        export_path,
                        stat.st_mtime_ns if stat else None,
                        stat.st_size if stat else None,
                    )
                )
            if fp != _last_export_fingerprint:
                _last_export_fingerprint.clear()
                _last_export_fingerprint.extend(fp)
                window.export_gallery.set_exports(data)
        finally:
            session.close()

    def _on_export_tile_clicked(photo_id: int, export_path: str, thumb_path: str) -> None:
        """Open the embedded photo editor for the clicked export tile."""
        _open_photo_editor(photo_id, source="exports")

    # ------------------------------------------------------------------
    # Photo Editor (Lightroom-style)
    # ------------------------------------------------------------------

    def _open_photo_editor(photo_id: int, source: str = "library") -> None:
        """Load photos into the embedded editor and switch to the Edit step."""
        from imagic.ai.feedback_learner import get_learner

        db = DatabaseManager.get()
        session = db.get_session()
        try:
            if source == "exports":
                photos = (
                    session.query(Photo)
                    .filter(Photo.status == PhotoStatus.EXPORTED.value)
                    .filter(Photo.export_path.isnot(None))
                    .order_by(Photo.created_at.desc())
                    .limit(500)
                    .all()
                )
            else:
                photos = (
                    session.query(Photo)
                    .filter(Photo.thumbnail_path.isnot(None))
                    .order_by(Photo.created_at.desc())
                    .limit(500)
                    .all()
                )
            photo_list = [
                {
                    "id": p.id,
                    "file_name": p.file_name,
                    "file_path": p.file_path,
                    "export_path": p.export_path or "",
                    "thumbnail_path": p.thumbnail_path or "",
                    "manual_overrides": p.manual_overrides or "",
                    "scene_preset": p.scene_preset or "",
                    "quality_score": p.quality_score,
                    "exif_iso": p.exif_iso,
                    "exif_aperture": p.exif_aperture,
                    "exif_shutter_speed": p.exif_shutter_speed,
                    "color_grade": p.color_grade or "natural",
                }
                for p in photos
            ]
        finally:
            session.close()

        current_idx = 0
        for i, p in enumerate(photo_list):
            if p["id"] == photo_id:
                current_idx = i
                break

        if not photo_list:
            return

        # Load into embedded editor and switch to Edit step
        window.set_editor_photos(photo_list, current_idx)
        window.go_to_step(3)

    def _on_edit_step_entered() -> None:
        """Load kept photos into the editor when entering the Edit step."""
        # Skip if the editor already has photos (e.g. from double-click)
        if window.photo_editor._photos:
            return
        db = DatabaseManager.get()
        session = db.get_session()
        try:
            photos = (
                session.query(Photo)
                .filter(Photo.status.in_([
                    PhotoStatus.KEEP.value,
                    PhotoStatus.EXPORTED.value,
                ]))
                .filter(Photo.thumbnail_path.isnot(None))
                .order_by(Photo.created_at.desc())
                .limit(500)
                .all()
            )
            if not photos:
                # Fallback: any analysed photo with a thumbnail
                photos = (
                    session.query(Photo)
                    .filter(Photo.thumbnail_path.isnot(None))
                    .filter(Photo.status.notin_([PhotoStatus.TRASH.value]))
                    .order_by(Photo.created_at.desc())
                    .limit(500)
                    .all()
                )
            photo_list = [
                {
                    "id": p.id,
                    "file_name": p.file_name,
                    "file_path": p.file_path,
                    "export_path": p.export_path or "",
                    "thumbnail_path": p.thumbnail_path or "",
                    "manual_overrides": p.manual_overrides or "",
                    "scene_preset": p.scene_preset or "",
                    "quality_score": p.quality_score,
                    "exif_iso": p.exif_iso,
                    "exif_aperture": p.exif_aperture,
                    "exif_shutter_speed": p.exif_shutter_speed,
                    "color_grade": p.color_grade or "natural",
                }
                for p in photos
            ]
        finally:
            session.close()
        if photo_list:
            window.set_editor_photos(photo_list, 0)

    def _on_library_double_click(photo_id: int) -> None:
        _open_photo_editor(photo_id, source="library")

    def _on_export_double_click(photo_id: int, export_path: str, thumb_path: str) -> None:
        _open_photo_editor(photo_id, source="exports")

    def _handle_editor_apply(pid: int, overrides: dict) -> None:
        """Persist overrides from the embedded editor, re-export, and record feedback."""
        from imagic.ai.feedback_learner import get_learner

        learner = get_learner()
        db2 = DatabaseManager.get()
        s2 = db2.get_session()
        try:
            photo_obj = s2.get(Photo, pid)
            if photo_obj is None:
                window.photo_editor.on_export_finished(False)
                return

            learner.record_edit_feedback(
                file_name=photo_obj.file_name,
                overrides=overrides,
                iso=photo_obj.exif_iso,
                mean_brightness=128.0,
                color_grade=overrides.get("color_grade", photo_obj.color_grade or "natural"),
            )

            if overrides.get("color_grade"):
                photo_obj.color_grade = overrides["color_grade"]
            photo_obj.manual_overrides = json.dumps(overrides)

            old_export = Path(photo_obj.export_path) if photo_obj.export_path else None
            if old_export and old_export.is_file():
                old_export.unlink()

            photo_obj.status = PhotoStatus.KEEP.value
            s2.commit()
        finally:
            s2.close()

        result = app.export_service.export_photo(pid)

        s3 = db2.get_session()
        try:
            refreshed = s3.get(Photo, pid)
            new_path = refreshed.export_path or "" if refreshed else ""
        finally:
            s3.close()

        window.photo_editor.on_export_finished(result.success, new_path)
        _refresh_exports()

    window.import_requested.connect(_on_import)
    window.analyse_requested.connect(_on_analyse)
    window.export_requested.connect(_on_export)

    def _on_reexport_broken() -> None:
        """Re-export photos that had auto-crop (bad scaling bug) or errors."""
        db_re = DatabaseManager.get()
        s_re = db_re.get_session()
        try:
            broken = (
                s_re.query(Photo)
                .filter(
                    (Photo.auto_crop_data.isnot(None) & (Photo.auto_crop_data != ""))
                    | (Photo.status == PhotoStatus.ERROR.value)
                )
                .all()
            )
            ids = [p.id for p in broken]
        finally:
            s_re.close()

        if not ids:
            window.status_bar.set_status("No broken exports found.")
            return

        proc_ctrl.export_by_ids(ids)
        window.status_bar.set_status(f"Re-exporting {len(ids)} photos\u2026")

    window.reexport_broken_requested.connect(_on_reexport_broken)
    window.duplicate_scan_requested.connect(_on_duplicates)
    window.generate_thumbnails_requested.connect(_on_generate_thumbnails)
    window.choose_style_requested.connect(_on_choose_style)
    window.culling_preview_requested.connect(_on_culling_preview)
    window.review_status_changed.connect(_on_review_status_changed)
    window.export_gallery.photo_clicked.connect(_on_export_tile_clicked)
    window.export_gallery.photo_double_clicked.connect(_on_export_double_click)
    window.edit_step_entered.connect(_on_edit_step_entered)
    window.edit_photo_requested.connect(_on_library_double_click)
    window.photo_editor.edit_applied.connect(_handle_editor_apply)

    def _handle_photo_trash(photo_id: int) -> None:
        """Mark a photo as trashed in the database."""
        db3 = DatabaseManager.get()
        s3 = db3.get_session()
        try:
            photo_obj = s3.get(Photo, photo_id)
            if photo_obj:
                photo_obj.status = PhotoStatus.TRASH.value
                s3.commit()
                logger.info("Trashed photo %d from editor", photo_id)
        except Exception:
            s3.rollback()
        finally:
            s3.close()

    window.photo_editor.photo_trashed.connect(_handle_photo_trash)

    def _handle_edits_saved(batch: list) -> None:
        """Persist all edits to the database without exporting."""
        db4 = DatabaseManager.get()
        s4 = db4.get_session()
        saved = 0
        try:
            for photo_id, overrides in batch:
                photo_obj = s4.get(Photo, photo_id)
                if photo_obj:
                    photo_obj.manual_overrides = json.dumps(overrides)
                    saved += 1
            s4.commit()
            logger.info("Saved edits for %d photos", saved)
        except Exception:
            s4.rollback()
            logger.exception("Failed to save edits")
        finally:
            s4.close()
        window.photo_editor.on_edits_saved(saved)

    window.photo_editor.edits_saved.connect(_handle_edits_saved)
    window.settings_changed.connect(_on_settings_changed)
    window.refresh_requested.connect(_refresh_library)
    window.refresh_requested.connect(_refresh_exports)
    window.clear_library_requested.connect(_on_clear_library)
    window.open_export_folder_requested.connect(_on_open_export_folder)
    window.reanalyse_all_requested.connect(_on_reanalyse_all)

    # ------------------------------------------------------------------
    # Tutorial overlay
    # ------------------------------------------------------------------
    _tutorial_steps = [
        TutorialStep(
            title="👋 Welcome to Imagic!",
            body="This quick tour will walk you through the five-step workflow. "
                 "You can restart it any time from <b>Help → Start Tutorial</b>.",
            target_name="_step_buttons.0",
            pointer=PointerSide.LEFT,
            pointer_offset=0.3,
        ),
        TutorialStep(
            title="Step 1 — Import",
            body="Start here! Click <b>Browse</b> to select a folder of RAW or JPEG "
                 "photos. Imagic will scan and catalogue every image.",
            target_name="_import_btn",
            pointer=PointerSide.BOTTOM,
            pointer_offset=0.5,
        ),
        TutorialStep(
            title="Step 2 — AI Analysis",
            body="The AI scores every photo for quality, sharpness, and exposure. "
                 "It auto-classifies them as <b>Keep</b>, <b>Review</b>, or <b>Trash</b>.",
            target_name="_analyse_btn",
            pointer=PointerSide.BOTTOM,
            pointer_offset=0.5,
        ),
        TutorialStep(
            title="Re-Analyse for Stricter Results",
            body="Not happy? Press <b>Re-Analyse All</b> — each press makes the AI "
                 "pickier, raising the bar for \"keep\".",
            target_name="_reanalyse_btn",
            pointer=PointerSide.BOTTOM,
            pointer_offset=0.5,
        ),
        TutorialStep(
            title="Step 3 — Review & Cull",
            body="Flip through photos and confirm the AI's suggestions. "
                 "Mark keepers with ✓ or trash with ✗. Fast and visual.",
            target_name="_review_grid",
            pointer=PointerSide.LEFT,
            pointer_offset=0.3,
        ),
        TutorialStep(
            title="Step 4 — Edit",
            body="Double-click any photo to open the full editor. "
                 "Adjust exposure, colour, sharpness and more — or let the AI "
                 "<b>Auto-Enhance</b> for you.",
            target_name="photo_editor",
            pointer=PointerSide.LEFT,
            pointer_offset=0.3,
        ),
        TutorialStep(
            title="Step 5 — Export",
            body="When you're done, export all kept photos as high-quality JPEGs "
                 "ready to share. That's it — enjoy!",
            target_name="export_gallery",
            pointer=PointerSide.LEFT,
            pointer_offset=0.3,
        ),
    ]

    _tutorial_overlay: Optional[TutorialOverlay] = None

    def _on_start_tutorial() -> None:
        nonlocal _tutorial_overlay
        # Dismiss any tip bubble
        _tip_bubble.dismiss()
        # Go to the first step so sidebar is visible
        window.go_to_step(0)
        _tutorial_overlay = TutorialOverlay(
            _tutorial_steps,
            host=window.centralWidget(),
            target_root=window,
        )
        _tutorial_overlay.finished.connect(lambda: window.status_bar.set_status("Tutorial finished — happy editing!"))
        _tutorial_overlay.start()

    window.tutorial_requested.connect(_on_start_tutorial)

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


def _ensure_headless_activation(app_controller) -> bool:
    from imagic.services.license_client import DesktopLicenseClient, LicenseClientError

    require_activation = bool(
        app_controller.settings.get_nested("security", "require_activation", default=False)
    )
    if not require_activation:
        return True
    base_url = str(
        app_controller.settings.get_nested("security", "license_api_base_url", default="")
    ).strip()
    token = str(
        app_controller.settings.get_nested("security", "activation_token", default="")
    ).strip()
    if not base_url or not token:
        logger.error("Headless mode requires a pre-activated desktop license.")
        return False
    client = DesktopLicenseClient(base_url)
    try:
        client.validate(token)
        return True
    except LicenseClientError as exc:
        logger.error("Desktop activation validation failed: %s", exc)
        return False


def main() -> None:
    """Application entry point."""
    args = parse_args()

    if args.headless:
        sys.exit(run_headless(args))
    else:
        sys.exit(run_gui(args))


if __name__ == "__main__":
    main()
