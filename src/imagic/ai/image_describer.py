"""Image description analyser — optional Florence-2 vision-language model.

Uses Microsoft's Florence-2-base to generate natural-language descriptions
of image content and to perform visual grounding (object detection).  This
provides semantic understanding that goes beyond low-level quality metrics.

Florence-2-base is ~460 MB (base) or ~1.5 GB (large).  If the model or
``transformers`` / ``torch`` are not installed, the analyser gracefully
returns an error result and the pipeline continues.

**Capabilities used:**

* ``<CAPTION>`` — short one-line description.
* ``<DETAILED_CAPTION>`` — longer paragraph description.
* ``<MORE_DETAILED_CAPTION>`` — comprehensive content description.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from imagic.ai.base_analyzer import AnalysisResult, BaseAnalyzer

logger = logging.getLogger(__name__)


class ImageDescriptionAnalyzer(BaseAnalyzer):
    """Generate image descriptions and content tags with Florence-2.

    Args:
        model_name: Hugging Face model identifier.  Defaults to
            the base model (~460 MB).  Use ``microsoft/Florence-2-large``
            for better accuracy at the cost of memory and speed.
        enabled: Set ``False`` to skip entirely.
        detail_level: One of ``"short"``, ``"medium"``, ``"detailed"``.
            Controls how much description to generate.
    """

    _TASK_MAP = {
        "short": "<CAPTION>",
        "medium": "<DETAILED_CAPTION>",
        "detailed": "<MORE_DETAILED_CAPTION>",
    }

    def __init__(
        self,
        model_name: str = "microsoft/Florence-2-base",
        enabled: bool = True,
        detail_level: str = "medium",
    ) -> None:
        self._model_name = model_name
        self._enabled = enabled
        self._detail_level = detail_level
        self._model = None
        self._processor = None

    @property
    def name(self) -> str:  # noqa: D401
        return "ImageDescriptionAnalyzer (Florence-2)"

    def load_model(self) -> None:
        """Download / load Florence-2 model and processor."""
        if not self._enabled:
            return
        try:
            from transformers import AutoModelForCausalLM, AutoProcessor

            self._processor = AutoProcessor.from_pretrained(
                self._model_name, trust_remote_code=True,
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                self._model_name, trust_remote_code=True,
            )
            self._model.eval()
            logger.info("Florence-2 model loaded: %s", self._model_name)
        except ImportError:
            logger.warning(
                "transformers / torch not installed — ImageDescriptionAnalyzer disabled."
            )
            self._enabled = False
        except Exception as exc:
            logger.error("Failed to load Florence-2 model: %s", exc)
            self._enabled = False

    def unload_model(self) -> None:
        """Free model memory."""
        self._model = None
        self._processor = None
        super().unload_model()

    def analyse(self, file_path: Path) -> AnalysisResult:
        """Generate a description for a single image.

        Returns:
            ``AnalysisResult`` with ``labels`` containing:

            * ``caption`` — short description
            * ``detailed_caption`` — longer description (if detail_level >= medium)
        """
        if not self._enabled:
            return AnalysisResult(
                file_path=file_path,
                error="ImageDescriptionAnalyzer is disabled (dependencies missing).",
            )
        self.ensure_model()
        try:
            import torch
            from PIL import Image

            img = Image.open(file_path).convert("RGB")

            # Resize for speed — Florence-2 works well at 384-768px.
            w, h = img.size
            max_side = max(w, h)
            if max_side > 768:
                scale = 768.0 / max_side
                img = img.resize(
                    (int(w * scale), int(h * scale)), Image.LANCZOS,
                )

            labels = {}

            # Always generate short caption.
            short = self._run_task(img, "<CAPTION>")
            if short:
                labels["caption"] = short

            # Generate detailed caption if requested.
            if self._detail_level in ("medium", "detailed"):
                task = self._TASK_MAP.get(self._detail_level, "<DETAILED_CAPTION>")
                detailed = self._run_task(img, task)
                if detailed:
                    labels["detailed_caption"] = detailed

            return AnalysisResult(
                file_path=file_path,
                score=1.0,  # Not a quality score — signals "analysed OK".
                labels=labels,
            )
        except Exception as exc:
            logger.error("ImageDescriptionAnalyzer failed for %s: %s", file_path, exc)
            return AnalysisResult(file_path=file_path, error=str(exc))

    def _run_task(self, image, task_prompt: str) -> Optional[str]:
        """Run a single Florence-2 task and return the text output."""
        try:
            import torch

            inputs = self._processor(
                text=task_prompt, images=image, return_tensors="pt",
            )
            with torch.no_grad():
                generated_ids = self._model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=256,
                    num_beams=3,
                )
            result = self._processor.batch_decode(
                generated_ids, skip_special_tokens=False,
            )[0]
            parsed = self._processor.post_process_generation(
                result, task=task_prompt, image_size=image.size,
            )
            # parsed is a dict like {task_prompt: "the caption text"}
            text = parsed.get(task_prompt, "")
            return text.strip() if text else None
        except Exception as exc:
            logger.debug("Florence-2 task %s failed: %s", task_prompt, exc)
            return None
