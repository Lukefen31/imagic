"""Cross-platform path utilities built on ``pathlib``.

Every file-path operation in Imagic should flow through these helpers to
guarantee correct behaviour on Windows, macOS, and Linux.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


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
