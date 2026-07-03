"""app/external/triage_ranker.py.

Live client for the Triage Ranker service.

Same pattern as FacilityRegistryClient and EmergencyDispatchClient:
returns None/empty on failure, never raises, every external call
wrapped in try/except. Called as asyncio.create_task() in
create_incident — non-blocking.

Integration plan from IMPLEMENTATION PLAN Phase 2.7:
- enrich(incident_desc, gcs_score, acvpu, sbp, hr) -> Optional[TriageEnrichment]
- TriageEnrichment dataclass: triage_level, esi_level, top_diagnosis,
  icd10_code, snomed_code, shock_index, degraded_mode
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..config import get_triage_ranker_config
from ..retry import async_retry, with_timeout

logger = logging.getLogger(__name__)


@dataclass
class TriageEnrichment:
    """Structured triage enrichment result from the Triage Ranker service."""

    triage_level: str  # "P1", "P2", "P3", "P4"
    esi_level: int  # 1-5
    top_diagnosis: str
    icd10_code: str | None
    snomed_code: str | None
    shock_index: float | None
    degraded_mode: bool


class TriageRankerClient:
    """HTTP client for the Triage Ranker service (port 8100)."""

    def __init__(self) -> None:
        self._config = get_triage_ranker_config()

    def _configured(self) -> bool:
        return bool(self._config["base_url"])

    async def enrich(
        self,
        incident_desc: str,
        gcs_score: int | None = None,
        acvpu: str | None = None,
        sbp: int | None = None,
        hr: int | None = None,
    ) -> TriageEnrichment | None:
        """Enrich an incident with structured clinical triage.

        Returns TriageEnrichment on success, None on any failure.
        Never raises — callers must treat None as "enrichment not
        available, proceed without it".

        Called as asyncio.create_task() in create_incident —
        the incident endpoint returns before this resolves.
        """
        if not self._configured():
            logger.debug(
                "TriageRankerClient not configured "
                "(TRIAGE_RANKER_BASE_URL unset). Skipping enrichment."
            )
            return None

        payload = {"incident_desc": incident_desc}
        if gcs_score is not None:
            payload["gcs_score"] = gcs_score
        if acvpu is not None:
            payload["acvpu"] = acvpu
        if sbp is not None:
            payload["sbp"] = sbp
        if hr is not None:
            payload["hr"] = hr

        try:
            async with httpx.AsyncClient(
                base_url=self._config["base_url"],
                headers=self._auth_headers(),
            ) as client:

                async def _call():
                    return await client.post("/triage", json=payload)

                response = await with_timeout(
                    async_retry(_call, max_attempts=2),
                    self._config["timeout_seconds"],
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.debug("TriageRankerClient.enrich failed: %s", exc)
            return None

        # Extract top diagnosis from ranking
        ranking = data.get("diagnosis_ranking", [])
        top_diagnosis = ranking[0]["canonical_name"] if ranking else "Undifferentiated"
        icd10_code = ranking[0].get("icd10_code") if ranking else None
        snomed_code = ranking[0].get("snomed_code") if ranking else None

        return TriageEnrichment(
            triage_level=data.get("triage_level", "P2"),
            esi_level=data.get("esi_level", 3),
            top_diagnosis=top_diagnosis,
            icd10_code=icd10_code,
            snomed_code=snomed_code,
            shock_index=data.get("metadata", {}).get("shock_index"),
            degraded_mode=data.get("degraded_mode", False),
        )

    def _auth_headers(self) -> dict:
        """Authentication headers if API key is configured."""
        if self._config.get("api_key"):
            return {"Authorization": f"Bearer {self._config['api_key']}"}
        return {}
