"""Runtime-aware paths for source and packaged desktop builds."""

from __future__ import annotations

import platform
import sys
from pathlib import Path


def _source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def install_root() -> Path:
    """Return the directory containing the running desktop executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _source_root()


def resource_root() -> Path:
    """Return the root directory used for packaged data files."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return install_root()


def resolve_resource(*parts: str) -> Path:
    """Resolve a resource path in both source and frozen builds."""
    relative = Path(*parts)
    for base in (resource_root(), install_root(), _source_root()):
        candidate = base / relative
        if candidate.exists():
            return candidate
    return _source_root() / relative


def _rawtherapee_candidate_patterns() -> list[str]:
    system = platform.system()
    if system == "Windows":
        return [
            "RawTherapee/rawtherapee-cli.exe",
            "RawTherapee/bin/rawtherapee-cli.exe",
            "RawTherapee/*/rawtherapee-cli.exe",
            "RawTherapee/**/rawtherapee-cli.exe",
        ]

    if system == "Darwin":
        return [
            "RawTherapee/rawtherapee-cli",
            "RawTherapee/bin/rawtherapee-cli",
            "RawTherapee/RawTherapee.app/Contents/MacOS/rawtherapee-cli",
            "RawTherapee/*.app/Contents/MacOS/rawtherapee-cli",
            "RawTherapee/**/*.app/Contents/MacOS/rawtherapee-cli",
            "RawTherapee/**/rawtherapee-cli",
        ]

    return [
        "RawTherapee/rawtherapee-cli",
        "RawTherapee/bin/rawtherapee-cli",
        "RawTherapee/*/rawtherapee-cli",
        "RawTherapee/**/rawtherapee-cli",
    ]


def find_bundled_rawtherapee_cli() -> Path | None:
    """Return the bundled RawTherapee CLI path when shipped beside the app.

    The release pipeline normalises bundled payloads under a top-level
    ``RawTherapee`` directory. This lookup supports native Windows, macOS,
    and Linux layouts so the desktop app can stay platform-agnostic.
    """
    patterns = _rawtherapee_candidate_patterns()
    seen: set[Path] = set()
    for base in (install_root(), resource_root()):
        for pattern in patterns:
            for candidate in base.glob(pattern):
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                if resolved.is_file():
                    return resolved
    return None