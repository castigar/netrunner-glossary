"""term_extraction_eval.py — Precision/recall evaluation for the term extraction algorithm.

Uses card_subtypes.json 88 entries as the direct scoring gold set.

Matching rule:
  EN side — subtype id is normalized to a candidate en_term by replacing '_' with ' '.
             e.g. "code_gate" → "code gate", "barrier" → "barrier"
  A subtype is considered *found* when its normalized EN form appears as the en_term
  of any extracted TermCandidate.

Metrics:
  TP  = gold EN terms that appear in the extracted candidate EN terms.
  FP_count = extracted EN terms that do NOT appear in the gold set.
  FN_count = gold EN terms NOT found in the extracted candidate EN terms.
  Precision = TP / (TP + FP) = TP / |extracted_en_terms|
  Recall    = TP / (TP + FN) = TP / |gold_en_terms|
  F1        = harmonic mean of precision and recall.

Note: precision will naturally be low because generate_candidates() surfaces
many high-frequency EN n-grams that are not card subtypes. Recall is the
primary signal — it shows how many of the 88 official subtypes the statistical
extractor recovers from the corpus.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from term_candidate_extractor import TermCandidate


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

    Replaces underscores with spaces; keeps everything lowercase.
    e.g. "code_gate" → "code gate", "ai" → "ai"
    """
    return subtype_id.replace("_", " ").lower()


def evaluate_extraction(
    candidates: list[TermCandidate],
    gold_subtypes: list[dict],
) -> EvaluationResult:
    """Compute precision and recall against the card_subtypes.json gold set.

    Args:
        candidates:    Output of generate_candidates() — list of TermCandidate.
        gold_subtypes: List of {"id": <str>, "name": <str>} dicts from
                       card_subtypes.json (88 entries expected).

    Returns:
        EvaluationResult with precision, recall, f1, and supporting counts.
    """
    gold_en_terms: set[str] = {
        _normalize_subtype_id(entry["id"]) for entry in gold_subtypes
    }
    extracted_en_terms: set[str] = {c.en_term for c in candidates}

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
