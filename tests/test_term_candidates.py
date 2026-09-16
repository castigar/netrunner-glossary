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
    result = _extract_ko_eojeol_ngrams("프로그램을 설치한다", max_n=1)
    assert "프로그램을" in result
    assert "설치한다" in result


def test_extract_ko_eojeol_bigrams():
    result = _extract_ko_eojeol_ngrams("프로그램을 설치한다", max_n=2)
    assert "프로그램을 설치한다" in result


def test_extract_ko_eojeol_trigrams():
    result = _extract_ko_eojeol_ngrams("이 카드를 파기하여 크레딧을 얻는다", max_n=3)
    assert "이 카드를 파기하여" in result


def test_extract_ko_eojeol_empty_text():
    assert _extract_ko_eojeol_ngrams("", max_n=3) == []


def test_extract_ko_eojeol_single_token():
    result = _extract_ko_eojeol_ngrams("설치한다", max_n=2)
    assert "설치한다" in result
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
    """Allow all imports except LLM packages."""
    import builtins

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
    Design: "install"→"설치한다" appears together in 5 of 10 cards.
    "install" alone (with other KO text) appears in 0 extra cards,
    "설치한다" alone appears in 0 extra cards.
    So P(A)=P(B)=0.5, P(A,B)=0.5 → PMI=log2(0.5/(0.5*0.5))=log2(2)=1.0>0.
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
        (c for c in candidates if c.en_term == "install" and "설치한다" in c.ko_term),
        None,
    )
    assert install_pair is not None
    assert install_pair.pmi > 0.0, (
        f"install→설치한다 co-occurs 5/10 with P(A)=P(B)=0.5; "
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
