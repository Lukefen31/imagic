"""PyInstaller runtime hook – ensure Qt6 DLLs are discoverable."""
import os
import sys

if sys.platform == "win32":
    # In a frozen app the _internal dir sits next to the executable.
    _internal = os.path.join(os.path.dirname(sys.executable), "_internal")
    qt_bin = os.path.join(_internal, "PyQt6", "Qt6", "bin")

    if os.path.isdir(qt_bin):
        os.environ["PATH"] = qt_bin + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(qt_bin)
        except (OSError, AttributeError):
            pass
