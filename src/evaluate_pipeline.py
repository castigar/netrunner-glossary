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

# TM Echo Gate policy constant — same status as gate1's 0.95 and gate2's 1.0.
# Not derived from a measurement, so pinning it is not "baking a measured number
# into the criteria": it is a policy floor with a 5x margin over the 1%
# coincidence rate observed on hold-out.  The other half of the threshold
# relation (the coincidence rate) stays measured_in_run.
ECHO_FLOOR = 0.05

# run_model_provenance: gate verdicts are only meaningful for a real model run.
REAL_RUN_MODE = "real"


def _norm_ws(text: str) -> str:
    """Normalize whitespace: strip, collapse internal spaces."""
    return " ".join(text.split())


@dataclass
class EchoGateResult:
    """TM Echo Gate result — regression canary for prompt/retrieval quality.

    Checks whether draft_ko coincidentally copies tm_hits[0].ko_text too often.
    A high echo rate indicates the LLM merely parrots TM results.

    Passed when actual_echo_rate <= threshold.
    Threshold = max(ECHO_FLOOR, coincidence_rate_measured_in_run).

    This gate is a canary, not adversarial defense — a single character change
    evades it. Stub run protection is handled by the llm_model=None block.
    """

    passed: bool
    actual_echo_rate: float
    coincidence_rate: float  # TM top-1 hits ref_ko: calibrates threshold
    threshold: float         # max(ECHO_FLOOR, coincidence_rate)
    denominator: int         # tm_hits non-empty AND draft_ko non-empty
    echo_count: int
    coincidence_count: int
    echo_floor: float = ECHO_FLOOR


def score_echo_gate(
    field_records: list[dict],
    hold_out_cards: list[dict],
) -> EchoGateResult:
    """Compute the TM Echo Gate over field-level pipeline records.

    Denominator: records where tm_hits is non-empty AND draft_ko is non-empty.
    Interrupted records are included (they still have draft_ko from generation).

    Coincidence rate: fraction where tm_hits[0].ko_text matches hold-out reference.
    This calibrates the threshold — it is NOT prediction leakage; the reference is
    only used to measure how often TM retrieval happens to return the right answer.
    """
    ref_by_card: dict[str, dict[str, str]] = {}
    for card in hold_out_cards:
        cid = card.get("id", "")
        ref_by_card[cid] = {
            "text": _norm_ws(card.get("ko_text") or ""),
            "flavor": _norm_ws(card.get("ko_flavor") or ""),
        }

    echo_count = 0
    coincidence_count = 0
    denominator = 0

    for rec in field_records:
        tm_hits = rec.get("tm_hits") or []
        draft_ko = _norm_ws(rec.get("draft_ko") or "")
        if not tm_hits or not draft_ko:
            continue
        denominator += 1

        top1_ko = _norm_ws(tm_hits[0].get("ko_text", ""))
        if draft_ko == top1_ko:
            echo_count += 1

        cid = rec.get("card_id", "")
        field_name = rec.get("field") or (
            "text" if rec.get("route", "rule") == "rule" else "flavor"
        )
        ref_ko = ref_by_card.get(cid, {}).get(field_name, "")
        if ref_ko and top1_ko == ref_ko:
            coincidence_count += 1

    actual_echo_rate = echo_count / denominator if denominator > 0 else 0.0
    coincidence_rate = coincidence_count / denominator if denominator > 0 else 0.0
    threshold = max(ECHO_FLOOR, coincidence_rate)

    return EchoGateResult(
        passed=actual_echo_rate <= threshold,
        actual_echo_rate=actual_echo_rate,
        coincidence_rate=coincidence_rate,
        threshold=threshold,
        denominator=denominator,
        echo_count=echo_count,
        coincidence_count=coincidence_count,
    )


@dataclass
class RunProvenance:
    """run_model_provenance: which models produced the scored artifact.

    Read from the ``run_pipeline_header`` line of pipeline_output.jsonl.  A run
    is "real" only when the header says so; gate verdicts are withheld otherwise
    (a stub run echoes the top TM hit, so its gate numbers describe the TM index,
    not a translator).
    """

    run_mode: str = "unknown"
    primary_model_id: Optional[str] = None
    models_used: list[str] = field(default_factory=list)
    model_record_counts: dict[str, int] = field(default_factory=dict)
    model_attempts_log: list[dict] = field(default_factory=list)
    model_failure_count: int = 0

    @property
    def mixed_run(self) -> bool:
        """True when more than one model produced records in the same run."""
        return len(self.models_used) > 1


@dataclass
class PerModelGateNumbers:
    """Gate numbers restricted to the cards a single model produced.

    A mixed run's single average represents neither model, so the report breaks
    the gates down per model.  These are filtered aggregations over the gate
    scorers' own ``card_results`` — the scoring logic is not re-implemented.
    """

    model_id: str
    card_count: int
    field_record_count: int
    gate1_rate: Optional[float]
    gate2_rate: Optional[float]
    gate3_median: Optional[float]
    echo_rate: Optional[float]


def _read_run_header(pipeline_output_path: Path) -> RunProvenance:
    """Read the run_pipeline_header line from a pipeline output file."""
    with Path(pipeline_output_path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("_meta") == "run_pipeline_header":
                return RunProvenance(
                    run_mode=obj.get("run_mode", "unknown"),
                    primary_model_id=obj.get("primary_model_id"),
                    models_used=list(obj.get("models_used") or []),
                    model_record_counts=dict(obj.get("model_record_counts") or {}),
                    model_attempts_log=list(obj.get("model_attempts_log") or []),
                    model_failure_count=int(obj.get("model_failure_count") or 0),
                )
            if "_meta" not in obj:
                break
    return RunProvenance()


def _cards_by_model(field_records: list[dict]) -> dict[str, set[str]]:
    """Map model_id -> set of card_ids that model produced records for."""
    out: dict[str, set[str]] = {}
    for rec in field_records:
        mid = rec.get("model_id")
        if not mid:
            continue
        out.setdefault(mid, set()).add(rec.get("card_id", ""))
    return out


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def per_model_gate_numbers(
    verdict: GateVerdict,
    field_records: list[dict],
) -> list[PerModelGateNumbers]:
    """Break the already-scored gate results down by the model that produced them.

    Filters each gate scorer's ``card_results`` by model attribution.  Returns an
    empty list when no record carries a model_id (stub runs).
    """
    by_model = _cards_by_model(field_records)
    if not by_model:
        return []

    out: list[PerModelGateNumbers] = []
    for model_id in sorted(by_model):
        cards = by_model[model_id]
        recs = [r for r in field_records if r.get("model_id") == model_id]

        g1_checks = sum(c.term_checks for c in verdict.gate1.card_results if c.card_id in cards)
        g1_viol = sum(c.violations for c in verdict.gate1.card_results if c.card_id in cards)
        gate1_rate = (g1_checks - g1_viol) / g1_checks if g1_checks else None

        g2_cards = [c for c in verdict.gate2.card_results if c.card_id in cards]
        gate2_rate = (
            sum(1 for c in g2_cards if c.passed) / len(g2_cards) if g2_cards else None
        )

        g3_dists = [c.distance for c in verdict.gate3.card_results if c.card_id in cards]
        gate3_median = _median(g3_dists)

        echo_denom = 0
        echo_hits = 0
        for r in recs:
            hits = r.get("tm_hits") or []
            draft = _norm_ws(r.get("draft_ko") or "")
            if not hits or not draft:
                continue
            echo_denom += 1
            if draft == _norm_ws(hits[0].get("ko_text", "")):
                echo_hits += 1

        out.append(PerModelGateNumbers(
            model_id=model_id,
            card_count=len(cards),
            field_record_count=len(recs),
            gate1_rate=gate1_rate,
            gate2_rate=gate2_rate,
            gate3_median=gate3_median,
            echo_rate=(echo_hits / echo_denom) if echo_denom else None,
        ))
    return out


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
    # AC6: TM Echo Gate (regression canary) and run provenance
    echo_gate_result: Optional[EchoGateResult] = None
    run_mode: str = "unknown"
    model_ids_used: list[str] = field(default_factory=list)
    provenance: Optional[RunProvenance] = None
    per_model_gates: list[PerModelGateNumbers] = field(default_factory=list)

    @property
    def gates_are_reportable(self) -> bool:
        """Gate verdicts are only issued for a real model run.

        A stub run's draft_ko is a copy of the top TM hit, so scoring it measures
        the TM index rather than the translator.  run_mode must say ``real``.
        """
        return self.run_mode == REAL_RUN_MODE

    @property
    def final_passed(self) -> Optional[bool]:
        """Overall pass: the three hard gates AND the TM echo gate.

        ``None`` when the run is not a real model run — no verdict is issued.
        """
        if not self.gates_are_reportable:
            return None
        if not self.verdict.passed:
            return False
        if self.echo_gate_result is not None and not self.echo_gate_result.passed:
            return False
        return True


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

    # Parse ALL field records — collect header for AC6 run_mode and AC8 HITL stats.
    field_records: list[dict] = []
    hitl_stats: Optional[dict] = None
    run_mode = "unknown"
    with pipeline_output_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_meta" in obj:
                meta = obj.get("_meta")
                if meta == "run_pipeline_header":
                    run_mode = obj.get("run_mode", "unknown")
                elif meta == "eval_autoresume_header":
                    hitl_stats = obj.get("hitl_stats")
                continue
            field_records.append(obj)

    # AC7: synthesize field-level records into card-level judgments.
    synthesis = synthesize_field_to_card(field_records, hold_out)
    # AC6: populate run provenance and raw field records for echo gate.
    synthesis.run_mode = run_mode
    synthesis.field_records = field_records

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
    run_mode: str = "unknown",
    model_ids_used: Optional[list[str]] = None,
    provenance: Optional[RunProvenance] = None,
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

    # AC6: TM Echo Gate — compute from field_synthesis.field_records when available.
    echo_gate_result: Optional[EchoGateResult] = None
    if field_synthesis is not None and field_synthesis.field_records:
        hold_out_cards = json.loads(Path(hold_out_path).read_text(encoding="utf-8"))
        echo_gate_result = score_echo_gate(field_synthesis.field_records, hold_out_cards)

    # Propagate run_mode / model ids from the pipeline header when not passed in.
    effective_run_mode = run_mode
    if effective_run_mode == "unknown" and provenance is not None:
        effective_run_mode = provenance.run_mode
    if effective_run_mode == "unknown" and field_synthesis is not None:
        effective_run_mode = field_synthesis.run_mode

    effective_model_ids = list(model_ids_used) if model_ids_used else []
    if not effective_model_ids and provenance is not None:
        effective_model_ids = list(provenance.models_used)

    # run_model_provenance: a mixed run's single average represents no model,
    # so gate numbers are also broken down per producing model.
    per_model: list[PerModelGateNumbers] = []
    if field_synthesis is not None and field_synthesis.field_records:
        per_model = per_model_gate_numbers(verdict, field_synthesis.field_records)

    # model_invocation_failure is produced by run_pipeline, not inferable here:
    # take the header count when the synthesis has not already classified it.
    if provenance is not None and provenance.model_failure_count:
        empty_by_cause.setdefault("model_invocation_failure", 0)

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
        echo_gate_result=echo_gate_result,
        run_mode=effective_run_mode,
        model_ids_used=effective_model_ids,
        provenance=provenance,
        per_model_gates=per_model,
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

    # AC6 run_model_provenance: run mode and model ids are part of the artifact.
    print(f"=== 실행 모드: {report.run_mode.upper()} ===")
    prov = report.provenance
    if report.run_mode == "stub":
        print("  ※ 스텁 실행 — 초벌이 TM 상위 1건의 복사본이므로 게이트 판정을 내지 않습니다.")
        print("  실제 게이트 판정은 --llm-model로 Bedrock 모델을 지정한 뒤 재실행하십시오.")
    elif report.run_mode != REAL_RUN_MODE:
        print("  ※ 실행 모드를 확인할 수 없습니다(run_pipeline_header 없음) — 게이트 판정을 내지 않습니다.")
    else:
        if prov is not None and prov.primary_model_id:
            print(f"  1순위 모델: {prov.primary_model_id}")
        if report.model_ids_used:
            print(f"  실제 사용 모델: {', '.join(report.model_ids_used)}")
        if prov is not None and prov.mixed_run:
            print("  ※ 혼합 실행 — 둘 이상의 모델이 쓰였습니다. 단일 평균은 어느 모델도 대표하지 않습니다.")
            for mid, cnt in sorted(prov.model_record_counts.items()):
                print(f"    - {mid}: {cnt} 레코드")
        if prov is not None and prov.model_attempts_log:
            failures = [a for a in prov.model_attempts_log if not a.get("success")]
            print(f"  모델 시도 로그: 총 {len(prov.model_attempts_log)}회, 실패 {len(failures)}회")
            seen: set[str] = set()
            for a in failures:
                key = f"{a.get('model_id')}|{a.get('error', '')[:80]}"
                if key in seen:
                    continue
                seen.add(key)
                print(
                    f"    - {a.get('model_id')} (시도 {a.get('attempt')}): "
                    f"{a.get('error', '')[:160]}"
                )
        if prov is not None and prov.model_failure_count:
            print(f"  전 모델 실패로 빈 예측이 된 필드: {prov.model_failure_count}건")
    print()

    # Per-model gate numbers — required whenever more than one model produced records.
    if report.per_model_gates and len(report.per_model_gates) > 1:
        print("=== 모델별 게이트 수치 (혼합 실행) ===")
        for pm in report.per_model_gates:
            def _f(v: Optional[float]) -> str:
                return "n/a" if v is None else f"{v:.4f}"
            print(
                f"  {pm.model_id}: 카드 {pm.card_count} / 필드 {pm.field_record_count} | "
                f"게이트1 {_f(pm.gate1_rate)} | 게이트2 {_f(pm.gate2_rate)} | "
                f"게이트3 중앙 {_f(pm.gate3_median)} | 에코 {_f(pm.echo_rate)}"
            )
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

    if report.run_mode != "stub":
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
                f"  ※ 게이트 3 판정에 실제로 쓰인 임계: {report.verdict.gate3.threshold:.4f}"
            )
        else:
            print(
                f"  게이트 3 임계: 모듈 내장 상수 {gate3_edit_distance.THRESHOLD:.4f}"
                " (TM 베이스라인 미측정)"
            )
    else:
        print("  [게이트 판정·임계 출처 생략 — stub 실행]")

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
        "model_invocation_failure": "Bedrock 모델 호출 실패",
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
    if report.run_mode != "stub":
        print(
            f"  3단 게이트별 비율/합격: 게이트1 {report.verdict.gate1.compliance_rate:.1%}"
            f" / 게이트2 {report.verdict.gate2.preservation_rate:.1%}"
            f" / 게이트3 중앙값 {report.verdict.gate3.median_distance:.4f}"
        )
        print(
            f"  4단 최종 판정(gate_verdict.combine): {report.verdict.verdict_label}"
        )

    # AC6: TM Echo Gate — regression canary
    print()
    print("=== TM 에코 게이트 (AC6 — 회귀 카나리아) ===")
    eg = report.echo_gate_result
    if eg is None:
        print("  에코 게이트: 필드 레코드 없음 — pipeline_output.jsonl 로드 시에만 측정됩니다.")
    else:
        status = "통과" if eg.passed else "미달"
        print(f"  결과: {status}")
        print(f"  실측 에코율: {eg.echo_count}/{eg.denominator} = {eg.actual_echo_rate:.3f}")
        print(f"  정당한 우연일치율 (TM top-1 == 참조 KO): {eg.coincidence_count}/{eg.denominator} = {eg.coincidence_rate:.3f}")
        print(f"  ECHO_FLOOR (정책 하한): {eg.echo_floor:.2f}")
        print(f"  임계 = max(ECHO_FLOOR, 우연일치율) = {eg.threshold:.3f}")
        print(f"  분모 n = {eg.denominator}  (tm_hits 존재 AND draft_ko 비어 있지 않은 레코드)")
        print(
            "  ※ 이 게이트는 적대적 방어 장치가 아니라 프롬프트·검색 회귀 카나리아입니다. "
            "문자 하나로 회피되며, 스텁 실행 방어는 진입구 봉쇄(--llm-model 필수)가 담당합니다."
        )
        print(f"  실행 모드: {report.run_mode}  모델: {', '.join(report.model_ids_used) or 'n/a'}")

    # Final pass = three hard gates AND the TM echo gate.
    print()
    if report.final_passed is None:
        print(f"=== 최종 판정: 보류 (run_mode={report.run_mode}, 실제 모델 실행 아님) ===")
    else:
        label = "합격" if report.final_passed else "불합격"
        print(f"=== 최종 판정(하드 게이트 3 + TM 에코 게이트): {label} ===")

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
        help=(
            "Bedrock model ID from availableModelsOnBedrock.md. Required with "
            "--run-pipeline; there is no stub default."
        ),
    )
    parser.add_argument(
        "--allow-stub-output",
        action="store_true",
        help=(
            "Wiring-test only: accept a --pipeline-output file whose header is not "
            "run_mode=real. Gate verdicts are still withheld for such a run."
        ),
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
    provenance: Optional[RunProvenance] = None
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
        # Stub prevention: --llm-model required; stub runs cannot reach gate scoring.
        if not args.llm_model:
            parser.error(
                "--llm-model is required with --run-pipeline. "
                "Stub runs produce meaningless gate scores. "
                "Specify a Bedrock model ID from availableModelsOnBedrock.md."
            )
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
        provenance = _read_run_header(pipeline_output_path)
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
        # stub_prevention (c): an arbitrary jsonl must not be scored as if it were
        # a real model run.  A stub-era file left in the repo reproduces the same
        # numbers with no flag, so the header is checked before anything is scored.
        provenance = _read_run_header(pipeline_output_path)
        if provenance.run_mode != REAL_RUN_MODE and not args.allow_stub_output:
            parser.error(
                f"{pipeline_output_path} has run_mode={provenance.run_mode!r}, not "
                f"{REAL_RUN_MODE!r}. Gate numbers from a stub or headerless run are "
                "invalid. Re-run run_pipeline.py with --llm-model, or pass "
                "--allow-stub-output to inspect it without a gate verdict."
            )
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
        provenance=provenance,
    )
    print_evaluation_report(report)


if __name__ == "__main__":
    main()
