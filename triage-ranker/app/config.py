"""
triage-ranker/app/config.py

Environment configuration for the Triage Ranker service.
Follows ambulance-cdss conventions — small, explicit, no magic defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_PATH)


def get_environment() -> str:
    """Current deployment environment."""
    return os.getenv("ENVIRONMENT", "development").strip().lower()


def is_production() -> bool:
    """True when running in production mode."""
    return get_environment() == "production"


def get_admin_api_key() -> str:
    """API key required for admin endpoints."""
    return os.getenv("ADMIN_API_KEY", "").strip()


def get_allowed_origins() -> list[str]:
    """Comma-separated CORS origins. Empty = '*' (dev only)."""
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def get_spacy_model_path() -> str:
    """
    Path to the spaCy en_core_web_md model.
    Must be pre-installed (baked into Docker image).
    Never downloaded at runtime — Kenya's network cannot be assumed.
    """
    return os.getenv("SPACY_MODEL_PATH", "en_core_web_md")


def get_umls_api_key() -> str:
    """
    UMLS API key for L3 resolution. Empty string = not configured.
    When not configured, only L4 fallback rules are used (degraded mode).
    """
    return os.getenv("UMLS_API_KEY", "").strip()


def is_umls_configured() -> bool:
    """True when UMLS API key is set."""
    return bool(get_umls_api_key())


def get_umls_api_timeout() -> float:
    """Timeout in seconds for UMLS REST API calls."""
    return float(os.getenv("UMLS_API_TIMEOUT_SECONDS", "3"))


def get_clinical_rules_path() -> str:
    """Path to clinical_rules.yaml."""
    return os.getenv("CLINICAL_RULES_PATH", "app/rules/clinical_rules.yaml")


def get_umls_cache_db_path() -> str:
    """Path to SQLite database for L2 persistent cache."""
    return os.getenv("UMLS_CACHE_DB_PATH", "cache/umls_cache.db")


def validate_startup_config() -> None:
    """Hard assertions for production startup."""
    if is_production():
        if not get_admin_api_key():
            raise RuntimeError(
                "ADMIN_API_KEY must be set before running in production."
            )
        if get_allowed_origins() == ["*"]:
            raise RuntimeError(
                "ALLOWED_ORIGINS must be set to specific origins (not '*') "
                "before running in production."
            )
