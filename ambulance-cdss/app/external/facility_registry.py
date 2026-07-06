"""app/external/facility_registry.py.

Live client for the facility registry service with fallback facility data
for development/testing when the external service is unavailable.

Enhanced with:
- County referral awareness (KEPH levels 1-6)
- Diversion status checking (Redis-backed)
- Facility stock availability (Redis-backed)
- KEPH level-based facility preference in routing
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..config import get_facility_registry_config
from ..retry import async_retry, with_timeout
from .fallback_facilities import find_nearest_fallback

logger = logging.getLogger(__name__)


@dataclass
class FacilityResult:
    facility_id: str
    name: str
    lat: float
    lon: float
    distance_km: float
    services: list[str]
    capacity_status: str | None = None
    level: int | None = None
    county: str | None = None
    is_diverted: bool = False
    diversion_reason: str | None = None
    critical_stock: dict = field(default_factory=dict)


@dataclass
class FacilityCapacity:
    facility_id: str
    capacity_status: str
    available_beds: int | None = None


class FacilityRegistryClient:
    def __init__(self) -> None:
        self._config = get_facility_registry_config()

    def _configured(self) -> bool:
        return bool(self._config["base_url"])

    async def find_nearest(
        self,
        lat: float,
        lon: float,
        required_services: list[str] | None = None,
        radius_km: float = 50.0,
        county: str | None = None,
        check_diversion: bool = True,
        min_level: int | None = None,
    ) -> list[FacilityResult]:
        """Returns nearest facilities matching required_services, sorted by
        distance. Falls back to FALLBACK_FACILITIES when the external service
        is unavailable or unconfigured.
        """
        if not self._configured():
            logger.info(
                "FacilityRegistryClient not configured — using fallback facilities."
            )
            return self._fallback_results(lat, lon, required_services, radius_km, county, min_level)

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
            logger.warning("FacilityRegistryClient.find_nearest failed, using fallback: %s", exc)
            return self._fallback_results(lat, lon, required_services, radius_km, county, min_level)

        results = [
            FacilityResult(
                facility_id=f["facility_id"],
                name=f["name"],
                lat=f["lat"],
                lon=f["lon"],
                distance_km=f["distance_km"],
                services=f.get("services", []),
                capacity_status=f.get("capacity_status"),
                level=f.get("level"),
                county=f.get("county"),
                is_diverted=f.get("is_diverted", False),
                diversion_reason=f.get("diversion_reason"),
                critical_stock=f.get("critical_stock", {}),
            )
            for f in data.get("facilities", [])
        ]

        if check_diversion:
            results = [r for r in results if not r.is_diverted]

        if min_level is not None:
            results = [r for r in results if r.level is not None and r.level >= min_level]

        return results

    def _fallback_results(
        self,
        lat: float,
        lon: float,
        required_services: list[str] | None,
        radius_km: float,
        county: str | None,
        min_level: int | None,
    ) -> list[FacilityResult]:
        """Build FacilityResult list from fallback facilities."""
        raw = find_nearest_fallback(lat, lon, required_services, radius_km, county)
        results = []
        for f in raw:
            if min_level is not None and f.get("level", 0) < min_level:
                continue
            if f.get("is_diverted", False):
                continue
            results.append(FacilityResult(
                facility_id=f["facility_id"],
                name=f["name"],
                lat=f["lat"],
                lon=f["lon"],
                distance_km=f["distance_km"],
                services=f.get("services", []),
                level=f.get("level"),
                county=f.get("county"),
                is_diverted=f.get("is_diverted", False),
                diversion_reason=f.get("diversion_reason"),
                critical_stock=f.get("critical_stock", {}),
            ))
        return results

    async def get_capacity(self, facility_id: str) -> FacilityCapacity | None:
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
