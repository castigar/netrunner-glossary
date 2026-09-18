"""Tests for conflict_detector.py (AC 6).

AC requirement:
  When the same EN term has different KO translations, the batch does NOT
  automatically pick the correct answer. It does not use "latest release date
  adoption" or "most frequent adoption". Instead, conflicts are separated into
  conflicts.json and the conflicted EN term is excluded from glossary.json.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conflict_detector import (
    ConflictDetectionResult,
    ConflictEntry,
    detect_conflicts,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _pairs(*tuples, include_card_id=False):
    """Build a list of term-pair dicts from (en, ko) or (en, ko, card_id) tuples."""
    result = []
    for i, t in enumerate(tuples):
        en, ko = t[0], t[1]
        d = {"en_term": en, "ko_term": ko}
        if include_card_id:
            card_id = t[2] if len(t) > 2 else f"card_{i}"
            d["card_id"] = card_id
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# detect_conflicts — basic behaviour
# ---------------------------------------------------------------------------


def test_no_conflict_when_all_unique():
    """EN terms each with a single distinct KO translation → no conflicts."""
    pairs = _pairs(("install", "설치"), ("trash", "파기"), ("rez", "레즈"))
    result = detect_conflicts(pairs)
    assert result.conflicts == []
    assert result.clean == {"install": "설치", "trash": "파기", "rez": "레즈"}


def test_conflict_detected_on_multiple_ko_for_same_en():
    """One EN term → two distinct KO terms → conflict, excluded from clean."""
    pairs = _pairs(("guard", "경비원"), ("guard", "보초"))
    result = detect_conflicts(pairs)

    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.en_term == "guard"
    assert set(conflict.ko_variants) == {"경비원", "보초"}
    # Must be excluded from clean glossary
    assert "guard" not in result.clean


def test_conflict_does_not_use_frequency_resolution():
    """Most-frequent KO variant must NOT be auto-selected; all must stay as conflict."""
    # "install" → "설치" appears 3 times, "설치하다" appears 1 time.
    # A frequency-based resolver would pick "설치". The correct behaviour is
    # to mark the EN term as a conflict regardless.
    pairs = _pairs(
        ("install", "설치"),
        ("install", "설치"),
        ("install", "설치"),
        ("install", "설치하다"),
    )
    result = detect_conflicts(pairs)

    # Conflict must be reported — NOT resolved to the majority "설치"
    assert len(result.conflicts) == 1
    assert result.conflicts[0].en_term == "install"
    assert "install" not in result.clean


def test_conflict_does_not_use_latest_date_resolution():
    """No date-based tiebreak; term stays in conflicts regardless of card order."""
    # If the batch used "latest card wins" it would pick the last ko value.
    # We verify that the conflict is still reported regardless.
    pairs = _pairs(
        ("sentry", "파수"),  # older card (first in list)
        ("sentry", "방벽"),  # newer card (last in list)
    )
    result = detect_conflicts(pairs)
    assert len(result.conflicts) == 1
    assert "sentry" not in result.clean


def test_clean_terms_excluded_from_conflicts():
    """Terms without conflict must NOT appear in the conflicts list."""
    pairs = _pairs(
        ("install", "설치"),
        ("trash", "파기"),
        ("guard", "경비원"),
        ("guard", "보초"),
    )
    result = detect_conflicts(pairs)
    conflict_terms = {c.en_term for c in result.conflicts}
    assert "install" not in conflict_terms
    assert "trash" not in conflict_terms


def test_multiple_conflicts_detected_independently():
    """Two independent conflicts are each reported."""
    pairs = _pairs(
        ("guard", "경비원"),
        ("guard", "보초"),
        ("sentry", "파수"),
        ("sentry", "방벽"),
        ("install", "설치"),
    )
    result = detect_conflicts(pairs)
    conflict_terms = {c.en_term for c in result.conflicts}
    assert "guard" in conflict_terms
    assert "sentry" in conflict_terms
    assert len(result.conflicts) == 2
    assert result.clean == {"install": "설치"}


def test_three_way_conflict():
    """Three distinct KO translations for one EN term → one conflict with 3 variants."""
    pairs = _pairs(("program", "프로그램"), ("program", "소프트웨어"), ("program", "앱"))
    result = detect_conflicts(pairs)
    assert len(result.conflicts) == 1
    assert len(result.conflicts[0].ko_variants) == 3


def test_empty_pairs_returns_empty_result():
    """Empty input → no clean terms, no conflicts."""
    result = detect_conflicts([])
    assert result.clean == {}
    assert result.conflicts == []


# ---------------------------------------------------------------------------
# Card id tracking in conflicts
# ---------------------------------------------------------------------------


def test_source_card_ids_collected_per_ko_variant():
    """Source card ids must be tracked per KO variant when card_id field present."""
    pairs = [
        {"en_term": "guard", "ko_term": "경비원", "card_id": "card_001"},
        {"en_term": "guard", "ko_term": "보초",   "card_id": "card_002"},
        {"en_term": "guard", "ko_term": "경비원", "card_id": "card_003"},
    ]
    result = detect_conflicts(pairs)
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    # "경비원" appears in two cards; "보초" in one
    assert set(conflict.source_card_ids.get("경비원", [])) == {"card_001", "card_003"}
    assert conflict.source_card_ids.get("보초", []) == ["card_002"]


def test_no_card_id_tracking_when_key_is_none():
    """When card_id_key=None, source_card_ids must be empty for all conflicts."""
    pairs = _pairs(("guard", "경비원"), ("guard", "보초"))
    result = detect_conflicts(pairs, card_id_key=None)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].source_card_ids == {}


# ---------------------------------------------------------------------------
# write_conflicts_json
# ---------------------------------------------------------------------------


def test_write_conflicts_json_creates_valid_file(tmp_path):
    """write_conflicts_json must write a valid JSON array of conflict entries."""
    pairs = _pairs(("guard", "경비원"), ("guard", "보초"))
    result = detect_conflicts(pairs)
    out_path = tmp_path / "conflicts.json"
    result.write_conflicts_json(out_path)

    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    entry = data[0]
    assert entry["en_term"] == "guard"
    assert set(entry["ko_variants"]) == {"경비원", "보초"}


def test_write_conflicts_json_empty_when_no_conflicts(tmp_path):
    """With no conflicts, write_conflicts_json must write an empty array."""
    pairs = _pairs(("install", "설치"), ("trash", "파기"))
    result = detect_conflicts(pairs)
    out_path = tmp_path / "conflicts.json"
    result.write_conflicts_json(out_path)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data == []


def test_write_conflicts_json_uses_utf8(tmp_path):
    """Output file must be valid UTF-8 with non-ASCII Korean characters preserved."""
    pairs = _pairs(("guard", "경비원"), ("guard", "보초"))
    result = detect_conflicts(pairs)
    out_path = tmp_path / "conflicts.json"
    result.write_conflicts_json(out_path)

    raw = out_path.read_text(encoding="utf-8")
    assert "경비원" in raw
    assert "보초" in raw


# ---------------------------------------------------------------------------
# ConflictEntry.to_dict
# ---------------------------------------------------------------------------


def test_conflict_entry_to_dict_has_required_keys():
    """to_dict() must include en_term, ko_variants, source_card_ids."""
    entry = ConflictEntry(
        en_term="guard",
        ko_variants=["경비원", "보초"],
        source_card_ids={"경비원": ["card_001"]},
    )
    d = entry.to_dict()
    assert "en_term" in d
    assert "ko_variants" in d
    assert "source_card_ids" in d
    assert d["en_term"] == "guard"


# ---------------------------------------------------------------------------
# Custom field names
# ---------------------------------------------------------------------------


def test_custom_en_and_ko_field_names():
    """detect_conflicts must work with non-default field names."""
    pairs = [
        {"source": "guard", "target": "경비원"},
        {"source": "guard", "target": "보초"},
    ]
    result = detect_conflicts(pairs, en_key="source", ko_key="target", card_id_key=None)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].en_term == "guard"


def test_pairs_missing_en_or_ko_are_skipped():
    """Pairs with empty or missing en_term / ko_term are silently skipped."""
    pairs = [
        {"en_term": "",       "ko_term": "설치"},   # empty en → skip
        {"en_term": "trash",  "ko_term": ""},        # empty ko → skip
        {"en_term": "rez",    "ko_term": "레즈"},    # valid
    ]
    result = detect_conflicts(pairs)
    assert result.clean == {"rez": "레즈"}
    assert result.conflicts == []
