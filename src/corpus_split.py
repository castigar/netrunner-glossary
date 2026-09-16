"""corpus_split.py — clean corpus loader and deterministic train/hold-out split.

Clean corpus definition (SERVICE.md §5):
  - Released 2012-2016 (inclusive)
  - EN card has a non-empty `text` field
  - KO translation file exists AND its `text` field is non-empty AND contains at least one Hangul character
  - Expected size: ~980 cards

Split (seed 42):
  - Shuffle the sorted card IDs with random.Random(42)
  - hold_out: first 100
  - train:    remaining 880
  - Hold-out is NEVER used as an asset-building input.
"""
from __future__ import annotations

import json
import os
import random
import unicodedata
from pathlib import Path


def _has_hangul(text: str) -> bool:
    """Return True if *text* contains at least one Hangul syllable/jamo character."""
    return any(unicodedata.name(c, "").startswith("HANGUL") for c in text)


def _corpus_root() -> Path:
    env = os.environ.get("CORPUS_ROOT")
    if not env:
        raise RuntimeError(
            "CORPUS_ROOT environment variable is not set. "
            "Point it to the netrunner-cards-json checkout root."
        )
    p = Path(env)
    if not p.is_dir():
        raise RuntimeError(f"CORPUS_ROOT does not exist: {p}")
    return p


def _build_card_earliest_dates(root: Path) -> dict[str, str]:
    """Return {card_id: earliest release date string} using v2 printings + card_sets."""
    sets_path = root / "v2" / "card_sets.json"
    with sets_path.open(encoding="utf-8") as f:
        card_sets = json.load(f)
    set_dates: dict[str, str] = {s["id"]: s.get("date_release", "") for s in card_sets}

    printings_dir = root / "v2" / "printings"
    card_earliest: dict[str, str] = {}
    for fname in printings_dir.iterdir():
        if not fname.suffix == ".json":
            continue
        with fname.open(encoding="utf-8") as f:
            printings = json.load(f)
        for p in printings:
            cid = p.get("card_id", "")
            sid = p.get("card_set_id", "")
            date = set_dates.get(sid, "")
            if date and cid:
                prev = card_earliest.get(cid, "")
                if not prev or date < prev:
                    card_earliest[cid] = date
    return card_earliest


def load_clean_corpus(root: Path | None = None) -> list[dict]:
    """Return sorted list of EN/KO card pairs matching the clean corpus criteria.

    Each entry::

        {
          "id":       str,                # card slug, e.g. "15_minutes"
          "en_text":  str,                # EN rules text (text field)
          "ko_text":  str,                # KO rules text (text field)
          "date":     str,                # YYYY-MM-DD earliest release
        }

    The list is sorted by (date, id) for reproducibility before any shuffle.
    """
    if root is None:
        root = _corpus_root()

    cards_dir = root / "v2" / "cards"
    ko_dir = root / "v2" / "translations" / "ko" / "cards"

    card_earliest = _build_card_earliest_dates(root)

    # Load KO translations into a lookup map
    ko_lookup: dict[str, dict] = {}
    for fname in ko_dir.iterdir():
        if fname.suffix != ".json":
            continue
        cid = fname.stem
        with fname.open(encoding="utf-8") as f:
            ko_lookup[cid] = json.load(f)

    clean: list[dict] = []
    for fname in cards_dir.iterdir():
        if fname.suffix != ".json":
            continue
        cid = fname.stem
        date = card_earliest.get(cid, "")
        if not date:
            continue
        year_str = date[:4]
        if not year_str.isdigit():
            continue
        year = int(year_str)
        if not (2012 <= year <= 2016):
            continue

        with fname.open(encoding="utf-8") as f:
            en_card = json.load(f)
        en_text = en_card.get("text") or ""
        if not en_text:
            continue

        ko_card = ko_lookup.get(cid)
        if ko_card is None:
            continue
        ko_text = ko_card.get("text") or ""
        if not ko_text:
            continue
        if not _has_hangul(ko_text):
            continue

        clean.append({"id": cid, "en_text": en_text, "ko_text": ko_text, "date": date})

    clean.sort(key=lambda c: (c["date"], c["id"]))
    return clean


def split_corpus(
    cards: list[dict],
    seed: int = 42,
    holdout_size: int = 100,
) -> tuple[list[dict], list[dict]]:
    """Shuffle *cards* with *seed*, then split into (hold_out, train).

    Returns (hold_out, train) where len(hold_out) == holdout_size.
    """
    rng = random.Random(seed)
    shuffled = cards[:]
    rng.shuffle(shuffled)
    return shuffled[:holdout_size], shuffled[holdout_size:]


def build_split(
    corpus_root: Path | None = None,
    seed: int = 42,
    holdout_size: int = 100,
) -> tuple[list[dict], list[dict]]:
    """Load clean corpus, shuffle, and return (hold_out, train)."""
    cards = load_clean_corpus(corpus_root)
    return split_corpus(cards, seed=seed, holdout_size=holdout_size)


def save_split(
    out_dir: Path,
    hold_out: list[dict],
    train: list[dict],
) -> None:
    """Persist hold_out.json and train.json under *out_dir*."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "hold_out.json").open("w", encoding="utf-8") as f:
        json.dump(hold_out, f, ensure_ascii=False, indent=2)
    with (out_dir / "train.json").open("w", encoding="utf-8") as f:
        json.dump(train, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    import sys

    root = Path(os.environ.get("CORPUS_ROOT", ""))
    out_dir = Path(os.environ.get("DATA_DIR", "data"))
    hold_out, train = build_split(root if root.is_dir() else None)
    total = len(hold_out) + len(train)
    save_split(out_dir, hold_out, train)
    print(
        f"Split complete: total={total} hold_out={len(hold_out)} train={len(train)}",
        file=sys.stderr,
    )
    print(f"Saved to {out_dir}/hold_out.json and {out_dir}/train.json", file=sys.stderr)
