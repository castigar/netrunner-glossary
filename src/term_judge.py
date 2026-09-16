"""term_judge.py — LLM accept/reject judgment for term candidates.

SERVICE.md §6 pipeline step 4 (LCEL + Pydantic 검증):
  LLM은 가부 판정만 수행한다. 후보 생성은 term_candidate_extractor.py가 담당하며
  LLM을 호출하지 않는다.

Uses LCEL chain with Pydantic structured output (TermJudgment) to decide
whether each statistically-generated candidate is a valid term pair.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from term_candidate_extractor import TermCandidate


class TermPair(BaseModel):
    """A term pair submitted to the LLM for accept/reject judgment."""

    en_term: str = Field(description="English term or n-gram")
    ko_term: str = Field(description="Korean eojeol n-gram translation")
    cooccurrence: int = Field(description="Number of cards where both terms appear")
    dice: float = Field(description="Dice coefficient (0-1, higher=more correlated)")
    pmi: float = Field(description="Pointwise Mutual Information score")


class TermJudgment(BaseModel):
    """LLM structured output for a single accept/reject decision."""

    en_term: str = Field(description="The English term being judged")
    ko_term: str = Field(description="The Korean term being judged")
    accepted: bool = Field(
        description=(
            "True if this is a valid EN→KO term pair that should enter the glossary. "
            "False if it is noise, a stop-word pair, or a non-term n-gram."
        )
    )
    reason: str = Field(
        description="One-sentence reason for the accept/reject decision"
    )


def build_judge_chain(llm: Any) -> Any:
    """Build an LCEL chain that accepts a TermPair and returns TermJudgment.

    The LLM is used **only** for accept/reject judgment, not for generating
    candidates.  Candidate generation is performed by generate_candidates()
    in term_candidate_extractor.py (0 LLM calls).

    Args:
        llm: A LangChain chat model instance that supports .with_structured_output().

    Returns:
        An LCEL chain: TermPair → TermJudgment.
    """
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "You are a terminology validator for Android: Netrunner card game translation. "
                    "You receive a candidate EN→KO term pair extracted by corpus statistics. "
                    "Accept the pair if it is a genuine game-rule term (e.g. 'install'→'설치', "
                    "'trash'→'파기', 'rez'→'레즈'). "
                    "Reject if it is a stop-word pair, fragment, or statistical artifact."
                ),
            ),
            (
                "human",
                (
                    "Candidate term pair:\n"
                    "  EN: {en_term}\n"
                    "  KO: {ko_term}\n"
                    "  Cooccurrence: {cooccurrence}\n"
                    "  Dice: {dice:.4f}\n"
                    "  PMI: {pmi:.4f}\n\n"
                    "Accept or reject this term pair?"
                ),
            ),
        ]
    )

    structured_llm = llm.with_structured_output(TermJudgment)
    return prompt | structured_llm


def judge_candidates(
    candidates: list[TermCandidate],
    llm: Any,
) -> tuple[list[TermJudgment], list[TermJudgment]]:
    """Judge each candidate as accepted or rejected using the LLM.

    Args:
        candidates: Output of generate_candidates() — statistically derived,
                    produced without any LLM calls.
        llm:        LangChain chat model for structured-output judgment.

    Returns:
        Tuple of (accepted, rejected) TermJudgment lists.
    """
    chain = build_judge_chain(llm)
    accepted: list[TermJudgment] = []
    rejected: list[TermJudgment] = []

    for cand in candidates:
        result: TermJudgment = chain.invoke(
            {
                "en_term": cand.en_term,
                "ko_term": cand.ko_term,
                "cooccurrence": cand.cooccurrence,
                "dice": cand.dice,
                "pmi": cand.pmi,
            }
        )
        if result.accepted:
            accepted.append(result)
        else:
            rejected.append(result)

    return accepted, rejected
