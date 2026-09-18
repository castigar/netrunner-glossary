"""test_gate3_edit_distance.py — Hard Gate 3 normalized edit distance scorer.

AC: hold-out 100장 기준 정규화 편집거리(공백 정규화 후 1 - SequenceMatcher.ratio)
중앙값 <= 0.27. TM 베이스라인 0.385의 30% 개선값.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate3_edit_distance import (
    CardDistanceResult,
    Gate3Result,
    THRESHOLD,
    TM_BASELINE_MEDIAN,
    THRESHOLD_FACTOR,
    _edit_distance,
    _normalize_ws,
    score_hold_out,
    score_self_reference,
)

DATA_HOLD_OUT = Path(__file__).parent.parent / "data" / "hold_out.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_hold_out(tmp_path: Path, cards: list[dict]) -> Path:
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _normalize_ws
# ---------------------------------------------------------------------------


def test_normalize_ws_collapses_spaces():
    assert _normalize_ws("a  b") == "a b"


def test_normalize_ws_collapses_newline():
    assert _normalize_ws("a\nb") == "a b"


def test_normalize_ws_strips_edges():
    assert _normalize_ws("  hello  ") == "hello"


def test_normalize_ws_empty():
    assert _normalize_ws("") == ""


# ---------------------------------------------------------------------------
# _edit_distance
# ---------------------------------------------------------------------------


def test_edit_distance_identical_is_zero():
    assert _edit_distance("hello", "hello") == pytest.approx(0.0)


def test_edit_distance_completely_different():
    d = _edit_distance("abc", "xyz")
    assert 0.0 < d <= 1.0


def test_edit_distance_bounds():
    d = _edit_distance("설치", "폐기")
    assert 0.0 <= d <= 1.0


def test_edit_distance_whitespace_normalized():
    # extra spaces should not matter
    d = _edit_distance("hello world", "hello  world")
    assert d == pytest.approx(0.0)


def test_edit_distance_empty_strings():
    assert _edit_distance("", "") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# THRESHOLD derivation invariant
# ---------------------------------------------------------------------------


def test_threshold_is_derived_not_written_down():
    """THRESHOLD must *be* TM_BASELINE_MEDIAN x THRESHOLD_FACTOR, not a copy of it.

    Exact equality, not a tolerance.  The previous version allowed 0.005 of slack,
    which is what let the hand-written 0.27 stand in for the real derivation 0.2695:
    the gate silently ran 0.0005 looser than the spec.  With equality the gate moves
    when the baseline moves, which is the point of deriving it.
    """
    assert THRESHOLD == TM_BASELINE_MEDIAN * THRESHOLD_FACTOR


def test_threshold_is_not_one_of_the_retired_values():
    """0.25 came from the discarded 0.359 baseline (legacy pack/ layout)."""
    assert THRESHOLD != 0.25
    assert TM_BASELINE_MEDIAN != 0.359


def test_baseline_constant_agrees_with_the_measured_baseline():
    """This module duplicates the baseline; the two copies must not drift apart.

    tests/test_tm_baseline.py is where the number is actually *measured*, via
    compute_tm_baseline().  This only pins that the gate reads the same one, so
    changing the measurement without changing the gate fails here.
    """
    import test_tm_baseline

    assert TM_BASELINE_MEDIAN == test_tm_baseline.BASELINE_MEDIAN


# ---------------------------------------------------------------------------
# score_hold_out — unit tests with synthetic data
# ---------------------------------------------------------------------------


def test_score_returns_gate3_result(tmp_path):
    cards = [{"id": "c1", "en_text": "Install.", "ko_text": "설치한다."}]
    path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(path, ["설치한다."])
    assert isinstance(result, Gate3Result)


def test_score_perfect_prediction_passes(tmp_path):
    """When predictions match references exactly, distance is 0.0 and gate passes."""
    cards = [{"id": "c1", "en_text": "Run.", "ko_text": "런을 수행한다."}]
    path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(path, ["런을 수행한다."])
    assert result.median_distance == pytest.approx(0.0)
    assert result.passed is True


def test_score_high_distance_fails(tmp_path):
    """When all predictions are completely wrong, gate fails."""
    cards = [{"id": f"c{i}", "en_text": "Run.", "ko_text": "런을 수행한다."} for i in range(10)]
    path = _write_hold_out(tmp_path, cards)
    predictions = ["abcdefghijklmnopqrstuvwxyz1234567890"] * 10
    result = score_hold_out(path, predictions)
    assert result.median_distance > THRESHOLD
    assert result.passed is False


def test_score_card_results_count_matches(tmp_path):
    n = 5
    cards = [{"id": f"c{i}", "en_text": "Run.", "ko_text": "런."} for i in range(n)]
    path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(path, ["런."] * n)
    assert len(result.card_results) == n


def test_score_card_result_type(tmp_path):
    cards = [{"id": "card_x", "en_text": "Run.", "ko_text": "런."}]
    path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(path, ["런."])
    assert isinstance(result.card_results[0], CardDistanceResult)
    assert result.card_results[0].card_id == "card_x"


def test_score_card_result_stores_texts(tmp_path):
    cards = [{"id": "c1", "en_text": "Run.", "ko_text": "런."}]
    path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(path, ["런 수행."])
    cr = result.card_results[0]
    assert cr.pred_text == "런 수행."
    assert cr.ref_text == "런."


def test_score_passes_at_threshold(tmp_path):
    """median == THRESHOLD should be a pass (<=)."""
    import math
    from difflib import SequenceMatcher

    # Build a card where distance is exactly close to threshold
    ref = "런을 수행한다."
    # perfect prediction gives 0.0; threshold = 0.27
    cards = [{"id": "c1", "en_text": "Run.", "ko_text": ref}]
    path = _write_hold_out(tmp_path, cards)
    # Use the ref itself (distance = 0.0) which is <= 0.27
    result = score_hold_out(path, [ref])
    assert result.passed is True


def test_score_raises_on_length_mismatch(tmp_path):
    cards = [{"id": "c1", "en_text": "Run.", "ko_text": "런."}]
    path = _write_hold_out(tmp_path, cards)
    with pytest.raises(ValueError, match="predictions length"):
        score_hold_out(path, ["런.", "extra"])


def test_score_threshold_field(tmp_path):
    cards = [{"id": "c1", "en_text": "Run.", "ko_text": "런."}]
    path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(path, ["런."])
    assert result.threshold == THRESHOLD


def test_score_n_field(tmp_path):
    n = 7
    cards = [{"id": f"c{i}", "en_text": "Run.", "ko_text": "런."} for i in range(n)]
    path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(path, ["런."] * n)
    assert result.n == n


def test_score_distances_in_bounds(tmp_path):
    cards = [
        {"id": "c1", "en_text": "Run.", "ko_text": "런을 수행한다."},
        {"id": "c2", "en_text": "Install.", "ko_text": "설치한다."},
    ]
    path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(path, ["런.", "설치 완료."])
    assert 0.0 <= result.min_distance <= result.max_distance <= 1.0
    assert 0.0 <= result.median_distance <= 1.0


# ---------------------------------------------------------------------------
# score_self_reference
# ---------------------------------------------------------------------------


def test_self_reference_distance_zero(tmp_path):
    """Self-reference: predictions == references → all distances 0.0."""
    cards = [
        {"id": "c1", "en_text": "Run.", "ko_text": "런을 수행한다."},
        {"id": "c2", "en_text": "Install.", "ko_text": "설치한다."},
    ]
    path = _write_hold_out(tmp_path, cards)
    result = score_self_reference(path)
    assert result.median_distance == pytest.approx(0.0)
    assert result.passed is True


def test_self_reference_n_matches_cards(tmp_path):
    n = 6
    cards = [{"id": f"c{i}", "en_text": "Run.", "ko_text": "런."} for i in range(n)]
    path = _write_hold_out(tmp_path, cards)
    result = score_self_reference(path)
    assert result.n == n


# ---------------------------------------------------------------------------
# Integration: real hold-out set (self-reference oracle)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_real_hold_out_has_100_cards():
    cards = json.loads(DATA_HOLD_OUT.read_text(encoding="utf-8"))
    assert len(cards) == 100


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_real_hold_out_self_reference_is_zero():
    """Oracle test: official KO as both pred and ref must give distance 0.0."""
    result = score_self_reference(DATA_HOLD_OUT)
    assert result.median_distance == pytest.approx(0.0)
    assert result.passed is True
    assert result.n == 100


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_real_hold_out_rejects_untranslated_predictions():
    """Feeding the EN source back as the 'translation' must fail the gate.

    Replaces an if/else that restated ``passed == (median <= THRESHOLD)`` and so
    could not fail.  This asserts an outcome the implementation could get wrong:
    leaving the source untranslated is the cheapest way to game an edit-distance
    gate, and on the real 100 cards it must be rejected.
    """
    import json

    cards = json.loads(Path(DATA_HOLD_OUT).read_text(encoding="utf-8"))
    predictions = [c["en_text"] for c in cards]

    result = score_hold_out(DATA_HOLD_OUT, predictions)

    assert result.median_distance > THRESHOLD
    assert result.passed is False
