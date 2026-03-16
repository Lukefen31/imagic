"""Cross-platform path utilities built on ``pathlib``.

Every file-path operation in Imagic should flow through these helpers to
guarantee correct behaviour on Windows, macOS, and Linux.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from imagic.utils.runtime_paths import find_bundled_rawtherapee_cli


def normalise(path: str | Path) -> Path:
    """Resolve and normalise a path.

    Args:
        path: Raw string or ``Path`` object.

    Returns:
        An absolute, resolved ``Path`` with all ``..`` segments collapsed.
    """
    return Path(path).expanduser().resolve()


def safe_filename(name: str) -> str:
    """Sanitise a string so it is safe to use as a filename on any OS.

    Args:
        name: The proposed filename.

    Returns:
        A sanitised version with illegal characters replaced by ``_``.
    """
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name).strip(". ")


def ensure_directory(path: Path) -> Path:
    """Create a directory (and parents) if it does not exist.

    Args:
        path: The directory to create.

    Returns:
        The same ``Path`` for chaining.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_sidecar(raw_path: Path) -> Optional[Path]:
    """Look for an XMP sidecar file next to a RAW image.

    Checks for both ``<stem>.xmp`` and ``<stem>.<ext>.xmp`` conventions.

    Args:
        raw_path: Path to the RAW file.

    Returns:
        The sidecar ``Path`` if found, otherwise ``None``.
    """
    candidates = [
        raw_path.with_suffix(".xmp"),
        raw_path.parent / f"{raw_path.name}.xmp",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def relative_to_safe(path: Path, base: Path) -> Path:
    """Return *path* relative to *base*, or *path* unchanged on failure.

    Args:
        path: The path to relativise.
        base: The base directory.

    Returns:
        A relative ``Path`` if possible, otherwise the original *path*.
    """
    try:
        return path.relative_to(base)
    except ValueError:
        return path


# ------------------------------------------------------------------
# Cross-platform CLI tool discovery
# ------------------------------------------------------------------

import platform
import shutil
from typing import Optional as _Optional


def _which(name: str) -> _Optional[str]:
    """Thin wrapper around ``shutil.which``."""
    return shutil.which(name)


def discover_rawtherapee_cli() -> str:
    """Return a best-guess path to ``rawtherapee-cli`` for the current OS.

    Searches:
    * ``PATH`` (all platforms)
    * Common macOS locations (``/Applications``, Homebrew)
    * Common Windows locations (``Program Files``)

    Returns:
        An absolute path string, or ``""`` if not found.
    """
    bundled = find_bundled_rawtherapee_cli()
    if bundled:
        return str(bundled)

    found = _which("rawtherapee-cli")
    if found:
        return found

    system = platform.system()
    if system == "Darwin":
        candidates = [
            "/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli",
            "/opt/homebrew/bin/rawtherapee-cli",
            "/usr/local/bin/rawtherapee-cli",
        ]
    elif system == "Windows":
        import glob
        candidates = glob.glob(
            r"C:\Program Files\RawTherapee\*\rawtherapee-cli.exe"
        ) + glob.glob(
            r"C:\Program Files (x86)\RawTherapee\*\rawtherapee-cli.exe"
        )
    else:  # Linux / other
        candidates = [
            "/usr/bin/rawtherapee-cli",
            "/usr/local/bin/rawtherapee-cli",
            "/snap/rawtherapee/current/usr/bin/rawtherapee-cli",
        ]

    for c in candidates:
        if Path(c).is_file():
            return str(c)
    return ""


def discover_darktable_cli() -> str:
    """Return a best-guess path to ``darktable-cli`` for the current OS.

    Returns:
        An absolute path string, or ``""`` if not found.
    """
    found = _which("darktable-cli")
    if found:
        return found

    system = platform.system()
    if system == "Darwin":
        candidates = [
            "/Applications/darktable.app/Contents/MacOS/darktable-cli",
            "/opt/homebrew/bin/darktable-cli",
            "/usr/local/bin/darktable-cli",
        ]
    elif system == "Windows":
        import glob
        candidates = glob.glob(
            r"C:\Program Files\darktable\bin\darktable-cli.exe"
        )
    else:
        candidates = [
            "/usr/bin/darktable-cli",
            "/usr/local/bin/darktable-cli",
            "/snap/darktable/current/usr/bin/darktable-cli",
        ]

    for c in candidates:
        if Path(c).is_file():
            return str(c)
    return ""


def discover_exiftool() -> str:
    """Return a best-guess path to ``exiftool`` for the current OS.

    Returns:
        An absolute path string, or ``""`` if not found.
    """
    found = _which("exiftool")
    if found:
        return found

    system = platform.system()
    if system == "Darwin":
        candidates = [
            "/opt/homebrew/bin/exiftool",
            "/usr/local/bin/exiftool",
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Windows\exiftool.exe",
            r"C:\exiftool\exiftool.exe",
        ]
    else:
        candidates = [
            "/usr/bin/exiftool",
            "/usr/local/bin/exiftool",
        ]

    for c in candidates:
        if Path(c).is_file():
            return str(c)
    return ""


def open_file_manager(path: Path) -> None:
    """Open *path* in the platform's native file manager.

    Works on Windows (``explorer``), macOS (``open``), and Linux
    (``xdg-open``).

    Args:
        path: Directory or file to reveal.
    """
    import subprocess

    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", str(path)])
    elif system == "Windows":
        subprocess.Popen(["explorer", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
