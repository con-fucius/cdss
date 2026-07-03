"""facility-mapper/app/config.py.

Environment configuration for the Facility Mapper service.

Follows the same conventions as ambulance-cdss/app/config.py:
small, explicit, no magic defaults, validate_startup_config()
blocks production startup if critical config is missing.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_PATH)


def get_environment() -> str:
    """Current deployment environment: 'development', 'staging', or 'production'."""
    return os.getenv("ENVIRONMENT", "development").strip().lower()


def is_production() -> bool:
    """True when running in production mode."""
    return get_environment() == "production"


def is_database_configured() -> bool:
    """True when DATABASE_URL is set and non-empty."""
    return bool(os.getenv("DATABASE_URL", "").strip())


def get_database_url() -> str:
    """Async database URL for SQLAlchemy. Raises RuntimeError if not set."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and configure it.")
    return url


def get_db_pool_min() -> int:
    """Minimum number of connections in the async pool."""
    return int(os.getenv("DB_POOL_MIN", "1"))


def get_db_pool_max() -> int:
    """Maximum number of connections in the async pool."""
    return int(os.getenv("DB_POOL_MAX", "10"))


def get_admin_api_key() -> str:
    """API key required for admin endpoints (/admin/*).
    An empty string means admin endpoints are unrestricted — acceptable
    in development, must be set before production deployment.
    """
    return os.getenv("ADMIN_API_KEY", "").strip()


def get_allowed_origins() -> list[str]:
    """Comma-separated list of allowed CORS origins.
    Defaults to ['*'] when not set (development only).
    Set ALLOWED_ORIGINS to a comma-separated list of specific origins
    before production deployment.
    """
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def get_ambulance_speed_kmh() -> float:
    """Ambulance speed in km/h for ETA calculation.
    Default 60 is a realistic urban/rural Kenya average.
    Documented: not a magic number.
    """
    return float(os.getenv("AMBULANCE_SPEED_KMH", "60"))


def get_facility_cache_ttl_seconds() -> int:
    """Maximum age (seconds) of in-process BallTree cache before automatic
    rebuild on next request. 0 = no automatic expiry (manual reload only).
    """
    return int(os.getenv("FACILITY_CACHE_TTL_SECONDS", "3600"))


def get_geocoding_user_agent() -> str:
    """User agent string for Nominatim geocoding requests."""
    return os.getenv("GEOCODING_USER_AGENT", "ambulance-cdss-facility-mapper")


def get_geocoding_timeout_seconds() -> float:
    """Timeout in seconds for Nominatim geocoding HTTP requests."""
    return float(os.getenv("GEOCODING_TIMEOUT_SECONDS", "5"))


def validate_startup_config() -> None:
    """Hard assertions that must hold before the app is allowed to serve
    traffic in a non-development environment. Mirrors ambulance-cdss
    convention — fail loudly at startup, not silently at runtime.
    """
    if is_production():
        if not get_admin_api_key():
            raise RuntimeError(
                "ADMIN_API_KEY must be set before running in production. "
                "Admin endpoints (/admin/*) are unrestricted without it."
            )
        if get_allowed_origins() == ["*"]:
            raise RuntimeError(
                "ALLOWED_ORIGINS must be set to specific origins (not '*') "
                "before running in production."
            )
