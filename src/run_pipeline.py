"""run_pipeline.py — Runner for the online translation pipeline (AC2 entry point).

Loads real assets (glossary.json, train.json, conflicts.json), builds the
HybridTMIndex and the translation graph, runs the pipeline on cards from
hold_out.json, and saves draft records to pipeline_output.jsonl.

Draft record schema (SERVICE.md §6, draft_record_contract):
    {
      card_id:        str,
      route:          "rule" | "flavor",
      draft_ko:       str,
      tm_hits:        [{id, en_text, score}, ...],
      injected_terms: [{en, ko, source, llm_judged}, ...],
      tm_confidence:  float
    }

The runner uses a stub LLM by default (--mock-llm / MOCK_LLM=1) that returns
a placeholder string built from TM hits and glossary terms.  Pass --llm-model
to use a real Bedrock model (requires AWS credentials in the environment).

Usage:
    python src/run_pipeline.py
    python src/run_pipeline.py --cards 5 --output pipeline_output.jsonl
    python src/run_pipeline.py --llm-model us.anthropic.claude-haiku-4-5-20251001-v1:0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup: src/ is the working directory when run via ``python src/...``
# from the project root; add the parent so imports resolve correctly.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from glossary_guard import load_flat_glossary
from tm_index import HybridTMIndex
from translation_graph import build_translation_graph, expand_card_to_field_inputs, CardState


# ---------------------------------------------------------------------------
# Stub LLM
# ---------------------------------------------------------------------------


class _StubLLM:
    """Deterministic stub LLM for the runner.

    Returns the KO text from the first TM hit (if any) as the draft translation.
    Falls back to a placeholder when no TM hit is found.  The purpose is to
    exercise the full pipeline wiring (TM search, glossary injection, guard
    validation) without requiring live model credentials.
    """

    def invoke(self, prompt: str) -> "_StubResponse":
        import re
        # safe_wrap() produces <card-text>\n...\n</card-text> (multi-line).
        # Find the first KO: <card-text>...</card-text> block in the TM section.
        match = re.search(r"KO:\s*<card-text>\s*(.*?)\s*</card-text>", prompt, re.DOTALL)
        if match:
            return _StubResponse(match.group(1).strip())
        return _StubResponse("[번역 초벌 — TM 일치 없음]")


class _StubResponse:
    def __init__(self, content: str) -> None:
        self.content = content


# ---------------------------------------------------------------------------
# AC4: TM confidence threshold derivation (measured_in_run)
# ---------------------------------------------------------------------------


def derive_tm_confidence_threshold(
    *,
    hold_out: list[dict],
    tm_index: HybridTMIndex,
    percentile: float = 20.0,
) -> tuple[float, dict]:
    """Derive the TM confidence threshold from the current run's corpus.

    AC4 constraint: the threshold for trigger ④ (low_tm_confidence) must be
    measured_in_run — derived from the distribution of tm_confidence scores
    observed during the current run, NOT a hard-coded constant.

    Searches TM for every hold-out EN text (NOT ko_text — reference_leakage_ban)
    and collects the top-1 RRF score.  Returns the *percentile*-th percentile of
    those scores as the threshold so that the bottom fraction of cards (low TM
    similarity) fires the interrupt trigger.

    Args:
        hold_out:   Hold-out records.  Only ``en_text`` is read; ``ko_text`` is
                    never accessed, satisfying hitl_threshold_provenance constraint.
        tm_index:   Already-built HybridTMIndex for TM searches.
        percentile: Lower percentile to use as threshold (default 20 → p20).
                    Cards whose tm_confidence < p20 of the corpus distribution
                    will fire trigger ④.

    Returns:
        (threshold, derivation_record)
        where derivation_record contains:
          - derivation_rule: human-readable description of the derivation
          - percentile:      the percentile used
          - n_cards:         number of cards whose scores contributed
          - scores_min/max/median: distribution statistics (no ko_text used)
          - derived_threshold: the computed threshold value
          - provenance:      always "measured_in_run" (AC4 ontology)
    """
    import statistics

    scores: list[float] = []
    for card in hold_out:
        en_text = (card.get("en_text") or "").strip()
        if not en_text:
            continue
        results = tm_index.search(en_text, k=1)
        scores.append(results[0]["score"] if results else 0.0)

    if not scores:
        threshold = 0.0
        derivation_record: dict = {
            "derivation_rule": f"p{percentile:.0f}(tm_confidence) from hold-out EN texts — no scores (empty index)",
            "percentile": percentile,
            "n_cards": 0,
            "scores_min": None,
            "scores_max": None,
            "scores_median": None,
            "derived_threshold": threshold,
            "provenance": "measured_in_run",
        }
        return threshold, derivation_record

    sorted_scores = sorted(scores)
    n = len(sorted_scores)
    idx = max(0, min(n - 1, int(percentile / 100.0 * n)))
    threshold = sorted_scores[idx]

    derivation_record = {
        "derivation_rule": (
            f"p{percentile:.0f}(tm_confidence scores from {n} hold-out EN texts, "
            f"no ko_text used — reference_leakage_ban)"
        ),
        "percentile": percentile,
        "n_cards": n,
        "scores_min": sorted_scores[0],
        "scores_max": sorted_scores[-1],
        "scores_median": statistics.median(sorted_scores),
        "derived_threshold": threshold,
        "provenance": "measured_in_run",
    }
    return threshold, derivation_record


# ---------------------------------------------------------------------------
# Draft record extraction
# ---------------------------------------------------------------------------


def _state_to_draft_record(state: CardState) -> dict:
    """Convert a CardState to the draft_record_contract schema.

    Strips extra TM search fields (bm25_rank, char_rank, dense_rank) that are
    internal to the index but not part of the output contract.

    AC3: When the card was routed to the review queue (guard_violations non-empty),
    the violation details are preserved in the output record so the caller can see
    which guard failed and why — not just that the card was interrupted.
    """
    slim_hits = [
        {"id": h["id"], "en_text": h["en_text"], "score": h["score"]}
        for h in state.get("tm_hits", [])
    ]
    record: dict = {
        "card_id": state.get("card_id", ""),
        "route": state.get("text_type", "rule"),
        "draft_ko": state.get("draft_ko", ""),
        "tm_hits": slim_hits,
        "injected_terms": state.get("injected_terms", []),
        "tm_confidence": state.get("tm_confidence", 0.0),
        "glossary_llm_judged": state.get("glossary_llm_judged", False),
    }
    # AC3: preserve guard violation details (which guard, why) in interrupted records.
    # guard_violations is set by validate_draft and carries {guard, detail|matches} per entry.
    guard_violations = state.get("guard_violations")
    if guard_violations:
        record["guard_violations"] = guard_violations
    return record


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_pipeline(
    *,
    data_dir: Path,
    assets_dir: Path,
    n_cards: int,
    output_path: Path,
    llm_model: str | None = None,
    tm_threshold_percentile: float = 20.0,
) -> list[dict]:
    """Wire the full pipeline and run it on *n_cards* from hold_out.json.

    Returns the list of draft records saved to *output_path*.
    """
    glossary_path = assets_dir / "glossary.json"
    conflicts_path = assets_dir / "conflicts.json"
    train_path = data_dir / "train.json"
    hold_out_path = data_dir / "hold_out.json"

    # ---------- Load assets ----------
    flat_glossary, llm_judged = load_flat_glossary(glossary_path)
    print(f"[run_pipeline] glossary loaded: {len(flat_glossary)} terms, llm_judged={llm_judged}")

    conflict_entries: list[dict] = []
    if conflicts_path.exists():
        raw_conflicts = json.loads(conflicts_path.read_text(encoding="utf-8"))
        conflict_entries = raw_conflicts if isinstance(raw_conflicts, list) else []
        print(f"[run_pipeline] conflicts loaded: {len(conflict_entries)} entries")

    train_data: list[dict] = json.loads(train_path.read_text(encoding="utf-8"))
    print(f"[run_pipeline] training corpus: {len(train_data)} records")

    # ---------- Build TM index ----------
    print("[run_pipeline] building HybridTMIndex (BM25 + dense + char)...")
    tm_index = HybridTMIndex(use_dense=False)  # dense skipped for speed; enable for production
    tm_index.build(train_data)
    print(f"[run_pipeline] index built with {len(tm_index)} records")

    # ---------- Choose LLM ----------
    if llm_model:
        from langchain_aws import ChatBedrockConverse
        llm: Any = ChatBedrockConverse(model=llm_model)
        print(f"[run_pipeline] using Bedrock model: {llm_model}")
    else:
        llm = _StubLLM()
        print("[run_pipeline] using stub LLM (no model credentials needed)")

    # ---------- AC4: Derive TM confidence threshold from current run ----------
    # Reference leakage ban: only hold-out EN texts are used — ko_text is never read.
    # The threshold is measured_in_run (hitl_threshold_provenance == "measured_in_run").
    all_hold_out: list[dict] = json.loads(hold_out_path.read_text(encoding="utf-8"))
    tm_threshold, tm_threshold_derivation = derive_tm_confidence_threshold(
        hold_out=all_hold_out,
        tm_index=tm_index,
        percentile=tm_threshold_percentile,
    )
    print(
        f"[run_pipeline] AC4 TM threshold derived: {tm_threshold:.6f}"
        f" (p{tm_threshold_percentile:.0f} of {tm_threshold_derivation['n_cards']} cards)"
        f" provenance={tm_threshold_derivation['provenance']}"
    )

    # ---------- Build translation graph ----------
    graph = build_translation_graph(
        tm_index=tm_index,
        flat_glossary=flat_glossary,
        llm_judged=llm_judged,
        llm=llm,
        conflict_entries=conflict_entries,
        tm_threshold=tm_threshold,
    )
    print("[run_pipeline] translation graph compiled")

    # ---------- Load hold-out cards ----------
    # all_hold_out already loaded for threshold derivation above.
    cards = all_hold_out[:n_cards]
    print(f"[run_pipeline] running {len(cards)} card(s) from hold_out.json")

    # ---------- Run pipeline ----------
    # AC1: iteration unit is (card_id, field) pair, not card.
    # A card with both en_text and en_flavor produces 2 DraftRecords.
    draft_records: list[dict] = []
    for card in cards:
        field_inputs = expand_card_to_field_inputs(card)
        for initial in field_inputs:
            state = graph.invoke(initial)

            # Interrupted cards (guard failures) still produce a draft — we record it.
            # reference_leakage_ban: never substitute hold-out ko_text as draft_ko.
            if isinstance(state, dict) and "__interrupt__" in state:
                record = _state_to_draft_record(state)
                record["interrupted"] = True
            else:
                record = _state_to_draft_record(state)
                record["interrupted"] = False

            draft_records.append(record)
            route = record["route"]
            tm_conf = record["tm_confidence"]
            n_terms = len(record["injected_terms"])
            print(f"  [{card['id']}:{route}] tm_confidence={tm_conf:.4f} terms={n_terms}")

    # ---------- Save output ----------
    # AC4: Write a metadata header line first with the threshold derivation record.
    # This records the measured_in_run threshold provenance in the output artifact.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "_meta": "run_pipeline_header",
        "tm_threshold_derivation": tm_threshold_derivation,
    }
    with output_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(metadata, ensure_ascii=False) + "\n")
        for rec in draft_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[run_pipeline] wrote {len(draft_records)} records → {output_path}")
    print(
        f"[run_pipeline] AC4 threshold derivation rule: "
        f"{tm_threshold_derivation['derivation_rule']}"
    )

    return draft_records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the translation pipeline on hold-out cards (AC2 entry point)."
    )
    parser.add_argument("--data-dir", default="data", help="Directory with hold_out.json and train.json")
    parser.add_argument("--assets-dir", default="assets", help="Directory with glossary.json and conflicts.json")
    parser.add_argument("--cards", type=int, default=3, help="Number of hold-out cards to process (default: 3)")
    parser.add_argument("--output", default="pipeline_output.jsonl", help="Output file path")
    parser.add_argument("--llm-model", default=None, help="Bedrock model ID (omit for stub LLM)")
    args = parser.parse_args()

    records = run_pipeline(
        data_dir=Path(args.data_dir),
        assets_dir=Path(args.assets_dir),
        n_cards=args.cards,
        output_path=Path(args.output),
        llm_model=args.llm_model,
    )
    print(f"\n[run_pipeline] done — {len(records)} draft records saved")


if __name__ == "__main__":
    main()
