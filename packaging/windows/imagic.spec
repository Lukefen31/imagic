# -*- mode: python ; coding: utf-8 -*-

import platform
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

project_root = Path(SPECPATH).parents[1]
entry_script = project_root / "launch_imagic.pyw"

# --- Icon selection ---
icon_arg = None
if IS_WINDOWS:
    win_icon = project_root / "packaging" / "windows" / "branding" / "imagic-app-icon.ico"
    if win_icon.is_file():
        icon_arg = str(win_icon)

mac_icns = None
mac_entitlements = None
if IS_MACOS:
    icns_path = project_root / "packaging" / "macos" / "branding" / "imagic.icns"
    if icns_path.is_file():
        mac_icns = str(icns_path)
    ent_path = project_root / "packaging" / "macos" / "entitlements.plist"
    if ent_path.is_file():
        mac_entitlements = str(ent_path)

import certifi as _certifi_mod
_certifi_dir = str(Path(_certifi_mod.__file__).parent)

datas = [
    (str(project_root / "assets"), "assets"),
    (str(project_root / "config"), "config"),
    (_certifi_dir, "certifi"),
]

hiddenimports = collect_submodules("imagic")

# Runtime hook that adds PyQt6/Qt6/bin to the DLL search path
_qt_rthook = str(project_root / "packaging" / "windows" / "rthook_pyqt6.py")
# Runtime hook that sets SSL_CERT_FILE for frozen macOS builds
_ssl_rthook = str(project_root / "packaging" / "rthook_ssl_certs.py")

a = Analysis(
    [str(entry_script)],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[_ssl_rthook, _qt_rthook],
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
    entitlements_file=mac_entitlements,
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

# --- macOS: wrap in a proper .app bundle ---
if IS_MACOS:
    app = BUNDLE(
        coll,
        name="imagic.app",
        icon=mac_icns,
        bundle_identifier="com.imagic.desktop",
        info_plist={
            "CFBundleDisplayName": "imagic",
            "CFBundleName": "imagic",
            "CFBundleShortVersionString": "0.4.0",
            "CFBundleVersion": "0.4.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "12.0",
        },
    )