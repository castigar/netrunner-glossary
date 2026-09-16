"""Tests for corpus_split.py (AC: ac_fb9a4fc0c1ec987b).

Requires CORPUS_ROOT environment variable pointing to the netrunner-cards-json
checkout; tests are skipped if it is unset or invalid.
"""
import os
import sys
from pathlib import Path

import pytest

# Make src importable when running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from corpus_split import (  # noqa: E402
    _has_hangul,
    load_clean_corpus,
    split_corpus,
    build_split,
)

CORPUS_ROOT = os.environ.get("CORPUS_ROOT", "")
SKIP_REASON = "CORPUS_ROOT not set or does not exist"
needs_corpus = pytest.mark.skipif(
    not CORPUS_ROOT or not Path(CORPUS_ROOT).is_dir(),
    reason=SKIP_REASON,
)


# ---------- unit tests (no corpus needed) ----------


def test_has_hangul_positive():
    assert _has_hangul("클릭") is True


def test_has_hangul_negative_empty():
    assert _has_hangul("") is False


def test_has_hangul_negative_ascii():
    assert _has_hangul("install") is False


def test_split_sizes():
    cards = [{"id": str(i)} for i in range(980)]
    hold_out, train = split_corpus(cards, seed=42, holdout_size=100)
    assert len(hold_out) == 100
    assert len(train) == 880


def test_split_disjoint():
    cards = [{"id": str(i)} for i in range(980)]
    hold_out, train = split_corpus(cards, seed=42, holdout_size=100)
    hold_ids = {c["id"] for c in hold_out}
    train_ids = {c["id"] for c in train}
    assert hold_ids.isdisjoint(train_ids)


def test_split_reproducible():
    cards = [{"id": str(i)} for i in range(980)]
    h1, t1 = split_corpus(cards, seed=42)
    h2, t2 = split_corpus(cards, seed=42)
    assert [c["id"] for c in h1] == [c["id"] for c in h2]
    assert [c["id"] for c in t1] == [c["id"] for c in t2]


def test_split_different_seed_differs():
    cards = [{"id": str(i)} for i in range(980)]
    h1, _ = split_corpus(cards, seed=42)
    h2, _ = split_corpus(cards, seed=99)
    assert [c["id"] for c in h1] != [c["id"] for c in h2]


# ---------- integration tests (corpus required) ----------


@needs_corpus
def test_clean_corpus_size():
    """Clean corpus (2012-2016, EN text, KO with Hangul) must be exactly 980 cards."""
    corpus = load_clean_corpus(Path(CORPUS_ROOT))
    assert len(corpus) == 980, f"Expected 980, got {len(corpus)}"


@needs_corpus
def test_clean_corpus_all_have_hangul():
    corpus = load_clean_corpus(Path(CORPUS_ROOT))
    for card in corpus:
        assert _has_hangul(card["ko_text"]), f"No Hangul in KO text for {card['id']}"


@needs_corpus
def test_clean_corpus_date_range():
    corpus = load_clean_corpus(Path(CORPUS_ROOT))
    years = {int(c["date"][:4]) for c in corpus}
    assert years.issubset(set(range(2012, 2017))), f"Out-of-range years: {years - set(range(2012, 2017))}"


@needs_corpus
def test_build_split_counts():
    hold_out, train = build_split(Path(CORPUS_ROOT))
    assert len(hold_out) == 100
    assert len(train) == 880


@needs_corpus
def test_build_split_holdout_excluded_from_train():
    hold_out, train = build_split(Path(CORPUS_ROOT))
    hold_ids = {c["id"] for c in hold_out}
    train_ids = {c["id"] for c in train}
    assert hold_ids.isdisjoint(train_ids), "Hold-out IDs leaked into train set"


@needs_corpus
def test_build_split_deterministic():
    """Same seed must produce identical splits on repeated calls."""
    h1, t1 = build_split(Path(CORPUS_ROOT))
    h2, t2 = build_split(Path(CORPUS_ROOT))
    assert [c["id"] for c in h1] == [c["id"] for c in h2]
    assert [c["id"] for c in t1] == [c["id"] for c in t2]
