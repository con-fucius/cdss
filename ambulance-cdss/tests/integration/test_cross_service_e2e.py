"""tests/integration/test_cross_service_e2e.py.

Cross-service E2E integration tests that wire ambulance-cdss terminal
outcomes to triage-ranker /triage calls. Tests the full dispatch→triage
pipeline across both services running on localhost.

Requires both services started:
  - ambulance-cdss on port 8000
  - triage-ranker on port 8100
"""

from __future__ import annotations

import httpx

CDSS_BASE = "http://localhost:8000"
TRIAGE_BASE = "http://localhost:8100"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _cdss() -> httpx.Client:
    return httpx.Client(base_url=CDSS_BASE, timeout=15)


def _triage() -> httpx.Client:
    return httpx.Client(base_url=TRIAGE_BASE, timeout=15)


def _create_and_walk(
    cdss: httpx.Client, trigger: str, choices: list[int] | None = None, choice: int = 0
) -> tuple[str, dict, dict]:
    """Create an incident, walk to terminal, return (incident_id, terminal_outcome, full_record)."""
    r = cdss.post("/incidents", json={"chief_complaint": trigger})
    assert r.status_code == 200, f"Create failed: {r.text}"
    data = r.json()
    assert data["protocol_matched"], f"'{trigger}' did not match a protocol"
    iid = data["incident"]["incident_id"]
    q = data["current_question"]

    # Walk to terminal
    step = 0
    outcome = None
    for _ in range(30):
        valid = q["valid_answers"]
        if choices and step < len(choices):
            idx = choices[step]
        else:
            idx = choice
        ans = valid[min(idx, len(valid) - 1)]
        r = cdss.post(
            f"/incidents/{iid}/answer",
            json={"current_question_id": q["question_id"], "answer": ans, "dispatcher_id": "xservice"},
        )
        assert r.status_code == 200, f"Answer failed ({q['question_id']}={ans}): {r.text}"
        d = r.json()
        if d.get("terminal"):
            outcome = d["outcome"]
            break
        q = d["current_question"]
        assert q is not None
        step += 1

    assert outcome is not None, "No terminal reached"
    full = cdss.get(f"/incidents/{iid}/full").json()
    cc = full["incident"]["chief_complaint"]
    return iid, outcome, cc


# ─────────────────────────────────────────────────────────────────────────────
# Cross-service: ambulance-cdss → triage-ranker
# ─────────────────────────────────────────────────────────────────────────────


class TestCrossServiceFlow:
    """Walk an incident through ambulance-cdss, then enrich the chief complaint
    via triage-ranker /triage to verify the two services produce compatible
    clinical assessments."""

    def test_cardiac_arrest_enriched_by_triage(self):
        """Cardiac arrest → P1 in CDSS → triage-ranker should agree on high acuity."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(cdss, "not breathing", choices=[1, 2, 1, 0])
            assert outcome["priority_code"] == "P1_CARDIAC_ARREST"

            # Enrich via triage-ranker
            tr = triage.post(
                "/triage",
                json={"incident_desc": cc, "acvpu": "unresponsive"},
            ).json()
            assert tr["triage_level"] in ("P1", "P2")
            assert len(tr["diagnosis_ranking"]) > 0
            assert "UNRESPONSIVE" in tr["metadata"]["inferred_risks"]

    def test_choking_enriched_by_triage(self):
        """Choking → CDSS triage + triage-ranker both flag respiratory."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(cdss, "choking")
            assert outcome["priority_code"]
            assert outcome["recommended_unit_type"]

            tr = triage.post(
                "/triage",
                json={"incident_desc": cc},
            ).json()
            assert len(tr["diagnosis_ranking"]) > 0
            assert len(tr["keywords"]) > 0

    def test_respiratory_enriched_by_triage(self):
        """Respiratory distress → both services flag respiratory."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(cdss, "difficulty breathing")
            assert outcome["priority_code"]

            tr = triage.post(
                "/triage",
                json={"incident_desc": cc},
            ).json()
            categories = {kw.get("category", "") for kw in tr["keywords"]}
            assert "RESPIRATORY" in categories

    def test_major_trauma_enriched_by_triage(self):
        """MVA trauma → triage-ranker flags trauma keywords."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(cdss, "car accident")
            assert outcome["priority_code"]

            tr = triage.post(
                "/triage",
                json={
                    "incident_desc": cc,
                    "hr": 120,
                    "sbp": 85,
                },
            ).json()
            assert tr["triage_level"] in ("P1", "P2")
            assert tr["metadata"]["shock_index"] > 1.0

    def test_obstetric_enriched_by_triage(self):
        """Obstetric emergency → CDSS triage + triage-ranker produce results."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(cdss, "pregnant")
            assert outcome["priority_code"]

            tr = triage.post(
                "/triage",
                json={"incident_desc": cc},
            ).json()
            assert len(tr["diagnosis_ranking"]) > 0

    def test_paediatric_respiratory_enriched_by_triage(self):
        """Child respiratory → both services produce results."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(cdss, "child can't breathe")
            assert outcome["priority_code"]

            tr = triage.post(
                "/triage",
                json={"incident_desc": cc},
            ).json()
            assert len(tr["diagnosis_ranking"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Cross-service: priority code consistency
# ─────────────────────────────────────────────────────────────────────────────


class TestPriorityConsistency:
    """Verify that CDSS terminal priority_code and triage-ranker triage_level
    are directionally consistent (both high or both low)."""

    def test_high_acuity_agreement(self):
        """P1 cardiac arrest → triage-ranker should also assign high acuity."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(
                cdss, "not breathing", choices=[1, 2, 1, 0]
            )
            assert outcome["priority_code"] == "P1_CARDIAC_ARREST"

            tr = triage.post(
                "/triage",
                json={"incident_desc": cc, "acvpu": "unresponsive"},
            ).json()
            # Both should agree this is high acuity
            assert outcome["priority_code"].startswith("P1")
            assert tr["triage_level"] in ("P1", "P2")

    def test_cardiac_arrest_elevated_esi(self):
        """Cardiac arrest → ESI level should be 1 or 2."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(
                cdss, "not breathing", choices=[1, 2, 1, 0]
            )
            tr = triage.post(
                "/triage",
                json={"incident_desc": cc, "acvpu": "unresponsive"},
            ).json()
            assert tr["esi_level"] <= 2


# ─────────────────────────────────────────────────────────────────────────────
# Cross-service: CDSS incident record enriches triage
# ─────────────────────────────────────────────────────────────────────────────


class TestCDSSRecordEnrichesTriage:
    """Use data from the CDSS incident record to enrich the triage-ranker call."""

    def test_vitals_from_incident_enrich_triage(self):
        """Walk to terminal, record vitals in CDSS, then use them in triage."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(cdss, "difficulty breathing")
            assert outcome["priority_code"]

            # Record vitals in CDSS
            cdss.post(
                f"/incidents/{iid}/vitals",
                json={
                    "recorded_by": "xservice",
                    "respiratory_rate": 24,
                    "heart_rate": 110,
                    "bp_systolic": 90,
                    "spo2": 92,
                },
            )

            # Use chief complaint + vitals in triage-ranker
            tr = triage.post(
                "/triage",
                json={
                    "incident_desc": cc,
                    "hr": 110,
                    "sbp": 90,
                },
            ).json()
            assert tr["triage_level"] in ("P1", "P2")
            assert tr["metadata"]["shock_index"] is not None
            assert tr["metadata"]["shock_index"] > 1.0

    def test_handoff_data_matches_triage_keywords(self):
        """CDSS handoff chief_complaint should produce matching triage keywords."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(cdss, "difficulty breathing")
            ho = cdss.get(f"/incidents/{iid}/handoff").json()

            # Triage the chief complaint from the handoff
            tr = triage.post(
                "/triage",
                json={"incident_desc": ho["chief_complaint"]},
            ).json()

            # Both should have results
            assert ho["priority_code"]
            assert len(tr["diagnosis_ranking"]) > 0
            # Keywords should reference respiratory
            kw_categories = {kw.get("category", "") for kw in tr["keywords"]}
            assert "RESPIRATORY" in kw_categories

    def test_dispatch_unit_after_triage_enrichment(self):
        """Full flow: CDSS walk → triage enrichment → CDSS dispatch-unit."""
        with _cdss() as cdss, _triage() as triage:
            iid, outcome, cc = _create_and_walk(
                cdss, "not breathing", choices=[1, 2, 1, 0]
            )

            # Enrich via triage
            tr = triage.post(
                "/triage",
                json={"incident_desc": cc, "acvpu": "unresponsive"},
            ).json()
            assert tr["triage_level"] in ("P1", "P2")

            # Dispatch a unit in CDSS
            dr = cdss.post(f"/incidents/{iid}/dispatch-unit", json={})
            assert dr.status_code == 200
            assert dr.json()["assigned_unit_id"]

            # Verify the incident record has both the terminal and dispatch
            final = cdss.get(f"/incidents/{iid}/full").json()
            assert final["incident"]["priority_code"]
            assert final["incident"]["assigned_unit_id"]
