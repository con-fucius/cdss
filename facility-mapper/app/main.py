"""facility-mapper/app/main.py.

FastAPI app entrypoint for the Facility Mapper service.

Endpoints:
- GET /health — service health with facility count and data currency
- GET /ready — 503 until BallTree built and facilities loaded
- GET /facilities/nearest — coordinate-based nearest facility search
- POST /facilities/nearest-by-location — text-location geocode + search
- GET /facilities/{facility_id} — single facility detail
- GET /data-currency — data import history
- POST /admin/reload-facilities — clear cache, rebuild BallTree

Follows ambulance-cdss conventions: admin endpoints gated by
_require_admin_key, CORS validated at startup, no PHI in logs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select

from .config import (
    get_admin_api_key,
    get_allowed_origins,
    is_database_configured,
    validate_startup_config,
)
from .data import get_ball_tree
from .db import check_database, close_engine, get_session, init_engine
from .geocoding import clear_cache
from .matcher import find_nearest_by_coords, find_nearest_by_location
from .models import DataImport, Facility

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


# ── Admin API key dependency ───────────────────────────────────────────────

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def _require_admin_key(key: str | None = Security(_admin_key_header)) -> None:
    """Validates the X-Admin-Key header for admin endpoints.
    If ADMIN_API_KEY is not configured (empty string), the check is
    bypassed — this is the development default.
    """
    configured = get_admin_api_key()
    if not configured:
        return
    if not key or key != configured:
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Valid X-Admin-Key header required."},
        )


# ── Request models ──────────────────────────────────────────────────────────


class NearestByLocationRequest(BaseModel):
    """Request body for POST /facilities/nearest-by-location."""

    location: str = Field(min_length=2, description="Free-text location to geocode.")
    radius_km: float = Field(default=50.0, ge=1.0, le=200.0)
    level_min: int = Field(default=1, ge=1, le=6)
    required_services: list[str] | None = None
    max_results: int = Field(default=3, ge=1, le=10)


# ── Lifespan ────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_startup_config()
    if is_database_configured():
        await init_engine()
        tree = get_ball_tree()
        count = await tree.build()
        if count == 0:
            logger.warning(
                "No facilities loaded. Service is degraded — "
                "run load_facilities.py to import facility data."
            )
    else:
        logger.warning("DATABASE_URL not configured. Service running in degraded mode.")
    yield
    if is_database_configured():
        await close_engine()


app = FastAPI(title="Facility Mapper", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


# ── Health & readiness ──────────────────────────────────────────────────────


@app.get("/health")
async def health():
    """Service health with facility count and data currency.
    Always includes data_as_of so the dispatcher UI can display it.
    """
    tree = get_ball_tree()

    if not is_database_configured():
        return {
            "status": "degraded",
            "database": "not_configured",
            "facility_count": 0,
            "data_as_of": None,
            "last_loaded_at": None,
            "ball_tree_ready": False,
        }

    db_ok = await check_database()
    data_as_of = None
    last_loaded_at = None

    if db_ok:
        try:
            async with get_session() as session:
                result = await session.execute(
                    select(DataImport).order_by(DataImport.loaded_at.desc()).limit(1)
                )
                import_row = result.scalar_one_or_none()
                if import_row:
                    data_as_of = import_row.source
                    last_loaded_at = import_row.loaded_at.isoformat()
        except Exception as exc:
            logger.warning("Failed to query data imports: %s", exc)

    return {
        "status": "ok" if db_ok and tree.is_ready() else "degraded",
        "database": "ok" if db_ok else "error",
        "facility_count": tree.facility_count,
        "data_as_of": data_as_of,
        "last_loaded_at": last_loaded_at,
        "ball_tree_ready": tree.is_ready(),
    }


@app.get("/ready")
async def readiness():
    """K8s-style readiness probe. 503 until BallTree built and at least
    1 facility loaded.
    """
    tree = get_ball_tree()
    if tree.is_ready():
        return {"status": "ready", "facility_count": tree.facility_count}
    raise HTTPException(
        status_code=503,
        detail={
            "status": "not_ready",
            "message": "BallTree not built or no facilities loaded.",
        },
    )


# ── Facility search endpoints ───────────────────────────────────────────────


@app.get("/facilities/nearest")
async def facilities_nearest(
    lat: float = Query(..., description="Latitude of search origin"),
    lon: float = Query(..., description="Longitude of search origin"),
    radius_km: float = Query(50.0, ge=1.0, le=200.0, description="Search radius in km"),
    level_min: int = Query(1, ge=1, le=6, description="Minimum facility level"),
    required_services: str | None = Query(None, description="Comma-separated required services"),
    max_results: int = Query(3, ge=1, le=10, description="Max results"),
):
    """Find nearest facilities by coordinates.
    Returns FacilitySearchResponse from shared contracts.
    """
    services = None
    if required_services:
        services = [s.strip() for s in required_services.split(",") if s.strip()]

    response = await find_nearest_by_coords(
        lat=lat,
        lon=lon,
        level_min=level_min,
        required_services=services,
        radius_km=radius_km,
        max_results=max_results,
    )
    return response.model_dump()


@app.post("/facilities/nearest-by-location")
async def facilities_nearest_by_location(request: NearestByLocationRequest):
    """Find nearest facilities by text location. Geocodes using Nominatim
    then performs the same BallTree search as /facilities/nearest.
    """
    response = await find_nearest_by_location(
        location_text=request.location,
        level_min=request.level_min,
        required_services=request.required_services,
        radius_km=request.radius_km,
        max_results=request.max_results,
    )
    if response is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "geocoding_failed",
                "message": f"Could not geocode location: {request.location!r}. "
                "Try providing coordinates directly via /facilities/nearest.",
            },
        )
    return response.model_dump()


@app.get("/facilities/{facility_id}")
async def facility_detail(facility_id: str):
    """Single facility detail."""
    async with get_session() as session:
        result = await session.execute(select(Facility).where(Facility.facility_id == facility_id))
        facility = result.scalar_one_or_none()

    if facility is None:
        raise HTTPException(status_code=404, detail="Facility not found")

    tree = get_ball_tree()
    return {
        "facility": {
            "facility_id": facility.facility_id,
            "name": facility.name,
            "county": facility.county,
            "level": facility.level,
            "lat": facility.lat,
            "lon": facility.lon,
            "phone": facility.phone,
            "services": facility.services or [],
            "distance_km": 0.0,
            "eta_minutes": 0.0,
        },
        "data_as_of": tree.built_at.isoformat() if tree.built_at else None,
    }


@app.get("/data-currency")
async def data_currency():
    """Data import history for ops transparency."""
    async with get_session() as session:
        result = await session.execute(
            select(DataImport).order_by(DataImport.loaded_at.desc()).limit(20)
        )
        imports = [
            {
                "source": row.source,
                "record_count": row.record_count,
                "loaded_at": row.loaded_at.isoformat(),
                "loaded_by": row.loaded_by,
            }
            for row in result.scalars()
        ]

    current_source = imports[0]["source"] if imports else None
    return {"imports": imports, "current_source": current_source}


# ── Admin endpoints ─────────────────────────────────────────────────────────


@app.post("/admin/reload-facilities", dependencies=[Security(_require_admin_key)])
async def reload_facilities():
    """Clear in-process cache and rebuild BallTree from DB.
    Also clears geocoding cache.
    """
    clear_cache()
    tree = get_ball_tree()
    count = await tree.build()
    return {
        "status": "ok",
        "facility_count": count,
        "built_at": tree.built_at.isoformat() if tree.built_at else None,
    }
