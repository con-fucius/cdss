"""app/terminology/service.py.

TerminologyService — the single stable interface for all UMLS operations.

This class is the ONLY entry point the rest of the application should ever
call into this package.  Internal helpers (repositories, ETL, Qdrant client)
are private to the package and subject to change.

Current implementation tier: Postgres-only, no Qdrant dependency.
Qdrant concept-vector search is added in the next tier once the ETL
has been run and embeddings exist.  The interface is stable either way.

Methods:
-------
search_concepts(query, semantic_types, top_k)
    Full-text search over preferred_name + alias table.
    Returns CUIs ranked by match quality.
    semantic_types filter accepts TUI codes or plain names.

link_text(text, disease)
    Find UMLS concepts mentioned in a text string.
    Uses alias lookup + optional Qdrant similarity (when available).
    Returns list of {cui, preferred_name, confidence, match_span}.

get_concept(cui)
    Exact CUI lookup.  Returns the full concept dict or None.

related_concepts(cui, relation_types, source_sabs, limit)
    Return UMLS relations for a CUI, filtered by type and source.
    Trust order: SNOMEDCT_US > MSH > ICD10CM > RXNORM.
    Never returns relations that contradict the evidence graph.
    (No cross-package call here — callers must reconcile themselves.)

coverage_report()
    Compute and persist per-disease annotation coverage statistics.
    Called by the admin endpoint; not called during chat.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Relation types we surface — others are suppressed as clinically noisy
_CLINICAL_RELATION_TYPES = {
    "RB",  # broader
    "RN",  # narrower
    "CHD",  # child
    "PAR",  # parent
    "RO",  # other
    "SIB",  # sibling
}

# Source trust order (index = priority; lower = higher trust)
_SOURCE_TRUST_ORDER = [
    "SNOMEDCT_US",
    "MSH",
    "ICD10CM",
    "ICD10",
    "RXNORM",
    "CPT",
]

_TERMINOLOGY_EXPANSION_TIMEOUT_SECONDS = float(
    os.getenv("CDSS_TERMINOLOGY_SHADOW_TIMEOUT_SECONDS", "8")
)


async def expand_query_with_terminology(
    query: str,
    disease: str | None = None,
) -> str:
    """Return query expanded with matched UMLS preferred names, or the raw query."""
    expanded, _ = await expand_query_with_terminology_details(query, disease)
    return expanded


async def expand_query_with_terminology_details(
    query: str,
    disease: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Link terminology and return the expanded query plus matched concepts."""
    if not query or not query.strip():
        return query, []
    try:
        concepts = await asyncio.wait_for(
            TerminologyService().link_text(text=query, disease=disease),
            timeout=_TERMINOLOGY_EXPANSION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("Terminology query expansion failed: %s", exc)
        return query, []

    expanded_terms = [
        c["preferred_name"]
        for c in concepts
        if c.get("preferred_name") and len(c["preferred_name"]) > 3
    ]
    if not expanded_terms:
        return query, concepts
    expanded = f"{query} {' '.join(expanded_terms[:5])}"
    return expanded, concepts


def _source_rank(sab: str | None) -> int:
    try:
        return _SOURCE_TRUST_ORDER.index(sab or "")
    except ValueError:
        return len(_SOURCE_TRUST_ORDER)


class TerminologyService:
    """Stable public interface for all UMLS terminology operations.

    Instantiate once and reuse; the Postgres session is acquired
    per-call so this is safe in an async FastAPI context.
    """

    def __init__(self, qdrant_url: str | None = None) -> None:
        """qdrant_url: optional Qdrant endpoint for concept-vector search.
        When None (default), all lookups use Postgres only.
        The Qdrant path activates automatically once embeddings exist.
        """
        self._qdrant_url = qdrant_url
        self._qdrant_client: Any = None  # lazy-loaded

    # ─────────────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────────────

    async def search_concepts(
        self,
        query: str,
        semantic_types: list[str] | None = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Search UMLS concepts by preferred name or alias.

        semantic_types: list of TUI codes (e.g. "T047") or plain names
                        (e.g. "Disease or Syndrome").
                        When None, no semantic type filter is applied.

        Returns list of {cui, preferred_name, definition, semantic_types,
                          match_score, match_field} sorted by match_score desc.
        """
        if not query or not query.strip():
            return []
        try:
            return await self._pg_search_concepts(query, semantic_types, top_k)
        except Exception as exc:
            logger.warning("search_concepts failed: %s", exc)
            return []

    async def link_text(
        self,
        text: str,
        disease: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find UMLS concepts mentioned in a text string.

        Uses a two-step process:
        1. Tokenise text into candidate spans (noun-phrase heuristic).
        2. For each span, alias-lookup in Postgres.
        Qdrant similarity is used as a fallback when no alias match found
        and Qdrant is configured.

        Returns list of:
            {cui, preferred_name, confidence, match_span, annotation_source}
        sorted by confidence desc.

        disease: optional hint for semantic type pre-filtering.
        """
        if not text or not text.strip():
            return []
        candidates = _extract_candidate_spans(text)
        results: list[dict[str, Any]] = []
        seen_cuis: set = set()

        for span in candidates:
            matches = await self._alias_lookup(span, disease)
            for match in matches:
                if match["cui"] not in seen_cuis:
                    seen_cuis.add(match["cui"])
                    results.append({**match, "match_span": span})

        # Qdrant fallback for unmatched spans
        if self._qdrant_url and len(results) < 3:
            qdrant_matches = await self._qdrant_link(text, disease, top_k=5)
            for match in qdrant_matches:
                if match["cui"] not in seen_cuis:
                    seen_cuis.add(match["cui"])
                    results.append(match)

        results.sort(key=lambda r: r.get("confidence", 0.0), reverse=True)
        return results[:20]

    async def get_concept(self, cui: str) -> dict[str, Any] | None:
        """Exact CUI lookup.  Returns None if not found."""
        if not cui or not cui.strip():
            return None
        try:
            return await self._pg_get_concept(cui.strip().upper())
        except Exception as exc:
            logger.warning("get_concept(%s) failed: %s", cui, exc)
            return None

    async def related_concepts(
        self,
        cui: str,
        relation_types: list[str] | None = None,
        source_sabs: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return UMLS relations for a CUI.

        relation_types: subset of _CLINICAL_RELATION_TYPES to include.
                        Defaults to all clinical relation types.
        source_sabs: restrict to specific UMLS sources.
                     Defaults to all sources, ranked by trust order.

        Results are sorted by source trust order (SNOMEDCT_US first).
        """
        if not cui:
            return []
        rel_types = relation_types or list(_CLINICAL_RELATION_TYPES)
        try:
            return await self._pg_related_concepts(
                cui.strip().upper(), rel_types, source_sabs, limit
            )
        except Exception as exc:
            logger.warning("related_concepts(%s) failed: %s", cui, exc)
            return []

    async def coverage_report(
        self, total_chunks_by_disease: dict[str, int] | None = None
    ) -> list[dict[str, Any]]:
        """Compute per-disease annotation coverage and persist to
        terminology_coverage table.

        Returns list of {disease, total_chunks, annotated_chunks,
                          unique_cuis, coverage_pct}.
        """
        try:
            return await self._compute_coverage(total_chunks_by_disease or {})
        except Exception as exc:
            logger.warning("coverage_report failed: %s", exc)
            return []

    # ─────────────────────────────────────────────────────────────────
    # Postgres implementation
    # ─────────────────────────────────────────────────────────────────

    async def _pg_search_concepts(
        self,
        query: str,
        semantic_types: list[str] | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import func, or_, select

        from ..db import get_session
        from .models import TerminologyAlias, TerminologyConcept

        q = query.strip().lower()
        async with get_session() as session:
            # Priority 1: exact preferred_name match (case-insensitive)
            # Priority 2: preferred_name ILIKE
            # Priority 3: alias ILIKE
            stmt = (
                select(
                    TerminologyConcept,
                    func.similarity(func.lower(TerminologyConcept.preferred_name), q).label("sim"),
                )
                .outerjoin(
                    TerminologyAlias,
                    TerminologyAlias.cui == TerminologyConcept.cui,
                )
                .where(
                    or_(
                        func.lower(TerminologyConcept.preferred_name).contains(q),
                        func.lower(TerminologyAlias.alias).contains(q),
                    )
                )
                .order_by(func.similarity(func.lower(TerminologyConcept.preferred_name), q).desc())
                .limit(top_k * 5)  # over-fetch before de-dupe and semantic filter
            )

            rows = (await session.execute(stmt)).all()

        results = []
        seen_cuis = set()
        semantic_type_filter = set(semantic_types or [])
        for row, sim in rows:
            concept = row
            if concept.cui in seen_cuis:
                continue
            seen_cuis.add(concept.cui)
            if semantic_type_filter:
                concept_stypes = set(concept.semantic_types or [])
                if not semantic_type_filter.intersection(concept_stypes):
                    continue
            results.append(
                {
                    "cui": concept.cui,
                    "preferred_name": concept.preferred_name,
                    "definition": concept.definition,
                    "semantic_types": concept.semantic_types,
                    "match_score": float(sim or 0.0),
                    "match_field": "preferred_name",
                }
            )
            if len(results) >= top_k:
                break

        return results

    async def _alias_lookup(
        self,
        span: str,
        disease: str | None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import func, select

        from ..db import get_session
        from .models import TerminologyAlias, TerminologyConcept

        span_lower = span.strip().lower()
        if len(span_lower) < 3:
            return []

        async with get_session() as session:
            stmt = (
                select(TerminologyConcept, TerminologyAlias.source_sab)
                .join(
                    TerminologyAlias,
                    TerminologyAlias.cui == TerminologyConcept.cui,
                )
                .where(func.lower(TerminologyAlias.alias) == span_lower)
                .limit(5)
            )
            rows = (await session.execute(stmt)).all()

        return [
            {
                "cui": concept.cui,
                "preferred_name": concept.preferred_name,
                "confidence": 0.95,
                "annotation_source": "exact_alias",
                "source_sab": sab,
            }
            for concept, sab in rows
        ]

    async def _pg_get_concept(self, cui: str) -> dict[str, Any] | None:
        from sqlalchemy import select

        from ..db import get_session
        from .models import TerminologyConcept

        async with get_session() as session:
            concept = await session.scalar(
                select(TerminologyConcept).where(TerminologyConcept.cui == cui)
            )
        if concept is None:
            return None
        return {
            "cui": concept.cui,
            "preferred_name": concept.preferred_name,
            "definition": concept.definition,
            "semantic_types": concept.semantic_types,
            "synonyms": concept.synonyms,
            "codes": concept.codes,
            "sources": concept.sources,
            "qdrant_id": concept.qdrant_id,
        }

    async def _pg_related_concepts(
        self,
        cui: str,
        relation_types: list[str],
        source_sabs: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from ..db import get_session
        from .models import TerminologyRelation

        async with get_session() as session:
            stmt = (
                select(TerminologyRelation)
                .where(
                    TerminologyRelation.cui1 == cui,
                    TerminologyRelation.relation_type.in_(relation_types),
                )
                .limit(limit * 2)
            )
            if source_sabs:
                stmt = stmt.where(TerminologyRelation.source_sab.in_(source_sabs))
            rows = (await session.scalars(stmt)).all()

        results = [
            {
                "cui": row.cui2,
                "relation_type": row.relation_type,
                "relation_label": row.relation_label,
                "source_sab": row.source_sab,
                "trust_rank": _source_rank(row.source_sab),
            }
            for row in rows
            if row.relation_type in _CLINICAL_RELATION_TYPES
        ]
        results.sort(key=lambda r: r["trust_rank"])
        return results[:limit]

    async def _compute_coverage(
        self, total_chunks_by_disease: dict[str, int]
    ) -> list[dict[str, Any]]:
        from sqlalchemy import func, select
        from sqlalchemy.dialects.postgresql import insert

        from ..db import get_session
        from .models import GuidelineChunkConcept, TerminologyCoverage

        async with get_session() as session:
            rows = (
                await session.execute(
                    select(
                        GuidelineChunkConcept.disease,
                        func.count(func.distinct(GuidelineChunkConcept.chunk_id)).label(
                            "annotated_chunks"
                        ),
                        func.count(func.distinct(GuidelineChunkConcept.cui)).label("unique_cuis"),
                    ).group_by(GuidelineChunkConcept.disease)
                )
            ).all()

            report = []
            for disease, annotated, unique in rows:
                # Get real total_chunks if passed, else fallback to annotated
                total_chunks = total_chunks_by_disease.get(disease, annotated)
                total_chunks = max(total_chunks, annotated)  # ensure at least annotated count
                pct = (annotated / total_chunks * 100.0) if total_chunks > 0 else 0.0

                stmt = insert(TerminologyCoverage).values(
                    disease=disease,
                    total_chunks=total_chunks,
                    annotated_chunks=annotated,
                    unique_cuis=unique,
                    coverage_pct=pct,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["disease"],
                    set_={
                        "total_chunks": total_chunks,
                        "annotated_chunks": annotated,
                        "unique_cuis": unique,
                        "coverage_pct": pct,
                    },
                )
                await session.execute(stmt)
                report.append(
                    {
                        "disease": disease,
                        "annotated_chunks": annotated,
                        "unique_cuis": unique,
                        "coverage_pct": pct,
                    }
                )
            await session.commit()

        return report

    # ─────────────────────────────────────────────────────────────────
    # Qdrant implementation (activated only when qdrant_url is set)
    # ─────────────────────────────────────────────────────────────────

    def _get_qdrant_client(self) -> Any:
        if self._qdrant_client is not None:
            return self._qdrant_client
        try:
            from qdrant_client import QdrantClient

            self._qdrant_client = QdrantClient(url=self._qdrant_url)
            return self._qdrant_client
        except ImportError:
            logger.warning("qdrant_client not installed; concept-vector search unavailable")
            return None

    async def _qdrant_link(
        self,
        text: str,
        disease: str | None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Embed text and search Qdrant for nearest concepts."""
        import asyncio
        import os

        client = self._get_qdrant_client()
        if client is None:
            return []

        collection = os.getenv("CDSS_QDRANT_COLLECTION", "umls_concepts")

        try:
            from fastembed import TextEmbedding

            from ..config import get_embedding_model_name

            model = TextEmbedding(model_name=get_embedding_model_name())
            vector = await asyncio.to_thread(lambda: list(model.embed([text]))[0].tolist())
            results = await asyncio.to_thread(
                lambda: client.search(
                    collection_name=collection,
                    query_vector=vector,
                    limit=top_k,
                    with_payload=True,
                )
            )
            return [
                {
                    "cui": hit.payload.get("cui", ""),
                    "preferred_name": hit.payload.get("preferred_name", ""),
                    "confidence": float(hit.score),
                    "annotation_source": "qdrant_similarity",
                    "match_span": text[:80],
                }
                for hit in results
                if hit.payload.get("cui")
            ]
        except Exception as exc:
            logger.warning("Qdrant link_text failed: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────────────────
# Text span extraction (no external NLP dependency)
# ─────────────────────────────────────────────────────────────────────────

_MIN_SPAN_LEN = 3
_MAX_SPAN_LEN = 80
# Stop words that should not be looked up as standalone concepts
_STOP_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "in",
    "for",
    "to",
    "with",
    "is",
    "are",
    "was",
    "be",
    "been",
    "by",
    "at",
    "on",
    "from",
    "this",
    "that",
    "it",
    "its",
    "as",
    "if",
    "not",
    "no",
    "but",
    "all",
    "any",
    "use",
    "used",
    "per",
    "ml",
    "mg",
    "kg",
}


def _extract_candidate_spans(text: str) -> list[str]:
    """Heuristic noun-phrase span extractor — no NLP library required.

    Generates candidates from:
    1. Every token of 3+ characters that is not a stop word or pure number.
    2. Bigrams and trigrams where neither token is a stop word.
    3. Any capitalised multi-word sequence (likely a proper clinical term).
    """
    # Normalise whitespace
    text = re.sub(r"\s+", " ", text).strip()
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-']{2,}", text)

    seen: set = set()
    candidates: list[str] = []

    def _add(span: str) -> None:
        s = span.strip()
        if _MIN_SPAN_LEN <= len(s) <= _MAX_SPAN_LEN and s.lower() not in seen:
            seen.add(s.lower())
            candidates.append(s)

    for tok in tokens:
        if tok.lower() not in _STOP_WORDS and not tok.isdigit():
            _add(tok)

    for i in range(len(tokens) - 1):
        if tokens[i].lower() not in _STOP_WORDS and tokens[i + 1].lower() not in _STOP_WORDS:
            _add(f"{tokens[i]} {tokens[i + 1]}")

    for i in range(len(tokens) - 2):
        if all(t.lower() not in _STOP_WORDS for t in tokens[i : i + 3]):
            _add(f"{tokens[i]} {tokens[i + 1]} {tokens[i + 2]}")

    # Capitalised phrase runs (likely named clinical entities)
    cap_run = re.findall(r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text)
    for phrase in cap_run:
        _add(phrase)

    return candidates
