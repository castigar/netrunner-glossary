"""term_judge.py — LLM accept/reject judgment for term candidates.

SERVICE.md §6 pipeline step 4 (LCEL + Pydantic 검증):
  LLM은 가부 판정만 수행한다. 후보 생성은 term_candidate_extractor.py가 담당하며
  LLM을 호출하지 않는다.

Uses LCEL chain with Pydantic structured output (TermJudgment) to decide
whether each statistically-generated candidate is a valid term pair.

청크 + 체크포인트
-----------------
후보 수천 건을 ``chain.batch()`` 한 번으로 돌리면 결과가 전부 메모리에만 쌓이고
디스크에는 맨 끝에야 쓰인다.  중간에 죽으면 판정 결과 전량과 그때까지 쓴 비용이
같이 사라지고 재개 지점도 없다.  그래서 *chunk_size*씩 끊어 돌리고 청크마다
판정 결과를 JSONL로 append한다.  이미 판정된 (en, ko)는 건너뛰므로 재실행이
증분이 된다 — SERVICE.md §6이 1단계 배치를 "증분 재실행"으로 규정한 것과 같다.

또 하나: 청크 경계마다 *on_progress* 콜백이 불린다.  이게 없으면 밖에서
"스로틀링으로 기어가는 중"과 "크리덴셜 문제로 전부 예외 처리되는 중"을 구분할
방법이 없고, 30분 뒤에야 accepted=0 dropped=4914를 보게 된다.

프롬프트 지문
-------------
판정 레코드마다 프롬프트 지문을 같이 적는다.  프롬프트를 고치면 지문이 달라지고
낡은 판정이 자동으로 무효가 된다.  없으면 프롬프트를 바꿔도 이전 판정이 조용히
재사용되어, 무엇에 대한 판정인지 알 수 없는 캐시가 된다.

지문을 쓰지 **않는** 것이 하나 있다: 형태소 정규화기다.  캐시 키가 (en_term,
ko_term)이고 ko_term이 이미 어간이므로, 정규화기를 바꿔 같은 어간이 나오면
키가 맞아 재사용되고(맞다) 다른 어간이 나오면 그것은 애초에 다른 후보다(맞다).
정규화기 지문을 키에 넣으면 어간이 그대로인 판정까지 버리게 되어 더 나빠진다.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler
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


SYSTEM_PROMPT = (
    "You are a terminology validator for Android: Netrunner card game translation. "
    "You receive a candidate EN→KO term pair extracted by corpus statistics. "
    "Accept the pair if it is a genuine game-rule term (e.g. 'install'→'설치', "
    "'trash'→'폐기', 'rez'→'레즈'). "
    "Reject if it is a stop-word pair, fragment, or statistical artifact."
)

HUMAN_PROMPT = (
    "Candidate term pair:\n"
    "  EN: {en_term}\n"
    "  KO: {ko_term}\n"
    "  Cooccurrence: {cooccurrence}\n"
    "  Dice: {dice:.4f}\n"
    "  PMI: {pmi:.4f}\n\n"
    "Accept or reject this term pair?"
)


def prompt_fingerprint() -> str:
    """Short stable digest of the judging prompt.

    Recorded on every judgment so a prompt edit invalidates the checkpoint
    instead of silently reusing verdicts the current prompt never produced.
    """
    digest = hashlib.sha256()
    digest.update(SYSTEM_PROMPT.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(HUMAN_PROMPT.encode("utf-8"))
    return digest.hexdigest()[:16]


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
        [("system", SYSTEM_PROMPT), ("human", HUMAN_PROMPT)]
    )

    structured_llm = llm.with_structured_output(TermJudgment)
    return prompt | structured_llm


DEFAULT_MAX_CONCURRENCY = 8
DEFAULT_CHUNK_SIZE = 250


def load_judgments(
    checkpoint_path: str | Path, fingerprint: str | None = None
) -> dict[tuple[str, str], TermJudgment]:
    """Read a checkpoint JSONL into {(en_term, ko_term): TermJudgment}.

    Records whose ``prompt_fingerprint`` differs from *fingerprint* are dropped:
    they are verdicts from a prompt that no longer exists.  A record with no
    fingerprint at all predates this field, so it is dropped too — reusing a
    verdict whose prompt cannot be identified is the failure this guards.
    Pass fingerprint=None to read every record regardless (for inspection).

    A missing file is an empty checkpoint, not an error — that is the first run.
    A truncated last line (killed mid-write) is skipped rather than fatal.
    """
    path = Path(checkpoint_path)
    judged: dict[tuple[str, str], TermJudgment] = {}
    if not path.exists():
        return judged
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if fingerprint is not None and record.get("prompt_fingerprint") != fingerprint:
                continue
            try:
                judgment = TermJudgment(**record)
            except Exception:
                continue
            judged[(judgment.en_term, judgment.ko_term)] = judgment
    return judged


def _append_judgments(
    checkpoint_path: Path, judgments: list[TermJudgment], fingerprint: str
) -> None:
    """Append a chunk's judgments to the checkpoint and flush to disk."""
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint_path.open("a", encoding="utf-8") as fh:
        for judgment in judgments:
            record = judgment.model_dump()
            record["prompt_fingerprint"] = fingerprint
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()


class _AttemptFailureCounter(BaseCallbackHandler):
    """Counts failed model calls by exception class.

    dropped=0 says nothing was ultimately lost; it does not say the concurrency
    level had headroom, because retries absorb throttling silently.  This is the
    number that says whether there was headroom.
    """

    def __init__(self, sink: Counter) -> None:
        self._sink = sink

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self._sink[type(error).__name__] += 1


def judge_candidates(
    candidates: list[TermCandidate],
    llm: Any,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    checkpoint_path: str | Path | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> tuple[list[TermJudgment], list[TermJudgment]]:
    """Judge each candidate as accepted or rejected using the LLM.

    Args:
        candidates:      Output of generate_candidates() — statistically derived,
                         produced without any LLM calls.
        llm:             LangChain chat model for structured-output judgment.
        max_concurrency: Parallel in-flight requests.  Judging the capped rule
                         path is a few thousand independent yes/no calls, so
                         serially it costs over an hour of wall clock for no
                         reason.
        chunk_size:      Candidates per batch.  Each chunk is checkpointed and
                         reported before the next one starts.
        checkpoint_path: JSONL of judgments so far.  Already-judged pairs are
                         skipped, so a killed run resumes instead of restarting.
                         None disables checkpointing entirely.
        on_progress:     Called once per chunk with a stats dict: ``done``,
                         ``total``, ``accepted``, ``rejected``, ``dropped``,
                         ``retries`` and ``errors`` (exception class -> count).

    Returns:
        Tuple of (accepted, rejected) TermJudgment lists, including any restored
        from the checkpoint.

    A candidate whose call raised is **not** silently accepted, and it is not
    counted as rejected either — it is dropped and left out of the checkpoint,
    so a later run retries it.  Inventing a judgment the model never made is the
    one failure mode worth ruling out.
    """
    # Bedrock throttles under concurrency; without a retry a throttled candidate
    # would be dropped, silently shrinking the judged set.
    #
    # Retries are counted, not just absorbed.  dropped=0 only means nothing was
    # lost after retrying — it is not evidence that the concurrency level had
    # headroom.  The retry count is what says that.
    # with_retry() exposes no retry hook, so failed attempts are counted through
    # the callback surface instead: on_llm_error fires once per failed call.
    attempt_failures: Counter[str] = Counter()

    fingerprint = prompt_fingerprint()
    chain = build_judge_chain(llm).with_retry(
        stop_after_attempt=3, wait_exponential_jitter=True
    )

    judged: dict[tuple[str, str], TermJudgment] = (
        load_judgments(checkpoint_path, fingerprint) if checkpoint_path else {}
    )
    pending = [c for c in candidates if (c.en_term, c.ko_term) not in judged]

    accepted: list[TermJudgment] = []
    rejected: list[TermJudgment] = []
    for judgment in judged.values():
        (accepted if judgment.accepted else rejected).append(judgment)

    total = len(candidates)
    dropped = 0
    errors: Counter[str] = Counter()

    for start in range(0, len(pending), chunk_size):
        chunk = pending[start : start + chunk_size]
        results = chain.batch(
            [
                {
                    "en_term": c.en_term,
                    "ko_term": c.ko_term,
                    "cooccurrence": c.cooccurrence,
                    "dice": c.dice,
                    "pmi": c.pmi,
                }
                for c in chunk
            ],
            config={
                "max_concurrency": max_concurrency,
                "callbacks": [_AttemptFailureCounter(attempt_failures)],
            },
            return_exceptions=True,
        )

        fresh: list[TermJudgment] = []
        for result in results:
            if not isinstance(result, TermJudgment):
                dropped += 1
                errors[type(result).__name__] += 1
                continue
            fresh.append(result)
            (accepted if result.accepted else rejected).append(result)

        if checkpoint_path and fresh:
            _append_judgments(Path(checkpoint_path), fresh, fingerprint)

        if on_progress is not None:
            on_progress(
                {
                    "done": len(accepted) + len(rejected) + dropped,
                    "total": total,
                    "accepted": len(accepted),
                    "rejected": len(rejected),
                    "dropped": dropped,
                    # Failed attempts, not lost candidates: a pair that succeeded
                    # on its third try contributes 2 here and 0 to dropped.
                    "attempt_failures": sum(attempt_failures.values()),
                    "attempt_failure_kinds": dict(attempt_failures),
                    "errors": dict(errors),
                }
            )

    return accepted, rejected
