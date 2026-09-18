"""conflict_guard.py — Guardrail: Term conflict HITL trigger.

HITL trigger ②: 용어 충돌(1 EN → 2개 이상 KO).
When the source EN text contains a term that has multiple registered KO
translations (unresolved conflict in conflicts.json), the translation is
blocked and routed to HITL for human resolution.

Usage::

    conflicts = load_conflict_entries(path)
    result = check_conflicts(en_text, conflicts)
    if not result.passed:
        raise ConflictError(result)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field


class ConflictViolation(BaseModel):
    """An unresolved term conflict found in the source EN text."""

    en_term: str = Field(description="The conflicted EN term found in the source text")
    ko_variants: list[str] = Field(description="The competing registered KO translations")


class ConflictCheckResult(BaseModel):
    """Result of the conflict guardrail check."""

    passed: bool = Field(description="True only when no conflicted terms appear in source")
    violations: list[ConflictViolation] = Field(default_factory=list)


class ConflictError(Exception):
    """Raised by callers to signal that HITL resolution is required.

    Carries the full ConflictCheckResult so the interrupt handler can
    surface the exact conflicts to the reviewer.
    """

    def __init__(self, result: ConflictCheckResult) -> None:
        self.result = result
        n = len(result.violations)
        terms = ", ".join(v.en_term for v in result.violations[:3])
        suffix = " …" if n > 3 else ""
        super().__init__(
            f"{n} conflicted term(s) require HITL resolution: {terms}{suffix}"
        )


def load_conflict_entries(conflicts_path: str | Path) -> list[dict]:
    """Load conflict entries from conflicts.json.

    Returns a list of dicts, each with at least:
      - 'en_term': str
      - 'ko_variants': list[str]
    """
    path = Path(conflicts_path)
    return json.loads(path.read_text(encoding="utf-8"))


def check_conflicts(
    en_text: str,
    conflicts: list[dict],
) -> ConflictCheckResult:
    """Check if any EN term in *en_text* is a known unresolved conflict.

    For every entry in *conflicts* that has 2+ KO variants, checks whether
    that EN term appears as a standalone word or phrase in *en_text*.  If so,
    the term is flagged: human resolution is required before translation can
    proceed.

    Args:
        en_text:   Source English card text.
        conflicts: List of conflict entries (from conflicts.json or in-memory).
                   Each entry must have 'en_term' and 'ko_variants' fields.

    Returns:
        ConflictCheckResult — passed=True only when no conflicted terms found.
    """
    violations: list[ConflictViolation] = []
    seen: set[str] = set()

    for entry in conflicts:
        en_term = entry.get("en_term", "")
        ko_variants = entry.get("ko_variants", [])

        if not en_term or len(ko_variants) < 2:
            continue

        en_norm = en_term.lower()
        if en_norm in seen:
            continue

        pattern = r"(?<!\w)" + re.escape(en_norm) + r"(?!\w)"
        if re.search(pattern, en_text, re.IGNORECASE):
            seen.add(en_norm)
            violations.append(
                ConflictViolation(en_term=en_term, ko_variants=list(ko_variants))
            )

    return ConflictCheckResult(passed=len(violations) == 0, violations=violations)
