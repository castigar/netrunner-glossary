"""Tests for term_extraction_eval.py (AC 5).

AC requirement:
  The term extraction algorithm uses card_subtypes.json 88 items directly as a
  scoring answer set to report precision and recall.

Tests verify:
  - _normalize_subtype_id correctly converts id slugs to card-text form.
  - evaluate_extraction() returns correct EvaluationResult fields.
  - Precision and recall are computed correctly with controlled data.
  - Edge cases: empty candidates, empty gold set.
  - Real corpus smoke test (skipped if corpus not available).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from term_candidate_extractor import TermCandidate, generate_candidates
from term_extraction_eval import (
    EvaluationResult,
    _normalize_subtype_id,
    evaluate_extraction,
    load_gold_subtypes,
)


# ---------------------------------------------------------------------------
# _normalize_subtype_id
# ---------------------------------------------------------------------------


def test_normalize_subtype_id_no_underscore():
    assert _normalize_subtype_id("barrier") == "barrier"


def test_normalize_subtype_id_underscore_to_space():
    assert _normalize_subtype_id("code_gate") == "code gate"


def test_normalize_subtype_id_multi_underscore():
    assert _normalize_subtype_id("black_ops") == "black ops"


def test_normalize_subtype_id_lowercase():
    assert _normalize_subtype_id("AI") == "ai"


def test_normalize_subtype_id_already_lowercase_no_op():
    assert _normalize_subtype_id("virus") == "virus"


# ---------------------------------------------------------------------------
# EvaluationResult fields
# ---------------------------------------------------------------------------


def _make_candidate(en_term: str, ko_term: str = "테스트", cooccur: int = 5) -> TermCandidate:
    return TermCandidate(
        en_term=en_term,
        ko_term=ko_term,
        cooccurrence=cooccur,
        dice=0.5,
        pmi=1.0,
    )


def test_evaluation_result_is_dataclass():
    result = EvaluationResult(
        precision=0.5,
        recall=0.8,
        f1=0.615,
        tp=4,
        fp=4,
        fn=1,
        gold_size=5,
        extracted_size=8,
        found_terms=frozenset(["barrier"]),
        missed_terms=frozenset(["ai"]),
    )
    assert result.precision == 0.5
    assert result.recall == 0.8


def test_evaluation_result_report_is_string():
    result = EvaluationResult(
        precision=0.25,
        recall=0.5,
        f1=0.333,
        tp=2,
        fp=6,
        fn=2,
        gold_size=4,
        extracted_size=8,
        found_terms=frozenset(["barrier", "sentry"]),
        missed_terms=frozenset(["ai", "virus"]),
    )
    report = result.report()
    assert isinstance(report, str)
    assert "Precision" in report
    assert "Recall" in report
    assert "F1" in report


# ---------------------------------------------------------------------------
# evaluate_extraction — correctness
# ---------------------------------------------------------------------------


def _make_gold(ids: list[str]) -> list[dict]:
    return [{"id": id_, "name": f"KO_{id_}"} for id_ in ids]


def test_perfect_recall_when_all_gold_in_candidates():
    """All gold terms appear in extracted candidates → recall = 1.0."""
    gold = _make_gold(["barrier", "sentry", "code_gate"])
    candidates = [
        _make_candidate("barrier"),
        _make_candidate("sentry"),
        _make_candidate("code gate"),  # code_gate normalized
    ]
    result = evaluate_extraction(candidates, gold)
    assert result.recall == pytest.approx(1.0)
    assert result.tp == 3
    assert result.fn == 0


def test_zero_recall_when_no_gold_in_candidates():
    """No gold terms in extracted candidates → recall = 0.0."""
    gold = _make_gold(["barrier", "sentry"])
    candidates = [
        _make_candidate("install"),
        _make_candidate("trash"),
    ]
    result = evaluate_extraction(candidates, gold)
    assert result.recall == pytest.approx(0.0)
    assert result.tp == 0
    assert result.fn == 2


def test_partial_recall():
    """Only some gold terms found → 0 < recall < 1."""
    gold = _make_gold(["barrier", "sentry", "virus"])
    candidates = [
        _make_candidate("barrier"),
        _make_candidate("install"),  # not in gold
    ]
    result = evaluate_extraction(candidates, gold)
    assert result.tp == 1
    assert result.fn == 2
    assert result.recall == pytest.approx(1 / 3)


def test_precision_counts_extracted_terms():
    """Precision = TP / |extracted EN terms|."""
    gold = _make_gold(["barrier", "sentry"])
    # 2 gold terms + 3 non-gold terms extracted
    candidates = [
        _make_candidate("barrier"),
        _make_candidate("sentry"),
        _make_candidate("install"),
        _make_candidate("trash"),
        _make_candidate("rez"),
    ]
    result = evaluate_extraction(candidates, gold)
    assert result.tp == 2
    assert result.fp == 3
    assert result.extracted_size == 5
    assert result.precision == pytest.approx(2 / 5)


def test_f1_is_harmonic_mean():
    """F1 = 2 * P * R / (P + R)."""
    gold = _make_gold(["barrier", "sentry", "virus"])
    candidates = [
        _make_candidate("barrier"),  # TP
        _make_candidate("trash"),    # FP
    ]
    result = evaluate_extraction(candidates, gold)
    # P = 1/2, R = 1/3
    expected_f1 = 2 * (1 / 2) * (1 / 3) / ((1 / 2) + (1 / 3))
    assert result.f1 == pytest.approx(expected_f1)


def test_found_terms_contains_matched_terms():
    """found_terms must contain the gold EN terms that were recovered."""
    gold = _make_gold(["barrier", "code_gate", "sentry"])
    candidates = [
        _make_candidate("barrier"),
        _make_candidate("code gate"),  # code_gate normalized
    ]
    result = evaluate_extraction(candidates, gold)
    assert "barrier" in result.found_terms
    assert "code gate" in result.found_terms
    assert "sentry" not in result.found_terms


def test_missed_terms_contains_unrecovered_gold():
    """missed_terms must contain the gold EN terms that were NOT found."""
    gold = _make_gold(["barrier", "sentry", "virus"])
    candidates = [_make_candidate("barrier")]
    result = evaluate_extraction(candidates, gold)
    assert "sentry" in result.missed_terms
    assert "virus" in result.missed_terms
    assert "barrier" not in result.missed_terms


def test_gold_size_reflects_gold_set():
    gold = _make_gold(["barrier", "sentry", "code_gate", "virus", "tracer"])
    result = evaluate_extraction([], gold)
    assert result.gold_size == 5


def test_underscore_ids_normalized_for_matching():
    """Subtype id 'code_gate' must match extracted term 'code gate'."""
    gold = [{"id": "code_gate", "name": "코드 게이트"}]
    candidates = [_make_candidate("code gate")]
    result = evaluate_extraction(candidates, gold)
    assert result.tp == 1
    assert result.recall == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_zero_precision_recall():
    gold = _make_gold(["barrier", "sentry"])
    result = evaluate_extraction([], gold)
    assert result.precision == pytest.approx(0.0)
    assert result.recall == pytest.approx(0.0)
    assert result.f1 == pytest.approx(0.0)
    assert result.tp == 0
    assert result.fn == 2
    assert result.extracted_size == 0


def test_empty_gold_returns_zero_recall():
    """No gold terms → recall is 0 (no terms to find)."""
    candidates = [_make_candidate("install"), _make_candidate("trash")]
    result = evaluate_extraction(candidates, [])
    assert result.recall == pytest.approx(0.0)
    assert result.gold_size == 0
    assert result.tp == 0


def test_both_empty_returns_all_zeros():
    result = evaluate_extraction([], [])
    assert result.precision == pytest.approx(0.0)
    assert result.recall == pytest.approx(0.0)
    assert result.f1 == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# load_gold_subtypes
# ---------------------------------------------------------------------------


def test_load_gold_subtypes_returns_list(tmp_path):
    data = [{"id": "barrier", "name": "방벽"}, {"id": "sentry", "name": "파수"}]
    p = tmp_path / "card_subtypes.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = load_gold_subtypes(p)
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["id"] == "barrier"


def test_load_gold_subtypes_raises_on_non_array(tmp_path):
    p = tmp_path / "card_subtypes.json"
    p.write_text('{"id": "barrier"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Expected JSON array"):
        load_gold_subtypes(p)


# ---------------------------------------------------------------------------
# Real corpus smoke tests
# ---------------------------------------------------------------------------

CORPUS_KO_DIR = Path(
    r"C:\Users\SDS\Desktop\netrunner-corpus\netrunner-cards-json\v2\translations\ko"
)
TRAIN_DATA_PATH = Path(__file__).parent.parent / "data" / "train.json"


@pytest.mark.skipif(
    not CORPUS_KO_DIR.exists() or not TRAIN_DATA_PATH.exists(),
    reason="Corpus or train data not available",
)
def test_real_corpus_gold_has_88_entries():
    """card_subtypes.json must contain exactly 88 entries."""
    gold = load_gold_subtypes(CORPUS_KO_DIR / "card_subtypes.json")
    assert len(gold) == 88


@pytest.mark.skipif(
    not CORPUS_KO_DIR.exists() or not TRAIN_DATA_PATH.exists(),
    reason="Corpus or train data not available",
)
def test_real_corpus_recall_above_zero(capsys):
    """Extraction on train corpus must recover at least some card subtypes (recall > 0)."""
    import json as _json

    gold = load_gold_subtypes(CORPUS_KO_DIR / "card_subtypes.json")
    with TRAIN_DATA_PATH.open(encoding="utf-8") as fh:
        train_data = _json.load(fh)

    candidates = generate_candidates(train_data, min_cooccur=5)
    result = evaluate_extraction(candidates, gold)

    # Print the evaluation report for visibility in pytest -v output
    print(f"\n--- Term Extraction Evaluation (AC 5) ---")
    print(result.report())
    print(f"Found subtypes: {sorted(result.found_terms)}")
    print(f"Missed subtypes: {sorted(result.missed_terms)}")

    assert result.recall > 0.0, (
        "Extraction must recover at least 1 of the 88 card subtypes from the corpus. "
        f"Got recall={result.recall}"
    )
    assert result.gold_size == 88


@pytest.mark.skipif(
    not CORPUS_KO_DIR.exists() or not TRAIN_DATA_PATH.exists(),
    reason="Corpus or train data not available",
)
def test_real_corpus_evaluation_fields_are_consistent():
    """EvaluationResult fields must be internally consistent for real data."""
    import json as _json

    gold = load_gold_subtypes(CORPUS_KO_DIR / "card_subtypes.json")
    with TRAIN_DATA_PATH.open(encoding="utf-8") as fh:
        train_data = _json.load(fh)

    candidates = generate_candidates(train_data, min_cooccur=5)
    result = evaluate_extraction(candidates, gold)

    # TP + FP = extracted_size
    assert result.tp + result.fp == result.extracted_size
    # TP + FN = gold_size
    assert result.tp + result.fn == result.gold_size
    # found_terms and missed_terms are disjoint
    assert result.found_terms & result.missed_terms == frozenset()
    # Precision/recall/f1 are in [0, 1]
    assert 0.0 <= result.precision <= 1.0
    assert 0.0 <= result.recall <= 1.0
    assert 0.0 <= result.f1 <= 1.0


# ---------- hyphen normalization (부제 경로 인쇄 표기) ----------


def test_normalize_subtype_id_hyphen_to_space():
    assert _normalize_subtype_id("g-mod") == "g mod"


def test_normalize_subtype_id_mixed_separators():
    assert _normalize_subtype_id("Consumer-Grade") == "consumer grade"


def test_printed_hyphen_form_matches_underscored_gold_id():
    """'Consumer-Grade' on a card is gold id 'consumer_grade', not a false positive."""
    gold = _make_gold(["consumer_grade", "g_mod"])
    candidates = [_make_candidate("consumer-grade"), _make_candidate("g-mod")]
    result = evaluate_extraction(candidates, gold)
    assert result.tp == 2
    assert result.fp == 0
    assert result.fn == 0
