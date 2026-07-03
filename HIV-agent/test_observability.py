import unittest
from unittest.mock import patch
import os

from fastapi.testclient import TestClient

os.environ.setdefault("CDSS_AUDIT_DB_PATH", ":memory:")
os.environ.setdefault("CDSS_SESSION_STORAGE_BACKEND", "memory")
os.environ.setdefault("CDSS_RUN_MIGRATIONS_ON_STARTUP", "false")

from app.api import app


class ObservabilityTests(unittest.TestCase):
    def test_metrics_endpoint_exposes_counters(self):
        with TestClient(app) as client:
            response = client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertIn("cdss_requests_total", response.text)
        self.assertIn("cdss_request_duration_seconds_sum", response.text)
        self.assertIn("cdss_rate_limit_hits_total", response.text)
        self.assertIn("cdss_rate_limited_total", response.text)

    def test_chat_rate_limit_returns_429(self):
        payload = {
            "session_id": "rate-limit-session",
            "message": "What is first line ART?",
            "context": {"active_conditions": ["hiv"], "clinical_params": {}, "medications": []},
        }
        with patch.dict("os.environ", {"CDSS_CHAT_RATE_LIMIT_PER_MIN": "1"}, clear=False):
            with TestClient(app) as client:
                first = client.post(
                    "/chat/stream",
                    json=payload,
                    headers={"X-Session-Id": "rate-limit-session"},
                )
                second = client.post(
                    "/chat/stream",
                    json=payload,
                    headers={"X-Session-Id": "rate-limit-session"},
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers.get("Retry-After"), "60")


if __name__ == "__main__":
    unittest.main()
