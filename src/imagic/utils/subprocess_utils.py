"""Subprocess helpers — hide console windows on Windows.

When the desktop app is launched without a console (windowed PyInstaller
build or ``.pyw`` launcher), calling a console-subsystem executable
through :mod:`subprocess` will allocate a brand-new console window for
each invocation. That produces visible "terminal spam" during batch
operations such as thumbnail generation and metadata reads.

This module centralises the Windows-specific flags so every subprocess
call in the codebase silences the console the same way.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

__all__ = ["hidden_subprocess_kwargs"]


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Return :func:`subprocess.run`/:class:`subprocess.Popen` kwargs that
    suppress the console window on Windows.

    The combination of ``CREATE_NO_WINDOW`` and a ``STARTUPINFO`` with
    ``STARTF_USESHOWWINDOW`` + ``SW_HIDE`` reliably hides both new
    console allocations and any window the child process might try to
    show on its own.

    On non-Windows platforms an empty dict is returned so callers can
    splat the result unconditionally.
    """
    if sys.platform != "win32":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }
