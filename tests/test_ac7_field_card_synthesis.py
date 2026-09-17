"""tests/test_ac7_field_card_synthesis.py — AC7: field-to-card judgment synthesis.

Acceptance criterion:
  필드 단위 결과를 카드 단위 판정으로 합성하는 규칙을 코드와 보고에 명시한다 —
  한 카드의 모든 필드 예측이 준수일 때만 그 카드를 준수로 세고, 한 필드라도
  위반이면 카드 위반으로 센다. 빈 예측(TM 실패·검토 큐 잔류·승인 미완)을 가진
  필드는 준수로 세지 않으며, 게이트별 분모에서 조용히 제외하지도 않고 원인별로
  따로 집계해 보고한다. 필드가 하나뿐인 카드와 둘인 카드가 동일한 규칙으로
  처리됨을 서로 다른 기대값으로 단언하는 테스트를 포함한다.

Tests:
  - 1-field card: compliant when text field non-empty (rule: all fields compliant)
  - 1-field card: violated when text field empty (rule: any field empty → violated)
  - 2-field card: compliant ONLY when BOTH fields non-empty
  - 2-field card: violated when text field empty, even if flavor is non-empty
  - 2-field card: violated when flavor field empty, even if text is non-empty
  - Both 1-field and 2-field cards use the same synthesis rule (same code path)
  - Different expected values: 1-field vs 2-field card counts
  - Empty prediction cause classification: tm_search_failure, guard_rejected, approval_incomplete
  - Empty counts are NOT silently excluded from denominators
  - Gate-scoring predictions: "" when any field violated, text draft_ko otherwise
  - synthesize_field_to_card is importable and callable (wiring_vs_reimplementation)
  - FieldSynthesisReport integrated into EvaluationReport (field_synthesis field)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from field_card_synthesis import (
    CardSynthesisResult,
    FieldSynthesisReport,
    synthesize_field_to_card,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic hold-out cards and field records
# ---------------------------------------------------------------------------


def _hold_out_card(
    card_id: str,
    en_text: str = "Install a program.",
    en_flavor: str = "",
) -> dict:
    """Create a minimal hold-out card dict."""
    return {
        "id": card_id,
        "en_text": en_text,
        "ko_text": "프로그램을 설치한다.",
        "en_flavor": en_flavor,
        "ko_flavor": "풍미 텍스트." if en_flavor else "",
    }


def _field_rec(
    card_id: str,
    route: str,
    draft_ko: str,
    interrupted: bool = False,
) -> dict:
    """Create a minimal pipeline field record."""
    return {
        "card_id": card_id,
        "route": route,
        "draft_ko": draft_ko,
        "interrupted": interrupted,
        "tm_hits": [],
        "injected_terms": [],
        "tm_confidence": 0.1 if draft_ko else 0.0,
    }


# ---------------------------------------------------------------------------
# 1-field card tests (text-only)
# ---------------------------------------------------------------------------


def test_one_field_card_compliant_when_text_nonempty():
    """A 1-field card with non-empty text draft is compliant."""
    hold_out = [_hold_out_card("c1")]
    records = [_field_rec("c1", "rule", "프로그램을 설치한다.")]

    report = synthesize_field_to_card(records, hold_out)

    assert report.total_count == 1
    assert report.compliant_count == 1
    assert report.violated_count == 0
    assert report.card_results[0].compliant is True
    assert report.card_results[0].field_count == 1


def test_one_field_card_violated_when_text_empty():
    """A 1-field card with empty text draft is violated."""
    hold_out = [_hold_out_card("c1")]
    records = [_field_rec("c1", "rule", "")]

    report = synthesize_field_to_card(records, hold_out)

    assert report.total_count == 1
    assert report.compliant_count == 0
    assert report.violated_count == 1
    assert report.card_results[0].compliant is False
    assert report.card_results[0].field_count == 1
    assert "text" in report.card_results[0].violated_fields


def test_one_field_card_violated_when_record_missing():
    """A 1-field card with no pipeline record at all is violated (approval_incomplete)."""
    hold_out = [_hold_out_card("c1")]
    records: list[dict] = []  # no pipeline output

    report = synthesize_field_to_card(records, hold_out)

    assert report.total_count == 1
    assert report.violated_count == 1
    assert report.card_results[0].compliant is False
    # Cause is approval_incomplete when the record is entirely missing
    causes = [c for _, c in report.card_results[0].empty_causes]
    assert "approval_incomplete" in causes


# ---------------------------------------------------------------------------
# 2-field card tests (text + flavor)
# ---------------------------------------------------------------------------


def test_two_field_card_compliant_only_when_both_nonempty():
    """A 2-field card is compliant only when BOTH text AND flavor drafts are non-empty.

    Expected values differ from 1-field: field_count==2, and the rule requires
    both fields to be compliant (more stringent than 1-field).
    """
    hold_out = [_hold_out_card("c2", en_flavor="Flavor text.")]
    records = [
        _field_rec("c2", "rule", "텍스트 번역."),
        _field_rec("c2", "flavor", "플레이버 번역."),
    ]

    report = synthesize_field_to_card(records, hold_out)

    assert report.total_count == 1
    assert report.compliant_count == 1
    assert report.card_results[0].field_count == 2  # differs from 1-field expectation
    assert report.card_results[0].compliant is True
    assert report.card_results[0].violated_fields == []


def test_two_field_card_violated_when_text_empty_flavor_ok():
    """A 2-field card is violated if the text field is empty — even if flavor is non-empty.

    This asserts the 'any field violated → card violated' rule with a specific
    expected value that differs from the 2-field-both-ok case above.
    """
    hold_out = [_hold_out_card("c2", en_flavor="Flavor text.")]
    records = [
        _field_rec("c2", "rule", ""),           # text field empty
        _field_rec("c2", "flavor", "플레이버."),  # flavor field non-empty
    ]

    report = synthesize_field_to_card(records, hold_out)

    # Card is violated despite the flavor field being fine
    assert report.compliant_count == 0
    assert report.violated_count == 1
    assert report.card_results[0].compliant is False
    assert "text" in report.card_results[0].violated_fields
    assert "flavor" not in report.card_results[0].violated_fields  # flavor was ok


def test_two_field_card_violated_when_flavor_empty_text_ok():
    """A 2-field card is violated if the flavor field is empty — even if text is non-empty.

    Symmetric counterpart to the previous test: flavor violation kills the card.
    """
    hold_out = [_hold_out_card("c2", en_flavor="Flavor text.")]
    records = [
        _field_rec("c2", "rule", "텍스트 번역."),   # text field non-empty
        _field_rec("c2", "flavor", ""),              # flavor field empty
    ]

    report = synthesize_field_to_card(records, hold_out)

    assert report.compliant_count == 0
    assert report.violated_count == 1
    assert report.card_results[0].compliant is False
    assert "flavor" in report.card_results[0].violated_fields
    assert "text" not in report.card_results[0].violated_fields  # text was ok


def test_two_field_card_violated_when_both_empty():
    """A 2-field card with both fields empty is violated with 2 violated fields."""
    hold_out = [_hold_out_card("c2", en_flavor="Flavor text.")]
    records = [
        _field_rec("c2", "rule", ""),
        _field_rec("c2", "flavor", ""),
    ]

    report = synthesize_field_to_card(records, hold_out)

    assert report.violated_count == 1
    result = report.card_results[0]
    assert len(result.violated_fields) == 2
    assert set(result.violated_fields) == {"text", "flavor"}


# ---------------------------------------------------------------------------
# Different expected values: 1-field vs 2-field with identical violation rule
# ---------------------------------------------------------------------------


def test_one_vs_two_field_card_different_expected_values():
    """1-field and 2-field cards obey the same synthesis rule but produce different counts.

    This test asserts the non-trivial different expected values requirement:
    - 1-field card with non-empty text: compliant_count=1, violated_count=0
    - 2-field card where flavor is empty: compliant_count=0, violated_count=1
    The same synthesis rule (all-fields-must-be-compliant) is applied in both cases,
    but the outcome differs because the 2-field card has an additional requirement.
    """
    # Scenario A: 1-field card, text non-empty → compliant
    hold_out_A = [_hold_out_card("ca")]
    records_A = [_field_rec("ca", "rule", "번역A")]
    report_A = synthesize_field_to_card(records_A, hold_out_A)

    # Scenario B: 2-field card, text non-empty but flavor empty → violated
    hold_out_B = [_hold_out_card("cb", en_flavor="Flavor.")]
    records_B = [
        _field_rec("cb", "rule", "번역B"),
        _field_rec("cb", "flavor", ""),  # empty flavor → card violated
    ]
    report_B = synthesize_field_to_card(records_B, hold_out_B)

    # Scenario A: compliant (all 1 field OK)
    assert report_A.compliant_count == 1, "1-field card with non-empty text must be compliant"
    assert report_A.violated_count == 0

    # Scenario B: violated (2-field, 1 field empty) — DIFFERENT expected value
    assert report_B.compliant_count == 0, "2-field card with empty flavor must be violated"
    assert report_B.violated_count == 1

    # Field counts differ
    assert report_A.card_results[0].field_count == 1
    assert report_B.card_results[0].field_count == 2


# ---------------------------------------------------------------------------
# Empty prediction cause classification
# ---------------------------------------------------------------------------


def test_cause_tm_search_failure_for_empty_noninterrupted():
    """Empty draft_ko on a non-interrupted record → tm_search_failure cause."""
    hold_out = [_hold_out_card("c1")]
    records = [_field_rec("c1", "rule", "", interrupted=False)]

    report = synthesize_field_to_card(records, hold_out)

    causes = [c for _, c in report.card_results[0].empty_causes]
    assert "tm_search_failure" in causes
    assert report.empty_by_cause["tm_search_failure"] == 1
    assert report.empty_by_cause["guard_rejected_in_review_queue"] == 0
    assert report.empty_by_cause["approval_incomplete"] == 0


def test_cause_guard_rejected_for_empty_interrupted():
    """Empty draft_ko on an interrupted record → guard_rejected_in_review_queue cause."""
    hold_out = [_hold_out_card("c1")]
    records = [_field_rec("c1", "rule", "", interrupted=True)]

    report = synthesize_field_to_card(records, hold_out)

    causes = [c for _, c in report.card_results[0].empty_causes]
    assert "guard_rejected_in_review_queue" in causes
    assert report.empty_by_cause["guard_rejected_in_review_queue"] == 1
    assert report.empty_by_cause["tm_search_failure"] == 0


def test_cause_approval_incomplete_for_missing_record():
    """No pipeline record for an expected field → approval_incomplete cause."""
    hold_out = [_hold_out_card("c1")]
    records: list[dict] = []  # no records at all

    report = synthesize_field_to_card(records, hold_out)

    causes = [c for _, c in report.card_results[0].empty_causes]
    assert "approval_incomplete" in causes
    assert report.empty_by_cause["approval_incomplete"] == 1


def test_empty_by_cause_counts_all_causes_across_cards():
    """empty_by_cause aggregates across all cards and all causes."""
    hold_out = [
        _hold_out_card("c1"),                              # 1-field
        _hold_out_card("c2"),                              # 1-field, will be interrupted
        _hold_out_card("c3"),                              # 1-field, will be missing
        _hold_out_card("c4", en_flavor="Flavor."),         # 2-field
    ]
    records = [
        _field_rec("c1", "rule", "", interrupted=False),   # tm_search_failure
        _field_rec("c2", "rule", "", interrupted=True),    # guard_rejected
        # c3 has no record → approval_incomplete
        _field_rec("c4", "rule", "텍스트"),                # c4 text OK
        _field_rec("c4", "flavor", "", interrupted=False), # c4 flavor tm_fail
    ]

    report = synthesize_field_to_card(records, hold_out)

    assert report.empty_by_cause["tm_search_failure"] == 2      # c1, c4-flavor
    assert report.empty_by_cause["guard_rejected_in_review_queue"] == 1  # c2
    assert report.empty_by_cause["approval_incomplete"] == 1    # c3
    assert report.compliant_count == 0  # all 4 cards have at least 1 violation
    assert report.violated_count == 4


# ---------------------------------------------------------------------------
# Gate-scoring predictions
# ---------------------------------------------------------------------------


def test_gate_prediction_is_text_draft_when_compliant():
    """When a card is fully compliant, the gate prediction is the text field draft_ko."""
    hold_out = [_hold_out_card("c1")]
    records = [_field_rec("c1", "rule", "프로그램을 설치한다.")]

    report = synthesize_field_to_card(records, hold_out)

    assert report.card_predictions == ["프로그램을 설치한다."]
    assert report.card_results[0].card_prediction == "프로그램을 설치한다."


def test_gate_prediction_is_empty_when_any_field_violated():
    """When any field is violated, the card-level gate prediction is empty string."""
    hold_out = [_hold_out_card("c1", en_flavor="Flavor.")]
    records = [
        _field_rec("c1", "rule", "텍스트."),
        _field_rec("c1", "flavor", ""),  # flavor violated
    ]

    report = synthesize_field_to_card(records, hold_out)

    # Card is violated → gate prediction is "" so the gate counts it as a miss
    assert report.card_predictions == [""]
    assert report.card_results[0].card_prediction == ""


def test_gate_predictions_aligned_with_hold_out_order():
    """card_predictions must be aligned with hold_out_cards order."""
    hold_out = [
        _hold_out_card("c1"),
        _hold_out_card("c2"),
        _hold_out_card("c3"),
    ]
    records = [
        _field_rec("c2", "rule", "c2 번역"),
        _field_rec("c3", "rule", "c3 번역"),
        # c1 has no record → approval_incomplete → ""
    ]

    report = synthesize_field_to_card(records, hold_out)

    # Predictions must follow hold_out order: [c1, c2, c3]
    assert report.card_predictions[0] == ""       # c1: missing → ""
    assert report.card_predictions[1] == "c2 번역"  # c2: compliant
    assert report.card_predictions[2] == "c3 번역"  # c3: compliant


# ---------------------------------------------------------------------------
# Empty predictions are NOT excluded from denominator
# ---------------------------------------------------------------------------


def test_empty_predictions_count_as_violations_not_excluded():
    """Empty predictions must NOT be excluded from the total; they count as violated.

    Denominator is always total_count.  violated_count + compliant_count == total_count.
    """
    hold_out = [
        _hold_out_card("c1"),  # will be empty
        _hold_out_card("c2"),  # will be compliant
    ]
    records = [
        _field_rec("c1", "rule", ""),        # empty
        _field_rec("c2", "rule", "번역"),    # non-empty
    ]

    report = synthesize_field_to_card(records, hold_out)

    assert report.total_count == 2
    assert report.compliant_count == 1
    assert report.violated_count == 1
    # Invariant: compliant + violated == total (no silent exclusion)
    assert report.compliant_count + report.violated_count == report.total_count


# ---------------------------------------------------------------------------
# FieldSynthesisReport integration with EvaluationReport
# ---------------------------------------------------------------------------


def test_evaluation_report_has_field_synthesis_field():
    """EvaluationReport must have a field_synthesis field (None by default)."""
    from evaluate_pipeline import EvaluationReport, run_evaluation

    hold_out_path = Path(__file__).parent.parent / "data" / "hold_out.json"
    glossary_path = Path(__file__).parent.parent / "assets" / "glossary.json"
    cards = json.loads(hold_out_path.read_text(encoding="utf-8"))
    preds = [c["ko_text"] for c in cards]

    report = run_evaluation(hold_out_path, glossary_path, preds, skip_llm_judge=True)
    assert hasattr(report, "field_synthesis"), "EvaluationReport must have field_synthesis attribute"
    assert report.field_synthesis is None  # None when not using pipeline_output path


def test_evaluation_report_field_synthesis_populated_when_provided():
    """When a FieldSynthesisReport is passed, report.field_synthesis is set."""
    from evaluate_pipeline import EvaluationReport, run_evaluation

    hold_out_path = Path(__file__).parent.parent / "data" / "hold_out.json"
    glossary_path = Path(__file__).parent.parent / "assets" / "glossary.json"
    cards = json.loads(hold_out_path.read_text(encoding="utf-8"))
    preds = [c["ko_text"] for c in cards]

    # Build a minimal synthesis report
    synthesis = synthesize_field_to_card(
        [{"card_id": c["id"], "route": "rule", "draft_ko": c["ko_text"], "interrupted": False}
         for c in cards],
        cards,
    )

    report = run_evaluation(
        hold_out_path, glossary_path, preds,
        skip_llm_judge=True,
        field_synthesis=synthesis,
    )
    assert report.field_synthesis is synthesis
    assert isinstance(report.field_synthesis, FieldSynthesisReport)


# ---------------------------------------------------------------------------
# print_evaluation_report includes synthesis breakdown
# ---------------------------------------------------------------------------


def test_print_evaluation_report_includes_synthesis_when_present(capsys):
    """When field_synthesis is set, print_evaluation_report must show the AC7 breakdown."""
    from evaluate_pipeline import print_evaluation_report, run_evaluation

    hold_out_path = Path(__file__).parent.parent / "data" / "hold_out.json"
    glossary_path = Path(__file__).parent.parent / "assets" / "glossary.json"
    cards = json.loads(hold_out_path.read_text(encoding="utf-8"))
    preds = [c["ko_text"] for c in cards]

    synthesis = synthesize_field_to_card(
        [{"card_id": c["id"], "route": "rule", "draft_ko": c["ko_text"], "interrupted": False}
         for c in cards],
        cards,
    )

    report = run_evaluation(
        hold_out_path, glossary_path, preds,
        skip_llm_judge=True,
        field_synthesis=synthesis,
    )
    print_evaluation_report(report)
    captured = capsys.readouterr()

    # AC7 section must appear in the output
    assert "필드→카드 합성" in captured.out or "AC7" in captured.out, (
        "print_evaluation_report must include field-to-card synthesis breakdown (AC7)"
    )


# ---------------------------------------------------------------------------
# _load_pipeline_output uses synthesis (AC7 wiring verification)
# ---------------------------------------------------------------------------


def test_load_pipeline_output_returns_synthesis_report(tmp_path):
    """_load_pipeline_output must return a FieldSynthesisReport as its 5th element.

    This is the AC7 wiring verification: _load_pipeline_output must call
    synthesize_field_to_card internally and return the synthesis report.
    Removing that call would cause this test to fail.
    """
    from evaluate_pipeline import _load_pipeline_output

    # Create a minimal hold-out with 1 card that has both text and flavor fields
    hold_out = [
        {
            "id": "test_card",
            "en_text": "Test rule text.",
            "ko_text": "테스트 룰 텍스트.",
            "en_flavor": "Test flavor.",
            "ko_flavor": "테스트 플레이버.",
        }
    ]
    hold_out_path = tmp_path / "hold_out.json"
    hold_out_path.write_text(json.dumps(hold_out, ensure_ascii=False), encoding="utf-8")

    # Write a pipeline_output.jsonl with both text and flavor records
    pipeline_output = tmp_path / "pipeline_output.jsonl"
    with pipeline_output.open("w", encoding="utf-8") as f:
        # header
        f.write(json.dumps({"_meta": "test_header"}) + "\n")
        # text field record
        f.write(json.dumps({
            "card_id": "test_card",
            "route": "rule",
            "draft_ko": "번역된 룰 텍스트.",
            "interrupted": False,
            "tm_hits": [],
            "injected_terms": [],
            "tm_confidence": 0.3,
        }) + "\n")
        # flavor field record
        f.write(json.dumps({
            "card_id": "test_card",
            "route": "flavor",
            "draft_ko": "번역된 플레이버.",
            "interrupted": False,
            "tm_hits": [],
            "injected_terms": [],
            "tm_confidence": 0.2,
        }) + "\n")

    result = _load_pipeline_output(pipeline_output, hold_out_path)

    # Must return a 6-tuple: the 6th element is the optional baseline record
    # added when AC6 split the two prediction series (commit bb83664).
    assert len(result) == 6, f"Expected 6-tuple, got {len(result)}-tuple"
    predictions, pending_count, empty_count, pending_ratio, synthesis, _ = result

    # The synthesis report must be a FieldSynthesisReport
    assert isinstance(synthesis, FieldSynthesisReport)
    # 1 card with 2 fields, both non-empty → compliant
    assert synthesis.total_count == 1
    assert synthesis.compliant_count == 1
    assert synthesis.violated_count == 0


def test_load_pipeline_output_card_violated_when_flavor_field_empty(tmp_path):
    """When a 2-field card has an empty flavor prediction, the card-level prediction is "".

    This verifies that the synthesis (not just field-level records) is used:
    the text field was non-empty, but the card is still violated because the
    flavor field was empty.  The returned prediction must be "" (not the text draft).
    """
    from evaluate_pipeline import _load_pipeline_output

    hold_out = [
        {
            "id": "tc",
            "en_text": "Rule text.",
            "ko_text": "룰 텍스트.",
            "en_flavor": "Flavor text.",
            "ko_flavor": "플레이버 텍스트.",
        }
    ]
    hold_out_path = tmp_path / "hold_out.json"
    hold_out_path.write_text(json.dumps(hold_out, ensure_ascii=False), encoding="utf-8")

    pipeline_output = tmp_path / "pipeline_output.jsonl"
    with pipeline_output.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "card_id": "tc",
            "route": "rule",
            "draft_ko": "룰 번역.",  # text is non-empty
            "interrupted": False,
            "tm_hits": [],
            "injected_terms": [],
            "tm_confidence": 0.3,
        }) + "\n")
        f.write(json.dumps({
            "card_id": "tc",
            "route": "flavor",
            "draft_ko": "",           # flavor is empty → card is violated
            "interrupted": False,
            "tm_hits": [],
            "injected_terms": [],
            "tm_confidence": 0.0,
        }) + "\n")

    predictions, _, empty_count, _, synthesis, _ = _load_pipeline_output(
        pipeline_output, hold_out_path
    )

    # Card-level prediction must be "" because the flavor field was empty
    assert predictions == [""], (
        "When any field is empty, card-level prediction must be '' "
        "(AC7 synthesis rule: any field violated → card violated)"
    )
    assert synthesis.violated_count == 1
    assert synthesis.compliant_count == 0
    assert empty_count == 1  # 1 field-level empty prediction
