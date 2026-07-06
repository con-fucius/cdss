"""Hazard zone registry — manually maintained road conditions.

Dispatchers can update via POST /hazard-zones. Stored in Redis with long TTL.
Pre-populated with known Nairobi hazard zones for development."""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HAZARD_ZONES = [
    {
        "zone_id": "nairobi-central-peak",
        "name": "Nairobi CBD Peak Hours",
        "description": "Heavy congestion 7-9am and 4-7pm on Moi Avenue, Kenyatta Avenue, Kenyatta Way",
        "lat_min": -1.2920, "lat_max": -1.2850,
        "lon_min": 36.8150, "lon_max": 36.8250,
        "severity": "high",
        "active_hours": "07:00-09:00,16:00-19:00",
        "days": "mon-fri",
        "source": "manual",
    },
    {
        "zone_id": "mombasa-road-flood",
        "name": "Mombasa Road Flood Zone",
        "description": "Prone to flooding during rainy season near Athi River",
        "lat_min": -1.4500, "lat_max": -1.4200,
        "lon_min": 36.9700, "lon_max": 37.0000,
        "severity": "medium",
        "active_hours": "all",
        "days": "rainy season only",
        "source": "manual",
    },
    {
        "zone_id": "thika-road-construction",
        "name": "Thika Road Construction Zone",
        "description": "Ongoing construction near Kasarani, expect delays",
        "lat_min": -1.2300, "lat_max": -1.2200,
        "lon_min": 36.8800, "lon_max": 36.9000,
        "severity": "medium",
        "active_hours": "all",
        "days": "all",
        "source": "manual",
    },
]


def _point_in_zone(lat: float, lon: float, zone: dict) -> bool:
    return (
        zone["lat_min"] <= lat <= zone["lat_max"]
        and zone["lon_min"] <= lon <= zone["lon_max"]
    )


def check_route_hazards(
    lat: float,
    lon: float,
    zones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Check if a point falls within any active hazard zones. Returns a list
    of warning dicts for zones that contain the point.
    """
    warnings = []
    for zone in zones:
        if _point_in_zone(lat, lon, zone):
            warnings.append({
                "zone_id": zone["zone_id"],
                "name": zone["name"],
                "severity": zone["severity"],
                "description": zone["description"],
            })
    return warnings
