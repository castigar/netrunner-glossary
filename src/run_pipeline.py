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
# Bedrock model fallback chain (availableModelsOnBedrock.md order)
# ---------------------------------------------------------------------------

FALLBACK_MODELS: tuple[str, ...] = (
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-2-lite-v1:0",
    "global.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-lite-v1:0",
)

# Exception message patterns that indicate throttling / capacity issues.
_RETRYABLE_PATTERNS = (
    "ThrottlingException",
    "throttl",
    "TooManyRequests",
    "ServiceUnavailable",
    "ModelNotReady",
    "modelStreamError",
    "RequestTooLarge",
)


class _FallbackLLM:
    """Bedrock LLM wrapper that retries through FALLBACK_MODELS on throttling failures.

    Never falls back to _StubLLM — if all models fail, raises RuntimeError.
    Tracks last_model_id and model_attempts_log for run provenance reporting.
    """

    def __init__(self, primary_model_id: str) -> None:
        self._model_ids: list[str] = [primary_model_id] + [
            m for m in FALLBACK_MODELS if m != primary_model_id
        ]
        self.last_model_id: str = primary_model_id
        self.model_attempts_log: list[dict] = []
        self.models_used: set[str] = set()

    def invoke(self, prompt: str) -> Any:
        import time
        from langchain_aws import ChatBedrockConverse

        last_error: Exception | None = None
        for model_id in self._model_ids:
            for attempt in range(1, 4):  # up to 3 attempts per model
                try:
                    response = ChatBedrockConverse(model=model_id).invoke(prompt)
                    self.last_model_id = model_id
                    self.models_used.add(model_id)
                    self.model_attempts_log.append(
                        {"model_id": model_id, "attempt": attempt, "success": True}
                    )
                    return response
                except Exception as exc:
                    err_str = str(exc)
                    retryable = any(p in err_str for p in _RETRYABLE_PATTERNS)
                    self.model_attempts_log.append({
                        "model_id": model_id,
                        "attempt": attempt,
                        "success": False,
                        "error": err_str[:300],
                        "retryable": retryable,
                    })
                    last_error = exc
                    if retryable and attempt < 3:
                        time.sleep(2 ** attempt)
                        continue
                    break  # non-retryable or exhausted retries → try next model

        raise RuntimeError(
            f"All Bedrock fallback models failed. "
            f"Models tried: {self._model_ids}. Last error: {last_error}"
        )


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

    AC5: new_terms field carries new_term_identity pairs ({en, ko_rendering}).
    For interrupted cards before approval, EN terms come from guard_violations
    with ko_rendering="" (unknown until human approval).  For post-approval states
    (after Command(resume=...)), new_terms comes from state["new_terms"] which
    carries the actual ko_rendering supplied by the reviewer.
    """
    slim_hits = [
        {"id": h["id"], "en_text": h["en_text"], "ko_text": h.get("ko_text", ""), "score": h["score"]}
        for h in state.get("tm_hits", [])
    ]

    # AC5: new_terms = new_term_identity pairs in DraftRecord.
    # Post-approval state (review_queue node set new_terms after interrupt returned).
    new_terms_in_state = state.get("new_terms")
    if new_terms_in_state is not None:
        new_terms: list[dict] = list(new_terms_in_state)
    else:
        # Pre-approval (interrupted) or clean pass: extract EN terms from guard_violations.
        # ko_rendering is unknown before approval — stored as "".
        new_terms = []
        for v in state.get("guard_violations") or []:
            if v.get("guard") == "new_term":
                for en_term in v.get("detail", {}).get("new_terms", []):
                    new_terms.append({"en": en_term, "ko_rendering": ""})

    record: dict = {
        "card_id": state.get("card_id", ""),
        "route": state.get("text_type", "rule"),
        "draft_ko": state.get("draft_ko", ""),
        "tm_hits": slim_hits,
        "injected_terms": state.get("injected_terms", []),
        "tm_confidence": state.get("tm_confidence", 0.0),
        "glossary_llm_judged": state.get("glossary_llm_judged", False),
        "new_terms": new_terms,  # AC5: new_term_identity pairs (en, ko_rendering)
        "recording_new_terms": state.get("recording_new_terms") or [],  # AC4: stored at detection time
    }
    # AC3: preserve guard violation details (which guard, why) in interrupted records.
    # guard_violations is set by validate_draft and carries {guard, detail|matches} per entry.
    guard_violations = state.get("guard_violations")
    if guard_violations:
        record["guard_violations"] = guard_violations
    return record


def _empty_model_failure_record(card: dict, initial: dict, error: str) -> dict:
    """Build an empty DraftRecord for a field whose model invocation failed.

    empty_prediction_cause = model_invocation_failure: every model in the
    availableModelsOnBedrock.md fallback chain failed for this field.  The
    record keeps draft_ko="" — neither stub output nor the hold-out ko_text
    is substituted (prediction_fallback_policy).
    """
    is_rule = bool((initial.get("en_rule") or "").strip())
    return {
        "card_id": card.get("id", ""),
        "field": "text" if is_rule else "flavor",
        "route": "rule" if is_rule else "flavor",
        "draft_ko": "",
        "tm_hits": [],
        "injected_terms": [],
        "tm_confidence": 0.0,
        "glossary_llm_judged": False,
        "new_terms": [],
        "recording_new_terms": [],
        "interrupted": False,
        "empty_cause": "model_invocation_failure",
        "model_error": error[:300],
        "model_id": None,
    }


# ---------------------------------------------------------------------------
# AC8: deterministic auto-responder (eval_autoresume mode)
# ---------------------------------------------------------------------------

#: Guard/trigger names whose interrupts the auto-responder refuses to resolve.
#: A blocking ``new_term`` means the source text carries an unregistered subtype
#: or proper noun; no machine can invent its Korean rendering, so approving the
#: draft as-is would launder an unresolved term into approved.jsonl.  Held
#: fields keep an empty prediction (empty_prediction_cause=approval_incomplete);
#: the hold-out ko_text is never substituted.
AUTO_RESPONDER_HOLD_TRIGGERS: frozenset[str] = frozenset({"new_term"})


def auto_respond(interrupt_payload: dict) -> str:
    """Decide the eval_autoresume resume value from the interrupt payload alone.

    Returns ``"approved"`` (resume with the draft unchanged) or ``"hold"``
    (do not resume; the field yields an empty prediction).

    The decision is a pure function of ``interrupt_payload["violations"]`` — the
    same payload a human reviewer would see.  It never reads data/hold_out.json
    or any reference ko_text (eval_mode_hitl_policy), and it carries no state
    across fields, so repeated runs over the same input are byte-identical.
    """
    violations = interrupt_payload.get("violations") or []
    for v in violations:
        if v.get("guard") in AUTO_RESPONDER_HOLD_TRIGGERS:
            return "hold"
    return "approved"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_pipeline(
    *,
    data_dir: Path,
    assets_dir: Path,
    n_cards: int,
    output_path: Path,
    llm_model: str | None,
    tm_threshold_percentile: float = 20.0,
    eval_autoresume: bool = False,
    approved_store_path: Path | str | None = None,
    new_term_candidates_path: Path | str | None = None,
) -> list[dict]:
    """Wire the full pipeline and run it on *n_cards* from hold_out.json.

    Returns the list of draft records saved to *output_path*.

    Args:
        llm_model: Bedrock model ID. Pass None only for wiring/unit tests (stub LLM).
            No default — explicit opt-in required (Seed: stub_prevention).
        eval_autoresume: AC8 eval mode. When True, the graph runs with a
            MemorySaver checkpointer so interrupt() actually fires, and a
            deterministic auto-responder supplies Command(resume="approved")
            for every interrupted card without consulting hold-out ko_text.
            An eval_autoresume_header is written to the output with HITL stats.
            Predictions are NOT human-approved (is_human_approved=False always).
        approved_store_path: Path to approved.jsonl. Defaults to output_path.parent / "approved.jsonl".
        new_term_candidates_path: Path to new_term_candidates.json. Defaults to output_path.parent / "new_term_candidates.json".
    """
    if approved_store_path is None:
        approved_store_path = output_path.parent / "approved.jsonl"
    if new_term_candidates_path is None:
        new_term_candidates_path = output_path.parent / "new_term_candidates.json"
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
    if llm_model is not None:
        llm: Any = _FallbackLLM(llm_model)
        run_mode = "real"
        print(f"[run_pipeline] using Bedrock model: {llm_model} (with fallback chain)")
    else:
        llm = _StubLLM()
        run_mode = "stub"
        print("[run_pipeline] using stub LLM (wiring/unit test mode)")

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
    # AC8: eval_autoresume mode uses MemorySaver so interrupt() can be resumed.
    # Operational mode (eval_autoresume=False) uses no checkpointer — graph
    # fires interrupt() but cannot resume in the same call.
    checkpointer = None
    if eval_autoresume:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
        print("[run_pipeline] AC8 eval_autoresume mode: MemorySaver checkpointer enabled")

    graph = build_translation_graph(
        tm_index=tm_index,
        flat_glossary=flat_glossary,
        llm_judged=llm_judged,
        llm=llm,
        conflict_entries=conflict_entries,
        tm_threshold=tm_threshold,
        checkpointer=checkpointer,
        approved_store_path=approved_store_path,
        new_term_candidates_path=new_term_candidates_path,
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
    # AC8: HITL stats counters (only meaningful in eval_autoresume mode).
    hitl_trigger_count = 0
    hitl_auto_approved_count = 0
    hitl_auto_held_count = 0
    # empty_prediction_cause: model_invocation_failure count.
    model_failure_count = 0

    for card in cards:
        field_inputs = expand_card_to_field_inputs(card)
        for field_idx, initial in enumerate(field_inputs):
            # model_invocation_failure: all Bedrock fallback models failed for
            # this field. Record an empty prediction with its cause — never
            # substitute stub output or hold-out ko_text (prediction_fallback_policy).
            try:
                if eval_autoresume:
                    # AC8 eval_autoresume mode:
                    # 1. Invoke with a per-(card, field) thread_id so the checkpointer
                    #    tracks each field independently.
                    # 2. If interrupt() fires, fire the deterministic auto-responder
                    #    (Command(resume="approved")) without consulting ko_text.
                    # 3. Record auto_approved and eval_autoresume flags in the output.
                    from langgraph.types import Command

                    field_label = "text" if (initial.get("en_rule") or "").strip() else "flavor"
                    thread_id = f"{card.get('id', '')}:{field_label}:{field_idx}"
                    config = {"configurable": {"thread_id": thread_id}}

                    state = graph.invoke(initial, config=config)

                    if isinstance(state, dict) and "__interrupt__" in state:
                        # Interrupt fired for real (never bypassed).  The
                        # deterministic auto-responder decides from the same
                        # payload a human reviewer would see — no ko_text is
                        # consulted (eval_mode_hitl_policy).
                        hitl_trigger_count += 1
                        payload = state["__interrupt__"][0].value
                        decision = auto_respond(payload)
                        if decision == "approved":
                            state = graph.invoke(Command(resume="approved"), config=config)
                            record = _state_to_draft_record(state)
                            record["interrupted"] = True
                            record["eval_autoresume"] = True
                            record["auto_approved"] = True
                            record["auto_held"] = False
                            hitl_auto_approved_count += 1
                        else:
                            # Held: the graph stays paused at interrupt().  The
                            # field yields an empty prediction rather than an
                            # unreviewed draft; hold-out ko_text is never
                            # substituted (prediction_fallback_policy).
                            record = _state_to_draft_record(state)
                            record["draft_ko"] = ""
                            record["interrupted"] = True
                            record["eval_autoresume"] = True
                            record["auto_approved"] = False
                            record["auto_held"] = True
                            record["empty_cause"] = "approval_incomplete"
                            record["hold_reasons"] = sorted(
                                {
                                    v.get("guard", "")
                                    for v in (payload.get("violations") or [])
                                    if v.get("guard") in AUTO_RESPONDER_HOLD_TRIGGERS
                                }
                            )
                            hitl_auto_held_count += 1
                    else:
                        record = _state_to_draft_record(state)
                        record["interrupted"] = False
                        record["eval_autoresume"] = True
                        record["auto_approved"] = False
                        record["auto_held"] = False
                else:
                    # Operational mode: interrupt fires and graph pauses — no auto-resume.
                    # reference_leakage_ban: never substitute hold-out ko_text as draft_ko.
                    state = graph.invoke(initial)

                    if isinstance(state, dict) and "__interrupt__" in state:
                        record = _state_to_draft_record(state)
                        record["interrupted"] = True
                    else:
                        record = _state_to_draft_record(state)
                        record["interrupted"] = False

            except RuntimeError as exc:
                record = _empty_model_failure_record(card, initial, str(exc))
                model_failure_count += 1
                print(
                    f"[run_pipeline] model_invocation_failure on "
                    f"{record['card_id']}:{record['field']} — {exc}"
                )

            # draft_record contract: field is the primary unit alongside card_id.
            record.setdefault("field", "text" if record.get("route") == "rule" else "flavor")

            # run_model_provenance: record which model generated this draft.
            # Failure records already carry model_id=None and keep it.
            if "model_id" not in record:
                record["model_id"] = llm.last_model_id if isinstance(llm, _FallbackLLM) else None

            draft_records.append(record)
            route = record["route"]
            tm_conf = record["tm_confidence"]
            n_terms = len(record["injected_terms"])
            interrupted_flag = record.get("interrupted", False)
            auto_flag = f" auto_approved={record['auto_approved']}" if eval_autoresume else ""
            print(
                f"  [{card['id']}:{route}] tm_confidence={tm_conf:.4f}"
                f" terms={n_terms} interrupted={interrupted_flag}{auto_flag}"
            )

    # ---------- Save output ----------
    # AC4: Write a metadata header line first with the threshold derivation record.
    # AC6: run_mode and model provenance recorded for echo gate and stub prevention.
    # AC8: In eval_autoresume mode, also write an eval_autoresume_header with HITL stats.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {
        "_meta": "run_pipeline_header",
        "run_mode": run_mode,
        "primary_model_id": llm_model,
        "tm_threshold_derivation": tm_threshold_derivation,
        "model_failure_count": model_failure_count,
    }
    if isinstance(llm, _FallbackLLM):
        metadata["models_used"] = sorted(llm.models_used)
        metadata["model_attempts_log"] = llm.model_attempts_log
        mixed = len(llm.models_used) > 1
        metadata["mixed_run"] = mixed
        if mixed:
            # Seed: mixed run must be disclosed.
            model_counts: dict[str, int] = {}
            for rec in draft_records:
                mid = rec.get("model_id") or "unknown"
                model_counts[mid] = model_counts.get(mid, 0) + 1
            metadata["model_record_counts"] = model_counts
    with output_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(metadata, ensure_ascii=False) + "\n")
        if eval_autoresume:
            # AC8: eval_autoresume_header consumed by evaluate_pipeline._load_pipeline_output.
            # is_human_approved is always False — the auto-responder is not a human.
            eval_header = {
                "_meta": "eval_autoresume_header",
                "hitl_stats": {
                    "trigger_count": hitl_trigger_count,
                    "auto_approved_count": hitl_auto_approved_count,
                    "auto_held_count": hitl_auto_held_count,
                    "is_human_approved": False,
                },
            }
            f.write(json.dumps(eval_header, ensure_ascii=False) + "\n")
        for rec in draft_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[run_pipeline] wrote {len(draft_records)} records → {output_path}")
    print(
        f"[run_pipeline] AC4 threshold derivation rule: "
        f"{tm_threshold_derivation['derivation_rule']}"
    )
    if eval_autoresume:
        print(
            f"[run_pipeline] AC8 eval_autoresume stats: "
            f"trigger_count={hitl_trigger_count} "
            f"auto_approved={hitl_auto_approved_count} "
            f"auto_held={hitl_auto_held_count} "
            f"is_human_approved=False"
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
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Bedrock model ID from availableModelsOnBedrock.md (required unless --allow-stub)",
    )
    parser.add_argument(
        "--allow-stub",
        action="store_true",
        help=(
            "Wiring-test only: run with _StubLLM, which echoes the top TM hit. "
            "Gate scores from a stub run are meaningless and are not reported."
        ),
    )
    parser.add_argument(
        "--approved-store",
        default=None,
        help="Path for approved.jsonl (default: module default). Separate concurrent runs.",
    )
    parser.add_argument(
        "--new-term-candidates",
        default=None,
        help="Path for new_term_candidates.json (default: module default).",
    )
    parser.add_argument(
        "--eval-autoresume",
        action="store_true",
        help=(
            "AC8 eval mode: fire interrupt() and auto-resume with a deterministic "
            "auto-responder (never consults ko_text). Writes eval_autoresume_header "
            "to output with HITL stats. is_human_approved is always False."
        ),
    )
    args = parser.parse_args()
    # stub_prevention: the stub is reachable only behind an explicit flag.
    if not args.llm_model and not args.allow_stub:
        parser.error(
            "--llm-model is required. Pass a Bedrock model ID from "
            "availableModelsOnBedrock.md, or --allow-stub for wiring tests only."
        )
    if args.llm_model and args.allow_stub:
        parser.error("--llm-model and --allow-stub are mutually exclusive.")

    records = run_pipeline(
        data_dir=Path(args.data_dir),
        assets_dir=Path(args.assets_dir),
        n_cards=args.cards,
        output_path=Path(args.output),
        llm_model=args.llm_model,
        eval_autoresume=args.eval_autoresume,
        approved_store_path=args.approved_store,
        new_term_candidates_path=args.new_term_candidates,
    )
    print(f"\n[run_pipeline] done — {len(records)} draft records saved")


if __name__ == "__main__":
    main()
