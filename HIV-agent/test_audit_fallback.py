import asyncio
import unittest
from unittest.mock import AsyncMock, patch


class AuditFallbackTests(unittest.TestCase):
    def test_postgres_write_failure_falls_back_to_sqlite_audit_path(self):
        from app.logs import write_audit_log

        with patch("app.config.get_audit_storage_backend", return_value="postgres"):
            with patch("app.logs.get_audit_storage_backend", return_value="postgres"):
                with patch(
                    "app.repositories.write_audit_log_db",
                    new=AsyncMock(side_effect=RuntimeError("db unavailable")),
                ) as postgres_write:
                    with patch("app.logs._write_audit_log") as sqlite_write:
                        asyncio.run(
                            write_audit_log(
                                event_type="query",
                                session_id="session-1",
                                query_id="query-1",
                                disease="hiv",
                                feedback_type="",
                                data={"ok": True},
                            )
                        )

        postgres_write.assert_awaited_once()
        sqlite_write.assert_called_once()
        fallback_payload = sqlite_write.call_args.args[5]
        self.assertEqual(fallback_payload["audit_backend_fallback"], "sqlite")
        self.assertIn("db unavailable", fallback_payload["audit_backend_error"])

    def test_postgres_read_success_reports_backend(self):
        from app.logs import read_audit_logs_async

        with patch("app.logs.get_audit_storage_backend", return_value="postgres"), patch(
            "app.repositories.read_audit_logs_db",
            new=AsyncMock(return_value={"logs": [], "total": 0, "page": 1, "limit": 50}),
        ):
            result = asyncio.run(read_audit_logs_async())

        self.assertEqual(result["storage_backend"], "postgres")

    def test_postgres_read_failure_reports_sqlite_fallback(self):
        from app.logs import read_audit_logs_async

        with patch("app.logs.get_audit_storage_backend", return_value="postgres"), patch(
            "app.repositories.read_audit_logs_db",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ), patch(
            "app.logs._read_audit_logs_sqlite",
            return_value={"logs": [], "total": 0, "page": 1, "limit": 50},
        ):
            result = asyncio.run(read_audit_logs_async())

        self.assertEqual(result["storage_backend"], "sqlite_fallback")
        self.assertIn("db unavailable", result["backend_error"])


if __name__ == "__main__":
    unittest.main()
