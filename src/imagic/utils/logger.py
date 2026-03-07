"""Centralised logging configuration.

Call ``setup_logging()`` once at application startup.  After that, every module
can simply use ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Optional

import yaml


def setup_logging(
    config_path: Optional[Path] = None,
    log_file: Optional[Path] = None,
    level: str = "INFO",
) -> None:
    """Configure the root logger.

    If *config_path* points to a valid YAML logging config it is loaded
    via ``logging.config.dictConfig``.  Otherwise a sensible console +
    rotating-file setup is created programmatically.

    Args:
        config_path: Path to a ``logging.yaml`` file.
        log_file: Override for the file handler's target path.
        level: Fallback root log level when no config file is found.
    """
    if config_path and config_path.is_file():
        with config_path.open("r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)

        # Optionally override the file handler path.
        if log_file and "handlers" in cfg and "file" in cfg["handlers"]:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            cfg["handlers"]["file"]["filename"] = str(log_file)

        logging.config.dictConfig(cfg)
        logging.getLogger(__name__).info("Logging configured from %s", config_path)
        return

    # Fallback: simple stream + file handler.
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not root.handlers:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler

        fh = RotatingFileHandler(
            str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    logging.getLogger(__name__).info("Logging initialised (level=%s).", level)
