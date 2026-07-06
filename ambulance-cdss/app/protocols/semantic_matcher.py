"""app/protocols/semantic_matcher.py

Semantic protocol matcher using TF-IDF similarity.
Falls back gracefully when sklearn is unavailable."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_tfidf_vectorizer = None
_tfidf_matrix = None
_protocol_ids: list[str] = []

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False
    logger.info(
        "sklearn not available — semantic matching disabled, "
        "using trigger-word matching only"
    )


def build_index(protocols: list[dict]) -> None:
    """Build TF-IDF index from protocol trigger words and descriptions."""
    global _tfidf_vectorizer, _tfidf_matrix, _protocol_ids
    if not _HAS_SKLEARN or not protocols:
        return

    corpus: list[str] = []
    _protocol_ids = []
    for p in protocols:
        triggers = " ".join(p.get("triggers", []))
        desc = p.get("description", "")
        corpus.append(f"{triggers} {desc}")
        _protocol_ids.append(p["protocol_id"])

    if not corpus:
        return

    _tfidf_vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    _tfidf_matrix = _tfidf_vectorizer.fit_transform(corpus)
    logger.info("Semantic matcher index built: %d protocols", len(corpus))


def find_best_match(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Find most similar protocols to query using cosine similarity."""
    if not _HAS_SKLEARN or _tfidf_vectorizer is None or _tfidf_matrix is None:
        return []
    try:
        query_vec = _tfidf_vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, _tfidf_matrix).flatten()
        top_indices = similarities.argsort()[::-1][:top_k]
        results: list[dict[str, Any]] = []
        for idx in top_indices:
            if similarities[idx] > 0.05:  # minimum threshold
                results.append({
                    "protocol_id": _protocol_ids[idx],
                    "similarity": float(similarities[idx]),
                })
        return results
    except Exception as exc:
        logger.warning("Semantic matching failed: %s", exc)
        return []


def is_available() -> bool:
    """Whether sklearn is installed and the index has been built."""
    return _HAS_SKLEARN and _tfidf_matrix is not None
