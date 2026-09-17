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
        pending_count:          Cards sent to HITL interrupt queue (pre-approval).
        pending_ratio:          pending_count / total_cards.
        empty_prediction_count: Predictions that were "" (TM search found no hit).
        tm_baseline_median:     TM-only baseline median edit distance (measured in run).
        gate3_derived_threshold: TM baseline × 0.7 — derived Gate 3 threshold.
    """

    verdict: GateVerdict
    obs: Optional[ObsReport] = None
    pending_count: int = 0
    pending_ratio: float = 0.0
    empty_prediction_count: int = 0
    tm_baseline_median: Optional[float] = None
    gate3_derived_threshold: Optional[float] = None


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
) -> tuple[float, int]:
    """Measure the TM-only baseline median edit distance and empty prediction count.

    Used to derive Gate 3 threshold at runtime: threshold = baseline × 0.7.
    Returns (baseline_median, empty_count).
    """
    preds, empty_count = _tm_predictions(hold_out_path, train_path)
    g3_result = gate3_edit_distance.score_hold_out(hold_out_path, preds)
    return g3_result.median_distance, empty_count


def _load_pipeline_output(
    pipeline_output_path: Path, hold_out_path: Path
) -> tuple[list[str], int, int, float]:
    """Load draft_ko predictions from a pipeline_output.jsonl file.

    Returns:
        (predictions, pending_count, empty_count, pending_ratio)

    pipeline_output.jsonl schema (one JSON object per line):
        {card_id, route, draft_ko, tm_hits, injected_terms, tm_confidence, interrupted}

    Cards are matched by order to hold_out.json.  If the pipeline output has
    fewer records than hold-out cards, the remainder are treated as empty predictions.
    """
    hold_out: list[dict] = json.loads(hold_out_path.read_text(encoding="utf-8"))
    hold_out_ids = [c.get("id", "") for c in hold_out]

    # Parse pipeline output
    records: list[dict] = []
    with pipeline_output_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # Build an id→record index for alignment
    record_by_id: dict[str, dict] = {r.get("card_id", ""): r for r in records}

    preds: list[str] = []
    pending_count = 0
    empty_count = 0
    for card_id in hold_out_ids:
        rec = record_by_id.get(card_id)
        if rec is None:
            preds.append("")
            empty_count += 1
        else:
            draft_ko = rec.get("draft_ko", "")
            preds.append(draft_ko)
            if not draft_ko:
                empty_count += 1
            if rec.get("interrupted", False):
                pending_count += 1

    total = len(hold_out_ids)
    pending_ratio = pending_count / total if total > 0 else 0.0
    return preds, pending_count, empty_count, pending_ratio


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
) -> EvaluationReport:
    """Evaluate pipeline predictions through hard gates and observation metrics.

    Calls gate_verdict.combine for the combined hard-gate verdict, and
    obs_llm_judge for observation metrics (unless skip_llm_judge is True).

    Scored artifact stage: pre-approval drafts.  pending_count/pending_ratio
    represent cards routed to HITL interrupt queue — reported separately from
    the gate verdict; the function does not wait for human approval.

    Gate 1 and 2 score the provided predictions (not card['ko_text']).
    Gate 3 scores the provided predictions against the hold-out reference.

    Args:
        hold_out_path:          Path to data/hold_out.json.
        glossary_path:          Path to assets/glossary.json.
        predictions:            Draft KO translations, one per hold-out card in order.
        skip_llm_judge:         When True, obs metrics are skipped and report.obs is None.
        rule_judgments:         Pre-computed rule judgments (bypasses Bedrock call).
                                Must be paired with flavor_judgments.
        flavor_judgments:       Pre-computed flavor judgments (bypasses Bedrock call).
        obs_llm:                Optional pre-built Bedrock LLM for obs scoring.
        pending_count:          Count of cards pending HITL approval (pre-scored).
        pending_ratio:          pending_count / total_cards.
        empty_prediction_count: Count of empty-string predictions (TM no-hit or missing).
        tm_baseline_median:     Pre-measured TM-only baseline median (skips re-measurement
                                when already computed by the caller).

    Returns:
        :class:`EvaluationReport` with verdict and optional obs metrics.
    """
    hold_out_path = Path(hold_out_path)
    glossary_path = Path(glossary_path)

    # Pass predictions to all three gates (gate_input_contract AC6).
    g1 = gate1_term_compliance.score_hold_out(
        hold_out_path, glossary_path, predictions=predictions
    )
    g2 = gate2_symbol_preservation.score_hold_out(hold_out_path, predictions=predictions)
    g3 = gate3_edit_distance.score_hold_out(hold_out_path, predictions)
    verdict = gate_verdict_mod.combine(g1, g2, g3)

    # Derive Gate 3 threshold from measured TM-only baseline.
    gate3_derived_threshold: Optional[float] = None
    if tm_baseline_median is not None:
        gate3_derived_threshold = tm_baseline_median * gate3_edit_distance.THRESHOLD_FACTOR

    obs: Optional[ObsReport] = None
    if not skip_llm_judge:
        if rule_judgments is not None and flavor_judgments is not None:
            obs = score_from_judgments(rule_judgments, flavor_judgments)
        else:
            obs = obs_llm_judge.score_hold_out(
                hold_out_path, predictions, llm=obs_llm
            )

    return EvaluationReport(
        verdict=verdict,
        obs=obs,
        pending_count=pending_count,
        pending_ratio=pending_ratio,
        empty_prediction_count=empty_prediction_count,
        tm_baseline_median=tm_baseline_median,
        gate3_derived_threshold=gate3_derived_threshold,
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
    """
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
            f"  (모듈 내장 상수: {hardcoded:.4f})"
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
    if report.empty_prediction_count > 0:
        print(
            f"  빈 예측 (TM 검색 결과 없음): {report.empty_prediction_count}건"
            " — 정답 ko_text 대체 없음 (reference_leakage_ban)"
        )

    if report.obs is not None:
        print()
        print(format_report(report.obs))


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

    if args.pipeline_output and args.predictions_file:
        parser.error("--pipeline-output and --predictions-file are mutually exclusive.")

    if args.pipeline_output:
        pipeline_output_path = Path(args.pipeline_output)
        predictions, pending_count, empty_prediction_count, pending_ratio = (
            _load_pipeline_output(pipeline_output_path, hold_out_path)
        )
        print(
            f"[INFO] Pipeline output loaded: {len(predictions)} cards, "
            f"pending={pending_count}, empty={empty_prediction_count}"
        )
        # Still measure TM baseline for Gate 3 threshold derivation.
        print("[INFO] Measuring TM-only baseline for Gate 3 threshold derivation...")
        tm_baseline_median, _ = _compute_tm_baseline_median(hold_out_path, train_path)
        print(f"[INFO] TM-only baseline median: {tm_baseline_median:.4f}")

    elif args.predictions_file:
        raw = Path(args.predictions_file).read_text(encoding="utf-8")
        if args.predictions_file.endswith(".json"):
            predictions = json.loads(raw)
        else:
            predictions = [line.rstrip("\n") for line in raw.splitlines()]
        empty_prediction_count = sum(1 for p in predictions if not p)
        # Measure TM baseline for Gate 3 threshold derivation.
        print("[INFO] Measuring TM-only baseline for Gate 3 threshold derivation...")
        tm_baseline_median, _ = _compute_tm_baseline_median(hold_out_path, train_path)
        print(f"[INFO] TM-only baseline median: {tm_baseline_median:.4f}")

    else:
        print("[INFO] No predictions source given — using TM-only baseline predictions.")
        print("[INFO] Measuring TM-only baseline...")
        predictions, empty_prediction_count = _tm_predictions(hold_out_path, train_path)
        # Gate 3 baseline = same predictions (TM-only IS the baseline)
        g3_result = gate3_edit_distance.score_hold_out(hold_out_path, predictions)
        tm_baseline_median = g3_result.median_distance
        print(f"[INFO] TM-only baseline median: {tm_baseline_median:.4f}")
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
    )
    print_evaluation_report(report)


if __name__ == "__main__":
    main()
