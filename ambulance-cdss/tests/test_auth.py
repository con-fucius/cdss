"""tests/test_auth.py.

EPIC 9.1 — Tests for dispatcher authentication endpoints:
- POST /auth/dispatcher-login
- Session token generation and validation
- Development mode bypass
- Production mode credential validation

Uses the synchronous TestClient from FastAPI/Starlette to avoid
pytest-asyncio version compatibility issues with async fixtures.
"""

from __future__ import annotations

import hashlib
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


# ── Helpers ────────────────────────────────────────────────────────────────


def _hash_pin(pin: str, salt: str = "test-salt") -> str:
    """Produce a sha256:salt:hash string for test credentials."""
    h = hashlib.sha256(f"{salt}:{pin}".encode()).hexdigest()
    return f"sha256:{salt}:{h}"


# ── Tests ──────────────────────────────────────────────────────────────────


class TestDispatcherLogin:
    """POST /auth/dispatcher-login"""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        with TestClient(app) as c:
            self.client = c

    def test_development_mode_accepts_any_credentials(self):
        """In dev mode (no DISPATCHER_CREDENTIALS), any username+PIN works."""
        resp = self.client.post(
            "/auth/dispatcher-login",
            json={"username": "test-dispatcher", "pin": "1234"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dispatcher_id"] == "test-dispatcher"
        assert "session_token" in data
        assert data["role"] == "dispatcher"
        assert data["expires_in_hours"] > 0

    def test_development_mode_returns_valid_token(self):
        """Token returned in dev mode should be a non-empty string."""
        resp = self.client.post(
            "/auth/dispatcher-login",
            json={"username": "disp1", "pin": "0000"},
        )
        assert resp.status_code == 200
        token = resp.json()["session_token"]
        assert isinstance(token, str)
        assert len(token) > 10

    def test_production_valid_credentials_accepted(self):
        """With valid DISPATCHER_CREDENTIALS set, correct creds are accepted."""
        pin_hash = _hash_pin("1234", "testsalt")
        creds = json.dumps({"dispatcher1": {"pin_hash": pin_hash, "role": "dispatcher"}})
        with patch.dict(os.environ, {"DISPATCHER_CREDENTIALS": creds, "ENVIRONMENT": "production"}, clear=False):
            resp = self.client.post(
                "/auth/dispatcher-login",
                json={"username": "dispatcher1", "pin": "1234"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["dispatcher_id"] == "dispatcher1"
            assert data["role"] == "dispatcher"

    def test_production_invalid_credentials_rejected(self):
        """With valid DISPATCHER_CREDENTIALS set, wrong PIN returns 401."""
        pin_hash = _hash_pin("1234", "testsalt")
        creds = json.dumps({"dispatcher1": {"pin_hash": pin_hash, "role": "dispatcher"}})
        with patch.dict(os.environ, {"DISPATCHER_CREDENTIALS": creds, "ENVIRONMENT": "production"}, clear=False):
            resp = self.client.post(
                "/auth/dispatcher-login",
                json={"username": "dispatcher1", "pin": "9999"},
            )
            assert resp.status_code == 401
            assert resp.json()["detail"]["error"] == "invalid_credentials"

    def test_production_unknown_user_rejected(self):
        """With valid DISPATCHER_CREDENTIALS set, unknown user returns 401."""
        pin_hash = _hash_pin("1234", "testsalt")
        creds = json.dumps({"dispatcher1": {"pin_hash": pin_hash, "role": "dispatcher"}})
        with patch.dict(os.environ, {"DISPATCHER_CREDENTIALS": creds, "ENVIRONMENT": "production"}, clear=False):
            resp = self.client.post(
                "/auth/dispatcher-login",
                json={"username": "unknown", "pin": "1234"},
            )
            assert resp.status_code == 401

    def test_short_pin_rejected(self):
        """PIN must be at least 4 characters (Pydantic validation)."""
        resp = self.client.post(
            "/auth/dispatcher-login",
            json={"username": "disp1", "pin": "12"},
        )
        assert resp.status_code == 422

    def test_empty_username_rejected(self):
        """Username must be non-empty (Pydantic validation)."""
        resp = self.client.post(
            "/auth/dispatcher-login",
            json={"username": "", "pin": "1234"},
        )
        assert resp.status_code == 422
