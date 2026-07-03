import os
import unittest
from unittest.mock import patch

os.environ.setdefault("CDSS_AUDIT_DB_PATH", ":memory:")
os.environ.setdefault("CDSS_SESSION_STORAGE_BACKEND", "memory")
os.environ.setdefault("CDSS_RUN_MIGRATIONS_ON_STARTUP", "false")

from app.api import app
from app.logs import _hash_patient_ref
from fastapi.testclient import TestClient


class ApiSmokeTests(unittest.TestCase):
    def test_health_and_diseases_are_available_without_llm_key(self):
        with TestClient(app) as client:
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertIn(health.json()["status"], {"ok", "degraded"})

            diseases = client.get("/diseases")
            self.assertEqual(diseases.status_code, 200)
            self.assertTrue(diseases.json()["diseases"])
            first = diseases.json()["diseases"][0]
            self.assertIn("source_mode", first)
            self.assertIn("table_name", first)
            self.assertIn("chunk_count", first)
            self.assertIn("pageindex_rows", first)
            self.assertIn("pageindex_status", first)
            self.assertIn("graph_nodes", first)
            self.assertIn("graph_edges", first)
            self.assertIn("graph_status", first)

    def test_available_diseases_keeps_legacy_hiv_with_other_tables(self):
        from app.search_tools import SearchIndex

        idx = SearchIndex()
        available = idx.available_diseases()

        self.assertIn("hiv", available)

    def test_cross_disease_search_includes_legacy_hiv_documents(self):
        from app.search_tools import SearchIndex

        idx = SearchIndex()
        tables = idx._get_table_names(None)

        self.assertIn("documents", tables)

    def test_legacy_hiv_guideline_browser_is_queryable(self):
        with TestClient(app) as client:
            response = client.get("/guidelines/hiv/toc")
            self.assertEqual(response.status_code, 200)
            toc = response.json()["toc"]
            self.assertTrue(toc)
            self.assertLess(len(toc), 983)
            self.assertTrue(toc[0]["id"].startswith("legacy-page-"))
            self.assertIn("Page", toc[0]["title"])

            section = client.get(f"/guidelines/hiv/section/{toc[0]['id']}")
            self.assertEqual(section.status_code, 200)
            self.assertTrue(section.json()["text"])
            self.assertIn("page", section.json())
            self.assertIn("source_url", section.json())

    def test_admin_audit_requires_admin_role(self):
        with TestClient(app) as client:
            denied = client.get("/admin/audit")
            self.assertEqual(denied.status_code, 403)

            allowed = client.get("/admin/audit", headers={"X-User-Role": "ADMIN"})
            self.assertEqual(allowed.status_code, 200)
            self.assertIn("logs", allowed.json())

    def test_admin_sessions_requires_admin_role(self):
        with TestClient(app) as client:
            denied = client.get("/admin/sessions")
            self.assertEqual(denied.status_code, 403)

            allowed = client.get("/admin/sessions", headers={"X-User-Role": "ADMIN"})
            self.assertEqual(allowed.status_code, 200)
            self.assertIn("sessions", allowed.json())

    def test_admin_stats_exposes_runtime_totals(self):
        with TestClient(app) as client:
            response = client.get("/admin/stats", headers={"X-User-Role": "ADMIN"})
            self.assertEqual(response.status_code, 200)
            stats = response.json()["stats"]
            self.assertIn("users_total", stats)
            self.assertIn("audit_events_total", stats)
            self.assertIn("indexed_diseases_total", stats)
            self.assertIn("configured_diseases_total", stats)
            self.assertIn("pageindex_rows_total", stats)
            self.assertIn("session_storage_backend", stats)
            self.assertIn("audit_storage_backend", stats)
            self.assertIn("missing_diseases", stats)

    def test_admin_users_requires_admin_role(self):
        with TestClient(app) as client:
            denied = client.get("/admin/users")
            self.assertEqual(denied.status_code, 403)

    def test_stream_endpoint_returns_sse_events_in_kb_only_mode(self):
        session_id = "test-session"
        with TestClient(app) as client:
            with client.stream(
                "POST",
                "/chat/stream",
                json={
                    "session_id": session_id,
                    "message": "What is first line ART?",
                    "context": {
                        "active_conditions": ["hiv"],
                        "clinical_params": {},
                        "medications": [],
                    },
                },
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(response.iter_text())
                self.assertIn('"type": "chunk"', body)
                self.assertIn('"type": "sources"', body)
                self.assertIn('"type": "stream_end"', body)
            sessions = client.get("/admin/sessions", headers={"X-User-Role": "ADMIN"}).json()[
                "sessions"
            ]
            self.assertTrue(any(row["session_id"] == session_id for row in sessions))

    def test_patient_context_requires_medications_array(self):
        with TestClient(app) as client:
            response = client.post(
                "/chat/stream",
                json={
                    "session_id": "test-session",
                    "message": "What is first line ART?",
                    "context": {
                        "active_conditions": ["hiv"],
                        "clinical_params": {},
                        "medications": "TDF",
                    },
                },
            )
            self.assertEqual(response.status_code, 422)

    def test_pageindex_query_endpoint_is_available(self):
        with TestClient(app) as client:
            response = client.post(
                "/pageindex/query",
                json={"query": "malaria treatment", "disease": "malaria", "top_k": 2},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("results", response.json())

            stats = client.get("/pageindex/stats")
            self.assertEqual(stats.status_code, 200)
            self.assertIn("by_disease", stats.json())

    def test_patient_ref_hash_uses_salt(self):
        context = {
            "active_conditions": ["hiv"],
            "clinical_params": {"cd4_count": "250"},
            "medications": ["TDF", "DTG"],
        }
        with patch.dict("os.environ", {"CDSS_PATIENT_SALT": "0123456789abcdef"}, clear=False):
            first = _hash_patient_ref(context)
            second = _hash_patient_ref(context)
        with patch.dict("os.environ", {"CDSS_PATIENT_SALT": "abcdef0123456789"}, clear=False):
            changed = _hash_patient_ref(context)
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
