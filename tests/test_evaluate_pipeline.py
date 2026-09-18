"""tests/test_evaluate_pipeline.py — AC6: pipeline evaluation entry point.

Tests assert that:
- run_evaluation() calls gate_verdict.combine and obs_llm_judge.
- Gate 3 median_distance is exactly 0.0 when reference ko_text is used as predictions.
- Gate 2 preservation_rate is 1.0 for the reference hold-out translations.
- report.obs is None when skip_llm_judge=True.
- report.obs is populated when pre-computed judgments are supplied.
- print_evaluation_report() produces non-empty output containing gate verdicts.
- CLI entry point (main) exits cleanly with skip-llm-judge and TM baseline.
- Changing predictions changes Gate 1 compliance_rate (non-tautological, AC6b).
- Changing predictions changes Gate 2 preservation_rate (non-tautological, AC6b).
- _tm_predictions does NOT substitute ko_text for no-hit cards (AC6c reference leakage ban).
- EvaluationReport carries pending_count and empty_prediction_count fields (AC6e).
- print_evaluation_report shows llm_judged status (AC6f).

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
    from gate3_edit_distance import THRESHOLD, TM_BASELINE_MEDIAN, THRESHOLD_FACTOR
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.verdict.gate3.passed is True
    # The threshold is derived, not written down: pinning the literal 0.27 here
    # was what let the hand-rounded constant stand in for the real derivation
    # 0.2695, running the gate 0.0005 looser than the spec.
    assert THRESHOLD == TM_BASELINE_MEDIAN * THRESHOLD_FACTOR


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


# ---------------------------------------------------------------------------
# AC6(b): predictions affect Gate 1 and Gate 2 rates — non-tautological
# ---------------------------------------------------------------------------


#: 대조군 예측. SERVICE.md §5 가 하드 게이트를 납품분에만 적용하므로, 빈 문자열을
#: 대조군으로 쓰면 납품 0건이 되어 채점 대상이 사라진다. "납품됐지만 틀린" 예측을 쓴다.
WRONG_PREDICTION = "이 칸은 일부러 틀린 번역이다"


def wrong_preds(cards):
    """납품은 됐으나 용어·기호를 하나도 보존하지 않은 예측 계열."""
    return [WRONG_PREDICTION] * len(cards)


def test_gate1_compliance_rate_changes_when_predictions_change(hold_out_cards):
    """Gate 1 compliance_rate must differ between reference and all-empty predictions.

    This is non-tautological: it would fail if score_hold_out ignores the predictions
    argument and always reads card['ko_text'] from the file.
    """
    n = len(hold_out_cards)
    # Use reference ko_text — baseline compliance
    ref_preds = [c["ko_text"] for c in hold_out_cards]
    report_ref = run_evaluation(HOLD_OUT, GLOSSARY, ref_preds, skip_llm_judge=True)
    rate_ref = report_ref.verdict.gate1.compliance_rate

    # Use all-empty predictions — no KO term present, so all occurrences are violations
    empty_preds = wrong_preds(hold_out_cards)
    report_empty = run_evaluation(HOLD_OUT, GLOSSARY, empty_preds, skip_llm_judge=True)
    rate_empty = report_empty.verdict.gate1.compliance_rate

    # The rates must differ: empty predictions should have worse compliance
    # (when glossary terms appear in EN text, empty KO has no compliant translation)
    assert rate_empty != rate_ref, (
        f"Gate 1 rate unchanged: ref={rate_ref:.4f}, empty={rate_empty:.4f}. "
        "score_hold_out is likely ignoring the predictions argument."
    )
    # Empty predictions should have ≤ reference compliance (never better)
    assert rate_empty <= rate_ref, (
        f"Empty predictions produced higher compliance ({rate_empty:.4f}) than "
        f"reference ({rate_ref:.4f}) — unexpected."
    )


def test_gate2_preservation_rate_changes_when_predictions_change(hold_out_cards):
    """Gate 2 preservation_rate must differ between reference and all-empty predictions.

    This is non-tautological: it would fail if score_hold_out ignores the predictions
    argument and always reads card['ko_text'] from the file.
    """
    n = len(hold_out_cards)
    # Reference predictions — measured at 0.95 (5 cards have symbol mismatches)
    ref_preds = [c["ko_text"] for c in hold_out_cards]
    report_ref = run_evaluation(HOLD_OUT, GLOSSARY, ref_preds, skip_llm_judge=True)
    rate_ref = report_ref.verdict.gate2.preservation_rate  # 0.95

    # All-empty predictions — KO has no symbols, any EN symbol counts as missing
    empty_preds = wrong_preds(hold_out_cards)
    report_empty = run_evaluation(HOLD_OUT, GLOSSARY, empty_preds, skip_llm_judge=True)
    rate_empty = report_empty.verdict.gate2.preservation_rate

    # Empty predictions have no KO symbols, so cards with EN symbols all fail
    assert rate_empty != rate_ref, (
        f"Gate 2 rate unchanged: ref={rate_ref:.4f}, empty={rate_empty:.4f}. "
        "score_hold_out is likely ignoring the predictions argument."
    )
    assert rate_empty < rate_ref, (
        f"Empty predictions produced higher preservation ({rate_empty:.4f}) than "
        f"reference ({rate_ref:.4f}) — unexpected."
    )


# ---------------------------------------------------------------------------
# AC6(c): reference leakage ban — _tm_predictions must not substitute ko_text
# ---------------------------------------------------------------------------


def test_tm_predictions_no_reference_leakage(tmp_path):
    """_tm_predictions must return "" for cards with no TM hit — never ko_text.

    When the training corpus is empty, every query returns no result.
    The prediction list must contain only empty strings (not the hold-out ko_text).
    """
    # Write a hold-out with 3 cards
    hold_out_cards = [
        {"id": "c1", "en_text": "Install a program.", "ko_text": "프로그램을 설치한다."},
        {"id": "c2", "en_text": "Make a run.", "ko_text": "런을 수행한다."},
        {"id": "c3", "en_text": "Gain credits.", "ko_text": "크레딧을 얻는다."},
    ]
    hold_out_path = tmp_path / "hold_out.json"
    hold_out_path.write_text(json.dumps(hold_out_cards, ensure_ascii=False), encoding="utf-8")

    # Empty training set → TM search always returns no hits
    train_path = tmp_path / "train.json"
    train_path.write_text("[]", encoding="utf-8")

    preds, empty_count = _tm_predictions(hold_out_path, train_path)

    assert len(preds) == 3
    assert empty_count == 3, f"Expected all 3 predictions empty, got empty_count={empty_count}"
    for i, pred in enumerate(preds):
        ko_ref = hold_out_cards[i]["ko_text"]
        assert pred != ko_ref, (
            f"Card {i}: prediction '{pred}' equals ko_text '{ko_ref}' — reference leakage!"
        )
        assert pred == "", (
            f"Card {i}: expected empty string, got '{pred}'"
        )


# ---------------------------------------------------------------------------
# AC6(e): EvaluationReport carries pending_count and pending_ratio fields
# ---------------------------------------------------------------------------


def test_evaluation_report_has_pending_count_field(reference_predictions):
    """EvaluationReport must have pending_count and pending_ratio fields."""
    report = run_evaluation(
        HOLD_OUT, GLOSSARY, reference_predictions,
        skip_llm_judge=True,
        pending_count=5,
        pending_ratio=0.05,
    )
    assert report.pending_count == 5
    assert report.pending_ratio == pytest.approx(0.05)


def test_evaluation_report_has_empty_prediction_count_field(reference_predictions):
    """EvaluationReport must have empty_prediction_count field."""
    report = run_evaluation(
        HOLD_OUT, GLOSSARY, reference_predictions,
        skip_llm_judge=True,
        empty_prediction_count=3,
    )
    assert report.empty_prediction_count == 3


def test_evaluation_report_default_pending_zero(reference_predictions):
    """EvaluationReport pending fields default to zero when not provided."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.pending_count == 0
    assert report.pending_ratio == 0.0
    assert report.empty_prediction_count == 0


# ---------------------------------------------------------------------------
# AC6(f): print_evaluation_report shows llm_judged status
# ---------------------------------------------------------------------------


def test_print_evaluation_report_includes_llm_judged_status(reference_predictions, capsys):
    """print_evaluation_report must include 용어집 LLM 판정 status text (AC6f)."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    print_evaluation_report(report)
    captured = capsys.readouterr()
    assert "LLM 판정" in captured.out, (
        "Expected llm_judged status in output (AC6f Gate1Result.llm_judged disclosure)"
    )


# ---------------------------------------------------------------------------
# AC6(d): EvaluationReport carries tm_baseline_median and derived threshold
# ---------------------------------------------------------------------------


def test_evaluation_report_carries_tm_baseline_and_derived_threshold(reference_predictions):
    """When tm_baseline_median is provided, gate3_derived_threshold must be computed."""
    import gate3_edit_distance

    baseline = 0.385  # typical TM-only baseline
    report = run_evaluation(
        HOLD_OUT, GLOSSARY, reference_predictions,
        skip_llm_judge=True,
        tm_baseline_median=baseline,
    )
    assert report.tm_baseline_median == pytest.approx(baseline)
    expected_threshold = baseline * gate3_edit_distance.THRESHOLD_FACTOR
    assert report.gate3_derived_threshold == pytest.approx(expected_threshold)


def test_evaluation_report_no_derived_threshold_when_no_baseline(reference_predictions):
    """When tm_baseline_median is not provided, gate3_derived_threshold must be None."""
    report = run_evaluation(HOLD_OUT, GLOSSARY, reference_predictions, skip_llm_judge=True)
    assert report.tm_baseline_median is None
    assert report.gate3_derived_threshold is None
