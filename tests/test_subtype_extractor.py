"""Tests for subtype_extractor.py — 부제 추출 경로 (SERVICE.md §6, 결정 ①).

The corpus-backed tests pin the numbers SERVICE.md §6 states as fact:
미번역 필터 후 부제 충돌은 12/80 = 15%다. 필터를 빼면 47/87 = 54%로 뛴다.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conflict_detector import detect_conflicts  # noqa: E402
from corpus_split import load_clean_corpus  # noqa: E402
from subtype_extractor import (  # noqa: E402
    SubtypePair,
    extract_subtype_pairs,
    to_term_pairs,
)
from term_extraction_eval import evaluate_extraction, load_gold_subtypes  # noqa: E402

CORPUS_ROOT = os.environ.get("CORPUS_ROOT", "")
needs_corpus = pytest.mark.skipif(
    not CORPUS_ROOT or not Path(CORPUS_ROOT).is_dir(),
    reason="CORPUS_ROOT not set or does not exist",
)


def card(cid, en, ko):
    return {"id": cid, "en_keywords": en, "ko_keywords": ko}


# ---------- positional alignment ----------


def test_aligns_by_position():
    r = extract_subtype_pairs([card("c1", ["Location", "Seedy"], ["장소", "지저분함"])])
    assert {(p.en_term, p.ko_term) for p in r.pairs} == {
        ("location", "장소"),
        ("seedy", "지저분함"),
    }


def test_en_term_is_lowercased_ko_term_is_not():
    r = extract_subtype_pairs([card("c1", ["Code Gate"], ["코드 게이트"])])
    assert r.pairs[0].en_term == "code gate"
    assert r.pairs[0].ko_term == "코드 게이트"


def test_length_mismatch_skips_whole_card():
    """A partial alignment would manufacture wrong pairs, so drop the card."""
    r = extract_subtype_pairs(
        [card("c1", ["Location", "Seedy", "Extra"], ["장소", "지저분함"])]
    )
    assert r.pairs == []
    assert r.cards_skipped_length_mismatch == 1
    assert r.cards_with_keywords == 1


def test_length_mismatch_does_not_affect_other_cards():
    r = extract_subtype_pairs(
        [
            card("c1", ["Location", "Extra"], ["장소"]),
            card("c2", ["Barrier"], ["방벽"]),
        ]
    )
    assert {(p.en_term, p.ko_term) for p in r.pairs} == {("barrier", "방벽")}
    assert r.cards_skipped_length_mismatch == 1


# ---------- untranslated filter ----------


def test_untranslated_ko_element_is_dropped():
    r = extract_subtype_pairs([card("c1", ["Ambush", "Barrier"], ["Ambush", "방벽"])])
    assert {(p.en_term, p.ko_term) for p in r.pairs} == {("barrier", "방벽")}
    assert r.elements_dropped_untranslated == 1


def test_untranslated_residue_would_otherwise_look_like_a_conflict():
    """This is the 54% -> 15% filter: English residue masquerades as a variant."""
    cards = [
        card("c1", ["Barrier"], ["방벽"]),
        card("c2", ["Barrier"], ["Barrier"]),
    ]
    r = extract_subtype_pairs(cards)
    result = detect_conflicts(to_term_pairs(r.pairs))
    assert result.conflicts == []
    assert result.clean == {"barrier": "방벽"}


# ---------- absent keywords ----------


def test_card_without_keywords_is_not_counted():
    r = extract_subtype_pairs(
        [card("c1", [], []), {"id": "c2"}, card("c3", ["Barrier"], ["방벽"])]
    )
    assert r.cards_with_keywords == 1
    assert len(r.pairs) == 1


def test_empty_input():
    r = extract_subtype_pairs([])
    assert r.pairs == []
    assert r.cards_with_keywords == 0


# ---------- aggregation ----------


def test_same_pair_from_several_cards_collapses_and_keeps_card_ids():
    r = extract_subtype_pairs(
        [card("c1", ["Barrier"], ["방벽"]), card("c2", ["Barrier"], ["방벽"])]
    )
    assert len(r.pairs) == 1
    assert r.pairs[0].card_count == 2
    assert set(r.pairs[0].card_ids) == {"c1", "c2"}


def test_competing_ko_variants_stay_separate_pairs():
    r = extract_subtype_pairs(
        [card("c1", ["Barrier"], ["방벽"]), card("c2", ["Barrier"], ["장벽"])]
    )
    assert len(r.pairs) == 2
    assert detect_conflicts(to_term_pairs(r.pairs)).conflicts[0].ko_variants == [
        "방벽",
        "장벽",
    ]


def test_pairs_are_sorted_deterministically():
    cards = [card("c1", ["Seedy", "Barrier"], ["지저분함", "방벽"])]
    assert [(p.en_term, p.ko_term) for p in extract_subtype_pairs(cards).pairs] == [
        ("barrier", "방벽"),
        ("seedy", "지저분함"),
    ]


def test_to_term_pairs_expands_one_dict_per_card():
    pairs = [SubtypePair("barrier", "방벽", ("c1", "c2"))]
    assert to_term_pairs(pairs) == [
        {"en_term": "barrier", "ko_term": "방벽", "card_id": "c1"},
        {"en_term": "barrier", "ko_term": "방벽", "card_id": "c2"},
    ]


def test_zero_llm_calls():
    """부제 경로도 룰 경로와 같이 LLM을 한 번도 호출하지 않는다."""
    import subtype_extractor

    source = Path(subtype_extractor.__file__).read_text(encoding="utf-8")
    for name in ("langchain", "openai", "anthropic", "boto3", "bedrock"):
        assert name not in source, f"subtype_extractor must not reference {name!r}"


# ---------- corpus-backed regression gates ----------


@pytest.fixture(scope="module")
def corpus():
    return load_clean_corpus(Path(CORPUS_ROOT))


@needs_corpus
def test_corpus_card_coverage(corpus):
    r = extract_subtype_pairs(corpus)
    assert r.cards_with_keywords == 801
    assert r.cards_skipped_length_mismatch == 19


@needs_corpus
def test_corpus_conflict_rate_is_15_percent(corpus):
    """SERVICE.md §6: 필터 후 부제 충돌은 12/80 = 15%."""
    r = extract_subtype_pairs(corpus)
    result = detect_conflicts(to_term_pairs(r.pairs))
    en_terms = {p.en_term for p in r.pairs}
    assert len(en_terms) == 80
    assert len(result.conflicts) == 12
    assert len(result.conflicts) / len(en_terms) == pytest.approx(0.15, abs=0.01)


@needs_corpus
def test_corpus_conflicts_are_real_translation_variants(corpus):
    """Not n-gram noise: every variant is a whole subtype rendered two ways."""
    r = extract_subtype_pairs(corpus)
    conflicts = detect_conflicts(to_term_pairs(r.pairs)).conflicts
    assert {e.en_term for e in conflicts} == {
        "barrier",
        "code gate",
        "connection",
        "initiative",
        "location",
        "priority",
        "public",
        "sentry",
        "stealth",
        "tracer",
        "virtual",
        "weapon",
    }


@needs_corpus
def test_corpus_precision_and_recall_against_88_gold(corpus):
    """관찰 지표: 88항목 정답셋 직접 채점 (SERVICE.md §5)."""
    gold = load_gold_subtypes(
        Path(CORPUS_ROOT) / "v2" / "translations" / "ko" / "card_subtypes.json"
    )
    assert len(gold) == 88
    r = extract_subtype_pairs(corpus)
    ev = evaluate_extraction(r.pairs, gold)
    # Measured 1.000 / 0.909. Kept tight on purpose: a loose band here would let
    # a regression in the alignment or the untranslated filter pass unnoticed.
    assert ev.precision >= 0.99, ev.report()
    assert ev.recall >= 0.90, ev.report()
