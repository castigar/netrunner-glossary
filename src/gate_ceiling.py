"""gate_ceiling.py — 하드 게이트의 천장 검사 (SERVICE.md §5).

모든 하드 게이트는 판정 실행마다 **공식 KO 정답을 동일 채점기에 통과시켜 천장을 먼저
측정한다.** 정답이 그 게이트의 임계에 미달하면 그 게이트는 '불합격'이 아니라
'계측 무효(UNREACHABLE)'이고 납품 거부 근거로 쓰지 않는다.

정답이 못 넘는 선은 품질 기준이 아니라 계측 오류다. 이 모듈이 없어서 게이트 1의
도달 불가능한 95%가 여러 세대를 버텼다 — 게이트 2에는 ``score_reference()``가
구현돼 있었는데도 판정 경로에 결선되지 않아 같은 일이 벌어졌다.

채점기를 재구현하지 않는다. 기존 ``score_reference()``/``score_hold_out()``을
호출해 얻은 수치를 판정으로 합성할 뿐이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: 판정 값. UNREACHABLE 은 실패가 아니라 "이 게이트로는 판정할 수 없다"는 뜻이다.
PASS = "PASS"
FAIL = "FAIL"
UNREACHABLE = "UNREACHABLE"


@dataclass
class CeilingVerdict:
    """한 게이트의 천장 검사 결과."""

    gate: str
    threshold: float
    ceiling: float                    # 공식 KO 정답을 같은 채점기로 채점한 값
    agent: Optional[float]            # 에이전트 산출물 점수 (없으면 None)
    reachable: bool                   # 정답이 임계를 넘는가
    verdict: str                      # PASS | FAIL | UNREACHABLE
    achievement: Optional[float]      # agent / ceiling — 에이전트가 천장의 몇 %인가
    higher_is_better: bool = True

    @property
    def blocks_delivery(self) -> bool:
        """이 판정이 납품을 막는가. UNREACHABLE 은 막지 않는다."""
        return self.verdict == FAIL


def check(
    gate: str,
    *,
    threshold: float,
    ceiling: float,
    agent: Optional[float] = None,
    higher_is_better: bool = True,
) -> CeilingVerdict:
    """천장을 먼저 보고 게이트 판정을 합성한다.

    Args:
        gate: 게이트 이름 (보고용).
        threshold: SERVICE.md §5 가 정한 임계.
        ceiling: 공식 KO 정답을 같은 채점기로 채점한 값.
        agent: 에이전트 산출물 점수. None 이면 천장만 보고한다.
        higher_is_better: 비율 지표는 True, 편집거리처럼 낮을수록 좋은 지표는 False.

    Returns:
        :class:`CeilingVerdict`. ``reachable`` 이 False 면 ``verdict`` 는 항상
        UNREACHABLE 이고 ``agent`` 값과 무관하다 — 잴 수 없는 자로 잰 값은
        합격도 불합격도 아니다.
    """

    def meets(value: float) -> bool:
        return value >= threshold if higher_is_better else value <= threshold

    reachable = meets(ceiling)

    if not reachable:
        verdict = UNREACHABLE
    elif agent is None:
        verdict = UNREACHABLE
    else:
        verdict = PASS if meets(agent) else FAIL

    achievement: Optional[float] = None
    if agent is not None and ceiling not in (0.0, None):
        achievement = agent / ceiling if higher_is_better else ceiling / agent

    return CeilingVerdict(
        gate=gate,
        threshold=threshold,
        ceiling=ceiling,
        agent=agent,
        reachable=reachable,
        verdict=verdict,
        achievement=achievement,
        higher_is_better=higher_is_better,
    )


def format_line(v: CeilingVerdict) -> str:
    """보고 한 줄. 에이전트·천장·임계를 늘 함께 낸다 — 셋 중 하나만 보면 오독한다."""
    agent = f"{v.agent:.4f}" if v.agent is not None else "—"
    ach = f"  달성률 {v.achievement:.1%}" if v.achievement is not None else ""
    tail = ""
    if not v.reachable:
        tail = "  ※ 정답이 임계를 못 넘는다 — 납품 거부 근거로 쓰지 않는다"
    return (
        f"  {v.gate}: 에이전트 {agent} / 정답 천장 {v.ceiling:.4f} / 임계 {v.threshold}"
        f"  → {v.verdict}{ach}{tail}"
    )
