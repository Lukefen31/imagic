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

# ── 1. Build with PyInstaller (produces imagic.app via BUNDLE) ──────────────
"$PYTHON_BIN" -m PyInstaller "$SPEC_PATH" --noconfirm --clean --distpath "$DIST_ROOT" --workpath "$WORK_ROOT"

APP_BUNDLE="$DIST_ROOT/imagic.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "PyInstaller did not produce $APP_BUNDLE" >&2
  exit 1
fi

# The COLLECT output lives inside the .app bundle:
APP_MACOS="$APP_BUNDLE/Contents/MacOS"
APP_RESOURCES="$APP_BUNDLE/Contents/Resources"

# ── 2. Bundle RawTherapee inside the .app ────────────────────────────────────
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

    # Place RawTherapee inside imagic.app/Contents/Resources/RawTherapee/
    mkdir -p "$APP_RESOURCES/RawTherapee"
    cp -R "$RAWTHERAPEE_APP" "$APP_RESOURCES/RawTherapee/RawTherapee.app"
    ln -sf "RawTherapee.app/Contents/MacOS/rawtherapee-cli" "$APP_RESOURCES/RawTherapee/rawtherapee-cli"
    echo "Bundled RawTherapee into $APP_RESOURCES/RawTherapee/"
  fi
fi

# ── 3. Create styled DMG with drag-to-Applications layout ────────────────────
DMG_PATH="$OUTPUT_ROOT/imagic-desktop.dmg"
DMG_BG="$REPO_ROOT/packaging/macos/branding/dmg-background.png"
ICNS_FILE="$REPO_ROOT/packaging/macos/branding/imagic.icns"

rm -f "$DMG_PATH"

# Prefer create-dmg for the styled experience; fall back to plain hdiutil.
if command -v create-dmg &>/dev/null && [[ -f "$DMG_BG" ]]; then
  echo "Building styled DMG with create-dmg..."

  CREATE_DMG_ARGS=(
    --volname "imagic"
    --volicon "$ICNS_FILE"
    --background "$DMG_BG"
    --window-pos 200 120
    --window-size 660 400
    --icon-size 80
    --icon "imagic.app" 180 200
    --icon "Applications" 480 200
    --hide-extension "imagic.app"
    --app-drop-link 480 200
    --no-internet-enable
    "$DMG_PATH"
    "$APP_BUNDLE"
  )

  create-dmg "${CREATE_DMG_ARGS[@]}" || {
    # create-dmg exits 2 when it succeeds but couldn't sign (expected without a dev cert)
    if [[ $? -ne 2 ]]; then
      echo "create-dmg failed" >&2
      exit 1
    fi
  }
else
  echo "Building plain DMG (install create-dmg via brew for a styled installer)..."
  DMG_STAGING="$BUILD_ROOT/dmg-staging"
  rm -rf "$DMG_STAGING"
  mkdir -p "$DMG_STAGING"
  cp -R "$APP_BUNDLE" "$DMG_STAGING/imagic.app"
  ln -s /Applications "$DMG_STAGING/Applications"
  hdiutil create -volname "imagic" -srcfolder "$DMG_STAGING" -ov -format UDZO "$DMG_PATH" >/dev/null
fi

echo ""
echo "macOS packaging complete."
echo "App bundle: $APP_BUNDLE"
echo "DMG: $DMG_PATH"
echo ""
echo "Users open the DMG and drag imagic.app → Applications."
echo "Manual follow-up: codesign and notarize the app bundle and DMG before public distribution."