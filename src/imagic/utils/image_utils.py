"""Image format detection and helper functions."""

from __future__ import annotations

from pathlib import Path

from imagic.models.enums import SupportedFormat


def is_raw_file(path: Path) -> bool:
    """Return ``True`` if *path* has a recognised RAW extension.

    Args:
        path: File path to test.

    Returns:
        ``True`` for known RAW suffixes (case-insensitive).
    """
    return path.suffix.lower() in SupportedFormat.raw_extensions()


def is_supported_file(path: Path) -> bool:
    """Return ``True`` if *path* has any supported image extension.

    Args:
        path: File path to test.

    Returns:
        ``True`` for RAW **or** output format suffixes.
    """
    return path.suffix.lower() in SupportedFormat.all_extensions()


def get_extension_normalised(path: Path) -> str:
    """Return the file extension in lowercase, including the leading dot.

    Args:
        path: File path.

    Returns:
        e.g. ``".cr2"``, ``".nef"``.
    """
    return path.suffix.lower()
