"""app/external/emergency_dispatch.py.

Live client for the emergency dispatch / unit-assignment service.

OPEN DECISION — Phase 0.4 (see docs/PHASE_STATUS.md): the real API
contract has not been confirmed. The shapes below are an INTERIM,
DOCUMENTED ASSUMPTION. Swapping to the real contract later is a
contained change to this file only.

Interim assumed contract:
  POST {base_url}/dispatch
    body: {"incident_id", "priority_code", "recommended_unit_type",
           "lat", "lon"}
    response: {"dispatch_id", "assigned_unit_id", "eta_minutes", "status"}

  POST {base_url}/admissions
    body: {"incident_id", "facility_id", "priority_code", "summary"}
    response: {"admission_id", "acknowledged", "facility_confirmation_id"}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..config import get_emergency_dispatch_config
from ..retry import async_retry, with_timeout

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    dispatch_id: str
    assigned_unit_id: str
    eta_minutes: float | None
    status: str


@dataclass
class AckResult:
    admission_id: str
    acknowledged: bool
    facility_confirmation_id: str | None = None


class EmergencyDispatchClient:
    def __init__(self) -> None:
        self._config = get_emergency_dispatch_config()

    def _configured(self) -> bool:
        return bool(self._config["base_url"])

    async def dispatch(
        self,
        incident_id: str,
        priority_code: str,
        recommended_unit_type: str,
        lat: float | None,
        lon: float | None,
    ) -> DispatchResult | None:
        """Returns None (never raises) if the service is unreachable or
        unconfigured. Callers must treat None as "manual dispatch
        required — radio/notify by other means", not as a non-event.
        This is the same fail-loud-not-silent posture as the rest of
        this system's external boundary.
        """
        if not self._configured():
            logger.warning(
                "EmergencyDispatchClient not configured "
                "(EMERGENCY_DISPATCH_BASE_URL unset). Manual dispatch required."
            )
            return None

        payload = {
            "incident_id": incident_id,
            "priority_code": priority_code,
            "recommended_unit_type": recommended_unit_type,
            "lat": lat,
            "lon": lon,
        }

        try:
            async with httpx.AsyncClient(
                base_url=self._config["base_url"],
                headers=self._auth_headers(),
            ) as client:

                async def _call():
                    return await client.post("/dispatch", json=payload)

                response = await with_timeout(
                    async_retry(_call, max_attempts=2),
                    self._config["timeout_seconds"],
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("EmergencyDispatchClient.dispatch failed: %s", exc)
            return None

        return DispatchResult(
            dispatch_id=data["dispatch_id"],
            assigned_unit_id=data["assigned_unit_id"],
            eta_minutes=data.get("eta_minutes"),
            status=data["status"],
        )

    async def report_admission(
        self,
        incident_id: str,
        facility_id: str,
        priority_code: str,
        summary: str,
    ) -> AckResult | None:
        if not self._configured():
            logger.warning("EmergencyDispatchClient not configured.")
            return None

        payload = {
            "incident_id": incident_id,
            "facility_id": facility_id,
            "priority_code": priority_code,
            "summary": summary,
        }

        try:
            async with httpx.AsyncClient(
                base_url=self._config["base_url"],
                headers=self._auth_headers(),
            ) as client:

                async def _call():
                    return await client.post("/admissions", json=payload)

                response = await with_timeout(
                    async_retry(_call, max_attempts=2),
                    self._config["timeout_seconds"],
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("EmergencyDispatchClient.report_admission failed: %s", exc)
            return None

        return AckResult(
            admission_id=data["admission_id"],
            acknowledged=data.get("acknowledged", False),
            facility_confirmation_id=data.get("facility_confirmation_id"),
        )

    def _auth_headers(self) -> dict:
        if self._config["api_key"]:
            return {"Authorization": f"Bearer {self._config['api_key']}"}
        return {}
