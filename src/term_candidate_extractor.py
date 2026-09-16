"""term_candidate_extractor.py — Statistical term candidate generation (0 LLM calls).

SERVICE.md §6 pipeline step 4 (통계 후보 생성):
  EN n-gram × KO 어절 n-gram 공기빈도, Dice/PMI, 최소 등장 5회.
  LLM을 한 번도 호출하지 않는다. LLM은 term_judge.py의 가부 판정 단계에서만 쓴다.

Algorithm:
  1. For each (EN, KO) card pair, extract EN n-grams (1-3) and KO eojeol n-grams (1-3).
  2. Record cooccurrence counts: how often each (en_ngram, ko_ngram) pair appears together
     in the same card.
  3. Compute Dice coefficient = 2 * cooccur(A,B) / (freq(A) + freq(B)).
  4. Compute PMI = log2(P(A,B) / (P(A) * P(B))).
  5. Keep only pairs with cooccurrence >= min_cooccur (default 5).
  6. Return sorted by Dice score descending.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass


def _extract_en_ngrams(text: str, max_n: int = 3) -> list[str]:
    """Extract 1..max_n word n-grams from lowercased EN text.

    Game symbols like [credit] are kept as single tokens.
    """
    tokens = re.findall(r"\[[^\]]+\]|\w+", text.lower())
    ngrams: list[str] = []
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            ngrams.append(" ".join(tokens[i : i + n]))
    return ngrams


def _extract_ko_eojeol_ngrams(text: str, max_n: int = 3) -> list[str]:
    """Extract 1..max_n eojeol n-grams from KO text.

    Korean eojeol (어절) are whitespace-delimited units.
    Game symbols and digits within brackets are preserved.
    """
    tokens = text.split()
    ngrams: list[str] = []
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            ngrams.append(" ".join(tokens[i : i + n]))
    return ngrams


@dataclass(frozen=True)
class TermCandidate:
    """A candidate (EN n-gram, KO eojeol n-gram) term pair with statistics."""

    en_term: str
    ko_term: str
    cooccurrence: int
    dice: float
    pmi: float


def generate_candidates(
    pairs: list[dict],
    en_field: str = "en_text",
    ko_field: str = "ko_text",
    max_n: int = 3,
    min_cooccur: int = 5,
) -> list[TermCandidate]:
    """Generate term candidates using only corpus statistics — 0 LLM calls.

    Args:
        pairs:        List of dicts, each with *en_field* and *ko_field* strings.
        en_field:     Key for the EN source text in each pair dict.
        ko_field:     Key for the KO translation text in each pair dict.
        max_n:        Maximum n-gram length (default 3).
        min_cooccur:  Minimum cooccurrence count threshold (default 5).

    Returns:
        List of TermCandidate, sorted by Dice coefficient descending.
        Only pairs whose cooccurrence >= *min_cooccur* are returned.

    Note:
        This function makes **zero** LLM API calls. All computation is
        purely statistical over the provided corpus pairs.
    """
    en_freq: Counter[str] = Counter()
    ko_freq: Counter[str] = Counter()
    cooccur: Counter[tuple[str, str]] = Counter()

    total_pairs = len(pairs)

    for pair in pairs:
        en_text = pair.get(en_field, "") or ""
        ko_text = pair.get(ko_field, "") or ""

        en_ngrams = set(_extract_en_ngrams(en_text, max_n))
        ko_ngrams = set(_extract_ko_eojeol_ngrams(ko_text, max_n))

        for en_ng in en_ngrams:
            en_freq[en_ng] += 1
        for ko_ng in ko_ngrams:
            ko_freq[ko_ng] += 1
        for en_ng in en_ngrams:
            for ko_ng in ko_ngrams:
                cooccur[(en_ng, ko_ng)] += 1

    candidates: list[TermCandidate] = []
    for (en_ng, ko_ng), cnt in cooccur.items():
        if cnt < min_cooccur:
            continue

        ef = en_freq[en_ng]
        kf = ko_freq[ko_ng]

        dice = (2.0 * cnt) / (ef + kf) if (ef + kf) > 0 else 0.0

        # PMI: log2(P(A,B) / (P(A)*P(B))), using card count as the universe.
        if total_pairs > 0 and ef > 0 and kf > 0:
            p_ab = cnt / total_pairs
            p_a = ef / total_pairs
            p_b = kf / total_pairs
            pmi = math.log2(p_ab / (p_a * p_b))
        else:
            pmi = 0.0

        candidates.append(
            TermCandidate(
                en_term=en_ng,
                ko_term=ko_ng,
                cooccurrence=cnt,
                dice=dice,
                pmi=pmi,
            )
        )

    candidates.sort(key=lambda c: c.dice, reverse=True)
    return candidates
