"""Tests for filter_untranslated.py (AC 3).

Verifies that all three untranslated representations are excluded before
statistical candidate generation, per SERVICE.md §6 pipeline constraint.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from filter_untranslated import has_hangul, is_translated, filter_untranslated


# ---------- has_hangul ---------------------------------------------------


def test_has_hangul_detects_hangul_syllables():
    assert has_hangul("설치") is True


def test_has_hangul_detects_mixed():
    assert has_hangul("install 설치") is True


def test_has_hangul_rejects_empty():
    assert has_hangul("") is False


def test_has_hangul_rejects_ascii_only():
    assert has_hangul("install") is False


def test_has_hangul_rejects_digits_and_symbols():
    assert has_hangul("123 [credit]") is False


# ---------- is_translated: case 1 — ko_data is None (key absent) ---------


def test_is_translated_none_ko_data():
    """Case 1: KO record does not exist at all."""
    assert is_translated(None) is False


# ---------- is_translated: case 2 — field key absent --------------------


def test_is_translated_field_key_absent():
    """Case 2: ko_data dict exists but the target field key is missing."""
    assert is_translated({}, field="text") is False
    assert is_translated({"flavor": "some flavor"}, field="text") is False


# ---------- is_translated: case 3 — field value is None ------------------


def test_is_translated_field_value_null():
    """Case 3: key exists but value is None."""
    assert is_translated({"text": None}) is False


# ---------- is_translated: case 4 — value is non-empty, no Hangul --------


def test_is_translated_value_no_hangul():
    """Case 4 (SERVICE.md case 3): value exists but contains no Hangul."""
    assert is_translated({"text": "Install a program."}) is False


def test_is_translated_value_empty_string():
    """Empty string — treated as no Hangul."""
    assert is_translated({"text": ""}) is False


# ---------- is_translated: valid translation passes ----------------------


def test_is_translated_valid():
    assert is_translated({"text": "프로그램을 설치한다."}) is True


def test_is_translated_mixed_text_with_hangul():
    assert is_translated({"text": "[credit]: 설치"}) is True


def test_is_translated_custom_field():
    assert is_translated({"flavor": "맛있다"}, field="flavor") is True
    assert is_translated({"text": "英語"}, field="flavor") is False


# ---------- filter_untranslated ------------------------------------------


def _make_pairs(ko_values: list) -> list[dict]:
    """Build pairs list from ko 'text' values. None means ko_key is absent."""
    pairs = []
    for i, val in enumerate(ko_values):
        pair: dict = {"id": str(i), "en": {"text": f"EN text {i}"}}
        if val is not _SENTINEL:
            if val is None:
                pair["ko"] = None
            else:
                pair["ko"] = {"text": val}
        pairs.append(pair)
    return pairs


_SENTINEL = object()  # represents "ko key absent from pair"


def test_filter_removes_missing_ko_key():
    """Pairs with no 'ko' key entirely are filtered out."""
    pairs = [{"id": "x", "en": {"text": "hello"}}]  # no 'ko' key
    assert filter_untranslated(pairs) == []


def test_filter_removes_null_ko_entry():
    """Pairs where ko_key value is None are filtered out (case 2)."""
    pairs = [{"id": "x", "en": {"text": "hello"}, "ko": None}]
    assert filter_untranslated(pairs) == []


def test_filter_removes_null_text_field():
    """Pairs where ko['text'] is None are filtered out (case 3)."""
    pairs = [{"id": "x", "en": {"text": "hello"}, "ko": {"text": None}}]
    assert filter_untranslated(pairs) == []


def test_filter_removes_no_hangul_text():
    """Pairs where ko['text'] has no Hangul are filtered out (case 4)."""
    pairs = [{"id": "x", "en": {"text": "Install."}, "ko": {"text": "Install."}}]
    assert filter_untranslated(pairs) == []


def test_filter_keeps_hangul_text():
    """Pairs with proper Hangul KO text pass through."""
    pairs = [{"id": "x", "en": {"text": "Install."}, "ko": {"text": "설치한다."}}]
    result = filter_untranslated(pairs)
    assert len(result) == 1
    assert result[0]["id"] == "x"


def test_filter_mixed_batch():
    """All three bad cases plus one good case — only the good one survives."""
    pairs = [
        {"id": "a", "en": {"text": "T"}, "ko": None},            # case 2
        {"id": "b", "en": {"text": "T"}, "ko": {"text": None}},  # case 3
        {"id": "c", "en": {"text": "T"}, "ko": {"text": "Eng"}}, # case 4
        {"id": "d", "en": {"text": "T"}},                        # case 1
        {"id": "e", "en": {"text": "T"}, "ko": {"text": "한글"}},# valid
    ]
    result = filter_untranslated(pairs)
    assert [r["id"] for r in result] == ["e"]


def test_filter_custom_ko_key():
    pairs = [
        {"id": "a", "translation": {"text": "안녕"}},
        {"id": "b", "translation": {"text": "hello"}},
    ]
    result = filter_untranslated(pairs, ko_key="translation")
    assert [r["id"] for r in result] == ["a"]


def test_filter_custom_field():
    pairs = [
        {"id": "a", "ko": {"flavor": "맛있다"}},
        {"id": "b", "ko": {"flavor": "tasty"}},
        {"id": "c", "ko": {"text": "한글"}},  # flavor key absent
    ]
    result = filter_untranslated(pairs, field="flavor")
    assert [r["id"] for r in result] == ["a"]


def test_filter_empty_input():
    assert filter_untranslated([]) == []


def test_filter_preserves_order():
    """Output order must match input order (for deterministic pipeline)."""
    pairs = [
        {"id": str(i), "ko": {"text": "한글" if i % 2 == 0 else "english"}}
        for i in range(10)
    ]
    result = filter_untranslated(pairs)
    expected_ids = [str(i) for i in range(0, 10, 2)]
    assert [r["id"] for r in result] == expected_ids
