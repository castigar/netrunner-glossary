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
  6. Cap in two stages: pick the *max_en_terms* most frequent EN terms, then the
     *top_k_per_en* highest-Dice KO candidates within each of them.

왜 두 단계인가 (SERVICE.md §6, 결정 ② 개정)
-------------------------------------------
원안은 "Dice 상위 N개 상한"이었는데, 실측 결과 그 순위가 진짜 용어를 잘라냈다.
전체 후보에서 install→설치는 17,436위, trash→폐기는 55,471위로 상위 5,000 밖이었다.

원인은 Dice의 정의다. ``2·공기빈도/(EN빈도+KO빈도)``이므로 널리 쓰이는 핵심 용어일수록
분모가 커져 점수가 떨어진다. 딱 몇 장에서만 같이 나오고 다른 데선 안 나오는 희귀
문구 쌍이 Dice 1.0을 받는다. **Dice는 배타성을 재지 용어다움을 재지 않는다.**

다만 Dice는 **하나의 EN 용어 안에서는** 잘 작동한다. trash의 후보들을 보면
폐기한다 0.796 vs 수 0.269 / 있다 0.242로 정답과 잡음이 뚜렷이 갈린다.
그래서 EN 용어 선정은 빈도로, 그 안의 역어 선정은 Dice로 한다.

상한은 판정 예산이지 품질 게이트가 아니다 — 잡음 후보를 실제로 걸러내는 것은
term_judge의 가부 판정이다.

부제 추출은 이 모듈이 아니라 subtype_extractor가 담당한다.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass


_MARKUP = re.compile(r"</?[a-zA-Z][^>]*>")


def _extract_en_ngrams(text: str, max_n: int = 3) -> list[str]:
    """Extract 1..max_n word n-grams from lowercased EN text.

    Game symbols like [credit] are kept as single tokens.  Markup is stripped
    first, because the word-character pattern below turns '<strong>' into the
    word 'strong', which polluted 208 EN terms with things like
    '1 strong barrier'.  SERVICE.md §5 already excludes <strong>/<em> from
    gate 2 for the same reason — it is notation, not text.
    """
    tokens = re.findall(r"\[[^\]]+\]|\w+", _MARKUP.sub(" ", text).lower())
    ngrams: list[str] = []
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            ngrams.append(" ".join(tokens[i : i + n]))
    return ngrams


# Edge punctuation to shed.  Brackets are deliberately absent: '[credit]' is a
# game symbol, and stripping it to 'credit' would both destroy the symbol and
# collide with the English word.
_KO_EDGE_PUNCT = ".,:;!?\"'()"


def _normalize_ko_token(token: str) -> str:
    """Strip markup and edge punctuation from one eojeol, keeping symbols intact.

    KO text is whitespace-split, so '있다.' and '있다' were counted as different
    terms and their cooccurrence was split between them.  Over the clean corpus
    this collapses 2,619 surface forms to 2,407.
    """
    return _MARKUP.sub("", token).strip(_KO_EDGE_PUNCT)


def _extract_ko_eojeol_ngrams(text: str, max_n: int = 3) -> list[str]:
    """Extract 1..max_n eojeol n-grams from KO text.

    Korean eojeol (어절) are whitespace-delimited units, normalized to shed
    markup and edge punctuation.  Game symbols like [credit] are preserved.

    Particles (조사) are left attached — separating them needs a morphological
    analyzer, which this pipeline does not have.
    """
    tokens = [t for t in (_normalize_ko_token(t) for t in text.split()) if t]
    ngrams: list[str] = []
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            ngrams.append(" ".join(tokens[i : i + n]))
    return ngrams


DEFAULT_MAX_EN_TERMS = 1500
DEFAULT_TOP_K_PER_EN = 3


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
    max_en_terms: int | None = DEFAULT_MAX_EN_TERMS,
    top_k_per_en: int | None = DEFAULT_TOP_K_PER_EN,
) -> list[TermCandidate]:
    """Generate term candidates using only corpus statistics — 0 LLM calls.

    Args:
        pairs:        List of dicts, each with *en_field* and *ko_field* strings.
        en_field:     Key for the EN source text in each pair dict.
        ko_field:     Key for the KO translation text in each pair dict.
        max_n:        Maximum n-gram length (default 3).
        min_cooccur:  Minimum cooccurrence count threshold (default 5).
        max_en_terms: Keep only the *max_en_terms* EN terms with the highest
                      cooccurrence. None keeps every EN term.
        top_k_per_en: Within each kept EN term, keep its *top_k_per_en* highest
                      Dice candidates. None keeps every candidate.

    Returns:
        List of TermCandidate, sorted by Dice coefficient descending.  Only pairs
        whose cooccurrence >= *min_cooccur* survive, then the two-stage cap.

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

    return _apply_cap(candidates, max_en_terms, top_k_per_en)


def _apply_cap(
    candidates: list[TermCandidate],
    max_en_terms: int | None,
    top_k_per_en: int | None,
) -> list[TermCandidate]:
    """Two-stage cap: frequent EN terms, then their best KO candidates by Dice.

    Ties are broken deterministically throughout, so the cut never depends on
    dict ordering.
    """
    by_en: dict[str, list[TermCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_en[candidate.en_term].append(candidate)

    # Stage 1 — EN terms by how often they occur, not by Dice.  An EN term's
    # cooccurrence with its best KO candidate is the available frequency proxy.
    ranked_en = sorted(
        by_en,
        key=lambda en: (-max(c.cooccurrence for c in by_en[en]), en),
    )
    if max_en_terms is not None:
        ranked_en = ranked_en[:max_en_terms]

    # Stage 2 — within one EN term, Dice separates the translation from the
    # sentence scaffolding it happens to sit next to.
    kept: list[TermCandidate] = []
    for en in ranked_en:
        group = sorted(
            by_en[en],
            key=lambda c: (-c.dice, -c.cooccurrence, c.ko_term),
        )
        kept.extend(group if top_k_per_en is None else group[:top_k_per_en])

    kept.sort(key=lambda c: (-c.dice, -c.cooccurrence, c.en_term, c.ko_term))
    return kept
