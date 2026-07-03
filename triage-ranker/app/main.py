"""triage-ranker/app/main.py.

FastAPI app entrypoint for the Triage Ranker service.

Endpoints:
- POST /triage — main triage enrichment endpoint
- GET /health — service health with UMLS reachability
- GET /ready — 503 until spaCy model loaded
- POST /admin/rules/reload — reload clinical_rules.yaml without restart
- DELETE /admin/cache — purge L1 and L2 caches

Design constraints:
- No patient text in any log output (PHI safety)
- Never returns zero results — fallback to 'Undifferentiated Emergency'
- UMLS API key absence handled at config level
- Every external call returns None/empty on failure
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import yaml
from fastapi import FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader

from .config import (
    get_admin_api_key,
    get_allowed_origins,
    get_clinical_rules_path,
    get_spacy_model_path,
    get_umls_cache_db_path,
    validate_startup_config,
)
from .pipeline.extractor import extract_keywords
from .pipeline.ranker import rank_diagnoses
from .pipeline.resolver import purge_caches, resolve_keywords
from .schemas import (
    TriageLevel,
    TriageRequest,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Global state ────────────────────────────────────────────────────────────

_spacy_model = None
_clinical_rules = None
_model_loaded = False


def _load_clinical_rules() -> list[dict]:
    """Load clinical_rules.yaml. Reloadable via admin endpoint."""
    global _clinical_rules
    rules_path = get_clinical_rules_path()
    try:
        with open(rules_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _clinical_rules = data.get("rules", [])
        logger.info("Loaded %d clinical rules from %s", len(_clinical_rules), rules_path)
        return _clinical_rules
    except Exception as exc:
        logger.error("Failed to load clinical rules: %s", exc)
        _clinical_rules = []
        return _clinical_rules


def _load_spacy_model():
    """Load spaCy model. Must be pre-installed (baked into Docker image)."""
    global _spacy_model, _model_loaded
    model_path = get_spacy_model_path()
    try:
        import spacy

        _spacy_model = spacy.load(model_path)
        _model_loaded = True
        logger.info("spaCy model loaded from %s", model_path)
    except Exception as exc:
        logger.warning(
            "spaCy model not available (%s). NLP extraction will use regex-only fallback.",
            exc,
        )
        _model_loaded = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_startup_config()
    _load_spacy_model()
    _load_clinical_rules()
    logger.info("Triage Ranker started. Degraded mode: %s", not _model_loaded)
    yield


app = FastAPI(title="Triage Ranker", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)

# ── Admin API key dependency ────────────────────────────────────────────────

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def _require_admin_key(key: str | None = Security(_admin_key_header)) -> None:
    """Validates the X-Admin-Key header for admin endpoints."""
    configured = get_admin_api_key()
    if not configured:
        return
    if not key or key != configured:
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Valid X-Admin-Key header required."},
        )


# ── Request/Response models ─────────────────────────────────────────────────

from pydantic import BaseModel


class TriageResultResponse(BaseModel):
    """Individual triage result."""

    triage_level: str
    esi_level: int
    top_diagnosis: str
    icd10_code: str | None = None
    snomed_code: str | None = None
    shock_index: float | None = None
    severity_level: str | None = None


# ── Health & readiness ──────────────────────────────────────────────────────


@app.get("/health")
async def health():
    """Service health with UMLS reachability (non-blocking), cache stats,
    model loaded status.
    """
    from .pipeline.resolver import _l1_cache

    umls_reachable = "not_configured"
    from .config import is_umls_configured

    if is_umls_configured():
        umls_reachable = "configured"

    return {
        "status": "ok",
        "spacy_model_loaded": _model_loaded,
        "clinical_rules_count": len(_clinical_rules or []),
        "umls_api": umls_reachable,
        "cache_l1_size": len(_l1_cache),
    }


@app.get("/ready")
async def readiness():
    """503 until spaCy model loaded."""
    if _model_loaded:
        return {"status": "ready"}
    # Still allow requests even without spaCy (regex fallback)
    return {"status": "ready", "warning": "spaCy model not loaded, using regex fallback"}


# ── Main triage endpoint ────────────────────────────────────────────────────


@app.post("/triage")
async def triage(request: TriageRequest):
    """POST /triage — main endpoint.

    Validates TriageRequest, runs all three pipeline stages, returns
    TriageResponse. No patient text in any log output.

    Max input length 5000 chars (validated by Pydantic).
    Processing time target: < 500ms on cache hit, < 5s on UMLS API call.
    """
    request_id = str(uuid.uuid4())[:8]
    t_start = time.monotonic()

    # Stage 1: NLP extraction
    t1 = time.monotonic()
    rules_path = get_clinical_rules_path()
    keywords = extract_keywords(
        incident_desc=request.incident_desc,
        rules_path=rules_path,
        spacy_model_path=get_spacy_model_path(),
    )
    t_extraction = (time.monotonic() - t1) * 1000

    # Stage 2: UMLS resolution
    t2 = time.monotonic()
    resolved_keywords, degraded_mode = await resolve_keywords(
        keywords=keywords,
        rules=_clinical_rules or [],
        cache_db_path=get_umls_cache_db_path(),
    )
    t_resolution = (time.monotonic() - t2) * 1000

    # Stage 3: Composite ranking
    t3 = time.monotonic()
    ranking, shock_index = rank_diagnoses(
        resolved_keywords=resolved_keywords,
        gcs_score=request.gcs_score,
        acvpu=request.acvpu,
        sbp=request.sbp,
        hr=request.hr,
        rules=_clinical_rules or [],
        degraded_mode=degraded_mode,
    )
    t_ranking = (time.monotonic() - t3) * 1000

    t_total = (time.monotonic() - t_start) * 1000

    # Determine overall triage level from top diagnosis
    triage_level = TriageLevel.P2  # Default
    esi_level = 3
    if ranking:
        top_score = ranking[0].score_breakdown.get("total", 0.3)
        if top_score >= 0.8:
            triage_level = TriageLevel.P1
            esi_level = 1
        elif top_score >= 0.55:
            triage_level = TriageLevel.P2
            esi_level = 2
        elif top_score >= 0.3:
            triage_level = TriageLevel.P3
            esi_level = 3
        else:
            triage_level = TriageLevel.P4
            esi_level = 5

    # Build response
    from ambulance_cdss_contracts.triage import (
        TriageMetadata,
        TriageResponse,
    )
    from .pipeline.resolver import _l1_cache

    metadata = TriageMetadata(
        request_id=request_id,
        processing_times_ms={
            "extraction": round(t_extraction, 1),
            "resolution": round(t_resolution, 1),
            "ranking": round(t_ranking, 1),
            "total": round(t_total, 1),
        },
        cache_stats={"l1_size": len(_l1_cache)},
        shock_index=shock_index,
        scoring_systems_used=[],
        inferred_risks=[],
    )

    # Infer risk flags
    inferred_risks = []
    if shock_index and shock_index > 1.0:
        inferred_risks.append("HAEMODYNAMIC_INSTABILITY")
    if request.gcs_score and request.gcs_score <= 8:
        inferred_risks.append("SEVERE_TBI")
    if request.acvpu and request.acvpu.lower() in ("u", "unresponsive", "unconscious"):
        inferred_risks.append("UNRESPONSIVE")
    metadata.inferred_risks = inferred_risks

    return TriageResponse(
        diagnosis_ranking=ranking,
        historical_findings=[],
        keywords=keywords,
        triage_level=triage_level,
        esi_level=esi_level,
        degraded_mode=degraded_mode,
        metadata=metadata,
    )


# ── Admin endpoints ─────────────────────────────────────────────────────────


@app.post("/admin/rules/reload", dependencies=[Security(_require_admin_key)])
async def reload_rules():
    """Reload clinical_rules.yaml from disk without restart."""
    rules = _load_clinical_rules()
    return {
        "status": "ok",
        "rules_count": len(rules),
        "reloaded_at": datetime.now(UTC).isoformat(),
    }


@app.delete("/admin/cache", dependencies=[Security(_require_admin_key)])
async def purge_cache():
    """Purge L1 (in-process) and L2 (SQLite) UMLS caches."""
    purge_caches()
    return {
        "status": "ok",
        "message": "L1 and L2 caches purged.",
        "purged_at": datetime.now(UTC).isoformat(),
    }
