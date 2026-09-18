"""test_glossary_guard.py — Tests for glossary compliance guardrail (§4.2).

AC: Registered EN terms must be translated with their registered KO equivalents.
Violations block the output and send it to the review queue.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from glossary_guard import (  # noqa: E402
    GlossaryCheckResult,
    GlossaryViolation,
    GlossaryViolationError,
    check_glossary_compliance,
    load_flat_glossary,
)

ASSETS_GLOSSARY = Path(__file__).parent.parent / "assets" / "glossary.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _glossary_file(tmp_path: Path, *, llm_judged: bool = True, **sections) -> Path:
    """Write a minimal glossary.json with the given sections."""
    data = {"llm_judged": llm_judged, **sections}
    p = tmp_path / "glossary.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _simple_glossary(tmp_path: Path) -> Path:
    return _glossary_file(
        tmp_path,
        official={"barrier": "방벽", "sentry": "파수"},
        subtype_extracted={},
        extracted={"install": "설치", "trash": "폐기"},
    )


# ---------------------------------------------------------------------------
# load_flat_glossary
# ---------------------------------------------------------------------------


def test_load_returns_flat_mapping(tmp_path):
    path = _simple_glossary(tmp_path)
    flat, llm_judged = load_flat_glossary(path)
    assert "barrier" in flat
    assert flat["barrier"] == ("방벽", "official")
    assert "install" in flat
    assert flat["install"] == ("설치", "extracted")
    assert llm_judged is True


def test_load_normalizes_underscores_to_spaces(tmp_path):
    path = _glossary_file(tmp_path, official={"code_gate": "코드 게이트"}, extracted={})
    flat, _ = load_flat_glossary(path)
    assert "code gate" in flat
    assert "code_gate" not in flat


def test_load_llm_judged_defaults_false_when_absent(tmp_path):
    data = {"official": {"barrier": "방벽"}, "extracted": {}}
    p = tmp_path / "glossary.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    _, llm_judged = load_flat_glossary(p)
    assert llm_judged is False


def test_load_official_priority_over_extracted(tmp_path):
    """Official section wins when the same EN key appears in multiple sections."""
    path = _glossary_file(
        tmp_path,
        official={"install": "공식설치"},
        extracted={"install": "설치"},
    )
    flat, _ = load_flat_glossary(path)
    assert flat["install"] == ("공식설치", "official")


def test_load_all_three_sections(tmp_path):
    path = _glossary_file(
        tmp_path,
        official={"barrier": "방벽"},
        subtype_extracted={"alliance": "연합"},
        extracted={"trash": "폐기"},
    )
    flat, _ = load_flat_glossary(path)
    assert "barrier" in flat
    assert "alliance" in flat
    assert "trash" in flat


def test_load_empty_sections_does_not_crash(tmp_path):
    path = _glossary_file(tmp_path)
    flat, _ = load_flat_glossary(path)
    assert flat == {}


# ---------------------------------------------------------------------------
# check_glossary_compliance — passing cases
# ---------------------------------------------------------------------------


def test_passes_when_no_glossary_terms_in_en_text():
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "Make a run on HQ.", "HQ에 런을 수행한다.", flat, llm_judged=True
    )
    assert result.passed
    assert result.violations == []


def test_passes_when_correct_ko_present_in_translation():
    flat = {"install": ("설치", "extracted"), "trash": ("폐기", "extracted")}
    result = check_glossary_compliance(
        "Install a program. Trash it.",
        "프로그램을 설치한다. 폐기한다.",
        flat,
        llm_judged=True,
    )
    assert result.passed


def test_passes_for_official_subtype_term():
    flat = {"barrier": ("방벽", "official")}
    result = check_glossary_compliance(
        "Encounter a barrier ICE.", "방벽 아이스를 대치한다.", flat, llm_judged=True
    )
    assert result.passed


def test_passes_with_empty_glossary():
    result = check_glossary_compliance(
        "Install a program.", "프로그램을 설치한다.", {}, llm_judged=True
    )
    assert result.passed


# ---------------------------------------------------------------------------
# check_glossary_compliance — violation cases
# ---------------------------------------------------------------------------


def test_violation_when_ko_term_absent_from_translation():
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "Install a program.", "프로그램을 배치한다.", flat, llm_judged=True
    )
    assert not result.passed
    assert len(result.violations) == 1
    assert result.violations[0].en_term == "install"
    assert result.violations[0].expected_ko == "설치"


def test_violation_records_correct_source_section():
    flat = {"trash": ("폐기", "extracted")}
    result = check_glossary_compliance(
        "Trash the program.", "프로그램을 파기한다.", flat, llm_judged=True
    )
    assert result.violations[0].source == "extracted"


def test_all_violations_reported_not_just_first():
    flat = {"install": ("설치", "extracted"), "trash": ("폐기", "extracted")}
    result = check_glossary_compliance(
        "Install a program and trash it.",
        "프로그램을 배치하고 파기한다.",
        flat,
        llm_judged=True,
    )
    assert not result.passed
    assert len(result.violations) == 2
    en_terms = {v.en_term for v in result.violations}
    assert en_terms == {"install", "trash"}


def test_term_absent_from_en_text_is_not_checked():
    flat = {"rez": ("레즈", "extracted")}
    result = check_glossary_compliance(
        "Install a program.", "프로그램을 설치한다.", flat, llm_judged=True
    )
    assert result.passed


def test_violation_returned_as_structured_model():
    flat = {"rez": ("레즈", "extracted")}
    result = check_glossary_compliance(
        "Rez this ICE.", "이 아이스를 활성화한다.", flat, llm_judged=True
    )
    assert isinstance(result, GlossaryCheckResult)
    assert isinstance(result.violations[0], GlossaryViolation)


# ---------------------------------------------------------------------------
# Word-boundary matching
# ---------------------------------------------------------------------------


def test_partial_word_match_is_not_a_violation():
    """'install' inside 'installation' must not trigger the guardrail."""
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "Use this installation token.",
        "이 설치 토큰을 사용한다.",
        flat,
        llm_judged=True,
    )
    # 'installation' is not 'install' as a standalone word → term not checked
    assert result.passed


def test_prefix_word_match_is_not_a_violation():
    """'run' must not match inside 'runner'."""
    flat = {"run": ("런", "official")}
    result = check_glossary_compliance(
        "The runner makes a move.", "러너가 이동한다.", flat, llm_judged=True
    )
    # 'runner' contains 'run' but is not 'run' → term not checked
    assert result.passed


def test_en_term_match_is_case_insensitive():
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "INSTALL a program.", "프로그램을 설치한다.", flat, llm_judged=True
    )
    assert result.passed


def test_uppercase_en_term_triggers_violation():
    flat = {"trash": ("폐기", "extracted")}
    result = check_glossary_compliance(
        "TRASH the card.", "카드를 파기한다.", flat, llm_judged=True
    )
    assert not result.passed


def test_multiword_term_is_matched_as_phrase():
    flat = {"end the run": ("런 종료", "extracted")}
    result = check_glossary_compliance(
        "End the run.", "런 종료한다.", flat, llm_judged=True
    )
    assert result.passed


def test_multiword_term_violation():
    flat = {"end the run": ("런 종료", "extracted")}
    result = check_glossary_compliance(
        "End the run.", "런을 끝낸다.", flat, llm_judged=True
    )
    assert not result.passed
    assert result.violations[0].expected_ko == "런 종료"


# ---------------------------------------------------------------------------
# llm_judged flag propagation
# ---------------------------------------------------------------------------


def test_llm_judged_false_propagated_on_pass():
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "Install a program.", "프로그램을 설치한다.", flat, llm_judged=False
    )
    assert result.passed
    assert result.llm_judged is False


def test_llm_judged_true_propagated_on_violation():
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "Install a program.", "프로그램을 배치한다.", flat, llm_judged=True
    )
    assert not result.passed
    assert result.llm_judged is True


# ---------------------------------------------------------------------------
# GlossaryViolationError — blocking signal for interrupt()
# ---------------------------------------------------------------------------


def test_violation_error_carries_result():
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "Install a program.", "프로그램을 배치한다.", flat, llm_judged=True
    )
    err = GlossaryViolationError(result)
    assert err.result is result


def test_violation_error_message_names_violated_terms():
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "Install a program.", "프로그램을 배치한다.", flat, llm_judged=True
    )
    err = GlossaryViolationError(result)
    assert "install" in str(err)


def test_violation_error_inherits_from_exception():
    flat = {"install": ("설치", "extracted")}
    result = check_glossary_compliance(
        "Install.", "배치.", flat, llm_judged=True
    )
    assert isinstance(GlossaryViolationError(result), Exception)


# ---------------------------------------------------------------------------
# Integration: real assets/glossary.json
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists(),
    reason="assets/glossary.json not present — run build_assets first",
)
def test_real_glossary_loads_and_known_terms_present():
    flat, llm_judged = load_flat_glossary(ASSETS_GLOSSARY)
    assert "install" in flat, "'install' must be in extracted glossary"
    assert flat["install"][0] == "설치"
    assert "trash" in flat, "'trash' must be in extracted glossary"
    assert flat["trash"][0] == "폐기"
    assert llm_judged is True, "phase-1 must have set llm_judged=true"


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists(),
    reason="assets/glossary.json not present — run build_assets first",
)
def test_real_glossary_violation_on_wrong_trash_translation():
    flat, llm_judged = load_flat_glossary(ASSETS_GLOSSARY)
    result = check_glossary_compliance(
        "Trash the top card of R&D.",
        "R&D 상단 카드를 파기한다.",
        flat,
        llm_judged=llm_judged,
    )
    assert not result.passed
    trash_violations = [v for v in result.violations if v.en_term == "trash"]
    assert trash_violations, "'trash' violation must be detected when '파기' is used"
    assert trash_violations[0].expected_ko == "폐기"


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists(),
    reason="assets/glossary.json not present — run build_assets first",
)
def test_real_glossary_passes_on_correct_trash_translation():
    flat, llm_judged = load_flat_glossary(ASSETS_GLOSSARY)
    result = check_glossary_compliance(
        "Trash the top card of R&D.",
        "R&D 상단 카드를 폐기한다.",
        flat,
        llm_judged=llm_judged,
    )
    trash_violations = [v for v in result.violations if v.en_term == "trash"]
    assert not trash_violations, "correct '폐기' translation must not be a violation"


@pytest.mark.skipif(
    not ASSETS_GLOSSARY.exists(),
    reason="assets/glossary.json not present — run build_assets first",
)
def test_real_glossary_llm_judged_is_true():
    _, llm_judged = load_flat_glossary(ASSETS_GLOSSARY)
    assert llm_judged is True
