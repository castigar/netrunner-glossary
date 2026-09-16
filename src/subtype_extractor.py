"""subtype_extractor.py — 부제(subtype) 추출 경로 (SERVICE.md §6, 결정 ①).

추출 경로는 둘로 나뉜다. 이 모듈은 그중 **부제 경로**를 담당한다.

  부제 경로 (이 파일)  — `keywords` 필드.  필드 단위라 후보가 수백 규모다.
                         `card_subtypes.json` 88항목 정답셋으로 직접 채점한다.
  룰 경로 (term_candidate_extractor) — `text` 필드.  n-gram 통계 + Dice 상위 N 상한.

정답셋 88항목은 **부제 경로에만** 적용한다.  룰 용어 추출은 별도 수동 라벨링
정답셋으로 채점하고, 여기서는 채점하지 않는다.

정렬 규칙
---------
`keywords`는 ` - `로 구분된 리스트이고 EN↔KO가 **위치로 1:1 대응**한다.
원소 수가 다른 카드는 대응이 어긋나므로 통째로 건너뛴다 (부분 정렬을 시도하면
틀린 쌍을 만들어낸다).

미번역 필터를 통계보다 먼저
---------------------------
KO 원소에 한글이 없으면 미번역 영문 잔재이므로 버린다.  이 필터를 빼면 영문
잔재가 경쟁 역어처럼 보여 부제 용어의 54%(47/87)가 충돌로 분류돼 용어집에서
사라진다.  필터 후에는 15%(12/80)로 떨어진다.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from corpus_split import _has_hangul


@dataclass(frozen=True)
class SubtypePair:
    """하나의 EN 부제와 그 KO 역어, 그리고 근거 카드들.

    ``en_term``은 소문자로 정규화한다 (정답셋 id와 맞추기 위해).
    ``ko_term``은 카드에 인쇄된 그대로 둔다.
    """

    en_term: str
    ko_term: str
    card_ids: tuple[str, ...]

    @property
    def card_count(self) -> int:
        return len(self.card_ids)


@dataclass
class SubtypeExtractionResult:
    """부제 추출 결과와 그 과정에서 무엇이 걸러졌는지에 대한 기록."""

    pairs: list[SubtypePair] = field(default_factory=list)
    cards_with_keywords: int = 0
    cards_skipped_length_mismatch: int = 0
    elements_dropped_untranslated: int = 0

    def report(self) -> str:
        return (
            f"Subtype pairs: {len(self.pairs)}  "
            f"EN terms: {len({p.en_term for p in self.pairs})}  "
            f"Cards: {self.cards_with_keywords} "
            f"(skipped {self.cards_skipped_length_mismatch} on length mismatch)  "
            f"Dropped untranslated elements: {self.elements_dropped_untranslated}"
        )


def extract_subtype_pairs(
    cards: list[dict],
    en_field: str = "en_keywords",
    ko_field: str = "ko_keywords",
) -> SubtypeExtractionResult:
    """*cards*의 keywords 필드를 위치 정렬해 EN→KO 부제 쌍을 뽑는다.

    Args:
        cards:     load_clean_corpus() 출력.  *en_field*/*ko_field*가 없거나
                   비어 있는 카드는 조용히 건너뛴다.
        en_field:  EN 부제 리스트 키.
        ko_field:  KO 부제 리스트 키.

    Returns:
        SubtypeExtractionResult.  ``pairs``는 (en_term, ko_term)로 유일하고
        en_term, ko_term 순으로 정렬돼 있다.

    LLM을 한 번도 호출하지 않는다.
    """
    occurrences: dict[tuple[str, str], list[str]] = defaultdict(list)
    result = SubtypeExtractionResult()

    for card in cards:
        en_kw = card.get(en_field) or []
        ko_kw = card.get(ko_field) or []
        if not en_kw or not ko_kw:
            continue
        result.cards_with_keywords += 1

        if len(en_kw) != len(ko_kw):
            result.cards_skipped_length_mismatch += 1
            continue

        card_id = card.get("id", "")
        for en_term, ko_term in zip(en_kw, ko_kw):
            if not _has_hangul(ko_term):
                result.elements_dropped_untranslated += 1
                continue
            occurrences[(en_term.lower(), ko_term)].append(card_id)

    result.pairs = [
        SubtypePair(en_term=en, ko_term=ko, card_ids=tuple(ids))
        for (en, ko), ids in sorted(occurrences.items())
    ]
    return result


def to_term_pairs(pairs: list[SubtypePair]) -> list[dict]:
    """detect_conflicts()가 받는 근거 카드 단위 dict 목록으로 펼친다."""
    return [
        {"en_term": p.en_term, "ko_term": p.ko_term, "card_id": card_id}
        for p in pairs
        for card_id in p.card_ids
    ]
