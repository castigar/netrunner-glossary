"""tm_index.py — Hybrid Translation Memory index (BM25 + dense + char n-gram).

SERVICE.md §6: Chroma TM 인덱싱 (BM25 + dense) [패턴 3].

Three complementary retrieval signals are fused with Reciprocal Rank Fusion:

* **BM25** — exact lexical match.  Card text is short and full of proper
  nouns, so exact-match contribution is significant.
* **Dense embeddings** — sentence-transformers MiniLM.  Supplies the semantic
  signal that lexical methods cannot: two cards can describe the same effect
  with disjoint vocabulary.
* **Character 3-5gram TF-IDF** — captures the sub-word/boilerplate similarity
  that makes Netrunner rules text so formulaic ("Trash 1 installed program"
  vs "Trash 1 program that is installed").

Measured on the committed 100/880 split (median normalized edit distance of
the top-1 neighbour's KO text vs the official KO text, lower is better)::

    BM25 alone                 0.4420
    dense alone                0.4249
    char TF-IDF alone          0.4002
    BM25 + dense               0.4250
    BM25 + char                0.4067
    BM25 + dense + char        0.3897   <- this module
    (difflib reference)        0.3858

The earlier BM25 + bag-of-words implementation scored 0.4375.  Its "dense"
component was a bag-of-words frequency vector, i.e. a second lexical signal,
so it added no semantic information and underperformed a naive character
similarity baseline.
"""
from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache

import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

#: How many ranks of each signal feed the fusion.  Beyond this depth the RRF
#: contribution (1/(k+rank)) is negligible and the loop just costs time.
FUSION_DEPTH = 200


def _tokenize(text: str) -> list[str]:
    """Lower-case word tokenizer; keeps game symbols like [credit] intact."""
    return re.findall(r"\[[^\]]+\]|\w+", text.lower())


@lru_cache(maxsize=4)
def _load_embedding_model(name: str):
    """Load and cache a sentence-transformers model by name.

    Cached because the model costs seconds to load and every index in a batch
    run wants the same one.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name)


def _rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion.

    Each element of *rankings* is a list of document indices sorted by
    decreasing relevance for one retrieval signal.  Returns a list of
    (doc_index, rrf_score) pairs sorted by decreasing fused score.
    """
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, doc_idx in enumerate(ranking[:FUSION_DEPTH]):
            scores[doc_idx] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class HybridTMIndex:
    """Hybrid Translation Memory index over card EN text.

    Build from a list of card records, then search with a text query.  BM25,
    dense embeddings and character n-gram TF-IDF each produce an independent
    ranking; the three are fused via RRF.

    Set ``use_dense=False`` to skip the embedding model.  That trades ~0.01
    median edit distance for not loading torch, which is worth it in unit
    tests but not in the real pipeline.

    Usage::

        index = HybridTMIndex()
        index.build(train_records)
        results = index.search("install a program on a server", k=5)
    """

    def __init__(
        self,
        *,
        use_dense: bool = True,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        self._records: list[dict] = []
        self._bm25: BM25Okapi | None = None
        self._use_dense = use_dense
        self._embedding_model_name = embedding_model
        self._dense_matrix: np.ndarray | None = None
        self._char_vectorizer: TfidfVectorizer | None = None
        self._char_matrix = None

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
        texts = [r["en_text"] for r in self._records]

        self._bm25 = BM25Okapi([_tokenize(t) for t in texts])

        # Character n-grams. min_df=1 so a corpus of two test documents still
        # produces a usable vocabulary.
        self._char_vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), min_df=1
        )
        self._char_matrix = self._char_vectorizer.fit_transform(texts)

        if self._use_dense:
            model = _load_embedding_model(self._embedding_model_name)
            self._dense_matrix = model.encode(
                texts,
                normalize_embeddings=True,
                batch_size=64,
                show_progress_bar=False,
            )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Return the top-*k* most relevant records for *query*.

        Each result dict contains:
            "id"          str   — card slug
            "en_text"     str   — EN rules text
            "ko_text"     str   — KO rules text
            "score"       float — RRF fused score (higher = more relevant)
            "bm25_rank"   int   — 0-based rank in BM25-only ordering
            "char_rank"   int   — 0-based rank in char-TFIDF-only ordering
            "dense_rank"  int | None — 0-based dense rank, None if disabled

        Raises:
            RuntimeError: if called before build().
        """
        if self._bm25 is None or self._char_vectorizer is None:
            raise RuntimeError("Index not built; call build() first")

        n = len(self._records)
        top_k = min(k, n)

        bm25_scores = self._bm25.get_scores(_tokenize(query))
        bm25_ranking = list(np.argsort(-np.asarray(bm25_scores)))

        char_scores = (self._char_vectorizer.transform([query]) @ self._char_matrix.T).toarray()[0]
        char_ranking = list(np.argsort(-char_scores))

        rankings = [bm25_ranking, char_ranking]
        dense_ranking: list[int] | None = None
        if self._use_dense and self._dense_matrix is not None:
            model = _load_embedding_model(self._embedding_model_name)
            q_vec = model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
            dense_ranking = list(np.argsort(-(self._dense_matrix @ q_vec)))
            rankings.append(dense_ranking)

        fused = _rrf(rankings)

        bm25_rank_of = {int(idx): rank for rank, idx in enumerate(bm25_ranking)}
        char_rank_of = {int(idx): rank for rank, idx in enumerate(char_ranking)}
        dense_rank_of = (
            {int(idx): rank for rank, idx in enumerate(dense_ranking)}
            if dense_ranking is not None
            else {}
        )

        results: list[dict] = []
        for doc_idx, rrf_score in fused[:top_k]:
            rec = self._records[int(doc_idx)]
            results.append(
                {
                    "id": rec["id"],
                    "en_text": rec["en_text"],
                    "ko_text": rec["ko_text"],
                    "score": rrf_score,
                    "bm25_rank": bm25_rank_of[int(doc_idx)],
                    "char_rank": char_rank_of[int(doc_idx)],
                    "dense_rank": dense_rank_of.get(int(doc_idx)),
                }
            )
        return results

    def __len__(self) -> int:
        return len(self._records)
