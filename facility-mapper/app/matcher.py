"""
facility-mapper/app/matcher.py

High-level facility matching that combines BallTree search with
geocoding for text-location callers.

Provides the two main search paths:
1. find_nearest_by_coords(lat, lon, ...) — direct coordinate search
2. find_nearest_by_location(location_text, ...) — geocode then search

Both return FacilitySearchResponse from shared contracts.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ambulance_cdss_contracts.facility import FacilityResult, FacilitySearchResponse

from .config import get_ambulance_speed_kmh
from .data import get_ball_tree
from .geocoding import geocode

logger = logging.getLogger(__name__)


def _compute_eta_minutes(distance_km: float) -> float:
    """
    ETA in minutes at the configured ambulance speed.

    Formula: (distance_km / AMBULANCE_SPEED_KMH) * 60
    Default speed 60 km/h is a realistic urban/rural Kenya average.
    Documented: not a magic number. Configurable via env var.
    """
    speed = get_ambulance_speed_kmh()
    if speed <= 0:
        speed = 60.0  # Defensive fallback
    return round((distance_km / speed) * 60, 1)


async def find_nearest_by_coords(
    lat: float,
    lon: float,
    level_min: int = 1,
    required_services: Optional[List[str]] = None,
    radius_km: float = 50.0,
    max_results: int = 3,
) -> FacilitySearchResponse:
    """
    Find nearest facilities by coordinates.

    Returns FacilitySearchResponse from shared contracts.
    Never raises — returns empty list on any error.
    """
    tree = get_ball_tree()
    raw_results = tree.find_nearest(
        lat=lat,
        lon=lon,
        level_min=level_min,
        required_services=required_services,
        radius_km=radius_km,
        max_results=max_results,
    )

    facilities = []
    for r in raw_results:
        eta = _compute_eta_minutes(r["distance_km"])
        facilities.append(
            FacilityResult(
                facility_id=r["facility_id"],
                name=r["name"],
                county=r.get("county"),
                level=r["level"],
                lat=r["lat"],
                lon=r["lon"],
                phone=r.get("phone"),
                services=r.get("services", []),
                distance_km=r["distance_km"],
                eta_minutes=eta,
            )
        )

    # data_as_of from BallTree build timestamp
    data_as_of = tree.built_at.isoformat() if tree.built_at else None

    return FacilitySearchResponse(
        facilities=facilities,
        total_found=len(facilities),
        data_as_of=data_as_of,
    )


async def find_nearest_by_location(
    location_text: str,
    level_min: int = 1,
    required_services: Optional[List[str]] = None,
    radius_km: float = 50.0,
    max_results: int = 3,
) -> Optional[FacilitySearchResponse]:
    """
    Find nearest facilities by text location (geocode then search).

    Returns None if geocoding fails — caller treats as "service unavailable".
    Never raises.
    """
    coords = await geocode(location_text)
    if coords is None:
        logger.warning("Geocoding failed for location: %s", location_text)
        return None

    lat, lon = coords
    response = await find_nearest_by_coords(
        lat=lat,
        lon=lon,
        level_min=level_min,
        required_services=required_services,
        radius_km=radius_km,
        max_results=max_results,
    )
    response.geocoded_location = f"{lat},{lon}"
    return response
