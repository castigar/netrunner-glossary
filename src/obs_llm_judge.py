"""obs_llm_judge.py — Observation metrics: LLM-Judge for rule text and flavor.

SERVICE.md §5 observation metrics (not hard gates):
  - Rule text sentence pattern match (문형 일치): LLM-Judge 3-point scale >= 2.5/3
  - Flavor naturalness (자연스러움): LLM-Judge 3-point scale >= 2.0/3

These metrics are reported but do not cause delivery rejection.

Uses us.anthropic.claude-haiku-4-5-20251001-v1:0 via Bedrock (simple
classification repeated at volume — upper models are excessive).

AWS credentials are read from the environment. Missing credentials raise
immediately — the function does not silently fall through to a dry run.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Observation thresholds (not hard gates — delivery never rejected on these)
RULE_PATTERN_THRESHOLD = 2.5  # 문형 일치 목표
FLAVOR_NATURALNESS_THRESHOLD = 2.0  # 자연스러움 목표

# Bedrock model (Haiku for high-volume binary/trinary judgment)
JUDGE_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
JUDGE_REGION = "us-east-1"


# ---------------------------------------------------------------------------
# Pydantic output schemas
# ---------------------------------------------------------------------------


class RulePatternJudgment(BaseModel):
    """LLM structured output for rule text sentence pattern match."""

    card_id: str = Field(description="Card identifier")
    score: int = Field(
        ge=1,
        le=3,
        description=(
            "1 = 문형 불일치 (sentence pattern does not match Netrunner conventions); "
            "2 = 부분 일치 (partial match, some pattern elements missing); "
            "3 = 완전 일치 (sentence pattern fully matches game conventions)"
        ),
    )
    reasoning: str = Field(description="One-sentence reason for the score")


class FlavorNaturalnessJudgment(BaseModel):
    """LLM structured output for flavor text naturalness."""

    card_id: str = Field(description="Card identifier")
    score: int = Field(
        ge=1,
        le=3,
        description=(
            "1 = 어색함 (unnatural Korean, reads like a literal translation); "
            "2 = 보통 (acceptable but slightly unnatural); "
            "3 = 자연스러움 (reads naturally as Korean)"
        ),
    )
    reasoning: str = Field(description="One-sentence reason for the score")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CardObservation:
    """Observation result for a single hold-out card."""

    card_id: str
    rule_pattern_score: int  # 1–3
    flavor_naturalness_score: int  # 1–3
    rule_reasoning: str = ""
    flavor_reasoning: str = ""


@dataclass
class ObsReport:
    """Aggregated observation metrics report."""

    rule_pattern_mean: float
    rule_pattern_median: float
    flavor_naturalness_mean: float
    flavor_naturalness_median: float
    n: int
    rule_pattern_threshold: float = RULE_PATTERN_THRESHOLD
    flavor_naturalness_threshold: float = FLAVOR_NATURALNESS_THRESHOLD
    rule_pattern_meets_target: bool = False
    flavor_naturalness_meets_target: bool = False
    card_observations: list[CardObservation] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rule_pattern_meets_target = (
            self.rule_pattern_mean >= self.rule_pattern_threshold
        )
        self.flavor_naturalness_meets_target = (
            self.flavor_naturalness_mean >= self.flavor_naturalness_threshold
        )


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_RULE_SYSTEM = (
    "You are a quality reviewer for Android: Netrunner card translation (EN→KO). "
    "Evaluate whether the Korean translation follows standard Netrunner rule text "
    "sentence patterns (문형). Game-specific conventions: 런(run), 설치(install), "
    "폐기(trash), 레즈(rez), 덱(stack). Use only the provided text, not your general "
    "knowledge of the game."
)

_RULE_HUMAN = (
    "Card ID: {card_id}\n"
    "EN rule text: {en_text}\n"
    "KO translation: {ko_text}\n\n"
    "Score the KO translation on sentence pattern match (문형 일치):\n"
    "3 = Pattern fully matches Netrunner conventions\n"
    "2 = Partial match (some elements off)\n"
    "1 = Pattern does not match conventions\n\n"
    "Respond with the card_id, score (1/2/3), and one-sentence reasoning."
)

_FLAVOR_SYSTEM = (
    "You are a quality reviewer for Android: Netrunner card translation (EN→KO). "
    "Evaluate the naturalness of the Korean card text as read by a Korean speaker. "
    "Focus on fluency and natural expression, not game terminology accuracy."
)

_FLAVOR_HUMAN = (
    "Card ID: {card_id}\n"
    "KO text: {ko_text}\n\n"
    "Score the naturalness of the Korean text:\n"
    "3 = Reads naturally as Korean (자연스러움)\n"
    "2 = Acceptable but slightly unnatural\n"
    "1 = Reads like a literal translation (어색함)\n\n"
    "Respond with the card_id, score (1/2/3), and one-sentence reasoning."
)


# ---------------------------------------------------------------------------
# LLM chain builder
# ---------------------------------------------------------------------------


def _build_llm(region: str = JUDGE_REGION) -> Any:
    """Instantiate the Bedrock Haiku judge LLM.

    Reads AWS credentials from the environment. Raises immediately if boto3
    cannot locate credentials — does not silently fall back to a dry run.
    """
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=JUDGE_MODEL,
        region_name=region,
        temperature=0,
    )


def _build_rule_chain(llm: Any) -> Any:
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages(
        [("system", _RULE_SYSTEM), ("human", _RULE_HUMAN)]
    )
    return prompt | llm.with_structured_output(RulePatternJudgment)


def _build_flavor_chain(llm: Any) -> Any:
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages(
        [("system", _FLAVOR_SYSTEM), ("human", _FLAVOR_HUMAN)]
    )
    return prompt | llm.with_structured_output(FlavorNaturalnessJudgment)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_hold_out(
    hold_out_path: str | Path,
    predictions: list[str],
    *,
    llm: Any = None,
    max_concurrency: int = 4,
) -> ObsReport:
    """Score observation metrics over the hold-out set.

    Args:
        hold_out_path: Path to data/hold_out.json (list of {id, en_text, ko_text}).
        predictions: Agent-generated KO translations, one per card in hold_out order.
        llm: Optional pre-built LangChain LLM. Built automatically if None.
        max_concurrency: Parallel Bedrock requests.

    Returns:
        :class:`ObsReport` with mean/median scores for both metrics.
        These are observation metrics only — no delivery decision is made here.
    """
    cards: list[dict] = json.loads(Path(hold_out_path).read_text(encoding="utf-8"))

    if len(cards) != len(predictions):
        raise ValueError(
            f"predictions length {len(predictions)} != hold-out length {len(cards)}"
        )

    if llm is None:
        llm = _build_llm()

    rule_chain = _build_rule_chain(llm)
    flavor_chain = _build_flavor_chain(llm)

    rule_inputs = [
        {
            "card_id": card.get("id", ""),
            "en_text": card.get("en_text", ""),
            "ko_text": pred,
        }
        for card, pred in zip(cards, predictions)
    ]
    flavor_inputs = [
        {
            "card_id": card.get("id", ""),
            "ko_text": pred,
        }
        for card, pred in zip(cards, predictions)
    ]

    rule_results: list[RulePatternJudgment] = rule_chain.batch(
        rule_inputs,
        config={"max_concurrency": max_concurrency},
    )
    flavor_results: list[FlavorNaturalnessJudgment] = flavor_chain.batch(
        flavor_inputs,
        config={"max_concurrency": max_concurrency},
    )

    observations: list[CardObservation] = []
    for rule_r, flavor_r in zip(rule_results, flavor_results):
        observations.append(
            CardObservation(
                card_id=rule_r.card_id,
                rule_pattern_score=rule_r.score,
                flavor_naturalness_score=flavor_r.score,
                rule_reasoning=rule_r.reasoning,
                flavor_reasoning=flavor_r.reasoning,
            )
        )

    return _aggregate(observations)


def score_from_judgments(
    rule_judgments: list[RulePatternJudgment],
    flavor_judgments: list[FlavorNaturalnessJudgment],
) -> ObsReport:
    """Aggregate pre-computed judgments into an ObsReport.

    Useful for testing or when judgments are obtained outside this module
    (e.g., from a checkpoint or a mocked LLM).
    """
    if len(rule_judgments) != len(flavor_judgments):
        raise ValueError(
            f"rule_judgments length {len(rule_judgments)} != "
            f"flavor_judgments length {len(flavor_judgments)}"
        )

    observations: list[CardObservation] = []
    for rule_r, flavor_r in zip(rule_judgments, flavor_judgments):
        observations.append(
            CardObservation(
                card_id=rule_r.card_id,
                rule_pattern_score=rule_r.score,
                flavor_naturalness_score=flavor_r.score,
                rule_reasoning=rule_r.reasoning,
                flavor_reasoning=flavor_r.reasoning,
            )
        )
    return _aggregate(observations)


def _aggregate(observations: list[CardObservation]) -> ObsReport:
    if not observations:
        return ObsReport(
            rule_pattern_mean=0.0,
            rule_pattern_median=0.0,
            flavor_naturalness_mean=0.0,
            flavor_naturalness_median=0.0,
            n=0,
            card_observations=[],
        )

    rule_scores = [o.rule_pattern_score for o in observations]
    flavor_scores = [o.flavor_naturalness_score for o in observations]

    return ObsReport(
        rule_pattern_mean=statistics.mean(rule_scores),
        rule_pattern_median=statistics.median(rule_scores),
        flavor_naturalness_mean=statistics.mean(flavor_scores),
        flavor_naturalness_median=statistics.median(flavor_scores),
        n=len(observations),
        card_observations=observations,
    )


def format_report(report: ObsReport) -> str:
    """Format an ObsReport as a human-readable summary string."""
    rule_status = "✓" if report.rule_pattern_meets_target else "△"
    flavor_status = "✓" if report.flavor_naturalness_meets_target else "△"

    lines = [
        "=== 관찰 지표 보고 (Observation Metrics Report) ===",
        f"카드 수: {report.n}",
        "",
        f"룰 텍스트 문형 일치 (Rule Pattern Match) [{rule_status}]",
        f"  평균: {report.rule_pattern_mean:.3f} / 3.0  (목표 >= {report.rule_pattern_threshold})",
        f"  중앙값: {report.rule_pattern_median:.1f}",
        "",
        f"플레이버 자연스러움 (Flavor Naturalness) [{flavor_status}]",
        f"  평균: {report.flavor_naturalness_mean:.3f} / 3.0  (목표 >= {report.flavor_naturalness_threshold})",
        f"  중앙값: {report.flavor_naturalness_median:.1f}",
        "",
        "※ 관찰 지표는 납품 거부 사유가 아닙니다.",
    ]
    return "\n".join(lines)
