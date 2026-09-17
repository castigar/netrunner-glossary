"""evaluate_pipeline.py — Entry point for AC6: evaluate pipeline outputs.

Passes pipeline-generated draft records through all three hard gates and
observation metrics for the 100-card hold-out set, then reports verdict.

Scoring population: data/hold_out.json (all 100 cards, deterministic order).
Scored artifact stage: pre-approval drafts.  Interrupt-pending count/ratio are
reported separately and the entry point exits without waiting for human approval.

Hard gates (SERVICE.md §5):
  - Gate 1 (term compliance ≥ 95%)   gate1_term_compliance.score_hold_out
  - Gate 2 (symbol preservation 100%) gate2_symbol_preservation.score_hold_out
  - Gate 3 (edit distance median)     gate3_edit_distance.score_hold_out
  - Combined verdict                  gate_verdict.combine

Gate threshold derivation:
  - Gate 1: SERVICE.md §5 명시값 0.95
  - Gate 2: SERVICE.md §5 명시값 1.0 (구조적 불합격 주의: 참조 코퍼스 실측 0.95)
  - Gate 3: 같은 실행에서 측정한 TM-only 베이스라인 × 0.7 (실측값 보고서에 출력)

Reference leakage ban: when TM search returns no result, the prediction is set to
"" (empty string) — never substituted with hold-out ko_text.

Observation metrics (not hard gates):
  - obs_llm_judge.score_from_judgments or score_hold_out

Usage (CLI):
    python src/evaluate_pipeline.py
    python src/evaluate_pipeline.py --data-dir data --assets-dir assets
    python src/evaluate_pipeline.py --pipeline-output pipeline_output.jsonl
    python src/evaluate_pipeline.py --predictions-file preds.json --skip-llm-judge

Usage (API):
    from evaluate_pipeline import run_evaluation, EvaluationReport
    report = run_evaluation(hold_out_path, glossary_path, predictions, skip_llm_judge=True)
    print_evaluation_report(report)
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import gate1_term_compliance
import gate2_symbol_preservation
import gate3_edit_distance
import gate_verdict as gate_verdict_mod
import obs_llm_judge
from field_card_synthesis import ALL_CAUSES, FieldSynthesisReport, synthesize_field_to_card
from gate_verdict import GateVerdict, format_verdict
from obs_llm_judge import (
    FlavorNaturalnessJudgment,
    ObsReport,
    RulePatternJudgment,
    format_report,
    score_from_judgments,
)
from tm_index import HybridTMIndex


@dataclass
class EvaluationReport:
    """Combined evaluation report from all gates and optional observation metrics.

    Attributes:
        verdict:                Combined hard gate verdict from gate_verdict.combine.
        obs:                    Observation metrics from obs_llm_judge. None when skipped.
        pipeline_predictions:   The prediction series the three hard gates scored.
                                Named series #1 (gate_scoring_subject=pipeline_predictions).
        tm_only_baseline_predictions: The TM-search-only prediction series, kept
                                distinct from pipeline_predictions in the same run.
                                Named series #2, scored only to derive the Gate 3
                                threshold.  None when the baseline was supplied
                                pre-measured rather than generated here.
        gate3_threshold_provenance: "measured_in_run" when the Gate 3 threshold was
                                derived from this run's tm_only_baseline, else
                                "module_constant".
        empty_by_cause:         Empty-prediction counts per empty_prediction_cause
                                (tm_search_failure / guard_rejected_in_review_queue /
                                approval_incomplete).  All three keys are always present.
        field_compliant_count:  Aggregation stage 1 — (card_id, field) pairs with a
                                non-empty prediction.
        field_violated_count:   Aggregation stage 1 — (card_id, field) pairs with an
                                empty prediction.
        pending_count:          Cards sent to HITL interrupt queue (pre-approval).
        pending_ratio:          pending_count / total_cards.
        empty_prediction_count: Predictions that were "" (TM search found no hit).
        tm_baseline_median:     TM-only baseline median edit distance (measured in run).
        gate3_derived_threshold: TM baseline × 0.7 — derived Gate 3 threshold.
        field_synthesis:        AC7 field-to-card synthesis report. Populated when
                                loading from pipeline_output.jsonl (not for TM-only
                                or flat-prediction modes).
        hitl_mode:              AC8 HITL execution mode. "eval_autoresume" when the
                                pipeline ran with deterministic auto-resume; None when
                                not applicable (TM-only or flat-prediction mode).
        hitl_trigger_count:     AC8 number of interrupt() calls that fired.
        hitl_auto_approved_count: AC8 number of cards auto-approved by the auto-responder.
        hitl_auto_held_count:   AC8 number of cards auto-held (not auto-approved).
        is_human_approved:      AC8 whether the predictions went through human approval.
                                Always False in eval_autoresume mode.
    """

    verdict: GateVerdict
    obs: Optional[ObsReport] = None
    pipeline_predictions: list[str] = field(default_factory=list)
    tm_only_baseline_predictions: Optional[list[str]] = None
    gate3_threshold_provenance: str = "module_constant"
    obs_error: Optional[str] = None
    empty_by_cause: dict[str, int] = field(default_factory=dict)
    field_compliant_count: int = 0
    field_violated_count: int = 0
    pending_count: int = 0
    pending_ratio: float = 0.0
    empty_prediction_count: int = 0
    tm_baseline_median: Optional[float] = None
    gate3_derived_threshold: Optional[float] = None
    field_synthesis: Optional[FieldSynthesisReport] = None
    # AC8: HITL mode disclosure fields
    hitl_mode: Optional[str] = None
    hitl_trigger_count: int = 0
    hitl_auto_approved_count: int = 0
    hitl_auto_held_count: int = 0
    is_human_approved: bool = False


def _tm_predictions(
    hold_out_path: Path, train_path: Path
) -> tuple[list[str], int]:
    """Generate TM-only predictions using nearest-neighbour from the training set.

    Reference leakage ban: when TM search returns no result, appends "" (empty
    string) — never substitutes hold-out ko_text.  Returns (predictions, empty_count).
    When the training set is empty, all predictions are empty strings.
    """
    hold_out: list[dict] = json.loads(hold_out_path.read_text(encoding="utf-8"))
    train: list[dict] = json.loads(train_path.read_text(encoding="utf-8"))

    preds: list[str] = []

    if not train:
        # No training data — all predictions are empty (reference_leakage_ban).
        preds = [""] * len(hold_out)
    else:
        index = HybridTMIndex(use_dense=False)
        index.build(train)

        for card in hold_out:
            results = index.search(card["en_text"], k=1)
            if results:
                preds.append(results[0]["ko_text"])
            else:
                preds.append("")  # reference_leakage_ban: no ko_text substitution

    empty_count = sum(1 for p in preds if not p)
    return preds, empty_count


def _compute_tm_baseline_median(
    hold_out_path: Path, train_path: Path
) -> tuple[float, int, list[str]]:
    """Measure the tm_only_baseline series in this run.

    This is the second named prediction series: TM search output only, with no
    guardrails, glossary injection or HITL.  It exists solely to derive the
    Gate 3 threshold (baseline × THRESHOLD_FACTOR) — it is never the gate
    scoring subject.

    Returns (baseline_median, empty_count, baseline_predictions).
    """
    preds, empty_count = _tm_predictions(hold_out_path, train_path)
    g3_result = gate3_edit_distance.score_hold_out(hold_out_path, preds)
    return g3_result.median_distance, empty_count, preds


def _load_pipeline_output(
    pipeline_output_path: Path, hold_out_path: Path
) -> tuple[list[str], int, int, float, FieldSynthesisReport, Optional[dict]]:
    """Load draft_ko predictions from a pipeline_output.jsonl file.

    AC7: Uses field-to-card synthesis to handle (card_id, field) pairs correctly.
    A card with both text and flavor fields produces two records; any empty field
    makes the card's gate-scoring prediction "" (card violated).

    AC8: When the header has _meta="eval_autoresume_header", extracts hitl_stats
    and returns them as the 6th element (None otherwise).

    Returns:
        (predictions, pending_count, empty_count, pending_ratio, field_synthesis, hitl_stats)

        predictions:      Card-level gate predictions aligned with hold_out.json order.
                          Derived from synthesize_field_to_card (AC7 synthesis rule).
        pending_count:    Cards where at least one field record has interrupted=True
                          AND auto_approved is False (or missing). eval_autoresume
                          records with auto_approved=True are NOT counted as pending.
        empty_count:      Total number of empty field predictions (across all fields).
        pending_ratio:    pending_count / total hold-out cards.
        field_synthesis:  AC7 FieldSynthesisReport (card-level compliance breakdown).
        hitl_stats:       AC8 HITL stats from eval_autoresume header, or None.

    pipeline_output.jsonl schema (one JSON object per line):
        {card_id, route, draft_ko, tm_hits, injected_terms, tm_confidence, interrupted}
        For eval_autoresume records: also {eval_autoresume, auto_approved}
    """
    hold_out: list[dict] = json.loads(hold_out_path.read_text(encoding="utf-8"))

    # Parse ALL field records — collect header for AC8 HITL stats.
    field_records: list[dict] = []
    hitl_stats: Optional[dict] = None
    with pipeline_output_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_meta" in obj:
                # AC8: extract hitl_stats from eval_autoresume header.
                if obj.get("_meta") == "eval_autoresume_header":
                    hitl_stats = obj.get("hitl_stats")
                continue
            field_records.append(obj)

    # AC7: synthesize field-level records into card-level judgments.
    synthesis = synthesize_field_to_card(field_records, hold_out)

    # Count pending cards: interrupted=True AND NOT auto_approved.
    # In eval_autoresume mode, auto_approved=True means the graph completed
    # (auto-resumed) — these are NOT truly pending (not waiting for human review).
    interrupted_cards: set[str] = {
        r.get("card_id", "")
        for r in field_records
        if r.get("interrupted", False) and not r.get("auto_approved", False)
    }
    pending_count = sum(
        1 for r in synthesis.card_results
        if r.card_id in interrupted_cards
    )

    # empty_count = total number of field-level empty predictions (all causes combined).
    empty_count = sum(synthesis.empty_by_cause.values())

    total = synthesis.total_count
    pending_ratio = pending_count / total if total > 0 else 0.0

    return synthesis.card_predictions, pending_count, empty_count, pending_ratio, synthesis, hitl_stats


def run_evaluation(
    hold_out_path: str | Path,
    glossary_path: str | Path,
    predictions: list[str],
    *,
    skip_llm_judge: bool = False,
    rule_judgments: Optional[list[RulePatternJudgment]] = None,
    flavor_judgments: Optional[list[FlavorNaturalnessJudgment]] = None,
    obs_llm: Any = None,
    pending_count: int = 0,
    pending_ratio: float = 0.0,
    empty_prediction_count: int = 0,
    tm_baseline_median: Optional[float] = None,
    tm_only_baseline_predictions: Optional[list[str]] = None,
    field_synthesis: Optional[FieldSynthesisReport] = None,
    hitl_mode: Optional[str] = None,
    hitl_trigger_count: int = 0,
    hitl_auto_approved_count: int = 0,
    hitl_auto_held_count: int = 0,
    is_human_approved: bool = False,
) -> EvaluationReport:
    """Evaluate pipeline predictions through hard gates and observation metrics.

    Calls gate_verdict.combine for the combined hard-gate verdict, and
    obs_llm_judge for observation metrics (unless skip_llm_judge is True).

    Scored artifact stage: pre-approval drafts.  pending_count/pending_ratio
    represent cards routed to HITL interrupt queue — reported separately from
    the gate verdict; the function does not wait for human approval.

    Gate 1 and 2 score the provided predictions (not card['ko_text']).
    Gate 3 scores the provided predictions against the hold-out reference.

    AC7: When field_synthesis is provided (loaded from pipeline_output.jsonl),
    the report includes card-level compliance breakdown and empty-prediction
    counts by cause (tm_search_failure / guard_rejected_in_review_queue /
    approval_incomplete).  The gate-scoring predictions are the synthesized
    card-level predictions from FieldSynthesisReport.card_predictions.

    Args:
        hold_out_path:          Path to data/hold_out.json.
        glossary_path:          Path to assets/glossary.json.
        predictions:            Draft KO translations, one per hold-out card in order.
                                When loading from pipeline_output.jsonl, these are the
                                synthesized card-level predictions (AC7).
        skip_llm_judge:         When True, obs metrics are skipped and report.obs is None.
        rule_judgments:         Pre-computed rule judgments (bypasses Bedrock call).
                                Must be paired with flavor_judgments.
        flavor_judgments:       Pre-computed flavor judgments (bypasses Bedrock call).
        obs_llm:                Optional pre-built Bedrock LLM for obs scoring.
        pending_count:          Count of cards pending HITL approval (pre-scored).
        pending_ratio:          pending_count / total_cards.
        empty_prediction_count: Count of empty-string field predictions (all causes).
        tm_baseline_median:     Pre-measured TM-only baseline median (skips re-measurement
                                when already computed by the caller).
        field_synthesis:        AC7 FieldSynthesisReport from synthesize_field_to_card.
                                None when using flat predictions (TM-only or file mode).

    Returns:
        :class:`EvaluationReport` with verdict and optional obs metrics.
    """
    hold_out_path = Path(hold_out_path)
    glossary_path = Path(glossary_path)

    # Aggregation stage 3: the three gates score the pipeline_predictions series.
    # (gate_scoring_subject = pipeline_predictions; ko_text is never a prediction.)
    g1 = gate1_term_compliance.score_hold_out(
        hold_out_path, glossary_path, predictions=predictions
    )
    g2 = gate2_symbol_preservation.score_hold_out(hold_out_path, predictions=predictions)

    # Gate 3's threshold is a relation, not a constant: it is derived from the
    # tm_only_baseline series measured in THIS run and handed to the scorer, so
    # the pass/fail decision — not merely the printout — uses the derived value.
    gate3_derived_threshold: Optional[float] = None
    gate3_threshold_provenance = "module_constant"
    if tm_baseline_median is not None:
        gate3_derived_threshold = gate3_edit_distance.derive_threshold(tm_baseline_median)
        gate3_threshold_provenance = "measured_in_run"

    g3 = gate3_edit_distance.score_hold_out(
        hold_out_path, predictions, threshold=gate3_derived_threshold
    )

    # Aggregation stage 4: single combined verdict.
    verdict = gate_verdict_mod.combine(g1, g2, g3)

    # Aggregation stage 1 (field level) and the empty-prediction cause table.
    # Every cause key is always present so the report lists all three counts,
    # including zeros.
    empty_by_cause: dict[str, int] = {c: 0 for c in ALL_CAUSES}
    field_compliant_count = 0
    field_violated_count = 0
    if field_synthesis is not None:
        for cause, count in field_synthesis.empty_by_cause.items():
            empty_by_cause[cause] = count
        for card_result in field_synthesis.card_results:
            field_violated_count += len(card_result.violated_fields)
            field_compliant_count += card_result.field_count - len(card_result.violated_fields)
    else:
        # Flat prediction series (no field records): an empty prediction can only
        # have come from the TM search returning nothing.
        empty_by_cause["tm_search_failure"] = empty_prediction_count
        field_violated_count = empty_prediction_count
        field_compliant_count = len(predictions) - empty_prediction_count

    obs: Optional[ObsReport] = None
    obs_error: Optional[str] = None
    if not skip_llm_judge:
        if rule_judgments is not None and flavor_judgments is not None:
            obs = score_from_judgments(rule_judgments, flavor_judgments)
        else:
            try:
                obs = obs_llm_judge.score_hold_out(
                    hold_out_path, predictions, llm=obs_llm
                )
            except Exception as exc:  # Bedrock unavailable / credentials absent
                # The hard-gate verdict must still be reported.  The failure is
                # surfaced in the report, never silently swallowed, and no
                # observation score is invented for a judgment the model never made.
                obs_error = f"{type(exc).__name__}: {exc}"

    return EvaluationReport(
        verdict=verdict,
        obs=obs,
        obs_error=obs_error,
        pipeline_predictions=list(predictions),
        tm_only_baseline_predictions=tm_only_baseline_predictions,
        gate3_threshold_provenance=gate3_threshold_provenance,
        empty_by_cause=empty_by_cause,
        field_compliant_count=field_compliant_count,
        field_violated_count=field_violated_count,
        pending_count=pending_count,
        pending_ratio=pending_ratio,
        empty_prediction_count=empty_prediction_count,
        tm_baseline_median=tm_baseline_median,
        gate3_derived_threshold=gate3_derived_threshold,
        field_synthesis=field_synthesis,
        hitl_mode=hitl_mode,
        hitl_trigger_count=hitl_trigger_count,
        hitl_auto_approved_count=hitl_auto_approved_count,
        hitl_auto_held_count=hitl_auto_held_count,
        is_human_approved=is_human_approved,
    )


def print_evaluation_report(report: EvaluationReport) -> None:
    """Print gate verdict and observation metrics to stdout.

    Also prints:
    - Gate1Result.llm_judged (용어집 LLM 판정 상태) — AC6(f)
    - Gate 3 threshold derivation: TM baseline × 0.7 — AC6(d)
    - Gate 1/2 threshold sources — AC6(d)
    - Interrupt-pending count/ratio — AC6(e)
    - Empty prediction count — AC6(c)
    - Gate 2 structural-fail notice when threshold > measured rate — AC6(d)
    - AC8 HITL mode disclosure: is_human_approved, trigger/approved/held counts
    """
    # AC8: HITL mode disclosure — must appear before gate scores.
    if report.hitl_mode is not None:
        print("=== HITL 실행 모드 (AC8) ===")
        print(f"  모드: {report.hitl_mode}")
        print(
            "  ※ 이 실행의 예측은 사람 승인을 거치지 않은 산출물입니다"
            f" (is_human_approved={report.is_human_approved})"
        )
        print(f"  트리거 발화 건수: {report.hitl_trigger_count}")
        print(f"  자동 승인 건수:  {report.hitl_auto_approved_count}")
        print(f"  자동 보류 건수:  {report.hitl_auto_held_count}")
        print()

    # AC6: Named prediction series.
    # Two series coexist in the same run; gates score pipeline_predictions.
    print("=== 예측 계열 (AC6) ===")
    print(
        "  게이트 채점 대상: pipeline_predictions (전체 파이프라인 출력 또는 제공된 예측)"
        f" — {len(report.pipeline_predictions)}건"
    )
    if report.tm_baseline_median is not None:
        n_base = (
            len(report.tm_only_baseline_predictions)
            if report.tm_only_baseline_predictions is not None
            else len(report.pipeline_predictions)
        )
        print(
            f"  tm_only_baseline: 게이트 3 임계 유도용 "
            f"(실측 중앙 편집거리 {report.tm_baseline_median:.4f}, {n_base}건)"
        )
        print(f"  두 계열은 같은 실행 안에서 이름으로 구분된다 (채점 대상은 pipeline_predictions).")
    else:
        print("  tm_only_baseline: 미측정 (게이트 3 임계 유도 없음)")
    print()

    print(format_verdict(report.verdict))

    # (f) Gate1Result.llm_judged
    llm_judged = report.verdict.gate1.llm_judged
    llm_judged_label = "완료" if llm_judged else "미완료 (추출 용어 미판정)"
    print(f"  용어집 LLM 판정 상태: {llm_judged_label}")

    # (d) Gate threshold sources
    print()
    print("=== 게이트 임계 출처 ===")
    print(f"  게이트 1 임계: SERVICE.md §5 명시값 >= {gate1_term_compliance.THRESHOLD:.0%}")
    print(f"  게이트 2 임계: SERVICE.md §5 명시값 {gate2_symbol_preservation.THRESHOLD:.0%}")
    # Gate 2 structural-fail notice
    if not report.verdict.gate2.passed:
        actual_rate = report.verdict.gate2.preservation_rate
        threshold = gate2_symbol_preservation.THRESHOLD
        if actual_rate < threshold:
            print(
                f"  ※ 게이트 2 구조적 불합격: 참조 코퍼스 실측 {actual_rate:.1%}"
                f" < 임계 {threshold:.0%} — 모든 출력에 대해 미달이 예상됩니다."
            )

    if report.tm_baseline_median is not None:
        factor = gate3_edit_distance.THRESHOLD_FACTOR
        derived = report.gate3_derived_threshold
        hardcoded = gate3_edit_distance.THRESHOLD
        print(
            f"  게이트 3 임계: TM-only 베이스라인 측정값 {report.tm_baseline_median:.4f}"
            f" × {factor:.2f} = {derived:.4f}"
            f"  (출처: {report.gate3_threshold_provenance}"
            f", 미사용 모듈 상수: {hardcoded:.4f})"
        )
        print(
            f"  ※ 게이트 3 합격 판정에 실제로 쓰인 임계: {report.verdict.gate3.threshold:.4f}"
        )
    else:
        print(
            f"  게이트 3 임계: 모듈 내장 상수 {gate3_edit_distance.THRESHOLD:.4f}"
            " (TM 베이스라인 미측정)"
        )

    # (e) Interrupt-pending count/ratio
    print()
    print("=== 보류 현황 (승인 전 초벌 채점) ===")
    print(
        f"  HITL interrupt 보류: {report.pending_count}건"
        f" / {len(report.verdict.gate3.card_results)}건"
        f"  ({report.pending_ratio:.1%})"
    )
    # AC6: empty-prediction counts by cause — always printed, all three causes,
    # including zeros.  An empty prediction is never backfilled with ko_text.
    print()
    print("=== 빈 예측 원인별 건수 (필드 단위) ===")
    cause_labels = {
        "tm_search_failure": "TM 검색 실패",
        "guard_rejected_in_review_queue": "가드 실패로 검토 큐 잔류",
        "approval_incomplete": "승인 미완",
    }
    for cause in ALL_CAUSES:
        print(f"  {cause_labels[cause]} ({cause}): {report.empty_by_cause.get(cause, 0)}건")
    print(
        f"  합계: {sum(report.empty_by_cause.values())}건"
        " — 정답 ko_text 대체 없음 (reference_leakage_ban)"
    )

    # AC6: three-level aggregation, printed in order.
    print()
    print("=== 3단 집계: 필드→카드 합성 (AC6/AC7) ===")
    field_total = report.field_compliant_count + report.field_violated_count
    print(
        f"  1단 필드별: 준수 {report.field_compliant_count}건"
        f" / 위반 {report.field_violated_count}건 (총 {field_total}개 (카드 id, 필드) 쌍)"
    )
    if report.field_synthesis is not None:
        fs = report.field_synthesis
        card_rate = (
            f"  ({fs.compliant_count / fs.total_count:.1%})" if fs.total_count > 0 else ""
        )
        print(
            f"  2단 카드별: 준수 {fs.compliant_count}건 / 위반 {fs.violated_count}건"
            f" (총 {fs.total_count}장){card_rate}"
            "  — 모든 필드가 준수일 때만 카드 준수"
        )
    else:
        print("  2단 카드별: 필드 레코드 없음 (평탄 예측 계열 — 카드 1장 = 예측 1개)")
    print(
        f"  3단 게이트별 비율/합격: 게이트1 {report.verdict.gate1.compliance_rate:.1%}"
        f" / 게이트2 {report.verdict.gate2.preservation_rate:.1%}"
        f" / 게이트3 중앙값 {report.verdict.gate3.median_distance:.4f}"
    )
    print(
        f"  4단 최종 판정(gate_verdict.combine): {report.verdict.verdict_label}"
    )

    print()
    if report.obs is not None:
        print(format_report(report.obs))
    elif report.obs_error is not None:
        print("=== 관찰 지표 (obs_llm_judge) ===")
        print(f"  산출 실패: {report.obs_error}")
        print("  관찰 지표는 하드 게이트가 아니므로 최종 판정에는 영향을 주지 않는다.")
    else:
        print("=== 관찰 지표 (obs_llm_judge) ===")
        print("  건너뜀 (--skip-llm-judge)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate pipeline outputs on the hold-out set (AC6 entry point).\n"
            "Scores pre-approval drafts; reports pending interrupt count separately."
        )
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing hold_out.json and train.json (default: data).",
    )
    parser.add_argument(
        "--assets-dir",
        default="assets",
        help="Directory containing glossary.json (default: assets).",
    )
    parser.add_argument(
        "--pipeline-output",
        default=None,
        help=(
            "Path to pipeline_output.jsonl produced by run_pipeline.py. "
            "Uses draft_ko as predictions and counts interrupted=true records as pending."
        ),
    )
    parser.add_argument(
        "--predictions-file",
        default=None,
        help=(
            "Path to a newline-delimited file of KO predictions, one per hold-out card."
            " JSON array (*.json) recommended to avoid newline ambiguity."
            " Mutually exclusive with --pipeline-output."
        ),
    )
    parser.add_argument(
        "--skip-llm-judge",
        action="store_true",
        help="Skip observation metrics (Bedrock calls). Useful when AWS creds are absent.",
    )
    parser.add_argument(
        "--run-pipeline",
        action="store_true",
        help=(
            "Run the full pipeline on ALL hold-out cards before evaluation. "
            "Card count is derived from data/hold_out.json (hold_out_size_source AC). "
            "Saves output to pipeline_output.jsonl then evaluates it. "
            "Mutually exclusive with --pipeline-output and --predictions-file."
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Bedrock model ID for --run-pipeline mode (omit to use stub LLM).",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    assets_dir = Path(args.assets_dir)
    hold_out_path = data_dir / "hold_out.json"
    glossary_path = assets_dir / "glossary.json"
    train_path = data_dir / "train.json"

    pending_count = 0
    pending_ratio = 0.0
    empty_prediction_count = 0
    tm_baseline_median: Optional[float] = None
    tm_baseline_predictions: Optional[list[str]] = None
    field_synthesis: Optional[FieldSynthesisReport] = None
    # AC8 HITL mode fields
    hitl_mode: Optional[str] = None
    hitl_trigger_count = 0
    hitl_auto_approved_count = 0
    hitl_auto_held_count = 0
    is_human_approved = False

    # Mutual-exclusion checks
    n_modes = sum([bool(args.pipeline_output), bool(args.predictions_file), args.run_pipeline])
    if n_modes > 1:
        parser.error(
            "--pipeline-output, --predictions-file, and --run-pipeline are mutually exclusive."
        )

    if args.run_pipeline:
        # AC6: Run the FULL pipeline on ALL hold-out cards.
        # Card count derived from file (hold_out_size_source: file, not constant).
        from run_pipeline import run_pipeline as _run_pipeline

        all_hold_out = json.loads(hold_out_path.read_text(encoding="utf-8"))
        n_cards = len(all_hold_out)  # derived from file, not hardcoded (hold_out_size_source AC)
        pipeline_output_path = Path("pipeline_output.jsonl")
        print(
            f"[INFO] Running pipeline on {n_cards} hold-out cards "
            f"(hold_out_size_source: {hold_out_path})..."
        )
        _run_pipeline(
            data_dir=data_dir,
            assets_dir=assets_dir,
            n_cards=n_cards,
            output_path=pipeline_output_path,
            llm_model=args.llm_model,
        )
        predictions, pending_count, empty_prediction_count, pending_ratio, field_synthesis, _ = (
            _load_pipeline_output(pipeline_output_path, hold_out_path)
        )
        print(
            f"[INFO] pipeline_predictions: {len(predictions)} cards "
            f"(pending={pending_count}, empty={empty_prediction_count})"
        )
        print("[INFO] Measuring tm_only_baseline for Gate 3 threshold derivation...")
        tm_baseline_median, _, tm_baseline_predictions = _compute_tm_baseline_median(
            hold_out_path, train_path
        )
        print(f"[INFO] tm_only_baseline median edit distance: {tm_baseline_median:.4f}")

    elif args.pipeline_output:
        pipeline_output_path = Path(args.pipeline_output)
        predictions, pending_count, empty_prediction_count, pending_ratio, field_synthesis, _hitl = (
            _load_pipeline_output(pipeline_output_path, hold_out_path)
        )
        # AC8: propagate HITL stats from eval_autoresume header if present.
        if _hitl is not None:
            hitl_mode = "eval_autoresume"
            hitl_trigger_count = _hitl.get("trigger_count", 0)
            hitl_auto_approved_count = _hitl.get("auto_approved_count", 0)
            hitl_auto_held_count = _hitl.get("auto_held_count", 0)
            is_human_approved = bool(_hitl.get("is_human_approved", False))
        print(
            f"[INFO] pipeline_predictions: {len(predictions)} cards loaded "
            f"(pending={pending_count}, empty={empty_prediction_count})"
        )
        if hitl_mode is not None:
            print(f"[INFO] HITL mode: {hitl_mode} (is_human_approved={is_human_approved})")
        # Measure tm_only_baseline for Gate 3 threshold derivation.
        print("[INFO] Measuring tm_only_baseline for Gate 3 threshold derivation...")
        tm_baseline_median, _, tm_baseline_predictions = _compute_tm_baseline_median(
            hold_out_path, train_path
        )
        print(f"[INFO] tm_only_baseline median edit distance: {tm_baseline_median:.4f}")

    elif args.predictions_file:
        raw = Path(args.predictions_file).read_text(encoding="utf-8")
        if args.predictions_file.endswith(".json"):
            predictions = json.loads(raw)
        else:
            predictions = [line.rstrip("\n") for line in raw.splitlines()]
        empty_prediction_count = sum(1 for p in predictions if not p)
        # Measure tm_only_baseline for Gate 3 threshold derivation.
        print("[INFO] Measuring tm_only_baseline for Gate 3 threshold derivation...")
        tm_baseline_median, _, tm_baseline_predictions = _compute_tm_baseline_median(
            hold_out_path, train_path
        )
        print(f"[INFO] tm_only_baseline median edit distance: {tm_baseline_median:.4f}")

    else:
        # Default: both pipeline_predictions and tm_only_baseline are the TM-only series.
        print("[INFO] No predictions source given — scoring tm_only_baseline as pipeline_predictions.")
        print("[INFO] Measuring tm_only_baseline...")
        predictions, empty_prediction_count = _tm_predictions(hold_out_path, train_path)
        # Gate 3 baseline = same predictions (TM-only IS the baseline)
        g3_result = gate3_edit_distance.score_hold_out(hold_out_path, predictions)
        tm_baseline_median = g3_result.median_distance
        tm_baseline_predictions = list(predictions)
        print(f"[INFO] tm_only_baseline median edit distance: {tm_baseline_median:.4f}")
        if empty_prediction_count > 0:
            print(
                f"[INFO] {empty_prediction_count} cards had no TM hit "
                "— empty predictions used (reference_leakage_ban)"
            )

    report = run_evaluation(
        hold_out_path,
        glossary_path,
        predictions,
        skip_llm_judge=args.skip_llm_judge,
        pending_count=pending_count,
        pending_ratio=pending_ratio,
        empty_prediction_count=empty_prediction_count,
        tm_baseline_median=tm_baseline_median,
        tm_only_baseline_predictions=tm_baseline_predictions,
        field_synthesis=field_synthesis,
        hitl_mode=hitl_mode,
        hitl_trigger_count=hitl_trigger_count,
        hitl_auto_approved_count=hitl_auto_approved_count,
        hitl_auto_held_count=hitl_auto_held_count,
        is_human_approved=is_human_approved,
    )
    print_evaluation_report(report)


if __name__ == "__main__":
    main()
