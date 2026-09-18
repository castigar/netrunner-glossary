"""Tests for load_official_glossary.py (AC 4).

Verifies that the five official KO glossary files are loaded as-is and that
all IDs are placed in the excluded_ids set, preventing statistical extraction
from operating on terms already covered by the official glossary.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from load_official_glossary import (
    OfficialGlossary,
    load_official_glossary,
    GLOSSARY_FILES,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

MINIMAL_FILES = {
    "card_subtypes": [{"id": "barrier", "name": "방벽"}, {"id": "ai", "name": "AI"}],
    "card_types": [{"id": "ice", "name": "아이스"}, {"id": "agenda", "name": "아젠다"}],
    "factions": [{"id": "anarch", "name": "아나크"}],
    "card_cycles": [{"id": "genesis", "name": "기원"}],
    "card_sets": [{"id": "core_set", "name": "코어 세트"}],
}


def _make_glossary_dir(tmp_path: Path, files: dict | None = None) -> Path:
    """Write glossary JSON files into *tmp_path* and return the directory."""
    data = files if files is not None else MINIMAL_FILES
    for name, records in data.items():
        (tmp_path / f"{name}.json").write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8"
        )
    return tmp_path


# ---------------------------------------------------------------------------
# GLOSSARY_FILES constant
# ---------------------------------------------------------------------------


def test_glossary_files_constant_contains_five_categories():
    assert len(GLOSSARY_FILES) == 5
    for name in ("card_subtypes", "card_types", "factions", "card_cycles", "card_sets"):
        assert name in GLOSSARY_FILES


# ---------------------------------------------------------------------------
# load_official_glossary: happy path
# ---------------------------------------------------------------------------


def test_load_returns_official_glossary_instance(tmp_path):
    _make_glossary_dir(tmp_path)
    result = load_official_glossary(tmp_path)
    assert isinstance(result, OfficialGlossary)


def test_load_populates_all_five_categories(tmp_path):
    _make_glossary_dir(tmp_path)
    g = load_official_glossary(tmp_path)
    assert set(g.entries.keys()) == set(GLOSSARY_FILES)


def test_load_correct_ko_names(tmp_path):
    _make_glossary_dir(tmp_path)
    g = load_official_glossary(tmp_path)
    assert g.entries["card_subtypes"]["barrier"] == "방벽"
    assert g.entries["card_types"]["ice"] == "아이스"
    assert g.entries["factions"]["anarch"] == "아나크"
    assert g.entries["card_cycles"]["genesis"] == "기원"
    assert g.entries["card_sets"]["core_set"] == "코어 세트"


def test_load_excluded_ids_covers_all_entries(tmp_path):
    _make_glossary_dir(tmp_path)
    g = load_official_glossary(tmp_path)
    # Every id from every category must appear in excluded_ids.
    for category_terms in g.entries.values():
        for entry_id in category_terms:
            assert entry_id in g.excluded_ids


def test_load_len_equals_total_entry_count(tmp_path):
    _make_glossary_dir(tmp_path)
    g = load_official_glossary(tmp_path)
    expected = sum(len(v) for v in MINIMAL_FILES.values())
    assert len(g) == expected


def test_all_terms_returns_flat_dict(tmp_path):
    _make_glossary_dir(tmp_path)
    g = load_official_glossary(tmp_path)
    flat = g.all_terms()
    assert isinstance(flat, dict)
    assert flat["barrier"] == "방벽"
    assert flat["ice"] == "아이스"
    assert flat["anarch"] == "아나크"
    assert flat["genesis"] == "기원"
    assert flat["core_set"] == "코어 세트"


# ---------------------------------------------------------------------------
# Statistical extraction exclusion: excluded_ids is non-empty and correct
# ---------------------------------------------------------------------------


def test_excluded_ids_prevents_overlap_with_official_terms(tmp_path):
    """The excluded_ids set is what callers use to skip official terms during
    statistical extraction. It must contain exactly the ids loaded."""
    _make_glossary_dir(tmp_path)
    g = load_official_glossary(tmp_path)
    all_ids = {entry_id for cat in MINIMAL_FILES.values() for rec in cat for entry_id in [rec["id"]]}
    assert g.excluded_ids == all_ids


def test_statistical_extraction_skips_official_ids(tmp_path):
    """Simulate the extraction filter: any id in excluded_ids must not be
    passed to the statistical extraction step."""
    _make_glossary_dir(tmp_path)
    g = load_official_glossary(tmp_path)
    # A mock set of candidate terms that mix official and novel ids.
    candidate_ids = {"barrier", "novel_term_xyz", "genesis", "unknown_card_mechanic"}
    remaining = candidate_ids - g.excluded_ids
    assert "barrier" not in remaining
    assert "genesis" not in remaining
    assert "novel_term_xyz" in remaining
    assert "unknown_card_mechanic" in remaining


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_load_raises_on_missing_file(tmp_path):
    """Missing any of the five files raises FileNotFoundError."""
    # Write only four files; omit card_sets.json
    files_without_sets = {k: v for k, v in MINIMAL_FILES.items() if k != "card_sets"}
    _make_glossary_dir(tmp_path, files=files_without_sets)
    with pytest.raises(FileNotFoundError, match="card_sets.json"):
        load_official_glossary(tmp_path)


def test_load_raises_on_non_array_json(tmp_path):
    """A file that contains a JSON object instead of array raises ValueError."""
    _make_glossary_dir(tmp_path)
    # Overwrite card_types.json with a dict instead of a list
    (tmp_path / "card_types.json").write_text('{"id": "ice", "name": "아이스"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Expected JSON array"):
        load_official_glossary(tmp_path)


def test_load_raises_on_entry_missing_id(tmp_path):
    """An entry without 'id' field raises ValueError."""
    bad_files = dict(MINIMAL_FILES)
    bad_files["factions"] = [{"name": "아나크"}]  # 'id' is missing
    _make_glossary_dir(tmp_path, files=bad_files)
    with pytest.raises(ValueError, match="missing 'id' or 'name'"):
        load_official_glossary(tmp_path)


def test_load_raises_on_entry_missing_name(tmp_path):
    """An entry without 'name' field raises ValueError."""
    bad_files = dict(MINIMAL_FILES)
    bad_files["card_cycles"] = [{"id": "genesis"}]  # 'name' is missing
    _make_glossary_dir(tmp_path, files=bad_files)
    with pytest.raises(ValueError, match="missing 'id' or 'name'"):
        load_official_glossary(tmp_path)


# ---------------------------------------------------------------------------
# Real corpus smoke test (skipped when corpus not available)
# ---------------------------------------------------------------------------

CORPUS_KO_DIR = Path(
    r"C:\Users\SDS\Desktop\netrunner-corpus\netrunner-cards-json\v2\translations\ko"
)


@pytest.mark.skipif(
    not CORPUS_KO_DIR.exists(),
    reason="Corpus not available at expected path",
)
def test_real_corpus_card_subtypes_has_88_entries():
    g = load_official_glossary(CORPUS_KO_DIR)
    assert len(g.entries["card_subtypes"]) == 88


@pytest.mark.skipif(
    not CORPUS_KO_DIR.exists(),
    reason="Corpus not available at expected path",
)
def test_real_corpus_excluded_ids_covers_all_loaded_entries():
    g = load_official_glossary(CORPUS_KO_DIR)
    for category_terms in g.entries.values():
        for entry_id in category_terms:
            assert entry_id in g.excluded_ids


@pytest.mark.skipif(
    not CORPUS_KO_DIR.exists(),
    reason="Corpus not available at expected path",
)
def test_real_corpus_all_five_categories_loaded():
    g = load_official_glossary(CORPUS_KO_DIR)
    assert set(g.entries.keys()) == set(GLOSSARY_FILES)
