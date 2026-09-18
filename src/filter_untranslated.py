"""filter_untranslated.py — Pre-statistical untranslated record filter.

Must run before any statistical candidate generation (SERVICE.md §6 pipeline step 2).

Three exclusion criteria applied to the nominated *field* in each KO data dict:
  1. key is absent from the KO data dict
  2. key is present but the value is None
  3. value is a non-empty string that contains no Hangul characters

Without this filter, applying conflict-exclusion rules on the full corpus
eliminates ~54% of subtitle terms from the glossary; after filtering only
~15% are excluded (those are genuine one-EN→two-KO conflicts).
"""
from __future__ import annotations

import unicodedata


def has_hangul(text: str) -> bool:
    """Return True iff *text* contains at least one Hangul syllable or jamo."""
    return any(unicodedata.name(c, "").startswith("HANGUL") for c in text)


def is_translated(ko_data: dict | None, field: str = "text") -> bool:
    """Return True iff *ko_data* has a non-null *field* value that contains Hangul.

    Covers all three untranslated representations:
    - Case 1: ko_data is None (no KO file/record exists)
    - Case 2: field key absent from ko_data
    - Case 3: field value is None
    - Case 4: field value is a string with no Hangul characters
    """
    if ko_data is None:
        return False
    if field not in ko_data:
        return False
    value = ko_data[field]
    if value is None:
        return False
    return has_hangul(str(value))


def filter_untranslated(
    pairs: list[dict],
    ko_key: str = "ko",
    field: str = "text",
) -> list[dict]:
    """Return only pairs where the KO translation is present and contains Hangul.

    Each element of *pairs* must be a dict with at least:
      - An EN card entry (any key)
      - A KO card entry at *ko_key* (dict or None)

    Records are excluded when the *field* in ko_data:
      (1) the *ko_key* entry is absent entirely
      (2) the *ko_key* entry is None
      (3) *field* key is absent inside ko_data
      (4) *field* value is None
      (5) *field* value is a string containing no Hangul characters

    Cases (1)-(2) correspond to "KO file does not exist".
    Cases (3)-(4) correspond to "key exists, value is null".
    Case (5) corresponds to "value exists but contains no Hangul".
    """
    return [p for p in pairs if is_translated(p.get(ko_key), field=field)]
