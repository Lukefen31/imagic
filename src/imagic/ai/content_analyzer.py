"""Content analyser — optional CLIP-based semantic tagging.

This module uses OpenAI's CLIP model (via ``transformers`` + ``torch``) to
produce content labels and a "relevance" score.  It is **optional**: if the
dependencies are not installed (e.g. no GPU, limited disk), the analyser
gracefully returns an error result and the rest of the pipeline continues.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from imagic.ai.base_analyzer import AnalysisResult, BaseAnalyzer

logger = logging.getLogger(__name__)

# Default candidate labels for zero-shot classification.
DEFAULT_LABELS: List[str] = [
    "landscape",
    "portrait",
    "architecture",
    "wildlife",
    "macro",
    "street photography",
    "night photography",
    "sports",
    "food",
    "abstract",
]


class ContentAnalyzer(BaseAnalyzer):
    """Zero-shot CLIP image classifier.

    On first use ``load_model()`` downloads / loads the CLIP model
    (~400 MB).  Set ``enabled=False`` to skip entirely in pipelines
    where speed matters more than content tags.

    Args:
        model_name: Hugging Face model identifier.
        candidate_labels: List of textual categories.
        enabled: If ``False``, ``analyse`` immediately returns a no-op result.
    """

    def __init__(
        self,
        model_name: str = "openai/clip-vit-base-patch32",
        candidate_labels: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> None:
        self._model_name = model_name
        self._labels = candidate_labels or DEFAULT_LABELS
        self._enabled = enabled
        self._processor = None
        self._model = None

    @property
    def name(self) -> str:  # noqa: D401
        return "ContentAnalyzer (CLIP)"

    def load_model(self) -> None:
        """Download and initialise the CLIP model + processor."""
        if not self._enabled:
            return
        try:
            from transformers import CLIPModel, CLIPProcessor

            self._processor = CLIPProcessor.from_pretrained(self._model_name)
            self._model = CLIPModel.from_pretrained(self._model_name)
            logger.info("CLIP model loaded: %s", self._model_name)
        except ImportError:
            logger.warning(
                "transformers / torch not installed — ContentAnalyzer disabled."
            )
            self._enabled = False
        except Exception as exc:
            logger.error("Failed to load CLIP model: %s", exc)
            self._enabled = False

    def unload_model(self) -> None:
        """Free GPU / CPU memory."""
        self._model = None
        self._processor = None
        super().unload_model()

    def analyse(self, file_path: Path) -> AnalysisResult:
        """Classify a single image.

        Args:
            file_path: Path to the image.

        Returns:
            ``AnalysisResult`` with the top label in ``labels["content"]``
            and the confidence as ``score``.
        """
        if not self._enabled:
            return AnalysisResult(
                file_path=file_path,
                error="ContentAnalyzer is disabled (dependencies missing).",
            )
        self.ensure_model()
        try:
            import torch
            from PIL import Image

            image = Image.open(file_path).convert("RGB")
            inputs = self._processor(
                text=self._labels,
                images=image,
                return_tensors="pt",
                padding=True,
            )
            with torch.no_grad():
                outputs = self._model(**inputs)

            logits = outputs.logits_per_image[0]
            probs = logits.softmax(dim=-1).cpu().numpy()

            sorted_indices = probs.argsort()[::-1]
            top_label = self._labels[sorted_indices[0]]
            top_score = float(probs[sorted_indices[0]])
            all_scores = {
                self._labels[i]: round(float(probs[i]), 4) for i in sorted_indices[:5]
            }

            return AnalysisResult(
                file_path=file_path,
                score=round(top_score, 4),
                labels={"content": top_label, "scores": all_scores},
            )
        except Exception as exc:
            logger.error("ContentAnalyzer failed for %s: %s", file_path, exc)
            return AnalysisResult(file_path=file_path, error=str(exc))
