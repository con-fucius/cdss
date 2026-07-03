"""
app/terminology/router.py

FastAPI router for terminology admin endpoints.

Mount point: /terminology  (admin-only, registered in api.py)

These endpoints are completely isolated from the live chat path.
They do not import from search_tools, search_agent, or ingest.
The TerminologyService is the only cross-package dependency.

Endpoints
---------
GET  /terminology/health
    Ping: confirms tables exist and returns concept/alias/relation counts.

GET  /terminology/concept/{cui}
    Full concept detail by CUI.

POST /terminology/search
    Full-text concept search.  Body: {query, semantic_types, top_k}.

POST /terminology/autocomplete
    Lightweight concept autocomplete for the chat input.  Body: {query, top_k}.

POST /terminology/link
    Annotate a text string with CUIs.  Body: {text, disease}.

GET  /terminology/related/{cui}
    UMLS relations for a CUI.  Query params: relation_types, source_sabs.

GET  /terminology/coverage
    Per-disease annotation coverage report.  Triggers a live recompute.

POST /terminology/annotate-chunk
    Annotate one guideline chunk.  Body: {chunk_id, disease, text}.
    Writes to guideline_chunk_concepts.  Admin only.
    This is the ingestion-time annotation path, callable manually for
    backfill.  During live ingestion it will be called by the annotator.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/terminology", tags=["terminology"])


# ─────────────────────────────────────────────────────────────────────────────
# Request models
# ─────────────────────────────────────────────────────────────────────────────

class ConceptSearchRequest(BaseModel):
    query: str
    semantic_types: Optional[List[str]] = None
    top_k: int = Field(default=10, ge=1, le=50)


class TerminologyAutocompleteRequest(BaseModel):
    query: str
    top_k: int = Field(default=8, ge=1, le=20)


class TerminologyExpandRequest(BaseModel):
    query: str
    disease: Optional[str] = None


class LinkTextRequest(BaseModel):
    text: str
    disease: Optional[str] = None


class AnnotateChunkRequest(BaseModel):
    chunk_id: str
    disease: str
    text: str


# ─────────────────────────────────────────────────────────────────────────────
# Dependency: require admin role (mirrors api.py pattern)
# ─────────────────────────────────────────────────────────────────────────────

def _require_admin(x_user_role: Optional[str] = Header(None)) -> str:
    """
    Reads the X-User-Role HTTP header (FastAPI injects via Header(None)).
    Falls back to CDSS_ROLE env var for local dev without a reverse proxy.
    Raises 403 if the resolved role is not ADMIN.
    """
    import os
    role = (x_user_role or os.getenv("CDSS_ROLE", "CLINICIAN")).upper()
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin access required")
    return role



# ─────────────────────────────────────────────────────────────────────────────
# Service accessor (lazy singleton)
# ─────────────────────────────────────────────────────────────────────────────

_service = None


def _get_service():
    global _service
    if _service is None:
        import os
        from .service import TerminologyService
        qdrant_url = os.getenv("CDSS_QDRANT_URL") or None
        _service = TerminologyService(qdrant_url=qdrant_url)
    return _service


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/health")
async def terminology_health(role: str = Depends(_require_admin)):
    """Confirm terminology tables exist and return row counts."""
    try:
        from sqlalchemy import func, select
        from ..db import get_session
        from .models import (
            GuidelineChunkConcept,
            TerminologyAlias,
            TerminologyConcept,
            TerminologyRelation,
        )

        async with get_session() as session:
            concept_count = await session.scalar(
                select(func.count()).select_from(TerminologyConcept)
            )
            alias_count = await session.scalar(
                select(func.count()).select_from(TerminologyAlias)
            )
            relation_count = await session.scalar(
                select(func.count()).select_from(TerminologyRelation)
            )
            chunk_concept_count = await session.scalar(
                select(func.count()).select_from(GuidelineChunkConcept)
            )

        return {
            "status": "ok",
            "terminology_concepts": int(concept_count or 0),
            "terminology_aliases": int(alias_count or 0),
            "terminology_relations": int(relation_count or 0),
            "guideline_chunk_concepts": int(chunk_concept_count or 0),
        }
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Terminology dependency missing: {exc.name}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Terminology tables unavailable: {exc}",
        ) from exc


@router.get("/concept/{cui}")
async def get_concept(cui: str, role: str = Depends(_require_admin)):
    svc = _get_service()
    concept = await svc.get_concept(cui.strip().upper())
    if concept is None:
        raise HTTPException(status_code=404, detail=f"CUI not found: {cui}")
    return concept


@router.post("/search")
async def search_concepts(request: ConceptSearchRequest, role: str = Depends(_require_admin)):
    svc = _get_service()
    results = await svc.search_concepts(
        query=request.query,
        semantic_types=request.semantic_types,
        top_k=request.top_k,
    )
    return {"results": results, "count": len(results)}


@router.post("/autocomplete")
async def autocomplete_concepts(request: TerminologyAutocompleteRequest):
    svc = _get_service()
    results = await svc.search_concepts(
        query=request.query,
        semantic_types=None,
        top_k=request.top_k,
    )
    autocomplete_results = [
        {"cui": item.get("cui", ""), "preferred_name": item.get("preferred_name", "")}
        for item in results
        if item.get("cui") or item.get("preferred_name")
    ]
    return {"results": autocomplete_results, "count": len(autocomplete_results)}


@router.post("/expand")
async def expand_query(request: TerminologyExpandRequest):
    from .service import expand_query_with_terminology_details

    expanded_query, concepts = await expand_query_with_terminology_details(
        query=request.query,
        disease=request.disease,
    )
    return {
        "expanded_query": expanded_query,
        "concepts": concepts,
        "count": len(concepts),
        "changed": expanded_query != request.query,
    }


@router.post("/link")
async def link_text(request: LinkTextRequest, role: str = Depends(_require_admin)):
    svc = _get_service()
    results = await svc.link_text(text=request.text, disease=request.disease)
    return {"concepts": results, "count": len(results)}


@router.get("/related/{cui}")
async def related_concepts(
    cui: str,
    relation_types: Optional[str] = None,
    source_sabs: Optional[str] = None,
    limit: int = 20,
    role: str = Depends(_require_admin),
):
    """
    Return UMLS relations for a CUI.

    relation_types: comma-separated list e.g. "RB,RN,CHD"
    source_sabs:    comma-separated list e.g. "SNOMEDCT_US,MSH"
    """
    svc = _get_service()
    rel_types = [r.strip() for r in relation_types.split(",")] if relation_types else None
    sabs = [s.strip() for s in source_sabs.split(",")] if source_sabs else None
    results = await svc.related_concepts(
        cui=cui.strip().upper(),
        relation_types=rel_types,
        source_sabs=sabs,
        limit=min(limit, 100),
    )
    return {"cui": cui, "relations": results, "count": len(results)}


@router.get("/coverage")
async def coverage_report(role: str = Depends(_require_admin)):
    """
    Recompute and return per-disease annotation coverage.
    Writes results to terminology_coverage table.
    """
    svc = _get_service()
    try:
        from ..search_tools import SearchIndex
        index = SearchIndex()
        stats = index.pageindex_stats()
        total_chunks_by_disease = stats.get("by_disease", {})
    except Exception as exc:
        logger.warning("Could not fetch pageindex stats for coverage: %s", exc)
        total_chunks_by_disease = {}

    report = await svc.coverage_report(total_chunks_by_disease)
    return {"coverage": report}


@router.post("/annotate-chunk")
async def annotate_chunk(request: AnnotateChunkRequest, role: str = Depends(_require_admin)):
    """
    Annotate one guideline chunk with UMLS concepts.
    Writes to guideline_chunk_concepts.

    This is the ingestion-time annotation entry point, also callable
    manually for backfill.  Does NOT modify LanceDB or the chat path.
    """
    svc = _get_service()
    concepts = await svc.link_text(text=request.text, disease=request.disease)

    if not concepts:
        return {"chunk_id": request.chunk_id, "concepts_found": 0}

    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from ..db import get_session
        from .models import GuidelineChunkConcept

        rows = [
            {
                "chunk_id": request.chunk_id,
                "cui": c["cui"],
                "preferred_name": c.get("preferred_name", "")[:500],
                "disease": request.disease,
                "confidence": c.get("confidence"),
                "annotation_source": c.get("annotation_source", "exact_alias"),
            }
            for c in concepts
            if c.get("cui")
        ]

        if rows:
            async with get_session() as session:
                stmt = pg_insert(GuidelineChunkConcept).values(rows)
                stmt = stmt.on_conflict_do_nothing(constraint="uq_gcc_chunk_cui")
                await session.execute(stmt)
                await session.commit()

        return {
            "chunk_id": request.chunk_id,
            "concepts_found": len(rows),
            "concepts": [{"cui": r["cui"], "preferred_name": r["preferred_name"]} for r in rows],
        }
    except Exception as exc:
        logger.error("annotate_chunk failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.get("/chunk/{chunk_id}")
async def get_chunk_concepts(chunk_id: str, role: str = Depends(_require_admin)):
    """
    Return all UMLS concepts annotated for a specific chunk (Admin X-Ray).
    """
    try:
        from sqlalchemy import select
        from ..db import get_session
        from .models import GuidelineChunkConcept
        
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(GuidelineChunkConcept)
                    .where(GuidelineChunkConcept.chunk_id == chunk_id)
                )
            ).scalars().all()
            
            return {
                "chunk_id": chunk_id,
                "concepts": [
                    {
                        "cui": r.cui,
                        "preferred_name": r.preferred_name,
                        "confidence": r.confidence,
                        "annotation_source": r.annotation_source
                    }
                    for r in rows
                ]
            }
    except Exception as exc:
        logger.warning("get_chunk_concepts failed: %s", exc)
        return {"chunk_id": chunk_id, "concepts": []}
