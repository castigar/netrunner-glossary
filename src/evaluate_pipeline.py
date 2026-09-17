"""evaluate_pipeline.py — Entry point for AC6: evaluate pipeline outputs.

Passes pipeline outputs through all three hard gates and observation metrics
for the 100-card hold-out set, then reports verdict.

Hard gates (SERVICE.md §5):
  - Gate 1 (term compliance)     gate1_term_compliance.score_hold_out
  - Gate 2 (symbol preservation) gate2_symbol_preservation.score_hold_out
  - Gate 3 (edit distance)       gate3_edit_distance.score_hold_out
  - Combined verdict             gate_verdict.combine

Observation metrics (not hard gates):
  - obs_llm_judge.score_from_judgments or score_hold_out

Usage (CLI):
    python src/evaluate_pipeline.py
    python src/evaluate_pipeline.py --data-dir data --assets-dir assets
    python src/evaluate_pipeline.py --data-dir data --assets-dir assets --skip-llm-judge

Usage (API):
    from evaluate_pipeline import run_evaluation, EvaluationReport
    report = run_evaluation(hold_out_path, glossary_path, predictions, skip_llm_judge=True)
    print_evaluation_report(report)
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
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
        verdict:  Combined hard gate verdict from gate_verdict.combine.
        obs:      Observation metrics from obs_llm_judge. None when skipped.
    """

    verdict: GateVerdict
    obs: Optional[ObsReport] = None


def _tm_predictions(hold_out_path: Path, train_path: Path) -> list[str]:
    """Generate TM-only predictions using nearest-neighbour from the training set.

    Used as the default predictions source when no predictions file is provided.
    """
    hold_out: list[dict] = json.loads(hold_out_path.read_text(encoding="utf-8"))
    train: list[dict] = json.loads(train_path.read_text(encoding="utf-8"))

    index = HybridTMIndex(use_dense=False)
    index.build(train)

    preds: list[str] = []
    for card in hold_out:
        results = index.search(card["en_text"], k=1)
        if results:
            preds.append(results[0]["ko_text"])
        else:
            preds.append(card.get("ko_text", ""))
    return preds


def run_evaluation(
    hold_out_path: str | Path,
    glossary_path: str | Path,
    predictions: list[str],
    *,
    skip_llm_judge: bool = False,
    rule_judgments: Optional[list[RulePatternJudgment]] = None,
    flavor_judgments: Optional[list[FlavorNaturalnessJudgment]] = None,
    obs_llm: Any = None,
) -> EvaluationReport:
    """Evaluate pipeline predictions through hard gates and observation metrics.

    Calls gate_verdict.combine for the combined hard-gate verdict, and
    obs_llm_judge for observation metrics (unless skip_llm_judge is True).

    Args:
        hold_out_path:    Path to data/hold_out.json.
        glossary_path:    Path to assets/glossary.json.
        predictions:      Agent KO translations, one per hold-out card in order.
        skip_llm_judge:   When True, obs metrics are skipped and report.obs is None.
        rule_judgments:   Pre-computed rule judgments (bypasses Bedrock call).
                          Must be paired with flavor_judgments.
        flavor_judgments: Pre-computed flavor judgments (bypasses Bedrock call).
        obs_llm:          Optional pre-built Bedrock LLM for obs scoring.

    Returns:
        :class:`EvaluationReport` with verdict and optional obs metrics.
    """
    hold_out_path = Path(hold_out_path)
    glossary_path = Path(glossary_path)

    g1 = gate1_term_compliance.score_hold_out(hold_out_path, glossary_path)
    g2 = gate2_symbol_preservation.score_hold_out(hold_out_path)
    g3 = gate3_edit_distance.score_hold_out(hold_out_path, predictions)
    verdict = gate_verdict_mod.combine(g1, g2, g3)

    obs: Optional[ObsReport] = None
    if not skip_llm_judge:
        if rule_judgments is not None and flavor_judgments is not None:
            obs = score_from_judgments(rule_judgments, flavor_judgments)
        else:
            obs = obs_llm_judge.score_hold_out(
                hold_out_path, predictions, llm=obs_llm
            )

    return EvaluationReport(verdict=verdict, obs=obs)


def print_evaluation_report(report: EvaluationReport) -> None:
    """Print gate verdict and observation metrics to stdout."""
    print(format_verdict(report.verdict))
    if report.obs is not None:
        print()
        print(format_report(report.obs))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate pipeline outputs on the hold-out set (AC6 entry point)."
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
        "--predictions-file",
        default=None,
        help=(
            "Path to a newline-delimited file of KO predictions, one per hold-out card."
            " Defaults to TM-only baseline predictions from train.json."
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

    if args.predictions_file:
        raw = Path(args.predictions_file).read_text(encoding="utf-8")
        # JSON array (recommended) avoids newline ambiguity in multi-line KO texts.
        # Newline-delimited text is supported only when cards are guaranteed single-line.
        if args.predictions_file.endswith(".json"):
            predictions = json.loads(raw)
        else:
            predictions = [line.rstrip("\n") for line in raw.splitlines()]
    else:
        print("[INFO] No --predictions-file given — using TM-only baseline predictions.")
        train_path = data_dir / "train.json"
        predictions = _tm_predictions(hold_out_path, train_path)

    report = run_evaluation(
        hold_out_path,
        glossary_path,
        predictions,
        skip_llm_judge=args.skip_llm_judge,
    )
    print_evaluation_report(report)


if __name__ == "__main__":
    main()
