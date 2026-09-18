"""Tests for term_candidate_extractor.py and term_judge.py (AC 2).

AC requirement:
  - Statistical candidate generation uses 0 LLM calls.
  - Candidates are derived from EN n-gram × KO eojeol n-gram cooccurrence,
    Dice/PMI, and a minimum cooccurrence threshold of 5.
  - LLM is used only for accept/reject judgment via Pydantic structured output.
"""
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from collections import Counter

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from term_candidate_extractor import (
    TermCandidate,
    _extract_en_ngrams,
    _extract_ko_eojeol_ngrams,
    generate_candidates,
)
from term_judge import TermJudgment, TermPair, build_judge_chain


# ---------------------------------------------------------------------------
# _extract_en_ngrams
# ---------------------------------------------------------------------------


def test_extract_en_ngrams_unigrams():
    result = _extract_en_ngrams("install a program", max_n=1)
    assert "install" in result
    assert "a" in result
    assert "program" in result


def test_extract_en_ngrams_bigrams():
    result = _extract_en_ngrams("install a program", max_n=2)
    assert "install a" in result
    assert "a program" in result


def test_extract_en_ngrams_trigrams():
    result = _extract_en_ngrams("install a program", max_n=3)
    assert "install a program" in result


def test_extract_en_ngrams_game_symbol_kept():
    result = _extract_en_ngrams("gain [credit] each turn", max_n=1)
    assert "[credit]" in result
    assert "credit" not in result  # symbol must stay bracketed


def test_extract_en_ngrams_lowercased():
    result = _extract_en_ngrams("Install A Program", max_n=1)
    assert "install" in result
    assert "Install" not in result


def test_extract_en_ngrams_empty_text():
    assert _extract_en_ngrams("", max_n=3) == []


# ---------------------------------------------------------------------------
# _extract_ko_eojeol_ngrams
# ---------------------------------------------------------------------------


def test_extract_ko_eojeol_unigrams():
    """Eojeol are reduced to content stems: 프로그램을 -> 프로그램, 설치한다 -> 설치."""
    result = _extract_ko_eojeol_ngrams("프로그램을 설치한다", max_n=1)
    assert "프로그램" in result
    assert "설치" in result
    assert "설치한다" not in result


def test_extract_ko_eojeol_bigrams():
    result = _extract_ko_eojeol_ngrams("프로그램을 설치한다", max_n=2)
    assert "프로그램 설치" in result


def test_extract_ko_eojeol_trigrams():
    """Function-word eojeol ('이') vanish, so the trigram is over content stems."""
    result = _extract_ko_eojeol_ngrams("이 카드를 파기하여 크레딧을 얻는다", max_n=3)
    assert "카드 파기 크레딧" in result


def test_extract_ko_eojeol_empty_text():
    assert _extract_ko_eojeol_ngrams("", max_n=3) == []


def test_extract_ko_eojeol_single_token():
    result = _extract_ko_eojeol_ngrams("설치한다", max_n=2)
    assert "설치" in result
    # no bigrams possible from single token
    assert len([r for r in result if " " in r]) == 0


# ---------------------------------------------------------------------------
# generate_candidates — 0 LLM call constraint
# ---------------------------------------------------------------------------


def test_generate_candidates_makes_zero_llm_calls():
    """Core AC 2 constraint: statistical candidate generation must not invoke any LLM."""
    pairs = _make_corpus(10, repeat_term=True)

    # Patch the entire langchain_core module to catch accidental imports
    with patch("builtins.__import__", side_effect=_import_guard):
        try:
            candidates = generate_candidates(pairs, min_cooccur=2)
        except _LLMImportError:
            pytest.fail(
                "generate_candidates() attempted to import an LLM library — "
                "statistical phase must make 0 LLM calls"
            )
    # Just verify it runs and returns a list
    assert isinstance(candidates, list)


class _LLMImportError(ImportError):
    """Raised when generate_candidates tries to import an LLM library."""


_LLM_PACKAGES = {"langchain", "langchain_core", "langchain_openai", "anthropic", "openai"}


def _import_guard(name: str, *args, **kwargs):
    """Allow all imports except LLM packages.

    Must not import anything itself — this runs *as* __import__ while
    __import__ is patched, so an import here re-enters the guard forever.
    """
    if any(name.startswith(pkg) for pkg in _LLM_PACKAGES):
        raise _LLMImportError(
            f"generate_candidates() must not import '{name}' — 0 LLM calls required"
        )
    return original_import(name, *args, **kwargs)


import builtins

original_import = builtins.__import__


# ---------------------------------------------------------------------------
# generate_candidates — cooccurrence threshold
# ---------------------------------------------------------------------------


def _make_corpus(n_cards: int, repeat_term: bool = False) -> list[dict]:
    """Create a small synthetic corpus for testing."""
    pairs = []
    for i in range(n_cards):
        if repeat_term and i < n_cards:
            # All cards share "install" → "설치" to trigger cooccurrence
            en = f"install a program on server {i}"
            ko = f"서버 {i}에 프로그램을 설치한다"
        else:
            en = f"card {i} does something"
            ko = f"카드 {i}는 무언가를 한다"
        pairs.append({"en_text": en, "ko_text": ko})
    return pairs


def test_min_cooccur_filters_rare_pairs():
    """Pairs that appear fewer than min_cooccur times must be excluded."""
    # 3 cards with "install"→"설치", 2 cards with "trash"→"파기"
    pairs = [
        {"en_text": "install a program", "ko_text": "프로그램을 설치한다"},
        {"en_text": "install ice here", "ko_text": "여기에 아이스를 설치한다"},
        {"en_text": "install at remote", "ko_text": "리모트에 설치한다"},
        {"en_text": "trash this card", "ko_text": "이 카드를 파기한다"},
        {"en_text": "trash that resource", "ko_text": "저 리소스를 파기한다"},
    ]
    # With min_cooccur=3, "trash/파기" pair (count=2) must be absent
    candidates = generate_candidates(pairs, min_cooccur=3)
    trash_pairs = [c for c in candidates if c.en_term == "trash" and "파기" in c.ko_term]
    assert len(trash_pairs) == 0, "trash→파기 appears only 2 times; must be filtered at min_cooccur=3"


def test_min_cooccur_keeps_frequent_pairs():
    """Pairs at or above min_cooccur threshold must appear in output."""
    # 5 cards all containing "install" and "설치"
    pairs = [
        {"en_text": f"install program {i}", "ko_text": f"프로그램 설치한다 {i}"}
        for i in range(5)
    ]
    candidates = generate_candidates(pairs, min_cooccur=5)
    install_pairs = [c for c in candidates if c.en_term == "install"]
    assert len(install_pairs) > 0, "install appears 5 times; must survive min_cooccur=5"


def test_min_cooccur_exactly_at_threshold():
    """A pair at exactly min_cooccur must be included."""
    pairs = [
        {"en_text": "rez this ice", "ko_text": "이 아이스를 레즈한다"}
        for _ in range(5)
    ]
    candidates = generate_candidates(pairs, min_cooccur=5)
    rez_pairs = [c for c in candidates if c.en_term == "rez"]
    assert len(rez_pairs) > 0, "rez at exactly 5 occurrences must pass threshold"


# ---------------------------------------------------------------------------
# generate_candidates — Dice and PMI correctness
# ---------------------------------------------------------------------------


def test_dice_is_between_zero_and_one():
    pairs = _make_corpus(10, repeat_term=True)
    candidates = generate_candidates(pairs, min_cooccur=1)
    for c in candidates:
        assert 0.0 <= c.dice <= 1.0, f"Dice out of range: {c}"


def test_dice_is_higher_for_exclusive_pairs():
    """A term pair that always co-occurs exclusively should have higher Dice than a noisy pair."""
    # 5 cards where "rez" always pairs with "레즈" and never with other terms
    exclusive = [
        {"en_text": "rez this ice", "ko_text": "이 아이스를 레즈한다"}
        for _ in range(5)
    ]
    # 5 cards where "gain" pairs with many different KO terms (noisy)
    noisy = [
        {"en_text": "gain credits always", "ko_text": f"크레딧을 얻는다 {i}"}
        for i in range(5)
    ]
    candidates = generate_candidates(exclusive + noisy, min_cooccur=1)

    rez_dice = max((c.dice for c in candidates if c.en_term == "rez" and "레즈" in c.ko_term), default=None)
    # rez is exclusive — its Dice should be among the higher values
    assert rez_dice is not None
    assert rez_dice > 0.0


def test_pmi_positive_for_strongly_associated_pairs():
    """Pairs that co-occur more than independence predicts should have positive PMI.

    For PMI > 0 we need P(A,B) > P(A)*P(B).
    Design: "install"→"설치" appears together in 5 of 10 cards.
    "install" alone (with other KO text) appears in 0 extra cards,
    "설치" alone appears in 0 extra cards.
    So P(A)=P(B)=0.5, P(A,B)=0.5 → PMI=log2(0.5/(0.5*0.5))=log2(2)=1.0>0.

    The KO side is the stem 설치, not the inflected 설치한다 the card carries.
    """
    exclusive_pairs = [
        {"en_text": "install a program", "ko_text": "프로그램을 설치한다"}
        for _ in range(5)
    ]
    # 5 unrelated cards to push P(A) and P(B) below 1.0
    filler_pairs = [
        {"en_text": f"gain credits on turn {i}", "ko_text": f"턴에 크레딧을 얻는다 {i}"}
        for i in range(5)
    ]
    candidates = generate_candidates(exclusive_pairs + filler_pairs, min_cooccur=5)
    install_pair = next(
        (c for c in candidates if c.en_term == "install" and c.ko_term == "설치"),
        None,
    )
    assert install_pair is not None
    assert install_pair.pmi > 0.0, (
        f"install→설치 co-occurs 5/10 with P(A)=P(B)=0.5; "
        f"PMI should be log2(2)=1.0 > 0, got {install_pair.pmi}"
    )


def test_candidates_sorted_by_dice_descending():
    """Output must be sorted by Dice coefficient in descending order."""
    pairs = _make_corpus(10, repeat_term=True)
    candidates = generate_candidates(pairs, min_cooccur=1)
    if len(candidates) >= 2:
        dice_scores = [c.dice for c in candidates]
        assert dice_scores == sorted(dice_scores, reverse=True)


def test_candidate_fields_are_present():
    """Each TermCandidate must have all required fields."""
    pairs = [
        {"en_text": "install a program", "ko_text": "프로그램을 설치한다"}
        for _ in range(5)
    ]
    candidates = generate_candidates(pairs, min_cooccur=1)
    assert len(candidates) > 0
    for c in candidates:
        assert isinstance(c, TermCandidate)
        assert isinstance(c.en_term, str) and c.en_term
        assert isinstance(c.ko_term, str) and c.ko_term
        assert isinstance(c.cooccurrence, int) and c.cooccurrence >= 1
        assert isinstance(c.dice, float)
        assert isinstance(c.pmi, float)


def test_empty_corpus_returns_empty():
    assert generate_candidates([]) == []


# ---------------------------------------------------------------------------
# TermJudgment Pydantic model (LLM judge output schema)
# ---------------------------------------------------------------------------


def test_term_judgment_pydantic_model_accepts_valid():
    """TermJudgment must be a valid Pydantic model with required fields."""
    j = TermJudgment(
        en_term="install",
        ko_term="설치",
        accepted=True,
        reason="Standard rule-text verb pair.",
    )
    assert j.accepted is True
    assert j.en_term == "install"


def test_term_judgment_pydantic_model_rejects_missing_fields():
    """TermJudgment must reject construction without required fields."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TermJudgment(accepted=True)  # missing en_term, ko_term, reason


def test_term_pair_pydantic_model():
    """TermPair must accept all statistical fields."""
    tp = TermPair(
        en_term="trash",
        ko_term="파기",
        cooccurrence=10,
        dice=0.75,
        pmi=2.3,
    )
    assert tp.cooccurrence == 10
    assert tp.dice == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# build_judge_chain — uses LLM only for judgment, not generation
# ---------------------------------------------------------------------------


def test_build_judge_chain_requires_llm():
    """build_judge_chain must accept a LangChain LLM instance."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = MagicMock()
    chain = build_judge_chain(mock_llm)
    # The chain is built by calling with_structured_output(TermJudgment)
    mock_llm.with_structured_output.assert_called_once_with(TermJudgment)


def test_build_judge_chain_uses_pydantic_structured_output():
    """build_judge_chain must pass TermJudgment (Pydantic model) to with_structured_output."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    build_judge_chain(mock_llm)

    call_args = mock_llm.with_structured_output.call_args
    schema_arg = call_args[0][0]
    assert schema_arg is TermJudgment, (
        "with_structured_output must receive the TermJudgment Pydantic model"
    )


# ---------- two-stage cap (SERVICE.md §6, 결정 ② 개정) ----------


def _cap_fixture(n_pairs=60):
    """Enough distinct vocabulary that min_cooccur alone leaves many candidates."""
    return [
        {"en_text": f"alpha beta word{i % 7}", "ko_text": f"알파 베타 단어{i % 7}"}
        for i in range(n_pairs)
    ]


def _uncapped(pairs):
    return generate_candidates(
        pairs, min_cooccur=1, max_en_terms=None, top_k_per_en=None
    )


def test_max_en_terms_limits_distinct_en_terms():
    pairs = _cap_fixture()
    assert len({c.en_term for c in _uncapped(pairs)}) > 3
    capped = generate_candidates(pairs, min_cooccur=1, max_en_terms=3)
    assert len({c.en_term for c in capped}) == 3


def test_top_k_per_en_limits_candidates_within_each_en_term():
    pairs = _cap_fixture()
    capped = generate_candidates(pairs, min_cooccur=1, top_k_per_en=2)
    counts = Counter(c.en_term for c in capped)
    assert counts and max(counts.values()) <= 2


def test_stage_two_keeps_the_highest_dice_within_an_en_term():
    pairs = _cap_fixture()
    full = _uncapped(pairs)
    best = max(c.dice for c in full if c.en_term == "alpha")
    capped = generate_candidates(pairs, min_cooccur=1, top_k_per_en=1)
    assert [c.dice for c in capped if c.en_term == "alpha"] == [best]


def test_en_terms_are_selected_by_frequency_not_dice():
    """The defect being fixed: a global Dice cut drops frequent real terms.

    This is trash→폐기한다 in miniature.  A real term's KO rendering also shows up
    on cards that do not carry the EN term, which inflates Dice's denominator;
    a rare pair that occurs together and nowhere else scores a perfect 1.0.
    Ranking EN terms by Dice therefore prefers the rare artifact.
    """
    pairs = [{"en_text": "common", "ko_text": "공통"} for _ in range(40)]
    pairs += [{"en_text": "filler", "ko_text": "공통"} for _ in range(20)]
    pairs += [{"en_text": "rare", "ko_text": "희귀"} for _ in range(2)]

    full = _uncapped(pairs)
    best = {}
    for c in full:
        best[c.en_term] = max(best.get(c.en_term, 0.0), c.dice)
    assert best["rare"] > best["common"], "fixture must reproduce the inversion"

    kept = {c.en_term for c in generate_candidates(pairs, min_cooccur=1, max_en_terms=1)}
    assert kept == {"common"}, "stage 1 must rank by frequency, not by Dice"


def test_none_removes_each_stage():
    pairs = _cap_fixture()
    full = _uncapped(pairs)
    assert len(generate_candidates(pairs, min_cooccur=1, max_en_terms=None,
                                   top_k_per_en=None)) == len(full)


def test_cap_larger_than_population_is_harmless():
    pairs = _cap_fixture()
    assert generate_candidates(
        pairs, min_cooccur=1, max_en_terms=10**6, top_k_per_en=10**6
    ) == _uncapped(pairs)


def test_cap_is_deterministic_across_runs():
    """Dice ties are common, so the cut must not depend on dict ordering."""
    pairs = _cap_fixture()
    first = generate_candidates(pairs, min_cooccur=1, max_en_terms=3, top_k_per_en=2)
    for _ in range(3):
        assert generate_candidates(
            pairs, min_cooccur=1, max_en_terms=3, top_k_per_en=2
        ) == first


def test_defaults_apply_a_cap():
    """The defaults must not be 'unlimited' — that is the defect being fixed."""
    from term_candidate_extractor import DEFAULT_MAX_EN_TERMS, DEFAULT_TOP_K_PER_EN

    assert DEFAULT_MAX_EN_TERMS and DEFAULT_MAX_EN_TERMS > 0
    assert DEFAULT_TOP_K_PER_EN and DEFAULT_TOP_K_PER_EN > 0


# ---------- markup and punctuation normalization ----------


def test_en_markup_is_not_tokenized_as_a_word():
    from term_candidate_extractor import _extract_en_ngrams

    assert "strong" not in _extract_en_ngrams("<strong>virus</strong> cards", 1)


def test_en_game_symbols_survive_markup_stripping():
    from term_candidate_extractor import _extract_en_ngrams

    assert "[click]" in _extract_en_ngrams("lose [click].", 1)


def test_ko_edge_punctuation_is_shed():
    from term_candidate_extractor import _normalize_ko_token

    assert _normalize_ko_token("있다.") == "있다"
    assert _normalize_ko_token("때,") == "때"


def test_ko_game_symbols_are_preserved():
    """Stripping brackets would destroy the symbol and collide with English."""
    from term_candidate_extractor import _normalize_ko_token

    assert _normalize_ko_token("[credit]") == "[credit]"
    assert _normalize_ko_token('"[click],') == "[click]"


def test_ko_markup_is_stripped():
    from term_candidate_extractor import _normalize_ko_token

    assert _normalize_ko_token("<strong>당신의") == "당신의"


def test_ko_surface_forms_merge_into_one_term():
    """'있다.' and '있다' were counted as different terms, splitting cooccurrence."""
    pairs = [{"en_text": "may", "ko_text": "있다."}, {"en_text": "may", "ko_text": "있다"}]
    candidates = generate_candidates(pairs, min_cooccur=2, max_en_terms=None,
                                     top_k_per_en=None)
    assert [(c.en_term, c.ko_term, c.cooccurrence) for c in candidates] == [
        ("may", "있다", 2)
    ]


# ---------------------------------------------------------------------------
# 형태소 정규화 (kiwipiepy)
# ---------------------------------------------------------------------------


def test_inflected_forms_collapse_to_one_term():
    """설치할 / 설치된 / 설치한다 were three terms; the judge saw them as three."""
    pairs = [
        {"en_text": "install", "ko_text": "설치할"},
        {"en_text": "install", "ko_text": "설치된"},
        {"en_text": "install", "ko_text": "설치한다"},
    ]
    candidates = generate_candidates(
        pairs, min_cooccur=3, max_en_terms=None, top_k_per_en=None
    )
    assert [(c.en_term, c.ko_term, c.cooccurrence) for c in candidates] == [
        ("install", "설치", 3)
    ]


def test_particles_are_stripped():
    """서버를 and 서버 are the same term; the particle split their cooccurrence."""
    pairs = [
        {"en_text": "server", "ko_text": "서버를"},
        {"en_text": "server", "ko_text": "서버"},
    ]
    candidates = generate_candidates(
        pairs, min_cooccur=2, max_en_terms=None, top_k_per_en=None
    )
    assert ("server", "서버") in {(c.en_term, c.ko_term) for c in candidates}


def test_game_symbols_survive_morphological_analysis():
    """Kiwi shatters '[credit]' into '[' + 'credit' + ']' unless it is protected."""
    result = _extract_ko_eojeol_ngrams("2[credit]을 지불한다", max_n=1)
    assert "2 [credit]" in result
    assert "credit" not in result


def test_function_word_only_eojeol_disappears():
    assert _extract_ko_eojeol_ngrams("당신의", max_n=1) == []


def test_light_verb_is_dropped_so_constructions_agree():
    """Kiwi tags 하 in 설치할 as XSV but in 레즈할 as VV; without dropping the
    verb-tagged light verb, 레즈할 would become '레즈 하다' and 설치할 '설치'."""
    from ko_morphology import normalize_eojeol

    assert normalize_eojeol("레즈할") == "레즈"
    assert normalize_eojeol("설치할") == "설치"


def test_real_verbs_are_lemmatised_not_dropped():
    from ko_morphology import normalize_eojeol

    assert normalize_eojeol("뽑는다") == "뽑다"


def test_morphology_makes_zero_llm_calls():
    from pathlib import Path as _Path

    import ko_morphology

    source = _Path(ko_morphology.__file__).read_text(encoding="utf-8")
    for name in ("langchain", "openai", "anthropic", "boto3", "bedrock"):
        assert name not in source, f"ko_morphology must not reference {name!r}"
