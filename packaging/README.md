# Universal Packaging

This directory defines the cross-platform release system for imagic Desktop.

The goal is not one installer file for every OS. The goal is one release pipeline that produces native installers for each supported platform while keeping the app and license flow identical.

## Release model

- Windows: native `.exe` installer
- macOS: native `.app` + `.dmg`
- Linux: native portable bundle, preferably AppImage-based output

Each platform may optionally bundle the matching RawTherapee build as the recommended zero-setup RAW workflow.

## RawTherapee vendor data

- Shared manifest: `packaging/rawtherapee-manifest.json`
- Shared fetcher: `packaging/fetch_rawtherapee_payload.py`

The manifest records:

- exact upstream binary asset per platform
- matching source tarball
- checksums
- expected CLI path once normalised into an imagic bundle

## Prepare a vendor payload

Run one of these from the repo root:

```powershell
python .\packaging\fetch_rawtherapee_payload.py windows-x64
python .\packaging\fetch_rawtherapee_payload.py macos-universal
python .\packaging\fetch_rawtherapee_payload.py linux-x64
```

That stages the official binary asset plus matching source archive under `build/vendor/rawtherapee/<platform>/payload`.

Native packaging scripts then perform the last host-specific preparation step:

- Windows: extract the upstream installer into a portable payload folder
- macOS: unzip the archive and mount the embedded DMG
- Linux: extract the AppImage into a normalised directory

## Compliance notes

If imagic redistributes RawTherapee:

- keep RawTherapee as a separate bundled component
- include RawTherapee licensing in the final installer or app bundle
- keep the matching source archive used for that release
- record the shipped version and checksum in release metadata

## Platform docs

- Windows: `packaging/windows/README.md`
- macOS: `packaging/macos/README.md`
- Linux: `packaging/linux/README.md`