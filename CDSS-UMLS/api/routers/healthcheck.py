"""Health check endpoint."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str


@router.get("/", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="healthy", timestamp=datetime.utcnow(), version="1.0.0")


@router.get("/ready")
async def readiness_check():
    """Readiness check - verify database connectivity."""
    # TODO: Add database connectivity check
    return {"status": "ready"}


@router.get("/live")
async def liveness_check():
    """Liveness check."""
    return {"status": "alive"}
