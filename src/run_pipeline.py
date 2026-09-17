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
from translation_graph import build_translation_graph, CardState


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
# Draft record extraction
# ---------------------------------------------------------------------------


def _state_to_draft_record(state: CardState) -> dict:
    """Convert a CardState to the draft_record_contract schema.

    Strips extra TM search fields (bm25_rank, char_rank, dense_rank) that are
    internal to the index but not part of the output contract.
    """
    slim_hits = [
        {"id": h["id"], "en_text": h["en_text"], "score": h["score"]}
        for h in state.get("tm_hits", [])
    ]
    return {
        "card_id": state.get("card_id", ""),
        "route": state.get("text_type", "rule"),
        "draft_ko": state.get("draft_ko", ""),
        "tm_hits": slim_hits,
        "injected_terms": state.get("injected_terms", []),
        "tm_confidence": state.get("tm_confidence", 0.0),
    }


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

    # ---------- Build translation graph ----------
    graph = build_translation_graph(
        tm_index=tm_index,
        flat_glossary=flat_glossary,
        llm_judged=llm_judged,
        llm=llm,
        conflict_entries=conflict_entries,
    )
    print("[run_pipeline] translation graph compiled")

    # ---------- Load hold-out cards ----------
    hold_out: list[dict] = json.loads(hold_out_path.read_text(encoding="utf-8"))
    cards = hold_out[:n_cards]
    print(f"[run_pipeline] running {len(cards)} card(s) from hold_out.json")

    # ---------- Run pipeline ----------
    draft_records: list[dict] = []
    for card in cards:
        initial: CardState = {
            "card_id": card["id"],
            "en_rule": card.get("en_text", ""),
            "en_flavor": card.get("en_flavor", ""),
        }
        state = graph.invoke(initial)

        # Interrupted cards (guard failures) still produce a draft — we record it.
        # reference_leakage_ban: never substitute hold-out ko_text as draft_ko.
        if isinstance(state, dict) and "__interrupt__" in state:
            # Retrieve current graph state snapshot for the draft fields
            record = _state_to_draft_record(state)
            record["interrupted"] = True
        else:
            record = _state_to_draft_record(state)
            record["interrupted"] = False

        draft_records.append(record)
        route = record["route"]
        tm_conf = record["tm_confidence"]
        n_terms = len(record["injected_terms"])
        print(f"  [{card['id']}] route={route} tm_confidence={tm_conf:.4f} terms={n_terms}")

    # ---------- Save output ----------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for rec in draft_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[run_pipeline] wrote {len(draft_records)} records → {output_path}")

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
