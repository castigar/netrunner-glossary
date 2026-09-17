"""test_hitl_interrupt.py — Tests for the unified HITL interrupt coordinator (AC 8).

HITL interrupt 트리거 4가지:
  ① 신규 EN 용어 발견          — new_term_guard.check_new_terms()
  ② 용어 충돌(1 EN → 2+ KO)   — conflict_guard.check_conflicts()
  ③ 규칙 검증 실패              — glossary_guard + fidelity_guard
  ④ TM 최고 유사도 임계 미만   — tm_top_score < tm_threshold
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conflict_guard import (  # noqa: E402
    ConflictCheckResult,
    ConflictError,
    ConflictViolation,
    check_conflicts,
    load_conflict_entries,
)
from hitl_interrupt import (  # noqa: E402
    HITLInterrupt,
    HITLInterruptResult,
    HITLTrigger,
    InterruptReason,
    check_hitl_triggers,
)

ASSETS_CONFLICTS = Path(__file__).parent.parent / "assets" / "conflicts.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_glossary() -> dict:
    return {
        "install": ("설치", "extracted"),
        "trash": ("폐기", "extracted"),
        "rez": ("레즈", "extracted"),
        "barrier": ("방벽", "official"),
        "program": ("프로그램", "extracted"),
    }


def _conflicts_with_barrier() -> list[dict]:
    return [
        {
            "en_term": "barrier",
            "ko_variants": ["방벽", "장벽"],
            "source_card_ids": {"방벽": ["card_a"], "장벽": ["card_b"]},
            "source": "subtype",
        }
    ]


def _no_conflicts() -> list[dict]:
    return []


# ===========================================================================
# conflict_guard.py tests
# ===========================================================================


class TestCheckConflicts:
    def test_passes_when_no_conflicts_in_source(self):
        result = check_conflicts("Install a program.", _no_conflicts())
        assert result.passed
        assert result.violations == []

    def test_passes_when_conflicted_term_not_in_source(self):
        result = check_conflicts("Install a program.", _conflicts_with_barrier())
        assert result.passed

    def test_detects_conflicted_term_in_source(self):
        result = check_conflicts("Encounter a barrier ICE.", _conflicts_with_barrier())
        assert not result.passed
        assert len(result.violations) == 1
        assert result.violations[0].en_term == "barrier"

    def test_violation_includes_ko_variants(self):
        result = check_conflicts("Encounter a barrier ICE.", _conflicts_with_barrier())
        v = result.violations[0]
        assert "방벽" in v.ko_variants
        assert "장벽" in v.ko_variants

    def test_case_insensitive_matching(self):
        result = check_conflicts("Encounter a BARRIER ICE.", _conflicts_with_barrier())
        assert not result.passed

    def test_partial_word_not_matched(self):
        """'barrier' must not match inside 'barrierworks' or 'subbarrier'."""
        result = check_conflicts("This barrierworks perfectly.", _conflicts_with_barrier())
        assert result.passed

    def test_skips_entry_with_single_ko_variant(self):
        """An entry with only one KO variant is not a conflict and is skipped."""
        single = [{"en_term": "rez", "ko_variants": ["레즈"], "source": "extracted"}]
        result = check_conflicts("Rez this ICE.", single)
        assert result.passed

    def test_skips_entry_with_no_en_term(self):
        malformed = [{"ko_variants": ["방벽", "장벽"], "source": "subtype"}]
        result = check_conflicts("barrier", malformed)
        assert result.passed

    def test_detects_multiword_conflict(self):
        conflicts = [
            {
                "en_term": "code gate",
                "ko_variants": ["코드 게이트", "코드게이트"],
                "source": "subtype",
            }
        ]
        result = check_conflicts("Encounter a code gate ICE.", conflicts)
        assert not result.passed
        assert result.violations[0].en_term == "code gate"

    def test_deduplicates_same_term_appearing_twice(self):
        """If the same conflicted term appears twice in the text, report once."""
        result = check_conflicts(
            "Encounter a barrier then another barrier.", _conflicts_with_barrier()
        )
        barrier_violations = [v for v in result.violations if v.en_term == "barrier"]
        assert len(barrier_violations) == 1

    def test_detects_multiple_conflicted_terms(self):
        conflicts = [
            {"en_term": "barrier", "ko_variants": ["방벽", "장벽"], "source": "subtype"},
            {
                "en_term": "code gate",
                "ko_variants": ["코드 게이트", "코드게이트"],
                "source": "subtype",
            },
        ]
        result = check_conflicts(
            "Encounter a barrier or a code gate ICE.", conflicts
        )
        assert not result.passed
        en_terms = {v.en_term for v in result.violations}
        assert "barrier" in en_terms
        assert "code gate" in en_terms

    def test_result_is_pydantic_model(self):
        result = check_conflicts("barrier ICE.", _conflicts_with_barrier())
        assert isinstance(result, ConflictCheckResult)
        assert isinstance(result.violations[0], ConflictViolation)


class TestConflictError:
    def test_error_carries_result(self):
        result = check_conflicts("Encounter a barrier ICE.", _conflicts_with_barrier())
        err = ConflictError(result)
        assert err.result is result

    def test_error_message_names_term(self):
        result = check_conflicts("Encounter a barrier ICE.", _conflicts_with_barrier())
        err = ConflictError(result)
        assert "barrier" in str(err)

    def test_error_inherits_exception(self):
        result = check_conflicts("Encounter a barrier ICE.", _conflicts_with_barrier())
        assert isinstance(ConflictError(result), Exception)

    def test_error_ellipsis_when_many_violations(self):
        many_conflicts = [
            {"en_term": f"term{i}", "ko_variants": ["a", "b"], "source": "test"}
            for i in range(5)
        ]
        text = " ".join(f"term{i}" for i in range(5))
        result = check_conflicts(text, many_conflicts)
        err = ConflictError(result)
        if len(result.violations) > 3:
            assert "…" in str(err)


class TestLoadConflictEntries:
    @pytest.mark.skipif(
        not ASSETS_CONFLICTS.exists(),
        reason="assets/conflicts.json not present — run build_assets first",
    )
    def test_loads_real_conflicts_json(self):
        entries = load_conflict_entries(ASSETS_CONFLICTS)
        assert isinstance(entries, list)
        assert len(entries) > 0

    @pytest.mark.skipif(
        not ASSETS_CONFLICTS.exists(),
        reason="assets/conflicts.json not present — run build_assets first",
    )
    def test_real_barrier_is_conflicted(self):
        entries = load_conflict_entries(ASSETS_CONFLICTS)
        barrier = next((e for e in entries if e["en_term"] == "barrier"), None)
        assert barrier is not None
        assert len(barrier["ko_variants"]) >= 2

    @pytest.mark.skipif(
        not ASSETS_CONFLICTS.exists(),
        reason="assets/conflicts.json not present — run build_assets first",
    )
    def test_real_conflict_detected_in_source(self):
        entries = load_conflict_entries(ASSETS_CONFLICTS)
        result = check_conflicts("Encounter a barrier ICE.", entries)
        assert not result.passed


# ===========================================================================
# hitl_interrupt.py tests
# ===========================================================================


class TestCheckHITLTriggers:
    """All 4 HITL triggers tested in isolation and combination."""

    # ------------------------------------------------------------------
    # No triggers — happy path
    # ------------------------------------------------------------------

    def test_no_trigger_when_all_pass(self):
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            conflict_entries=[],
            tm_top_score=0.05,
            tm_threshold=0.01,
        )
        assert not result.should_interrupt
        assert result.triggers == []

    # ------------------------------------------------------------------
    # Trigger ①: 신규 EN 용어 발견
    # ------------------------------------------------------------------

    def test_trigger_new_term_fires_for_unregistered_term(self):
        glossary = {"install": ("설치", "extracted")}
        result = check_hitl_triggers(
            "Frobbulate a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
        )
        assert result.should_interrupt
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.NEW_TERM in reasons

    def test_trigger_new_term_includes_term_list(self):
        glossary = {}
        result = check_hitl_triggers(
            "Frobbulate the runner.",
            "러너를 공격한다.",
            glossary=glossary,
            llm_judged=True,
        )
        new_term_trigger = next(
            t for t in result.triggers if t.reason == InterruptReason.NEW_TERM
        )
        assert "frobbulate" in new_term_trigger.detail["new_terms"]

    def test_trigger_new_term_does_not_fire_when_all_registered(self):
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
        )
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.NEW_TERM not in reasons

    # ------------------------------------------------------------------
    # Trigger ②: 용어 충돌
    # ------------------------------------------------------------------

    def test_trigger_conflict_fires_for_conflicted_term(self):
        glossary = {}
        result = check_hitl_triggers(
            "Encounter a barrier ICE.",
            "방벽 아이스를 대치한다.",
            glossary=glossary,
            llm_judged=True,
            conflict_entries=_conflicts_with_barrier(),
        )
        assert result.should_interrupt
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.TERM_CONFLICT in reasons

    def test_trigger_conflict_includes_variants(self):
        glossary = {}
        result = check_hitl_triggers(
            "Encounter a barrier ICE.",
            "방벽 아이스를 대치한다.",
            glossary=glossary,
            llm_judged=True,
            conflict_entries=_conflicts_with_barrier(),
        )
        conflict_trigger = next(
            t for t in result.triggers if t.reason == InterruptReason.TERM_CONFLICT
        )
        conflict_detail = conflict_trigger.detail["conflicts"][0]
        assert "방벽" in conflict_detail["ko_variants"]
        assert "장벽" in conflict_detail["ko_variants"]

    def test_trigger_conflict_skipped_when_no_entries(self):
        """When conflict_entries is None, trigger ② is not checked."""
        glossary = {}
        result = check_hitl_triggers(
            "Encounter a barrier ICE.",
            "방벽 아이스를 대치한다.",
            glossary=glossary,
            llm_judged=True,
            conflict_entries=None,  # skip check
        )
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.TERM_CONFLICT not in reasons

    def test_trigger_conflict_does_not_fire_when_term_not_in_source(self):
        glossary = {}
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            conflict_entries=_conflicts_with_barrier(),
        )
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.TERM_CONFLICT not in reasons

    # ------------------------------------------------------------------
    # Trigger ③: 규칙 검증 실패
    # ------------------------------------------------------------------

    def test_trigger_rule_violation_fires_on_glossary_violation(self):
        glossary = {"trash": ("폐기", "extracted")}
        result = check_hitl_triggers(
            "Trash the card.",
            "카드를 파기한다.",  # wrong: '파기' instead of '폐기'
            glossary=glossary,
            llm_judged=True,
        )
        assert result.should_interrupt
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.RULE_VIOLATION in reasons

    def test_trigger_rule_violation_fires_on_fidelity_violation(self):
        glossary = {}
        result = check_hitl_triggers(
            "Gain 2[credit].",
            "크레딧 3을 얻는다.",  # wrong number: 3 instead of 2
            glossary=glossary,
            llm_judged=True,
        )
        assert result.should_interrupt
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.RULE_VIOLATION in reasons

    def test_trigger_rule_violation_detail_has_violation_lists(self):
        glossary = {"trash": ("폐기", "extracted")}
        result = check_hitl_triggers(
            "Trash the card.",
            "카드를 파기한다.",
            glossary=glossary,
            llm_judged=True,
        )
        rule_trigger = next(
            t for t in result.triggers if t.reason == InterruptReason.RULE_VIOLATION
        )
        assert "glossary_violations" in rule_trigger.detail
        assert "fidelity_violations" in rule_trigger.detail

    def test_trigger_rule_violation_does_not_fire_when_all_correct(self):
        glossary = {"trash": ("폐기", "extracted")}
        result = check_hitl_triggers(
            "Trash the card.",
            "카드를 폐기한다.",
            glossary=glossary,
            llm_judged=True,
        )
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.RULE_VIOLATION not in reasons

    # ------------------------------------------------------------------
    # Trigger ④: TM 최고 유사도 임계 미만
    # ------------------------------------------------------------------

    def test_trigger_low_tm_fires_when_score_below_threshold(self):
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            tm_top_score=0.005,
            tm_threshold=0.01,
        )
        assert result.should_interrupt
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.LOW_TM_CONFIDENCE in reasons

    def test_trigger_low_tm_includes_score_and_threshold_in_detail(self):
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            tm_top_score=0.003,
            tm_threshold=0.01,
        )
        low_tm_trigger = next(
            t for t in result.triggers if t.reason == InterruptReason.LOW_TM_CONFIDENCE
        )
        assert low_tm_trigger.detail["tm_top_score"] == pytest.approx(0.003)
        assert low_tm_trigger.detail["threshold"] == pytest.approx(0.01)

    def test_trigger_low_tm_does_not_fire_when_score_above_threshold(self):
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            tm_top_score=0.05,
            tm_threshold=0.01,
        )
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.LOW_TM_CONFIDENCE not in reasons

    def test_trigger_low_tm_does_not_fire_when_score_equals_threshold(self):
        """Score exactly at threshold is NOT below threshold → no interrupt."""
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            tm_top_score=0.01,
            tm_threshold=0.01,
        )
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.LOW_TM_CONFIDENCE not in reasons

    def test_trigger_low_tm_skipped_when_score_is_none(self):
        """When tm_top_score is None, trigger ④ is not checked."""
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            tm_top_score=None,
        )
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.LOW_TM_CONFIDENCE not in reasons

    def test_trigger_low_tm_threshold_from_env(self, monkeypatch):
        """TM_THRESHOLD env var overrides the default when tm_threshold is not passed."""
        monkeypatch.setenv("TM_THRESHOLD", "0.02")
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            tm_top_score=0.015,
            tm_threshold=None,  # triggers env var fallback
        )
        reasons = [t.reason for t in result.triggers]
        assert InterruptReason.LOW_TM_CONFIDENCE in reasons

    # ------------------------------------------------------------------
    # Multiple triggers can fire simultaneously
    # ------------------------------------------------------------------

    def test_multiple_triggers_can_fire(self):
        """New term + rule violation can both fire at once."""
        glossary = {"trash": ("폐기", "extracted")}
        result = check_hitl_triggers(
            "Frobbulate the trash program.",
            "카드를 파기한다.",  # wrong translation
            glossary=glossary,
            llm_judged=True,
        )
        reasons = {t.reason for t in result.triggers}
        assert InterruptReason.NEW_TERM in reasons
        assert InterruptReason.RULE_VIOLATION in reasons

    def test_all_four_triggers_can_fire(self):
        """All 4 triggers at once on a pathological input."""
        glossary = {}
        result = check_hitl_triggers(
            "Frobbulate the barrier ICE and gain 2[credit].",
            "크레딧 3을 얻는다.",  # wrong number → fidelity; barrier → conflict
            glossary=glossary,
            llm_judged=True,
            conflict_entries=_conflicts_with_barrier(),
            tm_top_score=0.001,
            tm_threshold=0.01,
        )
        assert result.should_interrupt
        reasons = {t.reason for t in result.triggers}
        assert InterruptReason.NEW_TERM in reasons
        assert InterruptReason.TERM_CONFLICT in reasons
        assert InterruptReason.RULE_VIOLATION in reasons
        assert InterruptReason.LOW_TM_CONFIDENCE in reasons


# ===========================================================================
# HITLInterrupt exception
# ===========================================================================


class TestHITLInterrupt:
    def test_raises_with_result(self):
        glossary = {}
        result = check_hitl_triggers(
            "Frobbulate.", "설치.", glossary=glossary, llm_judged=True
        )
        exc = HITLInterrupt(result)
        assert exc.result is result

    def test_exception_message_names_reasons(self):
        glossary = {}
        result = check_hitl_triggers(
            "Frobbulate.", "설치.", glossary=glossary, llm_judged=True
        )
        exc = HITLInterrupt(result)
        assert "new_term" in str(exc)

    def test_inherits_exception(self):
        glossary = {}
        result = check_hitl_triggers(
            "Frobbulate.", "설치.", glossary=glossary, llm_judged=True
        )
        assert isinstance(HITLInterrupt(result), Exception)

    def test_can_be_raised_and_caught(self):
        glossary = {}
        result = check_hitl_triggers(
            "Frobbulate.", "설치.", glossary=glossary, llm_judged=True
        )
        with pytest.raises(HITLInterrupt) as exc_info:
            if result.should_interrupt:
                raise HITLInterrupt(result)
        assert exc_info.value.result is result


# ===========================================================================
# InterruptReason enum
# ===========================================================================


class TestInterruptReason:
    def test_all_four_reasons_defined(self):
        reasons = {r.value for r in InterruptReason}
        assert "new_term" in reasons
        assert "term_conflict" in reasons
        assert "rule_violation" in reasons
        assert "low_tm_confidence" in reasons

    def test_four_reasons_total(self):
        assert len(InterruptReason) == 4


# ===========================================================================
# HITLInterruptResult structure
# ===========================================================================


class TestHITLInterruptResult:
    def test_result_is_pydantic_model(self):
        glossary = {}
        result = check_hitl_triggers(
            "Frobbulate.", "설치.", glossary=glossary, llm_judged=True
        )
        assert isinstance(result, HITLInterruptResult)

    def test_triggers_are_hitl_trigger_instances(self):
        glossary = {}
        result = check_hitl_triggers(
            "Frobbulate.", "설치.", glossary=glossary, llm_judged=True
        )
        for t in result.triggers:
            assert isinstance(t, HITLTrigger)

    def test_no_interrupt_has_empty_triggers(self):
        glossary = _simple_glossary()
        result = check_hitl_triggers(
            "Install a program.",
            "프로그램을 설치한다.",
            glossary=glossary,
            llm_judged=True,
            conflict_entries=[],
            tm_top_score=0.05,
            tm_threshold=0.01,
        )
        assert not result.should_interrupt
        assert result.triggers == []
