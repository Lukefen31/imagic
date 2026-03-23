"""Perceptual quality scorer — learned IQA metrics via ``pyiqa``.

Uses pre-trained neural networks for no-reference image quality assessment.
These models are trained on human-annotated quality datasets and provide a
more perceptually-aligned score than hand-tuned signal-processing formulas.

Three metrics are combined for robustness:

* **CLIPIQA+** — CLIP-based quality predictor (~150 MB, fast).
* **MUSIQ** — Multi-scale IQA, handles variable input sizes (~30 MB).
* **TOPIQ_NR** — Top-performing no-reference metric (~80 MB).

All are **optional** — if ``torch`` or ``pyiqa`` is not installed, the
analyser gracefully returns an error result and the pipeline continues.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from imagic.ai.base_analyzer import AnalysisResult, BaseAnalyzer

logger = logging.getLogger(__name__)


class PerceptualScorer(BaseAnalyzer):
    """Combine multiple learned IQA metrics into a single perceptual score.

    The final score is a weighted average of the constituent metrics,
    normalised to [0, 1].  Each metric's weight can be overridden.

    Args:
        enabled: Set ``False`` to skip entirely (useful in builds without
            GPU or when speed is critical).
        metrics: Dict mapping pyiqa metric names to their weights.
            Defaults to a balanced selection.
    """

    _DEFAULT_METRICS: Dict[str, float] = {
        "clipiqa+": 0.40,
        "musiq": 0.30,
        "topiq_nr": 0.30,
    }

    def __init__(
        self,
        enabled: bool = True,
        metrics: Optional[Dict[str, float]] = None,
    ) -> None:
        self._enabled = enabled
        self._metric_weights = metrics or dict(self._DEFAULT_METRICS)
        self._models: Dict[str, object] = {}

    @property
    def name(self) -> str:  # noqa: D401
        return "PerceptualScorer (pyiqa)"

    def load_model(self) -> None:
        """Initialise pyiqa metric models."""
        if not self._enabled:
            return
        try:
            import pyiqa  # noqa: F811

            for metric_name in self._metric_weights:
                try:
                    model = pyiqa.create_metric(metric_name, device="cpu")
                    self._models[metric_name] = model
                    logger.info("Loaded pyiqa metric: %s", metric_name)
                except Exception as exc:
                    logger.warning(
                        "Failed to load pyiqa metric %s: %s", metric_name, exc,
                    )
            if not self._models:
                logger.warning("No pyiqa metrics loaded — PerceptualScorer disabled.")
                self._enabled = False
        except ImportError:
            logger.warning("pyiqa / torch not installed — PerceptualScorer disabled.")
            self._enabled = False

    def unload_model(self) -> None:
        """Free model memory."""
        self._models.clear()
        super().unload_model()

    def analyse(self, file_path: Path) -> AnalysisResult:
        """Score a single image using learned perceptual metrics.

        Returns:
            ``AnalysisResult`` with the combined perceptual score.
            ``labels`` contains individual metric scores.
        """
        if not self._enabled:
            return AnalysisResult(
                file_path=file_path,
                error="PerceptualScorer is disabled (dependencies missing).",
            )
        self.ensure_model()
        try:
            import torch
            from PIL import Image
            from torchvision import transforms

            img = Image.open(file_path).convert("RGB")

            # Common preprocessing — resize to max 512 on the long side
            # to keep inference fast while preserving quality signal.
            w, h = img.size
            max_side = max(w, h)
            if max_side > 512:
                scale = 512.0 / max_side
                img = img.resize(
                    (int(w * scale), int(h * scale)), Image.LANCZOS,
                )

            individual_scores: Dict[str, float] = {}
            weighted_sum = 0.0
            total_weight = 0.0

            for metric_name, weight in self._metric_weights.items():
                model = self._models.get(metric_name)
                if model is None:
                    continue
                try:
                    with torch.no_grad():
                        score_tensor = model(img)
                        raw_score = float(score_tensor.item())

                    # pyiqa scores vary by metric — normalise to [0, 1].
                    norm_score = self._normalise(metric_name, raw_score)
                    individual_scores[metric_name] = round(norm_score, 4)
                    weighted_sum += weight * norm_score
                    total_weight += weight
                except Exception as exc:
                    logger.debug(
                        "pyiqa metric %s failed for %s: %s",
                        metric_name, file_path, exc,
                    )

            if total_weight < 1e-6:
                return AnalysisResult(
                    file_path=file_path,
                    error="All pyiqa metrics failed.",
                )

            combined = weighted_sum / total_weight
            return AnalysisResult(
                file_path=file_path,
                score=round(combined, 4),
                labels={
                    "perceptual_scores": individual_scores,
                    "perceptual_combined": round(combined, 4),
                },
            )
        except Exception as exc:
            logger.error("PerceptualScorer failed for %s: %s", file_path, exc)
            return AnalysisResult(file_path=file_path, error=str(exc))

    @staticmethod
    def _normalise(metric_name: str, raw: float) -> float:
        """Map a raw pyiqa score to [0, 1].

        Different metrics have different native ranges. This mapping is
        calibrated empirically — scores below ``low`` map to 0, above
        ``high`` to 1, linear in between.
        """
        # CLIPIQA+ outputs ~[0, 1] already (higher = better).
        if "clipiqa" in metric_name:
            return max(0.0, min(1.0, raw))

        # MUSIQ outputs ~[0, 100] (higher = better).
        if "musiq" in metric_name:
            return max(0.0, min(1.0, raw / 100.0))

        # TOPIQ_NR outputs ~[0, 1] (higher = better).
        if "topiq" in metric_name:
            return max(0.0, min(1.0, raw))

        # Fallback — assume [0, 1] range.
        return max(0.0, min(1.0, raw))
