"""Abstract base class for all AI image analysers.

Every concrete analyser (quality scorer, duplicate detector, content tagger)
must implement the ``analyse`` method.  This abstraction layer means you can
swap models later without touching any other file.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Standard container returned by every analyser.

    Attributes:
        file_path: The image that was analysed.
        score: Normalised quality / relevance score (0.0–1.0).
        labels: Free-form key-value metadata produced by the analyser.
        error: Error description if analysis failed.
    """

    file_path: Path
    score: Optional[float] = None
    labels: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """``True`` if the analysis completed without error."""
        return self.error is None


class BaseAnalyzer(ABC):
    """Abstract interface for an image analyser.

    Subclasses must implement:
    * ``analyse(file_path)`` — analyse a single image.
    * ``name`` (property)   — human-readable analyser name.

    Optionally override:
    * ``load_model()`` — called once before the first ``analyse`` call.
    * ``unload_model()`` — called on shutdown to free GPU / RAM.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this analyser."""
        ...

    def ensure_model(self) -> None:
        """Load the model if it has not been loaded yet."""
        if not getattr(self, "_model_loaded", False):
            logger.info("Loading model for %s…", self.name)
            self.load_model()
            self._model_loaded = True

    def load_model(self) -> None:
        """Override to perform one-time, heavy model loading."""

    def unload_model(self) -> None:
        """Override to free resources on shutdown."""
        self._model_loaded = False

    @abstractmethod
    def analyse(self, file_path: Path) -> AnalysisResult:
        """Analyse a single image file.

        Args:
            file_path: Absolute path to the image.

        Returns:
            An ``AnalysisResult`` with at least the ``score`` populated.
        """
        ...

    def analyse_batch(self, file_paths: list[Path]) -> list[AnalysisResult]:
        """Analyse multiple images sequentially.

        Override for batch-optimised implementations.

        Args:
            file_paths: List of image paths.

        Returns:
            One ``AnalysisResult`` per input path.
        """
        self.ensure_model()
        results: list[AnalysisResult] = []
        for fp in file_paths:
            try:
                results.append(self.analyse(fp))
            except Exception as exc:
                logger.error("Analysis failed for %s: %s", fp, exc)
                results.append(AnalysisResult(file_path=fp, error=str(exc)))
        return results
