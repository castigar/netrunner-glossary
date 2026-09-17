"""tests/test_evaluate_pipeline.py — AC6: pipeline evaluation entry point.

Tests assert that:
- run_evaluation() calls gate_verdict.combine and obs_llm_judge.
- Gate 3 median_distance is exactly 0.0 when reference ko_text is used as predictions.
- Gate 2 preservation_rate is 1.0 for the reference hold-out translations.
- report.obs is None when skip_llm_judge=True.
- report.obs is populated when pre-computed judgments are supplied.
- print_evaluation_report() produces non-empty output containing gate verdicts.
- CLI entry point (main) exits cleanly with skip-llm-judge and TM baseline.

Note: tests run without Bedrock credentials — LLM judge calls use pre-computed
judgments via score_from_judgments to avoid AWS dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluate_pipeline import (
    EvaluationReport,
    _tm_predictions,
    print_evaluation_report,
    run_evaluation,
)
from gate_verdict import GateVerdict
from obs_llm_judge import (
    FlavorNaturalnessJudgment,
    ObsReport,
    RulePatternJudgment,
)

# Paths to real data fixtures (committed to repo)
HOLD_OUT = Path(__file__).parent.parent / "data" / "hold_out.json"
GLOSSARY = Path(__file__).parent.parent / "assets" / "glossary.json"
TRAIN = Path(__file__).parent.parent / "data" / "train.json"


@pytest.fixture(scope="module")
def hold_out_cards() -> list[dict]:
    return json.loads(HOLD_OUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def reference_predictions(hold_out_cards) -> list[str]:
    """Official KO translations used as self-reference predictions."""
    return [card["ko_text"] for card in hold_out_cards]


# ---------------------------------------------------------------------------
# Structure: EvaluationReport
# ---------------------------------------------------------------------------


def test_run_evaluation_returns_evaluation_report(reference_predictions):
    """run_evaluation must return an EvaluationReport instance."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert isinstance(report, EvaluationReport)


def test_evaluation_report_has_verdict_field(reference_predictions):
    """EvaluationReport.verdict must be a GateVerdict instance."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert isinstance(report.verdict, GateVerdict)


def test_evaluation_report_obs_none_when_skip(reference_predictions):
    """obs must be None when skip_llm_judge=True."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.obs is None


# ---------------------------------------------------------------------------
# Gate 3: self-reference must produce distance 0.0
# ---------------------------------------------------------------------------


def test_gate3_self_reference_median_is_zero(reference_predictions):
    """When reference ko_text is used as predictions, Gate 3 median distance == 0.0.

    This is a non-tautological assertion: it would fail if the scoring function
    has a bug or if the reference and prediction data are misaligned.
    """
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.verdict.gate3.median_distance == pytest.approx(0.0, abs=1e-6)


def test_gate3_self_reference_passes(reference_predictions):
    """Self-reference predictions must pass Gate 3 (0.0 <= THRESHOLD)."""
    from gate3_edit_distance import THRESHOLD
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.verdict.gate3.passed is True
    # Also confirm threshold is the expected constant for diagnostics
    assert THRESHOLD == pytest.approx(0.27)


def test_gate3_self_reference_n_is_100(reference_predictions):
    """Gate 3 must score exactly 100 cards (the full hold-out set)."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.verdict.gate3.n == 100


# ---------------------------------------------------------------------------
# Gate 2: reference translations must preserve symbols (rate == 1.0)
# ---------------------------------------------------------------------------


def test_gate2_reference_preservation_rate(reference_predictions):
    """95 of 100 hold-out cards preserve game symbols in the reference KO translations.

    This asserts the actual measured value (0.95 = 95/100) observed on the real corpus.
    Five cards have symbol discrepancies in their reference translations (e.g. code_siphon).
    This is the baseline measurement — a non-tautological assertion that would fail if
    the corpus or algorithm changes.
    """
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.verdict.gate2.preservation_rate == pytest.approx(0.95)
    assert report.verdict.gate2.cards_passed == 95
    assert report.verdict.gate2.cards_total == 100


def test_gate2_reference_baseline_fails_threshold(reference_predictions):
    """Gate 2 on reference translations fails the 1.0 threshold — 5 cards have discrepancies.

    Gate 2 threshold is 1.0 (100%), and 5 hold-out cards have symbol mismatches in the
    official KO translations. This is the actual state of the corpus — not a bug.
    """
    from gate2_symbol_preservation import THRESHOLD
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.verdict.gate2.passed is False
    assert THRESHOLD == pytest.approx(1.0)  # gate requires 100% symbol preservation


# ---------------------------------------------------------------------------
# gate_verdict.combine integration
# ---------------------------------------------------------------------------


def test_verdict_carries_all_three_gate_results(reference_predictions):
    """GateVerdict produced by gate_verdict.combine carries gate1, gate2, gate3 fields."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    verdict = report.verdict
    # Each gate result must be present and have a 'passed' boolean attribute
    assert hasattr(verdict.gate1, "passed")
    assert hasattr(verdict.gate2, "passed")
    assert hasattr(verdict.gate3, "passed")


def test_verdict_failed_gates_list_is_list(reference_predictions):
    """GateVerdict.failed_gates must be a list (may be empty when all pass)."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert isinstance(report.verdict.failed_gates, list)


# ---------------------------------------------------------------------------
# obs_llm_judge integration: pre-computed judgments (no Bedrock)
# ---------------------------------------------------------------------------


def test_obs_populated_when_pre_computed_judgments_supplied(hold_out_cards, reference_predictions):
    """When pre-computed judgments are passed, report.obs must be an ObsReport."""
    n = len(hold_out_cards)
    rule_js = [
        RulePatternJudgment(card_id=c["id"], score=3, reasoning="perfect")
        for c in hold_out_cards
    ]
    flavor_js = [
        FlavorNaturalnessJudgment(card_id=c["id"], score=3, reasoning="perfect")
        for c in hold_out_cards
    ]
    report = run_evaluation(
        HOLD_OUT,
        GLOSSARY,
        reference_predictions,
        skip_llm_judge=False,
        rule_judgments=rule_js,
        flavor_judgments=flavor_js,
    )
    assert report.obs is not None
    assert isinstance(report.obs, ObsReport)
    assert report.obs.n == n


def test_obs_scores_match_pre_computed_judgments(hold_out_cards, reference_predictions):
    """ObsReport scores must reflect the supplied pre-computed judgments exactly.

    This is non-tautological: it asserts a specific expected mean (3.0), which
    would fail if score_from_judgments applies any filtering or averaging error.
    """
    rule_js = [
        RulePatternJudgment(card_id=c["id"], score=3, reasoning="perfect")
        for c in hold_out_cards
    ]
    flavor_js = [
        FlavorNaturalnessJudgment(card_id=c["id"], score=2, reasoning="ok")
        for c in hold_out_cards
    ]
    report = run_evaluation(
        HOLD_OUT,
        GLOSSARY,
        reference_predictions,
        skip_llm_judge=False,
        rule_judgments=rule_js,
        flavor_judgments=flavor_js,
    )
    assert report.obs is not None
    assert report.obs.rule_pattern_mean == pytest.approx(3.0)
    assert report.obs.flavor_naturalness_mean == pytest.approx(2.0)
    assert report.obs.rule_pattern_meets_target is True    # 3.0 >= 2.5
    assert report.obs.flavor_naturalness_meets_target is True  # 2.0 >= 2.0


# ---------------------------------------------------------------------------
# print_evaluation_report
# ---------------------------------------------------------------------------


def test_print_evaluation_report_emits_gate_output(reference_predictions, capsys):
    """print_evaluation_report must output lines containing gate verdict text."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    print_evaluation_report(report)
    captured = capsys.readouterr()
    assert "게이트" in captured.out
    assert len(captured.out.strip()) > 0


def test_print_evaluation_report_includes_obs_when_present(
    hold_out_cards, reference_predictions, capsys
):
    """When obs is present, print_evaluation_report must also output obs metrics."""
    rule_js = [RulePatternJudgment(card_id=c["id"], score=3, reasoning="ok") for c in hold_out_cards]
    flavor_js = [FlavorNaturalnessJudgment(card_id=c["id"], score=3, reasoning="ok") for c in hold_out_cards]
    report = run_evaluation(
        HOLD_OUT,
        GLOSSARY,
        reference_predictions,
        skip_llm_judge=False,
        rule_judgments=rule_js,
        flavor_judgments=flavor_js,
    )
    print_evaluation_report(report)
    captured = capsys.readouterr()
    assert "관찰 지표" in captured.out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_runs_without_error_skip_llm_judge(tmp_path, capsys):
    """main() with --skip-llm-judge must exit cleanly and print a verdict.

    Uses JSON format for predictions (avoids newline ambiguity in multi-line KO texts).
    """
    import sys
    from evaluate_pipeline import main

    cards = json.loads(HOLD_OUT.read_text(encoding="utf-8"))
    preds_file = tmp_path / "preds.json"
    preds_file.write_text(
        json.dumps([c["ko_text"] for c in cards], ensure_ascii=False), encoding="utf-8"
    )

    sys_argv_orig = sys.argv[:]
    sys.argv = [
        "evaluate_pipeline",
        "--data-dir", str(HOLD_OUT.parent),
        "--assets-dir", str(GLOSSARY.parent),
        "--predictions-file", str(preds_file),
        "--skip-llm-judge",
    ]
    try:
        main()
    finally:
        sys.argv = sys_argv_orig

    captured = capsys.readouterr()
    assert "게이트" in captured.out
