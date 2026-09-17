"""injection_guard.py — Guardrail §4.5: Prompt injection defense.

SERVICE.md §4, rule 5:
  Card text is treated as data to translate, never as instructions to the LLM.
  Detected injection patterns are blocked; the caller triggers interrupt().

Two-layer defense:
  1. Pattern check — regex over known injection signals in card text (EN + KO).
  2. Structural sandboxing — safe_wrap() surrounds card text in XML delimiters
     so the model knows it is receiving game data, not commands.

Usage::

    result = check_injection(card_text)
    if not result.passed:
        raise InjectionError(result)

    prompt = safe_wrap(card_text)   # use the wrapped text in any LLM prompt
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Injection detection patterns (EN + KO)
# ---------------------------------------------------------------------------

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # English — role takeover / instruction override
        r"ignore\s+(the\s+|all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)",
        r"disregard\s+(the\s+|all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)",
        r"forget\s+(your|all|the|previous|prior)\s+(role|instructions?|rules?|context)",
        r"you\s+are\s+now\s+a?\s*(different|new|unrestricted)",
        r"act\s+as\s+if\s+(you\s+have\s+no\s+restrictions?|you\s+are\s+not\s+bound)",
        r"(new\s+)?system\s*:\s*you",
        # XML / delimiter injection
        r"</?\s*(system|admin|root|assistant|user)\s*>",
        r"\[\s*(system|admin|root|assistant|user)\s*\]",
        # Prompt extraction
        r"(reveal|print|show|output|repeat)\s+(your\s+)?(system|initial|original)\s+(prompt|instructions?)",
        # Korean — instruction override (from reference guards_input.py)
        r"(위의?|이전|기존|지금까지)\s*(모든\s*)?(지시|명령|규칙|프롬프트)[은는를]?\s*.{0,8}(무시|잊어|버려)",
        r"규칙\s*(이|가)\s*없는\s*(AI|인공지능|모드)",
        r"개발자\s*모드",
        r"지시문?\s*무시",
    ]
]

_XML_OPEN = "<card-text>"
_XML_CLOSE = "</card-text>"

# Escape sequences for any accidental delimiters in card text
_OPEN_ESCAPED = "&lt;card-text&gt;"
_CLOSE_ESCAPED = "&lt;/card-text&gt;"


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class InjectionMatch(BaseModel):
    """A single matched injection pattern."""

    pattern: str = Field(description="The regex pattern that fired")
    matched_text: str = Field(description="The substring that matched")
    side: str = Field(
        default="source",
        description=(
            "Which text fired: 'source' for the card text, 'draft_ko' for the "
            "generated translation. A reviewer needs this — an injection in the "
            "source is upstream data, one in the draft is the model's own output."
        ),
    )


class InjectionCheckResult(BaseModel):
    """Result of the prompt injection guardrail check."""

    passed: bool = Field(description="True only when no injection patterns were detected")
    matches: list[InjectionMatch] = Field(default_factory=list)


class InjectionError(Exception):
    """Raised by callers to signal that the card text must be blocked.

    Carry the full InjectionCheckResult so the HITL interrupt handler can
    surface the exact matches to the reviewer.
    """

    def __init__(self, result: InjectionCheckResult) -> None:
        self.result = result
        snippets = "; ".join(m.matched_text[:40] for m in result.matches[:3])
        super().__init__(
            f"{len(result.matches)} injection pattern(s) detected: {snippets}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_injection(text: str, ko_text: str | None = None) -> InjectionCheckResult:
    """Scan *text* — and optionally a KO draft — for prompt injection patterns.

    Card text is game data and must never be executed as instructions by an
    LLM.  This function detects common injection signals so the caller can
    block the text and route it to the review queue via interrupt().

    Args:
        text: Raw source card text before it reaches any LLM prompt.
        ko_text: Optional KO draft translation for the same card.  A draft can
            carry an injection the source did not — the model wrote it — so a
            guard that only reads the source cannot see it.  Callers holding
            both sides pass both; ``side`` on each match says which one fired.

    Returns:
        :class:`InjectionCheckResult` — ``passed=True`` when no patterns fire.
    """
    matches: list[InjectionMatch] = []
    for side, candidate in (("source", text), ("draft_ko", ko_text)):
        if not candidate:
            continue
        for pattern in _PATTERNS:
            m = pattern.search(candidate)
            if m:
                matches.append(
                    InjectionMatch(
                        pattern=pattern.pattern,
                        matched_text=m.group(0),
                        side=side,
                    )
                )
    return InjectionCheckResult(passed=len(matches) == 0, matches=matches)


def safe_wrap(text: str) -> str:
    """Wrap *text* in XML delimiters so LLMs treat it as data, not instructions.

    Any occurrence of the delimiter tags inside *text* is XML-escaped so the
    model cannot break out of the sandbox via nested tags.

    Args:
        text: Card text to sandbox.

    Returns:
        A string of the form ``<card-text>…</card-text>`` safe for inclusion
        in any LLM prompt that references these delimiters.
    """
    sandboxed = text.replace(_XML_OPEN, _OPEN_ESCAPED).replace(_XML_CLOSE, _CLOSE_ESCAPED)
    return f"{_XML_OPEN}\n{sandboxed}\n{_XML_CLOSE}"
