"""gate3_edit_distance.py — Hard Gate 3: normalized edit distance median.

SERVICE.md §5, gate 3:
  For each card in the hold-out set, compute normalized edit distance between
  the predicted KO translation and the official KO reference:

      distance = 1 - SequenceMatcher.ratio(normalize_ws(pred), normalize_ws(ref))

  The gate passes when median(distances) <= THRESHOLD.

THRESHOLD derivation:
  The threshold is a relation, not a constant: ``tm_only_baseline × 0.70``.
  The AC6 entry point measures the tm_only_baseline series in the same run and
  passes ``derive_threshold(baseline)`` into :func:`score_hold_out`
  (baseline_provenance = measured_in_run).  The module-level ``THRESHOLD``
  below is only the fallback for callers that supply no baseline; its value
  comes from the pinned reference baseline 0.385 × 0.70 ≈ 0.27
  (tests/test_tm_baseline.py, ± 0.025).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

# TM-only baseline median, pinned by tests/test_tm_baseline.py, which measures it
# with compute_tm_baseline() rather than asserting it.
TM_BASELINE_MEDIAN = 0.385
THRESHOLD_FACTOR = 0.70  # gate requires this fraction of the baseline

# Derived, not written down.  The spec forbids putting a measured number into an
# acceptance criterion as a constant: the gate must move when the baseline moves.
# Writing 0.27 by hand also quietly rounded it — the derivation is 0.2695.
THRESHOLD = TM_BASELINE_MEDIAN * THRESHOLD_FACTOR


@dataclass
class CardDistanceResult:
    """Edit distance result for a single hold-out card."""

    card_id: str
    distance: float   # 1 - SequenceMatcher.ratio(pred, ref) after ws normalization
    pred_text: str
    ref_text: str


@dataclass
class Gate3Result:
    """Aggregated Hard Gate 3 result over all hold-out cards."""

    passed: bool
    median_distance: float
    mean_distance: float
    min_distance: float
    max_distance: float
    n: int
    threshold: float = THRESHOLD
    card_results: list[CardDistanceResult] = field(default_factory=list)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _edit_distance(pred: str, ref: str) -> float:
    """Normalized edit distance: 1 - SequenceMatcher.ratio after whitespace normalization."""
    return 1.0 - SequenceMatcher(None, _normalize_ws(pred), _normalize_ws(ref)).ratio()


def derive_threshold(tm_only_baseline_median: float) -> float:
    """Derive the Gate 3 threshold from a TM-only baseline measured in the run.

    The Seed forbids pinning the gate threshold to a constant: it must be
    derived as ``tm_only_baseline x THRESHOLD_FACTOR`` from the baseline the
    entry point measures in the same run (baseline_provenance=measured_in_run).

    Args:
        tm_only_baseline_median: Median edit distance of the tm_only_baseline
            prediction series, measured in this run.

    Returns:
        The derived threshold.
    """
    return tm_only_baseline_median * THRESHOLD_FACTOR


def score_hold_out(
    hold_out_path: str | Path,
    predictions: list[str],
    threshold: float | None = None,
) -> Gate3Result:
    """Score Hard Gate 3 over the hold-out set.

    Args:
        hold_out_path: Path to data/hold_out.json (list of {id, en_text, ko_text}).
            The ``ko_text`` field is the official reference translation.
        predictions: Agent-generated KO translations, one per card in the same
            order as hold_out.json.  Length must equal len(hold_out).
        threshold: Optional pass threshold.  When given, ``passed`` is decided
            against it instead of the module constant.  The AC6 entry point
            passes ``derive_threshold(tm_only_baseline_median)`` so the gate is
            judged against a baseline measured in the same run rather than a
            pinned constant.  ``None`` keeps the module default.

    Returns:
        :class:`Gate3Result` with ``passed=True`` iff median_distance <= threshold.
    """
    effective_threshold = THRESHOLD if threshold is None else threshold
    cards: list[dict] = json.loads(Path(hold_out_path).read_text(encoding="utf-8"))

    if len(cards) != len(predictions):
        raise ValueError(
            f"predictions length {len(predictions)} != hold-out length {len(cards)}"
        )

    card_results: list[CardDistanceResult] = []
    for card, pred in zip(cards, predictions):
        ref = card.get("ko_text", "")
        dist = _edit_distance(pred, ref)
        card_results.append(
            CardDistanceResult(
                card_id=card.get("id", ""),
                distance=dist,
                pred_text=pred,
                ref_text=ref,
            )
        )

    arr = np.array([r.distance for r in card_results], dtype=np.float64)
    median = float(np.median(arr)) if len(arr) > 0 else 0.0

    return Gate3Result(
        passed=median <= effective_threshold,
        median_distance=median,
        mean_distance=float(np.mean(arr)) if len(arr) > 0 else 0.0,
        min_distance=float(arr.min()) if len(arr) > 0 else 0.0,
        max_distance=float(arr.max()) if len(arr) > 0 else 0.0,
        n=len(card_results),
        threshold=effective_threshold,
        card_results=card_results,
    )


def score_self_reference(hold_out_path: str | Path) -> Gate3Result:
    """Score hold-out using the official KO translations as predictions.

    This is the oracle upper bound: a perfect agent that reproduces the official
    translations exactly should achieve median_distance == 0.0.

    Args:
        hold_out_path: Path to data/hold_out.json.

    Returns:
        :class:`Gate3Result` (always passes since distance == 0 <= THRESHOLD).
    """
    cards: list[dict] = json.loads(Path(hold_out_path).read_text(encoding="utf-8"))
    predictions = [card.get("ko_text", "") for card in cards]
    return score_hold_out(hold_out_path, predictions)
