"""tests/test_run_pipeline.py — AC2 runner tests.

Verifies that run_pipeline.py:
  - Loads the 279-term real glossary asset (not a hand-crafted test dict).
  - Builds a HybridTMIndex from the 880-card training corpus.
  - Generates at least 1 draft record with the correct schema.
  - Draft records carry {card_id, route, draft_ko, tm_hits, injected_terms, tm_confidence}.
  - injected_terms have per-term provenance {en, ko, source, llm_judged}.
  - tm_confidence is a float (0.0 when TM returns no hits, > 0 otherwise).
  - reference_leakage_ban: the hold-out ko_text ground truth is not used as draft_ko.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from run_pipeline import _StubLLM, _state_to_draft_record, run_pipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ASSETS_DIR = Path("assets")
DATA_DIR = Path("data")


@pytest.fixture(scope="module")
def real_pipeline_records(tmp_path_factory) -> list[dict]:
    """Run the full pipeline on 3 hold-out cards and return their draft records.

    Uses the real glossary.json (279 terms) and real train.json (880 records).
    """
    out = tmp_path_factory.mktemp("pipeline") / "output.jsonl"
    records = run_pipeline(
        data_dir=DATA_DIR,
        assets_dir=ASSETS_DIR,
        n_cards=3,
        output_path=out,
    )
    return records


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_pipeline_produces_at_least_one_record(real_pipeline_records):
    """Pipeline must produce at least 1 draft record from real assets."""
    assert len(real_pipeline_records) >= 1


def test_draft_record_has_required_schema_fields(real_pipeline_records):
    """Every draft record must have the draft_record_contract fields."""
    required = {"card_id", "route", "draft_ko", "tm_hits", "injected_terms", "tm_confidence"}
    for rec in real_pipeline_records:
        missing = required - set(rec.keys())
        assert not missing, f"record {rec.get('card_id')} missing fields: {missing}"


def test_draft_record_route_is_rule_or_flavor(real_pipeline_records):
    """route field must be 'rule' or 'flavor'."""
    for rec in real_pipeline_records:
        assert rec["route"] in ("rule", "flavor"), f"unexpected route: {rec['route']}"


def test_draft_record_tm_hits_have_slim_schema(real_pipeline_records):
    """tm_hits must have {id, en_text, score} only (not bm25_rank, char_rank, etc.)."""
    for rec in real_pipeline_records:
        for hit in rec["tm_hits"]:
            assert "id" in hit
            assert "en_text" in hit
            assert "score" in hit
            assert isinstance(hit["score"], float)


def test_draft_record_injected_terms_have_provenance(real_pipeline_records):
    """injected_terms must have {en, ko, source, llm_judged} per term."""
    for rec in real_pipeline_records:
        for term in rec["injected_terms"]:
            assert "en" in term, f"injected_term missing 'en': {term}"
            assert "ko" in term, f"injected_term missing 'ko': {term}"
            assert "source" in term, f"injected_term missing 'source': {term}"
            assert "llm_judged" in term, f"injected_term missing 'llm_judged': {term}"
            assert term["source"] in ("official", "subtype_extracted", "extracted")
            assert isinstance(term["llm_judged"], bool)


def test_draft_record_tm_confidence_is_float(real_pipeline_records):
    """tm_confidence must be a float, not None or string."""
    for rec in real_pipeline_records:
        assert isinstance(rec["tm_confidence"], float), (
            f"tm_confidence is {type(rec['tm_confidence'])}: {rec['tm_confidence']}"
        )


def test_draft_record_tm_confidence_positive_with_880_train_records(real_pipeline_records):
    """With 880 training records, TM should find a non-zero match for most cards."""
    n_positive = sum(1 for rec in real_pipeline_records if rec["tm_confidence"] > 0.0)
    # With 880 cards in the training set, at least 1 out of 3 should get a TM hit.
    assert n_positive >= 1, (
        f"Expected at least 1 positive tm_confidence, got {n_positive} from {len(real_pipeline_records)} records"
    )


def test_reference_leakage_ban(real_pipeline_records):
    """draft_ko must NOT be the hold-out ground-truth ko_text (no reference leakage)."""
    hold_out = json.loads((DATA_DIR / "hold_out.json").read_text(encoding="utf-8"))
    ko_by_id = {c["id"]: c.get("ko_text", "") for c in hold_out}

    for rec in real_pipeline_records:
        card_id = rec["card_id"]
        ground_truth = ko_by_id.get(card_id, "")
        if ground_truth:  # only check cards that have a ground truth
            assert rec["draft_ko"] != ground_truth, (
                f"card {card_id}: draft_ko == ground_truth (reference leakage!)"
            )


def test_real_glossary_has_279_terms_in_pipeline():
    """The pipeline loads the real glossary with the expected flat count."""
    from glossary_guard import load_flat_glossary
    flat, llm_judged = load_flat_glossary(ASSETS_DIR / "glossary.json")
    # Exact count may vary with corpus changes; assert >= 270 to detect serious regression.
    assert len(flat) >= 270, f"expected >= 270 flat terms, got {len(flat)}"
    assert llm_judged is True, "glossary.json must have llm_judged=True for production use"


def test_pipeline_output_written_to_disk(tmp_path):
    """run_pipeline saves output to the specified file path."""
    out = tmp_path / "pipeline_test.jsonl"
    run_pipeline(
        data_dir=DATA_DIR,
        assets_dir=ASSETS_DIR,
        n_cards=1,
        output_path=out,
    )
    assert out.exists(), "pipeline output file must be created"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert "card_id" in rec
    assert "draft_ko" in rec


# ---------------------------------------------------------------------------
# State-to-record conversion unit tests
# ---------------------------------------------------------------------------


def test_state_to_draft_record_slims_tm_hits():
    """_state_to_draft_record keeps only {id, en_text, score} in tm_hits."""
    state = {
        "card_id": "x",
        "text_type": "rule",
        "draft_ko": "번역",
        "tm_hits": [
            {
                "id": "a", "en_text": "Gain 9.", "ko_text": "9 얻.", "score": 0.04,
                "bm25_rank": 0, "char_rank": 1, "dense_rank": None
            }
        ],
        "injected_terms": [],
        "tm_confidence": 0.04,
    }
    rec = _state_to_draft_record(state)
    assert rec["tm_hits"] == [{"id": "a", "en_text": "Gain 9.", "score": 0.04}]
    assert rec["route"] == "rule"
    assert rec["card_id"] == "x"


def test_state_to_draft_record_zero_confidence_when_no_tm_hits():
    """When tm_hits is empty, tm_confidence must be 0.0 in the record."""
    state = {
        "card_id": "y",
        "text_type": "flavor",
        "draft_ko": "플레이버",
        "tm_hits": [],
        "injected_terms": [],
        "tm_confidence": 0.0,
    }
    rec = _state_to_draft_record(state)
    assert rec["tm_confidence"] == 0.0
    assert rec["tm_hits"] == []


# ---------------------------------------------------------------------------
# Stub LLM tests
# ---------------------------------------------------------------------------


def test_stub_llm_returns_tm_hit_ko_when_present():
    """_StubLLM extracts KO text from the first TM hit in the prompt."""
    from injection_guard import safe_wrap
    wrapped_ko = safe_wrap("9 크레딧을 얻는다.")
    prompt = f"Some text\n  [1] EN: {safe_wrap('Gain 9.')}\n       KO: {wrapped_ko}\n## Card to translate"
    llm = _StubLLM()
    response = llm.invoke(prompt)
    assert response.content == "9 크레딧을 얻는다."


def test_stub_llm_fallback_when_no_tm_hit():
    """_StubLLM returns fallback when no TM hit found."""
    prompt = "No TM hits here.\n## Card to translate\n<card-text>\nGain 9.\n</card-text>"
    llm = _StubLLM()
    response = llm.invoke(prompt)
    assert response.content != ""  # must return something
    assert "TM" in response.content or "번역" in response.content  # fallback placeholder
