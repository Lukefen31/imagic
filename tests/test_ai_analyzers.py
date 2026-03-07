"""Unit tests for AI analysers (quality scorer, duplicate detector)."""

from pathlib import Path

from imagic.ai.base_analyzer import AnalysisResult, BaseAnalyzer
from imagic.ai.quality_scorer import QualityScorer
from imagic.ai.duplicate_detector import DuplicateDetector


class TestBaseAnalyzer:
    """Tests for the abstract ``BaseAnalyzer``."""

    def test_cannot_instantiate_directly(self) -> None:
        """BaseAnalyzer is abstract and should not be instantiated."""
        import pytest
        with pytest.raises(TypeError):
            BaseAnalyzer()  # type: ignore[abstract]

    def test_analysis_result_ok(self) -> None:
        """AnalysisResult.ok should be True when error is None."""
        r = AnalysisResult(file_path=Path("/tmp/test.jpg"), score=0.8)
        assert r.ok

    def test_analysis_result_error(self) -> None:
        """AnalysisResult.ok should be False when error is set."""
        r = AnalysisResult(file_path=Path("/tmp/test.jpg"), error="boom")
        assert not r.ok


class TestQualityScorer:
    """Tests for ``QualityScorer``."""

    def test_name(self) -> None:
        assert QualityScorer().name == "QualityScorer"

    def test_analyse_returns_valid_score(self, tmp_path: Path) -> None:
        """analyse should return a result with a float score in [0, 1]."""
        import numpy as np
        from PIL import Image

        # Create a real test image (grayscale noise).
        arr = (np.random.rand(200, 200) * 255).astype(np.uint8)
        img = Image.fromarray(arr, mode="L")
        img_path = tmp_path / "test_quality.jpg"
        img.save(str(img_path))

        scorer = QualityScorer()
        result = scorer.analyse(img_path)

        assert result.ok
        assert result.score is not None
        assert 0.0 <= result.score <= 1.0
        assert "sharpness" in result.labels
        assert "exposure" in result.labels
        assert "detail" in result.labels


class TestDuplicateDetector:
    """Tests for ``DuplicateDetector``."""

    def test_name(self) -> None:
        assert DuplicateDetector().name == "DuplicateDetector"

    def test_find_duplicates_identical_hashes(self) -> None:
        """Identical hashes should be detected as duplicates."""
        detector = DuplicateDetector(threshold=5)
        hash_map = {
            "/a.jpg": "abcdef1234567890",
            "/b.jpg": "abcdef1234567890",
            "/c.jpg": "0000000000000000",
        }
        dupes = detector.find_duplicates(hash_map)
        # a and b have distance 0 → should appear.
        paths_in_dupes = set()
        for a, b, dist in dupes:
            paths_in_dupes.add(a)
            paths_in_dupes.add(b)
        assert "/a.jpg" in paths_in_dupes
        assert "/b.jpg" in paths_in_dupes

    def test_group_duplicates(self) -> None:
        """group_duplicates should cluster identical hashes."""
        detector = DuplicateDetector(threshold=0)
        hash_map = {
            "/a.jpg": "aaaaaaaaaaaaaaaa",
            "/b.jpg": "aaaaaaaaaaaaaaaa",
            "/c.jpg": "bbbbbbbbbbbbbbbb",
        }
        groups = detector.group_duplicates(hash_map)
        assert len(groups) == 1  # only a+b
        assert len(groups[0]) == 2
