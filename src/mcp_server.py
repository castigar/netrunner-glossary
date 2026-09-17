"""mcp_server.py — Netrunner translation assistant MCP server.

Exposes four tools (SERVICE.md §3):
  - lookup_term(en)            EN term → registered KO translation
  - search_tm(text)            EN text → similar past card EN/KO pairs
  - lookup_pattern(text)       EN rule sentence → matching KO template
  - check_translation(en, ko)  EN/KO pair → combined violation report

Run::

    python -m mcp_server   # stdio MCP mode (clients spawn this as subprocess)

Environment variables consumed:
  CORPUS_ROOT    Path to netrunner-cards-json checkout; required for search_tm.
  GLOSSARY_PATH  Override path to assets/glossary.json (default: assets/ sibling).
  PATTERNS_PATH  Override path to assets/patterns.json (default: assets/ sibling).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from fastmcp import FastMCP

from fidelity_guard import check_fidelity
from glossary_guard import GlossaryFlat, check_glossary_compliance, load_flat_glossary
from injection_guard import check_injection
from pattern_extractor import PatternEntry
from pattern_extractor import lookup_pattern as _lookup_pattern_entry

mcp = FastMCP("netrunner-translation-assistant")

_ASSETS_DIR = Path(__file__).parent.parent / "assets"


# ---------------------------------------------------------------------------
# Asset loading helpers (path-keyed LRU cache → one load per unique path)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _load_glossary(path: str) -> tuple[GlossaryFlat, bool]:
    return load_flat_glossary(path)


@lru_cache(maxsize=4)
def _load_patterns(path: str) -> tuple[PatternEntry, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(PatternEntry(**item) for item in data)


def _glossary_path() -> str:
    return os.environ.get("GLOSSARY_PATH", str(_ASSETS_DIR / "glossary.json"))


def _patterns_path() -> str:
    return os.environ.get("PATTERNS_PATH", str(_ASSETS_DIR / "patterns.json"))


# ---------------------------------------------------------------------------
# TM index — lazy, built from corpus on first search_tm call.
# Tests may inject a pre-built index by setting _TM_INDEX and _TM_INDEX_LOADED.
# ---------------------------------------------------------------------------

_TM_INDEX = None
_TM_INDEX_LOADED: bool = False


def _build_tm_index():
    """Build TM index from the corpus. Returns None when CORPUS_ROOT is not set."""
    corpus_root = os.environ.get("CORPUS_ROOT", "")
    if not corpus_root:
        return None
    from corpus_split import load_clean_corpus
    from tm_index import HybridTMIndex

    try:
        cards = load_clean_corpus(Path(corpus_root))
    except Exception:
        return None
    if not cards:
        return None
    _, train = _split_train(cards)
    idx = HybridTMIndex()
    idx.build(train)
    return idx


def _split_train(cards: list[dict]) -> tuple[list[dict], list[dict]]:
    from corpus_split import split_corpus
    return split_corpus(cards)


def _get_tm_index():
    global _TM_INDEX, _TM_INDEX_LOADED
    if not _TM_INDEX_LOADED:
        _TM_INDEX = _build_tm_index()
        _TM_INDEX_LOADED = True
    return _TM_INDEX


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@mcp.tool()
def lookup_term(en: str) -> str:
    """Look up the registered KO translation for an EN game term.

    Returns the official KO equivalent and which glossary section it came from.
    When ``llm_judged`` is ``false``, the ``extracted`` section has not been
    LLM-validated; callers should treat those translations with appropriate caution.

    Args:
        en: EN term to look up (e.g. ``install``, ``trash``, ``barrier``).
            Case-insensitive; underscores are treated as spaces.
    """
    flat, llm_judged = _load_glossary(_glossary_path())
    en_norm = en.replace("_", " ").strip().lower()
    if en_norm in flat:
        ko_term, source = flat[en_norm]
        return json.dumps(
            {
                "found": True,
                "en": en,
                "ko": ko_term,
                "source": source,
                "llm_judged": llm_judged,
            },
            ensure_ascii=False,
        )
    return json.dumps({"found": False, "en": en}, ensure_ascii=False)


@mcp.tool()
def search_tm(text: str) -> str:
    """Search translation memory for similar past card EN/KO pairs.

    Uses hybrid BM25 + dense + character-ngram retrieval with RRF fusion
    (SERVICE.md §3, §6). Returns up to 5 results sorted by relevance.

    Requires the ``CORPUS_ROOT`` environment variable to be set to the
    netrunner-cards-json checkout root. Returns an error JSON when unavailable.

    Args:
        text: EN card text to search for (rules or flavour sentence).
    """
    idx = _get_tm_index()
    if idx is None:
        return json.dumps(
            {
                "error": "TM index unavailable",
                "reason": "CORPUS_ROOT is not set or corpus could not be loaded",
            },
            ensure_ascii=False,
        )
    results = idx.search(text, k=5)
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
def lookup_pattern(text: str) -> str:
    """Look up the KO sentence template for an EN rule sentence.

    Abstracts numbers and game symbols, then matches against the pattern table
    built from the clean corpus (SERVICE.md §6). Returns the most frequent
    matching template, or ``{"found": false}`` when nothing matches.

    Args:
        text: One EN rules sentence (e.g. ``Trash 1 installed program.``).
    """
    entries = list(_load_patterns(_patterns_path()))
    match = _lookup_pattern_entry(entries, text)
    if match is None:
        return json.dumps({"found": False, "text": text}, ensure_ascii=False)
    return json.dumps({"found": True, **match.to_dict()}, ensure_ascii=False)


@mcp.tool()
def check_translation(en: str, ko: str) -> str:
    """Check an EN/KO card text pair for translation violations.

    Runs three guardrails defined in SERVICE.md §4:

    - **injection** — card text must not contain prompt injection patterns.
    - **glossary** — registered EN terms must use their registered KO equivalents.
    - **fidelity** — numbers, game symbols, and conditional structures must match.

    ``passed`` is ``true`` only when all three guardrails pass.

    Args:
        en: Source English card text (``text`` / rule field).
        ko: Draft Korean translation to validate.
    """
    flat, llm_judged = _load_glossary(_glossary_path())

    inj_result = check_injection(en)
    glossary_result = check_glossary_compliance(en, ko, flat, llm_judged)
    fidelity_result = check_fidelity(en, ko)

    all_passed = inj_result.passed and glossary_result.passed and fidelity_result.passed
    return json.dumps(
        {
            "passed": all_passed,
            "injection": inj_result.model_dump(),
            "glossary": glossary_result.model_dump(),
            "fidelity": fidelity_result.model_dump(),
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
