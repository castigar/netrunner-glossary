"""Tests for tm_index.py — Hybrid TM index (BM25 + dense, AC 7).

AC requirement: TM search must use BM25 and dense embeddings together
(hybrid).  Card text is short and contains many proper nouns, so BM25
exact-match contribution is significant.

Tests verify:
- Helper functions (_tokenize, _rrf)
- HybridTMIndex.build() / .search() contract
- Both BM25 and dense signals contribute independently
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tm_index import HybridTMIndex, _tokenize, _rrf


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


def test_tokenize_basic_words():
    assert _tokenize("install a program") == ["install", "a", "program"]


def test_tokenize_game_symbol_kept_intact():
    tokens = _tokenize("gain [credit] each turn")
    assert "[credit]" in tokens
    assert "credit" not in tokens  # symbol must stay bracketed


def test_tokenize_multiple_symbols():
    tokens = _tokenize("[click]: install [program]")
    assert "[click]" in tokens
    assert "[program]" in tokens


def test_tokenize_lowercases():
    assert _tokenize("Install") == ["install"]


def test_tokenize_empty_string():
    assert _tokenize("") == []


def test_tokenize_punctuation_stripped():
    tokens = _tokenize("end the run.")
    assert "run." not in tokens
    assert "run" in tokens


# ---------------------------------------------------------------------------
# _rrf
# ---------------------------------------------------------------------------


def test_rrf_top_of_both_rankings_wins():
    # doc 0 is top in both → should be first
    result = _rrf([[0, 1, 2], [0, 2, 1]])
    assert result[0][0] == 0


def test_rrf_first_in_both_wins():
    # doc 1 is first in both rankings → highest RRF score
    result = _rrf([[1, 0, 2], [1, 2, 0]])
    assert result[0][0] == 1


def test_rrf_scores_are_descending():
    result = _rrf([[0, 1, 2], [0, 1, 2]])
    scores = [r[1] for r in result]
    assert scores == sorted(scores, reverse=True)


def test_rrf_all_docs_present():
    rankings = [[2, 0, 1], [1, 2, 0]]
    result = _rrf(rankings)
    assert {r[0] for r in result} == {0, 1, 2}


# ---------------------------------------------------------------------------
# HybridTMIndex fixture data
# ---------------------------------------------------------------------------

SAMPLE_RECORDS = [
    {
        "id": "card_a",
        "en_text": "install a program on a server",
        "ko_text": "서버에 프로그램을 설치한다",
    },
    {
        "id": "card_b",
        "en_text": "trash this card to gain 3 credits",
        "ko_text": "이 카드를 파기하여 크레딧 3을 얻는다",
    },
    {
        "id": "card_c",
        "en_text": "rez this ice paying its rez cost",
        "ko_text": "레즈 비용을 지불하여 이 아이스를 레즈한다",
    },
    {
        "id": "card_d",
        "en_text": "gain [click] for each program installed",
        "ko_text": "설치된 프로그램당 [클릭]을 얻는다",
    },
    {
        "id": "card_e",
        "en_text": "make a run on any server",
        "ko_text": "임의의 서버에 런을 한다",
    },
]


# ---------------------------------------------------------------------------
# HybridTMIndex.build
# ---------------------------------------------------------------------------


def test_build_sets_record_count():
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    assert len(idx) == len(SAMPLE_RECORDS)


def test_build_empty_raises_value_error():
    idx = HybridTMIndex()
    with pytest.raises(ValueError, match="empty"):
        idx.build([])


def test_search_before_build_raises_runtime_error():
    idx = HybridTMIndex()
    with pytest.raises(RuntimeError, match="build"):
        idx.search("install")


# ---------------------------------------------------------------------------
# HybridTMIndex.search — contract
# ---------------------------------------------------------------------------


def test_search_returns_at_most_k_results():
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("install program", k=3)
    assert len(results) <= 3


def test_search_result_has_all_required_fields():
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("install program", k=1)
    assert len(results) == 1
    r = results[0]
    for field in ("id", "en_text", "ko_text", "score", "bm25_rank", "dense_rank"):
        assert field in r, f"Missing field: {field}"


def test_search_result_ids_are_from_corpus():
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    valid_ids = {r["id"] for r in SAMPLE_RECORDS}
    results = idx.search("gain credits", k=5)
    for r in results:
        assert r["id"] in valid_ids


def test_search_scores_are_positive():
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("server run", k=3)
    for r in results:
        assert r["score"] > 0


def test_search_scores_are_descending():
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("install program server", k=5)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_k_larger_than_corpus_returns_all():
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("any query", k=100)
    assert len(results) == len(SAMPLE_RECORDS)


# ---------------------------------------------------------------------------
# HybridTMIndex.search — relevance
# ---------------------------------------------------------------------------


def test_bm25_exact_match_retrieved():
    """Exact keyword match (BM25 strength) must be in top results."""
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("trash this card", k=3)
    top_ids = [r["id"] for r in results]
    assert "card_b" in top_ids  # card_b has 'trash this card'


def test_install_query_retrieves_install_cards():
    """'install' query should retrieve card_a and/or card_d (both contain 'install')."""
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("install", k=3)
    top_ids = [r["id"] for r in results]
    assert any(cid in top_ids for cid in ("card_a", "card_d"))


def test_game_symbol_query_handled():
    """Queries with game symbols like [click] must not raise and must retrieve relevant cards."""
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("gain [click] program", k=3)
    assert len(results) > 0
    top_ids = [r["id"] for r in results]
    assert "card_d" in top_ids  # card_d has [click] and program


# ---------------------------------------------------------------------------
# AC 7 core: hybrid — both signals must contribute independently
# ---------------------------------------------------------------------------


def test_bm25_and_dense_ranks_differ_for_at_least_one_result():
    """The BM25 and dense rankings must diverge for at least one document,
    proving both signals produce independent rankings that are fused.

    Design: "rez" is a rare term (appears in 1 of 10 docs); "program" is
    common (appears in 8 of 10 docs).  For query "rez program":
    - BM25 weights "rez" heavily (high IDF) → doc "rez_only" ranks high
    - Dense BoW treats terms equally → doc "install_program_*" ranks higher
      because it matches the "program" term with better cosine proportion

    This IDF vs. uniform-weight asymmetry guarantees rank divergence.
    """
    idx = HybridTMIndex()
    # 8 docs with "install program" → program is very common (low BM25 IDF)
    common_records = [
        {"id": f"install_{i}", "en_text": "install a program", "ko_text": f"설치 {i}"}
        for i in range(8)
    ]
    # 1 doc with "rez" only → rez is rare (high BM25 IDF)
    rez_record = {"id": "rez_only", "en_text": "rez this ice", "ko_text": "레즈"}
    # 1 unrelated doc
    other_record = {"id": "other", "en_text": "gain credits each turn", "ko_text": "크레딧"}

    idx.build(common_records + [rez_record, other_record])

    # Query: "rez program"
    # BM25: prefers rez_only (high IDF for rare "rez")
    # Dense: prefers install_* (better cosine match on common "program" token)
    results = idx.search("rez program", k=10)
    has_divergence = any(r["bm25_rank"] != r["dense_rank"] for r in results)
    assert has_divergence, (
        "BM25 and dense rankings must differ for at least one result — "
        "rare-term IDF weighting (BM25) vs. uniform-weight cosine (dense) "
        "should produce different orderings"
    )


def test_bm25_rank_metadata_is_zero_based():
    """bm25_rank and dense_rank must be non-negative integers."""
    idx = HybridTMIndex()
    idx.build(SAMPLE_RECORDS)
    results = idx.search("install", k=5)
    for r in results:
        assert isinstance(r["bm25_rank"], int)
        assert isinstance(r["dense_rank"], int)
        assert r["bm25_rank"] >= 0
        assert r["dense_rank"] >= 0
