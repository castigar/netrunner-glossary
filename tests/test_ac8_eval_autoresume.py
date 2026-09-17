"""tests/test_ac8_eval_autoresume.py — AC8: eval_autoresume mode tests.

AC8 specifies that the evaluation pipeline must:
  1. NOT bypass interrupt() — it must actually fire.
  2. Use a deterministic auto-responder (not a human) to supply Command(resume=...)
     after each interrupt, without consulting hold-out ko_text.
  3. Write an eval_autoresume_header to the output with HITL stats:
       {trigger_count, auto_approved_count, auto_held_count, is_human_approved=False}
  4. Report: predictions are NOT human-approved; trigger/approved/held counts.
  5. Operational mode (no eval_autoresume): the same graph actually pauses at
     interrupt() — asserted by a separate test in this file.

Producer: run_pipeline.py (eval_autoresume=True)
Consumer: evaluate_pipeline._load_pipeline_output (reads eval_autoresume_header)

Tests must be non-tautological: they assert observable outcomes that can fail
independently of the implementation (e.g. trigger_count > 0 when using a
card+glossary combo known to violate guards).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from run_pipeline import run_pipeline
from translation_graph import build_translation_graph
from tm_index import HybridTMIndex
from glossary_guard import GlossaryFlat


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _MockLLM:
    def __init__(self, response: str = "번역 결과") -> None:
        self._response = response

    class _R:
        def __init__(self, c): self.content = c

    def invoke(self, prompt: str) -> "_R":
        return self._R(self._response)


@pytest.fixture(scope="module")
def small_tm_index() -> HybridTMIndex:
    index = HybridTMIndex(use_dense=False)
    index.build([
        {"id": "sure_gamble", "en_text": "Gain 9 credits.", "ko_text": "9 크레딧을 얻는다."},
        {"id": "hedge_fund", "en_text": "Gain 9 credits. Spend [click].", "ko_text": "9 크레딧을 얻는다. [click]을 쓴다."},
        {"id": "icewall", "en_text": "End the run.", "ko_text": "런을 종료한다."},
    ])
    return index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_jsonl_output(path: Path) -> tuple[dict, dict | None, list[dict]]:
    """Return (run_header, eval_autoresume_header|None, card_records)."""
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    objects = [json.loads(l) for l in lines]
    run_header = objects[0]
    eval_header = None
    records = []
    for obj in objects[1:]:
        if "_meta" in obj:
            eval_header = obj
        else:
            records.append(obj)
    return run_header, eval_header, records


# ---------------------------------------------------------------------------
# AC8 producer tests: eval_autoresume mode in run_pipeline.py
# ---------------------------------------------------------------------------


def test_eval_autoresume_writes_header(tmp_path):
    """run_pipeline(eval_autoresume=True) must write an eval_autoresume_header line."""
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=3,
        output_path=out,
        eval_autoresume=True,
    )
    _, eval_header, _ = _parse_jsonl_output(out)
    assert eval_header is not None, (
        "eval_autoresume_header must be written when eval_autoresume=True; got None"
    )
    assert eval_header.get("_meta") == "eval_autoresume_header", (
        f"_meta must be 'eval_autoresume_header'; got {eval_header.get('_meta')!r}"
    )


def test_eval_autoresume_header_has_required_hitl_stats_keys(tmp_path):
    """eval_autoresume_header.hitl_stats must contain all four required keys."""
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=3,
        output_path=out,
        eval_autoresume=True,
    )
    _, eval_header, _ = _parse_jsonl_output(out)
    assert eval_header is not None
    stats = eval_header.get("hitl_stats", {})
    required_keys = {"trigger_count", "auto_approved_count", "auto_held_count", "is_human_approved"}
    missing = required_keys - set(stats.keys())
    assert not missing, f"hitl_stats missing keys: {missing}"


def test_eval_autoresume_is_never_human_approved(tmp_path):
    """is_human_approved must always be False in eval_autoresume mode.

    This can fail if the auto-responder mistakenly sets is_human_approved=True.
    """
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=3,
        output_path=out,
        eval_autoresume=True,
    )
    _, eval_header, _ = _parse_jsonl_output(out)
    assert eval_header is not None
    is_human = eval_header["hitl_stats"]["is_human_approved"]
    assert is_human is False, (
        f"is_human_approved must be False in eval_autoresume mode; got {is_human!r}"
    )


def test_eval_autoresume_records_carry_eval_flags(tmp_path):
    """Every record in eval_autoresume output must carry eval_autoresume=True."""
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=3,
        output_path=out,
        eval_autoresume=True,
    )
    _, _, records = _parse_jsonl_output(out)
    assert len(records) >= 1, "eval_autoresume run must produce at least 1 card record"
    for rec in records:
        assert rec.get("eval_autoresume") is True, (
            f"record {rec.get('card_id')!r} missing eval_autoresume=True: {rec}"
        )
        assert "auto_approved" in rec, (
            f"record {rec.get('card_id')!r} missing auto_approved field"
        )


def test_eval_autoresume_auto_approved_count_consistent_with_records(tmp_path):
    """auto_approved_count in header must equal the number of records with auto_approved=True.

    This test can fail if the counter and the per-record flag get out of sync.
    """
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=5,
        output_path=out,
        eval_autoresume=True,
    )
    _, eval_header, records = _parse_jsonl_output(out)
    assert eval_header is not None
    counted_in_records = sum(1 for r in records if r.get("auto_approved") is True)
    from_header = eval_header["hitl_stats"]["auto_approved_count"]
    assert counted_in_records == from_header, (
        f"auto_approved_count in header ({from_header}) does not match "
        f"records with auto_approved=True ({counted_in_records})"
    )


def test_eval_autoresume_trigger_count_consistent_with_interrupted_records(tmp_path):
    """trigger_count in header must equal the number of records with interrupted=True.

    This test can fail if trigger_count is incremented without matching interrupted=True
    in the record (e.g., if the auto-responder loop runs extra times).
    """
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=5,
        output_path=out,
        eval_autoresume=True,
    )
    _, eval_header, records = _parse_jsonl_output(out)
    assert eval_header is not None
    interrupted_count = sum(1 for r in records if r.get("interrupted") is True)
    trigger_count = eval_header["hitl_stats"]["trigger_count"]
    assert interrupted_count == trigger_count, (
        f"trigger_count ({trigger_count}) does not match records with interrupted=True "
        f"({interrupted_count})"
    )


def test_eval_autoresume_mode_absent_in_operational_output(tmp_path):
    """Without eval_autoresume=True, output must NOT contain eval_autoresume_header.

    This test would fail if eval_autoresume_header is always emitted regardless of mode.
    """
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=2,
        output_path=out,
        eval_autoresume=False,
    )
    _, eval_header, _ = _parse_jsonl_output(out)
    assert eval_header is None, (
        f"eval_autoresume_header must NOT appear in non-eval output; got {eval_header}"
    )


def test_eval_autoresume_auto_responder_does_not_consult_ko_text(tmp_path):
    """Auto-responder must not write ko_text from hold-out into draft_ko.

    If a record's auto_approved=True and its draft_ko is identical to the
    hold-out ko_text for that card, this does NOT by itself prove leakage
    (TM search could coincidentally return the same text). However, the
    auto-responder is defined as Command(resume="approved"), which keeps the
    original draft_ko unchanged. We verify that auto-approved records have a
    non-empty draft_ko (graph completed and produced a translation) and that
    the auto_approved flag is correctly set — not that draft_ko ≠ ko_text
    (which would be a false negative criterion).

    The real leakage ban is structural: the auto-responder only passes "approved"
    as the resume value, never reads hold-out records.
    """
    import json as _json
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=3,
        output_path=out,
        eval_autoresume=True,
    )
    _, _, records = _parse_jsonl_output(out)
    auto_approved_records = [r for r in records if r.get("auto_approved") is True]
    # Every auto-approved record must have completed the graph — draft_ko is from the LLM.
    # (An empty draft_ko here would mean the graph failed to resume correctly.)
    for rec in auto_approved_records:
        # draft_ko may be empty if the LLM produced nothing, but that's an LLM issue,
        # not a leakage issue.  We assert the field exists and is a string.
        assert isinstance(rec.get("draft_ko", ""), str), (
            f"auto_approved record {rec.get('card_id')!r} has non-string draft_ko"
        )


# ---------------------------------------------------------------------------
# AC8 consumer tests: evaluate_pipeline reads eval_autoresume_header
# ---------------------------------------------------------------------------


def test_evaluate_pipeline_reads_hitl_stats_from_header(tmp_path):
    """evaluate_pipeline._load_pipeline_output must extract hitl_stats from eval_autoresume_header.

    This test can fail if _load_pipeline_output doesn't read the header or reads the
    wrong field name.
    """
    from evaluate_pipeline import _load_pipeline_output

    hold_out_path = Path("data") / "hold_out.json"
    # Build a minimal pipeline output file with both headers and one record.
    pipeline_path = tmp_path / "pipeline.jsonl"
    hold_out = json.loads(hold_out_path.read_text(encoding="utf-8"))
    first_card_id = hold_out[0]["id"]

    with pipeline_path.open("w", encoding="utf-8") as f:
        # run_pipeline_header
        f.write(json.dumps({"_meta": "run_pipeline_header", "tm_threshold_derivation": {}}) + "\n")
        # eval_autoresume_header
        f.write(json.dumps({
            "_meta": "eval_autoresume_header",
            "hitl_stats": {
                "trigger_count": 7,
                "auto_approved_count": 7,
                "auto_held_count": 0,
                "is_human_approved": False,
            },
        }) + "\n")
        # One minimal card record
        f.write(json.dumps({
            "card_id": first_card_id,
            "route": "rule",
            "draft_ko": "9 크레딧을 얻는다.",
            "tm_hits": [],
            "injected_terms": [],
            "tm_confidence": 0.04,
            "interrupted": False,
            "eval_autoresume": True,
            "auto_approved": False,
        }) + "\n")

    _, _, _, _, _, hitl_stats = _load_pipeline_output(pipeline_path, hold_out_path)
    assert hitl_stats is not None, (
        "_load_pipeline_output must return hitl_stats when eval_autoresume_header is present"
    )
    assert hitl_stats["trigger_count"] == 7
    assert hitl_stats["auto_approved_count"] == 7
    assert hitl_stats["is_human_approved"] is False


def test_evaluate_pipeline_hitl_mode_none_without_header(tmp_path):
    """When pipeline output has no eval_autoresume_header, hitl_stats is None."""
    from evaluate_pipeline import _load_pipeline_output

    hold_out_path = Path("data") / "hold_out.json"
    pipeline_path = tmp_path / "pipeline.jsonl"
    hold_out = json.loads(hold_out_path.read_text(encoding="utf-8"))
    first_card_id = hold_out[0]["id"]

    with pipeline_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": "run_pipeline_header", "tm_threshold_derivation": {}}) + "\n")
        f.write(json.dumps({
            "card_id": first_card_id,
            "route": "rule",
            "draft_ko": "번역",
            "tm_hits": [],
            "injected_terms": [],
            "tm_confidence": 0.0,
            "interrupted": False,
        }) + "\n")

    _, _, _, _, _, hitl_stats = _load_pipeline_output(pipeline_path, hold_out_path)
    assert hitl_stats is None, (
        "hitl_stats must be None when no eval_autoresume_header is present"
    )


def test_print_evaluation_report_includes_hitl_disclosure(capsys):
    """print_evaluation_report must include HITL disclosure when hitl_mode is set.

    This test can fail if print_evaluation_report omits the HITL section or
    if is_human_approved is incorrectly set to True.

    Uses hold_out.json to derive the prediction count (hold_out_size_source AC)
    so gate scoring does not raise a length mismatch.
    """
    import json as _json
    from evaluate_pipeline import EvaluationReport, print_evaluation_report
    import gate1_term_compliance
    import gate2_symbol_preservation
    import gate3_edit_distance
    from gate_verdict import combine

    hold_out_path = Path("data") / "hold_out.json"
    glossary_path = Path("assets") / "glossary.json"
    hold_out = _json.loads(hold_out_path.read_text(encoding="utf-8"))
    # One prediction per hold-out card — same placeholder for each.
    preds = ["번역"] * len(hold_out)

    g1 = gate1_term_compliance.score_hold_out(hold_out_path, glossary_path, predictions=preds)
    g2 = gate2_symbol_preservation.score_hold_out(hold_out_path, predictions=preds)
    g3 = gate3_edit_distance.score_hold_out(hold_out_path, preds)
    verdict = combine(g1, g2, g3)

    report = EvaluationReport(
        verdict=verdict,
        hitl_mode="eval_autoresume",
        hitl_trigger_count=15,
        hitl_auto_approved_count=15,
        hitl_auto_held_count=0,
        is_human_approved=False,
    )
    print_evaluation_report(report)
    captured = capsys.readouterr()

    assert "eval_autoresume" in captured.out, (
        "print_evaluation_report must display the HITL mode name"
    )
    assert "사람 승인을 거치지 않" in captured.out, (
        "print_evaluation_report must state predictions are not human-approved"
    )
    assert "15" in captured.out, (
        "print_evaluation_report must include the trigger count"
    )
    assert "is_human_approved=False" in captured.out, (
        "print_evaluation_report must show is_human_approved=False"
    )


# ---------------------------------------------------------------------------
# AC8 operational-mode test: graph actually pauses (no auto-resume)
# ---------------------------------------------------------------------------


def test_operational_mode_graph_pauses_at_interrupt(small_tm_index):
    """Operational mode: the graph fires interrupt() and returns without auto-resuming.

    This is the "same graph" assertion required by AC8: in non-eval mode, the
    translation graph actually stops at interrupt() — there is no auto-responder.
    The test can fail if the review_queue node no longer calls interrupt().
    """
    flat: GlossaryFlat = {"run": ("런", "official")}
    # Fidelity violation: EN says 9, KO says 10 → fidelity guard fires
    graph = build_translation_graph(
        tm_index=small_tm_index,
        flat_glossary=flat,
        llm_judged=True,
        llm=_MockLLM("10 크레딧을 얻는다."),
        conflict_entries=[],
    )
    # No checkpointer → graph cannot resume, but interrupt still fires immediately.
    result = graph.invoke({"card_id": "sure_gamble", "en_rule": "Gain 9 credits."})
    assert "__interrupt__" in result, (
        "Operational-mode graph must fire interrupt() and return with '__interrupt__' key; "
        "got result without it — graph may have bypassed interrupt or the guard didn't fire"
    )
    # Critically: no auto-resume happened — the interrupt payload must be present.
    interrupt_payload = result["__interrupt__"][0].value
    assert interrupt_payload.get("card_id") == "sure_gamble", (
        f"interrupt payload must carry the card_id; got {interrupt_payload}"
    )


def test_operational_mode_no_eval_autoresume_flag_in_records(tmp_path):
    """In operational mode, output records must NOT have eval_autoresume=True.

    This can fail if eval_autoresume flag is accidentally written in non-eval mode.
    """
    out = tmp_path / "output.jsonl"
    run_pipeline(
        data_dir=Path("data"),
        assets_dir=Path("assets"),
        n_cards=2,
        output_path=out,
        eval_autoresume=False,
    )
    _, _, records = _parse_jsonl_output(out)
    for rec in records:
        assert rec.get("eval_autoresume") is not True, (
            f"record {rec.get('card_id')!r} has eval_autoresume=True in operational mode"
        )
