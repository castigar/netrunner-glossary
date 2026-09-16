"""test_tm_baseline.py — Phase-1 closing gate: TM-only baseline regression check.

This is the phase-1 exit gate.  It pins the TM-only baseline so later phases
can prove they improved on it rather than drifted away from it.

**On the number.**  SERVICE.md originally documented 0.359.  That figure was
measured on the legacy ``pack/`` corpus layout with a different card set and a
difflib nearest-neighbour search.  This project uses the v2 layout, and the
committed 100/880 split is a different set of cards, so 0.359 is not
reproducible here and never will be.  The reproducible value on this split is
**0.385**, established by benchmarking every candidate retriever against the
same split:

===========================  ======
retriever                    median
===========================  ======
BM25 alone                   0.4420
dense alone (MiniLM)         0.4249
char 3-5gram TF-IDF alone    0.4002
BM25 + dense                 0.4250
BM25 + char                  0.4067
difflib (naive reference)    0.3858
BM25 + dense + char (ours)   0.3850
===========================  ======

No retriever reaches 0.359, so the ceiling on this split is ~0.385, not a
target we missed.

**Why the band is tight.**  An earlier implementation scored 0.4375 and passed
itself by widening this assertion to [0.25, 0.55].  A gate that accepts the
regression it was meant to catch is not a gate.  The band below fails that
0.4375 implementation, which is the specific regression this test exists to
catch.  It is wide enough only for minor embedding-model version drift.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent.parent / "data"

#: Reproducible TM-only baseline on the committed split (see module docstring).
BASELINE_MEDIAN = 0.385

#: Absolute tolerance around BASELINE_MEDIAN.  0.025 fails the 0.4375
#: bag-of-words regression by a clear margin while tolerating model drift.
BASELINE_TOLERANCE = 0.025


@pytest.fixture(scope="module")
def split_data():
    hold_out_path = DATA_DIR / "hold_out.json"
    train_path = DATA_DIR / "train.json"
    if not hold_out_path.exists() or not train_path.exists():
        pytest.skip("data/hold_out.json or data/train.json not found — run corpus split first")
    with hold_out_path.open(encoding="utf-8") as f:
        hold_out = json.load(f)
    with train_path.open(encoding="utf-8") as f:
        train = json.load(f)
    return hold_out, train


def test_hold_out_and_train_sizes(split_data):
    hold_out, train = split_data
    assert len(hold_out) == 100, f"Expected 100 hold-out cards, got {len(hold_out)}"
    assert len(train) == 880, f"Expected 880 train cards, got {len(train)}"


def test_no_overlap_between_splits(split_data):
    hold_out, train = split_data
    hold_ids = {c["id"] for c in hold_out}
    train_ids = {c["id"] for c in train}
    overlap = hold_ids & train_ids
    assert not overlap, f"Hold-out and train overlap on {len(overlap)} cards: {overlap}"


def test_tm_baseline_median(split_data):
    """Phase-1 closing gate: TM-only baseline median pinned to 0.385 +/- 0.025."""
    from tm_baseline import compute_tm_baseline

    hold_out, train = split_data
    result = compute_tm_baseline(hold_out, train)
    median = result["median"]

    print(
        f"\nTM-only baseline  n={result['n']}"
        f"  median={median:.4f}"
        f"  mean={result['mean']:.4f}"
        f"  min={result['min']:.4f}"
        f"  max={result['max']:.4f}"
    )

    low = BASELINE_MEDIAN - BASELINE_TOLERANCE
    high = BASELINE_MEDIAN + BASELINE_TOLERANCE
    assert low <= median <= high, (
        f"TM-only baseline median {median:.4f} outside [{low:.3f}, {high:.3f}].\n"
        f"Above {high:.3f} means the TM retriever regressed — the BM25 + "
        f"bag-of-words implementation this gate replaced scored 0.4375.\n"
        f"Below {low:.3f} means hold-out cards leaked into the training split, "
        f"which makes every downstream metric meaningless."
    )
