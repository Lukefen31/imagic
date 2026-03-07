"""Pytest fixtures shared across all test modules."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from imagic.config.settings import Settings
from imagic.models.database import Base, DatabaseManager


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for test files."""
    return tmp_path


@pytest.fixture()
def db(tmp_path: Path):
    """Provide a fresh in-memory-like SQLite database for each test.

    Yields:
        A ``DatabaseManager`` instance.  Cleaned up after the test.
    """
    db_path = tmp_path / "test.db"
    manager = DatabaseManager(db_path)
    yield manager
    manager.close()
    # Reset singleton so the next test gets a fresh DB.
    DatabaseManager._instance = None


@pytest.fixture()
def settings(tmp_path: Path):
    """Provide a ``Settings`` instance backed by defaults.

    Yields:
        A ``Settings`` singleton.  Reset after the test.
    """
    s = Settings()
    # Override data_dir to tmp
    s.data["app"]["data_dir"] = str(tmp_path)
    s.data["app"]["database"] = str(tmp_path / "test.db")
    Settings._instance = s
    yield s
    Settings._instance = None


@pytest.fixture()
def sample_raw_dir(tmp_path: Path) -> Path:
    """Create a directory with fake RAW files for scanner tests.

    Returns:
        Path to a directory containing dummy ``.cr2`` and ``.nef`` files.
    """
    raw_dir = tmp_path / "photos"
    raw_dir.mkdir()
    for name in ["IMG_001.cr2", "IMG_002.cr2", "DSC_003.nef", "readme.txt"]:
        (raw_dir / name).write_bytes(b"\x00" * 64)

    sub = raw_dir / "subfolder"
    sub.mkdir()
    (sub / "IMG_004.arw").write_bytes(b"\x00" * 64)
    return raw_dir
