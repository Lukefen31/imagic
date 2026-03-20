# macOS Packaging

This folder is the native packaging entry point for macOS builds.

## What the build script does

1. **Auto-fetches RawTherapee** — downloads the vendor payload if not already present
2. **Builds the .app bundle** — via PyInstaller using the shared cross-platform spec
3. **Bundles RawTherapee** — extracts and embeds `RawTherapee.app` inside the app bundle (required, not optional)
4. **Embeds the EULA** — copies `packaging/EULA.txt` into the app bundle and attaches it to the DMG
5. **Ad-hoc code signs** — signs all dylibs, embedded apps, and the main bundle with entitlements so macOS Gatekeeper won't flag the app as "damaged"
6. **Creates a styled DMG** — with drag-to-Applications layout, background, and EULA prompt

## Manual requirements

- Build on macOS
- `create-dmg` (install via `brew install create-dmg` for styled DMG with EULA)
- Apple Developer signing identity (optional — for public distribution)
- Notarization credentials (optional — for friction-free public release)

## Quick start

```bash
# 1. Set up the Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[packaging]"

# 2. Run the build (auto-fetches RawTherapee if needed)
bash packaging/macos/build-desktop.sh

# 3. Output
ls -la dist/macos/imagic-desktop.dmg
```

## Output

- `dist/macos/imagic-desktop.dmg` — styled DMG with EULA, ad-hoc signed
- `build/macos/pyinstaller/dist/imagic.app` — the raw app bundle

## Code signing

The build script applies **ad-hoc signing** by default, which:
- Removes quarantine-related "damaged app" errors
- Allows users to launch the app (may require right-click → Open on first launch)
- Uses `packaging/macos/entitlements.plist` for library validation and JIT permissions

For **public distribution**, re-sign with a Developer ID and notarize:

```bash
codesign --force --deep --sign "Developer ID Application: ..." dist/macos/imagic-desktop.dmg
xcrun notarytool submit dist/macos/imagic-desktop.dmg \
  --apple-id "..." --team-id "..." --password "..."
xcrun stapler staple dist/macos/imagic-desktop.dmg
```

## Key files

| File | Purpose |
|------|---------|
| `build-desktop.sh` | Main build script |
| `entitlements.plist` | macOS entitlements for code signing |
| `branding/imagic.icns` | App icon |
| `branding/dmg-background.png` | DMG window background |
| `../EULA.txt` | License agreement (shown on DMG open) |
| `../windows/imagic.spec` | Shared PyInstaller spec |
| `../rawtherapee-manifest.json` | RawTherapee download manifest |

## Runtime expectation

The imagic app will auto-detect bundled RawTherapee at:

- `RawTherapee/rawtherapee-cli`
- `RawTherapee/RawTherapee.app/Contents/MacOS/rawtherapee-cli`
- `RawTherapee/bin/rawtherapee-cli`