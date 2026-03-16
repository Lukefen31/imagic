#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPEC_PATH="$REPO_ROOT/packaging/windows/imagic.spec"
BUILD_ROOT="$REPO_ROOT/build/linux"
DIST_ROOT="$BUILD_ROOT/pyinstaller/dist"
WORK_ROOT="$BUILD_ROOT/pyinstaller/work"
OUTPUT_ROOT="${OUTPUT_DIR:-$REPO_ROOT/dist/linux}"
VENDOR_PAYLOAD_DIR="${RAWTHERAPEE_PAYLOAD_DIR:-$REPO_ROOT/build/vendor/rawtherapee/linux-x64/payload}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script must run on Linux." >&2
  exit 1
fi

PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

rm -rf "$BUILD_ROOT"
mkdir -p "$DIST_ROOT" "$WORK_ROOT" "$OUTPUT_ROOT"

"$PYTHON_BIN" -m PyInstaller "$SPEC_PATH" --noconfirm --clean --distpath "$DIST_ROOT" --workpath "$WORK_ROOT"

APP_DIR="$DIST_ROOT/imagic"
if [[ ! -d "$APP_DIR" ]]; then
  echo "PyInstaller did not produce $APP_DIR" >&2
  exit 1
fi

if [[ -d "$VENDOR_PAYLOAD_DIR" ]]; then
  APPIMAGE_ASSET="$(find "$VENDOR_PAYLOAD_DIR" -maxdepth 1 -name 'RawTherapee_*.AppImage' | head -n 1)"
  if [[ -n "$APPIMAGE_ASSET" ]]; then
    RAWTHERAPEE_WORK="$BUILD_ROOT/rawtherapee-work"
    rm -rf "$RAWTHERAPEE_WORK"
    mkdir -p "$RAWTHERAPEE_WORK"

    chmod +x "$APPIMAGE_ASSET"
    (
      cd "$RAWTHERAPEE_WORK"
      "$APPIMAGE_ASSET" --appimage-extract >/dev/null
    )

    mkdir -p "$APP_DIR/RawTherapee"
    cp -R "$RAWTHERAPEE_WORK/squashfs-root" "$APP_DIR/RawTherapee/runtime"
  fi
fi

TARBALL_PATH="$OUTPUT_ROOT/imagic-desktop-linux.tar.gz"
tar -czf "$TARBALL_PATH" -C "$DIST_ROOT" imagic

echo ""
echo "Linux packaging complete."
echo "App folder: $APP_DIR"
echo "Portable archive: $TARBALL_PATH"
echo ""
echo "Manual follow-up: if you want a polished public Linux installer, create an AppImage or distro-specific package from this staged bundle."