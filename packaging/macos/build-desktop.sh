#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPEC_PATH="$REPO_ROOT/packaging/windows/imagic.spec"
BUILD_ROOT="$REPO_ROOT/build/macos"
DIST_ROOT="$BUILD_ROOT/pyinstaller/dist"
WORK_ROOT="$BUILD_ROOT/pyinstaller/work"
OUTPUT_ROOT="${OUTPUT_DIR:-$REPO_ROOT/dist/macos}"
VENDOR_PAYLOAD_DIR="${RAWTHERAPEE_PAYLOAD_DIR:-$REPO_ROOT/build/vendor/rawtherapee/macos-universal/payload}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS." >&2
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
  ZIP_ASSET="$(find "$VENDOR_PAYLOAD_DIR" -maxdepth 1 -name 'RawTherapee_macOS_*.zip' | head -n 1)"
  if [[ -n "$ZIP_ASSET" ]]; then
    RAWTHERAPEE_WORK="$BUILD_ROOT/rawtherapee-work"
    RAWTHERAPEE_MOUNT="$BUILD_ROOT/rawtherapee-mount"
    rm -rf "$RAWTHERAPEE_WORK" "$RAWTHERAPEE_MOUNT"
    mkdir -p "$RAWTHERAPEE_WORK" "$RAWTHERAPEE_MOUNT"

    unzip -q "$ZIP_ASSET" -d "$RAWTHERAPEE_WORK"
    DMG_ASSET="$(find "$RAWTHERAPEE_WORK" -name '*.dmg' | head -n 1)"
    if [[ -z "$DMG_ASSET" ]]; then
      echo "No DMG found inside $ZIP_ASSET" >&2
      exit 1
    fi

    hdiutil attach "$DMG_ASSET" -mountpoint "$RAWTHERAPEE_MOUNT" -nobrowse -quiet
    trap 'hdiutil detach "$RAWTHERAPEE_MOUNT" -quiet >/dev/null 2>&1 || true' EXIT

    RAWTHERAPEE_APP="$(find "$RAWTHERAPEE_MOUNT" -maxdepth 1 -name 'RawTherapee*.app' | head -n 1)"
    if [[ -z "$RAWTHERAPEE_APP" ]]; then
      echo "RawTherapee.app was not found in mounted DMG." >&2
      exit 1
    fi

    mkdir -p "$APP_DIR/RawTherapee"
    cp -R "$RAWTHERAPEE_APP" "$APP_DIR/RawTherapee/RawTherapee.app"
    ln -sf "RawTherapee.app/Contents/MacOS/rawtherapee-cli" "$APP_DIR/RawTherapee/rawtherapee-cli"
  fi
fi

DMG_PATH="$OUTPUT_ROOT/imagic-desktop.dmg"
hdiutil create -volname "imagic Desktop" -srcfolder "$APP_DIR" -ov -format UDZO "$DMG_PATH" >/dev/null

echo ""
echo "macOS packaging complete."
echo "App bundle: $APP_DIR"
echo "DMG: $DMG_PATH"
echo ""
echo "Manual follow-up: codesign and notarize the app bundle and DMG before public distribution."