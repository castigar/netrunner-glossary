"""pattern_extractor.py — rule-text sentence pattern (문형) extraction.

SERVICE.md §3 exposes ``lookup_pattern(text)``: given a rules sentence, return
the KO sentence template the team has used for it before.  This module builds
the table that tool reads, and it makes **zero LLM calls** — the templates come
from counting the corpus, exactly like term candidates do.

How a template is made
----------------------
Netrunner rules text is highly formulaic, so two cards usually differ only in
their numbers and symbols.  Abstracting those away collapses many cards onto
one template::

    EN  "Trash 1 installed program."          -> "Trash {N} installed program."
    KO  "설치된 프로그램 1개를 폐기한다."          -> "설치된 프로그램 {N}개를 폐기한다."

    EN  "The Runner loses 2[credit]."         -> "The Runner loses {N}{SYM}."
    KO  "러너는 2[credit]을 잃는다."             -> "러너는 {N}{SYM}을 잃는다."

Cards are split into sentences, EN and KO sentences are paired positionally,
each pair is abstracted, and identical abstractions are counted.  A template
is kept only when it recurs at least ``min_count`` times, so one-off phrasings
never become "the way we say it".

Positional pairing is the honest limit here: it holds because official KO
translations keep EN sentence order, and any card whose sentence counts differ
between EN and KO is skipped rather than mis-aligned.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

#: Game symbols such as [credit], [click], [subroutine].
SYMBOL_RE = re.compile(r"\[[a-z0-9\-]+\]")

#: Standalone integers.  Bounded by non-digits so "2.0" is not split.
NUMBER_RE = re.compile(r"(?<!\d)\d+(?!\d)")

#: Sentence boundary: a newline, or a period/question/exclamation followed by
#: whitespace.  Korean rules text ends sentences with "다." so the period rule
#: carries over.
SENTENCE_SPLIT_RE = re.compile(r"\n+|(?<=[.!?])\s+")

#: Markup the corpus uses for emphasis and keywords; irrelevant to sentence shape.
MARKUP_RE = re.compile(r"</?(?:strong|em|ul|li|trace)>")


@dataclass
class PatternEntry:
    """One recurring EN->KO sentence template pair."""

    en_template: str
    ko_template: str
    count: int
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "en_template": self.en_template,
            "ko_template": self.ko_template,
            "count": self.count,
            "examples": self.examples,
        }


def abstract(text: str) -> str:
    """Replace numbers and game symbols with placeholders.

    Markup tags are stripped first so ``<strong>`` does not split a template
    away from its plain-text twin.

    Args:
        text: one sentence of EN or KO rules text.

    Returns:
        The sentence with every game symbol replaced by ``{SYM}`` and every
        standalone integer replaced by ``{N}``, whitespace collapsed.
    """
    out = MARKUP_RE.sub("", text)
    out = SYMBOL_RE.sub("{SYM}", out)
    out = NUMBER_RE.sub("{N}", out)
    return re.sub(r"\s+", " ", out).strip()


def split_sentences(text: str) -> list[str]:
    """Split rules text into non-empty sentences."""
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def extract_patterns(
    pairs: list[dict],
    en_field: str = "en_text",
    ko_field: str = "ko_text",
    min_count: int = 3,
    max_examples: int = 3,
) -> list[PatternEntry]:
    """Extract recurring EN->KO sentence templates from aligned card text.

    Makes zero LLM calls.

    Args:
        pairs:        Card dicts carrying *en_field*, *ko_field* and "id".
        en_field:     Key holding EN rules text.
        ko_field:     Key holding KO rules text.
        min_count:    Minimum occurrences for a template pair to be kept.
        max_examples: How many example card ids to record per template.

    Returns:
        PatternEntry list sorted by descending count, then EN template.

    Note:
        Cards whose EN and KO sentence counts differ are skipped, because
        positional pairing would silently misalign them.
    """
    counts: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], list[str]] = defaultdict(list)

    for pair in pairs:
        en_sentences = split_sentences(pair.get(en_field, ""))
        ko_sentences = split_sentences(pair.get(ko_field, ""))
        if not en_sentences or len(en_sentences) != len(ko_sentences):
            continue

        card_id = pair.get("id", "")
        for en_sentence, ko_sentence in zip(en_sentences, ko_sentences):
            key = (abstract(en_sentence), abstract(ko_sentence))
            if not key[0] or not key[1]:
                continue
            counts[key] += 1
            if card_id and len(examples[key]) < max_examples:
                examples[key].append(card_id)

    entries = [
        PatternEntry(en_template=en, ko_template=ko, count=n, examples=examples[(en, ko)])
        for (en, ko), n in counts.items()
        if n >= min_count
    ]
    entries.sort(key=lambda e: (-e.count, e.en_template))
    return entries


def write_patterns_json(entries: list[PatternEntry], path: str | Path) -> None:
    """Write *entries* to *path* as a JSON array."""
    payload = [e.to_dict() for e in entries]
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def lookup_pattern(entries: list[PatternEntry], text: str) -> PatternEntry | None:
    """Return the template matching *text*, or None.

    *text* is abstracted the same way the table was built, so a caller can pass
    a raw EN sentence.

    Args:
        entries: table returned by :func:`extract_patterns`.
        text:    one EN rules sentence.

    Returns:
        The most frequent matching PatternEntry, or None when nothing matches.
    """
    target = abstract(text)
    matches = [e for e in entries if e.en_template == target]
    if not matches:
        return None
    return max(matches, key=lambda e: e.count)
