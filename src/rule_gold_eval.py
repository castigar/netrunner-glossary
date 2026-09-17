"""rule_gold_eval.py — 룰 용어 수동 정답셋(data/rule_terms_gold.json) 채점기.

SERVICE.md §6 결정 ①에 따라 추출 경로는 둘이다.  부제 경로는 card_subtypes.json
88항목으로 채점하고(term_extraction_eval), 룰 경로는 번역가가 손으로 라벨링한
정답셋으로 채점한다.  이 모듈이 후자를 맡는다.

data/README-rule-gold.md §5가 번역가에게 약속한 두 숫자를 낸다.

  EN 재현율 = 추출기가 올린 정답 EN 용어 / 채점 대상 정답 EN 용어
  KO 정확도 = 역어까지 맞은 것 / 추출기가 올린 정답 EN 용어

정밀도는 내지 않는다.  32항목은 룰 용어 전체가 아니므로 정답셋에 없는 추출 결과를
오탐으로 셀 수 없다.  그쪽은 하드 게이트 1이 간접 검증한다.

닿지 않는 항목을 분모에서 뺀다 (README §4)
--------------------------------------------
추출기가 **구조적으로 낼 수 없는** 항목을 못 맞힌 것과 같이 세면 숫자가 거짓말을
한다.  못 찾은 항목에 한해 아래 순서로 이유를 붙이고, 이유가 붙은 것은 분모에서
뺀다.  찾은 항목은 이유를 따지지 않는다 — 실제로 잡혔으면 잡힌 것이다.

  official_excluded   공식 용어집이 이미 덮는 용어.  build_assets가 룰 후보에서
                      excluded_ids로 **의도적으로** 걸러낸다.  실측상 32항목 중
                      16개가 여기 해당한다(barrier·ice·run·agenda…).  이것을
                      분리하지 않으면 재현율이 절반으로 보이는데 그 절반은
                      추출기의 실패가 아니라 설계다.
  over_max_n          n-gram 상한(3)을 넘는 길이.
  below_min_cooccur   EN 등장 카드 수 < min_cooccur(5).  공기빈도는 EN 문서빈도를
                      넘을 수 없으므로 이건 증명이다.  AP(1)·killer(1)·AI(4).
  below_rank_cut      EN 용어 상한(max_en_terms) 밖.  **추정이다** — 상한의 실제
                      정렬 키는 EN별 최대 공기빈도인데 그 값은 전체 후보 생성을
                      다시 돌려야 나오므로 문서빈도를 대리값으로 쓴다.  문서빈도는
                      공기빈도의 상한이라 방향은 맞지만 경계에서 어긋날 수 있다.

표기 손상(symbol_mangled)은 제외 사유가 아니다.  'R&D'는 토크나이저가 'r d'로
쪼개지만 그 형태로 후보에 실제로 오른다(실측 문서빈도 85).  README §4는 이것을
못 잡는 항목으로 예상했는데 실측은 달랐다.  그래서 양쪽을 같은 토크나이저로
정규화해 채점하고, 표기가 손상됐다는 사실만 표시한다.

무엇에 대고 채점하는가
----------------------
``pairs``는 en_term/ko_term을 가진 무엇이든 된다.  세 가지로 부를 수 있다.

  1. generate_candidates()의 후보 목록 — 추출기가 **올린** 비율 (README §5의 정의)
  2. glossary.json의 extracted — LLM 판정까지 통과한 룰 용어집
  3. 공식+부제+룰을 합친 납품 용어집 — 번역가가 실제로 받는 것.  이때
     official_excluded는 비워야 한다.  공식 용어집 항목은 여기서는 제외 대상이
     아니라 정상 수록분이다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Protocol, Sequence

from ko_morphology import normalize_eojeol
from term_candidate_extractor import DEFAULT_MAX_EN_TERMS, _extract_en_ngrams

#: build_assets.DEFAULT_MIN_COOCCUR와 같은 값.  build_assets를 import하면 판정기와
#: 임베딩 모델까지 딸려오므로 여기서는 값만 맞춘다.
DEFAULT_MIN_COOCCUR = 5

#: 채점 결과에 붙는 이유.  FOUND와 MISSED가 분모에 남는다.
FOUND = "found"
MISSED = "missed"
OFFICIAL_EXCLUDED = "official_excluded"
OVER_MAX_N = "over_max_n"
BELOW_MIN_COOCCUR = "below_min_cooccur"
BELOW_RANK_CUT = "below_rank_cut"

_VALID_SOURCES = frozenset({"unaided", "aided"})
_SUPPORTED_SCHEMA = 1

#: 어절 하나를 형태소 정규화하기 전에 떼어낼 문장부호.  term_candidate_extractor의
#: _KO_EDGE_PUNCT와 같은 목록이다 — 정답셋과 후보가 같은 처리를 받아야 한다.
_KO_EDGE_PUNCT = ".,:;!?\"'()"

_MARKUP = re.compile(r"</?[a-zA-Z][^>]*>")


@dataclass(frozen=True)
class RuleGoldEntry:
    """정답셋 한 항목.  ko는 정당한 역어 전부이고 하나라도 맞으면 정답이다."""

    en: str
    ko: tuple[str, ...]
    source: str
    note: str = ""


class HasEnKoTerm(Protocol):
    """en_term/ko_term을 가진 것 — TermCandidate, TermPair, 아래 SimpleTermPair."""

    en_term: str
    ko_term: str


@dataclass(frozen=True)
class SimpleTermPair:
    """dict 형태의 용어집을 채점기에 넣기 위한 최소 어댑터."""

    en_term: str
    ko_term: str


@dataclass(frozen=True)
class EntryOutcome:
    """정답셋 한 항목의 채점 결과."""

    en: str
    normalized_en: str
    source: str
    found: bool
    reach: str
    ko_gold: tuple[str, ...]
    ko_system: tuple[str, ...]
    ko_match: bool | None
    symbol_mangled: bool


@dataclass(frozen=True)
class RuleGoldResult:
    """룰 용어 정답셋 채점 결과."""

    outcomes: tuple[EntryOutcome, ...]
    gold_size: int
    scored_size: int
    found: int
    ko_correct: int
    en_recall: float
    ko_accuracy: float
    unreachable: Mapping[str, int]
    by_source: Mapping[str, Mapping[str, int]]

    def report(self) -> str:
        lines = [
            f"정답셋 {self.gold_size}항목 · 채점 대상 {self.scored_size}항목",
            f"EN 재현율 {self.en_recall:.4f} ({self.found}/{self.scored_size})  "
            f"KO 정확도 {self.ko_accuracy:.4f} ({self.ko_correct}/{self.found})",
        ]

        for source in sorted(self.by_source):
            counts = self.by_source[source]
            lines.append(
                f"  {source:8} 채점 {counts['scored']:2}  "
                f"EN {counts['found']:2}  KO {counts['ko_correct']:2}"
            )

        if self.unreachable:
            lines.append("닿지 않는 항목 (분모에서 제외):")
            for reason, count in sorted(self.unreachable.items()):
                terms = ", ".join(
                    o.en for o in self.outcomes if o.reach == reason
                )
                lines.append(f"  {reason:20} {count:2}  {terms}")

        missed = [o for o in self.outcomes if o.reach == MISSED]
        if missed:
            lines.append(f"못 맞힌 것 {len(missed)}: " + ", ".join(o.en for o in missed))

        wrong_ko = [o for o in self.outcomes if o.ko_match is False]
        if wrong_ko:
            lines.append("역어 불일치:")
            for outcome in wrong_ko:
                lines.append(
                    f"  {outcome.en:16} 정답 {'/'.join(outcome.ko_gold)}"
                    f"  ← 시스템 {'/'.join(outcome.ko_system)}"
                )

        mangled = [o for o in self.outcomes if o.symbol_mangled]
        if mangled:
            lines.append(
                "표기 손상(토크나이저): "
                + ", ".join(f"{o.en}→{o.normalized_en}" for o in mangled)
            )

        return "\n".join(lines)


def normalize_en_term(term: str) -> str:
    """EN 용어를 추출기와 같은 토크나이저로 정규화한다.

        'Archives'  -> 'archives'
        'R&D'       -> 'r d'      (\\w+가 &를 버린다 — 후보에도 이 형태로 오른다)
        'code_gate' -> 'code gate' (공식 용어집 id와 인쇄 표기를 맞춘다)

    밑줄·붙임표를 먼저 공백으로 바꾼다.  둘 다 \\w에 걸려 토큰 안에 남으므로
    그대로 두면 공식 용어집 id 'code_gate'가 인쇄 표기 'code gate'와 영원히
    어긋난다.  term_extraction_eval._normalize_subtype_id이 부제 경로에서 같은
    이유로 같은 처리를 한다.
    """
    spaced = term.replace("_", " ").replace("-", " ")
    return " ".join(_extract_en_ngrams(spaced, max_n=1))


def normalize_ko_term(term: str) -> str:
    """KO 역어를 추출기와 같은 형태소 정규화로 통과시킨다.

        '호스트된'      -> '호스트'
        '런을 종료한다' -> '런 종료'

    정답셋은 기본형으로 적히고 추출 결과는 어간으로 모여 있으므로, 양쪽을 같은
    함수에 통과시켜야 활용형 차이로 오답이 나지 않는다.
    """
    normalized = [
        normalize_eojeol(stripped)
        for stripped in (
            _MARKUP.sub("", token).strip(_KO_EDGE_PUNCT) for token in term.split()
        )
        if stripped
    ]
    return " ".join(part for part in normalized if part)


def _is_symbol_mangled(term: str, normalized: str) -> bool:
    """인쇄 표기가 토크나이저를 통과하며 부서졌는가 ('R&D' -> 'r d')."""
    plain = " ".join(term.replace("_", " ").replace("-", " ").lower().split())
    return normalized != plain


def load_rule_gold(path: str | Path) -> tuple[RuleGoldEntry, ...]:
    """data/rule_terms_gold.json을 읽어 검증한다.

    Raises:
        ValueError: schema_version이 지원 범위를 벗어나거나, 항목에 en/ko가 없거나,
                    source가 unaided/aided가 아닐 때.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    version = payload.get("schema_version")
    if version != _SUPPORTED_SCHEMA:
        raise ValueError(
            f"Unsupported schema_version {version!r} in {path} "
            f"(expected {_SUPPORTED_SCHEMA})"
        )

    entries: list[RuleGoldEntry] = []
    for i, record in enumerate(payload.get("entries", [])):
        en = record.get("en")
        if not en:
            raise ValueError(f"Entry {i} in {path} has no 'en'")
        ko = record.get("ko")
        if not ko or not isinstance(ko, list):
            raise ValueError(f"Entry {i} ({en}) in {path} has no 'ko' list")
        source = record.get("source")
        if source not in _VALID_SOURCES:
            raise ValueError(
                f"Entry {i} ({en}) in {path} has source {source!r}; "
                f"expected one of {sorted(_VALID_SOURCES)}"
            )
        entries.append(
            RuleGoldEntry(
                en=en,
                ko=tuple(ko),
                source=source,
                note=record.get("note", ""),
            )
        )
    return tuple(entries)


def pairs_from_mapping(
    mapping: Mapping[str, str | Sequence[str]]
) -> list[SimpleTermPair]:
    """{en: ko} 또는 {en: [ko, ...]} 용어집을 채점기 입력으로 바꾼다."""
    pairs: list[SimpleTermPair] = []
    for en_term, ko in mapping.items():
        ko_terms = [ko] if isinstance(ko, str) else list(ko)
        pairs.extend(SimpleTermPair(en_term=en_term, ko_term=k) for k in ko_terms)
    return pairs


def en_document_frequency(
    pairs: Iterable[Mapping[str, str]],
    en_field: str = "en_text",
    max_n: int = 3,
) -> Counter[str]:
    """EN n-gram이 등장한 **카드 수**를 센다 (등장 횟수가 아니다).

    generate_candidates가 카드마다 n-gram 집합을 세는 것과 같은 계산이다.  닿지
    않는 항목 판정의 min_cooccur·max_en_terms 비교에 쓴다.
    """
    freq: Counter[str] = Counter()
    for pair in pairs:
        for ngram in set(_extract_en_ngrams(pair.get(en_field, "") or "", max_n)):
            freq[ngram] += 1
    return freq


def _rank_cut_boundary(
    en_doc_freq: Mapping[str, int] | None,
    min_cooccur: int,
    max_en_terms: int | None,
) -> int | None:
    """상한에 걸린 EN 용어의 경계 문서빈도.  상한에 닿지 않으면 None."""
    if en_doc_freq is None or max_en_terms is None:
        return None
    eligible = sorted(
        (freq for freq in en_doc_freq.values() if freq >= min_cooccur), reverse=True
    )
    if len(eligible) <= max_en_terms:
        return None
    return eligible[max_en_terms - 1]


def _classify_reach(
    normalized_en: str,
    *,
    excluded: frozenset[str],
    en_doc_freq: Mapping[str, int] | None,
    max_n: int,
    min_cooccur: int,
    boundary: int | None,
) -> str:
    """못 찾은 항목에 이유를 붙인다.  이유가 없으면 MISSED."""
    if normalized_en in excluded:
        return OFFICIAL_EXCLUDED
    if len(normalized_en.split()) > max_n:
        return OVER_MAX_N
    if en_doc_freq is not None:
        freq = en_doc_freq.get(normalized_en, 0)
        if freq < min_cooccur:
            return BELOW_MIN_COOCCUR
        if boundary is not None and freq < boundary:
            return BELOW_RANK_CUT
    return MISSED


def evaluate_rule_gold(
    entries: Sequence[RuleGoldEntry],
    pairs: Iterable[HasEnKoTerm],
    *,
    en_doc_freq: Mapping[str, int] | None = None,
    official_excluded: Iterable[str] = (),
    max_n: int = 3,
    min_cooccur: int = DEFAULT_MIN_COOCCUR,
    max_en_terms: int | None = DEFAULT_MAX_EN_TERMS,
) -> RuleGoldResult:
    """룰 용어 정답셋으로 추출 결과를 채점한다.

    Args:
        entries:           load_rule_gold()가 돌려준 정답셋.
        pairs:             채점 대상.  en_term/ko_term을 가진 것이면 된다
                           (후보 목록 · 룰 용어집 · 납품 용어집 전체).
        en_doc_freq:       EN n-gram별 등장 카드 수.  없으면 빈도 기반 제외
                           사유(below_min_cooccur / below_rank_cut)를 판정하지
                           않고 전부 못 맞힌 것으로 센다.
        official_excluded: 룰 후보에서 제외되는 공식 용어집 id.  납품 용어집
                           전체를 채점할 때는 비운다.
        max_n:             추출기의 n-gram 상한.
        min_cooccur:       추출기의 최소 공기빈도.
        max_en_terms:      추출기의 EN 용어 상한 (2단계 상한 1단계).

    Returns:
        RuleGoldResult.  en_recall은 채점 대상 항목에 대한 비율이고, ko_accuracy는
        찾은 항목에 대한 비율이다.
    """
    system_ko: dict[str, list[str]] = {}
    for pair in pairs:
        system_ko.setdefault(normalize_en_term(pair.en_term), []).append(pair.ko_term)

    excluded = frozenset(normalize_en_term(term) for term in official_excluded)
    boundary = _rank_cut_boundary(en_doc_freq, min_cooccur, max_en_terms)

    outcomes: list[EntryOutcome] = []
    for entry in entries:
        normalized_en = normalize_en_term(entry.en)
        offered = system_ko.get(normalized_en)
        found = offered is not None

        if found:
            gold_ko = {normalize_ko_term(ko) for ko in entry.ko}
            ko_match = any(normalize_ko_term(ko) in gold_ko for ko in offered)
            reach = FOUND  # 찾았으면 닿는 범위를 따지지 않는다
        else:
            ko_match = None
            reach = _classify_reach(
                normalized_en,
                excluded=excluded,
                en_doc_freq=en_doc_freq,
                max_n=max_n,
                min_cooccur=min_cooccur,
                boundary=boundary,
            )

        outcomes.append(
            EntryOutcome(
                en=entry.en,
                normalized_en=normalized_en,
                source=entry.source,
                found=found,
                reach=reach,
                ko_gold=entry.ko,
                ko_system=tuple(offered or ()),
                ko_match=ko_match,
                symbol_mangled=_is_symbol_mangled(entry.en, normalized_en),
            )
        )

    scored = [o for o in outcomes if o.reach in (FOUND, MISSED)]
    found_count = sum(1 for o in scored if o.found)
    ko_correct = sum(1 for o in scored if o.ko_match)

    unreachable: Counter[str] = Counter(
        o.reach for o in outcomes if o.reach not in (FOUND, MISSED)
    )

    by_source: dict[str, dict[str, int]] = {}
    for outcome in scored:
        counts = by_source.setdefault(
            outcome.source, {"scored": 0, "found": 0, "ko_correct": 0}
        )
        counts["scored"] += 1
        counts["found"] += int(outcome.found)
        counts["ko_correct"] += int(bool(outcome.ko_match))

    return RuleGoldResult(
        outcomes=tuple(outcomes),
        gold_size=len(outcomes),
        scored_size=len(scored),
        found=found_count,
        ko_correct=ko_correct,
        en_recall=found_count / len(scored) if scored else 0.0,
        ko_accuracy=ko_correct / found_count if found_count else 0.0,
        unreachable=dict(unreachable),
        by_source=by_source,
    )


def main() -> None:
    """현재 자산을 정답셋으로 채점해 두 장의 성적표를 낸다.

    사용법:  python -m rule_gold_eval [--corpus-root PATH] [--assets DIR]
    """
    import argparse
    import os

    from corpus_split import load_clean_corpus
    from glossary_guard import load_flat_glossary

    parser = argparse.ArgumentParser(description="룰 용어 정답셋 채점")
    parser.add_argument(
        "--corpus-root",
        default=os.environ.get("CORPUS_ROOT"),
        help="netrunner-cards-json 루트 (기본값: CORPUS_ROOT 환경변수)",
    )
    parser.add_argument("--assets", default="assets", help="glossary.json이 있는 디렉터리")
    parser.add_argument("--gold", default="data/rule_terms_gold.json")
    args = parser.parse_args()

    entries = load_rule_gold(args.gold)
    glossary = json.loads(
        (Path(args.assets) / "glossary.json").read_text(encoding="utf-8")
    )

    en_doc_freq = None
    if args.corpus_root:
        en_doc_freq = en_document_frequency(load_clean_corpus(Path(args.corpus_root)))

    rule_result = evaluate_rule_gold(
        entries,
        pairs_from_mapping(glossary["extracted"]),
        en_doc_freq=en_doc_freq,
        official_excluded=set(glossary["official"]),
    )
    print("=== 룰 추출 경로 (glossary.json extracted) ===")
    print(rule_result.report())

    # 납품 뷰는 가드레일이 쓰는 것과 같아야 한다 — 우선순위(공식 > 부제 > 룰)를
    # 여기서 따로 정하지 않고 glossary_guard.load_flat_glossary를 그대로 쓴다.
    flat, _ = load_flat_glossary(Path(args.assets) / "glossary.json")
    delivered_result = evaluate_rule_gold(
        entries,
        pairs_from_mapping({en: ko for en, (ko, _section) in flat.items()}),
        en_doc_freq=en_doc_freq,
    )
    print("\n=== 납품 용어집 전체 (공식+부제+룰) ===")
    print(delivered_result.report())


if __name__ == "__main__":
    main()
