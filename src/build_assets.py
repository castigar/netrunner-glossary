"""build_assets.py — phase-1 asset build batch (SERVICE.md §6, 1단계).

Runs the offline pipeline end to end and writes the three assets the rest of
the system reads::

    glossary.json             official terms + statistically extracted terms
    patterns.json             recurring EN->KO sentence templates
    conflicts.json            EN terms with more than one KO translation

Pipeline order is fixed by SERVICE.md §6 and the order matters:

1. Load the clean corpus (2012-2016, EN/KO text present, KO contains Hangul).
2. Filter untranslated records **before** any statistics.  Without this step
   54% of subtype terms end up in the conflict list instead of 15%, because
   untranslated English residue looks like a competing translation.
3. Load the official glossary (subtypes, card types, factions, cycles, sets)
   and exclude those ids from statistical extraction — they are already
   authoritative, so re-deriving them would only add noise.
4. Generate term candidates from corpus statistics alone.  Zero LLM calls.
5. Adjudicate candidates with an LLM (yes/no only).  Optional: see below.
6. Detect EN terms with multiple KO translations and hold them back.
7. Extract sentence templates.
8. Write the three assets.

On LLM adjudication
-------------------
Step 5 needs a chat model.  When no model is supplied the batch still runs and
still writes every asset, but ``glossary.json`` records
``"llm_judged": false`` and keeps the statistical candidates unadjudicated.
The batch never invents judgments it did not make: an unadjudicated glossary is
labelled as such so a downstream consumer can refuse it.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conflict_detector import detect_conflicts
from corpus_split import load_clean_corpus
from load_official_glossary import load_official_glossary
from pattern_extractor import extract_patterns, write_patterns_json
from term_candidate_extractor import generate_candidates

DEFAULT_MIN_COOCCUR = 5
DEFAULT_MIN_PATTERN_COUNT = 3


def _ko_translations_dir(corpus_root: Path) -> Path:
    return corpus_root / "v2" / "translations" / "ko"


def _candidate_to_term_pair(candidate: Any) -> dict:
    """Shape a TermCandidate for detect_conflicts()."""
    return {
        "en_term": candidate.en_term,
        "ko_term": candidate.ko_term,
        "card_id": None,
        "dice": candidate.dice,
        "pmi": candidate.pmi,
        "cooccurrence": candidate.cooccurrence,
    }


def build_assets(
    corpus_root: Path | str,
    out_dir: Path | str = ".",
    *,
    llm: Any | None = None,
    min_cooccur: int = DEFAULT_MIN_COOCCUR,
    min_pattern_count: int = DEFAULT_MIN_PATTERN_COUNT,
) -> dict:
    """Run the phase-1 asset build and write glossary/patterns/conflicts JSON.

    Args:
        corpus_root:       netrunner-cards-json checkout root.
        out_dir:           directory the three JSON assets are written to.
        llm:               optional LangChain chat model for step 5.  When None,
                           candidates are written unadjudicated and the glossary
                           is flagged ``llm_judged: false``.
        min_cooccur:       minimum cooccurrence for a term candidate.
        min_pattern_count: minimum occurrences for a sentence template.

    Returns:
        A summary dict with the counts at each stage and the written paths.
    """
    corpus_root = Path(corpus_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1-2. Clean corpus.  load_clean_corpus() already drops records that are
    # missing text or carry no Hangul, which is the untranslated-record filter.
    corpus = load_clean_corpus(corpus_root)

    # 3. Official glossary — authoritative, excluded from extraction.
    official = load_official_glossary(_ko_translations_dir(corpus_root))

    # 4. Statistical candidates, zero LLM calls.
    candidates = generate_candidates(corpus, min_cooccur=min_cooccur)
    candidates = [
        c for c in candidates if c.en_term.lower() not in official.excluded_ids
    ]

    # 5. LLM adjudication, only when a model was supplied.
    llm_judged = False
    if llm is not None:
        from term_judge import judge_candidates

        accepted_judgments, _rejected = judge_candidates(candidates, llm)
        accepted = {(j.en_term, j.ko_term) for j in accepted_judgments if j.accepted}
        candidates = [c for c in candidates if (c.en_term, c.ko_term) in accepted]
        llm_judged = True

    # 6. Conflicts are held back from the glossary until a human resolves them.
    conflict_result = detect_conflicts([_candidate_to_term_pair(c) for c in candidates])

    # 7. Sentence templates.
    patterns = extract_patterns(corpus, min_count=min_pattern_count)

    # 8. Write assets.
    glossary_path = out_dir / "glossary.json"
    patterns_path = out_dir / "patterns.json"
    conflicts_path = out_dir / "conflicts.json"

    glossary_payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus_root": str(corpus_root),
        "llm_judged": llm_judged,
        "official": official.all_terms(),
        "extracted": conflict_result.clean,
        "counts": {
            "official": len(official),
            "extracted": len(conflict_result.clean),
            "conflicts_held_back": len(conflict_result.conflicts),
        },
    }
    glossary_path.write_text(
        json.dumps(glossary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_patterns_json(patterns, patterns_path)
    conflict_result.write_conflicts_json(conflicts_path)

    return {
        "corpus_cards": len(corpus),
        "official_terms": len(official),
        "candidates": len(candidates),
        "glossary_terms": len(conflict_result.clean),
        "conflicts": len(conflict_result.conflicts),
        "patterns": len(patterns),
        "llm_judged": llm_judged,
        "paths": {
            "glossary": str(glossary_path),
            "patterns": str(patterns_path),
            "conflicts": str(conflicts_path),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase-1 asset build batch")
    parser.add_argument(
        "--corpus-root",
        default=os.environ.get("CORPUS_ROOT"),
        help="netrunner-cards-json checkout root (default: $CORPUS_ROOT)",
    )
    parser.add_argument("--out-dir", default="assets", help="output directory")
    parser.add_argument(
        "--min-cooccur", type=int, default=DEFAULT_MIN_COOCCUR
    )
    parser.add_argument(
        "--min-pattern-count", type=int, default=DEFAULT_MIN_PATTERN_COUNT
    )
    args = parser.parse_args()

    if not args.corpus_root:
        raise SystemExit("CORPUS_ROOT is not set and --corpus-root was not given")

    summary = build_assets(
        args.corpus_root,
        args.out_dir,
        min_cooccur=args.min_cooccur,
        min_pattern_count=args.min_pattern_count,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["llm_judged"]:
        print(
            "\nNOTE: no LLM was supplied, so statistical candidates were written "
            "unadjudicated and glossary.json records llm_judged=false."
        )


if __name__ == "__main__":
    main()
