"""EN rule-text term frequency aid sheet for manual gold-set labelling.

Derived from the corpus ONLY. It never reads glossary.json, judgments.jsonl or
any other extractor output, so consulting it cannot make the gold set agree
with the system by construction.

KO is deliberately absent. The labeller supplies every KO form from their own
knowledge; the sheet only suggests WHICH EN terms are worth labelling.

Candidate filter is purely linguistic: an n-gram is dropped when its first or
last token is a function word or a bare number, which is the standard keyphrase
boundary rule. It removes 'of', 'piece of', 'is not trashed' while keeping
'trash', 'end the run', '[credit]'.
"""
import sys
from collections import Counter
from pathlib import Path

W = Path(r"C:/Users/SDS/.ouroboros/worktrees/mini_pjt/orch_a6bc6099bdae")
sys.path.insert(0, str(W / "src"))

from corpus_split import load_clean_corpus
from term_candidate_extractor import _extract_en_ngrams

FUNCTION_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "it", "its",
    "of", "to", "in", "on", "for", "from", "at", "by", "with", "as", "into",
    "and", "or", "if", "when", "whenever", "then", "than", "but", "not",
    "is", "are", "was", "be", "been", "do", "does", "did", "has", "have", "had",
    "you", "your", "he", "she", "they", "their", "his", "her",
    "may", "can", "must", "will", "would", "any", "all", "each", "other",
    "another", "up", "out", "off", "only", "also", "more", "most", "one",
    "there", "here", "who", "which", "what", "how", "so", "no", "nor",
}

MIN_COOCCUR = 5      # term_candidate_extractor.generate_candidates default
MAX_EN_TERMS = 1500  # DEFAULT_MAX_EN_TERMS


def is_candidate(term: str) -> bool:
    tokens = term.split()
    for edge in (tokens[0], tokens[-1]):
        if edge in FUNCTION_WORDS or edge.isdigit():
            return False
    return True


cards = load_clean_corpus()
df = Counter()
example = {}
for card in cards:
    for ngram in set(_extract_en_ngrams(card["en_text"])):
        df[ngram] += 1
        example.setdefault(ngram, card["id"])

universe = [(t, n) for t, n in df.most_common() if n >= MIN_COOCCUR]
in_cap = {t for t, _ in universe[:MAX_EN_TERMS]}
filtered = [(t, n) for t, n in universe if is_candidate(t)]

print(f"cards={len(cards)}  universe(DF>=5)={len(universe)}  "
      f"after boundary filter={len(filtered)}", file=sys.stderr)
print(f"DF at cap boundary (rank {MAX_EN_TERMS}) = {universe[MAX_EN_TERMS - 1][1]}",
      file=sys.stderr)

rows = ["df\tin_cap\ten_term\texample_card"]
for term, n in filtered[:500]:
    rows.append(f"{n}\t{'yes' if term in in_cap else 'NO'}\t{term}\t{example[term]}")
Path(sys.argv[1]).write_text("\n".join(rows) + "\n", encoding="utf-8")
print(f"wrote {len(rows) - 1} rows", file=sys.stderr)
