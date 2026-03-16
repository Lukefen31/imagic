"""Imagic launcher — entry point for the desktop shortcut."""
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

# If we're not already running inside the project venv, re-launch with it.
_VENV_PYTHON = _ROOT / ".venv" / "Scripts" / "pythonw.exe"
if _VENV_PYTHON.is_file() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
    subprocess.Popen([str(_VENV_PYTHON), str(Path(__file__).resolve())])
    sys.exit(0)

# Ensure the src package tree is importable when running from source.
_src = str(_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

sys.argv = ["imagic"]
from imagic.main import main
main()
