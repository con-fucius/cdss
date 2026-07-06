"""tests/test_sse_stream.py.

EPIC 9.1 — Tests for the SSE endpoint and event notification system.
Verifies that _notify_sse fires correctly and SSE stream is functional.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.main import _notify_sse, _sse_queues


class TestNotifySSE:
    """Unit tests for the _notify_sse helper function."""

    def test_notify_empty_queues_no_crash(self):
        """Notifying an incident with no connected clients should not crash."""
        _notify_sse("nonexistent-incident", "vitals_added", {"incident_id": "test"})

    def test_notify_delivers_to_connected_queue(self):
        """Message is delivered to a connected queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=10)
        _sse_queues["test-incident"] = [q]

        _notify_sse("test-incident", "vitals_added", {"incident_id": "test-incident"})

        assert not q.empty()
        msg = q.get_nowait()
        assert "event: vitals_added" in msg
        assert "test-incident" in msg
        # Cleanup
        _sse_queues.pop("test-incident", None)

    def test_notify_removes_full_queues(self):
        """Full queues are removed from the list (dead-queue cleanup)."""
        full_q: asyncio.Queue = asyncio.Queue(maxsize=1)
        full_q.put_nowait("existing")
        _sse_queues["test-full"] = [full_q]

        _notify_sse("test-full", "vitals_added", {"incident_id": "test-full"})

        assert full_q not in _sse_queues.get("test-full", [])
        _sse_queues.pop("test-full", None)

    def test_notify_multiple_queues(self):
        """Message is delivered to all connected queues for an incident."""
        q1: asyncio.Queue = asyncio.Queue(maxsize=10)
        q2: asyncio.Queue = asyncio.Queue(maxsize=10)
        _sse_queues["test-multi"] = [q1, q2]

        _notify_sse("test-multi", "medication_added", {"drug": "adrenaline"})

        assert not q1.empty()
        assert not q2.empty()
        msg1 = q1.get_nowait()
        msg2 = q2.get_nowait()
        assert "medication_added" in msg1
        assert "medication_added" in msg2
        _sse_queues.pop("test-multi", None)

    def test_notify_json_encodes_data(self):
        """Data payload is JSON-encoded in the SSE message."""
        q: asyncio.Queue = asyncio.Queue(maxsize=10)
        _sse_queues["test-json"] = [q]

        payload = {"incident_id": "abc", "nested": {"key": "value"}}
        _notify_sse("test-json", "status_changed", payload)

        msg = q.get_nowait()
        assert "event: status_changed" in msg
        # Extract the data line
        for line in msg.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                assert data["incident_id"] == "abc"
                assert data["nested"]["key"] == "value"
                break
        else:
            pytest.fail("No data: line found in SSE message")
        _sse_queues.pop("test-json", None)

    def test_notify_unknown_incident_no_crash(self):
        """Notifying an incident not in _sse_queues should not crash."""
        _notify_sse("completely-unknown", "field_log_added", {})
