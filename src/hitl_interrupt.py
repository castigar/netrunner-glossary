"""hitl_interrupt.py — Unified HITL interrupt coordinator (SERVICE.md §6, Pattern 7).

HITL interrupt 트리거 4가지:
  ① 신규 EN 용어 발견   — new_term_guard.check_new_terms()
  ② 용어 충돌           — conflict_guard.check_conflicts()
  ③ 규칙 검증 실패      — glossary_guard + fidelity_guard
  ④ TM 최고 유사도 임계 미만

All four triggers are checked by :func:`check_hitl_triggers`.  When any fires,
``should_interrupt`` is True and the caller raises :class:`HITLInterrupt` to
route the card to the human review queue.

Usage::

    result = check_hitl_triggers(
        en_text, ko_text,
        glossary=flat, llm_judged=llm_judged,
        conflict_entries=conflicts_data,
        tm_top_score=0.005,
    )
    if result.should_interrupt:
        raise HITLInterrupt(result)
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Default TM confidence threshold.  The RRF score from HybridTMIndex peaks at
# roughly 3 × 1/(60+1) ≈ 0.049 for a 3-signal fusion.  A score below this
# default suggests the TM found no meaningful neighbour for the query.
# Override via the TM_THRESHOLD environment variable or the tm_threshold param.
_DEFAULT_TM_THRESHOLD: float = 0.01


class InterruptReason(str, Enum):
    NEW_TERM = "new_term"                  # ① 신규 EN 용어 발견
    TERM_CONFLICT = "term_conflict"        # ② 용어 충돌
    RULE_VIOLATION = "rule_violation"      # ③ 규칙 검증 실패
    LOW_TM_CONFIDENCE = "low_tm_confidence"  # ④ TM 유사도 임계 미만


class HITLTrigger(BaseModel):
    """One fired HITL trigger with its structured detail."""

    reason: InterruptReason
    detail: dict[str, Any] = Field(default_factory=dict)


class HITLInterruptResult(BaseModel):
    """Aggregated result of running all 4 HITL trigger checks."""

    should_interrupt: bool = Field(
        description="True when at least one trigger fired and human review is required"
    )
    triggers: list[HITLTrigger] = Field(
        default_factory=list,
        description="The triggers that fired; empty when should_interrupt is False",
    )
    recording_new_terms: list[str] = Field(
        default_factory=list,
        description=(
            "EN terms classified as recording-type (unregistered but not blocking). "
            "Callers must store these to new_term_candidates.json at detection time "
            "without interrupting the pipeline (AC4 new_term_blocking_boundary)."
        ),
    )


class HITLInterrupt(Exception):
    """Raised to route a card to the human review queue.

    Carries the full :class:`HITLInterruptResult` so the queue handler can
    surface the exact trigger reasons and details to the reviewer.
    """

    def __init__(self, result: HITLInterruptResult) -> None:
        self.result = result
        reasons = [t.reason.value for t in result.triggers]
        super().__init__(f"HITL interrupt required: {', '.join(reasons)}")


def check_hitl_triggers(
    en_text: str,
    ko_text: str,
    *,
    glossary: dict,
    llm_judged: bool,
    conflict_entries: list[dict] | None = None,
    tm_top_score: float | None = None,
    tm_threshold: float | None = None,
    en_keywords: list[str] | None = None,
    route: str = "rule",
) -> HITLInterruptResult:
    """Run all four HITL trigger checks and report which ones fired.

    Each trigger is independent; multiple can fire simultaneously.

    Trigger ① (new_term) fires ONLY for blocking-type new terms (AC4):
      (a) Items in en_keywords (card subtypes) not in the glossary.
      (b) Non-sentence-first, uppercase-starting, unregistered tokens in rule text.
    Recording-type new terms (all others) are returned in recording_new_terms
    for callers to store at detection time — they do NOT trigger interrupt.

    Args:
        en_text:          Source English card text (rule or flavour).
        ko_text:          Draft Korean translation to validate.
        glossary:         Flat glossary from :func:`glossary_guard.load_flat_glossary`.
        llm_judged:       Whether the extracted glossary section has been LLM-validated.
        conflict_entries: Conflict entries from conflicts.json.  Pass None to skip
                          trigger ②.
        tm_top_score:     The highest RRF score returned by the TM search for this
                          card.  Pass None to skip trigger ④.
        tm_threshold:     Score below which TM confidence is considered too low.
                          Defaults to the TM_THRESHOLD env var, then to 0.01.
                          Not a hardcoded magic constant — callers and operators
                          set this based on observed score distributions.
        en_keywords:      Card subtype keywords (e.g. ["Icebreaker", "Barrier"]).
                          Passed to new_term_guard for blocking type (a) detection.
        route:            "rule" or "flavor"; passed to new_term_guard for type (b).

    Returns:
        :class:`HITLInterruptResult` — ``should_interrupt=True`` if any trigger fired,
        plus ``recording_new_terms`` for non-blocking unregistered terms.
    """
    from conflict_guard import check_conflicts
    from fidelity_guard import check_fidelity
    from glossary_guard import check_glossary_compliance
    from new_term_guard import check_new_terms

    if tm_threshold is None:
        tm_threshold = float(os.environ.get("TM_THRESHOLD", _DEFAULT_TM_THRESHOLD))

    triggers: list[HITLTrigger] = []

    # ① 신규 EN 용어 발견 — only BLOCKING terms trigger interrupt (AC4).
    #   Recording terms are returned separately for callers to store at detection time.
    new_term_result = check_new_terms(
        en_text, glossary, llm_judged, en_keywords=en_keywords, route=route
    )
    if new_term_result.blocking_new_terms:
        triggers.append(
            HITLTrigger(
                reason=InterruptReason.NEW_TERM,
                detail={"new_terms": [t.en_term for t in new_term_result.blocking_new_terms]},
            )
        )
    recording_terms = [t.en_term for t in new_term_result.recording_new_terms]

    # ② 용어 충돌
    if conflict_entries is not None:
        conflict_result = check_conflicts(en_text, conflict_entries)
        if not conflict_result.passed:
            triggers.append(
                HITLTrigger(
                    reason=InterruptReason.TERM_CONFLICT,
                    detail={
                        "conflicts": [
                            {"en_term": v.en_term, "ko_variants": v.ko_variants}
                            for v in conflict_result.violations
                        ]
                    },
                )
            )

    # ③ 규칙 검증 실패 (glossary compliance + fidelity)
    glossary_result = check_glossary_compliance(en_text, ko_text, glossary, llm_judged)
    fidelity_result = check_fidelity(en_text, ko_text)
    if not glossary_result.passed or not fidelity_result.passed:
        triggers.append(
            HITLTrigger(
                reason=InterruptReason.RULE_VIOLATION,
                detail={
                    "glossary_violations": [
                        v.model_dump() for v in glossary_result.violations
                    ],
                    "fidelity_violations": [
                        v.model_dump() for v in fidelity_result.violations
                    ],
                },
            )
        )

    # ④ TM 최고 유사도 임계 미만
    if tm_top_score is not None and tm_top_score < tm_threshold:
        triggers.append(
            HITLTrigger(
                reason=InterruptReason.LOW_TM_CONFIDENCE,
                detail={"tm_top_score": tm_top_score, "threshold": tm_threshold},
            )
        )

    return HITLInterruptResult(
        should_interrupt=len(triggers) > 0,
        triggers=triggers,
        recording_new_terms=recording_terms,
    )
