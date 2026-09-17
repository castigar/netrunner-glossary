"""gate1_term_compliance.py — Hard Gate 1: term compliance rate >= 95%.

SERVICE.md §5, gate 1:
  For each card in the hold-out set, every glossary EN term that appears in
  en_text must be translated with its registered KO equivalent in ko_text.
  Term-level compliance rate across all 100 hold-out cards must be >= 95%.
  Below threshold →납품 거부 (delivery rejected).

Compliance is measured at the term-occurrence level:
  rate = compliant_occurrences / total_term_occurrences

where a "term occurrence" is a (card, glossary_en_term) pair where the EN term
appears in the card's en_text, and it is "compliant" when the registered KO
term is present somewhere in ko_text.

Cards where no glossary term appears in en_text contribute 0 to both numerator
and denominator (they neither help nor hurt the rate).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from glossary_guard import GlossaryFlat, check_glossary_compliance, load_flat_glossary

THRESHOLD = 0.95


@dataclass
class CardCompliance:
    """Compliance result for a single hold-out card."""

    card_id: str
    term_checks: int  # EN terms present in en_text
    violations: int   # registered KO terms missing from ko_text
    llm_judged: bool


@dataclass
class Gate1Result:
    """Aggregated Hard Gate 1 result over all hold-out cards."""

    passed: bool
    compliance_rate: float  # compliant / total, or 1.0 when total == 0
    total_checks: int       # sum of term_checks across all cards
    total_violations: int   # sum of violations across all cards
    llm_judged: bool        # glossary.llm_judged propagated from load
    card_results: list[CardCompliance] = field(default_factory=list)
    threshold: float = THRESHOLD


def score_hold_out(
    hold_out_path: str | Path,
    glossary_path: str | Path,
) -> Gate1Result:
    """Score Hard Gate 1 over the hold-out set.

    Args:
        hold_out_path: Path to data/hold_out.json (list of {id, en_text, ko_text}).
        glossary_path: Path to assets/glossary.json.

    Returns:
        :class:`Gate1Result` with ``passed=True`` iff compliance_rate >= 0.95.
    """
    glossary, llm_judged = load_flat_glossary(glossary_path)
    cards: list[dict] = json.loads(Path(hold_out_path).read_text(encoding="utf-8"))

    card_results: list[CardCompliance] = []
    total_checks = 0
    total_violations = 0

    for card in cards:
        card_id = card.get("id", "")
        en_text = card.get("en_text", "")
        ko_text = card.get("ko_text", "")

        result = check_glossary_compliance(en_text, ko_text, glossary, llm_judged)

        # count how many EN terms were checked (present in en_text)
        checks = _count_term_checks(en_text, glossary)
        viol = len(result.violations)

        card_results.append(
            CardCompliance(
                card_id=card_id,
                term_checks=checks,
                violations=viol,
                llm_judged=llm_judged,
            )
        )
        total_checks += checks
        total_violations += viol

    if total_checks == 0:
        compliance_rate = 1.0
    else:
        compliance_rate = (total_checks - total_violations) / total_checks

    return Gate1Result(
        passed=compliance_rate >= THRESHOLD,
        compliance_rate=compliance_rate,
        total_checks=total_checks,
        total_violations=total_violations,
        llm_judged=llm_judged,
        card_results=card_results,
        threshold=THRESHOLD,
    )


def _count_term_checks(en_text: str, glossary: GlossaryFlat) -> int:
    """Count how many glossary EN terms appear in *en_text* (term-occurrence count)."""
    import re

    count = 0
    for en_term in glossary:
        pattern = r"(?<!\w)" + re.escape(en_term) + r"(?!\w)"
        if re.search(pattern, en_text, re.IGNORECASE):
            count += 1
    return count
