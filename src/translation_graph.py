"""translation_graph.py — 2단계 온라인 번역 파이프라인 그래프.

SERVICE.md §6, 2단계:
  새 카드 → 종류 라우팅(룰/플레이버)
    → TM 검색 + 용어 주입 → 초벌 생성  [패턴 2, 4]
    → 규칙 검증 (4절 가드레일)          [패턴 6]
    → 위반·신규용어·저신뢰 → interrupt() [패턴 7]
    → 번역가 수정·승인 → approved.jsonl  [패턴 10]

AC1 범위: 새 카드를 룰 텍스트와 플레이버로 라우팅한다.
  - 라우팅은 에이전트 분할이 아니라 그래프 분기(conditional edge)로 구현.
  - Supervisor 패턴·Plan-Execute 미채택.

AC2 범위: TM 검색과 용어집 주입으로 초벌 번역을 생성한다.
  - injection_guard.safe_wrap으로 카드 텍스트를 감싼다.
  - HybridTMIndex.search로 유사 카드를 검색한다.
  - glossary.json에서 관련 용어를 주입한다.
  - llm_judged=False면 추출 용어가 미판정임을 프롬프트에 드러낸다.

AC3 범위: 생성된 초벌을 기존 가드레일 5종으로 검증한다.
  - injection_guard.check_injection: 소스 텍스트 프롬프트 인젝션 검사.
  - hitl_interrupt.check_hitl_triggers: new_term·conflict·glossary·fidelity 4종 통합 검사.
  - 검증 실패 건은 review_queue로 라우팅 (승인 저장소로 가지 않음).

AC5 범위: 사람이 승인하면 approved.jsonl에 적재하고 신규 용어를 new_term_candidates.json에 적재한다.
  - interrupt() 반환값으로 사람 결정을 수신한다.
  - approved_store.append_approved: approved.jsonl 기록.
  - approved_store.append_new_term_candidate: 신규 용어만 new_term_candidates.json 기록.
  - glossary.json은 승인만으로 갱신되지 않는다.

카드 한 장은 text_type이 결정되면 그에 맞는 브랜치로 흐른다.
  "rule"   — en_rule(text 필드)이 있는 카드
  "flavor" — en_rule이 없고 en_flavor(flavor 필드)만 있는 카드
  두 필드가 모두 있으면 rule 브랜치로 라우팅한다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, StateGraph

from approved_store import (
    ApprovedRecord,
    NewTermCandidateRecord,
    append_approved,
    append_new_term_candidate,
)
from glossary_guard import GlossaryFlat
from hitl_interrupt import check_hitl_triggers
from injection_guard import check_injection, safe_wrap
from tm_index import HybridTMIndex


# ---------------------------------------------------------------------------
# AC3: Guard-input projection adapter
# ---------------------------------------------------------------------------


def _apply_guard_projections(
    *,
    en_source: str,
    draft_ko: str,
    injected_terms: list[dict],
    flat_glossary: GlossaryFlat,
    llm_judged: bool,
    conflict_entries: list[dict],
    tm_confidence: float | None = None,
    tm_threshold: float | None = None,
) -> list[dict]:
    """Single adapter that maps DraftRecord field projections to guard calls.

    Guard-input projection mapping (guard_input_projection ontology concept):
      injection_guard:  (en_source, draft_ko)
      fidelity_guard:   (en_source, draft_ko)      [via check_hitl_triggers]
      glossary_guard:   (draft_ko, injected_terms) [via check_hitl_triggers]
      new_term_guard:   (en_source, draft_ko, glossary) [via check_hitl_triggers]
      conflict_guard:   (draft_ko, injected_terms, conflicts) [via check_hitl_triggers]

    Each guard is imported and called here — not reimplemented.  Calling a
    guard without declaring its projection in this adapter does not count as
    calling it (wiring_vs_reimplementation constraint).

    Args:
        en_source:       EN source text (text or flavor field, already stripped).
        draft_ko:        Draft Korean translation from the process node.
        injected_terms:  Per-term provenance list from the process node
                         ({en, ko, source, llm_judged}); pre-filtered to terms
                         present in en_source. Used as the glossary_guard and
                         conflict_guard projections.
        flat_glossary:   Full flat glossary for new_term_guard and the HITL
                         coordinator (glossary_guard uses this internally).
        llm_judged:      Whether the extracted glossary section is validated.
        conflict_entries: Conflict entries from conflicts.json for conflict_guard.
        tm_confidence:   Top-1 TM RRF score; triggers low_tm_confidence when
                         below the threshold (trigger ④).
        tm_threshold:    Threshold for trigger ④ — must be derived from the
                         current run's tm_confidence distribution (measured_in_run),
                         not a hard-coded constant. When None, hitl_interrupt falls
                         back to TM_THRESHOLD env var then to _DEFAULT_TM_THRESHOLD.
                         Callers (build_translation_graph) should always pass the
                         run-time-derived value to satisfy AC4.

    Returns:
        List of violation dicts, each with at least:
          - ``guard``: str  — which guard fired
          - ``detail`` or ``matches``: structured reason data
        Empty list when all guards pass.
    """
    violations: list[dict] = []

    # Guard 1: injection_guard — projection: (en_source, draft_ko)
    # check_injection accepts ko_text as the draft_ko projection field.
    inj_result = check_injection(en_source, ko_text=draft_ko)
    if not inj_result.passed:
        violations.append({
            "guard": "injection",
            "matches": [m.model_dump() for m in inj_result.matches],
        })

    # Guards 2–5: fidelity, glossary, new_term, conflict — via HITL coordinator.
    # Projections consumed per guard (documented, not reimplemented):
    #   fidelity_guard:   (en_source, draft_ko)
    #   glossary_guard:   (draft_ko, injected_terms)  — injected_terms pre-filter
    #                     means glossary check covers exactly the injected terms
    #   new_term_guard:   (en_source, draft_ko, glossary)
    #   conflict_guard:   (draft_ko, injected_terms, conflicts) — injected EN
    #                     terms used to identify which sourced terms conflict
    hitl_result = check_hitl_triggers(
        en_source,
        draft_ko,
        glossary=flat_glossary,
        llm_judged=llm_judged,
        conflict_entries=conflict_entries,
        tm_top_score=tm_confidence,
        tm_threshold=tm_threshold,
    )
    for trigger in hitl_result.triggers:
        violations.append({
            "guard": trigger.reason.value,
            "detail": trigger.detail,
        })

    return violations


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class CardState(TypedDict, total=False):
    """Shared state threaded through every node of the translation graph."""

    card_id: str
    en_rule: str       # source EN rule text  (card["text"])
    en_flavor: str     # source EN flavor text (card["flavor"])
    text_type: Literal["rule", "flavor"]   # set by the routing node
    # AC2 fields: draft generation outputs — draft_record_contract (SERVICE.md §6)
    draft_ko: str                 # draft Korean translation
    tm_hits: list[dict]           # TM search results: [{id, en_text, score}]
    tm_confidence: float          # RRF score of top-1 TM hit (0.0 if no hits)
    injected_terms: list[dict]    # per-term provenance: [{en, ko, source, llm_judged}]
    glossary_llm_judged: bool     # whether extracted glossary terms are LLM-validated
    # AC3 fields: guardrail validation outputs
    guard_passed: bool               # True when all 5 guards pass
    guard_violations: list           # list of violation dicts from failed guards
    needs_review: bool               # True when card is routed to review queue
    # AC5 fields: post-approval storage outputs
    approved_ko: str                 # final approved Korean translation (post-interrupt)
    new_terms: list[dict]            # new_term_identity pairs: [{en, ko_rendering}] (AC5)
    new_terms_submitted: list[str]   # EN terms written to new_term_candidates.json


# ---------------------------------------------------------------------------
# Nodes — AC1 routing
# ---------------------------------------------------------------------------


def route_card(state: CardState) -> CardState:
    """Determine whether this card goes through the rule or flavor branch.

    Rule text takes priority: if ``en_rule`` is present and non-empty, the
    card is routed to the rule branch.  Otherwise it uses the flavor branch.
    """
    en_rule = (state.get("en_rule") or "").strip()
    text_type: Literal["rule", "flavor"] = "rule" if en_rule else "flavor"
    return {**state, "text_type": text_type}


# ---------------------------------------------------------------------------
# Routing function (conditional edge) — AC1
# ---------------------------------------------------------------------------


def _route_decision(state: CardState) -> Literal["process_rule", "process_flavor"]:
    """Return the next node name based on the text_type set by route_card."""
    if state.get("text_type") == "flavor":
        return "process_flavor"
    return "process_rule"


def expand_card_to_field_inputs(card: dict) -> list[CardState]:
    """Expand a card dict into one CardState per non-empty text field.

    AC1: The routing input unit is (card_id, field) pair, not card.
    A card with both en_text and en_flavor produces 2 CardState inputs with
    different routes. Each input isolates exactly one field so the conditional
    edge routes deterministically:
      - en_text non-empty → CardState with en_rule=text, en_flavor="" → rule route
      - en_flavor non-empty → CardState with en_rule="", en_flavor=flavor → flavor route
      - both non-empty → two CardStates in order [rule, flavor]
      - neither non-empty → empty list

    This function does NOT invoke the graph; it is the expansion step that the
    caller (e.g. run_pipeline.py) uses before invoking graph.invoke().
    """
    card_id = card.get("id", "")
    en_text = (card.get("en_text") or "").strip()
    en_flavor = (card.get("en_flavor") or "").strip()

    inputs: list[CardState] = []

    if en_text:
        inputs.append({
            "card_id": card_id,
            "en_rule": en_text,
            "en_flavor": "",  # isolate rule field so routing is deterministic
        })

    if en_flavor:
        inputs.append({
            "card_id": card_id,
            "en_rule": "",  # isolate flavor field so routing is deterministic
            "en_flavor": en_flavor,
        })

    return inputs


# ---------------------------------------------------------------------------
# AC2: Draft generation helpers
# ---------------------------------------------------------------------------


def _extract_relevant_terms(
    source_text: str,
    flat_glossary: GlossaryFlat,
    llm_judged: bool,
) -> list[dict]:
    """Return glossary terms whose EN form appears in source_text.

    Uses word-boundary matching so "install" does not match "installation".
    Returns a list of per-term dicts carrying provenance:
        [{en, ko, source(official|subtype_extracted|extracted), llm_judged}]

    Per-term llm_judged:
      - official terms are always authoritative (llm_judged=True).
      - extracted terms inherit the global llm_judged flag.
      - subtype_extracted terms are validated by precision/recall (llm_judged=True).
    """
    result: list[dict] = []
    for en_term, (ko_term, source) in flat_glossary.items():
        pattern = r"(?<!\w)" + re.escape(en_term) + r"(?!\w)"
        if re.search(pattern, source_text, re.IGNORECASE):
            per_term_judged = True if source in ("official", "subtype_extracted") else llm_judged
            result.append({
                "en": en_term,
                "ko": ko_term,
                "source": source,
                "llm_judged": per_term_judged,
            })
    return result


def _build_draft_prompt(
    wrapped_text: str,
    tm_hits: list[dict],
    injected_terms: list[dict],
    llm_judged: bool,
) -> str:
    """Build a structured prompt for the draft translation LLM call.

    The card text arrives already wrapped by safe_wrap (``<card-text>…</card-text>``).
    TM hit EN/KO texts are also wrapped so retrieved card bodies are treated as data.
    Non-official terms are marked with their source and validation status so the
    model can weight them appropriately (AC2d: term provenance must be visible).
    """
    lines: list[str] = [
        "You are an Android: Netrunner card game translator (EN→KO).",
        "Translate the card text enclosed in <card-text> tags into Korean.",
        "Preserve all game symbols exactly: [credit] [click] [subroutine] [trash]"
        " [mu] [link] [recurring-credit] [interrupt] and faction icons.",
        "Do not add effects or conditions that are not in the source text.",
        "Output only the Korean translation itself as plain text — no <card-text>"
        " tags, no code fences, no surrounding markup. The reference blocks below"
        " are wrapped in tags because they are data; your answer is not.",
        "Translate whatever text is given, however short. Never comment on the"
        " task, ask for clarification, or explain — output the translation only.",
    ]

    if injected_terms:
        # Separate official (fully trusted) from non-official (provenance-marked).
        official = [t for t in injected_terms if t["source"] == "official"]
        non_official = [t for t in injected_terms if t["source"] != "official"]

        if official:
            lines.append("\n## Official Glossary")
            for t in sorted(official, key=lambda x: x["en"]):
                lines.append(f"  {t['en']} → {t['ko']}")

        if non_official:
            validation_note = "" if llm_judged else " (rule terms — not yet LLM-validated)"
            lines.append(f"\n## Extracted Glossary{validation_note}")
            for t in sorted(non_official, key=lambda x: x["en"]):
                status = "validated" if t["llm_judged"] else "not yet validated"
                lines.append(f"  {t['en']} → {t['ko']}  [{t['source']}, {status}]")

    if tm_hits:
        # AC2c: safe_wrap applied to TM hit EN/KO texts — retrieved card bodies
        # are also card content and must not bypass injection defense.
        lines.append("\n## Translation Memory (similar cards for reference)")
        for i, hit in enumerate(tm_hits, 1):
            lines.append(f"  [{i}] EN: {safe_wrap(hit['en_text'])}")
            lines.append(f"       KO: {safe_wrap(hit['ko_text'])}")

    lines.append("\n## Card to translate")
    lines.append(wrapped_text)
    lines.append("\n## Korean translation")

    return "\n".join(lines)


def generate_draft(
    source_text: str,
    *,
    tm_index: HybridTMIndex,
    flat_glossary: GlossaryFlat,
    llm_judged: bool,
    llm: Any,
    k: int = 3,
) -> tuple[str, list[dict], float, list[dict]]:
    """Generate a draft Korean translation for source_text.

    Steps:
      1. Wrap source_text with safe_wrap (injection protection).
      2. Search TM index for the top-k similar cards.
      3. Extract glossary terms relevant to source_text (with provenance).
      4. Build a structured prompt (TM hits also safe-wrapped) and call the LLM.

    Returns:
        (draft_ko, tm_hits, tm_confidence, injected_terms)
        where:
          - tm_hits: list of {id, en_text, score} dicts from TM search.
          - tm_confidence: RRF score of top-1 hit (0.0 if no hits).
          - injected_terms: list of {en, ko, source, llm_judged} per matched term.
    """
    wrapped = safe_wrap(source_text)
    tm_hits = tm_index.search(source_text, k=k)
    tm_confidence = tm_hits[0]["score"] if tm_hits else 0.0
    injected_terms = _extract_relevant_terms(source_text, flat_glossary, llm_judged)

    prompt = _build_draft_prompt(wrapped, tm_hits, injected_terms, llm_judged)
    response = llm.invoke(prompt)
    # Support both BaseChatModel (response.content) and plain string returns.
    draft_ko = (
        response.content.strip()
        if hasattr(response, "content")
        else str(response).strip()
    )

    return draft_ko, tm_hits, tm_confidence, injected_terms


def _make_process_node(
    source_field: str,
    tm_index: HybridTMIndex,
    flat_glossary: GlossaryFlat,
    llm_judged: bool,
    llm: Any,
    inject_glossary: bool = True,
) -> Callable[[CardState], CardState]:
    """Return a LangGraph node function that generates a draft for *source_field*.

    Routing decision (AC2e) — both rule and flavor branches use the same
    flat_glossary injection when *inject_glossary* is True (default).
    Rationale: flavor text frequently references game concepts (e.g. "install",
    "run") that require consistent translation, so the same term-matching logic
    applies.  Callers may pass inject_glossary=False to disable injection for
    flavor text; the decision must be explicit rather than implicit.
    """
    _glossary = flat_glossary if inject_glossary else {}

    def process_node(state: CardState) -> CardState:
        source = (state.get(source_field) or "").strip()  # type: ignore[arg-type]
        if not source:
            return state
        draft, hits, confidence, terms = generate_draft(
            source,
            tm_index=tm_index,
            flat_glossary=_glossary,
            llm_judged=llm_judged,
            llm=llm,
        )
        return {
            **state,
            "draft_ko": draft,
            "tm_hits": hits,
            "tm_confidence": confidence,
            "injected_terms": terms,
            "glossary_llm_judged": llm_judged,
        }

    return process_node


# ---------------------------------------------------------------------------
# AC3: Guard validation helpers
# ---------------------------------------------------------------------------


def _make_validate_node(
    flat_glossary: GlossaryFlat,
    llm_judged: bool,
    conflict_entries: list[dict],
    tm_threshold: float | None = None,
) -> Callable[[CardState], CardState]:
    """Return a node that validates the draft with all 5 guardrails.

    Delegates to :func:`_apply_guard_projections` — the single adapter that
    maps DraftRecord field projections to each guard call (AC3).

    Guards called via the adapter (existing modules, not re-implemented):
      1. injection_guard   — projection: (en_source, draft_ko)
      2. new_term_guard    — projection: (en_source, draft_ko, glossary)
      3. conflict_guard    — projection: (draft_ko, injected_terms, conflicts)
      4. glossary_guard    — projection: (draft_ko, injected_terms)
      5. fidelity_guard    — projection: (en_source, draft_ko)

    Failed items set needs_review=True and are routed to review_queue.
    """

    def _validate_draft(state: CardState) -> CardState:
        text_type = state.get("text_type", "rule")
        source_text = (
            (state.get("en_rule") or "") if text_type == "rule"
            else (state.get("en_flavor") or "")
        ).strip()
        draft_ko = (state.get("draft_ko") or "").strip()

        # No draft to validate (stub mode or empty source)
        if not draft_ko:
            return {**state, "guard_passed": True, "guard_violations": [], "needs_review": False}

        # All 5 guards applied through the projection adapter.
        violations = _apply_guard_projections(
            en_source=source_text,
            draft_ko=draft_ko,
            injected_terms=state.get("injected_terms") or [],
            flat_glossary=flat_glossary,
            llm_judged=llm_judged,
            conflict_entries=conflict_entries,
            tm_confidence=state.get("tm_confidence"),
            tm_threshold=tm_threshold,
        )
        guard_passed = len(violations) == 0

        return {
            **state,
            "guard_passed": guard_passed,
            "guard_violations": violations,
            "needs_review": not guard_passed,
        }

    return _validate_draft


def _validate_route(state: CardState) -> str:
    """Route to review_queue when any guard failed; otherwise END."""
    if not state.get("guard_passed", True):
        return "review_queue"
    return END


def _make_review_queue_node(
    approved_store_path: Path | str = "approved.jsonl",
    new_term_candidates_path: Path | str = "new_term_candidates.json",
) -> Callable[[CardState], CardState]:
    """Return a review queue node with closure over store paths.

    AC4: Calls LangGraph interrupt() to pause for human review.
    AC5: After interrupt() returns (human approved), persists the decision:
      - append_approved → approved.jsonl
      - append_new_term_candidate → new_term_candidates.json (new_term violations only)
      - glossary.json is NOT modified; approval does not enforce terms on next cards.

    Human decision protocol (Command(resume=...)):
      - "approved"                     → approve draft as-is (modified=False)
      - {"approved_ko": "<new text>"}  → approve with modification (modified=True)
    """

    def _review_queue(state: CardState) -> CardState:
        from langgraph.types import interrupt

        text_type = state.get("text_type", "rule")
        source_text = (
            (state.get("en_rule") or "") if text_type == "rule"
            else (state.get("en_flavor") or "")
        ).strip()
        draft_ko = (state.get("draft_ko") or "").strip()

        human_decision = interrupt({
            "card_id": state.get("card_id"),
            "text_type": text_type,
            "source_text": source_text,
            "draft_ko": draft_ko,
            "violations": state.get("guard_violations", []),
        })

        # Resolve approved KO text from human decision.
        # Reviewer may also supply term_renderings: {en_term: ko_rendering} for new_term pairs.
        term_renderings: dict[str, str] = {}
        if isinstance(human_decision, dict):
            approved_ko = human_decision.get("approved_ko", draft_ko)
            term_renderings = human_decision.get("term_renderings", {})
        else:
            approved_ko = draft_ko  # plain "approved" string → use draft as-is
        modified = approved_ko != draft_ko

        # field/route provenance for the storage unit (card_id, field) + route (AC5).
        field_name = "text" if text_type == "rule" else "flavor"

        # Collect interrupt reasons for the record
        interrupt_reasons = [
            v.get("guard", "") for v in state.get("guard_violations", [])
        ]

        # Persist to approved store (approved.jsonl) — unit is (card_id, field) + route.
        record = ApprovedRecord(
            card_id=state.get("card_id", ""),
            field=field_name,
            route=text_type,
            en_text=source_text,
            ko_draft=draft_ko,
            approved_ko=approved_ko,
            modified=modified,
            interrupt_reasons=interrupt_reasons,
        )
        append_approved(record, store_path=approved_store_path)

        # Collect new_term_identity pairs — (en, ko_rendering) from new_term violations.
        # ko_rendering comes from reviewer-supplied term_renderings if available; else "".
        # These populate DraftRecord.new_terms (AC5) and are written to new_term_candidates.json.
        # glossary.json is NOT modified — phase-3 confirmation is required (AC5).
        new_terms: list[dict] = []
        new_terms_submitted: list[str] = []
        for v in state.get("guard_violations", []):
            if v.get("guard") == "new_term":
                for en_term in v.get("detail", {}).get("new_terms", []):
                    ko_rendering = term_renderings.get(en_term, "")
                    new_terms.append({"en": en_term, "ko_rendering": ko_rendering})
                    candidate = NewTermCandidateRecord(
                        en_term=en_term,
                        ko_rendering=ko_rendering,
                        source_card_id=state.get("card_id", ""),
                        source_field=field_name,
                        approved_ko_context=approved_ko,
                    )
                    append_new_term_candidate(candidate, store_path=new_term_candidates_path)
                    new_terms_submitted.append(en_term)

        return {
            **state,
            "approved_ko": approved_ko,
            "new_terms": new_terms,
            "new_terms_submitted": new_terms_submitted,
        }

    return _review_queue


# Module-level node (default paths) used by the stub translation_graph.
_review_queue = _make_review_queue_node()


# Stub nodes (no deps) kept for AC1 backward compatibility.
def process_rule(state: CardState) -> CardState:
    """Stub: returns state unchanged.  Use build_translation_graph(deps) for real draft."""
    return state


def process_flavor(state: CardState) -> CardState:
    """Stub: returns state unchanged.  Use build_translation_graph(deps) for real draft."""
    return state


def validate_draft(state: CardState) -> CardState:
    """Stub: passes through when no guard deps available."""
    return {**state, "guard_passed": True, "guard_violations": [], "needs_review": False}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_translation_graph(
    *,
    tm_index: HybridTMIndex | None = None,
    flat_glossary: GlossaryFlat | None = None,
    llm_judged: bool = False,
    llm: Any = None,
    conflict_entries: list[dict] | None = None,
    checkpointer: Any = None,
    approved_store_path: Path | str = "approved.jsonl",
    new_term_candidates_path: Path | str = "new_term_candidates.json",
    flavor_inject_glossary: bool = True,
    tm_threshold: float | None = None,
) -> StateGraph:
    """Build and return the compiled translation graph.

    When *tm_index*, *flat_glossary*, and *llm* are all provided the graph
    performs real draft generation (AC2).  When *flat_glossary* is provided
    the graph performs real guard validation (AC3).  Without them the
    corresponding nodes are stubs.

    Graph topology::

        route_card
            |
        [conditional edge: text_type]
          /              \\
    process_rule    process_flavor
          \\              /
         validate_draft
              |
        [conditional: guard_passed]
          /                  \\
        END            review_queue → END

    Args:
        tm_index:        Hybrid TM index for draft generation (AC2).
        flat_glossary:   Flat EN→KO glossary for term injection (AC2) and guard
                         validation (AC3).
        llm_judged:      Whether the extracted glossary section is LLM-validated.
        llm:             LLM callable for draft generation (AC2).
        conflict_entries: Conflict entries for conflict guard (AC3).  Pass None
                         to skip trigger ②.
        checkpointer:    LangGraph checkpointer (e.g. MemorySaver) required for
                         interrupt() to support resume via Command(resume=...).
                         Without one, interrupt() still fires but the graph
                         cannot be resumed in the same thread.
        approved_store_path:      Path to approved.jsonl (AC5).
        new_term_candidates_path: Path to new_term_candidates.json (AC5).
        flavor_inject_glossary:   Whether to inject rule glossary terms for flavor
                                  text (AC2e routing decision). Default True: flavor
                                  text can reference game terms that need consistent
                                  translation; pass False to disable injection for
                                  flavor cards explicitly.
        tm_threshold:    Threshold for trigger ④ (low_tm_confidence). Must be
                         derived from the current run's tm_confidence distribution
                         (measured_in_run, AC4). When None, hitl_interrupt falls
                         back to TM_THRESHOLD env var → _DEFAULT_TM_THRESHOLD.
                         Pipeline runners (run_pipeline.py, evaluate_pipeline.py)
                         must call derive_tm_confidence_threshold() first and pass
                         the result here. Passing None is only acceptable in tests
                         that control the threshold via TM_THRESHOLD env var.

    Returns:
        A compiled :class:`langgraph.graph.StateGraph` ready to invoke.
    """
    builder = StateGraph(CardState)

    builder.add_node("route_card", route_card)

    deps_ready = tm_index is not None and flat_glossary is not None and llm is not None
    if deps_ready:
        rule_node = _make_process_node("en_rule", tm_index, flat_glossary, llm_judged, llm, inject_glossary=True)  # type: ignore[arg-type]
        flavor_node = _make_process_node("en_flavor", tm_index, flat_glossary, llm_judged, llm, inject_glossary=flavor_inject_glossary)  # type: ignore[arg-type]
    else:
        rule_node = process_rule
        flavor_node = process_flavor

    builder.add_node("process_rule", rule_node)
    builder.add_node("process_flavor", flavor_node)

    # AC3: guard validation node
    if flat_glossary is not None:
        validate_node = _make_validate_node(
            flat_glossary, llm_judged, conflict_entries or [], tm_threshold=tm_threshold
        )
    else:
        validate_node = validate_draft  # stub

    builder.add_node("validate_draft", validate_node)

    # AC5: review queue node with store paths injected via closure
    review_queue_node = _make_review_queue_node(approved_store_path, new_term_candidates_path)
    builder.add_node("review_queue", review_queue_node)

    builder.set_entry_point("route_card")

    builder.add_conditional_edges(
        "route_card",
        _route_decision,
        {
            "process_rule": "process_rule",
            "process_flavor": "process_flavor",
        },
    )

    builder.add_edge("process_rule", "validate_draft")
    builder.add_edge("process_flavor", "validate_draft")

    builder.add_conditional_edges(
        "validate_draft",
        _validate_route,
        {"review_queue": "review_queue", END: END},
    )

    builder.add_edge("review_queue", END)

    return builder.compile(checkpointer=checkpointer)


# Module-level compiled graph (stub mode) for import convenience.
translation_graph = build_translation_graph()
