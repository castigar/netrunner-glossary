"""test_approved_store.py — Tests for the approved.jsonl append-only store (AC 5).

승인 또는 수정 후 승인된 결과는 원본 git repo에 쓰지 않고
approved.jsonl로만 적재한다.  repo 반영·커밋은 사람이 별도로 수행한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from approved_store import ApprovedRecord, append_approved, load_approved  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**kwargs) -> ApprovedRecord:
    defaults = dict(
        card_id="15_minutes",
        en_text="Gain 2[credit].",
        ko_draft="크레딧 2를 얻는다.",
        approved_ko="크레딧 2를 얻는다.",
        modified=False,
    )
    defaults.update(kwargs)
    return ApprovedRecord(**defaults)


# ===========================================================================
# ApprovedRecord model
# ===========================================================================


class TestApprovedRecord:
    def test_basic_fields_stored(self):
        rec = _make_record()
        assert rec.card_id == "15_minutes"
        assert rec.en_text == "Gain 2[credit]."
        assert rec.ko_draft == "크레딧 2를 얻는다."
        assert rec.approved_ko == "크레딧 2를 얻는다."
        assert not rec.modified

    def test_approved_at_is_iso_string(self):
        rec = _make_record()
        # Must parse without error and contain a timezone offset
        from datetime import datetime
        dt = datetime.fromisoformat(rec.approved_at)
        assert dt.tzinfo is not None

    def test_modified_flag_true(self):
        rec = _make_record(
            ko_draft="프로그램을 설치한다.",
            approved_ko="프로그램을 설치하라.",
            modified=True,
        )
        assert rec.modified

    def test_interrupt_reasons_defaults_empty(self):
        rec = _make_record()
        assert rec.interrupt_reasons == []

    def test_interrupt_reasons_stored(self):
        rec = _make_record(interrupt_reasons=["new_term", "low_tm_confidence"])
        assert "new_term" in rec.interrupt_reasons
        assert "low_tm_confidence" in rec.interrupt_reasons

    def test_extra_defaults_empty(self):
        rec = _make_record()
        assert rec.extra == {}

    def test_extra_stored(self):
        rec = _make_record(extra={"card_type": "event", "faction": "shaper"})
        assert rec.extra["card_type"] == "event"

    def test_is_pydantic_model(self):
        from pydantic import BaseModel
        assert isinstance(_make_record(), BaseModel)


# ===========================================================================
# append_approved — writes to file, never to git
# ===========================================================================


class TestAppendApproved:
    def test_creates_file_on_first_append(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        assert not store.exists()
        append_approved(_make_record(), store)
        assert store.exists()

    def test_appends_one_record(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(), store)
        lines = [l for l in store.read_text("utf-8").splitlines() if l.strip()]
        assert len(lines) == 1

    def test_appends_multiple_records(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        for i in range(3):
            append_approved(_make_record(card_id=f"card_{i}"), store)
        lines = [l for l in store.read_text("utf-8").splitlines() if l.strip()]
        assert len(lines) == 3

    def test_each_line_is_valid_json(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        for i in range(3):
            append_approved(_make_record(card_id=f"card_{i}"), store)
        for line in store.read_text("utf-8").splitlines():
            if line.strip():
                json.loads(line)  # must not raise

    def test_card_id_persisted_in_json(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(card_id="15_minutes"), store)
        data = json.loads(store.read_text("utf-8").strip())
        assert data["card_id"] == "15_minutes"

    def test_approved_ko_persisted_exactly(self, tmp_path):
        ko = "크레딧 2를 얻는다. [subroutine] 방벽."
        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(approved_ko=ko), store)
        data = json.loads(store.read_text("utf-8").strip())
        assert data["approved_ko"] == ko

    def test_returns_the_same_record(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        rec = _make_record()
        result = append_approved(rec, store)
        assert result is rec

    def test_does_not_overwrite_existing_records(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(card_id="card_a"), store)
        append_approved(_make_record(card_id="card_b"), store)
        loaded = load_approved(store)
        assert len(loaded) == 2
        ids = {r.card_id for r in loaded}
        assert "card_a" in ids
        assert "card_b" in ids

    def test_creates_parent_directories(self, tmp_path):
        store = tmp_path / "subdir" / "deep" / "approved.jsonl"
        append_approved(_make_record(), store)
        assert store.exists()

    def test_unicode_preserved(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        ko = "러너가 [credit]을 얻는다. “안드로이드”."
        append_approved(_make_record(approved_ko=ko), store)
        data = json.loads(store.read_text("utf-8").strip())
        assert data["approved_ko"] == ko


# ===========================================================================
# load_approved — round-trip and edge cases
# ===========================================================================


class TestLoadApproved:
    def test_returns_empty_when_file_missing(self, tmp_path):
        result = load_approved(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_loads_written_records(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(card_id="15_minutes"), store)
        loaded = load_approved(store)
        assert len(loaded) == 1
        assert loaded[0].card_id == "15_minutes"

    def test_loaded_are_approved_record_instances(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(), store)
        loaded = load_approved(store)
        assert isinstance(loaded[0], ApprovedRecord)

    def test_round_trip_approved_ko(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        ko = "설치한 프로그램을 폐기한다."
        append_approved(_make_record(approved_ko=ko), store)
        assert load_approved(store)[0].approved_ko == ko

    def test_round_trip_interrupt_reasons(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        rec = _make_record(interrupt_reasons=["new_term", "rule_violation"])
        append_approved(rec, store)
        loaded = load_approved(store)
        assert loaded[0].interrupt_reasons == ["new_term", "rule_violation"]

    def test_skips_blank_lines(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        rec = _make_record()
        store.write_text(rec.model_dump_json() + "\n\n\n", encoding="utf-8")
        loaded = load_approved(store)
        assert len(loaded) == 1

    def test_multiple_records_order_preserved(self, tmp_path):
        store = tmp_path / "approved.jsonl"
        ids = [f"card_{i}" for i in range(5)]
        for cid in ids:
            append_approved(_make_record(card_id=cid), store)
        loaded = load_approved(store)
        assert [r.card_id for r in loaded] == ids


# ===========================================================================
# No git writes — the store must never invoke git
# ===========================================================================


class TestNoGitWrites:
    def test_append_does_not_call_git(self, tmp_path, monkeypatch):
        """append_approved must not invoke any git subprocess."""
        calls: list = []

        original_run = subprocess.run

        def patched_run(args, *a, **kw):
            calls.append(args)
            return original_run(args, *a, **kw)

        monkeypatch.setattr(subprocess, "run", patched_run)

        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(), store)

        git_calls = [c for c in calls if isinstance(c, (list, tuple)) and c and "git" in str(c[0])]
        assert git_calls == [], f"Unexpected git subprocess call: {git_calls}"

    def test_load_does_not_call_git(self, tmp_path, monkeypatch):
        """load_approved must not invoke any git subprocess."""
        calls: list = []

        original_run = subprocess.run

        def patched_run(args, *a, **kw):
            calls.append(args)
            return original_run(args, *a, **kw)

        monkeypatch.setattr(subprocess, "run", patched_run)

        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(), store)
        load_approved(store)

        git_calls = [c for c in calls if isinstance(c, (list, tuple)) and c and "git" in str(c[0])]
        assert git_calls == [], f"Unexpected git subprocess call: {git_calls}"

    def test_store_path_is_local_file_not_repo(self, tmp_path):
        """The store path must be a plain file path — not inside .git or remote URL."""
        store = tmp_path / "approved.jsonl"
        append_approved(_make_record(), store)
        assert store.is_file()
        assert ".git" not in str(store)
        assert not str(store).startswith(("http://", "https://", "git@"))
