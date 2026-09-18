"""term_extraction_eval.py — Precision/recall evaluation for the term extraction algorithm.

Uses card_subtypes.json 88 entries as the direct scoring gold set.

Scored path (SERVICE.md §6, 결정 ①): the 88-entry gold set covers **subtypes only**,
so it scores the 부제 path (subtype_extractor).  The rule path is scored against a
separate hand-labelled gold set and is not measured here.

Matching rule:
  Both sides are normalized the same way: '_' and '-' become ' ', then lowercase.
  e.g. gold id "code_gate" → "code gate"; printed keyword "Consumer-Grade" →
  "consumer grade", which is the same term as gold id "consumer_grade".
  Normalizing only the gold side scored the 부제 path's printed hyphen forms
  ("G-Mod", "Consumer-Grade") as both a miss and a false positive.
  A subtype is considered *found* when its normalized EN form matches the
  normalized en_term of any extracted candidate.

Metrics:
  TP  = gold EN terms that appear in the extracted candidate EN terms.
  FP_count = extracted EN terms that do NOT appear in the gold set.
  FN_count = gold EN terms NOT found in the extracted candidate EN terms.
  Precision = TP / (TP + FP) = TP / |extracted_en_terms|
  Recall    = TP / (TP + FN) = TP / |gold_en_terms|
  F1        = harmonic mean of precision and recall.

Note: on the 부제 path precision is meaningful, because the candidates are whole
keyword-field elements. Pointing this at the rule path instead would depress
precision by construction — its n-gram candidates are mostly not subtypes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import json


class HasEnTerm(Protocol):
    """Anything carrying an EN term: TermCandidate (rule path) or SubtypePair."""

    en_term: str


@dataclass
class EvaluationResult:
    """Precision/recall evaluation result against card_subtypes.json gold set."""

    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int
    gold_size: int
    extracted_size: int
    found_terms: frozenset[str]
    missed_terms: frozenset[str]

    def report(self) -> str:
        return (
            f"Precision: {self.precision:.4f}  "
            f"Recall: {self.recall:.4f}  "
            f"F1: {self.f1:.4f}  "
            f"TP={self.tp} FP={self.fp} FN={self.fn}  "
            f"Gold={self.gold_size} Extracted={self.extracted_size}"
        )


def _normalize_subtype_id(subtype_id: str) -> str:
    """Convert a subtype id slug to the form expected in card text.

    Replaces underscores and hyphens with spaces; keeps everything lowercase.
    e.g. "code_gate" → "code gate", "ai" → "ai", "G-Mod" → "g mod"

    The same function normalizes extracted terms, so a printed hyphen form
    matches its underscored gold id.
    """
    return subtype_id.replace("_", " ").replace("-", " ").lower()


def evaluate_extraction(
    candidates: list[HasEnTerm],
    gold_subtypes: list[dict],
) -> EvaluationResult:
    """Compute precision and recall against the card_subtypes.json gold set.

    Args:
        candidates:    Extraction output — anything with an ``en_term``, i.e.
                       SubtypePair (부제 경로) or TermCandidate (룰 경로).
        gold_subtypes: List of {"id": <str>, "name": <str>} dicts from
                       card_subtypes.json (88 entries expected).

    Returns:
        EvaluationResult with precision, recall, f1, and supporting counts.
    """
    gold_en_terms: set[str] = {
        _normalize_subtype_id(entry["id"]) for entry in gold_subtypes
    }
    extracted_en_terms: set[str] = {
        _normalize_subtype_id(c.en_term) for c in candidates
    }

    found = gold_en_terms & extracted_en_terms
    missed = gold_en_terms - extracted_en_terms
    fp_count = len(extracted_en_terms - gold_en_terms)

    tp = len(found)
    fn = len(missed)
    precision = tp / len(extracted_en_terms) if extracted_en_terms else 0.0
    recall = tp / len(gold_en_terms) if gold_en_terms else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return EvaluationResult(
        precision=precision,
        recall=recall,
        f1=f1,
        tp=tp,
        fp=fp_count,
        fn=fn,
        gold_size=len(gold_en_terms),
        extracted_size=len(extracted_en_terms),
        found_terms=frozenset(found),
        missed_terms=frozenset(missed),
    )


def load_gold_subtypes(card_subtypes_path: str | Path) -> list[dict]:
    """Load card_subtypes.json and return the list of {"id", "name"} entries."""
    path = Path(card_subtypes_path)
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}, got {type(data).__name__}")
    return data
