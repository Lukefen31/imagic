# Linux Packaging

This folder is the native packaging entry point for Linux builds.

## Manual requirements

- Build on Linux
- Decide which Linux output formats you want to support publicly

Recommended order:

- AppImage first
- optional `.tar.gz` portable build
- optional `.deb` later

## Recommended flow

1. Prepare the vendor payload:

```bash
python packaging/fetch_rawtherapee_payload.py linux-x64
```

2. Run the native build script on Linux:

```bash
bash packaging/linux/build-desktop.sh
```

That script builds the app, extracts the staged RawTherapee AppImage into a bundled runtime tree, and creates `dist/linux/imagic-desktop-linux.tar.gz`.

3. If you want a polished public Linux installer, convert that staged bundle into an AppImage or a distro-native package.

## Runtime expectation

The imagic app will auto-detect bundled RawTherapee at:

- `RawTherapee/rawtherapee-cli`
- `RawTherapee/bin/rawtherapee-cli`