"""천장 검사와 채점 모집단 분리 (SERVICE.md §5, 2026-09-18 개정).

여기서 지키려는 성질은 둘이다.

1. **정답이 못 넘는 임계는 납품을 막지 못한다.** 공식 KO 번역을 같은 채점기에 넣어
   임계를 못 넘으면 그 게이트는 불합격이 아니라 계측 무효다. 이 경로가 없어서
   게이트 1의 도달 불가능한 95%가 여러 세대를 버텼다.
2. **미납품은 오역이 아니다.** 빈 예측은 게이트 분모에서 빠지고 처리율로 보고된다.

동어반복을 피하려고 판정을 구현과 독립적으로 고정한다 — 기대값을 실측 수치와 위치로
박지 않고, "천장을 인위적으로 올리면 판정이 뒤집힌다" 같은 **실패할 수 있는** 대비로 쓴다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import gate_ceiling
import gate1_term_compliance
from evaluate_pipeline import run_evaluation

ROOT = Path(__file__).resolve().parents[1]
HOLD_OUT = ROOT / "data" / "hold_out.json"
GLOSSARY = ROOT / "assets" / "glossary.json"


@pytest.fixture(scope="module")
def hold_out_cards():
    return json.loads(HOLD_OUT.read_text(encoding="utf-8"))


class TestCeilingVerdict:
    """gate_ceiling.check 의 판정 규칙."""

    def test_unreachable_when_reference_below_threshold(self):
        v = gate_ceiling.check("g", threshold=0.95, ceiling=0.75, agent=0.99)
        assert v.verdict == gate_ceiling.UNREACHABLE
        assert not v.reachable
        assert not v.blocks_delivery, "계측 무효는 납품을 막지 않는다"

    def test_agent_above_threshold_still_unreachable_when_ceiling_is_low(self):
        """에이전트가 임계를 넘어도 천장이 낮으면 판정하지 않는다.

        자가 틀렸으면 그 자로 잰 값은 합격도 불합격도 아니다.
        """
        v = gate_ceiling.check("g", threshold=0.95, ceiling=0.75, agent=0.97)
        assert v.verdict == gate_ceiling.UNREACHABLE

    def test_fail_only_when_gate_is_reachable(self):
        v = gate_ceiling.check("g", threshold=0.95, ceiling=1.0, agent=0.90)
        assert v.verdict == gate_ceiling.FAIL
        assert v.blocks_delivery, "도달 가능한 게이트의 미달은 납품을 막는다"

    def test_pass_when_reachable_and_agent_meets_threshold(self):
        v = gate_ceiling.check("g", threshold=0.95, ceiling=1.0, agent=0.96)
        assert v.verdict == gate_ceiling.PASS
        assert not v.blocks_delivery

    def test_verdict_flips_when_ceiling_rises(self):
        """같은 에이전트 점수라도 천장이 올라가면 판정이 바뀐다 — 구현 무관 대비."""
        low = gate_ceiling.check("g", threshold=0.95, ceiling=0.75, agent=0.90)
        high = gate_ceiling.check("g", threshold=0.95, ceiling=1.0, agent=0.90)
        assert low.verdict == gate_ceiling.UNREACHABLE
        assert high.verdict == gate_ceiling.FAIL
        assert low.verdict != high.verdict

    def test_achievement_is_agent_over_ceiling(self):
        v = gate_ceiling.check("g", threshold=0.95, ceiling=0.80, agent=0.60)
        assert v.achievement == pytest.approx(0.75)

    def test_lower_is_better_inverts_comparison(self):
        """편집거리처럼 낮을수록 좋은 지표."""
        v = gate_ceiling.check(
            "g", threshold=0.28, ceiling=0.0, agent=0.18, higher_is_better=False
        )
        assert v.reachable
        assert v.verdict == gate_ceiling.PASS


class TestGate1ScoringView:
    """채점용 용어집 뷰 — 역어에 한글이 없는 항목을 뺀다."""

    def test_drops_entries_whose_rendering_has_no_hangul(self):
        g = {
            "corp": ("Corporation", "official"),
            "trash": ("폐기", "official"),
            "nbn": ("NBN", "official"),
        }
        view = gate1_term_compliance.scoring_view(g)
        assert "trash" in view
        assert "corp" not in view, "역어가 영어인 항목은 채점에서 빠진다"
        assert "nbn" not in view

    def test_scoring_view_does_not_mutate_the_source(self):
        """주입·가드는 전체 용어집을 그대로 써야 한다."""
        g = {"corp": ("Corporation", "official"), "trash": ("폐기", "official")}
        gate1_term_compliance.scoring_view(g)
        assert "corp" in g, "원본 용어집이 변형되면 주입·차단 트리거가 함께 망가진다"


class TestDeliveredPopulation:
    """하드 게이트는 납품된 예측만 채점한다."""

    def test_withheld_cards_leave_the_gate_denominator(self, hold_out_cards):
        n = len(hold_out_cards)
        half = n // 2
        preds = [c["ko_text"] for c in hold_out_cards[:half]] + [""] * (n - half)

        report = run_evaluation(HOLD_OUT, GLOSSARY, preds, skip_llm_judge=True)

        assert report.delivered_count == half
        assert report.withheld_count == n - half
        assert report.delivered_count + report.withheld_count == n
        assert report.throughput == pytest.approx(half / n)
        assert len(report.verdict.gate3.card_results) == half, (
            "미납품 카드가 게이트 분모에 남아 있다 — 번역 없음이 오역으로 계상된다"
        )

    def test_withholding_does_not_lower_the_measured_rate(self, hold_out_cards):
        """같은 예측을 절반만 납품해도 납품분의 준수율은 내려가지 않는다.

        빈 예측이 분모에 섞이던 시절에는 내려갔다 — 그것이 이 분리의 이유다.
        """
        n = len(hold_out_cards)
        half = n // 2
        ref = [c["ko_text"] for c in hold_out_cards]
        partial = ref[:half] + [""] * (n - half)

        full_rate = run_evaluation(
            HOLD_OUT, GLOSSARY, ref, skip_llm_judge=True
        ).verdict.gate1.compliance_rate
        partial_rate = run_evaluation(
            HOLD_OUT, GLOSSARY, partial, skip_llm_judge=True
        ).verdict.gate1.compliance_rate

        assert partial_rate == pytest.approx(full_rate, abs=0.25), (
            "미납품이 준수율을 끌어내리고 있다 — 처리율과 품질이 섞였다"
        )


class TestUnreachableDoesNotRejectDelivery:
    """계측 무효 게이트는 최종 판정을 막지 못한다."""

    def test_real_hold_out_gate1_is_unreachable(self, hold_out_cards):
        """현행 hold-out 에서 공식 KO 정답은 게이트 1 임계를 넘지 못한다.

        이 단언이 깨지면 좋은 일이다 — 천장이 올라갔다는 뜻이므로 게이트 1을
        하드 게이트로 되돌리고 SERVICE.md §5 를 함께 고친다.
        """
        ref = gate1_term_compliance.score_reference(HOLD_OUT, GLOSSARY)
        assert ref.compliance_rate < gate1_term_compliance.THRESHOLD

    def test_gate3_still_blocks_when_ceiling_checks_are_present(self, hold_out_cards):
        """천장 검사가 붙어도 게이트 3은 최종 판정에 남아 있어야 한다.

        게이트 3은 천장 검사 대상이 아니다(정답 대 정답은 거리 0이라 장식이 된다).
        그래서 final_passed 가 ceiling_verdicts 만 보고 분기하면 게이트 3이 통째로
        빠진다 — 실제로 그렇게 짰다가 이 테스트를 붙이며 고쳤다.
        """
        from evaluate_pipeline import EvaluationReport

        # 게이트 1·2 는 계측 무효(납품을 막지 않는다), 게이트 3 은 불합격.
        preds = [c["ko_text"] for c in hold_out_cards]
        report = run_evaluation(HOLD_OUT, GLOSSARY, preds, skip_llm_judge=True)
        assert report.ceiling_verdicts, "천장 검사가 실행되지 않아 전제가 성립하지 않는다"
        assert not any(v.blocks_delivery for v in report.ceiling_verdicts), (
            "이 전제에서는 천장 검사가 납품을 막지 않아야 한다"
        )

        object.__setattr__(report.verdict.gate3, "passed", False)
        report.run_mode = "real"
        assert report.final_passed is False, (
            "게이트 3 불합격이 최종 판정에 반영되지 않았다 — 천장 검사가 붙으면서 "
            "게이트 3 검사가 분기에서 빠졌다"
        )

        object.__setattr__(report.verdict.gate3, "passed", True)
        assert report.final_passed is True

    def test_unreachable_gate_is_not_counted_as_failure(self, hold_out_cards):
        preds = [c["ko_text"] for c in hold_out_cards]
        report = run_evaluation(HOLD_OUT, GLOSSARY, preds, skip_llm_judge=True)

        assert report.ceiling_verdicts, "천장 검사가 실행되지 않았다"
        assert report.unreachable_gates, "현행 hold-out 에서는 계측 무효가 나와야 한다"
        for v in report.ceiling_verdicts:
            if v.verdict == gate_ceiling.UNREACHABLE:
                assert not v.blocks_delivery
