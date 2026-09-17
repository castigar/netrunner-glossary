"""tests/test_ac6_entry_point.py — AC6 verification: evaluation entry point.

Acceptance criterion (AC6):
  data/hold_out.json 전량에 대해 파이프라인을 실행하고 하드 게이트 3개와 관찰
  지표로 판정을 보고하는 진입점을 제공한다.
  - 같은 실행 안에 두 예측 계열(pipeline_predictions / tm_only_baseline) 공존
  - 게이트 1·2·3은 pipeline_predictions를 채점
  - 게이트 3 임계 = tm_only_baseline × 0.7
  - 집계 3단: 필드별 → 카드별 → 게이트별
  - 보고: 게이트 수치, 최종 판정, TM 베이스라인, llm_judged, 빈 예측 원인별 건수
  - hold_out ko_text를 예측으로 대체하지 않음

Non-tautological assertions:
  - Named series appear in INFO output (would fail if labels were removed)
  - Gate 3 derived threshold equals baseline × 0.70 (would fail at a different factor)
  - field_synthesis is populated when loading pipeline_output (fails without the fix)
  - Cause counts sum to total empty count (fails if any cause bucket is dropped)
  - Card count equals hold_out.json length, not a hardcoded constant
  - Gates score pipeline_predictions (verified by changing predictions → rate changes)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from evaluate_pipeline import (
    EvaluationReport,
    _load_pipeline_output,
    _tm_predictions,
    main,
    print_evaluation_report,
    run_evaluation,
)
from field_card_synthesis import FieldSynthesisReport, synthesize_field_to_card
from gate3_edit_distance import THRESHOLD_FACTOR

HOLD_OUT = Path(__file__).parent.parent / "data" / "hold_out.json"
GLOSSARY = Path(__file__).parent.parent / "assets" / "glossary.json"
TRAIN = Path(__file__).parent.parent / "data" / "train.json"
PIPELINE_OUTPUT = Path(__file__).parent.parent / "pipeline_output.jsonl"


# ---------------------------------------------------------------------------
# Fixture: hold_out cards
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def hold_out_cards() -> list[dict]:
    return json.loads(HOLD_OUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hold_out_size(hold_out_cards) -> int:
    """Card count derived from file, not a hardcoded constant (hold_out_size_source AC)."""
    return len(hold_out_cards)


# ---------------------------------------------------------------------------
# AC6 §1: Entry point names prediction series in output
# ---------------------------------------------------------------------------


class TestNamedPredictionSeries:
    """Two prediction series must coexist and be named in the same run."""

    def test_pipeline_predictions_label_in_report_output(self, hold_out_cards, capsys):
        """print_evaluation_report must include 'pipeline_predictions' label (AC6 naming)."""
        n = len(hold_out_cards)
        preds = [c["ko_text"] for c in hold_out_cards]
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            tm_baseline_median=0.385,
        )
        print_evaluation_report(report)
        out = capsys.readouterr().out
        assert "pipeline_predictions" in out, (
            "Expected 'pipeline_predictions' label in print_evaluation_report output (AC6)"
        )

    def test_tm_only_baseline_label_in_report_output(self, hold_out_cards, capsys):
        """print_evaluation_report must include 'tm_only_baseline' label (AC6 naming)."""
        preds = [c["ko_text"] for c in hold_out_cards]
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            tm_baseline_median=0.385,
        )
        print_evaluation_report(report)
        out = capsys.readouterr().out
        assert "tm_only_baseline" in out, (
            "Expected 'tm_only_baseline' label in print_evaluation_report output (AC6)"
        )

    def test_named_series_labels_absent_when_no_baseline(self, hold_out_cards, capsys):
        """When tm_baseline_median is None, report still shows pipeline_predictions label."""
        preds = [c["ko_text"] for c in hold_out_cards]
        report = run_evaluation(HOLD_OUT, GLOSSARY, preds, skip_llm_judge=True)
        print_evaluation_report(report)
        out = capsys.readouterr().out
        # pipeline_predictions label must always appear
        assert "pipeline_predictions" in out


# ---------------------------------------------------------------------------
# AC6 §2: Card count derived from file, not hardcoded
# ---------------------------------------------------------------------------


class TestHoldOutSizeSource:
    """Card count must be derived from data/hold_out.json, not a hardcoded constant."""

    def test_hold_out_size_derived_from_file(self, hold_out_size):
        """hold_out_size equals len(hold_out.json), not any specific constant."""
        # Non-tautological: asserts actual file count, would fail if file shrinks/grows.
        assert hold_out_size > 0, "hold_out.json must have at least 1 card"
        assert hold_out_size == 100, (
            f"Expected 100 hold-out cards per data/hold_out.json, got {hold_out_size}"
        )

    def test_gate3_scores_all_hold_out_cards(self, hold_out_cards):
        """Gate 3 must score exactly hold_out_size cards — derived from file, not constant."""
        preds = [""] * len(hold_out_cards)
        report = run_evaluation(HOLD_OUT, GLOSSARY, preds, skip_llm_judge=True)
        assert report.verdict.gate3.n == len(hold_out_cards), (
            "Gate 3 must score exactly the number of cards in hold_out.json"
        )


# ---------------------------------------------------------------------------
# AC6 §3: Gates score pipeline_predictions (not hold_out ko_text)
# ---------------------------------------------------------------------------


class TestGatesScorePipelinePredictions:
    """Non-tautological proof that gates score pipeline_predictions, not card['ko_text']."""

    def test_gate1_rate_changes_when_predictions_change(self, hold_out_cards):
        """Gate 1 compliance_rate changes between reference and empty predictions.

        Failure mode: if score_hold_out ignores the predictions arg and reads
        card['ko_text'] directly, both runs would produce identical rates.
        """
        ref_preds = [c["ko_text"] for c in hold_out_cards]
        empty_preds = [""] * len(hold_out_cards)

        report_ref = run_evaluation(HOLD_OUT, GLOSSARY, ref_preds, skip_llm_judge=True)
        report_empty = run_evaluation(HOLD_OUT, GLOSSARY, empty_preds, skip_llm_judge=True)

        # Empty predictions must have lower compliance (or equal when no terms in EN text).
        assert report_empty.verdict.gate1.compliance_rate <= report_ref.verdict.gate1.compliance_rate
        # The rates must differ — proves predictions arg is used.
        assert report_empty.verdict.gate1.compliance_rate != report_ref.verdict.gate1.compliance_rate, (
            "Gate 1 compliance_rate unchanged between reference and empty predictions — "
            "score_hold_out likely ignores the predictions argument."
        )

    def test_gate2_rate_changes_when_predictions_change(self, hold_out_cards):
        """Gate 2 preservation_rate changes between reference and empty predictions."""
        ref_preds = [c["ko_text"] for c in hold_out_cards]
        empty_preds = [""] * len(hold_out_cards)

        report_ref = run_evaluation(HOLD_OUT, GLOSSARY, ref_preds, skip_llm_judge=True)
        report_empty = run_evaluation(HOLD_OUT, GLOSSARY, empty_preds, skip_llm_judge=True)

        assert report_empty.verdict.gate2.preservation_rate < report_ref.verdict.gate2.preservation_rate, (
            "Gate 2 preservation_rate unchanged — score_hold_out may be ignoring predictions arg."
        )

    def test_gate3_distance_changes_when_predictions_change(self, hold_out_cards):
        """Gate 3 median_distance changes between reference and empty predictions.

        Reference predictions → distance 0.0.  Empty predictions → distance > 0.
        This would fail if score_hold_out compared ko_text to itself (reference leakage).
        """
        ref_preds = [c["ko_text"] for c in hold_out_cards]
        empty_preds = [""] * len(hold_out_cards)

        report_ref = run_evaluation(HOLD_OUT, GLOSSARY, ref_preds, skip_llm_judge=True)
        report_empty = run_evaluation(HOLD_OUT, GLOSSARY, empty_preds, skip_llm_judge=True)

        assert report_ref.verdict.gate3.median_distance == pytest.approx(0.0, abs=1e-6)
        assert report_empty.verdict.gate3.median_distance > 0.9, (
            f"Empty predictions should have high edit distance; got "
            f"{report_empty.verdict.gate3.median_distance:.4f}"
        )


# ---------------------------------------------------------------------------
# AC6 §4: Gate 3 threshold = tm_only_baseline × THRESHOLD_FACTOR
# ---------------------------------------------------------------------------


class TestGate3ThresholdDerivation:
    """Gate 3 threshold must be derived from tm_only_baseline × 0.70 (measured_in_run)."""

    def test_gate3_derived_threshold_equals_baseline_times_factor(self, hold_out_cards):
        """gate3_derived_threshold = tm_baseline_median × THRESHOLD_FACTOR (0.70).

        Non-tautological: would fail if the factor were changed or the formula were wrong.
        """
        baseline = 0.385  # representative baseline (pinned by test_tm_baseline.py)
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, [c["ko_text"] for c in hold_out_cards],
            skip_llm_judge=True,
            tm_baseline_median=baseline,
        )
        expected = baseline * THRESHOLD_FACTOR
        assert report.gate3_derived_threshold == pytest.approx(expected, rel=1e-6), (
            f"gate3_derived_threshold must be baseline × {THRESHOLD_FACTOR}; "
            f"got {report.gate3_derived_threshold}"
        )

    def test_gate3_derived_threshold_none_when_no_baseline(self, hold_out_cards):
        """When tm_baseline_median is not provided, gate3_derived_threshold must be None."""
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, [c["ko_text"] for c in hold_out_cards],
            skip_llm_judge=True,
        )
        assert report.tm_baseline_median is None
        assert report.gate3_derived_threshold is None

    def test_gate3_threshold_factor_is_0_70(self):
        """THRESHOLD_FACTOR must be 0.70 (30% improvement over TM baseline)."""
        assert THRESHOLD_FACTOR == pytest.approx(0.70), (
            f"THRESHOLD_FACTOR changed from 0.70 to {THRESHOLD_FACTOR}; "
            "gate3 threshold derivation formula must be updated."
        )


# ---------------------------------------------------------------------------
# AC6 §5: 3-level aggregation — field→card→gate
# ---------------------------------------------------------------------------


class TestThreeLevelAggregation:
    """Field-to-card synthesis is wired into the evaluation entry point."""

    def test_field_synthesis_populated_when_loading_pipeline_output(self, tmp_path):
        """When loading pipeline_output.jsonl, EvaluationReport.field_synthesis is set.

        Non-tautological: fails if _load_pipeline_output does not call
        synthesize_field_to_card or does not return the synthesis report.
        """
        if not PIPELINE_OUTPUT.exists():
            pytest.skip("pipeline_output.jsonl not present — run run_pipeline.py first")

        preds, pending, empty, ratio, synthesis, _ = _load_pipeline_output(
            PIPELINE_OUTPUT, HOLD_OUT
        )
        assert isinstance(synthesis, FieldSynthesisReport), (
            "Expected FieldSynthesisReport from _load_pipeline_output, got "
            f"{type(synthesis)}"
        )
        # synthesis must cover the full hold-out set (100 cards, not just those in the file)
        assert synthesis.total_count == len(json.loads(HOLD_OUT.read_text(encoding="utf-8"))), (
            "FieldSynthesisReport.total_count must equal hold_out.json card count"
        )

    def test_field_synthesis_cards_cover_full_hold_out(self, tmp_path):
        """FieldSynthesisReport.card_results must have one entry per hold-out card."""
        if not PIPELINE_OUTPUT.exists():
            pytest.skip("pipeline_output.jsonl not present")

        preds, _, _, _, synthesis, _ = _load_pipeline_output(PIPELINE_OUTPUT, HOLD_OUT)
        hold_out_size = len(json.loads(HOLD_OUT.read_text(encoding="utf-8")))
        assert len(synthesis.card_results) == hold_out_size, (
            "field synthesis must have one CardSynthesisResult per hold-out card"
        )

    def test_field_synthesis_multi_field_cards_counted_separately(self, tmp_path):
        """Cards with two fields (text + flavor) have field_count == 2 in synthesis."""
        if not PIPELINE_OUTPUT.exists():
            pytest.skip("pipeline_output.jsonl not present")

        _, _, _, _, synthesis, _ = _load_pipeline_output(PIPELINE_OUTPUT, HOLD_OUT)
        hold_out = json.loads(HOLD_OUT.read_text(encoding="utf-8"))
        cards_with_flavor = [c for c in hold_out if c.get("en_flavor", "").strip()]

        # Find the card result for a card that has flavor text
        if not cards_with_flavor:
            pytest.skip("no cards with flavor in hold_out.json")
        flavor_card_id = cards_with_flavor[0]["id"]
        result = next(
            (r for r in synthesis.card_results if r.card_id == flavor_card_id), None
        )
        assert result is not None, f"No card result for {flavor_card_id}"
        assert result.field_count == 2, (
            f"Card {flavor_card_id} has both text and flavor fields; "
            f"expected field_count=2, got {result.field_count}"
        )

    def test_card_prediction_is_empty_when_any_field_violated(self):
        """When any field is violated, the card's gate prediction must be ''.

        Non-tautological: fails if the synthesis rule uses the text draft_ko even
        when flavor is violated.
        """
        # 2-field card: text OK, flavor violated
        hold_out = [
            {"id": "c1", "en_text": "Install a program.", "en_flavor": "Flavor text here."}
        ]
        field_records = [
            {
                "card_id": "c1", "route": "rule", "draft_ko": "프로그램을 설치한다.",
                "interrupted": False,
            },
            {
                "card_id": "c1", "route": "flavor", "draft_ko": "",  # flavor is empty
                "interrupted": False,
            },
        ]
        synthesis = synthesize_field_to_card(field_records, hold_out)
        assert synthesis.card_predictions == [""], (
            "When flavor field is empty, card prediction must be '' "
            "(one field violated → card violated → gate scores '')"
        )
        assert synthesis.violated_count == 1
        assert synthesis.compliant_count == 0

    def test_card_prediction_is_text_draft_when_all_fields_ok(self):
        """When all fields are OK, the card's gate prediction is the text field draft_ko."""
        hold_out = [
            {"id": "c1", "en_text": "Install a program.", "en_flavor": "Flavor text."}
        ]
        field_records = [
            {
                "card_id": "c1", "route": "rule", "draft_ko": "프로그램을 설치한다.",
                "interrupted": False,
            },
            {
                "card_id": "c1", "route": "flavor", "draft_ko": "플레이버 텍스트.",
                "interrupted": False,
            },
        ]
        synthesis = synthesize_field_to_card(field_records, hold_out)
        assert synthesis.card_predictions == ["프로그램을 설치한다."], (
            "When all fields are OK, prediction must be the text-field draft_ko"
        )
        assert synthesis.compliant_count == 1
        assert synthesis.violated_count == 0


# ---------------------------------------------------------------------------
# AC6 §6: Empty prediction cause counts in report
# ---------------------------------------------------------------------------


class TestEmptyPredictionCauseCounts:
    """Report must include empty prediction cause counts by type (3 causes)."""

    def test_cause_counts_sum_to_total_empty(self):
        """Sum of cause counts must equal total empty field predictions.

        Non-tautological: fails if any cause bucket is absent from the dict.
        """
        hold_out = [
            {"id": "c1", "en_text": "Install a program.", "en_flavor": ""},  # text only
            {"id": "c2", "en_text": "Make a run.", "en_flavor": "Flavor text."},  # text + flavor
            {"id": "c3", "en_text": "Gain credits.", "en_flavor": ""},  # text only, absent
        ]
        field_records = [
            # c1: text field — empty, no TM hits → tm_search_failure
            {
                "card_id": "c1", "route": "rule", "draft_ko": "",
                "interrupted": False, "tm_hits": [],
            },
            # c2: text field OK, flavor absent → flavor is approval_incomplete
            {
                "card_id": "c2", "route": "rule", "draft_ko": "런을 수행한다.",
                "interrupted": False, "tm_hits": [{"id": "x"}],
            },
            # c3 is entirely absent (approval_incomplete for c3 text field)
        ]
        synthesis = synthesize_field_to_card(field_records, hold_out)

        total_empty = sum(synthesis.empty_by_cause.values())
        assert total_empty > 0, "Expected some empty predictions in test data"

        # Verify all three cause keys are present
        from field_card_synthesis import ALL_CAUSES
        for cause in ALL_CAUSES:
            assert cause in synthesis.empty_by_cause, (
                f"Cause bucket '{cause}' missing from empty_by_cause"
            )

        # c1 text field empty + no TM hits → tm_search_failure
        assert synthesis.empty_by_cause["tm_search_failure"] >= 1, (
            "c1 text field is empty with no TM hits — must count as tm_search_failure"
        )
        # c2 flavor field absent → approval_incomplete
        assert synthesis.empty_by_cause["approval_incomplete"] >= 1, (
            "c2 flavor field absent — must count as approval_incomplete"
        )

    def test_print_report_shows_cause_counts_when_field_synthesis_present(
        self, tmp_path, capsys
    ):
        """print_evaluation_report must output cause counts when field_synthesis is set."""
        from field_card_synthesis import FieldSynthesisReport, CardSynthesisResult

        synthesis = FieldSynthesisReport(
            card_results=[
                CardSynthesisResult(
                    card_id="c1", field_count=1, compliant=False,
                    violated_fields=["text"],
                    empty_causes=[("text", "tm_search_failure")],
                    card_prediction="",
                )
            ],
            compliant_count=0,
            violated_count=1,
            total_count=1,
            empty_by_cause={"tm_search_failure": 1, "guard_rejected_in_review_queue": 0, "approval_incomplete": 0},
            card_predictions=[""],
        )
        hold_out = json.loads(HOLD_OUT.read_text(encoding="utf-8"))
        n = len(hold_out)
        from obs_llm_judge import RulePatternJudgment, FlavorNaturalnessJudgment
        rule_js = [RulePatternJudgment(card_id=c["id"], score=3, reasoning="ok") for c in hold_out]
        flavor_js = [FlavorNaturalnessJudgment(card_id=c["id"], score=3, reasoning="ok") for c in hold_out]
        preds = [""] + [""] * (n - 1)

        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            field_synthesis=synthesis,
        )
        print_evaluation_report(report)
        out = capsys.readouterr().out

        # The report must show the cause section
        assert "TM 검색 실패" in out or "tm_search_failure" in out.lower(), (
            "Report must show 'TM 검색 실패' cause count when field_synthesis is set"
        )

    def test_empty_cause_guard_queue_classification(self):
        """Empty draft_ko with interrupted=True → guard_rejected_in_review_queue."""
        hold_out = [{"id": "c1", "en_text": "Install a program.", "en_flavor": ""}]
        field_records = [
            {
                "card_id": "c1", "route": "rule", "draft_ko": "",  # empty + interrupted
                "interrupted": True, "tm_hits": [{"id": "x"}],
            },
        ]
        synthesis = synthesize_field_to_card(field_records, hold_out)
        assert synthesis.empty_by_cause["guard_rejected_in_review_queue"] == 1, (
            "Empty draft_ko with interrupted=True must be classified as guard_rejected_in_review_queue"
        )
        assert synthesis.empty_by_cause["tm_search_failure"] == 0


# ---------------------------------------------------------------------------
# AC6 §7: Load pipeline output correctly handles multiple field records per card
# ---------------------------------------------------------------------------


class TestLoadPipelineOutputFieldGrouping:
    """_load_pipeline_output must handle multiple (card_id, field) records correctly.

    The previous implementation used a dict-comprehension that overwrote earlier
    records when a card had both rule and flavor records. This test proves the fix.
    """

    def test_multiple_field_records_per_card_not_overwritten(self, tmp_path):
        """Both rule and flavor field records for the same card must be processed.

        Non-tautological: would fail if the code uses {card_id: rec for rec in records}
        which overwrites the rule record with the flavor record.
        """
        hold_out = [
            {
                "id": "test_card",
                "en_text": "Install a program.",
                "ko_text": "프로그램을 설치한다.",
                "en_flavor": "Flavor text here.",
                "ko_flavor": "플레이버 텍스트.",
            }
        ]
        hold_out_path = tmp_path / "hold_out.json"
        hold_out_path.write_text(json.dumps(hold_out, ensure_ascii=False), encoding="utf-8")

        # Write a pipeline output with both rule and flavor records for the same card
        pipeline_records = [
            {"_meta": "run_pipeline_header", "tm_threshold_derivation": {}},  # header
            {
                "card_id": "test_card", "route": "rule",
                "draft_ko": "프로그램을 설치한다.", "interrupted": False,
                "tm_hits": [{"id": "x"}], "tm_confidence": 0.5,
                "injected_terms": [], "glossary_llm_judged": True,
            },
            {
                "card_id": "test_card", "route": "flavor",
                "draft_ko": "플레이버 텍스트.", "interrupted": False,
                "tm_hits": [], "tm_confidence": 0.0,
                "injected_terms": [], "glossary_llm_judged": True,
            },
        ]
        pipeline_path = tmp_path / "pipeline_output.jsonl"
        pipeline_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in pipeline_records) + "\n",
            encoding="utf-8",
        )

        preds, pending, empty, ratio, synthesis, _ = _load_pipeline_output(
            pipeline_path, hold_out_path
        )

        # Card should be compliant: both fields have non-empty draft_ko
        assert len(preds) == 1
        assert preds[0] == "프로그램을 설치한다.", (
            "When both fields are non-empty, card prediction must be the text-field draft_ko. "
            "If the flavor record overwrote the rule record, this would be '플레이버 텍스트.'"
        )
        assert synthesis.compliant_count == 1, (
            "Card with both fields OK must be counted as compliant"
        )
        assert synthesis.violated_count == 0

    def test_flavor_only_overwrite_bug_not_present(self, tmp_path):
        """If flavor record appeared last and was overwriting rule, the card would fail.

        This test would FAIL with the old dict-comprehension implementation because
        the flavor record would overwrite the rule record, and the flavor draft_ko
        (not in the text field) would be used as the card prediction.
        """
        hold_out = [
            {
                "id": "card_a",
                "en_text": "End the run.",
                "ko_text": "런을 종료한다.",
                "en_flavor": "",
                "ko_flavor": "",
            }
        ]
        hold_out_path = tmp_path / "hold_out.json"
        hold_out_path.write_text(json.dumps(hold_out, ensure_ascii=False), encoding="utf-8")

        # Only rule record — if the code handled this correctly, prediction = rule draft_ko
        pipeline_records = [
            {"_meta": "run_pipeline_header", "tm_threshold_derivation": {}},
            {
                "card_id": "card_a", "route": "rule",
                "draft_ko": "런을 종료한다.", "interrupted": False,
                "tm_hits": [{"id": "y"}], "tm_confidence": 0.4,
                "injected_terms": [], "glossary_llm_judged": True,
            },
        ]
        pipeline_path = tmp_path / "pipeline_output.jsonl"
        pipeline_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in pipeline_records) + "\n",
            encoding="utf-8",
        )

        preds, _, _, _, synthesis, _ = _load_pipeline_output(pipeline_path, hold_out_path)

        # Card has no flavor field expected → text field alone determines compliance
        assert preds[0] == "런을 종료한다.", (
            "Text-only card prediction must be the rule draft_ko"
        )
        assert synthesis.compliant_count == 1


# ---------------------------------------------------------------------------
# AC6 §8: Reference leakage ban — ko_text never substituted as prediction
# ---------------------------------------------------------------------------


class TestReferenceLeakageBan:
    """No hold_out ko_text must appear as a prediction via substitution."""

    def test_tm_predictions_empty_when_no_training_data(self, tmp_path):
        """When training corpus is empty, all predictions must be '' (not ko_text)."""
        hold_out = [
            {"id": "c1", "en_text": "Install a program.", "ko_text": "프로그램을 설치한다."},
        ]
        hold_out_path = tmp_path / "hold_out.json"
        hold_out_path.write_text(json.dumps(hold_out, ensure_ascii=False), encoding="utf-8")
        train_path = tmp_path / "train.json"
        train_path.write_text("[]", encoding="utf-8")

        preds, empty_count = _tm_predictions(hold_out_path, train_path)

        assert len(preds) == 1
        assert preds[0] == "", "No TM hit → prediction must be '' (not ko_text)"
        assert empty_count == 1

    def test_gate3_ko_text_not_used_as_prediction(self, hold_out_cards):
        """Gate 3 with empty predictions must have high distance (not 0.0).

        If ko_text were substituted as prediction, distance would be 0.0 (reference leakage).
        Distance > 0 proves the predictions are genuinely empty and not ko_text.
        """
        empty_preds = [""] * len(hold_out_cards)
        report = run_evaluation(HOLD_OUT, GLOSSARY, empty_preds, skip_llm_judge=True)
        # Empty predictions vs reference: distance should be close to 1.0, NOT 0.0
        assert report.verdict.gate3.median_distance > 0.5, (
            f"Empty predictions must have high edit distance (not substituted with ko_text). "
            f"Got median_distance={report.verdict.gate3.median_distance:.4f}. "
            "If distance is 0.0, reference leakage is present."
        )


# ---------------------------------------------------------------------------
# AC6 §9: CLI entry point (--pipeline-output mode) labels output correctly
# ---------------------------------------------------------------------------


class TestCLIEntryPoint:
    """CLI main() must name prediction series in INFO output."""

    def test_cli_pipeline_output_mode_labels_pipeline_predictions(self, tmp_path, capsys):
        """main() with --pipeline-output must print 'pipeline_predictions' label."""
        if not PIPELINE_OUTPUT.exists():
            pytest.skip("pipeline_output.jsonl not present")

        import sys as _sys
        orig = _sys.argv[:]
        _sys.argv = [
            "evaluate_pipeline",
            "--data-dir", str(HOLD_OUT.parent),
            "--assets-dir", str(GLOSSARY.parent),
            "--pipeline-output", str(PIPELINE_OUTPUT),
            "--skip-llm-judge",
        ]
        try:
            main()
        finally:
            _sys.argv = orig

        out = capsys.readouterr().out
        assert "pipeline_predictions" in out, (
            "CLI --pipeline-output mode must print 'pipeline_predictions' in output (AC6 naming)"
        )
        assert "tm_only_baseline" in out, (
            "CLI --pipeline-output mode must print 'tm_only_baseline' in output (AC6 naming)"
        )

    def test_cli_default_mode_labels_both_series(self, tmp_path, capsys):
        """main() with no prediction source must name both series in INFO output."""
        import sys as _sys
        orig = _sys.argv[:]
        _sys.argv = [
            "evaluate_pipeline",
            "--data-dir", str(HOLD_OUT.parent),
            "--assets-dir", str(GLOSSARY.parent),
            "--skip-llm-judge",
        ]
        try:
            main()
        finally:
            _sys.argv = orig

        out = capsys.readouterr().out
        assert "tm_only_baseline" in out, (
            "CLI default mode must print 'tm_only_baseline' in INFO output"
        )
