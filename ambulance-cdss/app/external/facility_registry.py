"""
app/external/facility_registry.py

Live client for the facility registry service.

OPEN DECISION — Phase 0.3 (see docs/PHASE_STATUS.md): the real API
contract for the facility registry service has not been confirmed. The
request/response shapes below are an INTERIM, DOCUMENTED ASSUMPTION,
chosen to be the most conservative reasonable guess so that swapping to
the real contract later is a contained change to this file only.

Interim assumed contract:
  GET {base_url}/facilities/nearest
    query params: lat, lon, required_services (comma-separated), radius_km
    response: {"facilities": [{"facility_id", "name", "lat", "lon",
                                "distance_km", "services": [...],
                                "capacity_status"}]}

  GET {base_url}/facilities/{facility_id}/capacity
    response: {"facility_id", "capacity_status", "available_beds"}

When the real contract is confirmed, update _build_nearest_request,
_parse_nearest_response, and the capacity equivalents. Nothing outside
this file should need to change — callers only see FacilityResult /
FacilityCapacity dataclasses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import httpx

from ..config import get_facility_registry_config
from ..retry import async_retry, with_timeout

logger = logging.getLogger(__name__)


@dataclass
class FacilityResult:
    facility_id: str
    name: str
    lat: float
    lon: float
    distance_km: float
    services: List[str]
    capacity_status: Optional[str] = None


@dataclass
class FacilityCapacity:
    facility_id: str
    capacity_status: str
    available_beds: Optional[int] = None


class FacilityRegistryClient:
    def __init__(self) -> None:
        self._config = get_facility_registry_config()

    def _configured(self) -> bool:
        return bool(self._config["base_url"])

    async def find_nearest(
        self,
        lat: float,
        lon: float,
        required_services: Optional[List[str]] = None,
        radius_km: float = 50.0,
    ) -> List[FacilityResult]:
        """
        Returns nearest facilities matching required_services, sorted by
        distance. Returns an empty list (never raises) if the service is
        unreachable or unconfigured — callers must treat an empty list as
        "fall back to local known-facility cache", not as confirmation
        there are no facilities. See docs/PHASE_STATUS.md item 0.3 and
        Phase 6.4 of the implementation plan: this fallback behaviour is
        load-bearing for patient safety during a live incident.
        """
        if not self._configured():
            logger.warning(
                "FacilityRegistryClient not configured (FACILITY_REGISTRY_BASE_URL "
                "unset). Returning empty result — caller must fall back."
            )
            return []

        params = {
            "lat": lat,
            "lon": lon,
            "radius_km": radius_km,
        }
        if required_services:
            params["required_services"] = ",".join(required_services)

        try:
            async with httpx.AsyncClient(
                base_url=self._config["base_url"],
                headers=self._auth_headers(),
            ) as client:

                async def _call():
                    return await client.get("/facilities/nearest", params=params)

                response = await with_timeout(
                    async_retry(_call, max_attempts=2),
                    self._config["timeout_seconds"],
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("FacilityRegistryClient.find_nearest failed: %s", exc)
            return []

        return [
            FacilityResult(
                facility_id=f["facility_id"],
                name=f["name"],
                lat=f["lat"],
                lon=f["lon"],
                distance_km=f["distance_km"],
                services=f.get("services", []),
                capacity_status=f.get("capacity_status"),
            )
            for f in data.get("facilities", [])
        ]

    async def get_capacity(self, facility_id: str) -> Optional[FacilityCapacity]:
        if not self._configured():
            logger.warning("FacilityRegistryClient not configured.")
            return None

        try:
            async with httpx.AsyncClient(
                base_url=self._config["base_url"],
                headers=self._auth_headers(),
            ) as client:

                async def _call():
                    return await client.get(f"/facilities/{facility_id}/capacity")

                response = await with_timeout(
                    async_retry(_call, max_attempts=2),
                    self._config["timeout_seconds"],
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("FacilityRegistryClient.get_capacity failed: %s", exc)
            return None

        return FacilityCapacity(
            facility_id=data["facility_id"],
            capacity_status=data["capacity_status"],
            available_beds=data.get("available_beds"),
        )

    def _auth_headers(self) -> dict:
        if self._config["api_key"]:
            return {"Authorization": f"Bearer {self._config['api_key']}"}
        return {}
