"""tm_index.py — Hybrid Translation Memory index (BM25 + dense).

SERVICE.md §6: Chroma TM 인덱싱 (BM25 + dense) [패턴 3].
Card text is short and contains many proper nouns, so exact-match
(BM25) contribution is significant.  Dense bag-of-words vectors
capture term-overlap similarity independent of document frequency.
The two ranking lists are merged via Reciprocal Rank Fusion (RRF).
"""
from __future__ import annotations

import re
from collections import Counter

import numpy as np
from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    """Lower-case word tokenizer; keeps game symbols like [credit] intact."""
    return re.findall(r"\[[^\]]+\]|\w+", text.lower())


def _build_bow_vector(tokens: list[str], vocab: dict[str, int], dim: int) -> np.ndarray:
    """L2-normalised bag-of-words frequency vector over *vocab*."""
    vec = np.zeros(dim, dtype=np.float32)
    for tok in tokens:
        if tok in vocab:
            vec[vocab[tok]] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0.0:
        vec /= norm
    return vec


def _rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion.

    Each element of *rankings* is a list of document indices sorted by
    decreasing relevance for one retrieval signal.  Returns a list of
    (doc_index, rrf_score) pairs sorted by decreasing fused score.
    """
    scores: dict[int, float] = Counter()
    for ranking in rankings:
        for rank, doc_idx in enumerate(ranking):
            scores[doc_idx] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class HybridTMIndex:
    """In-memory hybrid Translation Memory index (BM25 + dense BoW).

    Build from a list of card records, then search with a text query.
    Both BM25 (exact match) and dense cosine similarity contribute to
    the final ranking via RRF, as required by SERVICE.md §6.

    Usage::

        index = HybridTMIndex()
        index.build(train_records)
        results = index.search("install a program on a server", k=5)
    """

    def __init__(self) -> None:
        self._records: list[dict] = []
        self._bm25: BM25Okapi | None = None
        self._dense_matrix: np.ndarray | None = None
        self._vocab: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, records: list[dict]) -> None:
        """Index *records* for hybrid retrieval.

        Args:
            records: list of dicts, each with at minimum:
                     "id" (str), "en_text" (str), "ko_text" (str).

        Raises:
            ValueError: if *records* is empty.
        """
        if not records:
            raise ValueError("Cannot build index from empty records list")

        self._records = list(records)
        tokenised = [_tokenize(r["en_text"]) for r in self._records]

        # BM25
        self._bm25 = BM25Okapi(tokenised)

        # Dense vocabulary over all tokens
        all_tokens: set[str] = set()
        for toks in tokenised:
            all_tokens.update(toks)
        self._vocab = {tok: i for i, tok in enumerate(sorted(all_tokens))}
        dim = len(self._vocab)

        # Dense document matrix: shape (n_docs, vocab_size)
        self._dense_matrix = np.stack(
            [_build_bow_vector(toks, self._vocab, dim) for toks in tokenised]
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Return the top-*k* most relevant records for *query*.

        Both BM25 and dense cosine scores produce independent rankings,
        which are fused via RRF.  Each result dict contains:
            "id"         str   — card slug
            "en_text"    str   — EN rules text
            "ko_text"    str   — KO rules text
            "score"      float — RRF fused score (higher = more relevant)
            "bm25_rank"  int   — 0-based rank in BM25-only ordering
            "dense_rank" int   — 0-based rank in dense-only ordering

        Raises:
            RuntimeError: if called before build().
        """
        if self._bm25 is None or self._dense_matrix is None:
            raise RuntimeError("Index not built; call build() first")

        n = len(self._records)
        top_k = min(k, n)
        q_tokens = _tokenize(query)

        # --- BM25 ranking ---
        bm25_scores = self._bm25.get_scores(q_tokens)
        bm25_ranking = sorted(range(n), key=lambda i: bm25_scores[i], reverse=True)

        # --- Dense ranking ---
        dim = len(self._vocab)
        q_vec = _build_bow_vector(q_tokens, self._vocab, dim)
        if np.linalg.norm(q_vec) == 0.0:
            dense_scores = np.zeros(n, dtype=np.float32)
        else:
            dense_scores = self._dense_matrix @ q_vec
        dense_ranking = sorted(range(n), key=lambda i: dense_scores[i], reverse=True)

        # --- RRF fusion ---
        fused = _rrf([bm25_ranking, dense_ranking])

        # Rank lookup for metadata
        bm25_rank_of = {idx: rank for rank, idx in enumerate(bm25_ranking)}
        dense_rank_of = {idx: rank for rank, idx in enumerate(dense_ranking)}

        results: list[dict] = []
        for doc_idx, rrf_score in fused[:top_k]:
            rec = self._records[doc_idx]
            results.append(
                {
                    "id": rec["id"],
                    "en_text": rec["en_text"],
                    "ko_text": rec["ko_text"],
                    "score": rrf_score,
                    "bm25_rank": bm25_rank_of[doc_idx],
                    "dense_rank": dense_rank_of[doc_idx],
                }
            )
        return results

    def __len__(self) -> int:
        return len(self._records)
