from __future__ import annotations

from pathlib import Path
import tomllib

from imagic import __version__


def test_runtime_version_matches_pyproject() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject_path = repo_root / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        pyproject_data = tomllib.load(handle)

    project_version = pyproject_data["project"]["version"]
    assert __version__ == project_version
