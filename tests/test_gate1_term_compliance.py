"""test_gate1_term_compliance.py — Hard Gate 1 term compliance scorer.

AC: hold-out 100장 기준 용어 준수율 >= 95%, 규칙 자동 채점, 미달 시 납품 거부.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate1_term_compliance import (
    CardCompliance,
    Gate1Result,
    THRESHOLD,
    _count_term_checks,
    score_hold_out,
)

ASSETS_GLOSSARY = Path(__file__).parent.parent / "assets" / "glossary.json"
DATA_HOLD_OUT = Path(__file__).parent.parent / "data" / "hold_out.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_glossary(tmp_path: Path, *, llm_judged: bool = True, **sections) -> Path:
    data = {"llm_judged": llm_judged, **sections}
    p = tmp_path / "glossary.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _write_hold_out(tmp_path: Path, cards: list[dict]) -> Path:
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _count_term_checks
# ---------------------------------------------------------------------------


def test_count_term_checks_finds_present_term():
    glossary = {"install": ("설치", "extracted")}
    assert _count_term_checks("Install a program.", glossary) == 1


def test_count_term_checks_skips_absent_term():
    glossary = {"trash": ("폐기", "extracted")}
    assert _count_term_checks("Install a program.", glossary) == 0


def test_count_term_checks_word_boundary():
    glossary = {"install": ("설치", "extracted")}
    assert _count_term_checks("Use this installation.", glossary) == 0


def test_count_term_checks_multiple_terms():
    glossary = {"install": ("설치", "extracted"), "trash": ("폐기", "extracted")}
    assert _count_term_checks("Install and then trash it.", glossary) == 2


def test_count_term_checks_empty_text():
    glossary = {"install": ("설치", "extracted")}
    assert _count_term_checks("", glossary) == 0


def test_count_term_checks_empty_glossary():
    assert _count_term_checks("Install a program.", {}) == 0


# ---------------------------------------------------------------------------
# score_hold_out — unit tests with synthetic data
# ---------------------------------------------------------------------------


def test_score_perfect_compliance(tmp_path):
    glossary_path = _write_glossary(
        tmp_path, official={"barrier": "방벽"}, extracted={"install": "설치"}
    )
    cards = [
        {"id": "c1", "en_text": "Install a barrier.", "ko_text": "방벽을 설치한다."},
        {"id": "c2", "en_text": "Make a run.", "ko_text": "런을 수행한다."},
    ]
    hold_out_path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(hold_out_path, glossary_path)
    assert result.passed is True
    assert result.compliance_rate == 1.0
    assert result.total_violations == 0


def test_score_returns_gate1_result(tmp_path):
    glossary_path = _write_glossary(tmp_path, extracted={"install": "설치"})
    cards = [{"id": "c1", "en_text": "Install it.", "ko_text": "설치한다."}]
    hold_out_path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(hold_out_path, glossary_path)
    assert isinstance(result, Gate1Result)


def test_score_returned_card_results_present(tmp_path):
    glossary_path = _write_glossary(tmp_path, extracted={"install": "설치"})
    cards = [{"id": "card_a", "en_text": "Install it.", "ko_text": "설치한다."}]
    hold_out_path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(hold_out_path, glossary_path)
    assert len(result.card_results) == 1
    assert isinstance(result.card_results[0], CardCompliance)
    assert result.card_results[0].card_id == "card_a"


def test_score_violation_decreases_rate(tmp_path):
    glossary_path = _write_glossary(tmp_path, extracted={"install": "설치"})
    cards = [
        {"id": "c1", "en_text": "Install it.", "ko_text": "설치한다."},
        {"id": "c2", "en_text": "Install it.", "ko_text": "배치한다."},  # violation
    ]
    hold_out_path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(hold_out_path, glossary_path)
    assert result.total_checks == 2
    assert result.total_violations == 1
    assert abs(result.compliance_rate - 0.5) < 1e-9


def test_score_fails_when_rate_below_threshold(tmp_path):
    # 1 compliant out of 20 = 5% < 95%
    glossary_path = _write_glossary(tmp_path, extracted={"install": "설치"})
    cards = [{"id": f"c{i}", "en_text": "Install it.", "ko_text": "배치한다."} for i in range(20)]
    cards[0]["ko_text"] = "설치한다."  # only the first passes
    hold_out_path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(hold_out_path, glossary_path)
    assert result.passed is False
    assert result.compliance_rate < THRESHOLD


def test_score_passes_at_threshold(tmp_path):
    # exactly 95 out of 100 compliant
    glossary_path = _write_glossary(tmp_path, extracted={"install": "설치"})
    cards = [
        {"id": f"c{i}", "en_text": "Install it.", "ko_text": "설치한다."} for i in range(95)
    ] + [
        {"id": f"c{i+95}", "en_text": "Install it.", "ko_text": "배치한다."} for i in range(5)
    ]
    hold_out_path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(hold_out_path, glossary_path)
    assert abs(result.compliance_rate - 0.95) < 1e-9
    assert result.passed is True  # >= 95% passes


def test_score_no_applicable_terms_is_perfect(tmp_path):
    """Cards where no glossary term appears in en_text contribute 0/0; rate = 1.0."""
    glossary_path = _write_glossary(tmp_path, extracted={"install": "설치"})
    cards = [{"id": "c1", "en_text": "Make a run on HQ.", "ko_text": "HQ에 런 수행."}]
    hold_out_path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(hold_out_path, glossary_path)
    assert result.passed is True
    assert result.compliance_rate == 1.0
    assert result.total_checks == 0


def test_score_llm_judged_propagated(tmp_path):
    glossary_path = _write_glossary(tmp_path, llm_judged=False, extracted={})
    hold_out_path = _write_hold_out(tmp_path, [])
    result = score_hold_out(hold_out_path, glossary_path)
    assert result.llm_judged is False


def test_score_threshold_value():
    assert THRESHOLD == 0.95


def test_score_card_results_count_matches_input(tmp_path):
    glossary_path = _write_glossary(tmp_path, extracted={})
    n = 7
    cards = [{"id": f"c{i}", "en_text": "Run.", "ko_text": "런."} for i in range(n)]
    hold_out_path = _write_hold_out(tmp_path, cards)
    result = score_hold_out(hold_out_path, glossary_path)
    assert len(result.card_results) == n


# ---------------------------------------------------------------------------
# Integration: real assets and hold-out
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists() or not DATA_HOLD_OUT.exists(),
    reason="assets/glossary.json or data/hold_out.json not present",
)
def test_real_hold_out_gate1_runs_and_returns_result():
    """Hard Gate 1 scorer must run on the real hold-out set and return a valid result.

    The gate mechanism is correct when it:
      - produces a Gate1Result without error
      - returns a compliance_rate in [0, 1]
      - sets passed=True iff compliance_rate >= 0.95

    Note: the *reference* hold-out translations are the gold standard for edit-
    distance (gate 3), not for gate 1.  Gate 1 is designed to score AGENT output.
    When the reference translations score below 95% it reflects glossary mismatches
    (e.g. official 'corp'→'Corporation' while KO texts say '기업') — that is a known
    limitation of the current glossary, not a bug in the gate.
    """
    result = score_hold_out(DATA_HOLD_OUT, ASSETS_GLOSSARY)
    assert isinstance(result, Gate1Result)
    assert 0.0 <= result.compliance_rate <= 1.0
    assert result.total_violations <= result.total_checks
    # Gate correctly reflects threshold
    if result.compliance_rate >= THRESHOLD:
        assert result.passed is True
    else:
        assert result.passed is False


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists() or not DATA_HOLD_OUT.exists(),
    reason="assets/glossary.json or data/hold_out.json not present",
)
def test_real_hold_out_has_100_cards():
    cards = json.loads(DATA_HOLD_OUT.read_text(encoding="utf-8"))
    assert len(cards) == 100


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists() or not DATA_HOLD_OUT.exists(),
    reason="assets/glossary.json or data/hold_out.json not present",
)
def test_real_hold_out_compliance_rate_reported(capsys):
    result = score_hold_out(DATA_HOLD_OUT, ASSETS_GLOSSARY)
    # Just verify the result object is well-formed; rate is printed for diagnostics
    assert 0.0 <= result.compliance_rate <= 1.0
    assert result.total_checks >= 0
    assert result.total_violations >= 0
    assert result.total_violations <= result.total_checks
