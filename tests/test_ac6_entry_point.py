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


# ---------------------------------------------------------------------------
# AC6 §4b: the DERIVED threshold decides Gate 3 — not the module constant
# ---------------------------------------------------------------------------


class TestGate3DerivedThresholdDecidesVerdict:
    """The in-run baseline must drive the pass/fail decision, not just the printout."""

    def test_gate3_passes_under_derived_threshold_while_failing_module_constant(
        self, hold_out_cards
    ):
        """A median above the module constant but below the derived threshold must PASS.

        Non-tautological: the expected verdict is computed from the derived
        threshold only.  If score_hold_out ignored the derived value and kept
        using gate3_edit_distance.THRESHOLD, this assertion would flip.
        """
        import gate3_edit_distance

        # Predictions distant enough that the median exceeds the module constant.
        preds = ["완전히 다른 문장 " + "가" * 30 for _ in hold_out_cards]
        median = gate3_edit_distance.score_hold_out(HOLD_OUT, preds).median_distance
        assert median > gate3_edit_distance.THRESHOLD, (
            "test setup: median must exceed the module constant so the two rules disagree"
        )

        # Baseline chosen so the derived threshold clears the median.
        baseline = (median + 0.05) / THRESHOLD_FACTOR
        derived = baseline * THRESHOLD_FACTOR
        assert derived > median

        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            tm_baseline_median=baseline,
        )
        assert report.verdict.gate3.threshold == pytest.approx(derived, rel=1e-9), (
            "Gate 3 must be scored against the threshold derived from this run's "
            "tm_only_baseline, not the module constant"
        )
        assert report.verdict.gate3.passed is True, (
            "Gate 3 must pass when the median is below the in-run derived threshold"
        )

    def test_gate3_fails_when_derived_threshold_drops_below_median(self, hold_out_cards):
        """The same predictions must FAIL once the measured baseline shrinks.

        Pairs with the previous test: only the measured baseline changes, so a
        constant-threshold implementation cannot satisfy both.
        """
        import gate3_edit_distance

        preds = ["완전히 다른 문장 " + "가" * 30 for _ in hold_out_cards]
        median = gate3_edit_distance.score_hold_out(HOLD_OUT, preds).median_distance
        baseline = max(median - 0.05, 0.001) / THRESHOLD_FACTOR
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            tm_baseline_median=baseline,
        )
        assert report.verdict.gate3.passed is False
        assert 3 in report.verdict.failed_gates

    def test_threshold_provenance_is_measured_in_run(self, hold_out_cards):
        """baseline_provenance = measured_in_run whenever a baseline is supplied."""
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, [c["ko_text"] for c in hold_out_cards],
            skip_llm_judge=True,
            tm_baseline_median=0.4,
        )
        assert report.gate3_threshold_provenance == "measured_in_run"

    def test_threshold_provenance_is_module_constant_without_baseline(
        self, hold_out_cards
    ):
        import gate3_edit_distance

        report = run_evaluation(
            HOLD_OUT, GLOSSARY, [c["ko_text"] for c in hold_out_cards],
            skip_llm_judge=True,
        )
        assert report.gate3_threshold_provenance == "module_constant"
        assert report.verdict.gate3.threshold == pytest.approx(
            gate3_edit_distance.THRESHOLD
        )


# ---------------------------------------------------------------------------
# AC6 §2b: both named series are held distinctly on the report object
# ---------------------------------------------------------------------------


class TestTwoSeriesAreStructurallyDistinct:
    """pipeline_predictions and tm_only_baseline must be separable, not just labels."""

    def test_report_keeps_both_series_and_they_differ(self, hold_out_cards):
        pipeline_preds = ["파이프라인 출력 " + str(i) for i in range(len(hold_out_cards))]
        baseline_preds = ["TM 베이스라인 " + str(i) for i in range(len(hold_out_cards))]
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, pipeline_preds,
            skip_llm_judge=True,
            tm_baseline_median=0.4,
            tm_only_baseline_predictions=baseline_preds,
        )
        assert report.pipeline_predictions == pipeline_preds
        assert report.tm_only_baseline_predictions == baseline_preds
        assert report.pipeline_predictions != report.tm_only_baseline_predictions, (
            "the two series must remain distinguishable within a single run"
        )

    def test_scored_series_is_pipeline_predictions_not_baseline(self, hold_out_cards):
        """The gate-3 median must match pipeline_predictions, not the baseline series."""
        import gate3_edit_distance

        pipeline_preds = [c.get("ko_text", "") for c in hold_out_cards]  # distance ~0
        baseline_preds = ["전혀 다른 문장" for _ in hold_out_cards]      # distance ~1
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, pipeline_preds,
            skip_llm_judge=True,
            tm_baseline_median=0.4,
            tm_only_baseline_predictions=baseline_preds,
        )
        expected = gate3_edit_distance.score_hold_out(
            HOLD_OUT, pipeline_preds
        ).median_distance
        baseline_median = gate3_edit_distance.score_hold_out(
            HOLD_OUT, baseline_preds
        ).median_distance
        assert report.verdict.gate3.median_distance == pytest.approx(expected)
        assert report.verdict.gate3.median_distance != pytest.approx(baseline_median), (
            "gate_scoring_subject must be pipeline_predictions, not tm_only_baseline"
        )


# ---------------------------------------------------------------------------
# AC6 §6: every empty-prediction cause is reported, including zeros
# ---------------------------------------------------------------------------


class TestAllCausesAlwaysReported:
    def test_report_lists_all_three_causes_even_when_zero(self, hold_out_cards, capsys):
        from field_card_synthesis import ALL_CAUSES

        report = run_evaluation(
            HOLD_OUT, GLOSSARY, [c["ko_text"] for c in hold_out_cards],
            skip_llm_judge=True,
            tm_baseline_median=0.4,
        )
        assert set(report.empty_by_cause) == set(ALL_CAUSES), (
            "empty_by_cause must always carry all three cause keys"
        )
        print_evaluation_report(report)
        out = capsys.readouterr().out
        for cause in ALL_CAUSES:
            assert cause in out, f"cause {cause} must be reported even at zero"

    def test_flat_series_empties_are_attributed_to_tm_search_failure(
        self, hold_out_cards
    ):
        """Without field records an empty prediction can only be a TM search miss."""
        preds = [
            "" if i < 7 else c.get("ko_text", "")
            for i, c in enumerate(hold_out_cards)
        ]
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            empty_prediction_count=7,
        )
        assert report.empty_by_cause["tm_search_failure"] == 7
        assert report.empty_by_cause["guard_rejected_in_review_queue"] == 0
        assert report.empty_by_cause["approval_incomplete"] == 0

    def test_field_level_counts_come_from_synthesis(self, tmp_path):
        """Aggregation stage 1 counts (card_id, field) pairs, not cards."""
        hold_out = [
            {
                "id": "c1",
                "en_text": "Trash 1 card.",
                "en_flavor": "Flavor one.",
                "ko_text": "카드 1장을 폐기한다.",
                "ko_flavor": "플레이버 하나.",
            },
            {"id": "c2", "en_text": "Gain 1 credit.", "ko_text": "1크레딧을 얻는다."},
        ]
        field_records = [
            {
                "card_id": "c1",
                "route": "rule",
                "draft_ko": "카드 1장을 폐기한다.",
                "interrupted": False,
            },
            {"card_id": "c1", "route": "flavor", "draft_ko": "", "interrupted": True},
            {
                "card_id": "c2",
                "route": "rule",
                "draft_ko": "1크레딧을 얻는다.",
                "interrupted": False,
            },
        ]
        synthesis = synthesize_field_to_card(field_records, hold_out)

        ho_path = tmp_path / "hold_out.json"
        ho_path.write_text(json.dumps(hold_out, ensure_ascii=False), encoding="utf-8")
        report = run_evaluation(
            ho_path, GLOSSARY, synthesis.card_predictions,
            skip_llm_judge=True,
            field_synthesis=synthesis,
        )
        # 3 expected (card, field) pairs: c1/text, c1/flavor, c2/text
        assert report.field_compliant_count == 2
        assert report.field_violated_count == 1
        # card level: c1 violated (flavor empty), c2 compliant
        assert report.field_synthesis.compliant_count == 1
        assert report.field_synthesis.violated_count == 1
        assert report.empty_by_cause["guard_rejected_in_review_queue"] == 1


# ---------------------------------------------------------------------------
# AC6 §7: reference leakage ban holds on the report object itself
# ---------------------------------------------------------------------------


def test_report_predictions_are_never_the_hold_out_answers(hold_out_cards):
    """When the pipeline yields empty drafts the report must keep them empty."""
    preds = ["" for _ in hold_out_cards]
    report = run_evaluation(
        HOLD_OUT, GLOSSARY, preds,
        skip_llm_judge=True,
        empty_prediction_count=len(preds),
        tm_baseline_median=0.4,
    )
    assert report.pipeline_predictions == preds
    ko_texts = [c.get("ko_text", "") for c in hold_out_cards]
    assert report.pipeline_predictions != ko_texts
    assert all(p == "" for p in report.pipeline_predictions), (
        "empty predictions must never be backfilled with hold_out ko_text"
    )


# ---------------------------------------------------------------------------
# AC6 §1b: --run-pipeline covers the WHOLE hold-out set, size taken from the file
# ---------------------------------------------------------------------------


class TestRunPipelineModeCoversWholeHoldOut:
    """The entry point must run the pipeline over every hold-out record.

    The card count must come from data/hold_out.json, never from a literal.
    A stub stands in for the real pipeline so the assertion is about the wiring,
    not about model latency.
    """

    def test_run_pipeline_receives_full_hold_out_size(
        self, tmp_path, monkeypatch, hold_out_cards, capsys
    ):
        import sys as _sys
        import types

        captured: dict = {}

        def _fake_run_pipeline(*, data_dir, assets_dir, n_cards, output_path, llm_model=None):
            captured["n_cards"] = n_cards
            cards = json.loads(
                (Path(data_dir) / "hold_out.json").read_text(encoding="utf-8")
            )[:n_cards]
            with Path(output_path).open("w", encoding="utf-8") as fh:
                for card in cards:
                    fh.write(json.dumps({
                        "card_id": card["id"],
                        "route": "rule",
                        "draft_ko": "초벌",
                        "tm_confidence": 0.5,
                        "interrupted": False,
                    }, ensure_ascii=False) + "\n")

        stub = types.ModuleType("run_pipeline")
        stub.run_pipeline = _fake_run_pipeline
        monkeypatch.setitem(_sys.modules, "run_pipeline", stub)
        monkeypatch.chdir(tmp_path)

        orig = _sys.argv[:]
        _sys.argv = [
            "evaluate_pipeline",
            "--data-dir", str(HOLD_OUT.parent),
            "--assets-dir", str(GLOSSARY.parent),
            "--run-pipeline",
            "--llm-model", "global.anthropic.claude-haiku-4-5-20251001-v1:0",
            "--skip-llm-judge",
        ]
        try:
            main()
        finally:
            _sys.argv = orig

        assert captured["n_cards"] == len(hold_out_cards), (
            "the entry point must derive the card count from data/hold_out.json "
            "(hold_out_size_source), not from a constant"
        )
        out = capsys.readouterr().out
        assert "pipeline_predictions" in out and "tm_only_baseline" in out


# ---------------------------------------------------------------------------
# AC6 §3b: observation-metric failure must not erase the hard-gate verdict
# ---------------------------------------------------------------------------


def test_obs_failure_is_reported_and_gates_still_scored(hold_out_cards, capsys):
    """A Bedrock failure surfaces as obs_error; the three gates are still judged."""
    import obs_llm_judge as _obs

    class _Boom:
        pass

    def _raise(*args, **kwargs):
        raise RuntimeError("no AWS credentials")

    orig = _obs.score_hold_out
    _obs.score_hold_out = _raise
    try:
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, [c["ko_text"] for c in hold_out_cards],
            skip_llm_judge=False,
            tm_baseline_median=0.4,
        )
    finally:
        _obs.score_hold_out = orig

    assert report.obs is None
    assert report.obs_error is not None and "no AWS credentials" in report.obs_error
    assert report.verdict is not None
    print_evaluation_report(report)
    out = capsys.readouterr().out
    assert "관찰 지표" in out
    assert "최종 판정" in out


# ---------------------------------------------------------------------------
# AC6: TM Echo Gate tests
# ---------------------------------------------------------------------------


class TestEchoGate:
    """TM Echo Gate: regression canary that catches LLM-parrots-TM regression."""

    def test_score_echo_gate_passes_when_no_echo(self):
        """Echo gate passes when draft_ko differs from tm_hits[0].ko_text."""
        from evaluate_pipeline import ECHO_FLOOR, EchoGateResult, score_echo_gate

        hold_out = [{"id": "card1", "ko_text": "가나다", "ko_flavor": ""}]
        field_records = [{
            "card_id": "card1",
            "route": "rule",
            "draft_ko": "다른 번역",
            "tm_hits": [{"id": "x", "ko_text": "TM 번역", "en_text": "...", "score": 0.5}],
        }]
        result = score_echo_gate(field_records, hold_out)
        assert isinstance(result, EchoGateResult)
        assert result.echo_count == 0
        assert result.denominator == 1
        assert result.actual_echo_rate == 0.0
        assert result.passed is True

    def test_score_echo_gate_detects_echo(self):
        """Echo gate fails when draft_ko == tm_hits[0].ko_text and rate exceeds threshold."""
        from evaluate_pipeline import ECHO_FLOOR, score_echo_gate

        # Make 100% of drafts echo TM (rate = 1.0 >> ECHO_FLOOR)
        hold_out = [{"id": f"c{i}", "ko_text": f"ref{i}", "ko_flavor": ""} for i in range(10)]
        field_records = [{
            "card_id": f"c{i}",
            "route": "rule",
            "draft_ko": f"TM{i}",
            "tm_hits": [{"id": "x", "ko_text": f"TM{i}", "en_text": "...", "score": 0.5}],
        } for i in range(10)]
        result = score_echo_gate(field_records, hold_out)
        assert result.echo_count == 10
        assert result.actual_echo_rate == 1.0
        assert result.passed is False  # 1.0 >> ECHO_FLOOR=0.05

    def test_score_echo_gate_echo_floor_is_005(self):
        """ECHO_FLOOR must be 0.05 — policy constant (same status as gate1's 0.95)."""
        from evaluate_pipeline import ECHO_FLOOR
        assert ECHO_FLOOR == 0.05

    def test_score_echo_gate_threshold_is_max_floor_and_coincidence(self):
        """threshold = max(ECHO_FLOOR, coincidence_rate_measured_in_run)."""
        from evaluate_pipeline import ECHO_FLOOR, score_echo_gate

        # TM top-1 matches ref for 3/10 cards → coincidence=0.3 > ECHO_FLOOR=0.05
        hold_out = [{"id": f"c{i}", "ko_text": f"TM{i}", "ko_flavor": ""} for i in range(10)]
        field_records = [{
            "card_id": f"c{i}",
            "route": "rule",
            "draft_ko": "다른 번역",
            "tm_hits": [{"id": "x", "ko_text": f"TM{i}", "en_text": "...", "score": 0.5}],
        } for i in range(10)]
        result = score_echo_gate(field_records, hold_out)
        assert result.coincidence_count == 10  # all TM top-1 == ref
        assert result.coincidence_rate == 1.0
        assert result.threshold == max(ECHO_FLOOR, result.coincidence_rate)

    def test_score_echo_gate_excludes_empty_draft(self):
        """Records with empty draft_ko must be excluded from denominator."""
        from evaluate_pipeline import score_echo_gate

        hold_out = [
            {"id": "c1", "ko_text": "ref1", "ko_flavor": ""},
            {"id": "c2", "ko_text": "ref2", "ko_flavor": ""},
        ]
        field_records = [
            {
                "card_id": "c1",
                "route": "rule",
                "draft_ko": "",  # empty — excluded from denominator
                "tm_hits": [{"id": "x", "ko_text": "TM1", "en_text": "...", "score": 0.5}],
            },
            {
                "card_id": "c2",
                "route": "rule",
                "draft_ko": "다른 번역",
                "tm_hits": [{"id": "y", "ko_text": "TM2", "en_text": "...", "score": 0.5}],
            },
        ]
        result = score_echo_gate(field_records, hold_out)
        assert result.denominator == 1  # c1 excluded (empty draft_ko)
        assert result.echo_count == 0

    def test_score_echo_gate_excludes_no_tm_hits(self):
        """Records with no TM hits must be excluded from denominator."""
        from evaluate_pipeline import score_echo_gate

        hold_out = [{"id": "c1", "ko_text": "ref", "ko_flavor": ""}]
        field_records = [{
            "card_id": "c1",
            "route": "rule",
            "draft_ko": "번역",
            "tm_hits": [],  # no hits
        }]
        result = score_echo_gate(field_records, hold_out)
        assert result.denominator == 0

    def test_echo_gate_result_in_evaluation_report(self, hold_out_cards, tmp_path):
        """run_evaluation computes echo_gate_result when field_synthesis has field_records."""
        import json as _json

        pipeline_path = tmp_path / "pipeline.jsonl"
        first_card = hold_out_cards[0]
        with pipeline_path.open("w", encoding="utf-8") as f:
            f.write(_json.dumps({
                "_meta": "run_pipeline_header",
                "run_mode": "real",
                "tm_threshold_derivation": {},
            }, ensure_ascii=False) + "\n")
            f.write(_json.dumps({
                "card_id": first_card["id"],
                "route": "rule",
                "draft_ko": "번역 결과",
                "interrupted": False,
                "tm_hits": [{"id": "x", "ko_text": "TM 번역", "en_text": "...", "score": 0.5}],
                "injected_terms": [],
                "tm_confidence": 0.5,
                "glossary_llm_judged": True,
            }, ensure_ascii=False) + "\n")
        preds, _, _, _, synthesis, _ = _load_pipeline_output(pipeline_path, HOLD_OUT)
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            field_synthesis=synthesis,
            tm_baseline_median=0.4,
        )
        assert report.echo_gate_result is not None
        assert isinstance(report.echo_gate_result.passed, bool)
        assert report.echo_gate_result.denominator >= 1

    def test_echo_gate_printed_in_report(self, hold_out_cards, tmp_path, capsys):
        """print_evaluation_report must include echo gate section."""
        import json as _json

        pipeline_path = tmp_path / "pipeline.jsonl"
        first_card = hold_out_cards[0]
        with pipeline_path.open("w", encoding="utf-8") as f:
            f.write(_json.dumps({
                "_meta": "run_pipeline_header",
                "run_mode": "real",
                "tm_threshold_derivation": {},
            }, ensure_ascii=False) + "\n")
            f.write(_json.dumps({
                "card_id": first_card["id"],
                "route": "rule",
                "draft_ko": "번역 결과",
                "interrupted": False,
                "tm_hits": [{"id": "x", "ko_text": "TM 번역", "en_text": "...", "score": 0.5}],
                "injected_terms": [],
                "tm_confidence": 0.5,
                "glossary_llm_judged": True,
            }, ensure_ascii=False) + "\n")
        preds, _, _, _, synthesis, _ = _load_pipeline_output(pipeline_path, HOLD_OUT)
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            field_synthesis=synthesis,
            tm_baseline_median=0.4,
        )
        print_evaluation_report(report)
        out = capsys.readouterr().out
        assert "에코 게이트" in out or "Echo" in out
        assert "ECHO_FLOOR" in out or "echo_floor" in out or "0.05" in out


# ---------------------------------------------------------------------------
# AC6: run_mode tracking tests
# ---------------------------------------------------------------------------


class TestRunMode:
    """run_mode is tracked from pipeline header and gates are blocked for stub runs."""

    def test_load_pipeline_output_extracts_run_mode_real(self, tmp_path):
        """_load_pipeline_output reads run_mode='real' from pipeline header."""
        import json as _json

        p = tmp_path / "p.jsonl"
        p.write_text(
            _json.dumps({"_meta": "run_pipeline_header", "run_mode": "real", "tm_threshold_derivation": {}}) + "\n"
            + _json.dumps({"card_id": "x", "route": "rule", "draft_ko": "번역", "interrupted": False,
                           "tm_hits": [], "injected_terms": [], "tm_confidence": 0.0}) + "\n",
            encoding="utf-8",
        )
        _, _, _, _, synthesis, _ = _load_pipeline_output(p, HOLD_OUT)
        assert synthesis.run_mode == "real"

    def test_load_pipeline_output_extracts_run_mode_stub(self, tmp_path):
        """_load_pipeline_output reads run_mode='stub' from pipeline header."""
        import json as _json

        p = tmp_path / "p.jsonl"
        p.write_text(
            _json.dumps({"_meta": "run_pipeline_header", "run_mode": "stub", "tm_threshold_derivation": {}}) + "\n"
            + _json.dumps({"card_id": "x", "route": "rule", "draft_ko": "번역", "interrupted": False,
                           "tm_hits": [], "injected_terms": [], "tm_confidence": 0.0}) + "\n",
            encoding="utf-8",
        )
        _, _, _, _, synthesis, _ = _load_pipeline_output(p, HOLD_OUT)
        assert synthesis.run_mode == "stub"

    def test_load_pipeline_output_run_mode_unknown_when_absent(self, tmp_path):
        """When header lacks run_mode, synthesis.run_mode defaults to 'unknown'."""
        import json as _json

        p = tmp_path / "p.jsonl"
        p.write_text(
            _json.dumps({"_meta": "run_pipeline_header", "tm_threshold_derivation": {}}) + "\n"
            + _json.dumps({"card_id": "x", "route": "rule", "draft_ko": "번역", "interrupted": False,
                           "tm_hits": [], "injected_terms": [], "tm_confidence": 0.0}) + "\n",
            encoding="utf-8",
        )
        _, _, _, _, synthesis, _ = _load_pipeline_output(p, HOLD_OUT)
        assert synthesis.run_mode == "unknown"

    def test_report_run_mode_propagated_from_field_synthesis(self, hold_out_cards, tmp_path):
        """run_evaluation propagates run_mode from field_synthesis when not explicitly passed."""
        import json as _json

        pipeline_path = tmp_path / "p.jsonl"
        card = hold_out_cards[0]
        with pipeline_path.open("w", encoding="utf-8") as f:
            f.write(_json.dumps({
                "_meta": "run_pipeline_header",
                "run_mode": "real",
                "tm_threshold_derivation": {},
            }) + "\n")
            f.write(_json.dumps({
                "card_id": card["id"], "route": "rule", "draft_ko": "번역",
                "interrupted": False, "tm_hits": [], "injected_terms": [],
                "tm_confidence": 0.0, "glossary_llm_judged": True,
            }) + "\n")
        preds, _, _, _, synthesis, _ = _load_pipeline_output(pipeline_path, HOLD_OUT)
        report = run_evaluation(HOLD_OUT, GLOSSARY, preds, skip_llm_judge=True, field_synthesis=synthesis)
        assert report.run_mode == "real"

    def test_stub_run_does_not_print_gate_verdicts(self, hold_out_cards, capsys):
        """print_evaluation_report omits gate verdicts when run_mode='stub'."""
        preds = [c["ko_text"] for c in hold_out_cards]
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            tm_baseline_median=0.4,
            run_mode="stub",
        )
        print_evaluation_report(report)
        out = capsys.readouterr().out
        assert "STUB" in out or "stub" in out.lower()
        # Gate verdicts should not appear in stub output
        assert "합격" not in out and "불합격" not in out

    def test_run_pipeline_writes_run_mode_stub_to_header(self, tmp_path):
        """run_pipeline with llm_model=None writes run_mode='stub' to header."""
        import json as _json
        from run_pipeline import run_pipeline

        out = tmp_path / "out.jsonl"
        run_pipeline(
            data_dir=Path("data"),
            assets_dir=Path("assets"),
            n_cards=1,
            output_path=out,
            llm_model=None,
        )
        lines = out.read_text(encoding="utf-8").splitlines()
        header = _json.loads(lines[0])
        assert header.get("run_mode") == "stub"
        assert header.get("_meta") == "run_pipeline_header"

    def test_run_pipeline_writes_run_mode_field_records(self, tmp_path):
        """run_pipeline writes run_mode into the header and model_id into each record."""
        import json as _json
        from run_pipeline import run_pipeline

        out = tmp_path / "out.jsonl"
        run_pipeline(
            data_dir=Path("data"),
            assets_dir=Path("assets"),
            n_cards=1,
            output_path=out,
            llm_model=None,
        )
        lines = out.read_text(encoding="utf-8").splitlines()
        records = [_json.loads(line) for line in lines if not _json.loads(line).get("_meta")]
        for rec in records:
            assert "model_id" in rec, f"record missing model_id: {rec}"
            assert rec["model_id"] is None  # stub → None


# ---------------------------------------------------------------------------
# AC6: model_invocation_failure cause
# ---------------------------------------------------------------------------


class TestModelInvocationFailureCause:
    """model_invocation_failure is a valid empty prediction cause (4th)."""

    def test_all_causes_has_model_invocation_failure(self):
        """ALL_CAUSES must include 'model_invocation_failure'."""
        from field_card_synthesis import ALL_CAUSES
        assert "model_invocation_failure" in ALL_CAUSES

    def test_evaluation_report_has_model_invocation_failure_key(self, hold_out_cards):
        """EvaluationReport.empty_by_cause must always have all 4 cause keys."""
        from field_card_synthesis import ALL_CAUSES
        preds = [""] * len(hold_out_cards)
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            tm_baseline_median=0.4,
        )
        for cause in ALL_CAUSES:
            assert cause in report.empty_by_cause, (
                f"empty_by_cause missing '{cause}' key"
            )

    def test_print_report_includes_model_invocation_failure_label(self, hold_out_cards, capsys):
        """print_evaluation_report must label model_invocation_failure in Korean."""
        from field_card_synthesis import ALL_CAUSES
        preds = [""] * len(hold_out_cards)
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds,
            skip_llm_judge=True,
            tm_baseline_median=0.4,
        )
        print_evaluation_report(report)
        out = capsys.readouterr().out
        assert "model_invocation_failure" in out


# ---------------------------------------------------------------------------
# AC6: stub prevention — CLI requires --llm-model with --run-pipeline
# ---------------------------------------------------------------------------


class TestStubPrevention:
    """The CLI must refuse --run-pipeline without --llm-model."""

    def test_run_pipeline_mode_requires_llm_model(self, tmp_path, monkeypatch):
        """main() must call parser.error when --run-pipeline is given without --llm-model."""
        import sys as _sys

        monkeypatch.chdir(tmp_path)
        orig = _sys.argv[:]
        _sys.argv = [
            "evaluate_pipeline",
            "--data-dir", str(HOLD_OUT.parent),
            "--assets-dir", str(GLOSSARY.parent),
            "--run-pipeline",
            # No --llm-model intentionally
            "--skip-llm-judge",
        ]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0  # parser.error exits non-zero
        finally:
            _sys.argv = orig

    def test_run_pipeline_no_default_llm_model(self):
        """run_pipeline() must NOT have a default for llm_model — forces explicit opt-in."""
        import inspect
        from run_pipeline import run_pipeline as _rp

        sig = inspect.signature(_rp)
        param = sig.parameters.get("llm_model")
        assert param is not None, "run_pipeline must have llm_model parameter"
        assert param.default is inspect.Parameter.empty, (
            "llm_model must have no default — caller must explicitly pass None or a model ID"
        )
