"""test_ac6_run_provenance.py — AC6 run provenance, stub prevention, echo gate verdict.

These tests cover the parts of AC6 that concern *where the scored numbers came
from* rather than how the gates score:

  - stub_prevention: the three entry points that could otherwise reach a gate
    score computed from stub output (run_pipeline's CLI, evaluate_pipeline's
    --run-pipeline, and --pipeline-output pointed at an arbitrary jsonl).
  - run_model_provenance: the fallback chain stays inside the available model
    list, never degrades to the stub, and records what it tried.
  - mixed runs: gate numbers are broken down per producing model.
  - the final verdict combines the three hard gates with the TM echo gate, and
    is withheld entirely unless run_mode is "real".

Each assertion states an expected value that the implementation could get
wrong — none of them restate a threshold comparison the implementation defines.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluate_pipeline import (  # noqa: E402
    _load_pipeline_output,
    _read_run_header,
    main,
    print_evaluation_report,
    run_evaluation,
)

HOLD_OUT = ROOT / "data" / "hold_out.json"
GLOSSARY = ROOT / "assets" / "glossary.json"


@pytest.fixture(scope="module")
def hold_out_cards() -> list[dict]:
    return json.loads(HOLD_OUT.read_text(encoding="utf-8"))


def _header(run_mode=None, **extra) -> dict:
    header = {"_meta": "run_pipeline_header", "tm_threshold_derivation": {}}
    if run_mode is not None:
        header["run_mode"] = run_mode
    header.update(extra)
    return header


def _record(card_id, **extra) -> dict:
    rec = {
        "card_id": card_id,
        "field": "text",
        "route": "rule",
        "draft_ko": "번역",
        "interrupted": False,
        "tm_hits": [],
        "injected_terms": [],
        "tm_confidence": 0.0,
        "glossary_llm_judged": True,
    }
    rec.update(extra)
    return rec


def _write_jsonl(path: Path, objs: list[dict]) -> Path:
    with path.open("w", encoding="utf-8") as f:
        for obj in objs:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------
# stub_prevention
# ---------------------------------------------------------------------------


class TestStubOutputCannotBeScored:
    """An arbitrary jsonl must not be scored as if it were a real model run."""

    def _run_main(self, output_path, extra_argv=()):
        orig = sys.argv[:]
        sys.argv = [
            "evaluate_pipeline",
            "--data-dir", str(HOLD_OUT.parent),
            "--assets-dir", str(GLOSSARY.parent),
            "--pipeline-output", str(output_path),
            "--skip-llm-judge",
            *extra_argv,
        ]
        try:
            main()
        finally:
            sys.argv = orig

    def test_stub_output_is_refused_without_explicit_flag(self, tmp_path, hold_out_cards):
        p = _write_jsonl(
            tmp_path / "stub.jsonl",
            [_header("stub"), _record(hold_out_cards[0]["id"])],
        )
        with pytest.raises(SystemExit) as exc:
            self._run_main(p)
        assert exc.value.code != 0

    def test_headerless_output_is_refused_without_explicit_flag(self, tmp_path, hold_out_cards):
        """A stub-era file whose header predates run_mode is refused too.

        This is the concrete regression being closed: the Gen-4
        pipeline_output.jsonl left in the repo reproduced a full set of gate
        numbers with no flag at all.
        """
        p = _write_jsonl(
            tmp_path / "legacy.jsonl",
            [_header(None), _record(hold_out_cards[0]["id"])],
        )
        with pytest.raises(SystemExit) as exc:
            self._run_main(p)
        assert exc.value.code != 0

    def test_real_output_is_accepted_without_the_flag(self, tmp_path, hold_out_cards, capsys):
        """The guard is about provenance, not about making every file hard to read."""
        p = _write_jsonl(
            tmp_path / "real.jsonl",
            [_header("real"), _record(hold_out_cards[0]["id"])],
        )
        self._run_main(p)
        out = capsys.readouterr().out
        assert "실행 모드: REAL" in out

    def test_stub_output_with_flag_still_yields_no_gate_verdict(
        self, tmp_path, hold_out_cards, capsys
    ):
        """--allow-stub-output permits inspection but never produces a verdict."""
        p = _write_jsonl(
            tmp_path / "stub.jsonl",
            [_header("stub"), _record(hold_out_cards[0]["id"])],
        )
        self._run_main(p, ["--allow-stub-output"])
        out = capsys.readouterr().out
        assert "최종 판정: 보류" in out
        assert "생략" in out


class TestRunPipelineCLIStubOptIn:
    """run_pipeline's stub is reachable only behind an explicit flag."""

    def test_cli_refuses_without_model_or_stub_flag(self, monkeypatch):
        import run_pipeline as rp

        monkeypatch.setattr(sys, "argv", ["run_pipeline", "--cards", "1"])
        with pytest.raises(SystemExit) as exc:
            rp.main()
        assert exc.value.code != 0

    def test_cli_refuses_model_and_stub_together(self, monkeypatch):
        import run_pipeline as rp

        monkeypatch.setattr(
            sys,
            "argv",
            ["run_pipeline", "--cards", "1", "--allow-stub", "--llm-model", "some.model"],
        )
        with pytest.raises(SystemExit) as exc:
            rp.main()
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# run_model_provenance: the fallback chain
# ---------------------------------------------------------------------------


class TestFallbackChain:
    """Fallback stays inside the available-model list and never reaches the stub."""

    def test_fallback_order_starts_with_primary_and_lists_no_duplicates(self):
        from run_pipeline import FALLBACK_MODELS, _FallbackLLM

        primary = "us.amazon.nova-pro-v1:0"
        llm = _FallbackLLM(primary)
        assert llm._model_ids[0] == primary
        assert len(llm._model_ids) == len(set(llm._model_ids))
        assert set(llm._model_ids) <= set(FALLBACK_MODELS) | {primary}

    def test_all_models_failing_raises_instead_of_degrading_to_the_stub(self, monkeypatch):
        import time as _time

        import run_pipeline as rp

        class _Boom:
            def __init__(self, model):
                self.model = model

            def invoke(self, prompt):
                raise RuntimeError("ValidationException: model not available")

        monkeypatch.setattr("langchain_aws.ChatBedrockConverse", _Boom)
        monkeypatch.setattr(_time, "sleep", lambda *_: None)

        llm = rp._FallbackLLM("global.anthropic.claude-haiku-4-5-20251001-v1:0")
        with pytest.raises(RuntimeError):
            llm.invoke("hello")

        attempted = {a["model_id"] for a in llm.model_attempts_log}
        assert attempted == set(llm._model_ids)
        assert all(a["success"] is False for a in llm.model_attempts_log)
        assert llm.models_used == set()

    def test_throttled_primary_is_retried_then_the_next_model_is_used(self, monkeypatch):
        """A run that fell back is still a real run, and the used model is recorded."""
        import time as _time

        import run_pipeline as rp

        primary = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        second = rp.FALLBACK_MODELS[1]

        class _Flaky:
            def __init__(self, model):
                self.model = model

            def invoke(self, prompt):
                if self.model == primary:
                    raise RuntimeError("ThrottlingException: slow down")
                return type("R", (), {"content": "번역"})()

        monkeypatch.setattr("langchain_aws.ChatBedrockConverse", _Flaky)
        monkeypatch.setattr(_time, "sleep", lambda *_: None)

        llm = rp._FallbackLLM(primary)
        llm.invoke("hello")

        assert llm.last_model_id == second
        assert llm.models_used == {second}
        primary_attempts = [a for a in llm.model_attempts_log if a["model_id"] == primary]
        assert len(primary_attempts) == 3
        assert all(a["retryable"] for a in primary_attempts)

    def test_non_retryable_failure_moves_on_without_burning_retries(self, monkeypatch):
        import time as _time

        import run_pipeline as rp

        primary = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        second = rp.FALLBACK_MODELS[1]

        class _Denied:
            def __init__(self, model):
                self.model = model

            def invoke(self, prompt):
                if self.model == primary:
                    raise RuntimeError("AccessDeniedException")
                return type("R", (), {"content": "번역"})()

        monkeypatch.setattr("langchain_aws.ChatBedrockConverse", _Denied)
        monkeypatch.setattr(_time, "sleep", lambda *_: None)

        llm = rp._FallbackLLM(primary)
        llm.invoke("hello")

        primary_attempts = [a for a in llm.model_attempts_log if a["model_id"] == primary]
        assert len(primary_attempts) == 1
        assert primary_attempts[0]["retryable"] is False
        assert llm.last_model_id == second


class TestModelInvocationFailure:
    """A field whose models all failed yields an empty prediction, not a guess."""

    def test_failure_record_is_empty_and_carries_its_cause(self):
        from run_pipeline import _empty_model_failure_record

        card = {"id": "c1", "en_text": "Trash a card.", "ko_text": "카드를 폐기한다."}
        initial = {"card_id": "c1", "en_rule": "Trash a card.", "en_flavor": ""}
        rec = _empty_model_failure_record(card, initial, "boom")

        assert rec["draft_ko"] == ""
        assert rec["empty_cause"] == "model_invocation_failure"
        assert rec["field"] == "text"
        assert rec["model_id"] is None
        # reference_leakage_ban: the reference answer never enters the record.
        assert card["ko_text"] not in json.dumps(rec, ensure_ascii=False)

    def test_flavor_only_input_produces_a_flavor_failure_record(self):
        from run_pipeline import _empty_model_failure_record

        card = {"id": "c1"}
        initial = {"card_id": "c1", "en_rule": "", "en_flavor": "A quiet night."}
        rec = _empty_model_failure_record(card, initial, "boom")
        assert rec["field"] == "flavor"
        assert rec["route"] == "flavor"

    def test_failure_is_counted_under_its_own_cause_not_tm_search_failure(
        self, tmp_path, hold_out_cards
    ):
        card = hold_out_cards[0]
        p = _write_jsonl(
            tmp_path / "p.jsonl",
            [
                _header("real"),
                _record(
                    card["id"],
                    draft_ko="",
                    empty_cause="model_invocation_failure",
                    model_id=None,
                ),
            ],
        )
        preds, _, _, _, synthesis, _ = _load_pipeline_output(p, HOLD_OUT)

        assert synthesis.empty_by_cause["model_invocation_failure"] == 1
        assert synthesis.empty_by_cause["tm_search_failure"] == 0
        # The empty prediction is never backfilled with the reference.
        assert preds[0] == ""


# ---------------------------------------------------------------------------
# Mixed runs and the combined final verdict
# ---------------------------------------------------------------------------


class TestMixedRunReporting:
    """A mixed run's single average represents no model, so gates break down per model."""

    def _mixed_output(self, tmp_path, hold_out_cards) -> Path:
        a, b = hold_out_cards[0], hold_out_cards[1]
        return _write_jsonl(
            tmp_path / "mixed.jsonl",
            [
                _header(
                    "real",
                    primary_model_id="model-a",
                    models_used=["model-a", "model-b"],
                    model_record_counts={"model-a": 1, "model-b": 1},
                    model_attempts_log=[
                        {
                            "model_id": "model-a",
                            "attempt": 1,
                            "success": False,
                            "error": "ThrottlingException",
                            "retryable": True,
                        },
                        {"model_id": "model-b", "attempt": 1, "success": True},
                    ],
                ),
                _record(
                    a["id"],
                    draft_ko="가나다",
                    model_id="model-a",
                    tm_hits=[{"id": "t", "en_text": "x", "ko_text": "TM", "score": 0.5}],
                ),
                _record(
                    b["id"],
                    draft_ko="라마바",
                    model_id="model-b",
                    tm_hits=[{"id": "t", "en_text": "x", "ko_text": "TM", "score": 0.5}],
                ),
            ],
        )

    def _report(self, path):
        provenance = _read_run_header(path)
        preds, _, _, _, synthesis, _ = _load_pipeline_output(path, HOLD_OUT)
        return provenance, run_evaluation(
            HOLD_OUT,
            GLOSSARY,
            preds,
            skip_llm_judge=True,
            field_synthesis=synthesis,
            tm_baseline_median=0.4,
            provenance=provenance,
        )

    def test_per_model_breakdown_splits_cards_by_producing_model(
        self, tmp_path, hold_out_cards
    ):
        provenance, report = self._report(self._mixed_output(tmp_path, hold_out_cards))

        assert provenance.mixed_run is True
        assert {pm.model_id for pm in report.per_model_gates} == {"model-a", "model-b"}
        for pm in report.per_model_gates:
            assert pm.card_count == 1
            assert pm.field_record_count == 1
            # Neither draft copies its TM top-1, so neither model echoes.
            assert pm.echo_rate == 0.0

    def test_single_model_run_is_not_reported_as_mixed(self, tmp_path, hold_out_cards):
        p = _write_jsonl(
            tmp_path / "single.jsonl",
            [
                _header("real", primary_model_id="model-a", models_used=["model-a"]),
                _record(hold_out_cards[0]["id"], model_id="model-a"),
            ],
        )
        provenance, report = self._report(p)
        assert provenance.mixed_run is False
        assert [pm.model_id for pm in report.per_model_gates] == ["model-a"]

    def test_mixed_run_is_named_and_broken_down_in_the_printed_report(
        self, tmp_path, hold_out_cards, capsys
    ):
        _, report = self._report(self._mixed_output(tmp_path, hold_out_cards))
        print_evaluation_report(report)
        out = capsys.readouterr().out

        assert "혼합 실행" in out
        assert "모델별 게이트 수치" in out
        assert "model-a" in out and "model-b" in out
        # The reason the first model was skipped is disclosed, not hidden.
        assert "ThrottlingException" in out


class TestFinalVerdictCombinesEchoGate:
    """final_passed is the three hard gates AND the echo gate — and only for real runs."""

    def _report(self, tmp_path, hold_out_cards, drafts_echo_tm: bool):
        objs: list[dict] = [_header("real", models_used=["model-a"])]
        for card in hold_out_cards[:10]:
            objs.append(
                _record(
                    card["id"],
                    draft_ko="TM 번역" if drafts_echo_tm else "서로 다른 번역",
                    model_id="model-a",
                    tm_hits=[
                        {"id": "t", "en_text": "x", "ko_text": "TM 번역", "score": 0.5}
                    ],
                )
            )
        p = _write_jsonl(tmp_path / "p.jsonl", objs)
        preds, _, _, _, synthesis, _ = _load_pipeline_output(p, HOLD_OUT)
        return run_evaluation(
            HOLD_OUT,
            GLOSSARY,
            preds,
            skip_llm_judge=True,
            field_synthesis=synthesis,
            tm_baseline_median=0.4,
            provenance=_read_run_header(p),
        )

    def test_echo_gate_failure_makes_the_final_verdict_fail(self, tmp_path, hold_out_cards):
        report = self._report(tmp_path, hold_out_cards, drafts_echo_tm=True)

        assert report.echo_gate_result.denominator == 10
        assert report.echo_gate_result.actual_echo_rate == 1.0
        assert report.echo_gate_result.passed is False
        assert report.final_passed is False

    def test_echo_gate_denominator_ignores_the_interrupted_flag(
        self, tmp_path, hold_out_cards
    ):
        """Interrupted records still carry a draft, so they stay in the denominator."""
        objs: list[dict] = [_header("real")]
        for card in hold_out_cards[:4]:
            objs.append(
                _record(
                    card["id"],
                    draft_ko="자체 번역",
                    interrupted=True,
                    tm_hits=[{"id": "t", "en_text": "x", "ko_text": "TM", "score": 0.5}],
                )
            )
        p = _write_jsonl(tmp_path / "p.jsonl", objs)
        preds, _, _, _, synthesis, _ = _load_pipeline_output(p, HOLD_OUT)
        report = run_evaluation(
            HOLD_OUT, GLOSSARY, preds, skip_llm_judge=True,
            field_synthesis=synthesis, tm_baseline_median=0.4,
            provenance=_read_run_header(p),
        )
        assert report.echo_gate_result.denominator == 4

    def test_no_verdict_is_issued_for_a_non_real_run(self, hold_out_cards):
        report = run_evaluation(
            HOLD_OUT,
            GLOSSARY,
            [""] * len(hold_out_cards),
            skip_llm_judge=True,
            tm_baseline_median=0.4,
            run_mode="unknown",
        )
        assert report.gates_are_reportable is False
        assert report.final_passed is None

    def test_headerless_load_leaves_the_run_mode_unknown(self, tmp_path, hold_out_cards):
        """A file with no header at all must not silently read as a real run."""
        p = _write_jsonl(tmp_path / "p.jsonl", [_record(hold_out_cards[0]["id"])])
        assert _read_run_header(p).run_mode == "unknown"
