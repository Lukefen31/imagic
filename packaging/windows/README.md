# Windows Packaging

This folder builds the standalone Windows desktop app and the installers used by desktop checkout fulfilment.

For the shared cross-platform release model and RawTherapee vendor manifest, see `packaging/README.md`.

## Requirements

- Python environment with `PyInstaller` installed
- Inno Setup 6 (`ISCC.exe`)
- Optional RawTherapee payload folder containing `rawtherapee-cli.exe`

## Install packaging dependencies

```powershell
pip install -e ".[packaging]"
```

## Build the standard installer

```powershell
.\packaging\windows\build-desktop.ps1 -Version 0.1.0
```

This produces:

- `imagic-desktop-setup.exe`

## Prepare the Windows RawTherapee payload

Stage the official upstream files first:

```powershell
python .\packaging\fetch_rawtherapee_payload.py windows-x64
```

That downloads the official Windows asset plus matching source archive into `build/vendor/rawtherapee/windows-x64/payload`.

Because the upstream Windows binary is distributed as an installer, one manual step is still required on Windows: extract that installer into a folder that contains `rawtherapee-cli.exe`.

## Build the recommended installer with bundled RawTherapee

After extraction, point the build script at the extracted RawTherapee directory:

```powershell
.\packaging\windows\build-desktop.ps1 -Version 0.1.0 -RawTherapeePayloadDir "C:\path\to\RawTherapee"
```

This produces:

- `imagic-desktop-setup.exe`
- `imagic-desktop-recommended-rawtherapee-setup.exe`

The recommended installer includes an optional but preselected RawTherapee component. If the component stays enabled, imagic auto-detects the bundled `rawtherapee-cli.exe` after install.

## Wire the downloads into desktop checkout fulfilment

Point the website backend at the generated installers:

```powershell
$env:IMAGIC_DESKTOP_INSTALLER_PATH = "C:\path\to\imagic-desktop-setup.exe"
$env:IMAGIC_DESKTOP_BUNDLE_PATH = "C:\path\to\imagic-desktop-recommended-rawtherapee-setup.exe"
```

The checkout flow will then expose both download links automatically.