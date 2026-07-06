"""app/auth.py.

Epic 6 — Authentication & session management.

HMAC-signed session tokens for dispatchers and paramedics.
In development mode, auth is bypassed — any dispatcher_id is accepted.
In production, credentials must be configured via DISPATCHER_CREDENTIALS.

Tokens use HMAC-SHA256 with expiry, same pattern as handoff_link.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass

from .config import get_dispatcher_credentials, get_handoff_signing_key, get_session_token_expiry_hours

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    user_id: str
    role: str  # "dispatcher" | "paramedic"
    issued_at: float
    expires_at: float


def _hash_pin(pin: str, salt: str = "") -> str:
    """SHA256 hash of PIN with optional salt. Used for credential storage."""
    return hashlib.sha256(f"{salt}:{pin}".encode()).hexdigest()


def verify_credentials(username: str, pin: str) -> dict | None:
    """Verify dispatcher credentials against configured list.

    Returns {"username": str, "role": str} on success, None on failure.
    In development mode (no credentials configured), always succeeds.
    """
    credentials = get_dispatcher_credentials()
    if not credentials:
        # Development mode — accept any credentials
        return {"username": username, "role": "dispatcher"}

    entry = credentials.get(username)
    if entry is None:
        return None

    pin_hash = entry.get("pin_hash", "")
    # Support "sha256:salt:hash" format
    if pin_hash.startswith("sha256:"):
        parts = pin_hash.split(":", 2)
        if len(parts) == 3:
            expected = _hash_pin(pin, parts[1])
            if hmac.compare_digest(expected, parts[2]):
                return {"username": username, "role": entry.get("role", "dispatcher")}
    else:
        # Plain comparison (dev only — production should use hashed pins)
        if hmac.compare_digest(pin_hash, pin):
            return {"username": username, "role": entry.get("role", "dispatcher")}

    return None


def generate_session_token(user_id: str, role: str = "dispatcher") -> str:
    """Generate an HMAC-signed session token with expiry."""
    signing_key = get_handoff_signing_key()
    now = time.time()
    expiry_hours = get_session_token_expiry_hours()
    expires_at = now + (expiry_hours * 3600)

    payload = json.dumps({"uid": user_id, "role": role, "iat": now, "exp": expires_at})
    signature = hmac.new(signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()

    encoded_payload = base64.urlsafe_b64encode(payload.encode()).decode()
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str) -> SessionInfo | None:
    """Verify a session token. Returns SessionInfo on success, None on failure."""
    try:
        encoded_payload, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded_payload.encode()).decode()
        signing_key = get_handoff_signing_key()

        expected_sig = hmac.new(signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            return None

        data = json.loads(payload)
        if time.time() > data.get("exp", 0):
            return None

        return SessionInfo(
            user_id=data["uid"],
            role=data.get("role", "dispatcher"),
            issued_at=data.get("iat", 0),
            expires_at=data.get("exp", 0),
        )
    except Exception:
        return None


def get_session_role(token: str) -> str | None:
    """Extract the role from a session token without full verification.
    Returns the role string or None if the token is malformed.
    For security-critical checks, use verify_session_token() instead.
    """
    try:
        encoded_payload, _signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded_payload.encode()).decode()
        data = json.loads(payload)
        return data.get("role")
    except Exception:
        return None
