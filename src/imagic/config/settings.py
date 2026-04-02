"""Settings manager — loads, merges, and persists YAML configuration.

The manager deep-merges user overrides on top of the built-in defaults so that
every key always has a sensible value.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from imagic.config.defaults import DEFAULTS

logger = logging.getLogger(__name__)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*.

    Args:
        base: The default configuration dictionary.
        override: User-supplied overrides (may be partial).

    Returns:
        A new dictionary with overrides applied.
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class Settings:
    """Application-wide configuration store.

    Attributes:
        data: The fully merged configuration dictionary.
        config_path: Path to the user's YAML configuration file.
    """

    _instance: Optional["Settings"] = None

    def __init__(self, config_path: Optional[Path] = None) -> None:
        """Load configuration from *config_path*, falling back to defaults.

        Args:
            config_path: Path to a YAML file with user overrides.  If ``None``
                the default path (``~/.imagic/config.yaml``) is tried before
                falling back to built-in defaults.
        """
        # If no explicit path given, try the standard user config location so
        # that saved values (e.g. activation_token) are restored on next launch.
        _default_path = Path(DEFAULTS["app"]["data_dir"]) / "config.yaml"
        resolved = config_path or (_default_path if _default_path.is_file() else None)

        self.config_path: Optional[Path] = resolved or _default_path
        self.data: dict = copy.deepcopy(DEFAULTS)

        if resolved and resolved.is_file():
            try:
                with resolved.open("r", encoding="utf-8") as fh:
                    user_cfg = yaml.safe_load(fh) or {}
                self.data = _deep_merge(DEFAULTS, user_cfg)
                logger.info("User config loaded from %s", resolved)
            except Exception:
                logger.exception("Failed to load config from %s — using defaults", resolved)
        else:
            logger.info("No user config found; using defaults.")

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------
    @classmethod
    def init(cls, config_path: Optional[Path] = None) -> "Settings":
        """Create (or return) the global ``Settings`` instance.

        Args:
            config_path: Optional path to the user config YAML.

        Returns:
            The singleton ``Settings`` instance.
        """
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def get(cls) -> "Settings":
        """Return the existing singleton.

        Raises:
            RuntimeError: If ``init()`` has not been called yet.
        """
        if cls._instance is None:
            raise RuntimeError("Settings.init() must be called before Settings.get()")
        return cls._instance

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        """Retrieve a deeply-nested value using a sequence of keys.

        Args:
            *keys: Successive dictionary keys (e.g. ``"processing", "max_workers"``).
            default: Fallback if the key chain is missing.

        Returns:
            The value, or *default*.
        """
        node: Any = self.data
        for k in keys:
            if isinstance(node, dict):
                node = node.get(k)
            else:
                return default
        return node if node is not None else default

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self, path: Optional[Path] = None) -> None:
        """Write current settings to a YAML file.

        Args:
            path: Destination path.  Defaults to ``self.config_path``.
        """
        target = path or self.config_path
        if target is None:
            target = Path(self.data["app"]["data_dir"]) / "config.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            yaml.dump(self.data, fh, default_flow_style=False, sort_keys=False)
        logger.info("Configuration saved to %s", target)

    def update(self, section: str, key: str, value: Any) -> None:
        """Update a single configuration value and mark dirty.

        Args:
            section: Top-level config section (e.g. ``"processing"``).
            key: Key inside the section.
            value: New value.
        """
        if section in self.data and isinstance(self.data[section], dict):
            self.data[section][key] = value
            logger.debug("Config updated: %s.%s = %r", section, key, value)
        else:
            logger.warning("Unknown config section: %s", section)
