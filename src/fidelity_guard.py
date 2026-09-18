"""fidelity_guard.py — Guardrail §4.4: Rule text semantic preservation.

SERVICE.md §4, rule 4:
  Do not add effects/conditions absent from the source EN text.
  Do not omit effects/conditions present in the source EN text.

  Violations are reported; callers trigger interrupt() to route the pair
  to the human review queue.

Check strategy (structural heuristics, no LLM per card):
  1. Numeric values  — magnitude of effects (gain 2, deal 1, draw 3 …)
  2. Game symbol counts — [credit]/[click]/[subroutine] occurrences
  3. Conditional keyword presence — if/when/unless/after/before … structures

All three proxy for "an effect or condition was added or dropped."
"""
from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Internal patterns
# ---------------------------------------------------------------------------

# Digit sequences (Korean counters like "1장", "3개" follow digits without space,
# so a simple r'\d+' is more reliable than word-boundary anchors across both scripts)
_NUM_RE = re.compile(r'\d+')

# Game symbols in bracket notation: [credit], [click], [subroutine], etc.
_SYMBOL_RE = re.compile(r'\[\w[\w-]*\]')

# EN trigger / conditional keywords
_EN_COND_RE = re.compile(
    r'\b(if|when|whenever|unless|after|before|during|instead|until|while)\b',
    re.IGNORECASE,
)

# KO conditional / temporal markers
_KO_COND_RE = re.compile(
    r'(이면|면|때|때마다|않으면|않는\s*한|후에|뒤에|전에|동안|중에|대신|까지|한다면|하면)',
)


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class FidelityViolation(BaseModel):
    """One structural discrepancy between the EN source and KO translation."""

    kind: str = Field(
        description=(
            "omitted_number | added_number | symbol_mismatch | omitted_condition"
        )
    )
    detail: str = Field(description="Human-readable description of the discrepancy")


class FidelityCheckResult(BaseModel):
    """Result of the fidelity guardrail check."""

    passed: bool = Field(description="True only when no violations detected")
    violations: list[FidelityViolation] = Field(default_factory=list)


class FidelityViolationError(Exception):
    """Raised by callers to signal that this translation needs human review.

    Carries the full :class:`FidelityCheckResult` so the HITL interrupt
    handler can surface the exact discrepancies to the reviewer.
    """

    def __init__(self, result: FidelityCheckResult) -> None:
        self.result = result
        kinds = sorted({v.kind for v in result.violations})
        super().__init__(
            f"{len(result.violations)} fidelity violation(s): {', '.join(kinds)}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _numbers(text: str) -> Counter:
    return Counter(int(m) for m in _NUM_RE.findall(text))


def _symbols(text: str) -> Counter:
    return Counter(_SYMBOL_RE.findall(text))


def _en_cond_count(text: str) -> int:
    return len(_EN_COND_RE.findall(text))


def _has_ko_cond(text: str) -> bool:
    return bool(_KO_COND_RE.search(text))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_fidelity(en_text: str, ko_text: str) -> FidelityCheckResult:
    """Check that *ko_text* preserves the effects and conditions of *en_text*.

    Three structural proxies are evaluated:

    1. **Numeric values** — each integer in EN must appear the same number of
       times in KO; extra or missing occurrences indicate an added or dropped
       numeric effect (cost, damage, card count, etc.).
    2. **Game symbol counts** — each bracket symbol ([credit], [click], …)
       must appear the same number of times in both texts; a mismatch means
       a game mechanic cost or reward was added or dropped.
    3. **Conditional structure presence** — if EN has conditional keywords
       (if / when / whenever / unless / …), KO must contain at least one
       conditional marker (면 / 때 / 않으면 / …).  A missing marker suggests
       a condition was omitted entirely.

    Args:
        en_text: Source English card text (``text`` / rule field).
        ko_text: Draft Korean translation to validate.

    Returns:
        :class:`FidelityCheckResult` — ``passed=True`` only when no violations.
    """
    violations: list[FidelityViolation] = []

    # 1. Number preservation
    en_nums = _numbers(en_text)
    ko_nums = _numbers(ko_text)
    for n in sorted(set(en_nums) | set(ko_nums)):
        en_c = en_nums.get(n, 0)
        ko_c = ko_nums.get(n, 0)
        if en_c > ko_c:
            violations.append(FidelityViolation(
                kind="omitted_number",
                detail=f"{n}: appears {en_c}× in EN but {ko_c}× in KO",
            ))
        elif ko_c > en_c:
            violations.append(FidelityViolation(
                kind="added_number",
                detail=f"{n}: appears {ko_c}× in KO but only {en_c}× in EN",
            ))

    # 2. Game symbol count preservation
    en_syms = _symbols(en_text)
    ko_syms = _symbols(ko_text)
    for sym in sorted(set(en_syms) | set(ko_syms)):
        en_c = en_syms.get(sym, 0)
        ko_c = ko_syms.get(sym, 0)
        if en_c != ko_c:
            violations.append(FidelityViolation(
                kind="symbol_mismatch",
                detail=f"{sym}: {en_c}× in EN, {ko_c}× in KO",
            ))

    # 3. Conditional presence
    en_cond = _en_cond_count(en_text)
    if en_cond > 0 and not _has_ko_cond(ko_text):
        violations.append(FidelityViolation(
            kind="omitted_condition",
            detail=(
                f"EN has {en_cond} conditional keyword(s) "
                f"but KO has no conditional marker"
            ),
        ))

    return FidelityCheckResult(passed=len(violations) == 0, violations=violations)
