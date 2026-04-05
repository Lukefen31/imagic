# v0.3.2 Mac Build Continuation Guide

> Pull `main` on Mac, build the installer, upload to GitHub Releases.

---

## 1. Pull the latest code

```bash
cd ~/path/to/imagic
git pull origin main
```

Verify commit `ba6260e` is present:
```bash
git log --oneline -3
```

---

## 2. Bump version numbers (if not already done on Windows)

Three files need the version set to `0.3.2`:

```bash
# src/imagic/__init__.py  →  __version__ = "0.3.2"
sed -i '' 's/__version__ = "0.3.1"/__version__ = "0.3.2"/' src/imagic/__init__.py

# pyproject.toml  →  version = "0.3.2"
sed -i '' 's/version = "0.2.0"/version = "0.3.2"/' pyproject.toml

# packaging/windows/imagic-installer.iss  →  #define MyAppVersion "0.3.2"
sed -i '' 's/#define MyAppVersion "0.3.0"/#define MyAppVersion "0.3.2"/' packaging/windows/imagic-installer.iss
```

Commit the bump:
```bash
git add -A && git commit -m "chore: bump version to 0.3.2" && git push origin main
```

---

## 3. Set up Python env (if first time on this Mac)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[packaging]"
python -m PyInstaller --version   # confirm available
```

---

## 4. Fetch RawTherapee vendor payload

```bash
python packaging/fetch_rawtherapee_payload.py macos-universal
```

Downloads RawTherapee 5.12 macOS universal binary into `build/vendor/rawtherapee/macos-universal/payload/` with SHA-256 verification.

---

## 5. Build the macOS installer

```bash
bash packaging/macos/build-desktop.sh
```

This produces: **`dist/macos/imagic-desktop.dmg`**

What the script does:
- PyInstaller builds `imagic.app` via the shared spec (`packaging/windows/imagic.spec`)
- Extracts RawTherapee from the vendor DMG, bundles it inside `imagic.app/Contents/MacOS/`
- Creates a `rawtherapee-cli` symlink
- Generates the final DMG with `hdiutil`

---

## 6. Verify

```bash
# Check DMG exists and note size
ls -lh dist/macos/imagic-desktop.dmg

# Mount and verify app launches
hdiutil attach dist/macos/imagic-desktop.dmg
open /Volumes/imagic/imagic.app

# Verify RawTherapee is bundled
ls /Volumes/imagic/imagic.app/Contents/MacOS/RawTherapee.app
```

---

## 7. Optional: codesign + notarize

```bash
codesign --deep --force --sign "Developer ID Application: YOUR_NAME" dist/macos/imagic-desktop.dmg
xcrun notarytool submit dist/macos/imagic-desktop.dmg \
  --apple-id "YOUR_APPLE_ID" \
  --team-id "YOUR_TEAM_ID" \
  --password "APP_SPECIFIC_PASSWORD"
```

Skip if no Apple Developer identity is available — unsigned DMG works for direct distribution.

---

## 8. Upload to GitHub Releases

```bash
# Create the release (or add to existing v0.3.2 tag)
gh release create v0.3.2 \
  dist/macos/imagic-desktop.dmg \
  --title "v0.3.2" \
  --notes "Export options dialog, batch export, culling sort by date, crop fixes, AI crop, auto-updater"
```

Or if the Windows `.exe` was already uploaded and a v0.3.2 release exists:
```bash
gh release upload v0.3.2 dist/macos/imagic-desktop.dmg --clobber
```

---

## 9. Update website API (already done)

The `/api/desktop/latest-version` endpoint on Fly.io already returns `installer_macos` URL. After uploading the DMG to GitHub Releases, verify:
```bash
curl -s https://imagic-ink.fly.dev/api/desktop/latest-version | python3 -m json.tool
```

If the `installer_macos` URL needs updating, edit `website/api/desktop_delivery.py`.

---

## What's new in v0.3.2

| Feature | Files |
|---------|-------|
| Export options dialog (format/quality/scope) | `src/imagic/views/export_dialog.py` (NEW) |
| Batch export from editor | `views/photo_editor.py`, `main.py` |
| Culling sorted by date taken | `main.py` |
| Crop fix (persist + undo/redo) | `views/photo_editor.py` |
| AI Suggest Crop | `views/photo_editor.py`, `services/auto_crop.py` |
| Aspect ratio lock | `views/photo_editor.py` |
| In-app auto-updater | `services/auto_updater.py`, `views/main_window.py` |
| Changelog page + feedback form | `website/templates/changelog.html`, `website/api/main.py` |

---

## Key file locations

| Purpose | Path |
|---------|------|
| macOS build script | `packaging/macos/build-desktop.sh` |
| Shared PyInstaller spec | `packaging/windows/imagic.spec` |
| RawTherapee manifest | `packaging/rawtherapee-manifest.json` |
| Vendor downloader | `packaging/fetch_rawtherapee_payload.py` |
| macOS entitlements | `packaging/macos/entitlements.plist` |
| Windows build script | `packaging/windows/build-desktop.ps1` |
| Windows Inno Setup script | `packaging/windows/imagic-installer.iss` |
