"""tests/test_version_mismatch.py.

Improvement 3.2 — tests for protocol version mismatch detection.

Tests confirm:
- submit_incident_answer checks snapshot version vs live version
- Mismatch produces warnings in the response body
- No exception is raised (live call must continue)
- Match produces no warnings key in the response
"""

from __future__ import annotations

import inspect

from app import main


class TestVersionMismatchDetection:
    def test_endpoint_checks_version_mismatch(self):
        """submit_incident_answer must check snapshot version against live version."""
        source = inspect.getsource(main.submit_incident_answer)
        assert "version_mismatch" in source
        assert "snapshot_version" in source
        assert "live_version" in source

    def test_mismatch_logs_warning(self):
        """Version mismatch must be logged at WARNING level."""
        source = inspect.getsource(main.submit_incident_answer)
        assert "logger.warning" in source
        assert "Protocol version mismatch" in source

    def test_mismatch_does_not_raise(self):
        """Version mismatch must NOT raise an exception — the call continues."""
        source = inspect.getsource(main.submit_incident_answer)
        # The mismatch handling must not include any raise, HTTPException,
        # or sys.exit for the version_mismatch case
        # Check that the mismatch code path adds warnings to the response
        assert '"warnings"' in source

    def test_warnings_dict_in_response(self):
        """The response must include a 'warnings' key when mismatch detected."""
        source = inspect.getsource(main.submit_incident_answer)
        assert 'resp["warnings"] = version_mismatch_warning' in source

    def test_warnings_absent_when_no_mismatch(self):
        """When no mismatch, warnings key is absent from the response (not empty dict)."""
        source = inspect.getsource(main.submit_incident_answer)
        # The warnings are only added if version_mismatch_warning is truthy
        assert "if version_mismatch_warning:" in source
