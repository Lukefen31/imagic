# -*- mode: python ; coding: utf-8 -*-

import platform
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parents[1]
entry_script = project_root / "launch_imagic.pyw"
icon_path = project_root / "packaging" / "windows" / "branding" / "imagic-app-icon.ico"
icon_arg = None
if platform.system() == "Windows" and icon_path.is_file():
    icon_arg = str(icon_path)

datas = [
    (str(project_root / "assets"), "assets"),
    (str(project_root / "config"), "config"),
]

hiddenimports = collect_submodules("imagic")


a = Analysis(
    [str(entry_script)],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="imagic",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="imagic",
)