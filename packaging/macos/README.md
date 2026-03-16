# macOS Packaging

This folder is the native packaging entry point for macOS builds.

## Manual requirements

- Build on macOS
- Apple Developer signing identity
- Notarization credentials if you want a friction-free public release

Those steps cannot be completed from this Windows workspace.

## Planned output

- `imagic.app`
- `imagic-desktop.dmg`
- optional recommended bundle with RawTherapee included

## Recommended flow

1. Prepare the vendor payload:

```bash
python packaging/fetch_rawtherapee_payload.py macos-universal
```

2. Run the native build script on macOS:

```bash
bash packaging/macos/build-desktop.sh
```

That script builds the app, unzips the vendor payload, mounts the embedded DMG, copies `RawTherapee.app`, and creates `dist/macos/imagic-desktop.dmg`.

3. Sign and notarize the final app and DMG.

## Runtime expectation

The imagic app will auto-detect bundled RawTherapee at:

- `RawTherapee/RawTherapee.app/Contents/MacOS/rawtherapee-cli`
- `RawTherapee/rawtherapee-cli`
- `RawTherapee/bin/rawtherapee-cli`