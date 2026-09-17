"""new_term_guard.py — Guardrail §4.3: New term HITL approval requirement.

SERVICE.md §4, rule 3:
  Do not arbitrarily create new terms not in the glossary.
  EN terms in the source text that are not registered require HITL approval.

HITL trigger ①: 신규 EN 용어 발견 (new EN term discovered in source text).
  Blocking terms trigger interrupt(); recording terms are stored without stopping
  the pipeline.

Blocking terms (two types only — AC4):
  (a) en_keywords (card subtypes) items not registered in the glossary.
  (b) Non-sentence-first, uppercase-starting, unregistered tokens in rule text.
      These identify referenced card names / proper nouns.

Recording terms: all other unregistered tokens — stored at detection time in
new_term_candidates.json and in the draft record, but pipeline continues.

Markup tags: <strong>, <em>, <trace> etc. are not words; their tag names must
NOT be tokenized.  Strip HTML tags before tokenizing (fix for the defect where
the tokenizer extracted 'strong' from '<strong>').

Classification is done here; blocking determination is done by hitl_interrupt.

Usage::

    glossary, llm_judged = load_flat_glossary(path)
    result = check_new_terms(en_text, glossary, llm_judged, en_keywords=card_en_keywords)
    if result.blocking_new_terms:
        # Caller invokes interrupt() for blocking terms.
        raise NewTermError(result)
    # result.recording_new_terms → write to new_term_candidates.json at detection time.
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

# Markup tag stripper: remove <tag> and </tag> patterns before tokenizing.
# This prevents tag names (strong, em, trace) from being extracted as tokens.
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Tokenize: words (possibly hyphenated) or game symbols [...].
# Applied AFTER markup stripping so tag names are never candidates.
_TOKEN_RE = re.compile(r"\[[^\]]+\]|[a-zA-Z][a-zA-Z'-]*[a-zA-Z]|[a-zA-Z]")

# Sentence-ending punctuation — a token immediately after these is sentence-first.
_SENTENCE_END = frozenset(".!?")

# Minimum word length to consider (anything shorter is noise).
_MIN_LEN = 3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_markup(text: str) -> str:
    """Remove HTML/markup tags, replacing with a space to preserve word boundaries."""
    return _HTML_TAG_RE.sub(" ", text)


def _is_sentence_start(stripped_text: str, match_start: int) -> bool:
    """Return True if the token at *match_start* opens a sentence.

    A token is sentence-first when:
      - it is the very first token in the text (no preceding non-whitespace), OR
      - the last non-whitespace character before it is a sentence-ending mark.
    """
    pre = stripped_text[:match_start].rstrip()
    if not pre:
        return True
    return pre[-1] in _SENTENCE_END


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class NewTermCandidate(BaseModel):
    """An EN term in the source that has no glossary registration."""

    en_term: str = Field(description="Normalized EN term found in the source text")


class NewTermCheckResult(BaseModel):
    """Result of the new term guardrail check.

    Classification (AC4):
      blocking_new_terms: terms that must trigger HITL interrupt.
      recording_new_terms: terms to record at detection time without stopping pipeline.
      new_terms: ALL unregistered terms (= blocking + recording), kept for backward compat.
      passed: True only when NO unregistered terms at all (blocking or recording).
    """

    passed: bool = Field(
        description="True only when no unregistered significant terms found (blocking or recording)"
    )
    new_terms: list[NewTermCandidate] = Field(
        default_factory=list,
        description="All unregistered terms (blocking + recording) — backward compat",
    )
    blocking_new_terms: list[NewTermCandidate] = Field(
        default_factory=list,
        description="Terms that trigger HITL interrupt (en_keywords unregistered, or non-sentence-first uppercase)",
    )
    recording_new_terms: list[NewTermCandidate] = Field(
        default_factory=list,
        description="Unregistered terms to record at detection time without interrupting",
    )
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
    *,
    en_keywords: list[str] | None = None,
    route: str = "rule",
) -> NewTermCheckResult:
    """Detect EN terms in *en_text* that are not registered in *glossary*.

    Strips HTML markup tags first (fixes the defect where tag names like 'strong'
    were extracted as tokens).  Tokenizes the stripped text, filters stopwords and
    short words, then checks the remaining content words against all glossary keys.

    Classification (AC4):
      Blocking (triggers interrupt):
        (a) Items in *en_keywords* (card subtypes) not in the glossary.
        (b) Tokens that start with an uppercase letter, are NOT at a sentence-start
            position, and are not registered — identifies referenced card names /
            proper nouns.  Only checked when route == "rule".
      Recording (store at detection time, no interrupt):
        All other unregistered tokens (lowercase, or sentence-first, etc.).

    Args:
        en_text:     Source English card text (rule or flavour).
        glossary:    Flat glossary from :func:`glossary_guard.load_flat_glossary`.
        llm_judged:  Whether the extracted section has been LLM-validated;
                     propagated to the result for callers to inspect.
        en_keywords: Card subtype keywords (e.g. ["Icebreaker", "Barrier"]).
                     Items not in the glossary are blocking-type (a).
        route:       "rule" or "flavor".  Blocking type (b) only applies to "rule".

    Returns:
        :class:`NewTermCheckResult` with blocking/recording classification.
    """
    # Build a set of ALL words that appear in any registered glossary phrase.
    # A unigram already covered by a multi-word entry is NOT a new term.
    covered_words: set[str] = set()
    for key in glossary:
        for word in key.split():
            covered_words.add(word)

    # (a) Blocking: unregistered items in en_keywords (card subtypes).
    blocking_seen: set[str] = set()
    blocking_new_terms: list[NewTermCandidate] = []
    if en_keywords:
        for kw in en_keywords:
            kw_norm = kw.strip().lower()
            if not kw_norm or len(kw_norm) < _MIN_LEN:
                continue
            if kw_norm in glossary or kw_norm in covered_words:
                continue
            if kw_norm not in blocking_seen:
                blocking_seen.add(kw_norm)
                blocking_new_terms.append(NewTermCandidate(en_term=kw_norm))

    # Tokenize the markup-stripped text for text-token classification.
    stripped = _strip_markup(en_text)

    seen: set[str] = set()
    recording_new_terms: list[NewTermCandidate] = []

    for m in _TOKEN_RE.finditer(stripped):
        raw = m.group(0)
        token = raw.lower()

        # Game symbols like [credit] are data, not terms.
        if token.startswith("["):
            continue

        if len(token) < _MIN_LEN:
            continue

        if token in _STOPWORDS:
            continue

        if token in seen or token in blocking_seen:
            continue

        # Already registered as a standalone key or covered by a multi-word phrase.
        if token in glossary or token in covered_words:
            continue

        seen.add(token)

        # (b) Blocking: non-sentence-first, uppercase-starting, unregistered token
        #     in rule text — identifies referenced card names / proper nouns.
        if route == "rule" and raw[0].isupper() and not _is_sentence_start(stripped, m.start()):
            blocking_seen.add(token)
            blocking_new_terms.append(NewTermCandidate(en_term=token))
        else:
            recording_new_terms.append(NewTermCandidate(en_term=token))

    all_new_terms = blocking_new_terms + recording_new_terms

    return NewTermCheckResult(
        passed=len(all_new_terms) == 0,
        new_terms=all_new_terms,
        blocking_new_terms=blocking_new_terms,
        recording_new_terms=recording_new_terms,
        llm_judged=llm_judged,
    )
