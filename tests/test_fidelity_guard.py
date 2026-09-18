"""test_fidelity_guard.py — Tests for the rule text fidelity guardrail (§4.4).

AC: Do not add effects/conditions absent from the EN source, and do not omit
    effects/conditions present in the EN source.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fidelity_guard import (  # noqa: E402
    FidelityCheckResult,
    FidelityViolation,
    FidelityViolationError,
    check_fidelity,
)


# ---------------------------------------------------------------------------
# Passing cases — no violations
# ---------------------------------------------------------------------------


def test_passes_when_numbers_match():
    result = check_fidelity(
        "Gain 2[credit].",
        "2[credit]을 얻는다.",
    )
    assert result.passed
    assert result.violations == []


def test_passes_with_multiple_matching_numbers():
    result = check_fidelity(
        "Deal 1 net damage. Draw 3 cards.",
        "순 피해 1을 가한다. 카드 3장을 뽑는다.",
    )
    assert result.passed


def test_passes_when_no_numbers_in_either_text():
    result = check_fidelity(
        "Make a run on HQ.",
        "HQ에 런을 수행한다.",
    )
    assert result.passed


def test_passes_with_matching_symbols():
    result = check_fidelity(
        "Pay [credit][credit] to install.",
        "[credit][credit]을 지불해 설치한다.",
    )
    assert result.passed


def test_passes_when_conditional_and_ko_marker_present():
    result = check_fidelity(
        "If the Runner accesses this, deal 1 net damage.",
        "러너가 이 카드에 접근하면 순 피해 1을 가한다.",
    )
    assert result.passed


def test_passes_when_no_conditional_in_en():
    result = check_fidelity(
        "Gain 2[credit].",
        "2[credit]을 얻는다.",
    )
    assert result.passed


def test_passes_with_whenever_and_ko_marker():
    result = check_fidelity(
        "Whenever the Runner makes a successful run, gain 1[credit].",
        "러너가 런을 성공할 때마다 1[credit]을 얻는다.",
    )
    assert result.passed


def test_passes_with_empty_texts():
    result = check_fidelity("", "")
    assert result.passed


def test_passes_with_no_symbols_in_either():
    result = check_fidelity(
        "The Runner accesses 1 additional card.",
        "러너는 카드 1장을 추가로 접근한다.",
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Omitted number violations
# ---------------------------------------------------------------------------


def test_omitted_number_single():
    result = check_fidelity(
        "Gain 2[credit].",
        "[credit]을 얻는다.",
    )
    assert not result.passed
    kinds = {v.kind for v in result.violations}
    assert "omitted_number" in kinds


def test_omitted_number_detail_mentions_number():
    result = check_fidelity(
        "Deal 3 net damage.",
        "순 피해를 가한다.",
    )
    omitted = [v for v in result.violations if v.kind == "omitted_number"]
    assert omitted
    assert "3" in omitted[0].detail


def test_omitted_number_multiple_distinct():
    result = check_fidelity(
        "Gain 2[credit] and draw 4 cards.",
        "[credit]을 얻고 카드를 뽑는다.",
    )
    omitted_nums = {
        v.detail.split(":")[0].strip()
        for v in result.violations
        if v.kind == "omitted_number"
    }
    assert "2" in omitted_nums
    assert "4" in omitted_nums


def test_repeated_number_partially_omitted():
    # EN: 2 appears twice; KO: 2 appears once → one count omitted
    result = check_fidelity(
        "Pay 2[credit]. Gain 2[credit].",
        "2[credit]을 지불한다. [credit]을 얻는다.",
    )
    assert not result.passed
    omitted = [v for v in result.violations if v.kind == "omitted_number"]
    assert omitted
    assert "2" in omitted[0].detail


# ---------------------------------------------------------------------------
# Added number violations
# ---------------------------------------------------------------------------


def test_added_number_single():
    result = check_fidelity(
        "Gain [credit].",
        "3[credit]을 얻는다.",
    )
    assert not result.passed
    kinds = {v.kind for v in result.violations}
    assert "added_number" in kinds


def test_added_number_detail_mentions_number():
    result = check_fidelity(
        "Make a run.",
        "런을 수행하면 5[credit]을 얻는다.",
    )
    added = [v for v in result.violations if v.kind == "added_number"]
    assert added
    assert "5" in added[0].detail


# ---------------------------------------------------------------------------
# Symbol mismatch violations
# ---------------------------------------------------------------------------


def test_symbol_omitted_from_ko():
    result = check_fidelity(
        "Pay [credit][credit].",
        "[credit]을 지불한다.",
    )
    assert not result.passed
    kinds = {v.kind for v in result.violations}
    assert "symbol_mismatch" in kinds


def test_symbol_added_in_ko():
    result = check_fidelity(
        "Make a run.",
        "[click]을 사용해 런을 수행한다.",
    )
    assert not result.passed
    kinds = {v.kind for v in result.violations}
    assert "symbol_mismatch" in kinds


def test_symbol_mismatch_detail_mentions_symbol():
    result = check_fidelity(
        "Gain [recurring-credit].",
        "얻는다.",
    )
    sym_violations = [v for v in result.violations if v.kind == "symbol_mismatch"]
    assert sym_violations
    assert "[recurring-credit]" in sym_violations[0].detail


def test_multiple_distinct_symbols_match():
    result = check_fidelity(
        "Pay [click][credit] to install.",
        "[click][credit]을 지불해 설치한다.",
    )
    sym_violations = [v for v in result.violations if v.kind == "symbol_mismatch"]
    assert not sym_violations


def test_different_symbol_types_tracked_independently():
    # EN has 2× [credit], KO has 2× [click] — two mismatches
    result = check_fidelity(
        "Pay [credit][credit].",
        "[click][click]을 지불한다.",
    )
    sym_violations = [v for v in result.violations if v.kind == "symbol_mismatch"]
    assert len(sym_violations) == 2


# ---------------------------------------------------------------------------
# Omitted condition violations
# ---------------------------------------------------------------------------


def test_omitted_condition_when():
    result = check_fidelity(
        "When the Runner accesses this card, deal 1 net damage.",
        "순 피해 1을 가한다.",
    )
    assert not result.passed
    kinds = {v.kind for v in result.violations}
    assert "omitted_condition" in kinds


def test_omitted_condition_unless():
    result = check_fidelity(
        "Unless the Corp pays 2[credit], end the run.",
        "런을 종료한다.",
    )
    kinds = {v.kind for v in result.violations}
    assert "omitted_condition" in kinds


def test_omitted_condition_detail_mentions_count():
    result = check_fidelity(
        "If the Runner has fewer than 3 cards in grip, deal 1 net damage.",
        "순 피해 1을 가한다.",
    )
    cond_violations = [v for v in result.violations if v.kind == "omitted_condition"]
    assert cond_violations
    assert "1" in cond_violations[0].detail


def test_no_false_condition_violation_with_ko_marker():
    # "면" is a KO conditional marker
    result = check_fidelity(
        "If the Runner makes a run on HQ, gain 1[credit].",
        "러너가 HQ에 런을 수행하면 1[credit]을 얻는다.",
    )
    cond_violations = [v for v in result.violations if v.kind == "omitted_condition"]
    assert not cond_violations


def test_no_condition_violation_when_en_has_no_conditional():
    result = check_fidelity(
        "Gain 2[credit].",
        "2[credit]을 얻는다.",
    )
    cond_violations = [v for v in result.violations if v.kind == "omitted_condition"]
    assert not cond_violations


def test_ko_temporal_marker_때마다_satisfies_whenever():
    result = check_fidelity(
        "Whenever you install a program, gain 1[credit].",
        "프로그램을 설치할 때마다 1[credit]을 얻는다.",
    )
    cond_violations = [v for v in result.violations if v.kind == "omitted_condition"]
    assert not cond_violations


def test_ko_marker_않으면_satisfies_unless():
    result = check_fidelity(
        "Unless you pay 1[credit], end the run.",
        "1[credit]을 지불하지 않으면 런을 종료한다.",
    )
    cond_violations = [v for v in result.violations if v.kind == "omitted_condition"]
    assert not cond_violations


# ---------------------------------------------------------------------------
# Multiple violations in one check
# ---------------------------------------------------------------------------


def test_multiple_violations_reported_together():
    # Missing number AND missing condition
    result = check_fidelity(
        "If the Runner has accessed 3 cards this run, gain 2[credit].",
        "[credit]을 얻는다.",
    )
    assert not result.passed
    kinds = {v.kind for v in result.violations}
    assert len(kinds) >= 2


def test_passed_result_has_empty_violations():
    result = check_fidelity(
        "Gain 1[credit].",
        "1[credit]을 얻는다.",
    )
    assert result.passed
    assert result.violations == []


# ---------------------------------------------------------------------------
# Model types
# ---------------------------------------------------------------------------


def test_result_is_fidelity_check_result():
    result = check_fidelity("Gain 2[credit].", "2[credit]을 얻는다.")
    assert isinstance(result, FidelityCheckResult)


def test_violation_is_fidelity_violation_model():
    result = check_fidelity("Gain 2[credit].", "[credit]을 얻는다.")
    assert result.violations
    assert isinstance(result.violations[0], FidelityViolation)


# ---------------------------------------------------------------------------
# FidelityViolationError — interrupt signal
# ---------------------------------------------------------------------------


def test_violation_error_carries_result():
    result = check_fidelity("Gain 2[credit].", "얻는다.")
    err = FidelityViolationError(result)
    assert err.result is result


def test_violation_error_message_contains_kinds():
    result = check_fidelity("Gain 2[credit].", "얻는다.")
    err = FidelityViolationError(result)
    msg = str(err)
    # At least one violation kind appears in the message
    assert any(k in msg for k in ("omitted_number", "symbol_mismatch", "omitted_condition"))


def test_violation_error_inherits_exception():
    result = check_fidelity("Gain 2[credit].", "얻는다.")
    assert isinstance(FidelityViolationError(result), Exception)


def test_violation_error_count_in_message():
    result = check_fidelity("Gain 2[credit].", "얻는다.")
    err = FidelityViolationError(result)
    # Message format: "N fidelity violation(s): ..."
    assert "violation" in str(err)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_number_zero_preserved():
    result = check_fidelity(
        "The Corp gains 0[credit] when this is trashed.",
        "이것이 폐기되면 Corp는 0[credit]을 얻는다.",
    )
    num_violations = [v for v in result.violations if "0" in v.detail]
    assert not num_violations


def test_numbers_in_card_names_not_double_counted():
    # Typically card names don't appear in the text field, but guard against it
    result = check_fidelity(
        "Install Program: Memory Chip. Gain 1[mu].",
        "프로그램: 메모리 칩을 설치한다. 1[mu]을 얻는다.",
    )
    # '1' appears 1× in EN and 1× in KO — should pass for numbers
    num_violations = [v for v in result.violations if v.kind in ("omitted_number", "added_number")]
    assert not num_violations


def test_multiline_text_handled():
    result = check_fidelity(
        "Gain 2[credit].\nWhen you make a run, draw 1 card.",
        "2[credit]을 얻는다.\n런을 수행하면 카드 1장을 뽑는다.",
    )
    assert result.passed
