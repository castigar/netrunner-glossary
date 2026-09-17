"""gate2_symbol_preservation.py — Hard Gate 2: game symbol preservation rate 100%.

SERVICE.md §5, gate 2:
  For each card in the hold-out set, game symbols in the KO text must exactly
  correspond to those in the EN text after applying the normalization mapping.

Normalization mapping (정규화 대응표):
  - EN Trace[N] ≡ KO <trace>추적 N</trace>  (equivalence rule, NOT literal)
  - [credit] [click] [subroutine] [trash] [mu] [link] [recurring-credit]
    [interrupt] and faction icons are 1:1 preserved
  - <strong> <em> <ul> <li> markup tags are excluded from the gate

Gate passes only when preservation_rate == 1.0 (every card passes).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Markup tags whose presence/absence is NOT a gate violation
_EXCLUDED_TAGS = frozenset({"strong", "em", "ul", "li"})

# Threshold: every card must preserve symbols
THRESHOLD = 1.0


@dataclass
class CardSymbolResult:
    """Symbol preservation result for a single hold-out card."""

    card_id: str
    passed: bool
    en_symbols: dict[str, int]   # canonical symbol counts from EN text
    ko_symbols: dict[str, int]   # canonical symbol counts from KO text
    missing: dict[str, int]      # symbols in EN with fewer occurrences in KO
    extra: dict[str, int]        # symbols in KO with more occurrences than EN


@dataclass
class Gate2Result:
    """Aggregated Hard Gate 2 result over all hold-out cards."""

    passed: bool
    preservation_rate: float     # cards_passed / cards_total
    cards_passed: int
    cards_total: int
    card_results: list[CardSymbolResult] = field(default_factory=list)
    threshold: float = THRESHOLD


def _strip_excluded_tags(text: str) -> str:
    """Remove opening/closing markup tags excluded from gate checking."""
    pattern = "|".join(rf"</?{tag}>" for tag in _EXCLUDED_TAGS)
    return re.sub(pattern, "", text)


def _canonical_symbols_en(text: str) -> Counter:
    """Extract normalized symbol tokens from English card text.

    Normalization rules:
    - Trace[N] → "trace_N"  (uppercase N)
    - [symbol_name] → "symbol_name"  (lowercase)
    - Excluded markup tags are stripped before extraction.
    """
    text = _strip_excluded_tags(text)
    tokens: Counter = Counter()

    # Normalize Trace[N] or Trace[X] → trace_<VALUE>
    for m in re.finditer(r"\bTrace\[(\w+)\]", text, re.IGNORECASE):
        tokens[f"trace_{m.group(1).upper()}"] += 1

    # Remove Trace[...] so its bracketed value is not re-extracted as a symbol
    remaining = re.sub(r"\bTrace\[\w+\]", "", text, flags=re.IGNORECASE)

    # Extract [symbol_name] tokens — first char must be a letter (not a digit)
    for m in re.finditer(r"\[([a-z][a-z0-9-]*)\]", remaining, re.IGNORECASE):
        tokens[m.group(1).lower()] += 1

    return tokens


def _canonical_symbols_ko(text: str) -> Counter:
    """Extract normalized symbol tokens from Korean card text.

    Normalization rules:
    - <trace>추적 N</trace> → "trace_N"  (uppercase N)
    - [symbol_name] → "symbol_name"  (lowercase)
    - Excluded markup tags are stripped before extraction.
    """
    text = _strip_excluded_tags(text)
    tokens: Counter = Counter()

    # Normalize <trace>추적 N</trace> → trace_<VALUE>
    for m in re.finditer(r"<trace>추적\s+(\w+)</trace>", text):
        tokens[f"trace_{m.group(1).upper()}"] += 1

    # Remove <trace>...</trace> so its content is not re-extracted
    remaining = re.sub(r"<trace>추적\s+\w+</trace>", "", text)

    # Extract [symbol_name] tokens
    for m in re.finditer(r"\[([a-z][a-z0-9-]*)\]", remaining, re.IGNORECASE):
        tokens[m.group(1).lower()] += 1

    return tokens


def _check_card(card_id: str, en_text: str, ko_text: str) -> CardSymbolResult:
    """Check symbol preservation for a single card."""
    en_syms = _canonical_symbols_en(en_text)
    ko_syms = _canonical_symbols_ko(ko_text)

    missing: Counter = Counter()
    extra: Counter = Counter()
    for sym in set(en_syms) | set(ko_syms):
        en_count = en_syms.get(sym, 0)
        ko_count = ko_syms.get(sym, 0)
        if ko_count < en_count:
            missing[sym] = en_count - ko_count
        elif ko_count > en_count:
            extra[sym] = ko_count - en_count

    return CardSymbolResult(
        card_id=card_id,
        passed=not missing and not extra,
        en_symbols=dict(en_syms),
        ko_symbols=dict(ko_syms),
        missing=dict(missing),
        extra=dict(extra),
    )


def score_hold_out(
    hold_out_path: str | Path,
    predictions: list[str] | None = None,
) -> Gate2Result:
    """Score Hard Gate 2 over the hold-out set.

    Args:
        hold_out_path: Path to data/hold_out.json (list of {id, en_text, ko_text}).
        predictions: Agent-generated KO translations, one per card in hold_out
            order; each is scored instead of ``card['ko_text']``.  This is what
            the gate is for — it decides whether *agent* output ships, and it is
            how the pipeline's output reaches the gate (gate_input_contract AC6).
            When None the official ``ko_text`` is scored instead, which measures
            the corpus, not the agent; that mode is a diagnostic
            (see :func:`score_reference`), not a delivery decision.

    Returns:
        :class:`Gate2Result` with ``passed=True`` iff all cards preserve symbols.

    Raises:
        ValueError: if *predictions* is given and its length differs from the
            hold-out length, the same guard gate 3 applies.
    """
    cards: list[dict] = json.loads(Path(hold_out_path).read_text(encoding="utf-8"))

    if predictions is not None and len(predictions) != len(cards):
        raise ValueError(
            f"predictions length {len(predictions)} != hold-out length {len(cards)}"
        )

    card_results: list[CardSymbolResult] = []
    cards_passed = 0

    for i, card in enumerate(cards):
        ko_text = predictions[i] if predictions is not None else card.get("ko_text", "")
        result = _check_card(
            card_id=card.get("id", ""),
            en_text=card.get("en_text", ""),
            ko_text=ko_text,
        )
        card_results.append(result)
        if result.passed:
            cards_passed += 1

    total = len(cards)
    rate = cards_passed / total if total > 0 else 1.0

    return Gate2Result(
        passed=rate >= THRESHOLD,
        preservation_rate=rate,
        cards_passed=cards_passed,
        cards_total=total,
        card_results=card_results,
    )


def score_reference(hold_out_path: str | Path) -> Gate2Result:
    """Score the official KO translations instead of agent output.

    A diagnostic, not a delivery gate: it measures the corpus.  It is useful
    because the answer is known to be below 1.0 — muresh_bodysuit,
    sacrificial_construct and disrupter drop [interrupt] in the official KO — so
    it reveals the ceiling any agent is graded against, and it makes the
    difference between "the agent lost a symbol" and "the reference never had
    one" visible instead of silent.
    """
    return score_hold_out(hold_out_path, predictions=None)
