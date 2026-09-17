"""test_new_term_candidate_store.py — Tests for AC 6: new term candidate non-auto-promotion.

승인된 번역에 용어집에 없던 신규 용어가 포함된 경우 new_term_candidates.json으로만 적재되고,
3단계 확정 절차를 거쳐야 용어집에 등재된다. 승인만으로 다음 카드부터 강제되지 않는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from approved_store import (  # noqa: E402
    NewTermCandidateRecord,
    append_new_term_candidate,
    load_new_term_candidates,
)
from new_term_guard import check_new_terms  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(**kwargs) -> NewTermCandidateRecord:
    defaults = dict(
        en_term="rez",
        source_card_id="ichi_1_0",
        approved_ko_context="이 카드를 레즈한다.",
    )
    defaults.update(kwargs)
    return NewTermCandidateRecord(**defaults)


def _minimal_glossary() -> dict:
    """A flat glossary that does NOT contain 'rez' — so it's a new term."""
    return {"install": ("설치", "extracted"), "trash": ("폐기", "extracted")}


# ===========================================================================
# NewTermCandidateRecord model
# ===========================================================================


class TestNewTermCandidateRecord:
    def test_basic_fields_stored(self):
        rec = _make_candidate()
        assert rec.en_term == "rez"
        assert rec.source_card_id == "ichi_1_0"
        assert "레즈" in rec.approved_ko_context

    def test_submitted_at_is_iso_string(self):
        from datetime import datetime
        rec = _make_candidate()
        dt = datetime.fromisoformat(rec.submitted_at)
        assert dt.tzinfo is not None

    def test_is_pydantic_model(self):
        from pydantic import BaseModel
        assert isinstance(_make_candidate(), BaseModel)


# ===========================================================================
# append_new_term_candidate — writes to new_term_candidates.json
# ===========================================================================


class TestAppendNewTermCandidate:
    def test_creates_file_on_first_append(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        assert not store.exists()
        append_new_term_candidate(_make_candidate(), store)
        assert store.exists()

    def test_file_is_valid_json_list(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        append_new_term_candidate(_make_candidate(), store)
        data = json.loads(store.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1

    def test_appends_multiple_candidates(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        for term in ["rez", "derez", "purge"]:
            append_new_term_candidate(_make_candidate(en_term=term), store)
        data = json.loads(store.read_text(encoding="utf-8"))
        assert len(data) == 3
        assert {item["en_term"] for item in data} == {"rez", "derez", "purge"}

    def test_en_term_persisted(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        append_new_term_candidate(_make_candidate(en_term="rez"), store)
        data = json.loads(store.read_text(encoding="utf-8"))
        assert data[0]["en_term"] == "rez"

    def test_source_card_id_persisted(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        append_new_term_candidate(_make_candidate(source_card_id="ichi_1_0"), store)
        data = json.loads(store.read_text(encoding="utf-8"))
        assert data[0]["source_card_id"] == "ichi_1_0"

    def test_returns_same_record(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        rec = _make_candidate()
        result = append_new_term_candidate(rec, store)
        assert result is rec

    def test_creates_parent_directories(self, tmp_path):
        store = tmp_path / "subdir" / "new_term_candidates.json"
        append_new_term_candidate(_make_candidate(), store)
        assert store.exists()

    def test_unicode_preserved(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        ko_ctx = "이 카드를 레즈하고 크레딧 1을 얻는다."
        append_new_term_candidate(_make_candidate(approved_ko_context=ko_ctx), store)
        data = json.loads(store.read_text(encoding="utf-8"))
        assert data[0]["approved_ko_context"] == ko_ctx


# ===========================================================================
# load_new_term_candidates — round-trip
# ===========================================================================


class TestLoadNewTermCandidates:
    def test_returns_empty_when_file_missing(self, tmp_path):
        result = load_new_term_candidates(tmp_path / "nonexistent.json")
        assert result == []

    def test_loads_written_records(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        append_new_term_candidate(_make_candidate(en_term="rez"), store)
        loaded = load_new_term_candidates(store)
        assert len(loaded) == 1
        assert loaded[0].en_term == "rez"

    def test_loaded_are_record_instances(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        append_new_term_candidate(_make_candidate(), store)
        loaded = load_new_term_candidates(store)
        assert isinstance(loaded[0], NewTermCandidateRecord)

    def test_round_trip_preserves_all_fields(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        rec = _make_candidate(en_term="derez", source_card_id="card_x", approved_ko_context="디레즈한다.")
        append_new_term_candidate(rec, store)
        loaded = load_new_term_candidates(store)
        assert loaded[0].en_term == "derez"
        assert loaded[0].source_card_id == "card_x"
        assert loaded[0].approved_ko_context == "디레즈한다."

    def test_multiple_records_order_preserved(self, tmp_path):
        store = tmp_path / "new_term_candidates.json"
        terms = ["rez", "derez", "purge"]
        for t in terms:
            append_new_term_candidate(_make_candidate(en_term=t), store)
        loaded = load_new_term_candidates(store)
        assert [r.en_term for r in loaded] == terms


# ===========================================================================
# Critical: glossary is NOT modified — no auto-promotion
# ===========================================================================


class TestGlossaryNotModified:
    def test_append_does_not_write_glossary_json(self, tmp_path):
        """Storing a new term candidate must NOT create or modify glossary.json."""
        glossary_path = tmp_path / "glossary.json"
        assert not glossary_path.exists()

        store = tmp_path / "new_term_candidates.json"
        append_new_term_candidate(_make_candidate(en_term="rez"), store)

        assert not glossary_path.exists(), (
            "append_new_term_candidate must not create or modify glossary.json"
        )

    def test_existing_glossary_unchanged_after_append(self, tmp_path):
        """An existing glossary.json must remain bit-for-bit identical after append."""
        glossary_path = tmp_path / "glossary.json"
        original_content = json.dumps({"install": ["설치", "extracted"]}, ensure_ascii=False)
        glossary_path.write_text(original_content, encoding="utf-8")

        store = tmp_path / "new_term_candidates.json"
        append_new_term_candidate(_make_candidate(en_term="rez"), store)

        assert glossary_path.read_text(encoding="utf-8") == original_content


# ===========================================================================
# Critical: new term is NOT enforced for the next card — still triggers HITL
# ===========================================================================


class TestNoAutoEnforcement:
    def test_term_still_flagged_after_candidate_stored(self, tmp_path):
        """After a new term candidate is stored, check_new_terms() must still flag
        that term for subsequent cards (no auto-promotion to glossary)."""
        glossary = _minimal_glossary()  # does not contain "rez"

        # Store "rez" as a new term candidate (simulating post-approval storage)
        store = tmp_path / "new_term_candidates.json"
        append_new_term_candidate(
            _make_candidate(en_term="rez", approved_ko_context="레즈한다."),
            store,
        )

        # Next card with "rez" in source text
        next_card_text = "Rez this card."
        result = check_new_terms(next_card_text, glossary, llm_judged=True)

        # Must still flag "rez" — not auto-promoted
        assert not result.passed, (
            "check_new_terms() must still flag 'rez' after it is stored as a candidate; "
            "only phase-3 confirmation should add it to the glossary"
        )
        flagged = {t.en_term for t in result.new_terms}
        assert "rez" in flagged

    def test_loading_candidates_does_not_suppress_hitl(self, tmp_path):
        """Loading new_term_candidates.json must not affect check_new_terms() output."""
        glossary = _minimal_glossary()
        store = tmp_path / "new_term_candidates.json"

        # Store two candidates
        for term in ["rez", "derez"]:
            append_new_term_candidate(_make_candidate(en_term=term), store)

        loaded = load_new_term_candidates(store)
        assert len(loaded) == 2

        # The glossary is still unchanged — HITL still fires
        result = check_new_terms("Rez this card.", glossary, llm_judged=True)
        assert not result.passed

    def test_separate_from_approved_store(self, tmp_path):
        """new_term_candidates.json and approved.jsonl are independent files."""
        from approved_store import ApprovedRecord, append_approved

        cand_store = tmp_path / "new_term_candidates.json"
        appr_store = tmp_path / "approved.jsonl"

        append_new_term_candidate(_make_candidate(), cand_store)
        append_approved(
            ApprovedRecord(
                card_id="ichi_1_0",
                en_text="Rez this.",
                ko_draft="레즈한다.",
                approved_ko="레즈한다.",
                modified=False,
                interrupt_reasons=["new_term"],
            ),
            appr_store,
        )

        assert cand_store.exists()
        assert appr_store.exists()
        # They are separate files
        assert cand_store != appr_store
        # Candidate store is JSON (list), approved store is JSONL
        data = json.loads(cand_store.read_text(encoding="utf-8"))
        assert isinstance(data, list)
