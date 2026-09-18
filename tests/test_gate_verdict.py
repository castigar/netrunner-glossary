"""test_gate_verdict.py — Combined hard gate verdict.

AC: 세 하드 게이트 중 하나라도 미달이면 불합격으로 판정한다.
"""
from __future__ import annotations

import pytest

from gate1_term_compliance import Gate1Result
from gate2_symbol_preservation import Gate2Result
from gate3_edit_distance import Gate3Result, THRESHOLD as G3_THRESHOLD
from gate_verdict import GateVerdict, combine, format_verdict


# ---------------------------------------------------------------------------
# Helpers: build minimal gate results
# ---------------------------------------------------------------------------


def _g1(passed: bool, rate: float = 1.0) -> Gate1Result:
    return Gate1Result(
        passed=passed,
        compliance_rate=rate,
        total_checks=10,
        total_violations=0 if passed else 1,
        llm_judged=True,
    )


def _g2(passed: bool, rate: float = 1.0) -> Gate2Result:
    cards = 10
    cards_passed = cards if passed else cards - 1
    return Gate2Result(
        passed=passed,
        preservation_rate=rate if passed else (cards - 1) / cards,
        cards_passed=cards_passed,
        cards_total=cards,
    )


def _g3(passed: bool) -> Gate3Result:
    median = 0.10 if passed else 0.50
    return Gate3Result(
        passed=passed,
        median_distance=median,
        mean_distance=median,
        min_distance=median,
        max_distance=median,
        n=10,
        threshold=G3_THRESHOLD,
    )


# ---------------------------------------------------------------------------
# combine — overall verdict logic
# ---------------------------------------------------------------------------


def test_all_pass_yields_overall_pass():
    verdict = combine(_g1(True), _g2(True), _g3(True))
    assert verdict.overall_passed is True


def test_gate1_fail_yields_overall_fail():
    verdict = combine(_g1(False), _g2(True), _g3(True))
    assert verdict.overall_passed is False


def test_gate2_fail_yields_overall_fail():
    verdict = combine(_g1(True), _g2(False), _g3(True))
    assert verdict.overall_passed is False


def test_gate3_fail_yields_overall_fail():
    verdict = combine(_g1(True), _g2(True), _g3(False))
    assert verdict.overall_passed is False


def test_all_fail_yields_overall_fail():
    verdict = combine(_g1(False), _g2(False), _g3(False))
    assert verdict.overall_passed is False


def test_failed_gates_lists_failed_indices_only():
    verdict = combine(_g1(False), _g2(True), _g3(False))
    assert verdict.failed_gates == [1, 3]


def test_failed_gates_empty_when_all_pass():
    verdict = combine(_g1(True), _g2(True), _g3(True))
    assert verdict.failed_gates == []


def test_failed_gates_all_three():
    verdict = combine(_g1(False), _g2(False), _g3(False))
    assert verdict.failed_gates == [1, 2, 3]


def test_gate1_fail_only():
    verdict = combine(_g1(False), _g2(True), _g3(True))
    assert verdict.failed_gates == [1]


def test_gate2_fail_only():
    verdict = combine(_g1(True), _g2(False), _g3(True))
    assert verdict.failed_gates == [2]


def test_gate3_fail_only():
    verdict = combine(_g1(True), _g2(True), _g3(False))
    assert verdict.failed_gates == [3]


# ---------------------------------------------------------------------------
# GateVerdict structure
# ---------------------------------------------------------------------------


def test_verdict_carries_gate1():
    g1 = _g1(True, rate=0.97)
    verdict = combine(g1, _g2(True), _g3(True))
    assert verdict.gate1 is g1


def test_verdict_carries_gate2():
    g2 = _g2(True, rate=1.0)
    verdict = combine(_g1(True), g2, _g3(True))
    assert verdict.gate2 is g2


def test_verdict_carries_gate3():
    g3 = _g3(True)
    verdict = combine(_g1(True), _g2(True), g3)
    assert verdict.gate3 is g3


def test_verdict_label_pass():
    verdict = combine(_g1(True), _g2(True), _g3(True))
    assert verdict.verdict_label == "합격"


def test_verdict_label_fail():
    verdict = combine(_g1(False), _g2(True), _g3(True))
    assert verdict.verdict_label == "불합격"


def test_returns_gate_verdict_instance():
    verdict = combine(_g1(True), _g2(True), _g3(True))
    assert isinstance(verdict, GateVerdict)


# ---------------------------------------------------------------------------
# format_verdict
# ---------------------------------------------------------------------------


def test_format_verdict_pass_contains_합격():
    verdict = combine(_g1(True), _g2(True), _g3(True))
    text = format_verdict(verdict)
    assert "합격" in text


def test_format_verdict_fail_contains_불합격():
    verdict = combine(_g1(False), _g2(True), _g3(True))
    text = format_verdict(verdict)
    assert "불합격" in text


def test_format_verdict_mentions_failed_gate_number():
    verdict = combine(_g1(True), _g2(False), _g3(True))
    text = format_verdict(verdict)
    assert "2" in text  # gate 2 failed


def test_format_verdict_all_gates_reported():
    verdict = combine(_g1(True), _g2(True), _g3(True))
    text = format_verdict(verdict)
    assert "게이트 1" in text
    assert "게이트 2" in text
    assert "게이트 3" in text
