"""tests/test_ac2_draft_generation.py — AC2 verification tests.

AC2 requires:
  1. TM search + glossary injection → DraftRecord for each (card_id, field) pair.
  2. safe_wrap applied to card text AND TM hit en_text/ko_text.
  3. If llm_judged=False, reveal in (a) prompt body AND (b) DraftRecord.glossary_llm_judged.
  4. Evidence: pipeline produces serialized DraftRecord file; test asserts
     safe_wrap boundary strings exist in prompts.

Removing _build_draft_prompt, generate_draft, or safe_wrap would fail these tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from injection_guard import safe_wrap
from translation_graph import (
    _build_draft_prompt,
    _extract_relevant_terms,
    _make_process_node,
    build_translation_graph,
    expand_card_to_field_inputs,
)

ASSETS_DIR = Path("assets")
DATA_DIR = Path("data")

# Safe-wrap delimiter constants (must match injection_guard._XML_OPEN / _XML_CLOSE)
_OPEN_TAG = "<card-text>"
_CLOSE_TAG = "</card-text>"


# ---------------------------------------------------------------------------
# Unit tests: safe_wrap boundary strings in prompt
# ---------------------------------------------------------------------------


def test_build_draft_prompt_wraps_card_text_with_xml_delimiters():
    """Prompt must contain safe_wrap boundary tags around card text (AC2c)."""
    wrapped = safe_wrap("End the run.")
    prompt = _build_draft_prompt(
        wrapped_text=wrapped,
        tm_hits=[],
        injected_terms=[],
        llm_judged=True,
    )
    assert _OPEN_TAG in prompt, f"Expected {_OPEN_TAG!r} in prompt"
    assert _CLOSE_TAG in prompt, f"Expected {_CLOSE_TAG!r} in prompt"


def test_build_draft_prompt_states_plain_text_output_contract():
    """Prompt must tell the model its answer is not wrapped in <card-text> tags.

    Without this, the model mirrors the tagged reference blocks and emits its
    translation inside <card-text>…</card-text> — measured at 16/17 records on a
    real Bedrock run, which fails every downstream guard.
    """
    wrapped = safe_wrap("End the run.")
    prompt = _build_draft_prompt(
        wrapped_text=wrapped,
        tm_hits=[],
        injected_terms=[],
        llm_judged=True,
    )
    assert "Output only the Korean translation itself as plain text" in prompt
    assert "your answer is not" in prompt


def test_build_draft_prompt_forbids_meta_commentary():
    """Short flavor lines drew English commentary instead of a translation."""
    wrapped = safe_wrap("Fifteen seconds of fame.")
    prompt = _build_draft_prompt(
        wrapped_text=wrapped,
        tm_hits=[],
        injected_terms=[],
        llm_judged=True,
    )
    assert "however short" in prompt
    assert "output the translation only" in prompt


def test_build_draft_prompt_wraps_tm_hit_en_text():
    """TM hit EN text must be safe-wrapped in prompt (AC2c — corpus source not trusted)."""
    wrapped_card = safe_wrap("End the run.")
    tm_hits = [
        {"id": "ice_wall", "en_text": "End the run.", "ko_text": "런을 종료한다.", "score": 0.04}
    ]
    prompt = _build_draft_prompt(
        wrapped_text=wrapped_card,
        tm_hits=tm_hits,
        injected_terms=[],
        llm_judged=True,
    )
    # safe_wrap(hit['en_text']) must appear in TM section
    expected_en_wrapped = safe_wrap("End the run.")
    assert expected_en_wrapped in prompt, (
        f"TM hit en_text must be safe-wrapped in prompt.\nExpected: {expected_en_wrapped!r}\nPrompt: {prompt!r}"
    )


def test_build_draft_prompt_wraps_tm_hit_ko_text():
    """TM hit KO text must be safe-wrapped in prompt (AC2c — corpus source not trusted)."""
    wrapped_card = safe_wrap("End the run.")
    tm_hits = [
        {"id": "ice_wall", "en_text": "End the run.", "ko_text": "런을 종료한다.", "score": 0.04}
    ]
    prompt = _build_draft_prompt(
        wrapped_text=wrapped_card,
        tm_hits=tm_hits,
        injected_terms=[],
        llm_judged=True,
    )
    expected_ko_wrapped = safe_wrap("런을 종료한다.")
    assert expected_ko_wrapped in prompt, (
        f"TM hit ko_text must be safe-wrapped in prompt.\nExpected: {expected_ko_wrapped!r}\nPrompt: {prompt!r}"
    )


def test_build_draft_prompt_multiple_tm_hits_all_wrapped():
    """All TM hits must have their en_text and ko_text safe-wrapped."""
    wrapped_card = safe_wrap("Gain 4 credits.")
    tm_hits = [
        {"id": "a", "en_text": "Gain 4 credits.", "ko_text": "4크레딧을 얻는다.", "score": 0.05},
        {"id": "b", "en_text": "Gain 2 credits.", "ko_text": "2크레딧을 얻는다.", "score": 0.03},
    ]
    prompt = _build_draft_prompt(
        wrapped_text=wrapped_card,
        tm_hits=tm_hits,
        injected_terms=[],
        llm_judged=True,
    )
    for hit in tm_hits:
        assert safe_wrap(hit["en_text"]) in prompt, f"en_text for {hit['id']} not wrapped"
        assert safe_wrap(hit["ko_text"]) in prompt, f"ko_text for {hit['id']} not wrapped"


# ---------------------------------------------------------------------------
# Unit tests: llm_judged=False disclosure in prompt
# ---------------------------------------------------------------------------


def test_build_draft_prompt_shows_unjudged_warning_when_llm_judged_false():
    """When llm_judged=False, prompt must contain a warning about unvalidated terms."""
    non_official_term = {"en": "install", "ko": "설치", "source": "extracted", "llm_judged": False}
    prompt = _build_draft_prompt(
        wrapped_text=safe_wrap("Install a program."),
        tm_hits=[],
        injected_terms=[non_official_term],
        llm_judged=False,
    )
    # The prompt must explicitly signal unvalidated rule terms
    assert "not yet LLM-validated" in prompt or "not yet validated" in prompt, (
        f"Prompt must disclose unjudged glossary status when llm_judged=False.\nPrompt: {prompt!r}"
    )


def test_build_draft_prompt_no_unjudged_warning_when_llm_judged_true():
    """When llm_judged=True, prompt must NOT show the unjudged validation warning."""
    non_official_term = {"en": "install", "ko": "설치", "source": "extracted", "llm_judged": True}
    prompt = _build_draft_prompt(
        wrapped_text=safe_wrap("Install a program."),
        tm_hits=[],
        injected_terms=[non_official_term],
        llm_judged=True,
    )
    # "(rule terms — not yet LLM-validated)" should not appear for the section header
    assert "(rule terms — not yet LLM-validated)" not in prompt, (
        "Prompt must NOT show unjudged warning when llm_judged=True"
    )


# ---------------------------------------------------------------------------
# Unit tests: glossary_llm_judged in CardState from process node
# ---------------------------------------------------------------------------


def test_process_node_sets_glossary_llm_judged_false():
    """When llm_judged=False, process node must set glossary_llm_judged=False in state."""
    from unittest.mock import MagicMock

    mock_tm = MagicMock()
    mock_tm.search.return_value = []
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="번역 결과")

    node = _make_process_node(
        "en_rule",
        tm_index=mock_tm,
        flat_glossary={},
        llm_judged=False,
        llm=mock_llm,
    )
    state = {"card_id": "test", "en_rule": "End the run.", "en_flavor": ""}
    result = node(state)

    assert "glossary_llm_judged" in result, "glossary_llm_judged must be in state after process node"
    assert result["glossary_llm_judged"] is False, (
        f"glossary_llm_judged must be False when llm_judged=False, got {result['glossary_llm_judged']}"
    )


def test_process_node_sets_glossary_llm_judged_true():
    """When llm_judged=True, process node must set glossary_llm_judged=True in state."""
    from unittest.mock import MagicMock

    mock_tm = MagicMock()
    mock_tm.search.return_value = []
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="번역 결과")

    node = _make_process_node(
        "en_rule",
        tm_index=mock_tm,
        flat_glossary={},
        llm_judged=True,
        llm=mock_llm,
    )
    state = {"card_id": "test", "en_rule": "End the run.", "en_flavor": ""}
    result = node(state)

    assert result.get("glossary_llm_judged") is True, (
        f"glossary_llm_judged must be True when llm_judged=True, got {result.get('glossary_llm_judged')}"
    )


# ---------------------------------------------------------------------------
# Unit tests: _state_to_draft_record includes glossary_llm_judged
# ---------------------------------------------------------------------------


def test_state_to_draft_record_includes_glossary_llm_judged():
    """_state_to_draft_record must include glossary_llm_judged in the output."""
    from run_pipeline import _state_to_draft_record

    state = {
        "card_id": "test",
        "text_type": "rule",
        "draft_ko": "번역",
        "tm_hits": [],
        "injected_terms": [],
        "tm_confidence": 0.0,
        "glossary_llm_judged": False,
    }
    rec = _state_to_draft_record(state)
    assert "glossary_llm_judged" in rec, "DraftRecord must include glossary_llm_judged"
    assert rec["glossary_llm_judged"] is False


def test_state_to_draft_record_glossary_llm_judged_true():
    """_state_to_draft_record preserves glossary_llm_judged=True from state."""
    from run_pipeline import _state_to_draft_record

    state = {
        "card_id": "test",
        "text_type": "flavor",
        "draft_ko": "플레이버",
        "tm_hits": [],
        "injected_terms": [],
        "tm_confidence": 0.0,
        "glossary_llm_judged": True,
    }
    rec = _state_to_draft_record(state)
    assert rec["glossary_llm_judged"] is True


def test_state_to_draft_record_glossary_llm_judged_defaults_false():
    """_state_to_draft_record defaults glossary_llm_judged to False when not in state."""
    from run_pipeline import _state_to_draft_record

    state = {
        "card_id": "test",
        "text_type": "rule",
        "draft_ko": "번역",
        "tm_hits": [],
        "injected_terms": [],
        "tm_confidence": 0.0,
        # glossary_llm_judged intentionally absent
    }
    rec = _state_to_draft_record(state)
    assert "glossary_llm_judged" in rec
    assert rec["glossary_llm_judged"] is False


# ---------------------------------------------------------------------------
# Integration test: pipeline produces DraftRecord with glossary_llm_judged
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pipeline_records_with_glossary_flag(tmp_path_factory):
    """Run pipeline on 2 hold-out cards; return records that include glossary_llm_judged."""
    from run_pipeline import run_pipeline

    out = tmp_path_factory.mktemp("ac2") / "ac2_output.jsonl"
    return run_pipeline(
        data_dir=DATA_DIR,
        assets_dir=ASSETS_DIR,
        n_cards=2,
        output_path=out,
        llm_model=None,
    )


def test_pipeline_records_have_glossary_llm_judged(pipeline_records_with_glossary_flag):
    """All pipeline DraftRecords must include glossary_llm_judged field."""
    records = pipeline_records_with_glossary_flag
    assert records, "Pipeline must produce at least 1 record"
    for rec in records:
        assert "glossary_llm_judged" in rec, (
            f"DraftRecord for {rec.get('card_id')} missing glossary_llm_judged"
        )
        assert isinstance(rec["glossary_llm_judged"], bool), (
            f"glossary_llm_judged must be bool, got {type(rec['glossary_llm_judged'])}"
        )


def test_pipeline_records_glossary_llm_judged_matches_real_glossary(pipeline_records_with_glossary_flag):
    """glossary_llm_judged in records must match the real glossary asset llm_judged flag."""
    from glossary_guard import load_flat_glossary

    _, expected_llm_judged = load_flat_glossary(ASSETS_DIR / "glossary.json")
    records = pipeline_records_with_glossary_flag
    for rec in records:
        # Only rule-routed records use extracted glossary terms that carry llm_judged
        if rec.get("injected_terms"):
            assert rec["glossary_llm_judged"] == expected_llm_judged, (
                f"card {rec['card_id']}: expected glossary_llm_judged={expected_llm_judged}, "
                f"got {rec['glossary_llm_judged']}"
            )


def test_pipeline_output_file_has_glossary_llm_judged(tmp_path):
    """Serialized pipeline output file must contain glossary_llm_judged in each record."""
    import json
    from run_pipeline import run_pipeline

    out = tmp_path / "ac2_evidence.jsonl"
    run_pipeline(
        data_dir=DATA_DIR,
        assets_dir=ASSETS_DIR,
        n_cards=1,
        output_path=out,
        llm_model=None,
    )
    assert out.exists(), "Output file must be created"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines, "Output file must be non-empty"

    for line in lines:
        rec = json.loads(line)
        if "_meta" in rec:
            continue  # skip AC4 metadata header line
        assert "glossary_llm_judged" in rec, (
            f"Serialized DraftRecord missing glossary_llm_judged: {line[:120]}"
        )


def test_state_to_draft_record_tm_hits_include_ko_text():
    """tm_hits in serialized DraftRecord must include ko_text (ontology: id·en_text·ko_text·score).

    Removing ko_text from slim_hits breaks the echo gate — it cannot compare
    draft_ko to tm_hits[0].ko_text from pipeline_output.jsonl alone.
    """
    from run_pipeline import _state_to_draft_record

    state = {
        "card_id": "test",
        "text_type": "rule",
        "draft_ko": "번역",
        "tm_hits": [
            {"id": "ice_wall", "en_text": "End the run.", "ko_text": "런을 종료한다.", "score": 0.04,
             "bm25_rank": 0, "char_rank": 1, "dense_rank": None},
        ],
        "injected_terms": [],
        "tm_confidence": 0.04,
        "glossary_llm_judged": True,
    }
    rec = _state_to_draft_record(state)
    assert rec["tm_hits"], "tm_hits must be non-empty"
    hit = rec["tm_hits"][0]
    assert "ko_text" in hit, f"ko_text missing from serialized tm_hits[0]: {hit}"
    assert hit["ko_text"] == "런을 종료한다.", f"ko_text value wrong: {hit['ko_text']!r}"
    # Internal rank fields must be stripped from the output
    assert "bm25_rank" not in hit
    assert "char_rank" not in hit
    assert "dense_rank" not in hit


def test_pipeline_output_file_tm_hits_include_ko_text(tmp_path):
    """Serialized pipeline output must have ko_text in each tm_hits entry (Seed: echo gate)."""
    import json
    from run_pipeline import run_pipeline

    out = tmp_path / "ac2_ko_text_check.jsonl"
    run_pipeline(
        data_dir=DATA_DIR,
        assets_dir=ASSETS_DIR,
        n_cards=1,
        output_path=out,
        llm_model=None,
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    records_checked = 0
    for line in lines:
        rec = json.loads(line)
        if "_meta" in rec:
            continue
        for hit in rec.get("tm_hits", []):
            assert "ko_text" in hit, (
                f"tm_hits entry missing ko_text in card {rec.get('card_id')}: {hit}"
            )
        records_checked += 1
    assert records_checked > 0, "No DraftRecords found in output file"
