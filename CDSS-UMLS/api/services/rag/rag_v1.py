"""RAG v1: BM25 + Simple Embeddings."""

import logging

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from api.config import settings
from api.services.rag.base_rag import BaseRAGService

logger = logging.getLogger(__name__)


class RAGServiceV1(BaseRAGService):
    """RAG implementation using BM25 and simple embeddings."""

    def __init__(self):
        self.bm25 = None
        self.embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
        self.documents = []
        self.document_embeddings = None

    async def retrieve(
        self, query: str, top_k: int = 5, **kwargs
    ) -> tuple[list[dict[str, str]], list[str]]:
        """Retrieve relevant documents using BM25 and embeddings."""
        if not self.documents:
            logger.warning("No documents indexed. Returning empty results.")
            return [], []

        # BM25 retrieval
        tokenized_query = query.lower().split()
        bm25_scores = self.bm25.get_scores(tokenized_query)

        # Embedding-based retrieval
        query_embedding = self.embedding_model.encode([query])[0]
        if self.document_embeddings is not None:
            similarities = np.dot(self.document_embeddings, query_embedding)
        else:
            similarities = np.zeros(len(self.documents))

        # Combine scores (weighted)
        combined_scores = 0.6 * bm25_scores + 0.4 * similarities

        # Get top k
        top_indices = np.argsort(combined_scores)[-top_k:][::-1]

        results = []
        umls_concepts = []

        for idx in top_indices:
            doc = self.documents[idx]
            results.append(
                {
                    "text": doc.get("text", ""),
                    "source": doc.get("source", ""),
                    "score": float(combined_scores[idx]),
                }
            )
            if "umls_concepts" in doc:
                umls_concepts.extend(doc["umls_concepts"])

        return results, list(set(umls_concepts))

    async def index_documents(self, documents: list[dict[str, str]]):
        """Index documents using BM25 and embeddings."""
        self.documents = documents

        # Tokenize for BM25
        tokenized_docs = [doc.get("text", "").lower().split() for doc in documents]
        self.bm25 = BM25Okapi(tokenized_docs)

        # Create embeddings
        texts = [doc.get("text", "") for doc in documents]
        self.document_embeddings = self.embedding_model.encode(texts)

        logger.info(f"Indexed {len(documents)} documents")
