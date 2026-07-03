"""app/config.py.

Environment configuration for the Ambulance CDSS.

Deliberately small. This system has no LLM provider configuration in its
core path (Mode 1 dispatch scripts are deterministic; Mode 2 guidance
lookup is a narrow bounded feature added later — see docs/PHASE_STATUS.md).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_PATH)


def get_environment() -> str:
    return os.getenv("ENVIRONMENT", "development").strip().lower()


def is_production() -> bool:
    return get_environment() == "production"


def is_database_configured() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and configure it.")
    return url


def get_db_pool_min() -> int:
    return int(os.getenv("DB_POOL_MIN", "1"))


def get_db_pool_max() -> int:
    return int(os.getenv("DB_POOL_MAX", "10"))


def get_incident_retention_days() -> int:
    """Days after incident closure before PII fields are purged.
    Resolved per Phase 1.9: 30 days. 0 would mean automatic purge is
    disabled — no longer the default; only relevant if a deployment
    deliberately overrides INCIDENT_RETENTION_DAYS back to 0, which
    validate_startup_config() still refuses in production.
    """
    return int(os.getenv("INCIDENT_RETENTION_DAYS", "30"))


def get_facility_registry_config() -> dict:
    return {
        "base_url": os.getenv("FACILITY_REGISTRY_BASE_URL", "").strip(),
        "api_key": os.getenv("FACILITY_REGISTRY_API_KEY", "").strip(),
        "timeout_seconds": float(os.getenv("FACILITY_REGISTRY_TIMEOUT_SECONDS", "5")),
    }


def get_emergency_dispatch_config() -> dict:
    return {
        "base_url": os.getenv("EMERGENCY_DISPATCH_BASE_URL", "").strip(),
        "api_key": os.getenv("EMERGENCY_DISPATCH_API_KEY", "").strip(),
        "timeout_seconds": float(os.getenv("EMERGENCY_DISPATCH_TIMEOUT_SECONDS", "5")),
    }


def get_prehospital_formulary() -> list[str]:
    """DEPRECATED — Phase 0.5 was resolved as: log every relevant drug/item
    a unit carries or considers, regardless of whether it was
    administered, with no allowlist gate. See
    app/main.py::add_incident_medication for the current (ungated)
    behaviour. This function and its backing env var
    (PREHOSPITAL_FORMULARY) are retained only so GET /formulary can keep
    returning a non-error response for any client still polling it.
    """
    raw = os.getenv("PREHOSPITAL_FORMULARY", "").strip()
    if not raw:
        return []
    return [d.strip() for d in raw.split(",") if d.strip()]


def is_formulary_configured() -> bool:
    return len(get_prehospital_formulary()) > 0


def get_rate_limit_chat_per_minute() -> int:
    return int(os.getenv("RATE_LIMIT_CHAT_PER_MINUTE", "60"))


def get_rate_limit_default_per_minute() -> int:
    return int(os.getenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "120"))


def get_answer_correction_window_seconds() -> int:
    """Seconds after submitting a dispatch answer during which the dispatcher
    can correct it via PATCH /incidents/{id}/answer/{log_id}.
    Improvement 4.2 — configurable, default 60.
    """
    return int(os.getenv("ANSWER_CORRECTION_WINDOW_SECONDS", "60"))


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


def get_triage_ranker_config() -> dict:
    return {
        "base_url": os.getenv("TRIAGE_RANKER_BASE_URL", "").strip(),
        "api_key": os.getenv("TRIAGE_RANKER_API_KEY", "").strip(),
        "timeout_seconds": float(os.getenv("TRIAGE_RANKER_TIMEOUT_SECONDS", "5")),
    }


def validate_startup_config() -> None:
    """Hard assertions that must hold before the app is allowed to serve traffic
    in a non-development environment. Mirrors the discipline already
    established in the chronic-disease CDSS (CDSS_PATIENT_SALT assertion
    pattern) — fail loudly at startup, not silently at runtime.
    """
    if is_production():
        if get_incident_retention_days() == 0:
            raise RuntimeError(
                "INCIDENT_RETENTION_DAYS must be set to a positive value before "
                "running in production. See docs/PHASE_STATUS.md item 1.9."
            )
        fr = get_facility_registry_config()
        ed = get_emergency_dispatch_config()
        if not fr["base_url"] or not ed["base_url"]:
            raise RuntimeError(
                "FACILITY_REGISTRY_BASE_URL and EMERGENCY_DISPATCH_BASE_URL must "
                "be set before running in production. See docs/PHASE_STATUS.md "
                "items 0.3 and 0.4."
            )
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
