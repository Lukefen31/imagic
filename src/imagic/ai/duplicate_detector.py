"""Duplicate detector — perceptual hashing for near-duplicate detection.

Uses ``imagehash`` (average hash / perceptual hash) to produce a compact
fingerprint for each image.  Images whose Hamming distance is below a
configurable threshold are flagged as duplicates.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from imagic.ai.base_analyzer import AnalysisResult, BaseAnalyzer

logger = logging.getLogger(__name__)


class DuplicateDetector(BaseAnalyzer):
    """Perceptual-hash based duplicate detector.

    Args:
        hash_size: Dimension of the hash grid (default 8 → 64-bit hash).
        threshold: Maximum Hamming distance to consider two images duplicates.
    """

    def __init__(self, hash_size: int = 8, threshold: int = 5) -> None:
        self._hash_size = hash_size
        self._threshold = threshold

    @property
    def name(self) -> str:  # noqa: D401
        return "DuplicateDetector"

    def analyse(self, file_path: Path) -> AnalysisResult:
        """Compute the perceptual hash for a single image.

        Args:
            file_path: Path to the image.

        Returns:
            ``AnalysisResult`` with the hex hash stored in
            ``labels["phash"]``.
        """
        self.ensure_model()
        try:
            import imagehash
            from PIL import Image

            img = Image.open(file_path)
            phash = imagehash.phash(img, hash_size=self._hash_size)

            return AnalysisResult(
                file_path=file_path,
                score=1.0,  # not a quality score — always 1 for "analysed".
                labels={"phash": str(phash)},
            )
        except Exception as exc:
            logger.error("DuplicateDetector failed for %s: %s", file_path, exc)
            return AnalysisResult(file_path=file_path, error=str(exc))

    def find_duplicates(
        self,
        hash_map: Dict[str, str],
    ) -> List[Tuple[str, str, int]]:
        """Compare all pairs and return those within the threshold.

        Args:
            hash_map: Mapping of ``file_path → hex_hash_string``.

        Returns:
            List of ``(path_a, path_b, distance)`` tuples.
        """
        try:
            import imagehash

            entries = [
                (path, imagehash.hex_to_hash(h)) for path, h in hash_map.items()
            ]
            duplicates: List[Tuple[str, str, int]] = []

            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    dist = entries[i][1] - entries[j][1]
                    if dist <= self._threshold:
                        duplicates.append((entries[i][0], entries[j][0], dist))

            logger.info("Duplicate scan: %d pairs within threshold %d.", len(duplicates), self._threshold)
            return duplicates
        except Exception as exc:
            logger.error("Duplicate comparison failed: %s", exc)
            return []

    def group_duplicates(
        self,
        hash_map: Dict[str, str],
    ) -> List[List[str]]:
        """Group file paths into clusters of duplicates.

        Args:
            hash_map: Mapping of ``file_path → hex_hash_string``.

        Returns:
            List of groups, each containing >= 2 duplicate paths.
        """
        pairs = self.find_duplicates(hash_map)
        # Union-Find style grouping.
        parent: Dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for a, b, _ in pairs:
            union(a, b)

        groups: Dict[str, List[str]] = defaultdict(list)
        for path in hash_map:
            groups[find(path)].append(path)

        return [g for g in groups.values() if len(g) > 1]
