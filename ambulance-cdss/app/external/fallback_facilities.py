"""Fallback facility data for development/testing when facility-mapper is not running."""

FALLBACK_FACILITIES = [
    {
        "facility_id": "KNH-001",
        "name": "Kenyatta National Hospital",
        "level": 6,
        "county": "Nairobi",
        "lat": -1.2996,
        "lon": 36.8163,
        "services": ["icu", "surgery", "cardiac_cath", "ct_scan", "blood_bank"],
        "phone": "+254-20-2726300",
        "is_diverted": False,
        "diversion_reason": None,
        "critical_stock": {"blood_o": True, "morphine": True, "oxygen": True},
    },
    {
        "facility_id": "Mbagathi-001",
        "name": "Mbagathi County Hospital",
        "level": 4,
        "county": "Nairobi",
        "lat": -1.3106,
        "lon": 36.7866,
        "services": ["emergency", "surgery", "maternity"],
        "phone": "+254-20-302546",
        "is_diverted": False,
        "diversion_reason": None,
        "critical_stock": {"blood_o": False, "morphine": True, "oxygen": True},
    },
    {
        "facility_id": "MamaLucy-001",
        "name": "Mama Lucy Kibaki Hospital",
        "level": 4,
        "county": "Nairobi",
        "lat": -1.2456,
        "lon": 36.8734,
        "services": ["emergency", "maternity", "paediatrics"],
        "phone": "+254-20-2545025",
        "is_diverted": False,
        "diversion_reason": None,
        "critical_stock": {"blood_o": True, "morphine": False, "oxygen": True},
    },
    {
        "facility_id": "Kenyatta-002",
        "name": "Kenyatta University Hospital",
        "level": 5,
        "county": "Nairobi",
        "lat": -1.1734,
        "lon": 36.9376,
        "services": ["emergency", "icu", "surgery", "paediatrics"],
        "phone": "+254-20-8710000",
        "is_diverted": False,
        "diversion_reason": None,
        "critical_stock": {"blood_o": True, "morphine": True, "oxygen": True},
    },
    {
        "facility_id": "AgaKhan-001",
        "name": "Aga Khan University Hospital",
        "level": 6,
        "county": "Nairobi",
        "lat": -1.2641,
        "lon": 36.8048,
        "services": ["emergency", "icu", "surgery", "cardiac_cath", "ct_scan", "mri"],
        "phone": "+254-20-3662000",
        "is_diverted": False,
        "diversion_reason": None,
        "critical_stock": {"blood_o": True, "morphine": True, "oxygen": True},
    },
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two lat/lon points in km."""
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearest_fallback(
    lat: float,
    lon: float,
    required_services: list[str] | None = None,
    radius_km: float = 50.0,
    county: str | None = None,
) -> list[dict]:
    """Find nearest fallback facilities, optionally filtered by services and county."""
    candidates = FALLBACK_FACILITIES

    if county:
        candidates = [f for f in candidates if f["county"].lower() == county.lower()]

    results = []
    for f in candidates:
        dist = _haversine_km(lat, lon, f["lat"], f["lon"])
        if dist > radius_km:
            continue
        if required_services:
            if not all(s in f["services"] for s in required_services):
                continue
        results.append({**f, "distance_km": round(dist, 2)})

    results.sort(key=lambda x: x["distance_km"])
    return results
