"""glossary_guard.py — Guardrail §4.2: Glossary compliance check.

SERVICE.md §4, rule 2:
  Registered EN terms must be translated with their registered KO equivalents.
  Violations are blocked; the caller triggers interrupt() to send the pair to
  the review queue.

Usage::

    glossary, llm_judged = load_flat_glossary(path)
    result = check_glossary_compliance(en_text, ko_text, glossary, llm_judged)
    if not result.passed:
        # Caller invokes interrupt() with result.violations as the payload.
        raise GlossaryViolationError(result)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

# Maps normalized EN term -> (KO translation, glossary section name)
GlossaryFlat = dict[str, tuple[str, str]]

_GLOSSARY_SECTIONS = ("official", "subtype_extracted", "extracted")


class GlossaryViolation(BaseModel):
    """A single registered term whose KO equivalent is absent from the translation."""

    en_term: str = Field(description="The EN term found in the source text")
    expected_ko: str = Field(description="The registered KO translation")
    source: str = Field(
        description="Glossary section: official | subtype_extracted | extracted"
    )


class GlossaryCheckResult(BaseModel):
    """Result of the glossary guardrail check."""

    passed: bool = Field(description="True only when no violations were found")
    violations: list[GlossaryViolation] = Field(default_factory=list)
    llm_judged: bool = Field(
        description=(
            "Whether the 'extracted' section has been LLM-validated. "
            "False means rule terms are not yet confirmed; surface this to callers."
        )
    )


class GlossaryViolationError(Exception):
    """Raised by a caller to signal that the translation must be blocked.

    Carry the full GlossaryCheckResult so the HITL interrupt handler can
    surface the exact violations to the reviewer.
    """

    def __init__(self, result: GlossaryCheckResult) -> None:
        self.result = result
        v_count = len(result.violations)
        terms = ", ".join(v.en_term for v in result.violations[:3])
        suffix = " …" if v_count > 3 else ""
        super().__init__(
            f"{v_count} glossary violation(s): {terms}{suffix}"
        )


def load_flat_glossary(glossary_path: str | Path) -> tuple[GlossaryFlat, bool]:
    """Load all glossary sections into a flat EN→KO lookup.

    Returns ``(flat_glossary, llm_judged)``.

    *flat_glossary* maps a normalized EN term (lower-case, underscores→spaces)
    to ``(ko_term, source_section)``.  Priority: official > subtype_extracted >
    extracted — the first match wins so the most authoritative entry is kept.

    *llm_judged* reflects ``glossary["llm_judged"]``.  False means the
    ``extracted`` section has not been validated; callers should expose this
    rather than treating those terms as authoritative.
    """
    path = Path(glossary_path)
    data: dict = json.loads(path.read_text(encoding="utf-8"))
    llm_judged: bool = bool(data.get("llm_judged", False))

    flat: GlossaryFlat = {}
    for section in _GLOSSARY_SECTIONS:
        for en_raw, ko in data.get(section, {}).items():
            en_norm = en_raw.replace("_", " ").strip().lower()
            if en_norm and ko and en_norm not in flat:
                flat[en_norm] = (ko, section)

    return flat, llm_judged


def _term_in_text(term: str, text: str) -> bool:
    """Return True if *term* appears as a standalone word/phrase in *text*.

    Uses negative lookbehind/lookahead on word characters so that "install"
    does not match inside "installation", but multi-word terms like
    "core damage" and symbol-containing terms like "core damage [subroutine]"
    are handled correctly.
    """
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    return bool(re.search(pattern, text, re.IGNORECASE))


def _normalize_paren_spacing(text: str) -> str:
    """괄호 앞뒤 공백만 정규화한다.

    용어집에 ``hq -> '본부 (HQ)'``처럼 괄호 앞 공백이 들어간 표제어가 있는데 실제
    번역문은 ``본부(HQ)``로 붙여 쓴다. 리터럴 부분문자열 비교에서는 공백 하나 때문에
    불일치가 나고, hold-out 실측에서 이 한 가지가 공식 KO 위반 161건 중 13건이었다.

    의도적으로 좁게 만든다 — 공백을 전부 지우면 어절 경계를 넘는 오탐이 생긴다.
    여기서 손대는 것은 여는 괄호 앞과 닫는 괄호 뒤의 공백뿐이다.
    """
    return re.sub(r"\s*\(\s*", "(", text).replace(" )", ")")


def check_glossary_compliance(
    en_text: str,
    ko_text: str,
    glossary: GlossaryFlat,
    llm_judged: bool,
) -> GlossaryCheckResult:
    """Check that *ko_text* uses registered translations for EN terms in *en_text*.

    For every glossary entry whose EN term appears in *en_text*, the registered
    KO translation must be present somewhere in *ko_text*.  A missing KO term
    is a violation.

    Args:
        en_text:    Source English card text (rule or flavour).
        ko_text:    Draft Korean translation to validate.
        glossary:   Flat glossary from :func:`load_flat_glossary`.
        llm_judged: Whether the extracted section has been LLM-validated;
                    propagated to the result for callers to inspect.

    Returns:
        :class:`GlossaryCheckResult` — ``passed=True`` only when no violations.
    """
    violations: list[GlossaryViolation] = []
    ko_text_norm = _normalize_paren_spacing(ko_text)

    for en_term, (ko_term, source) in glossary.items():
        if not _term_in_text(en_term, en_text):
            continue
        if _normalize_paren_spacing(ko_term) not in ko_text_norm:
            violations.append(
                GlossaryViolation(en_term=en_term, expected_ko=ko_term, source=source)
            )

    return GlossaryCheckResult(
        passed=len(violations) == 0,
        violations=violations,
        llm_judged=llm_judged,
    )
