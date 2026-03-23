"""Duplicate detector — perceptual hashing + burst-shot clustering.

Uses ``imagehash`` (perceptual hash) for visual similarity, combined with
EXIF timestamp analysis for burst-shot detection.

**Two-phase approach:**

1. **Time-based burst clustering** — photos taken within ``burst_window``
   seconds of each other (transitively) are grouped as burst duplicates.
   Within a burst, only wildly different frames are split out.
2. **Hash-based duplicate detection** — photos with Hamming distance below
   ``threshold`` are grouped regardless of time.

Both phases feed into a single union-find, so a burst group and a hash
group that share a member are merged automatically.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from imagic.ai.base_analyzer import AnalysisResult, BaseAnalyzer

logger = logging.getLogger(__name__)


class DuplicateDetector(BaseAnalyzer):
    """Perceptual-hash + burst-time duplicate detector.

    Args:
        hash_size: Dimension of the hash grid (default 8 → 64-bit hash).
        threshold: Maximum Hamming distance for hash-only duplicate pairs.
        burst_window: Max seconds between consecutive shots to be a burst.
        burst_hash_limit: Max Hamming distance within a burst group.  Pairs
            inside a burst whose distance exceeds this are NOT linked
            (prevents merging truly different scenes that happen to be close
            in time).
    """

    def __init__(
        self,
        hash_size: int = 8,
        threshold: int = 8,
        burst_window: float = 2.0,
        burst_hash_limit: int = 18,
    ) -> None:
        self._hash_size = hash_size
        self._threshold = threshold
        self._burst_window = burst_window
        self._burst_hash_limit = burst_hash_limit

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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _union_find_ops():
        """Return fresh find / union closures over a shared parent dict."""
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

        return find, union, parent

    def _burst_groups(
        self,
        time_map: Dict[str, Optional[datetime]],
    ) -> List[List[str]]:
        """Build transitive burst groups from timestamps.

        Photos are sorted by timestamp.  Consecutive photos within
        ``burst_window`` seconds are chained together.
        """
        timed = [
            (path, ts)
            for path, ts in time_map.items()
            if ts is not None
        ]
        if not timed:
            return []

        timed.sort(key=lambda x: x[1])

        find, union, parent = self._union_find_ops()

        for i in range(1, len(timed)):
            gap = abs((timed[i][1] - timed[i - 1][1]).total_seconds())
            if gap <= self._burst_window:
                union(timed[i][0], timed[i - 1][0])

        groups: Dict[str, List[str]] = defaultdict(list)
        for path, _ in timed:
            groups[find(path)].append(path)

        return [g for g in groups.values() if len(g) > 1]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_duplicates(
        self,
        hash_map: Dict[str, str],
        time_map: Optional[Dict[str, Optional[datetime]]] = None,
    ) -> List[Tuple[str, str, int]]:
        """Return duplicate pairs using hash similarity only.

        Args:
            hash_map: Mapping of ``file_path → hex_hash_string``.
            time_map: Unused here, kept for API compat.

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

            logger.info(
                "Hash scan: %d pairs within threshold %d.",
                len(duplicates), self._threshold,
            )
            return duplicates
        except Exception as exc:
            logger.error("Duplicate comparison failed: %s", exc)
            return []

    def group_duplicates(
        self,
        hash_map: Dict[str, str],
        time_map: Optional[Dict[str, Optional[datetime]]] = None,
    ) -> List[List[str]]:
        """Group file paths into clusters of duplicates.

        Combines **time-based burst clustering** with **hash-based
        similarity**.  Within burst groups, pairs whose hash distance
        exceeds ``burst_hash_limit`` are split apart to avoid merging
        genuinely different scenes.

        Args:
            hash_map: Mapping of ``file_path → hex_hash_string``.
            time_map: Optional mapping of ``file_path → datetime``.

        Returns:
            List of groups, each containing >= 2 duplicate paths.
        """
        import imagehash

        find, union, _parent = self._union_find_ops()

        # Phase 1 — time-based burst pairs.
        burst_groups = self._burst_groups(time_map or {})
        burst_pairs = 0

        hash_cache: Dict[str, object] = {}
        for path, h in hash_map.items():
            hash_cache[path] = imagehash.hex_to_hash(h)

        for group in burst_groups:
            # Within a burst group, link consecutive photos if their hash
            # distance is within the generous burst_hash_limit.
            for i in range(1, len(group)):
                ha = hash_cache.get(group[i - 1])
                hb = hash_cache.get(group[i])
                if ha is not None and hb is not None:
                    dist = ha - hb
                    if dist <= self._burst_hash_limit:
                        union(group[i - 1], group[i])
                        burst_pairs += 1
                else:
                    # No hash → still link by time
                    union(group[i - 1], group[i])
                    burst_pairs += 1

        # Phase 2 — hash-only pairs (catches duplicates far apart in time).
        hash_pairs = self.find_duplicates(hash_map)
        for a, b, _ in hash_pairs:
            union(a, b)

        logger.info(
            "Duplicate grouping: %d burst links + %d hash pairs.",
            burst_pairs, len(hash_pairs),
        )

        groups: Dict[str, List[str]] = defaultdict(list)
        for path in hash_map:
            groups[find(path)].append(path)

        return [g for g in groups.values() if len(g) > 1]

    @staticmethod
    def rank_burst_group(
        group: List[str],
        score_map: Dict[str, float],
    ) -> List[str]:
        """Sort a duplicate/burst group by quality score, best first.

        Args:
            group: File paths belonging to the same duplicate cluster.
            score_map: Mapping of ``file_path → quality_score``.  Paths
                absent from the map are treated as score 0 (worst).

        Returns:
            The same paths ordered from highest to lowest quality score.
        """
        return sorted(group, key=lambda p: score_map.get(p, 0.0), reverse=True)
