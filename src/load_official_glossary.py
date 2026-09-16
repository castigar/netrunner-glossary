"""load_official_glossary.py — Load official KO glossary files from the corpus.

SERVICE.md §6 pipeline step 3: load official glossary files as-is; do NOT
extract them statistically. Statistical extraction applies only to rule-text
terms (install, trash, rez, etc.).

Five files are loaded from v2/translations/ko/:
  - card_subtypes.json   (88 items)
  - card_types.json
  - factions.json
  - card_cycles.json
  - card_sets.json

Each file contains a JSON array of {"id": <str>, "name": <str>} objects.
The returned OfficialGlossary maps each EN id to its KO name, keyed by
source category so callers can exclude these terms from statistical extraction.
"""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field


GLOSSARY_FILES = (
    "card_subtypes",
    "card_types",
    "factions",
    "card_cycles",
    "card_sets",
)


@dataclass
class OfficialGlossary:
    """Official KO glossary loaded from corpus v2/translations/ko/.

    Attributes:
        entries: dict mapping category → {id: ko_name}
        excluded_ids: flat set of all ids covered by the official glossary
                      (these must be excluded from statistical extraction)
    """
    entries: dict[str, dict[str, str]] = field(default_factory=dict)
    excluded_ids: set[str] = field(default_factory=set)

    def all_terms(self) -> dict[str, str]:
        """Return flat {id: ko_name} across all categories."""
        merged: dict[str, str] = {}
        for category_terms in self.entries.values():
            merged.update(category_terms)
        return merged

    def __len__(self) -> int:
        return sum(len(v) for v in self.entries.values())


def _load_json_array(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}, got {type(data).__name__}")
    return data


def load_official_glossary(ko_translations_dir: str | Path) -> OfficialGlossary:
    """Load the five official glossary files from *ko_translations_dir*.

    Args:
        ko_translations_dir: Path to v2/translations/ko/ inside the corpus.
                             Must contain the five glossary JSON files.

    Returns:
        OfficialGlossary with entries grouped by source category and a flat
        excluded_ids set for use as a pre-filter in statistical extraction.

    Raises:
        FileNotFoundError: if any of the five required files is missing.
        ValueError: if a file is not a JSON array or entries lack 'id'/'name'.
    """
    base = Path(ko_translations_dir)
    glossary = OfficialGlossary()

    for category in GLOSSARY_FILES:
        path = base / f"{category}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Official glossary file not found: {path}\n"
                f"Expected v2/translations/ko/{category}.json in corpus."
            )
        records = _load_json_array(path)
        category_terms: dict[str, str] = {}
        for i, rec in enumerate(records):
            if "id" not in rec or "name" not in rec:
                raise ValueError(
                    f"Entry {i} in {path} is missing 'id' or 'name': {rec!r}"
                )
            entry_id = rec["id"]
            ko_name = rec["name"]
            category_terms[entry_id] = ko_name
            glossary.excluded_ids.add(entry_id)
        glossary.entries[category] = category_terms

    return glossary
