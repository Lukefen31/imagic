"""Unit tests for the LibraryScanner."""

from pathlib import Path

from imagic.services.scanner import LibraryScanner, ScanResult


class TestLibraryScanner:
    """Tests for ``LibraryScanner``."""

    def test_scan_finds_raw_files(self, sample_raw_dir: Path) -> None:
        """Scanner should discover .cr2, .nef, .arw but skip .txt."""
        scanner = LibraryScanner(recursive=True)
        result = scanner.scan(sample_raw_dir)

        assert isinstance(result, ScanResult)
        extensions = {p.suffix.lower() for p in result.discovered}
        assert ".cr2" in extensions
        assert ".nef" in extensions
        assert ".arw" in extensions
        assert ".txt" not in extensions

    def test_scan_counts_correct(self, sample_raw_dir: Path) -> None:
        """Should find 4 RAW files (3 top-level + 1 in subfolder)."""
        scanner = LibraryScanner(recursive=True)
        result = scanner.scan(sample_raw_dir)
        assert len(result.discovered) == 4
        assert len(result.new_files) == 4
        assert len(result.skipped) == 0

    def test_scan_skips_known(self, sample_raw_dir: Path) -> None:
        """Files already in the known set should go to ``skipped``."""
        scanner = LibraryScanner(recursive=True)

        # First scan to get paths.
        first = scanner.scan(sample_raw_dir)
        known = {str(p) for p in first.new_files}

        # Second scan with known paths.
        second = scanner.scan(sample_raw_dir, known_paths=known)
        assert len(second.new_files) == 0
        assert len(second.skipped) == 4

    def test_scan_non_recursive(self, sample_raw_dir: Path) -> None:
        """Non-recursive scan should miss the subfolder file."""
        scanner = LibraryScanner(recursive=False)
        result = scanner.scan(sample_raw_dir)
        assert len(result.discovered) == 3  # only top-level

    def test_scan_invalid_directory(self, tmp_path: Path) -> None:
        """Scanning a non-existent path should produce an error, not crash."""
        scanner = LibraryScanner()
        result = scanner.scan(tmp_path / "does_not_exist")
        assert len(result.errors) > 0
        assert len(result.discovered) == 0
