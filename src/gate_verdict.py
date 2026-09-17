"""gate_verdict.py — Combined hard gate verdict.

SERVICE.md §5: "하드 게이트 (하나라도 미달이면 불합격)"
If ANY of the three hard gates fails the overall verdict is FAIL (불합격).

This module accepts pre-scored gate results and returns a GateVerdict.
It does NOT re-run the individual gates — callers obtain those results
from gate1_term_compliance.score_hold_out, gate2_symbol_preservation.score_hold_out,
and gate3_edit_distance.score_hold_out respectively.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from gate1_term_compliance import Gate1Result
from gate2_symbol_preservation import Gate2Result
from gate3_edit_distance import Gate3Result


@dataclass
class GateVerdict:
    """Combined verdict across all three hard gates.

    ``overall_passed`` is True only when every gate's ``passed`` field is True.
    A single failure causes overall_passed == False (불합격).
    """

    overall_passed: bool
    gate1: Gate1Result
    gate2: Gate2Result
    gate3: Gate3Result
    failed_gates: list[int] = field(default_factory=list)  # 1, 2, and/or 3

    @property
    def verdict_label(self) -> str:
        return "합격" if self.overall_passed else "불합격"


def combine(gate1: Gate1Result, gate2: Gate2Result, gate3: Gate3Result) -> GateVerdict:
    """Combine three gate results into one verdict.

    Args:
        gate1: Result from gate1_term_compliance.score_hold_out.
        gate2: Result from gate2_symbol_preservation.score_hold_out.
        gate3: Result from gate3_edit_distance.score_hold_out.

    Returns:
        :class:`GateVerdict` with overall_passed=True only when ALL gates passed.
    """
    failed_gates: list[int] = []
    if not gate1.passed:
        failed_gates.append(1)
    if not gate2.passed:
        failed_gates.append(2)
    if not gate3.passed:
        failed_gates.append(3)

    return GateVerdict(
        overall_passed=len(failed_gates) == 0,
        gate1=gate1,
        gate2=gate2,
        gate3=gate3,
        failed_gates=failed_gates,
    )


def format_verdict(verdict: GateVerdict) -> str:
    """Format a GateVerdict as a human-readable summary string."""
    status_line = f"최종 판정: {verdict.verdict_label}"
    if verdict.failed_gates:
        gates_str = ", ".join(f"게이트 {g}" for g in verdict.failed_gates)
        status_line += f" — 미달 게이트: {gates_str}"

    def _pass(b: bool) -> str:
        return "통과" if b else "미달"

    lines = [
        "=== 하드 게이트 채점 결과 ===",
        f"게이트 1 (용어 준수율):      {_pass(verdict.gate1.passed)}"
        f"  {verdict.gate1.compliance_rate:.1%} (기준 >= {verdict.gate1.threshold:.0%})",
        f"게이트 2 (기호 보존율):      {_pass(verdict.gate2.passed)}"
        f"  {verdict.gate2.preservation_rate:.1%} (기준 {verdict.gate2.threshold:.0%})",
        f"게이트 3 (편집거리 중앙값):  {_pass(verdict.gate3.passed)}"
        f"  {verdict.gate3.median_distance:.4f} (기준 <= {verdict.gate3.threshold})",
        "",
        status_line,
    ]
    return "\n".join(lines)
