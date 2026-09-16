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
4. Extract terms on **two separate paths** (SERVICE.md §6, 결정 ①):
   - 부제 경로: the ``keywords`` field, positionally aligned EN<->KO.  Candidates
     are whole field elements, so the 88-entry gold set scores it directly.
   - 룰 경로: the ``text`` field, n-gram statistics capped in two stages — the
     most frequent EN terms, then their best KO candidates by Dice.  Uncapped
     this yields ~75k candidates, at which scale LLM adjudication is
     meaningless before it is expensive.  A single global Dice cap was tried
     first and cut almost every real term; see term_candidate_extractor.
   Running both through one path was the original defect: noise n-grams
   collided with each other and buried the 68 real subtype terms under 1,822
   spurious conflicts.
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

Judgments are checkpointed to ``judgments.jsonl`` in the output directory as
each chunk completes, and progress is reported to stderr.  A killed run resumes
from the checkpoint rather than restarting, and a run that is merely slow is
distinguishable from one that is failing every call.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conflict_detector import detect_conflicts, write_conflicts_json
from corpus_split import load_clean_corpus
from load_official_glossary import load_official_glossary
from pattern_extractor import extract_patterns, write_patterns_json
from subtype_extractor import extract_subtype_pairs, to_term_pairs
from term_candidate_extractor import (
    DEFAULT_MAX_EN_TERMS,
    DEFAULT_TOP_K_PER_EN,
    generate_candidates,
)
from term_extraction_eval import evaluate_extraction, load_gold_subtypes
from term_judge import DEFAULT_CHUNK_SIZE, DEFAULT_MAX_CONCURRENCY

DEFAULT_MIN_COOCCUR = 5
DEFAULT_MIN_PATTERN_COUNT = 3


BEDROCK_JUDGE_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _ko_translations_dir(corpus_root: Path) -> Path:
    return corpus_root / "v2" / "translations" / "ko"


def _build_bedrock_llm(model_id: str) -> Any:
    """Build the Bedrock chat model used for step 5 accept/reject judgment.

    Haiku is the right tier here: step 5 is a few thousand independent yes/no
    classifications, not reasoning.  AWS credentials come from the environment.
    """
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(model=model_id, temperature=0)


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
    max_en_terms: int | None = DEFAULT_MAX_EN_TERMS,
    top_k_per_en: int | None = DEFAULT_TOP_K_PER_EN,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress: bool = False,
) -> dict:
    """Run the phase-1 asset build and write glossary/patterns/conflicts JSON.

    Args:
        corpus_root:       netrunner-cards-json checkout root.
        out_dir:           directory the three JSON assets are written to.
        llm:               optional LangChain chat model for step 5.  When None,
                           candidates are written unadjudicated and the glossary
                           is flagged ``llm_judged: false``.
        min_cooccur:       minimum cooccurrence for a rule-path term candidate.
        min_pattern_count: minimum occurrences for a sentence template.
        max_en_terms:      Rule-path cap, stage 1 — EN terms kept, by frequency.
        top_k_per_en:      Rule-path cap, stage 2 — KO candidates kept per EN
                           term, by Dice.
        max_concurrency:   Parallel in-flight LLM requests in step 5.  Ignored
                           when *llm* is None.
        chunk_size:        Candidates per checkpointed batch in step 5.
        progress:          When True, print step-5 chunk progress to stderr.

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
    # The 88-entry gold set is the KO subtype table: v2/card_subtypes.json lists
    # 106 ids, but 18 of them have no KO name and so cannot be scored.
    gold_subtypes = load_gold_subtypes(
        _ko_translations_dir(corpus_root) / "card_subtypes.json"
    )

    # 4a. 부제 경로 — keywords field, positionally aligned.  Zero LLM calls.
    subtype_result = extract_subtype_pairs(corpus)
    subtype_conflicts = detect_conflicts(
        to_term_pairs(subtype_result.pairs), source="subtype"
    )
    subtype_eval = evaluate_extraction(subtype_result.pairs, gold_subtypes)

    # 4b. 룰 경로 — text field n-grams, capped at top_n by Dice.  Zero LLM calls.
    candidates = generate_candidates(
        corpus,
        min_cooccur=min_cooccur,
        max_en_terms=max_en_terms,
        top_k_per_en=top_k_per_en,
    )
    candidates = [
        c for c in candidates if c.en_term.lower() not in official.excluded_ids
    ]

    # 5. LLM adjudication of the rule path, only when a model was supplied.
    llm_judged = False
    judged_counts: dict[str, int] = {}
    if llm is not None:
        from term_judge import judge_candidates

        submitted = len(candidates)
        started = time.monotonic()

        def report(stats: dict) -> None:
            elapsed = time.monotonic() - started
            done, total = stats["done"], stats["total"]
            rate = done / elapsed if elapsed > 0 else 0.0
            eta = (total - done) / rate / 60 if rate > 0 else float("inf")
            errors = stats["errors"]
            print(
                f"  judged {done}/{total}"
                f"  accepted={stats['accepted']}"
                f"  rejected={stats['rejected']}"
                f"  dropped={stats['dropped']}"
                f"  {rate:.2f}/s  eta {eta:.0f}m"
                + (f"  errors={errors}" if errors else ""),
                file=sys.stderr,
                flush=True,
            )

        accepted_judgments, rejected_judgments = judge_candidates(
            candidates,
            llm,
            max_concurrency=max_concurrency,
            chunk_size=chunk_size,
            checkpoint_path=out_dir / "judgments.jsonl",
            on_progress=report if progress else None,
        )
        accepted = {(j.en_term, j.ko_term) for j in accepted_judgments if j.accepted}
        candidates = [c for c in candidates if (c.en_term, c.ko_term) in accepted]
        judged_counts = {
            "submitted": submitted,
            "accepted": len(accepted_judgments),
            "rejected": len(rejected_judgments),
            # Candidates whose call still failed after retries.  Reported rather
            # than folded into "rejected": the model never judged them.
            "dropped": submitted - len(accepted_judgments) - len(rejected_judgments),
        }
        llm_judged = True

    # 6. Conflicts are held back from the glossary until a human resolves them.
    conflict_result = detect_conflicts(
        [_candidate_to_term_pair(c) for c in candidates], source="rule"
    )

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
        "judged_counts": judged_counts,
        "official": official.all_terms(),
        "subtype_extracted": subtype_conflicts.clean,
        "extracted": conflict_result.clean,
        "subtype_evaluation": {
            "precision": subtype_eval.precision,
            "recall": subtype_eval.recall,
            "f1": subtype_eval.f1,
            "tp": subtype_eval.tp,
            "fp": subtype_eval.fp,
            "fn": subtype_eval.fn,
            "gold_size": subtype_eval.gold_size,
            "extracted_size": subtype_eval.extracted_size,
        },
        "counts": {
            "official": len(official),
            "subtype_extracted": len(subtype_conflicts.clean),
            "extracted": len(conflict_result.clean),
            "subtype_conflicts_held_back": len(subtype_conflicts.conflicts),
            "conflicts_held_back": len(conflict_result.conflicts),
        },
    }
    glossary_path.write_text(
        json.dumps(glossary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_patterns_json(patterns, patterns_path)
    write_conflicts_json(
        subtype_conflicts.conflicts + conflict_result.conflicts, conflicts_path
    )

    return {
        "corpus_cards": len(corpus),
        "official_terms": len(official),
        "subtype_cards": subtype_result.cards_with_keywords,
        "subtype_cards_skipped": subtype_result.cards_skipped_length_mismatch,
        "subtype_terms": len(subtype_conflicts.clean),
        "subtype_conflicts": len(subtype_conflicts.conflicts),
        "subtype_precision": subtype_eval.precision,
        "subtype_recall": subtype_eval.recall,
        "candidates": len(candidates),
        "glossary_terms": len(conflict_result.clean),
        "conflicts": len(conflict_result.conflicts),
        "patterns": len(patterns),
        "llm_judged": llm_judged,
        "judged_counts": judged_counts,
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
    parser.add_argument(
        "--judge-model",
        nargs="?",
        const=BEDROCK_JUDGE_MODEL,
        default=None,
        help=(
            "run step 5 LLM adjudication on Bedrock; bare flag uses "
            f"{BEDROCK_JUDGE_MODEL}. Omitted, the glossary stays llm_judged=false."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=(
            "candidates per checkpointed batch for --judge-model. Judgments are "
            "appended to <out-dir>/judgments.jsonl after each chunk, so a killed "
            "run resumes instead of restarting."
        ),
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=DEFAULT_MAX_CONCURRENCY,
        help=(
            "parallel in-flight requests for --judge-model. Raise it only with "
            "evidence: a run reporting dropped=0 saw no throttling at its level."
        ),
    )
    parser.add_argument(
        "--max-en-terms",
        type=int,
        default=DEFAULT_MAX_EN_TERMS,
        help="rule-path cap stage 1: EN terms kept, by frequency; 0 removes it",
    )
    parser.add_argument(
        "--top-k-per-en",
        type=int,
        default=DEFAULT_TOP_K_PER_EN,
        help="rule-path cap stage 2: KO candidates per EN term, by Dice; 0 removes it",
    )
    args = parser.parse_args()

    if not args.corpus_root:
        raise SystemExit("CORPUS_ROOT is not set and --corpus-root was not given")

    llm = _build_bedrock_llm(args.judge_model) if args.judge_model else None

    summary = build_assets(
        args.corpus_root,
        args.out_dir,
        llm=llm,
        min_cooccur=args.min_cooccur,
        min_pattern_count=args.min_pattern_count,
        max_en_terms=args.max_en_terms or None,
        top_k_per_en=args.top_k_per_en or None,
        max_concurrency=args.max_concurrency,
        chunk_size=args.chunk_size,
        progress=bool(args.judge_model),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["llm_judged"]:
        print(
            "\nNOTE: no LLM was supplied, so statistical candidates were written "
            "unadjudicated and glossary.json records llm_judged=false."
        )


if __name__ == "__main__":
    main()
