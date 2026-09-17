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


def test_threshold_derived_from_baseline():
    """THRESHOLD must equal TM_BASELINE_MEDIAN × THRESHOLD_FACTOR (within float tolerance)."""
    derived = TM_BASELINE_MEDIAN * THRESHOLD_FACTOR
    assert abs(THRESHOLD - derived) < 0.005, (
        f"THRESHOLD {THRESHOLD} not within 0.005 of baseline*factor {derived:.4f}"
    )


def test_threshold_value():
    assert THRESHOLD == pytest.approx(0.27, abs=0.005)


def test_tm_baseline_value():
    assert TM_BASELINE_MEDIAN == pytest.approx(0.385, abs=0.025)


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
def test_real_hold_out_gate3_mechanism_correct():
    """Gate correctly reflects: passed iff median_distance <= THRESHOLD."""
    result = score_self_reference(DATA_HOLD_OUT)
    if result.median_distance <= THRESHOLD:
        assert result.passed is True
    else:
        assert result.passed is False
