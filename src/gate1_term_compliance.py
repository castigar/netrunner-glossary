"""gate1_term_compliance.py — Hard Gate 1: term compliance rate >= 95%.

SERVICE.md §5, gate 1:
  For each card in the hold-out set, every glossary EN term that appears in
  en_text must be translated with its registered KO equivalent in ko_text.
  Term-level compliance rate across all 100 hold-out cards must be >= 95%.
  Below threshold →납품 거부 (delivery rejected).

Compliance is measured at the term-occurrence level:
  rate = compliant_occurrences / total_term_occurrences

where a "term occurrence" is a (card, glossary_en_term) pair where the EN term
appears in the card's en_text, and it is "compliant" when the registered KO
term is present somewhere in ko_text.

Cards where no glossary term appears in en_text contribute 0 to both numerator
and denominator (they neither help nor hurt the rate).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from glossary_guard import GlossaryFlat, check_glossary_compliance, load_flat_glossary

THRESHOLD = 0.95


@dataclass
class CardCompliance:
    """Compliance result for a single hold-out card."""

    card_id: str
    term_checks: int  # EN terms present in en_text
    violations: int   # registered KO terms missing from ko_text
    llm_judged: bool


@dataclass
class Gate1Result:
    """Aggregated Hard Gate 1 result over all hold-out cards."""

    passed: bool
    compliance_rate: float  # compliant / total, or 1.0 when total == 0
    total_checks: int       # sum of term_checks across all cards
    total_violations: int   # sum of violations across all cards
    llm_judged: bool        # glossary.llm_judged propagated from load
    card_results: list[CardCompliance] = field(default_factory=list)
    threshold: float = THRESHOLD


#: 한글 음절 범위. "KO 역어"에 한글이 하나도 없으면 역어가 아니다.
_HANGUL = re.compile(r"[가-힣]")


def scoring_view(glossary: GlossaryFlat) -> GlossaryFlat:
    """채점용 용어집 뷰 — KO 역어에 한글이 없는 항목을 뺀다.

    ``glossary.json``은 세 역할을 겸한다: 초벌 프롬프트의 주입원, new_term_guard 의
    차단 트리거원, 그리고 이 게이트의 정답지. 한 노브로 셋을 동시에 만족시킬 수 없어서
    **채점에서만** 걸러 낸다.

    걸러지는 것은 공식 subtype 파일의 미번역 값이 룰 텍스트 채점에 흘러든 항목이다 —
    ``corp -> 'Corporation'``, ``ambush -> 'Ambush'``, ``nbn -> 'NBN'`` 등 15건.
    "기업"이라고 옳게 옮긴 번역이 등재값이 영어라는 이유로 위반 처리되므로, 좋은
    한국어일수록 점수가 내려가는 역방향 압력이 생긴다.

    **자산에서 지우지 않는 이유**가 있다. 지우면 그 단어가 미등재가 되어
    new_term_guard 의 차단형 정의(룰 텍스트의 비문장초 대문자 미등재 토큰)에 걸린다 —
    ``The Corp guesses``의 ``Corp``가 정확히 그 형태다. 채점을 고치려다 HITL 보류율을
    올리게 된다. 주입과 가드는 전체 용어집을 그대로 쓴다.
    """
    return {
        en: (ko, source)
        for en, (ko, source) in glossary.items()
        if _HANGUL.search(ko)
    }


def score_hold_out(
    hold_out_path: str | Path,
    glossary_path: str | Path,
    predictions: list[str] | None = None,
    cards: list[dict] | None = None,
) -> Gate1Result:
    """Score Hard Gate 1 over the hold-out set.

    Args:
        hold_out_path: Path to data/hold_out.json (list of {id, en_text, ko_text}).
        glossary_path: Path to assets/glossary.json.
        predictions: Optional KO predictions, one per card in hold_out order.
            When provided, each prediction is scored instead of card['ko_text'].
            Enables pipeline output scoring (gate_input_contract AC6).
        cards: Optional pre-filtered card list used instead of reading
            *hold_out_path*.  SERVICE.md §5 scores hard gates on the delivered
            population only, so the caller hands in the delivered subset and the
            matching predictions.  Scoring an undelivered card counts a missing
            translation as "every term violated", which measures throughput, not
            translation quality.

    Returns:
        :class:`Gate1Result` with ``passed=True`` iff compliance_rate >= 0.95.
    """
    glossary, llm_judged = load_flat_glossary(glossary_path)
    glossary = scoring_view(glossary)
    if cards is None:
        cards = json.loads(Path(hold_out_path).read_text(encoding="utf-8"))

    if predictions is not None and len(predictions) != len(cards):
        raise ValueError(
            f"predictions length {len(predictions)} != hold-out length {len(cards)}"
        )

    card_results: list[CardCompliance] = []
    total_checks = 0
    total_violations = 0

    for i, card in enumerate(cards):
        card_id = card.get("id", "")
        en_text = card.get("en_text", "")
        ko_text = predictions[i] if predictions is not None else card.get("ko_text", "")

        result = check_glossary_compliance(en_text, ko_text, glossary, llm_judged)

        # count how many EN terms were checked (present in en_text)
        checks = _count_term_checks(en_text, glossary)
        viol = len(result.violations)

        card_results.append(
            CardCompliance(
                card_id=card_id,
                term_checks=checks,
                violations=viol,
                llm_judged=llm_judged,
            )
        )
        total_checks += checks
        total_violations += viol

    if total_checks == 0:
        compliance_rate = 1.0
    else:
        compliance_rate = (total_checks - total_violations) / total_checks

    return Gate1Result(
        passed=compliance_rate >= THRESHOLD,
        compliance_rate=compliance_rate,
        total_checks=total_checks,
        total_violations=total_violations,
        llm_judged=llm_judged,
        card_results=card_results,
        threshold=THRESHOLD,
    )


def score_reference(
    hold_out_path: str | Path, glossary_path: str | Path
) -> Gate1Result:
    """Score the official KO translations instead of agent output.

    The ceiling any agent is graded against.  SERVICE.md §5 requires every hard
    gate to measure this before it may reject a delivery: a threshold the
    reference itself cannot clear is an instrument error, not a quality bar.

    Gate 2 had this path from the start; gate 1 did not, which is why an
    unreachable 95% survived several generations unnoticed.  Measured on the
    2026-09-18 hold-out the reference scores 0.7469 — below the 0.95 threshold,
    so gate 1 reports UNREACHABLE rather than a failure (see
    :mod:`gate_ceiling`).
    """
    return score_hold_out(hold_out_path, glossary_path, predictions=None)


def _count_term_checks(en_text: str, glossary: GlossaryFlat) -> int:
    """Count how many glossary EN terms appear in *en_text* (term-occurrence count)."""
    import re

    count = 0
    for en_term in glossary:
        pattern = r"(?<!\w)" + re.escape(en_term) + r"(?!\w)"
        if re.search(pattern, en_text, re.IGNORECASE):
            count += 1
    return count
