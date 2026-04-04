"""In-app auto-updater: download the installer and launch it."""

from __future__ import annotations

import logging
import os
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional
from urllib import request

from PyQt6.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)


class _DownloadWorker(QObject):
    """Worker that downloads a file in a background QThread."""

    progress = pyqtSignal(int, int)  # bytes_downloaded, total_bytes
    finished = pyqtSignal(str)       # local file path
    error = pyqtSignal(str)          # error message

    def __init__(self, url: str, dest_path: str) -> None:
        super().__init__()
        self._url = url
        self._dest_path = dest_path

    def run(self) -> None:
        try:
            req = request.Request(self._url, method="GET")
            req.add_header("User-Agent", "imagic-updater/1.0")
            with request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 256 * 1024  # 256 KB
                with open(self._dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        self.progress.emit(downloaded, total)
            self.finished.emit(self._dest_path)
        except Exception as exc:
            # Clean up partial file
            try:
                os.remove(self._dest_path)
            except OSError:
                pass
            self.error.emit(str(exc))


class AutoUpdater(QObject):
    """Manages downloading and launching an installer update."""

    download_progress = pyqtSignal(int, int)  # bytes_downloaded, total_bytes
    download_finished = pyqtSignal(str)       # local file path
    download_error = pyqtSignal(str)          # error message

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_DownloadWorker] = None
        self._downloaded_path: Optional[str] = None

    def download(self, installer_url: str) -> None:
        """Start downloading the installer in the background."""
        if self._thread is not None:
            return  # already downloading

        # Determine file name from URL
        url_path = installer_url.split("?")[0]
        filename = url_path.rsplit("/", 1)[-1] or "imagic-update.exe"
        dest = os.path.join(tempfile.gettempdir(), filename)

        self._thread = QThread()
        self._worker = _DownloadWorker(installer_url, dest)
        self._worker.moveToThread(self._thread)

        self._worker.progress.connect(self.download_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def _on_finished(self, path: str) -> None:
        self._downloaded_path = path
        self._cleanup_thread()
        self.download_finished.emit(path)

    def _on_error(self, msg: str) -> None:
        self._cleanup_thread()
        self.download_error.emit(msg)

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None

    @staticmethod
    def launch_installer(path: str) -> None:
        """Launch the downloaded installer and exit the app."""
        logger.info("Launching installer: %s", path)
        if sys.platform == "win32":
            # Use ShellExecute so UAC prompt works correctly
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

        # Give the installer a moment to start, then quit
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.quit()
