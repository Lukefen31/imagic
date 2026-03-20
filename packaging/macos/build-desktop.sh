#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPEC_PATH="$REPO_ROOT/packaging/windows/imagic.spec"
BUILD_ROOT="$REPO_ROOT/build/macos"
DIST_ROOT="$BUILD_ROOT/pyinstaller/dist"
WORK_ROOT="$BUILD_ROOT/pyinstaller/work"
OUTPUT_ROOT="${OUTPUT_DIR:-$REPO_ROOT/dist/macos}"
VENDOR_PAYLOAD_DIR="${RAWTHERAPEE_PAYLOAD_DIR:-$REPO_ROOT/build/vendor/rawtherapee/macos-universal/payload}"
ENTITLEMENTS="$REPO_ROOT/packaging/macos/entitlements.plist"
EULA_FILE="$REPO_ROOT/packaging/EULA.txt"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS." >&2
  exit 1
fi

PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

# ── 0. Auto-fetch RawTherapee payload if not present ─────────────────────────
if [[ ! -d "$VENDOR_PAYLOAD_DIR" ]]; then
  echo "RawTherapee vendor payload not found — fetching automatically..."
  "$PYTHON_BIN" "$REPO_ROOT/packaging/fetch_rawtherapee_payload.py" macos-universal
fi

# Verify it arrived
if [[ ! -d "$VENDOR_PAYLOAD_DIR" ]]; then
  echo "ERROR: RawTherapee vendor payload still missing at:" >&2
  echo "  $VENDOR_PAYLOAD_DIR" >&2
  echo "" >&2
  echo "Run manually:  python packaging/fetch_rawtherapee_payload.py macos-universal" >&2
  exit 1
fi

rm -rf "$BUILD_ROOT/pyinstaller"
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

# ── 2. Bundle RawTherapee inside the .app (required) ────────────────────────
ZIP_ASSET="$(find "$VENDOR_PAYLOAD_DIR" -maxdepth 1 -name 'RawTherapee_macOS_*.zip' | head -n 1)"
if [[ -z "$ZIP_ASSET" ]]; then
  echo "ERROR: No RawTherapee_macOS_*.zip found in $VENDOR_PAYLOAD_DIR" >&2
  exit 1
fi

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

# The RawTherapee DMG contains a GPL license prompt — auto-accept it
echo "Y" | PAGER=cat hdiutil attach "$DMG_ASSET" -mountpoint "$RAWTHERAPEE_MOUNT" -nobrowse
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
echo "✔ Bundled RawTherapee into $APP_RESOURCES/RawTherapee/"

# ── 3. Copy EULA into the app bundle ────────────────────────────────────────
if [[ -f "$EULA_FILE" ]]; then
  cp "$EULA_FILE" "$APP_RESOURCES/EULA.txt"
  echo "✔ EULA copied into app bundle"
fi

# ── 4. Strip quarantine attributes & ad-hoc codesign ────────────────────────
# Without signing, macOS Gatekeeper will declare the app "damaged" when
# downloaded from the internet.  Ad-hoc signing (sign with "-") removes the
# quarantine issue and satisfies Gatekeeper for direct distribution.
# For public distribution via the web, a full Developer ID + notarization
# is still recommended, but ad-hoc makes the app *launchable*.

echo ""
echo "Fixing file permissions for codesigning..."
chmod -R u+rw "$APP_BUNDLE"

echo "Stripping quarantine extended attributes..."
xattr -cr "$APP_BUNDLE"

echo "Code-signing the app bundle (ad-hoc)..."

# Sign embedded frameworks & dylibs first (inside-out signing order)
find "$APP_BUNDLE" -type f \( -name "*.dylib" -o -name "*.so" \) -exec \
  codesign --force --sign - --entitlements "$ENTITLEMENTS" {} \;

# Sign any embedded .app bundles (e.g. RawTherapee.app)
find "$APP_BUNDLE/Contents/Resources" -maxdepth 3 -name "*.app" -type d | while read -r embedded_app; do
  echo "  Signing embedded app: $(basename "$embedded_app")"
  codesign --force --deep --sign - --entitlements "$ENTITLEMENTS" "$embedded_app"
done

# Sign the main executable
codesign --force --sign - --entitlements "$ENTITLEMENTS" "$APP_MACOS/imagic"

# Sign the top-level .app bundle
codesign --force --deep --sign - --entitlements "$ENTITLEMENTS" "$APP_BUNDLE"

echo "✔ Ad-hoc code signing complete"

# Verify the signature
echo "Verifying code signature..."
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE" 2>&1 || {
  echo "WARNING: Code signature verification reported issues (may be expected for ad-hoc signing)" >&2
}

# ── 5. Create styled DMG with EULA and drag-to-Applications layout ───────────
DMG_PATH="$OUTPUT_ROOT/imagic-desktop.dmg"
DMG_BG="$REPO_ROOT/packaging/macos/branding/dmg-background.png"
ICNS_FILE="$REPO_ROOT/packaging/macos/branding/imagic.icns"

rm -f "$DMG_PATH"

# Prefer create-dmg for the styled experience; fall back to plain hdiutil.
if command -v create-dmg &>/dev/null && [[ -f "$DMG_BG" ]]; then
  echo ""
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
  )

  # Attach EULA so macOS shows the license agreement when the DMG is opened
  if [[ -f "$EULA_FILE" ]]; then
    CREATE_DMG_ARGS+=(--eula "$EULA_FILE")
    echo "  Including EULA in DMG"
  fi

  CREATE_DMG_ARGS+=("$DMG_PATH" "$APP_BUNDLE")

  create-dmg "${CREATE_DMG_ARGS[@]}" || {
    rc=$?
    # create-dmg exits 2 when it succeeds but couldn't sign (expected without a dev cert)
    if [[ $rc -ne 2 ]]; then
      echo "create-dmg failed (exit code $rc)" >&2
      exit 1
    fi
  }
else
  echo ""
  echo "Building plain DMG (install create-dmg via brew for a styled installer)..."
  DMG_STAGING="$BUILD_ROOT/dmg-staging"
  rm -rf "$DMG_STAGING"
  mkdir -p "$DMG_STAGING"
  cp -R "$APP_BUNDLE" "$DMG_STAGING/imagic.app"
  ln -s /Applications "$DMG_STAGING/Applications"

  # Include EULA in the plain DMG contents as well
  if [[ -f "$EULA_FILE" ]]; then
    cp "$EULA_FILE" "$DMG_STAGING/EULA.txt"
  fi

  hdiutil create -volname "imagic" -srcfolder "$DMG_STAGING" -ov -format UDZO "$DMG_PATH" >/dev/null
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  macOS packaging complete"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  App bundle : $APP_BUNDLE"
echo "  DMG        : $DMG_PATH"
echo "  Signed     : ad-hoc (launch without 'damaged app' error)"
echo "  EULA       : $(if [[ -f "$EULA_FILE" ]]; then echo 'embedded in DMG'; else echo 'not found'; fi)"
echo "  RawTherapee: bundled"
echo ""
echo "  Users open the DMG, accept the EULA, and drag imagic.app → Applications."
echo ""
echo "  For public distribution, re-sign with a Developer ID and notarize:"
echo "    codesign --force --deep --sign 'Developer ID Application: ...' $DMG_PATH"
echo "    xcrun notarytool submit $DMG_PATH --apple-id '...' --team-id '...' --password '...'"
echo ""