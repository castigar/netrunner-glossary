"""tm_baseline.py — TM-only baseline evaluation (phase-1 regression gate).

SERVICE.md §5 gate 3: reproduce the TM nearest-neighbour baseline
median normalized edit distance on the 100-card hold-out set.
This value anchors the regression monitor for subsequent phases.

Metric: 1 - SequenceMatcher.ratio() after whitespace normalisation.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from tm_index import HybridTMIndex


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def compute_tm_baseline(
    hold_out: list[dict],
    train: list[dict],
) -> dict:
    """Compute TM-only baseline metrics on *hold_out* using *train* as the TM.

    For each hold-out card, the TM nearest neighbour's KO text is used as the
    predicted translation.  Normalised edit distance is computed as::

        1 - SequenceMatcher.ratio(normalised_pred, normalised_ref)

    Args:
        hold_out: list of card dicts with "id", "en_text", "ko_text".
        train:    list of card dicts to index as the TM (must not overlap hold-out).

    Returns:
        dict with keys:
            "median"     float  — median normalised edit distance
            "mean"       float  — mean normalised edit distance
            "min"        float  — minimum distance
            "max"        float  — maximum distance
            "n"          int    — number of evaluated cards
            "distances"  list   — per-card distances (sorted)
    """
    index = HybridTMIndex()
    index.build(train)

    distances: list[float] = []
    for card in hold_out:
        results = index.search(card["en_text"], k=1)
        if not results:
            distances.append(1.0)
            continue
        pred = _normalize_ws(results[0]["ko_text"])
        ref = _normalize_ws(card["ko_text"])
        dist = 1.0 - SequenceMatcher(None, pred, ref).ratio()
        distances.append(dist)

    arr = np.array(distances, dtype=np.float64)
    return {
        "median": float(np.median(arr)),
        "mean": float(np.mean(arr)),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "n": len(distances),
        "distances": sorted(distances),
    }


def run_baseline_from_files(data_dir: Path | str = "data") -> dict:
    """Load saved split files and compute the baseline.

    Args:
        data_dir: directory containing hold_out.json and train.json.

    Returns:
        Result dict from :func:`compute_tm_baseline`.
    """
    data_dir = Path(data_dir)
    with (data_dir / "hold_out.json").open(encoding="utf-8") as f:
        hold_out = json.load(f)
    with (data_dir / "train.json").open(encoding="utf-8") as f:
        train = json.load(f)
    return compute_tm_baseline(hold_out, train)


if __name__ == "__main__":
    import sys

    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    result = run_baseline_from_files(data_dir)
    print(
        f"TM-only baseline  n={result['n']}"
        f"  median={result['median']:.4f}"
        f"  mean={result['mean']:.4f}"
        f"  min={result['min']:.4f}"
        f"  max={result['max']:.4f}"
    )
    print(
        "NOTE: SERVICE.md §5 documents the expected baseline median as ~0.359."
        f" Actual computed value from this corpus split: {result['median']:.4f}."
        " This value becomes the regression sentinel for future phases."
    )
