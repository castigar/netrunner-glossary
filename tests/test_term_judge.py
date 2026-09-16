"""Tests for term_judge.py — 청크 + 체크포인트 + 진행 보고.

The point of these tests is the failure mode that cost a 30-minute run: a single
in-memory batch whose results reach disk only at the very end, with no way from
outside to tell "slow" from "failing every call".
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from term_candidate_extractor import TermCandidate  # noqa: E402
from term_judge import (  # noqa: E402
    TermJudgment,
    build_judge_chain,
    judge_candidates,
    load_judgments,
)


class FakeLLM:
    """Stands in for a chat model. *verdicts* maps en_term -> accept/reject/raise."""

    def __init__(self, verdicts, fail_with=RuntimeError("boom")):
        self.verdicts = verdicts
        self.fail_with = fail_with
        self.calls = []

    def with_structured_output(self, _schema):
        from langchain_core.runnables import RunnableLambda

        def respond(prompt_value):
            text = prompt_value.to_string()
            en = text.split("EN: ")[1].split("\n")[0].strip()
            ko = text.split("KO: ")[1].split("\n")[0].strip()
            self.calls.append(en)
            verdict = self.verdicts.get(en, "accept")
            if verdict == "raise":
                raise self.fail_with
            return TermJudgment(
                en_term=en, ko_term=ko, accepted=verdict == "accept", reason="test"
            )

        return RunnableLambda(respond)


def cands(*names):
    return [TermCandidate(n, f"역{n}", 10, 0.5, 2.0) for n in names]


# ---------- chunking ----------


def test_all_candidates_are_judged_across_chunks():
    llm = FakeLLM({})
    accepted, rejected = judge_candidates(
        cands("a", "b", "c", "d", "e"), llm, chunk_size=2
    )
    assert len(accepted) == 5
    assert rejected == []


def test_progress_is_reported_once_per_chunk():
    seen = []
    llm = FakeLLM({})
    judge_candidates(
        cands("a", "b", "c", "d", "e"),
        llm,
        chunk_size=2,
        on_progress=seen.append,
    )
    assert [s["done"] for s in seen] == [2, 4, 5]
    assert all(s["total"] == 5 for s in seen)


def test_progress_reports_accept_reject_split():
    seen = []
    judge_candidates(
        cands("a", "b"),
        FakeLLM({"b": "reject"}),
        chunk_size=2,
        on_progress=seen.append,
    )
    assert seen[-1]["accepted"] == 1
    assert seen[-1]["rejected"] == 1
    assert seen[-1]["dropped"] == 0


# ---------- dropped candidates ----------


def test_failed_call_is_dropped_not_rejected():
    """A call that raised must not be recorded as a 'no' the model never said."""
    accepted, rejected = judge_candidates(cands("a", "b"), FakeLLM({"b": "raise"}))
    assert [j.en_term for j in accepted] == ["a"]
    assert rejected == []


def test_dropped_count_and_error_classes_are_reported():
    seen = []
    judge_candidates(
        cands("a", "b"),
        FakeLLM({"b": "raise"}, fail_with=ValueError("nope")),
        on_progress=seen.append,
    )
    assert seen[-1]["dropped"] == 1
    assert seen[-1]["errors"] == {"ValueError": 1}


# ---------- checkpoint ----------


def test_checkpoint_is_written_per_chunk(tmp_path):
    ckpt = tmp_path / "judgments.jsonl"
    written = []

    seen = []

    def record(stats):
        seen.append(stats)
        written.append(len(ckpt.read_text(encoding="utf-8").splitlines()))

    judge_candidates(cands("a", "b", "c", "d"), FakeLLM({}), chunk_size=2,
                     checkpoint_path=ckpt, on_progress=record)
    # The file grows as chunks finish — it is not written once at the end.
    assert written == [2, 4]


def test_checkpoint_round_trips(tmp_path):
    ckpt = tmp_path / "judgments.jsonl"
    judge_candidates(cands("a", "b"), FakeLLM({"b": "reject"}), checkpoint_path=ckpt)
    restored = load_judgments(ckpt)
    assert restored[("a", "역a")].accepted is True
    assert restored[("b", "역b")].accepted is False


def test_resume_skips_already_judged(tmp_path):
    ckpt = tmp_path / "judgments.jsonl"
    first = FakeLLM({})
    judge_candidates(cands("a", "b"), first, checkpoint_path=ckpt)
    assert first.calls == ["a", "b"]

    second = FakeLLM({})
    accepted, _ = judge_candidates(cands("a", "b", "c"), second, checkpoint_path=ckpt)
    assert second.calls == ["c"], "already-judged pairs must not be re-sent"
    assert len(accepted) == 3


def test_dropped_candidates_are_retried_on_resume(tmp_path):
    """A dropped candidate is absent from the checkpoint, so the next run retries."""
    ckpt = tmp_path / "judgments.jsonl"
    judge_candidates(cands("a", "b"), FakeLLM({"b": "raise"}), checkpoint_path=ckpt)
    assert set(load_judgments(ckpt)) == {("a", "역a")}

    second = FakeLLM({})
    accepted, _ = judge_candidates(cands("a", "b"), second, checkpoint_path=ckpt)
    assert second.calls == ["b"]
    assert len(accepted) == 2


def test_missing_checkpoint_is_an_empty_one(tmp_path):
    assert load_judgments(tmp_path / "nope.jsonl") == {}


def test_truncated_last_line_is_skipped_not_fatal(tmp_path):
    """A run killed mid-write leaves a partial line; it must not poison resume."""
    ckpt = tmp_path / "judgments.jsonl"
    good = TermJudgment(en_term="a", ko_term="역a", accepted=True, reason="x")
    ckpt.write_text(
        json.dumps(good.model_dump(), ensure_ascii=False) + "\n{\"en_term\": \"b\"",
        encoding="utf-8",
    )
    assert set(load_judgments(ckpt)) == {("a", "역a")}


def test_no_checkpoint_path_writes_nothing(tmp_path):
    judge_candidates(cands("a"), FakeLLM({}), checkpoint_path=None)
    assert list(tmp_path.iterdir()) == []


# ---------- the prompt's few-shot examples ----------


def test_prompt_examples_match_the_official_translations():
    """The corpus has 폐기 232 times and 파기 zero; teaching 파기 biases judging."""
    source = Path(build_judge_chain.__globals__["__file__"]).read_text(encoding="utf-8")
    assert "'trash'→'폐기'" in source
    assert "파기" not in source


# ---------- 프롬프트 지문 ----------


def test_fingerprint_is_recorded_on_every_judgment(tmp_path):
    from term_judge import prompt_fingerprint

    ckpt = tmp_path / "judgments.jsonl"
    judge_candidates(cands("a"), FakeLLM({}), checkpoint_path=ckpt)
    record = json.loads(ckpt.read_text(encoding="utf-8").splitlines()[0])
    assert record["prompt_fingerprint"] == prompt_fingerprint()


def test_fingerprint_changes_when_the_prompt_changes(monkeypatch):
    import term_judge
    from term_judge import prompt_fingerprint

    before = prompt_fingerprint()
    monkeypatch.setattr(term_judge, "SYSTEM_PROMPT", term_judge.SYSTEM_PROMPT + " edited")
    assert prompt_fingerprint() != before


def test_judgments_from_another_prompt_are_not_reused(tmp_path, monkeypatch):
    """A prompt edit must invalidate the checkpoint, not silently reuse verdicts."""
    import term_judge

    ckpt = tmp_path / "judgments.jsonl"
    judge_candidates(cands("a", "b"), FakeLLM({}), checkpoint_path=ckpt)

    monkeypatch.setattr(term_judge, "SYSTEM_PROMPT", term_judge.SYSTEM_PROMPT + " edited")
    second = FakeLLM({})
    judge_candidates(cands("a", "b"), second, checkpoint_path=ckpt)
    assert second.calls == ["a", "b"], "stale-prompt verdicts must be re-judged"


def test_same_prompt_still_resumes(tmp_path):
    ckpt = tmp_path / "judgments.jsonl"
    judge_candidates(cands("a", "b"), FakeLLM({}), checkpoint_path=ckpt)
    second = FakeLLM({})
    judge_candidates(cands("a", "b"), second, checkpoint_path=ckpt)
    assert second.calls == [], "unchanged prompt must reuse the checkpoint"


def test_records_without_a_fingerprint_are_not_reused(tmp_path):
    """Predates the field, so the prompt behind the verdict cannot be identified."""
    ckpt = tmp_path / "judgments.jsonl"
    ckpt.write_text(
        json.dumps(
            {"en_term": "a", "ko_term": "역a", "accepted": True, "reason": "x"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    llm = FakeLLM({})
    judge_candidates(cands("a"), llm, checkpoint_path=ckpt)
    assert llm.calls == ["a"]


def test_load_judgments_without_fingerprint_filter_reads_everything(tmp_path):
    ckpt = tmp_path / "judgments.jsonl"
    ckpt.write_text(
        json.dumps(
            {"en_term": "a", "ko_term": "역a", "accepted": True, "reason": "x"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert set(load_judgments(ckpt)) == {("a", "역a")}


# ---------- 실패 시도 집계 ----------


def test_attempt_failure_counter_tallies_by_exception_class():
    """Unit-level: FakeLLM raises inside a RunnableLambda, which reports a chain
    error rather than on_llm_error, so the handler is exercised directly here.
    The real Bedrock path emits on_llm_error per failed call."""
    from collections import Counter

    from term_judge import _AttemptFailureCounter

    sink = Counter()
    handler = _AttemptFailureCounter(sink)
    handler.on_llm_error(ValueError("throttled"))
    handler.on_llm_error(ValueError("throttled"))
    handler.on_llm_error(RuntimeError("other"))
    assert dict(sink) == {"ValueError": 2, "RuntimeError": 1}


def test_attempt_failures_are_reported_separately_from_dropped():
    """dropped counts lost candidates; attempt_failures counts failed calls.
    A pair that succeeds on its third try adds 2 here and 0 to dropped."""
    seen = []
    judge_candidates(
        cands("a", "b"),
        FakeLLM({"b": "raise"}, fail_with=ValueError("nope")),
        on_progress=seen.append,
    )
    assert seen[-1]["dropped"] == 1
    assert "attempt_failures" in seen[-1]
    assert "attempt_failure_kinds" in seen[-1]


def test_clean_run_reports_no_attempt_failures():
    seen = []
    judge_candidates(cands("a", "b"), FakeLLM({}), on_progress=seen.append)
    assert seen[-1]["attempt_failures"] == 0
    assert seen[-1]["dropped"] == 0
