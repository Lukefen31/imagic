"""Library directory scanner.

Walks one or more directories, discovers RAW image files (using ``pathlib``
for cross-platform safety), checks the database for already-known files,
and returns a list of *new* files to ingest.

Threading
---------
The scanner itself is a plain synchronous function.  It is designed to be
submitted to the ``TaskQueue`` so scanning never blocks the UI.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

from imagic.models.enums import SupportedFormat
from imagic.utils.path_utils import normalise

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Container for the results of a single directory scan.

    Attributes:
        root: The directory that was scanned.
        discovered: All image files found on disk.
        new_files: Files not yet present in the database.
        skipped: Files that were already imported.
        errors: Paths that could not be accessed (permission denied, etc.).
    """

    root: Path
    discovered: List[Path] = field(default_factory=list)
    new_files: List[Path] = field(default_factory=list)
    skipped: List[Path] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class LibraryScanner:
    """Discovers RAW image files on disk.

    Args:
        extensions: Set of lowercase extensions (with leading dot) to accept.
            Defaults to ``SupportedFormat.raw_extensions()``.
        recursive: Walk subdirectories.
        follow_symlinks: Follow symbolic links while walking.
    """

    def __init__(
        self,
        extensions: Optional[Set[str]] = None,
        recursive: bool = True,
        follow_symlinks: bool = False,
    ) -> None:
        self._extensions: Set[str] = extensions or SupportedFormat.raw_extensions()
        self._recursive: bool = recursive
        self._follow_symlinks: bool = follow_symlinks

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def scan(
        self,
        directory: str | Path,
        known_paths: Optional[Set[str]] = None,
    ) -> ScanResult:
        """Scan *directory* for image files.

        Args:
            directory: Root directory to scan.
            known_paths: Set of absolute-path strings already present in the
                database.  Any discovered file whose resolved path is in this
                set will be placed in ``ScanResult.skipped``.

        Returns:
            A ``ScanResult`` summarising the scan.
        """
        root = normalise(directory)
        known = known_paths or set()
        result = ScanResult(root=root)

        if not root.is_dir():
            msg = f"Scan target is not a directory: {root}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        logger.info("Scanning %s (recursive=%s) …", root, self._recursive)

        try:
            if self._recursive:
                self._walk_recursive(root, known, result)
            else:
                self._walk_flat(root, known, result)
        except Exception as exc:
            msg = f"Unexpected error scanning {root}: {exc}"
            logger.exception(msg)
            result.errors.append(msg)

        logger.info(
            "Scan complete: %d found, %d new, %d skipped, %d errors.",
            len(result.discovered),
            len(result.new_files),
            len(result.skipped),
            len(result.errors),
        )
        return result

    def scan_multiple(
        self,
        directories: List[str | Path],
        known_paths: Optional[Set[str]] = None,
    ) -> List[ScanResult]:
        """Scan several directories.

        Args:
            directories: List of root paths.
            known_paths: Shared set of known absolute-path strings.

        Returns:
            One ``ScanResult`` per directory.
        """
        return [self.scan(d, known_paths) for d in directories]

    # ------------------------------------------------------------------
    # Internal walkers
    # ------------------------------------------------------------------
    def _walk_recursive(
        self, root: Path, known: Set[str], result: ScanResult
    ) -> None:
        """Use ``os.walk`` for efficient recursive traversal."""
        for dirpath_str, _dirnames, filenames in os.walk(
            root, followlinks=self._follow_symlinks
        ):
            dirpath = Path(dirpath_str)
            for fname in filenames:
                self._process_file(dirpath / fname, known, result)

    def _walk_flat(
        self, root: Path, known: Set[str], result: ScanResult
    ) -> None:
        """Non-recursive: iterate only the immediate children of *root*."""
        try:
            for entry in root.iterdir():
                if entry.is_file():
                    self._process_file(entry, known, result)
        except PermissionError as exc:
            msg = f"Permission denied: {root} — {exc}"
            logger.warning(msg)
            result.errors.append(msg)

    def _process_file(
        self, path: Path, known: Set[str], result: ScanResult
    ) -> None:
        """Check a single file against the extension filter and known set."""
        try:
            if path.suffix.lower() not in self._extensions:
                return  # not an image file we care about

            resolved = normalise(path)
            result.discovered.append(resolved)

            if str(resolved) in known:
                result.skipped.append(resolved)
            else:
                result.new_files.append(resolved)

        except PermissionError as exc:
            msg = f"Cannot access {path}: {exc}"
            logger.warning(msg)
            result.errors.append(msg)
        except Exception as exc:
            msg = f"Error processing {path}: {exc}"
            logger.warning(msg)
            result.errors.append(msg)
