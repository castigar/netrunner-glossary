"""conflict_detector.py — Term conflict detection for glossary building.

SERVICE.md §6 pipeline step (용어 충돌 검출):
  When the same EN term maps to more than one distinct KO translation,
  the batch does NOT automatically resolve it. It does NOT use:
    - latest release date adoption
    - most-frequent (majority vote) adoption
  Instead, conflicts are separated into conflicts.json and the conflicted
  EN term is excluded from glossary.json entirely until a human resolves it.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ConflictEntry:
    """A detected conflict: one EN term with multiple distinct KO translations."""

    en_term: str
    ko_variants: list[str]
    source_card_ids: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "en_term": self.en_term,
            "ko_variants": self.ko_variants,
            "source_card_ids": self.source_card_ids,
        }


@dataclass
class ConflictDetectionResult:
    """Result of running detect_conflicts()."""

    clean: dict[str, str]
    conflicts: list[ConflictEntry]

    def write_conflicts_json(self, path: str | Path) -> None:
        """Write conflicts to the given JSON file path."""
        output = [e.to_dict() for e in self.conflicts]
        Path(path).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


def detect_conflicts(
    term_pairs: list[dict],
    en_key: str = "en_term",
    ko_key: str = "ko_term",
    card_id_key: str | None = "card_id",
) -> ConflictDetectionResult:
    """Detect EN terms that have multiple distinct KO translations.

    For each EN term that maps to more than one distinct KO translation,
    the term is classified as a conflict and excluded from the clean glossary.
    No automatic resolution is performed (no frequency-based or date-based tiebreak).

    Args:
        term_pairs:  List of dicts, each representing one accepted EN→KO term pair.
                     Each dict must have *en_key* and *ko_key* fields.
        en_key:      Field name for the EN term (default "en_term").
        ko_key:      Field name for the KO translation (default "ko_term").
        card_id_key: Optional field name for source card id. When present, the
                     source card ids per KO variant are collected for the
                     conflict report. Pass None to skip card id tracking.

    Returns:
        ConflictDetectionResult with:
          - clean: {en_term: ko_term} for terms with exactly one KO translation
          - conflicts: list of ConflictEntry for terms with multiple KO translations
    """
    ko_variants: dict[str, set[str]] = defaultdict(set)
    card_ids_per_pair: dict[tuple[str, str], list[str]] = defaultdict(list)

    for pair in term_pairs:
        en = pair.get(en_key, "")
        ko = pair.get(ko_key, "")
        if not en or not ko:
            continue
        ko_variants[en].add(ko)
        if card_id_key:
            card_id = pair.get(card_id_key)
            if card_id:
                card_ids_per_pair[(en, ko)].append(str(card_id))

    clean: dict[str, str] = {}
    conflicts: list[ConflictEntry] = []

    for en_term, variants in ko_variants.items():
        if len(variants) == 1:
            clean[en_term] = next(iter(variants))
        else:
            sorted_variants = sorted(variants)
            source_card_ids: dict[str, list[str]] = {}
            for ko_v in sorted_variants:
                ids = card_ids_per_pair.get((en_term, ko_v), [])
                if ids:
                    source_card_ids[ko_v] = ids
            conflicts.append(
                ConflictEntry(
                    en_term=en_term,
                    ko_variants=sorted_variants,
                    source_card_ids=source_card_ids,
                )
            )

    return ConflictDetectionResult(clean=clean, conflicts=conflicts)
