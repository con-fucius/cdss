"""
Search tools for CDSS.

The runtime supports per-disease LanceDB tables and the legacy ``documents``
table so the system remains queryable during remediation.

Phase 0 fixes applied:
- HyDE endpoint is provider-aware via providers.py (no hardcoded Mistral URL)
- Vector search uses BGE instruction prefix for embedding only; raw query for FTS
- Cross-encoder scores are sigmoid-normalised before thresholding
- Cosine-distance scores normalised as 1.0 - distance (not 1/(1+d) which is L2)
- LanceDB sync calls in async tools wrapped in asyncio.to_thread
- Multi-table fan-out uses asyncio.gather for parallel search
- create_index called with explicit vector_column_name="vector"
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import lancedb
import pandas as pd

from .config import DISEASE_CONFIG
from .logs import log_retrieval
from .providers import (
    get_llm_provider,
    get_llm_model,
    provider_auth_header,
    provider_chat_endpoint,
    provider_has_credentials,
)
from .retry import async_retry

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = APP_DIR / "lancedb"
SEARCH_TASK_TIMEOUT_SECONDS = float(os.getenv("CDSS_SEARCH_TASK_TIMEOUT_SECONDS", "45"))
RERANK_STRATEGY = os.getenv("RERANK_STRATEGY", "cross-encoder").strip().lower()

# BGE instruction prefix — applied only to the query vector; never to FTS/BM25
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@dataclass
class RetrievedChunk:
    text: str
    parent_text: str
    section_title: str
    page: int
    score: float          # Always in [0, 1]; 1.0 = most similar
    disease: str
    chunk_id: str
    parent_id: str
    source_url: str
    guideline_name: str = ""
    low_confidence: bool = False


@dataclass
class KBResult:
    data: Dict[str, Any]
    text: str
    source: str
    disease: str
    table_type: str
    confidence: str


@dataclass
class PageIndexResult:
    disease: str
    page: int
    section_path: str
    summary: str
    text: str
    score: float


def _sigmoid(x: float) -> float:
    """Map cross-encoder logit to [0, 1]."""
    return 1.0 / (1.0 + math.exp(-x))


def _cosine_distance_to_similarity(distance: float) -> float:
    """
    LanceDB returns cosine *distance* in [0, 2] for unit vectors.
    BGE embeddings are L2-normalised so distance ∈ [0, 2].
    Similarity = 1 - distance/2  →  [0, 1].
    """
    return max(0.0, 1.0 - float(distance) / 2.0)


def _table_names(db: Any) -> List[str]:
    if hasattr(db, "table_names"):
        names = db.table_names()
        return list(names)
    tables = db.list_tables()
    if hasattr(tables, "tables"):
        return list(tables.tables)
    return list(tables)


class SearchIndex:
    _instance: Optional["SearchIndex"] = None

    def __new__(cls, db_path: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: Optional[str] = None):
        if self._initialized:
            return

        configured_path = (
            db_path or os.getenv("LANCEDB_PATH") or str(DEFAULT_DB_PATH)
        )
        self.db_path = Path(configured_path).resolve()
        self.db = lancedb.connect(str(self.db_path))
        self._embedding_model = None
        self._reranker = None
        self._initialized = True
        self._ensure_legacy_fts_index()
        logger.info("SearchIndex initialized at %s", self.db_path)

    # ------------------------------------------------------------------ #
    # Table helpers                                                         #
    # ------------------------------------------------------------------ #

    def table_names(self) -> List[str]:
        return _table_names(self.db)

    def guideline_tables(self) -> List[str]:
        return [n for n in self.table_names() if n.endswith("_guidelines")]

    def available_diseases(self) -> List[str]:
        diseases = [n.removesuffix("_guidelines") for n in self.guideline_tables()]
        if "documents" in self.table_names() and "hiv" not in diseases:
            diseases.append("hiv")
        return diseases

    def _ensure_legacy_fts_index(self) -> None:
        if "documents" not in self.table_names():
            return
        try:
            self.db.open_table("documents").create_fts_index("text", replace=False)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" not in msg and "exists" not in msg:
                logger.warning("Legacy FTS index check failed: %s", exc)

        if "pageindex_chunks" in self.table_names():
            try:
                self.db.open_table("pageindex_chunks").create_fts_index(
                    "summary",
                    replace=False,
                )
            except Exception as exc:
                msg = str(exc).lower()
                if "already exists" not in msg and "exists" not in msg:
                    logger.warning("PageIndex FTS index check failed: %s", exc)

    def _get_table_names(self, disease: Optional[str] = None) -> List[str]:
        all_tables = self.table_names()
        if disease:
            target = f"{disease.lower()}_guidelines"
            if target in all_tables:
                return [target]
            if disease.lower() == "hiv" and "documents" in all_tables:
                return ["documents"]
            return []
        tables = [n for n in all_tables if n.endswith("_guidelines")]
        if "documents" in all_tables:
            tables.append("documents")
        return tables

    # ------------------------------------------------------------------ #
    # Model accessors (lazy, cached)                                        #
    # ------------------------------------------------------------------ #

    def _get_embedding_model(self):
        if self._embedding_model is None:
            from fastembed import TextEmbedding
            self._embedding_model = TextEmbedding(model_name="BAAI/bge-base-en-v1.5")
        return self._embedding_model

    def _get_reranker(self):
        if self._reranker is None:
            from rerankers import Reranker
            self._reranker = Reranker(
                "BAAI/bge-reranker-base", model_type="cross-encoder"
            )
        return self._reranker

    # ------------------------------------------------------------------ #
    # HyDE — provider-aware, uses cheap model, non-fatal                   #
    # ------------------------------------------------------------------ #

    async def _generate_hyde_hypothesis(
        self, query: str, disease: Optional[str]
    ) -> str:
        """
        Generate a hypothetical guideline excerpt for better embedding alignment.
        Uses the configured provider's cheapest model.
        Falls back to the raw query on any error.
        """
        if not provider_has_credentials():
            return query

        disease_name = (
            DISEASE_CONFIG.get(disease.lower(), {}).get("display_name", "medical")
            if disease
            else "medical"
        )
        prompt = (
            f"Write a brief, factual clinical guideline excerpt that answers: '{query}' "
            f"in the context of {disease_name} in Kenya. "
            "Use the style of a national clinical guideline. Two to three sentences only."
        )

        provider = get_llm_provider()
        # Always use a cheap/fast model for HyDE — not the expensive reasoning model
        if provider not in {"groq", "puter"}:
            return query

        hyde_model = {
            "groq": "llama-3.1-8b-instant",
            "puter": "openai/gpt-4o-mini",
        }[provider]

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.post(
                    provider_chat_endpoint(provider),
                    headers={
                        **provider_auth_header(provider),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": hyde_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 150,
                        "temperature": 0.3,
                    },
                )
                if res.status_code == 200:
                    hypothesis = (
                        res.json()["choices"][0]["message"]["content"].strip()
                    )
                    return f"{query} {hypothesis}"
        except Exception as exc:
            logger.warning("HyDE generation failed (%s): %s", provider, exc)

        return query

    # ------------------------------------------------------------------ #
    # Row → chunk conversion                                               #
    # ------------------------------------------------------------------ #

    def _row_to_chunk(
        self, row: Any, score: float, table_name: str
    ) -> RetrievedChunk:
        LOW_CONFIDENCE_THRESHOLD = 0.45

        if table_name == "documents":
            source = str(
                row.get(
                    "source",
                    "Kenya HIV Prevention and Treatment Guidelines 2022",
                )
            )
            page = int(row.get("page") or 0)
            row_id = str(row.get("id") or f"legacy-{page}")
            return RetrievedChunk(
                text=str(row.get("text", "")),
                parent_text=str(row.get("text", "")),
                section_title=source,
                page=page,
                score=score,
                disease="hiv",
                chunk_id=row_id,
                parent_id=row_id,
                source_url=DISEASE_CONFIG["hiv"]["source_url"],
                guideline_name=DISEASE_CONFIG["hiv"]["guideline_name"],
                low_confidence=score < LOW_CONFIDENCE_THRESHOLD,
            )

        def value(key: str, default: Any = "") -> Any:
            return row.get(key, default)

        return RetrievedChunk(
            text=str(value("text")),
            parent_text=str(value("parent_text", value("text"))),
            section_title=str(value("section_title", "Guideline section")),
            page=int(value("page", 0) or 0),
            score=score,
            disease=str(value("disease", table_name.removesuffix("_guidelines"))),
            chunk_id=str(value("chunk_id")),
            parent_id=str(value("parent_id", value("chunk_id"))),
            source_url=str(value("source_url", "")),
            guideline_name=str(value("guideline_name", "")),
            low_confidence=score < LOW_CONFIDENCE_THRESHOLD,
        )

    # ------------------------------------------------------------------ #
    # Legacy helpers (documents table)                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def legacy_page_section_id(page: int) -> str:
        return f"legacy-page-{page}"

    @staticmethod
    def legacy_section_title(text: str, page: int, source: str) -> str:
        cleaned = []
        for raw in str(text or "").splitlines():
            line = re.sub(r"\s+", " ", raw).strip(" -\t")
            if not line:
                continue
            if line.lower().startswith(
                "kenya hiv prevention and treatment guidelines"
            ):
                continue
            if line == source:
                continue
            if re.fullmatch(r"\d+\s*-\s*\d+.*", line):
                continue
            cleaned.append(line)
        if not cleaned:
            return f"Page {page}"
        title = cleaned[0]
        if len(title) > 96:
            title = f"{title[:93].rstrip()}..."
        return f"Page {page}: {title}"

    def legacy_toc(self, limit: int = 1000) -> List[Dict[str, Any]]:
        if "documents" not in self.table_names():
            return []
        table = self.db.open_table("documents")
        df = table.search().limit(limit).to_pandas()
        if df.empty:
            return []
        toc = []
        for page, page_df in df.sort_values(["page", "id"]).groupby(
            "page", sort=True
        ):
            first = page_df.iloc[0]
            source = str(
                first.get("source", "Kenya-ARV-Guidelines-2022-Final-1.pdf")
            )
            page_number = int(page or 0)
            toc.append(
                {
                    "id": self.legacy_page_section_id(page_number),
                    "title": self.legacy_section_title(
                        str(first.get("text", "")), page_number, source
                    ),
                    "level": 1,
                    "page": page_number,
                    "source": source,
                    "chunk_count": int(len(page_df)),
                    "legacy": True,
                }
            )
        return toc

    # ------------------------------------------------------------------ #
    # Per-table search                                                     #
    # ------------------------------------------------------------------ #

    async def _search_legacy_documents(
        self, query: str, k_final: int
    ) -> List[RetrievedChunk]:
        def _sync() -> pd.DataFrame:
            return (
                self.db.open_table("documents")
                .search(query, query_type="fts")
                .limit(k_final)
                .to_pandas()
            )

        results: pd.DataFrame = await asyncio.to_thread(_sync)
        chunks: List[RetrievedChunk] = []
        for _, row in results.iterrows():
            raw_score = float(
                row.get("_score", row.get("_relevance_score", 1.0)) or 1.0
            )
            # FTS scores are not distances; keep as-is and clip to [0,1]
            score = min(1.0, max(0.0, raw_score))
            chunks.append(self._row_to_chunk(row, score, "documents"))
        return chunks

    async def _search_guideline_table(
        self,
        table_name: str,
        query: str,
        search_query: str,
        k_initial: int,
        k_final: int,
        timing_breakdowns: Optional[List[Dict[str, float]]] = None,
    ) -> List[RetrievedChunk]:
        """
        Vector search with BGE instruction prefix → cosine similarity normalisation
        → cross-encoder reranking with sigmoid normalisation.
        FTS is used as fallback on vector search failure.
        Both paths use the raw query string (no instruction prefix) for text matching.
        """
        embedded_query = f"{_BGE_QUERY_PREFIX}{search_query}"
        embed_ms = 0.0
        vector_search_ms = 0.0
        rerank_ms = 0.0

        def _vector_search() -> pd.DataFrame:
            nonlocal embed_ms, vector_search_ms
            table = self.db.open_table(table_name)
            embed_start = time.perf_counter()
            try:
                vec = list(
                    self._get_embedding_model().embed([embedded_query])
                )[0].tolist()
            finally:
                embed_ms = (time.perf_counter() - embed_start) * 1000

            search_start = time.perf_counter()
            try:
                return table.search(vec).limit(k_initial).to_pandas()
            finally:
                vector_search_ms = (time.perf_counter() - search_start) * 1000

        def _fts_search() -> pd.DataFrame:
            # Raw query — no instruction prefix for text matching
            return (
                self.db.open_table(table_name)
                .search(query, query_type="fts")
                .limit(k_initial)
                .to_pandas()
            )

        try:
            results: pd.DataFrame = await async_retry(
                lambda: asyncio.to_thread(_vector_search),
                max_attempts=2,
            )
            use_fts_scores = False
        except Exception as exc:
            logger.warning(
                "Vector search failed for %s; falling back to FTS: %s",
                table_name,
                exc,
            )
            results = await async_retry(
                lambda: asyncio.to_thread(_fts_search),
                max_attempts=2,
            )
            use_fts_scores = True

        if results.empty:
            return []

        passages = results["text"].astype(str).tolist()

        # Cross-encoder reranking — sigmoid-normalised scores, no threshold cut
        try:
            if RERANK_STRATEGY == "none":
                raise RuntimeError("Rerank disabled by RERANK_STRATEGY=none")

            def _rerank():
                return self._get_reranker().rank(
                    query=query,
                    docs=passages,
                    doc_ids=list(range(len(passages))),
                )

            rerank_start = time.perf_counter()
            try:
                reranked = await asyncio.to_thread(_rerank)
            finally:
                rerank_ms = (time.perf_counter() - rerank_start) * 1000
            ordered = [
                (int(item.document.doc_id), _sigmoid(float(item.score)))
                for item in list(reranked)[:k_final]
            ]
        except Exception as exc:
            logger.warning("Reranking failed; using raw search scores: %s", exc)
            # Fall back to cosine similarity from vector distance
            if use_fts_scores:
                ordered = [
                    (idx, min(1.0, float(results.iloc[idx].get("_score", 0.5) or 0.5)))
                    for idx in range(min(k_final, len(results)))
                ]
            else:
                ordered = [
                    (
                        idx,
                        _cosine_distance_to_similarity(
                            float(results.iloc[idx].get("_distance", 1.0) or 1.0)
                        ),
                    )
                    for idx in range(min(k_final, len(results)))
                ]

        chunks: List[RetrievedChunk] = []
        for idx, score in ordered:
            row = results.iloc[int(idx)]
            chunks.append(self._row_to_chunk(row, score, table_name))

        if timing_breakdowns is not None:
            timing_breakdowns.append(
                {
                    "table_name": table_name,
                    "embed_ms": embed_ms,
                    "vector_search_ms": vector_search_ms,
                    "rerank_ms": rerank_ms,
                }
            )
        return chunks

    # ------------------------------------------------------------------ #
    # Public search entry point                                            #
    # ------------------------------------------------------------------ #

    async def search_guidelines(
        self,
        query: str,
        disease: Optional[List[str]] = None,
        session_id: str = "default",
        query_id: str = "default",
        k_initial: int = 20,
        k_final: int = 5,
        use_hyde: bool = False,
        search_query: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        start_time = time.time()
        disease_values = (
            [d.lower() for d in disease if d]
            if isinstance(disease, list)
            else [disease.lower() if disease else None]
        )[:3]
        tables: List[str] = []
        for disease_value in disease_values:
            for table_name in self._get_table_names(disease_value):
                if table_name not in tables:
                    tables.append(table_name)
        if not tables:
            return []

        # HyDE: skip for queries that already contain specific numeric tokens
        _specific = {"mg", "kg", "ml", "mmol", "dose", "table", "figure"}
        skip_hyde = any(tok in query.lower().split() for tok in _specific)
        hyde_disease = disease_values[0] if len(disease_values) == 1 else None
        if use_hyde and not skip_hyde and not search_query:
            search_query = await self._generate_hyde_hypothesis(query, hyde_disease)
        else:
            search_query = search_query or query

        # Parallel fan-out across tables
        timing_breakdowns: List[Dict[str, float]] = []

        async def _search_one(table_name: str) -> List[RetrievedChunk]:
            try:
                async with asyncio.timeout(SEARCH_TASK_TIMEOUT_SECONDS):
                    if table_name == "documents":
                        return await self._search_legacy_documents(search_query, k_final)
                    return await self._search_guideline_table(
                        table_name,
                        query,
                        search_query,
                        k_initial,
                        k_final,
                        timing_breakdowns,
                    )
            except TimeoutError:
                logger.warning("Search timeout for %s", table_name)
                return []
            except Exception as exc:
                logger.warning("Search failed for %s: %s", table_name, exc)
                return []

        results_nested: List[List[RetrievedChunk]] = await asyncio.gather(
            *[_search_one(t) for t in tables]
        )
        chunks: List[RetrievedChunk] = [c for sub in results_nested for c in sub]
        if not chunks:
            chunks = await self._fallback_table_scan(tables, k_final)
        chunks.sort(key=lambda c: c.score, reverse=True)
        final_chunks = chunks[:k_final]

        latency = (time.time() - start_time) * 1000
        await log_retrieval(
            session_id=session_id,
            query_id=query_id,
            tool_name="search_guidelines",
            search_query=query,
            chunks_returned=len(final_chunks),
            top_score=final_chunks[0].score if final_chunks else 0.0,
            latency_ms=latency,
            embed_ms=sum(item["embed_ms"] for item in timing_breakdowns),
            vector_search_ms=sum(
                item["vector_search_ms"] for item in timing_breakdowns
            ),
            rerank_ms=sum(item["rerank_ms"] for item in timing_breakdowns),
            expanded_query=search_query if search_query != query else None,
        )
        return final_chunks

    async def _fallback_table_scan(
        self,
        tables: List[str],
        k_final: int,
    ) -> List[RetrievedChunk]:
        """Last-resort bounded scan so offline mode does not return empty text."""
        def _sync() -> List[RetrievedChunk]:
            fallback: List[RetrievedChunk] = []
            for table_name in tables:
                try:
                    df = self.db.open_table(table_name).search().limit(k_final).to_pandas()
                except Exception as exc:
                    logger.warning("Fallback scan failed for %s: %s", table_name, exc)
                    continue
                for _, row in df.iterrows():
                    fallback.append(self._row_to_chunk(row, 0.01, table_name))
                    if len(fallback) >= k_final:
                        return fallback
            return fallback

        return await asyncio.to_thread(_sync)

    # ------------------------------------------------------------------ #
    # Section fetch                                                        #
    # ------------------------------------------------------------------ #

    def get_section(
        self, section_id: str, disease: str
    ) -> Optional[RetrievedChunk]:
        table_names = self._get_table_names(disease)
        if not table_names:
            return None

        table_name = table_names[0]
        table = self.db.open_table(table_name)

        if table_name == "documents":
            if section_id.startswith("legacy-page-"):
                try:
                    page = int(section_id.replace("legacy-page-", "", 1))
                except ValueError:
                    return None
                results = (
                    table.search()
                    .where(f"page = {page}")
                    .limit(100)
                    .to_pandas()
                )
                if results.empty:
                    return None
                results = results.sort_values("id")
                first = results.iloc[0]
                source = str(
                    first.get("source", "Kenya-ARV-Guidelines-2022-Final-1.pdf")
                )
                combined = "\n\n".join(results["text"].astype(str).tolist())
                return RetrievedChunk(
                    text=combined,
                    parent_text=combined,
                    section_title=self.legacy_section_title(
                        combined, page, source
                    ),
                    page=page,
                    score=1.0,
                    disease="hiv",
                    chunk_id=section_id,
                    parent_id=section_id,
                    source_url=DISEASE_CONFIG["hiv"]["source_url"],
                    guideline_name=DISEASE_CONFIG["hiv"]["guideline_name"],
                    low_confidence=False,
                )
            results = (
                table.search()
                .where(f"id = '{section_id}'")
                .limit(1)
                .to_pandas()
            )
        else:
            results = (
                table.search()
                .where(f"parent_id = '{section_id}'")
                .limit(1)
                .to_pandas()
            )

        if results.empty:
            return None
        return self._row_to_chunk(results.iloc[0], 1.0, table_name)

    # ------------------------------------------------------------------ #
    # Structured KB lookup                                                 #
    # ------------------------------------------------------------------ #

    async def lookup_kb(
        self,
        query_type: str,
        disease: str,
        filters: Dict[str, Any],
        session_id: str = "default",
        query_id: str = "default",
    ) -> Optional[KBResult]:
        table_name = f"{disease.lower()}_kb_tables"
        if table_name not in self.table_names():
            return None

        def _sync() -> pd.DataFrame:
            return (
                self.db.open_table(table_name)
                .search()
                .where(f"table_type = '{query_type}'")
                .to_pandas()
            )

        results: pd.DataFrame = await asyncio.to_thread(_sync)
        if results.empty:
            return None

        for _, row in results.iterrows():
            row_data = json.loads(row["raw_json"])
            if all(
                str(v).lower() in str(row_data.get(k, "")).lower()
                for k, v in filters.items()
            ):
                return KBResult(
                    data=row_data,
                    text=row["text"],
                    source=row["source_ref"],
                    disease=row["disease"],
                    table_type=row["table_type"],
                    confidence="structured",
                )
        return None

    async def query_pageindex(
        self,
        query: str,
        disease: Optional[str] = None,
        top_k: int = 3,
    ) -> List[PageIndexResult]:
        """Search page-level summaries before escalating to chunk retrieval."""
        if "pageindex_chunks" not in self.table_names():
            return []

        def _vector_search() -> pd.DataFrame:
            table = self.db.open_table("pageindex_chunks")
            vec = list(
                self._get_embedding_model().embed([f"{_BGE_QUERY_PREFIX}{query}"])
            )[0].tolist()
            search = table.search(vec)
            if disease:
                search = search.where(f"disease = '{disease.lower()}'")
            return search.limit(top_k).to_pandas()

        def _fts_search() -> pd.DataFrame:
            table = self.db.open_table("pageindex_chunks")
            search = table.search(query, query_type="fts")
            if disease:
                search = search.where(f"disease = '{disease.lower()}'")
            return search.limit(top_k).to_pandas()

        try:
            df = await asyncio.to_thread(_vector_search)
            use_fts_scores = False
        except Exception as exc:
            logger.warning("PageIndex vector search failed: %s", exc)
            try:
                df = await asyncio.to_thread(_fts_search)
                use_fts_scores = True
            except Exception as fts_exc:
                logger.warning("PageIndex FTS search failed: %s", fts_exc)
                return []

        results: List[PageIndexResult] = []
        for _, row in df.iterrows():
            raw_score = float(row.get("_score", 0.5) or 0.5)
            distance = float(row.get("_distance", 1.0) or 1.0)
            score = raw_score if use_fts_scores else _cosine_distance_to_similarity(distance)
            results.append(
                PageIndexResult(
                    disease=str(row.get("disease", "")),
                    page=int(row.get("page", 0) or 0),
                    section_path=str(row.get("section_path", "")),
                    summary=str(row.get("summary", "")),
                    text=str(row.get("text", "")),
                    score=max(0.0, min(1.0, score)),
                )
            )
        return results

    def pageindex_stats(self) -> Dict[str, Any]:
        """Return row counts for the page-level index by disease."""
        if "pageindex_chunks" not in self.table_names():
            return {"total": 0, "by_disease": {}}
        try:
            df = self.db.open_table("pageindex_chunks").search().limit(100000).to_pandas()
        except Exception as exc:
            logger.warning("PageIndex stats unavailable: %s", exc)
            return {"total": 0, "by_disease": {}, "error": str(exc)}
        if df.empty or "disease" not in df.columns:
            return {"total": 0, "by_disease": {}}
        counts = df.groupby("disease").size().sort_index()
        return {
            "total": int(len(df)),
            "by_disease": {str(k): int(v) for k, v in counts.items()},
        }


# ---------------------------------------------------------------------------
# DEPRECATED shim — kept for backward-compat with PoC code
# ---------------------------------------------------------------------------

def text_search(query: str) -> List[str]:
    """DEPRECATED: use SearchIndex.search_guidelines."""
    idx = SearchIndex()
    try:
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(idx.search_guidelines(query))
        return [r.text for r in results]
    except Exception:
        return []
