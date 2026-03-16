# macOS Installer Build — Copilot Prompt

> Open this workspace in VS Code on your Mac, start a Copilot chat, and paste the prompt below.

---

## Prompt

```
I need you to build the macOS desktop installer for imagic. Everything is already scripted — you just need to run the steps and fix anything that comes up.

Here's the plan:

1. **Set up the Python environment**
   - Create a venv: `python3 -m venv .venv && source .venv/bin/activate`
   - Install the project: `pip install -e ".[packaging]"`
   - Confirm PyInstaller is available: `python -m PyInstaller --version`

2. **Fetch the RawTherapee vendor payload**
   ```bash
   python packaging/fetch_rawtherapee_payload.py macos-universal
   ```
   This downloads the official RawTherapee 5.12 macOS universal binary + source archive into `build/vendor/rawtherapee/macos-universal/payload/`. It verifies SHA-256 checksums automatically.

3. **Run the macOS build script**
   ```bash
   bash packaging/macos/build-desktop.sh
   ```
   This will:
   - Build the PyInstaller app bundle using `packaging/windows/imagic.spec` (shared cross-platform spec)
   - Unzip the RawTherapee vendor payload, mount the embedded DMG
   - Copy `RawTherapee.app` into the app bundle and create a `rawtherapee-cli` symlink
   - Create `dist/macos/imagic-desktop.dmg`

4. **Verify the output**
   - Check that `dist/macos/imagic-desktop.dmg` exists
   - Mount it and verify `imagic` app launches
   - Verify RawTherapee is bundled inside the app folder

5. **Optional: codesign + notarize**
   If we have an Apple Developer identity available, sign and notarize:
   ```bash
   codesign --deep --force --sign "Developer ID Application: ..." dist/macos/imagic-desktop.dmg
   xcrun notarytool submit dist/macos/imagic-desktop.dmg --apple-id "..." --team-id "..." --password "..."
   ```
   If no signing identity is available, skip this — the unsigned DMG still works for direct distribution.

6. **Report back**
   Tell me the final DMG path, file size, and whether RawTherapee bundling worked.

Key files for reference:
- `packaging/macos/build-desktop.sh` — the build script
- `packaging/macos/README.md` — macOS packaging docs
- `packaging/windows/imagic.spec` — shared PyInstaller spec
- `packaging/rawtherapee-manifest.json` — vendor manifest with URLs + checksums
- `packaging/fetch_rawtherapee_payload.py` — vendor downloader
- `packaging/README.md` — cross-platform packaging overview
```

---

## What's already done

- ✅ PyInstaller spec is cross-platform (icon conditional on Windows)
- ✅ macOS build script handles DMG creation, RawTherapee bundling, symlinks
- ✅ RawTherapee 5.12 manifest with real GitHub release URLs and SHA-256 checksums
- ✅ Vendor fetcher downloads + verifies automatically
- ✅ All branding assets generated

## What needs a Mac

- PyInstaller can only produce a `.app` bundle on macOS
- `hdiutil` (for DMG creation) is macOS-only
- Codesigning + notarization require macOS + Apple Developer credentials
