"""Default configuration values.

These are used when no user config file exists yet.  The structure mirrors
``default_config.yaml`` so that any key can be overridden by the user.
"""

from __future__ import annotations

from pathlib import Path

from imagic.utils.path_utils import (
    discover_darktable_cli,
    discover_exiftool,
    discover_rawtherapee_cli,
)

# Application data lives next to the user's home directory by default.
_APP_DIR = Path.home() / ".imagic"

DEFAULTS: dict = {
    "app": {
        "name": "Imagic",
        "data_dir": str(_APP_DIR),
        "database": str(_APP_DIR / "imagic.db"),
        "log_file": str(_APP_DIR / "imagic.log"),
        "log_level": "INFO",
    },
    "scanner": {
        "watch_directories": [],
        "recursive": True,
        "follow_symlinks": False,
        "raw_extensions": [
            ".cr2", ".cr3", ".nef", ".arw", ".raf",
            ".orf", ".rw2", ".dng", ".pef",
        ],
    },
    "processing": {
        "max_workers": 4,
        "export_format": "jpeg",
        "jpeg_quality": 95,
        "max_file_size_kb": 0,
        "output_directory": str(_APP_DIR / "exports"),
        "thumbnail_directory": str(_APP_DIR / "thumbnails"),
        "default_pp3": "",  # Path to default RawTherapee PP3 processing profile
        "thumbnail_size": [320, 320],
    },
    "cli_tools": {
        "darktable_cli": discover_darktable_cli(),
        "rawtherapee_cli": discover_rawtherapee_cli(),
        "exiftool": discover_exiftool(),
    },
    "ai": {
        "enabled": True,
        "keep_threshold": 0.50,
        "trash_threshold": 0.35,
        "duplicate_hash_threshold": 5,
        "models_directory": str(_APP_DIR / "models"),
        # Heavy optional models — disabled by default for speed.
        # Enable these if you want perceptual IQA (pyiqa) or AI captions (Florence-2).
        "enable_perceptual_scorer": False,
        "enable_image_describer": False,
    },
    "ui": {
        "theme": "dark",
        "thumbnail_grid_columns": 5,
        "show_status_bar": True,
        "tutorial_version": 1,
        "last_seen_tutorial_version": 0,
    },
    "security": {
        "require_activation": True,
        "license_api_base_url": "https://imagic.ink",
        "license_email": "",
        "license_key": "",
        "activation_token": "",
    },
}
