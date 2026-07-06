"""app/external/nlp_extractor.py.

EPIC 1.2 — External NLP entity extraction client.

Follows the same degraded-mode pattern as FacilityRegistryClient and
EmergencyDispatchClient: if the external service is unconfigured or
unreachable, returns None rather than raising into the caller. The
backend's inline regex extraction (main.py::extract_entities) serves
as the degraded-mode fallback.

This client wraps a hypothetical external NLP service that provides
higher-quality entity extraction (chief complaint, location, vitals,
medications, allergies) from unstructured call transcripts. When
unconfigured, the system falls back to the local regex patterns in
main.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..config import get_triage_ranker_config

logger = logging.getLogger(__name__)


@dataclass
class ExtractedEntities:
    """Structured result from NLP entity extraction."""

    chief_complaint: str | None = None
    location_text: str | None = None
    lat: float | None = None
    lon: float | None = None
    vitals: dict | None = None
    medications_mentioned: list[str] | None = None
    allergies_mentioned: list[str] | None = None
    confidence: float = 0.0
    degraded_mode: bool = True


class NLPExtractorClient:
    """Client for external NLP entity extraction service.

    Uses the TRIAGE_RANKER_BASE_URL config as the service endpoint.
    When unconfigured or unreachable, returns None — the caller falls
    back to local regex extraction.
    """

    def __init__(self) -> None:
        self._config = get_triage_ranker_config()

    async def extract(self, transcript: str) -> ExtractedEntities | None:
        """Extract entities from a call transcript.

        Returns None if the service is unconfigured or unreachable,
        allowing the caller to degrade to local regex extraction.
        """
        base_url = self._config.get("base_url", "")
        if not base_url:
            logger.debug("NLPExtractorClient: service not configured")
            return None

        try:
            async with httpx.AsyncClient(
                timeout=self._config.get("timeout_seconds", 5)
            ) as client:
                headers = {}
                api_key = self._config.get("api_key", "")
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                resp = await client.post(
                    f"{base_url.rstrip('/')}/extract-entities",
                    json={"transcript": transcript},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                return ExtractedEntities(
                    chief_complaint=data.get("chief_complaint"),
                    location_text=data.get("location_text"),
                    lat=data.get("lat"),
                    lon=data.get("lon"),
                    vitals=data.get("vitals"),
                    medications_mentioned=data.get("medications_mentioned"),
                    allergies_mentioned=data.get("allergies_mentioned"),
                    confidence=data.get("confidence", 0.8),
                    degraded_mode=False,
                )
        except Exception as exc:
            logger.debug("NLPExtractorClient.extract failed: %s", exc)
            return None
