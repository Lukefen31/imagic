"""Feedback learner — adapts culling and editing to the user's preferences.

Tracks two types of manual corrections and uses them to improve future
automated decisions:

**Culling feedback** — when the user overrides a KEEP/TRASH decision:
  - Records per-metric scores alongside the user's final decision.
  - Over time, learns adjusted metric weights and thresholds so the
    scorer makes fewer mistakes.

**Editing feedback** — when the user manually re-edits a photo:
  - Records the delta between auto-computed parameters and the user's
    chosen values, grouped by scene characteristics (ISO range, brightness
    bucket, color grade).
  - Over time, learns preferred baseline shifts so the PP3 generator
    starts closer to the user's taste.

The learned model is stored at ``~/.imagic/feedback_model.json``.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_MODEL_PATH = Path.home() / ".imagic" / "feedback_model.json"

# Minimum samples before we start trusting learned adjustments
_MIN_CULL_SAMPLES = 5
_MIN_EDIT_SAMPLES = 3


def _iso_bucket(iso: Optional[int]) -> str:
    """Map ISO to a coarse bucket for grouping edit preferences."""
    if iso is None:
        return "unknown"
    if iso <= 400:
        return "low"
    if iso <= 1600:
        return "medium"
    if iso <= 6400:
        return "high"
    return "very_high"


def _brightness_bucket(mean_brightness: float) -> str:
    """Map mean brightness (0-255) to a coarse bucket."""
    if mean_brightness < 60:
        return "dark"
    if mean_brightness < 140:
        return "mid"
    return "bright"


class FeedbackLearner:
    """Persistent feedback learner that adapts to user preferences.

    Call ``record_cull_feedback`` when the user overrides a culling
    decision and ``record_edit_feedback`` when they manually re-edit.

    Call ``get_score_adjustments`` to get learned weight/threshold
    adjustments, and ``get_edit_preferences`` to get learned editing
    parameter shifts.

    The model auto-saves after each recording.
    """

    def __init__(self, model_path: Optional[Path] = None) -> None:
        self._path = model_path or _MODEL_PATH
        self._data: Dict = {
            "version": 2,
            "cull_samples": [],
            "edit_samples": [],
            "learned_weights": {},
            "learned_thresholds": {},
            "learned_edit_prefs": {},
        }
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.is_file():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict) and stored.get("version", 0) >= 2:
                    self._data = stored
                    logger.info(
                        "Loaded feedback model: %d cull, %d edit samples",
                        len(self._data.get("cull_samples", [])),
                        len(self._data.get("edit_samples", [])),
                    )
            except Exception:
                logger.warning("Could not load feedback model, starting fresh")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            logger.warning("Could not save feedback model to %s", self._path)

    # ------------------------------------------------------------------
    # Culling feedback
    # ------------------------------------------------------------------

    def record_cull_feedback(
        self,
        file_name: str,
        auto_decision: str,
        user_decision: str,
        quality_score: float,
        metric_scores: Dict[str, float],
        iso: Optional[int] = None,
        mean_brightness: float = 128.0,
    ) -> None:
        """Record that the user disagreed with an auto cull decision.

        Args:
            file_name: Photo filename (for debugging, not used in learning).
            auto_decision: What the system chose ("keep"/"trash"/"culled").
            user_decision: What the user chose ("keep"/"trash"/"culled").
            quality_score: The overall quality score (0-1).
            metric_scores: Dict of per-metric scores, e.g.
                {"sharpness": 0.75, "exposure": 0.51, ...}.
            iso: EXIF ISO value if available.
            mean_brightness: Mean pixel brightness (0-255).
        """
        if auto_decision == user_decision:
            return  # no correction needed, nothing to learn

        sample = {
            "auto": auto_decision,
            "user": user_decision,
            "score": round(quality_score, 4),
            "metrics": {k: round(v, 4) for k, v in metric_scores.items()},
            "iso_bucket": _iso_bucket(iso),
            "brightness_bucket": _brightness_bucket(mean_brightness),
        }

        # Keep a rolling window of recent corrections (cap at 500)
        samples = self._data.setdefault("cull_samples", [])
        samples.append(sample)
        if len(samples) > 500:
            self._data["cull_samples"] = samples[-500:]

        self._recompute_cull_model()
        self._save()

        logger.info(
            "Cull feedback: %s auto=%s → user=%s (score=%.3f)",
            file_name, auto_decision, user_decision, quality_score,
        )

    def record_cull_confirmation(
        self,
        quality_score: float,
        metric_scores: Dict[str, float],
        decision: str,
        iso: Optional[int] = None,
        mean_brightness: float = 128.0,
    ) -> None:
        """Record that the user confirmed (did not change) a decision.

        Confirmations reinforce the current model.  We record them at a
        lower rate (1 in 5) to avoid drowning out corrections.
        """
        samples = self._data.get("cull_samples", [])
        confirmations = sum(1 for s in samples if s.get("auto") == s.get("user"))
        if confirmations > len(samples) * 0.6:
            return  # already have enough confirmation weight

        sample = {
            "auto": decision,
            "user": decision,  # same = confirmation
            "score": round(quality_score, 4),
            "metrics": {k: round(v, 4) for k, v in metric_scores.items()},
            "iso_bucket": _iso_bucket(iso),
            "brightness_bucket": _brightness_bucket(mean_brightness),
        }
        samples.append(sample)
        if len(samples) > 500:
            self._data["cull_samples"] = samples[-500:]

        self._recompute_cull_model()
        self._save()

    # ------------------------------------------------------------------
    # Editing feedback
    # ------------------------------------------------------------------

    def record_edit_feedback(
        self,
        file_name: str,
        overrides: Dict,
        iso: Optional[int] = None,
        mean_brightness: float = 128.0,
        color_grade: str = "natural",
    ) -> None:
        """Record a manual editing correction.

        Args:
            file_name: Photo filename.
            overrides: The user's manual override dict, e.g.
                {"exposure": -20, "contrast": 15, "saturation": 0, ...}.
            iso: EXIF ISO value.
            mean_brightness: Mean brightness (0-255).
            color_grade: The color grade applied.
        """
        # Only record non-zero adjustments (user actively changed something)
        active = {k: v for k, v in overrides.items()
                  if k != "color_grade" and v != 0
                  and not (k == "sharpness" and v == 100)}
        if not active and overrides.get("color_grade") == color_grade:
            return  # no meaningful change

        sample = {
            "overrides": overrides,
            "iso_bucket": _iso_bucket(iso),
            "brightness_bucket": _brightness_bucket(mean_brightness),
            "color_grade": color_grade,
        }

        samples = self._data.setdefault("edit_samples", [])
        samples.append(sample)
        if len(samples) > 500:
            self._data["edit_samples"] = samples[-500:]

        self._recompute_edit_model()
        self._save()

        logger.info(
            "Edit feedback: %s overrides=%s iso=%s bright=%.0f",
            file_name, active, _iso_bucket(iso), mean_brightness,
        )

    # ------------------------------------------------------------------
    # Learned adjustments — culling
    # ------------------------------------------------------------------

    def _recompute_cull_model(self) -> None:
        """Recompute metric weight adjustments from cull feedback.

        Strategy: Look at corrections where the user disagreed.
        - If user kept a photo the system wanted to trash, find which
          metrics were low and reduce their weight (the user doesn't care
          as much about those metrics).
        - If user trashed a photo the system wanted to keep, find which
          metrics were high and reduce their weight (those metrics are
          misleading for this user's taste).
        - Also adjust keep/trash thresholds toward the correction scores.
        """
        samples = self._data.get("cull_samples", [])
        corrections = [s for s in samples if s.get("auto") != s.get("user")]

        if len(corrections) < _MIN_CULL_SAMPLES:
            self._data["learned_weights"] = {}
            self._data["learned_thresholds"] = {}
            return

        metrics_list = ["sharpness", "exposure", "detail", "noise",
                        "composition", "faces"]

        # Accumulate weight adjustments
        weight_shifts: Dict[str, List[float]] = {m: [] for m in metrics_list}
        threshold_hints: List[Dict] = []

        for c in corrections:
            auto = c["auto"]
            user = c["user"]
            metrics = c.get("metrics", {})
            score = c.get("score", 0.5)

            if user == "keep" and auto in ("trash", "culled"):
                # System was too harsh — user wants this photo.
                # Metrics that scored low were overly penalizing.
                for m in metrics_list:
                    v = metrics.get(m, 0.5)
                    if v < 0.5:
                        # This metric dragged the score down unfairly
                        weight_shifts[m].append(-0.05 * (0.5 - v))
                threshold_hints.append({"type": "keep_lower", "score": score})

            elif user == "trash" and auto in ("keep", "culled"):
                # System was too lenient — user doesn't want this photo.
                # Metrics that scored high were misleading.
                for m in metrics_list:
                    v = metrics.get(m, 0.5)
                    if v > 0.5:
                        weight_shifts[m].append(-0.03 * (v - 0.5))
                threshold_hints.append({"type": "trash_higher", "score": score})

        # Compute average weight shifts (bounded to ±0.15)
        learned_w = {}
        for m in metrics_list:
            shifts = weight_shifts[m]
            if shifts:
                avg = sum(shifts) / len(shifts)
                bounded = max(-0.15, min(0.15, avg))
                if abs(bounded) > 0.005:
                    learned_w[m] = round(bounded, 4)

        self._data["learned_weights"] = learned_w

        # Adjust thresholds
        keep_hints = [h["score"] for h in threshold_hints if h["type"] == "keep_lower"]
        trash_hints = [h["score"] for h in threshold_hints if h["type"] == "trash_higher"]

        thresholds = {}
        if keep_hints:
            # User keeps photos at this score range — lower the keep threshold
            thresholds["keep_shift"] = round(
                -min(0.15, max(0.02, 0.50 - (sum(keep_hints) / len(keep_hints)))), 4
            )
        if trash_hints:
            # User trashes photos at this score range — raise the trash threshold
            thresholds["trash_shift"] = round(
                min(0.15, max(0.02, (sum(trash_hints) / len(trash_hints)) - 0.35)), 4
            )

        self._data["learned_thresholds"] = thresholds

    def get_score_adjustments(self) -> Dict:
        """Return learned culling adjustments.

        Returns:
            Dict with keys:
            - ``weight_shifts``: per-metric weight deltas
            - ``keep_shift``: delta to add to keep threshold (usually negative)
            - ``trash_shift``: delta to add to trash threshold (usually positive)
            - ``sample_count``: number of correction samples
        """
        samples = self._data.get("cull_samples", [])
        corrections = [s for s in samples if s.get("auto") != s.get("user")]
        n = len(corrections)

        if n < _MIN_CULL_SAMPLES:
            return {"weight_shifts": {}, "keep_shift": 0.0,
                    "trash_shift": 0.0, "sample_count": n}

        thresholds = self._data.get("learned_thresholds", {})
        return {
            "weight_shifts": dict(self._data.get("learned_weights", {})),
            "keep_shift": thresholds.get("keep_shift", 0.0),
            "trash_shift": thresholds.get("trash_shift", 0.0),
            "sample_count": n,
        }

    # ------------------------------------------------------------------
    # Learned adjustments — editing
    # ------------------------------------------------------------------

    def _recompute_edit_model(self) -> None:
        """Compute average edit preference shifts grouped by scene type.

        Groups by (iso_bucket, brightness_bucket) and computes the mean
        override for each slider.  This tells us e.g. "for dark high-ISO
        photos, the user typically wants +15 exposure and -10 saturation."
        """
        samples = self._data.get("edit_samples", [])
        if len(samples) < _MIN_EDIT_SAMPLES:
            self._data["learned_edit_prefs"] = {}
            return

        # Group by scene key
        groups: Dict[str, List[Dict]] = {}
        for s in samples:
            key = f"{s.get('iso_bucket', 'unknown')}_{s.get('brightness_bucket', 'mid')}"
            groups.setdefault(key, []).append(s.get("overrides", {}))

        # Also compute a global average
        all_overrides = [s.get("overrides", {}) for s in samples]
        groups["_global"] = all_overrides

        # For each group, compute the mean of each override parameter
        prefs: Dict[str, Dict[str, float]] = {}
        slider_keys = ["exposure", "contrast", "saturation", "sharpness",
                        "noise_reduction", "wb_warmth"]

        for group_key, override_list in groups.items():
            if len(override_list) < _MIN_EDIT_SAMPLES and group_key != "_global":
                continue
            means = {}
            for sk in slider_keys:
                default = 100 if sk == "sharpness" else 0
                values = [o.get(sk, default) for o in override_list]
                # Compute shift from default
                shifts = [v - default for v in values]
                avg_shift = sum(shifts) / len(shifts) if shifts else 0
                if abs(avg_shift) > 1.0:  # only store meaningful shifts
                    means[sk] = round(avg_shift, 1)

            # Track preferred color grades
            grades = [o.get("color_grade") for o in override_list if o.get("color_grade")]
            if grades:
                from collections import Counter
                most_common = Counter(grades).most_common(1)[0]
                if most_common[1] >= 2:
                    means["preferred_grade"] = most_common[0]

            if means:
                prefs[group_key] = means

        self._data["learned_edit_prefs"] = prefs

    def get_edit_preferences(
        self,
        iso: Optional[int] = None,
        mean_brightness: float = 128.0,
    ) -> Dict[str, float]:
        """Return learned edit preferences for a given scene type.

        Falls back to global preferences if insufficient scene-specific
        data exists.

        Returns:
            Dict of slider shift values, e.g.
            {"exposure": 12.5, "contrast": -5.0, "preferred_grade": "moody"}.
        """
        prefs = self._data.get("learned_edit_prefs", {})
        if not prefs:
            return {}

        # Try specific scene first
        key = f"{_iso_bucket(iso)}_{_brightness_bucket(mean_brightness)}"
        if key in prefs:
            return dict(prefs[key])

        # Fall back to global
        return dict(prefs.get("_global", {}))

    # ------------------------------------------------------------------
    # Duplicate choice feedback
    # ------------------------------------------------------------------

    def record_duplicate_choice(
        self,
        kept_metrics: Dict[str, float],
        rejected_metrics: List[Dict[str, float]],
    ) -> None:
        """Record which photo the user chose from a duplicate group.

        By comparing metric scores of the kept vs rejected photos we
        learn which metrics the user prioritises when picking between
        similar shots.

        Args:
            kept_metrics: Per-metric scores of the photo the user kept.
            rejected_metrics: Per-metric scores of each rejected photo.
        """
        if not rejected_metrics:
            return

        sample = {
            "kept": {k: round(v, 4) for k, v in kept_metrics.items()},
            "rejected": [
                {k: round(v, 4) for k, v in rm.items()} for rm in rejected_metrics
            ],
        }

        samples = self._data.setdefault("dup_samples", [])
        samples.append(sample)
        if len(samples) > 300:
            self._data["dup_samples"] = samples[-300:]

        self._recompute_dup_model()
        self._save()

        logger.info("Duplicate choice feedback recorded (%d samples)", len(samples))

    def _recompute_dup_model(self) -> None:
        """Learn which metrics matter most when choosing between duplicates.

        For each choice, compute which metrics the kept photo beat the
        rejected ones on.  Metrics that consistently win become more
        important for auto-ranking duplicates.
        """
        samples = self._data.get("dup_samples", [])
        if len(samples) < 3:
            self._data["learned_dup_weights"] = {}
            return

        metrics_list = ["sharpness", "exposure", "detail", "noise",
                        "composition", "faces"]
        win_counts: Dict[str, int] = {m: 0 for m in metrics_list}
        total = 0

        for s in samples:
            kept = s.get("kept", {})
            for rej in s.get("rejected", []):
                total += 1
                for m in metrics_list:
                    kv = kept.get(m, 0.5)
                    rv = rej.get(m, 0.5)
                    if kv > rv + 0.02:
                        win_counts[m] += 1

        if total == 0:
            return

        # Convert to relative importance weights (0-1)
        weights = {}
        for m in metrics_list:
            ratio = win_counts[m] / total
            if ratio > 0.1:
                weights[m] = round(ratio, 3)

        self._data["learned_dup_weights"] = weights

    def get_duplicate_ranking_weights(self) -> Dict[str, float]:
        """Return learned metric importance for ranking duplicates.

        Returns:
            Dict mapping metric names to importance weights (0-1).
            Higher = user cares more about this metric when choosing
            between similar photos.
        """
        return dict(self._data.get("learned_dup_weights", {}))

    # ------------------------------------------------------------------
    # Summary for UI display
    # ------------------------------------------------------------------

    def summary(self) -> Dict:
        """Return a human-readable summary of the learned model."""
        cull = self._data.get("cull_samples", [])
        edit = self._data.get("edit_samples", [])
        corrections = sum(1 for s in cull if s.get("auto") != s.get("user"))
        adj = self.get_score_adjustments()

        return {
            "total_cull_feedback": len(cull),
            "cull_corrections": corrections,
            "total_edit_feedback": len(edit),
            "weight_shifts": adj.get("weight_shifts", {}),
            "keep_threshold_shift": adj.get("keep_shift", 0.0),
            "trash_threshold_shift": adj.get("trash_shift", 0.0),
            "edit_preferences": dict(self._data.get("learned_edit_prefs", {})),
        }


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------
_INSTANCE: Optional[FeedbackLearner] = None


def get_learner() -> FeedbackLearner:
    """Return the global ``FeedbackLearner`` singleton."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = FeedbackLearner()
    return _INSTANCE
