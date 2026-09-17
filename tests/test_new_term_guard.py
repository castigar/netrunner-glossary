"""test_new_term_guard.py — Tests for new term HITL guardrail (§4.3).

AC: 용어집에 없는 신규 용어를 임의로 창작하지 않으며 HITL 승인을 요구한다.
(Do not create new terms not in the glossary; require HITL approval.)

HITL trigger ①: 신규 EN 용어 발견 — EN source text has terms not in glossary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from new_term_guard import (  # noqa: E402
    NewTermCandidate,
    NewTermCheckResult,
    NewTermError,
    check_new_terms,
)

ASSETS_GLOSSARY = Path(__file__).parent.parent / "assets" / "glossary.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_glossary(**kwargs) -> dict:
    """Build a flat glossary dict: en_term -> (ko_term, section)."""
    return kwargs


def _simple_glossary() -> dict:
    return {
        "install": ("설치", "extracted"),
        "trash": ("폐기", "extracted"),
        "rez": ("레즈", "extracted"),
        "barrier": ("방벽", "official"),
        "subroutine": ("서브루틴", "extracted"),
        "end the run": ("런 종료", "extracted"),
        "bad publicity": ("악평", "extracted"),
        "ice": ("아이스", "official"),
        "program": ("프로그램", "extracted"),
    }


# ---------------------------------------------------------------------------
# check_new_terms — passing cases
# ---------------------------------------------------------------------------


def test_passes_when_all_terms_registered():
    """All content words are in the glossary → passed."""
    glossary = {
        "install": ("설치", "extracted"),
        "trash": ("폐기", "extracted"),
        "program": ("프로그램", "extracted"),
    }
    result = check_new_terms("Install a program and trash it.", glossary, llm_judged=True)
    assert result.passed
    assert result.new_terms == []


def test_passes_when_text_is_only_stopwords():
    """Text with no significant content words → passed."""
    glossary = {}
    result = check_new_terms("If this is used, make it so.", glossary, llm_judged=True)
    assert result.passed


def test_passes_when_text_is_empty():
    result = check_new_terms("", {}, llm_judged=True)
    assert result.passed
    assert result.new_terms == []


def test_passes_when_text_has_only_game_symbols():
    """Game symbols [credit] etc. are data, not terms."""
    glossary = {}
    result = check_new_terms("Gain [credit][credit][credit].", glossary, llm_judged=True)
    assert result.passed


def test_passes_on_known_game_terms():
    """rez, trash, install etc. present in glossary → passed."""
    glossary = _simple_glossary()
    result = check_new_terms(
        "Rez this ICE. Install a program. Trash it.",
        glossary,
        llm_judged=True,
    )
    assert result.passed


def test_passes_when_unigram_is_part_of_registered_multiword():
    """'bad' and 'publicity' are covered by the registered phrase 'bad publicity'."""
    glossary = {"bad publicity": ("악평", "extracted")}
    result = check_new_terms("Take 1 bad publicity.", glossary, llm_judged=True)
    assert result.passed


def test_passes_multiword_phrase_end_the_run():
    """'end' and 'run' are covered by 'end the run' registration."""
    glossary = {"end the run": ("런 종료", "extracted")}
    result = check_new_terms("End the run.", glossary, llm_judged=True)
    assert result.passed


# ---------------------------------------------------------------------------
# check_new_terms — violation cases
# ---------------------------------------------------------------------------


def test_detects_unregistered_game_term():
    """An EN term not in the glossary and not a stopword is flagged."""
    glossary = {"install": ("설치", "extracted")}
    # "frogify" is clearly a new game term not in glossary
    result = check_new_terms("Frogify a program.", glossary, llm_judged=True)
    assert not result.passed
    terms = [t.en_term for t in result.new_terms]
    assert "frogify" in terms


def test_detects_unregistered_rez():
    """'rez' not in glossary → flagged."""
    glossary = {"trash": ("폐기", "extracted")}
    result = check_new_terms("Rez this ICE.", glossary, llm_judged=True)
    assert not result.passed
    terms = [t.en_term for t in result.new_terms]
    assert "rez" in terms


def test_detects_multiple_unregistered_terms():
    """Multiple new terms in source are all flagged."""
    glossary = {}
    result = check_new_terms(
        "Frozzle and quibble the runner's ICE.",
        glossary,
        llm_judged=True,
    )
    assert not result.passed
    terms = [t.en_term for t in result.new_terms]
    assert "frozzle" in terms
    assert "quibble" in terms


def test_deduplicates_repeated_new_terms():
    """The same new term appearing multiple times is reported only once."""
    glossary = {}
    result = check_new_terms(
        "Zorp the runner. Zorp again.",
        glossary,
        llm_judged=True,
    )
    zorp_terms = [t for t in result.new_terms if t.en_term == "zorp"]
    assert len(zorp_terms) == 1


def test_case_insensitive_detection():
    """'REZ' and 'rez' and 'Rez' all map to the same token 'rez'."""
    glossary = {}
    result = check_new_terms("REZ this ICE.", glossary, llm_judged=True)
    terms = [t.en_term for t in result.new_terms]
    assert "rez" in terms


def test_registered_term_not_double_reported():
    """A term that IS in the glossary must not appear in new_terms."""
    glossary = {"rez": ("레즈", "extracted"), "install": ("설치", "extracted")}
    result = check_new_terms(
        "Rez this ICE. Install a newgadget.",
        glossary,
        llm_judged=True,
    )
    terms = [t.en_term for t in result.new_terms]
    assert "rez" not in terms
    assert "install" not in terms
    assert "newgadget" in terms


# ---------------------------------------------------------------------------
# Stopword filtering
# ---------------------------------------------------------------------------


def test_stopwords_never_flagged():
    """Common English function words are never reported as new terms."""
    glossary = {}
    stopword_sentence = "If you do this, then that is not the end."
    result = check_new_terms(stopword_sentence, glossary, llm_judged=True)
    assert result.passed


def test_short_words_below_min_length_not_flagged():
    """Words shorter than 3 characters are ignored."""
    glossary = {}
    result = check_new_terms("Do it if ok.", glossary, llm_judged=True)
    # "it" (2 chars), "if" (2 chars) → ignored
    assert result.passed


# ---------------------------------------------------------------------------
# llm_judged flag propagation
# ---------------------------------------------------------------------------


def test_llm_judged_false_propagated_when_passed():
    glossary = {"rez": ("레즈", "extracted"), "ice": ("아이스", "official")}
    result = check_new_terms("Rez this ICE.", glossary, llm_judged=False)
    assert result.passed
    assert result.llm_judged is False


def test_llm_judged_true_propagated_when_failed():
    glossary = {}
    result = check_new_terms("Frobbulate the runner.", glossary, llm_judged=True)
    assert not result.passed
    assert result.llm_judged is True


# ---------------------------------------------------------------------------
# NewTermCheckResult model
# ---------------------------------------------------------------------------


def test_result_is_pydantic_model():
    glossary = {}
    result = check_new_terms("Frobbulate.", glossary, llm_judged=True)
    assert isinstance(result, NewTermCheckResult)
    assert isinstance(result.new_terms[0], NewTermCandidate)


def test_new_terms_are_lowercase_normalized():
    """Tokens are stored in lowercase regardless of source capitalization."""
    glossary = {}
    result = check_new_terms("FROBBULATE the runner.", glossary, llm_judged=True)
    terms = [t.en_term for t in result.new_terms]
    assert "frobbulate" in terms
    assert "FROBBULATE" not in terms


# ---------------------------------------------------------------------------
# NewTermError — HITL blocking signal
# ---------------------------------------------------------------------------


def test_new_term_error_carries_result():
    glossary = {}
    result = check_new_terms("Frobbulate.", glossary, llm_judged=True)
    err = NewTermError(result)
    assert err.result is result


def test_new_term_error_message_names_terms():
    glossary = {}
    result = check_new_terms("Frobbulate.", glossary, llm_judged=True)
    err = NewTermError(result)
    assert "frobbulate" in str(err)


def test_new_term_error_inherits_exception():
    glossary = {}
    result = check_new_terms("Frobbulate.", glossary, llm_judged=True)
    assert isinstance(NewTermError(result), Exception)


def test_new_term_error_message_shows_count():
    glossary = {}
    result = check_new_terms("Frobbulate.", glossary, llm_judged=True)
    err = NewTermError(result)
    assert "1" in str(err)


def test_new_term_error_ellipsis_when_many_terms():
    """Error message truncates to 3 terms and adds '…' when there are more."""
    glossary = {}
    result = check_new_terms(
        "Alpha bravo charlie delta echo frobnicate.",
        glossary,
        llm_judged=True,
    )
    err = NewTermError(result)
    if len(result.new_terms) > 3:
        assert "…" in str(err)


# ---------------------------------------------------------------------------
# Integration: real assets/glossary.json
# ---------------------------------------------------------------------------


def _load_real_glossary():
    """Load flat glossary from real assets (returns None if unavailable)."""
    if not ASSETS_GLOSSARY.exists():
        return None, None
    data = json.loads(ASSETS_GLOSSARY.read_text(encoding="utf-8"))
    llm_judged = bool(data.get("llm_judged", False))
    flat: dict = {}
    for section in ("official", "subtype_extracted", "extracted"):
        for en_raw, ko in data.get(section, {}).items():
            en_norm = en_raw.replace("_", " ").strip().lower()
            if en_norm and ko and en_norm not in flat:
                flat[en_norm] = (ko, section)
    return flat, llm_judged


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists(),
    reason="assets/glossary.json not present — run build_assets first",
)
def test_real_glossary_known_terms_pass():
    """Standard Netrunner game terms in the glossary do not trigger the guard."""
    flat, llm_judged = _load_real_glossary()
    result = check_new_terms(
        "Install a program. Trash it. Rez this ICE. End the run.",
        flat,
        llm_judged=llm_judged,
    )
    new_en = [t.en_term for t in result.new_terms]
    # Core registered terms must not appear as new terms
    for known in ("install", "trash", "rez"):
        assert known not in new_en, f"'{known}' should be registered but appeared as new"


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists(),
    reason="assets/glossary.json not present — run build_assets first",
)
def test_real_glossary_novel_term_triggers_hitl():
    """A genuinely new term (not in real glossary) triggers the guard."""
    flat, llm_judged = _load_real_glossary()
    result = check_new_terms(
        "Xeromorph this ICE to gain quintessence.",
        flat,
        llm_judged=llm_judged,
    )
    assert not result.passed
    new_en = [t.en_term for t in result.new_terms]
    assert "xeromorph" in new_en or "quintessence" in new_en
