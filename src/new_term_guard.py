"""new_term_guard.py — Guardrail §4.3: New term HITL approval requirement.

SERVICE.md §4, rule 3:
  Do not arbitrarily create new terms not in the glossary.
  EN terms in the source text that are not registered require HITL approval.

HITL trigger ①: 신규 EN 용어 발견 (new EN term discovered in source text).
Unregistered significant EN terms block the translation; callers invoke interrupt().

Usage::

    glossary, llm_judged = load_flat_glossary(path)
    result = check_new_terms(en_text, glossary, llm_judged)
    if not result.passed:
        # Caller invokes interrupt() with result.new_terms as the payload.
        raise NewTermError(result)
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from glossary_guard import GlossaryFlat

# ---------------------------------------------------------------------------
# Stop-words: common English words that are never standalone game terms.
# These cover function words, generic verbs, and filler nouns in card text.
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset({
    # Articles, conjunctions, prepositions
    "a", "an", "the", "and", "or", "but", "if", "in", "on", "to",
    "of", "for", "at", "by", "as", "with", "from", "into", "onto",
    "upon", "over", "under", "about", "between", "among", "without",
    "via", "per", "than", "after", "before", "during", "until", "while",
    "unless", "when", "whenever", "where", "once", "although", "because",
    "though", "since", "except", "out", "off", "up", "down",
    # Pronouns / determiners
    "this", "that", "which", "who", "what", "it", "its", "you", "your",
    "they", "their", "he", "she", "his", "her", "him", "we", "us",
    "them", "those", "these", "each", "any", "all", "no", "one",
    "both", "either", "other", "another", "same", "such",
    # Common verbs (not game-specific)
    "is", "are", "was", "were", "be", "been", "being", "has", "have",
    "had", "do", "does", "did", "will", "would", "can", "could",
    "may", "might", "shall", "should", "must", "make", "makes", "made",
    "take", "takes", "took", "give", "gives", "gave", "get", "gets",
    "got", "use", "uses", "used", "pay", "pays", "paid", "add", "adds",
    "place", "places", "placed", "move", "moves", "moved",
    "choose", "chooses", "chose", "look", "looks", "play", "plays",
    "end", "ends", "start", "starts", "begin", "begins", "became",
    "put", "let", "lets", "gain", "gains", "lose", "loses", "lost",
    "deal", "deals", "dealt", "avoid", "spend", "spends", "spent",
    "become", "becomes",
    # Common adjectives / adverbs
    "not", "new", "first", "last", "next", "only", "just",
    "also", "even", "still", "more", "most", "less", "much", "many",
    "some", "few", "own", "then", "so", "too", "very", "now",
    "here", "there", "instead", "rather",
    # Generic card-text nouns (not game-specific in isolation)
    "turn", "phase", "action", "player", "players", "time", "type",
    "side", "number", "amount", "effect", "effects", "ability",
    "abilities", "name", "target", "group", "copy", "copies",
    "text",
})

# Tokenize: words (possibly hyphenated) or game symbols [...].
# Strips <markup> tags by matching only word characters and hyphens.
_TOKEN_RE = re.compile(r"\[[^\]]+\]|[a-zA-Z][a-zA-Z'-]*[a-zA-Z]|[a-zA-Z]")

# Minimum word length to consider (anything shorter is noise).
_MIN_LEN = 3


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class NewTermCandidate(BaseModel):
    """An EN term in the source that has no glossary registration."""

    en_term: str = Field(description="Normalized EN term found in the source text")


class NewTermCheckResult(BaseModel):
    """Result of the new term guardrail check."""

    passed: bool = Field(description="True only when no unregistered significant terms found")
    new_terms: list[NewTermCandidate] = Field(default_factory=list)
    llm_judged: bool = Field(
        description=(
            "Whether the 'extracted' glossary section has been LLM-validated. "
            "False means rule terms are unconfirmed; surface this to callers."
        )
    )


class NewTermError(Exception):
    """Raised to signal that HITL approval is required for unregistered EN terms.

    Carry the full NewTermCheckResult so the HITL interrupt handler can
    surface the exact candidates to the reviewer.
    """

    def __init__(self, result: NewTermCheckResult) -> None:
        self.result = result
        n = len(result.new_terms)
        terms = ", ".join(t.en_term for t in result.new_terms[:3])
        suffix = " …" if n > 3 else ""
        super().__init__(
            f"{n} new term(s) require HITL approval: {terms}{suffix}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_new_terms(
    en_text: str,
    glossary: GlossaryFlat,
    llm_judged: bool,
) -> NewTermCheckResult:
    """Detect EN terms in *en_text* that are not registered in *glossary*.

    Tokenizes the source, filters stopwords and short words, then checks the
    remaining content words against all glossary keys (unigrams and the
    multi-word phrases registered as single keys).  Any token absent from the
    glossary is flagged as a new-term candidate requiring HITL approval.

    Tokens that appear as components of a registered multi-word phrase are
    considered covered and are NOT flagged individually.

    Args:
        en_text:    Source English card text (rule or flavour).
        glossary:   Flat glossary from :func:`glossary_guard.load_flat_glossary`.
        llm_judged: Whether the extracted section has been LLM-validated;
                    propagated to the result for callers to inspect.

    Returns:
        :class:`NewTermCheckResult` — ``passed=True`` only when no new terms.
    """
    # Build a set of ALL words that appear in any registered glossary phrase.
    # A unigram already covered by a multi-word entry is NOT a new term.
    covered_words: set[str] = set()
    for key in glossary:
        for word in key.split():
            covered_words.add(word)

    seen: set[str] = set()
    new_terms: list[NewTermCandidate] = []

    for raw in _TOKEN_RE.findall(en_text):
        token = raw.lower()

        # Game symbols like [credit] are data, not terms
        if token.startswith("["):
            continue

        if len(token) < _MIN_LEN:
            continue

        if token in _STOPWORDS:
            continue

        if token in seen:
            continue
        seen.add(token)

        # Already registered as a standalone key
        if token in glossary:
            continue

        # Part of a registered multi-word phrase — covered
        if token in covered_words:
            continue

        new_terms.append(NewTermCandidate(en_term=token))

    return NewTermCheckResult(
        passed=len(new_terms) == 0,
        new_terms=new_terms,
        llm_judged=llm_judged,
    )
