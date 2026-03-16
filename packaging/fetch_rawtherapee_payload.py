"""Prepare a normalised RawTherapee payload for native imagic bundles.

This script downloads the official RawTherapee binary asset and matching source
archive declared in ``packaging/rawtherapee-manifest.json``, verifies checksums,
and stages them for per-platform packaging.

It intentionally stops short of unsafe cross-platform extraction steps that are
host-specific, such as unpacking Windows installers on non-Windows or mounting a
macOS DMG on non-macOS. Those steps are delegated to the native packaging
scripts for each platform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "packaging" / "rawtherapee-manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def verify_checksum(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise RuntimeError(
            f"Checksum mismatch for {path.name}: expected {expected}, got {actual}"
        )


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def write_metadata(target_dir: Path, manifest: dict, platform_key: str, asset_path: Path) -> None:
    metadata = {
        "rawtherapee_version": manifest["version"],
        "platform": platform_key,
        "binary_asset": asset_path.name,
        "binary_sha256": manifest["platforms"][platform_key]["sha256"],
        "source_asset": manifest["source"]["asset_name"],
        "source_sha256": manifest["source"]["sha256"],
        "homepage": manifest["homepage"],
        "bundle_mode": manifest["platforms"][platform_key]["bundle_mode"],
        "executable_relpath": manifest["platforms"][platform_key]["executable_relpath"],
    }
    (target_dir / "rawtherapee-bundle-metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "platform",
        choices=["windows-x64", "macos-universal", "linux-x64"],
        help="Target RawTherapee bundle platform.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "build" / "vendor" / "rawtherapee"),
        help="Directory where vendor payloads should be staged.",
    )
    args = parser.parse_args()

    manifest = load_manifest()
    platform_entry = manifest["platforms"][args.platform]

    output_dir = Path(args.output_dir).resolve() / args.platform
    downloads_dir = output_dir / "downloads"
    payload_dir = output_dir / "payload"
    payload_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    binary_path = downloads_dir / platform_entry["asset_name"]
    if not binary_path.is_file():
        print(f"Downloading {platform_entry['asset_name']}...")
        download_file(platform_entry["url"], binary_path)
    verify_checksum(binary_path, platform_entry["sha256"])

    source_entry = manifest["source"]
    source_path = downloads_dir / source_entry["asset_name"]
    if not source_path.is_file():
        print(f"Downloading {source_entry['asset_name']}...")
        download_file(source_entry["url"], source_path)
    verify_checksum(source_path, source_entry["sha256"])

    shutil.copy2(binary_path, payload_dir / binary_path.name)
    shutil.copy2(source_path, payload_dir / source_path.name)
    write_metadata(payload_dir, manifest, args.platform, binary_path)

    print("")
    print("Prepared RawTherapee vendor payload:")
    print(f"  Platform: {args.platform}")
    print(f"  Payload dir: {payload_dir}")
    print(f"  Bundle mode: {platform_entry['bundle_mode']}")
    print("")
    print("Next step:")
    if platform_entry["bundle_mode"] == "manual-extract":
        print("  Extract the Windows installer into a RawTherapee folder before running the Windows packaging script.")
    elif platform_entry["bundle_mode"] == "zip-contains-dmg":
        print("  Run the macOS packaging script on macOS to unzip the archive and mount the DMG with hdiutil.")
    else:
        print("  Run the Linux packaging script on Linux to extract the AppImage into a normalised RawTherapee/bin layout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())