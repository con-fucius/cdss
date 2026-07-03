"""
Shared OpenAI-compatible LLM provider utilities for CDSS.

Mistral support is intentionally deprecated here. Runtime chat uses the
OpenAI-compatible pathway so development can run against Puter and production
can switch providers without rewriting the clinical retrieval layer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

load_dotenv(ROOT_DIR / ".env", override=False)
load_dotenv(APP_DIR / ".env", override=False)


def get_llm_provider() -> str:
    """Return the configured LLM provider key."""
    provider = os.getenv("QUERY_LLM_PROVIDER", "").strip().lower()
    if provider:
        return provider
    return "groq"


def get_llm_model() -> str:
    """Return the model string for the active provider."""
    provider = get_llm_provider()
    if provider == "groq":
        model = os.getenv("QUERY_LLM_MODEL", "").strip()
        if not model:
            return "qwen/qwen3-32b"
        return model
    if provider == "puter":
        return (
            os.getenv("PUTER_MODEL", "").strip()
            or os.getenv("QUERY_LLM_MODEL", "").strip()
            or "openai/gpt-4o-mini"
        )
    return os.getenv("QUERY_LLM_MODEL", "openai/gpt-4o-mini")


def provider_has_credentials(provider: Optional[str] = None) -> bool:
    p = provider or get_llm_provider()
    if p == "groq":
        return bool(os.getenv("GROQ_API_KEY"))
    if p == "puter":
        return bool(os.getenv("PUTER_AUTH_TOKEN"))
    return False


def provider_models_url(provider: str) -> Optional[str]:
    if provider == "puter":
        # Puter's OpenAI-compatible path is used for chat completions, but the
        # `/models` compatibility endpoint can return 404. Do not use it as a
        # reachability gate or valid Puter tokens get incorrectly downgraded.
        return None
    return {
        "groq": "https://api.groq.com/openai/v1/models",
    }.get(provider)


def provider_auth_header(provider: str) -> Dict[str, str]:
    if provider == "groq":
        return {"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"}
    if provider == "puter":
        return {"Authorization": f"Bearer {os.getenv('PUTER_AUTH_TOKEN', '')}"}
    return {}


def provider_chat_endpoint(provider: str) -> str:
    return {
        "groq": "https://api.groq.com/openai/v1/chat/completions",
        "puter": os.getenv(
            "PUTER_OPENAI_BASE_URL",
            "https://api.puter.com/puterai/openai/v1",
        ).rstrip("/") + "/chat/completions",
    }.get(provider, "")


def provider_offline() -> bool:
    return not provider_has_credentials()
