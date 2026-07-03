"""facility-mapper/app/data.py.

Facility data loading, validation, and BallTree construction.

Responsibilities:
- Load active facilities from PostgreSQL into a NumPy array
- Build a BallTree KNN index using Haversine metric
- Provide the singleton FacilityMatcher instance
- Validate incoming data (lat/lon bounds for Kenya/Uganda/DRC)

Design rationale:
BallTree is built once at startup and on explicit reload — not per
request. In-process cache is invalidated by TTL or explicit
/admin/reload-facilities call.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import select

from .db import get_session
from .models import Facility

logger = logging.getLogger(__name__)

# ── Validation bounds for Kenya/Uganda/DRC region ────────────────────────────
# These are geographic bounding boxes — not clinical decisions.
# Lat: -5 to 5, Lon: 29 to 42 covers Kenya, Uganda, and DRC.
_LAT_MIN, _LAT_MAX = -5.0, 5.0
_LON_MIN, _LON_MAX = 29.0, 42.0


def validate_coordinates(lat: float, lon: float) -> bool:
    """Check if coordinates fall within the Kenya/Uganda/DRC region."""
    return _LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX


class BallTreeIndex:
    """BallTree KNN spatial index for facility lookup.

    Uses Haversine metric (inputs in radians). Built from active
    facilities at startup and rebuilt on explicit reload.

    Level filter is applied post-KNN, not pre-KNN:
    KNN finds nearest N, then filter by level, then re-sort by distance.
    This avoids pre-filtering bias where a Level 3 hospital 0.1km away
    is invisible because level_min=4, but the next nearest Level 4
    is 5km away — the KNN radius must be large enough to find it.
    """

    def __init__(self) -> None:
        self._tree = None
        self._facilities: list[dict[str, Any]] = []
        self._lats: np.ndarray = np.array([])
        self._lons: np.ndarray = np.array([])
        self._built_at: datetime | None = None

    def is_ready(self) -> bool:
        """True when the BallTree has been built and has at least one facility."""
        return self._tree is not None and len(self._facilities) > 0

    @property
    def facility_count(self) -> int:
        """Number of active facilities in the index."""
        return len(self._facilities)

    @property
    def built_at(self) -> datetime | None:
        """Timestamp of the last successful BallTree build."""
        return self._built_at

    async def build(self) -> int:
        """Load all active facilities from DB and build the BallTree.
        Returns the number of facilities loaded.
        """
        from sklearn.neighbors import BallTree

        facilities: list[dict[str, Any]] = []

        async with get_session() as session:
            result = await session.execute(
                select(Facility).where(Facility.is_active == True)  # noqa: E712
            )
            for row in result.scalars():
                facilities.append(
                    {
                        "facility_id": row.facility_id,
                        "name": row.name,
                        "county": row.county,
                        "level": row.level,
                        "lat": row.lat,
                        "lon": row.lon,
                        "phone": row.phone,
                        "services": row.services or [],
                    }
                )

        if not facilities:
            logger.warning("No active facilities found in database. BallTree not built.")
            self._tree = None
            self._facilities = []
            return 0

        lats = np.array([f["lat"] for f in facilities])
        lons = np.array([f["lon"] for f in facilities])

        # Convert to radians for Haversine metric
        coords_rad = np.radians(np.column_stack([lats, lons]))

        self._tree = BallTree(coords_rad, metric="haversine")
        self._facilities = facilities
        self._lats = lats
        self._lons = lons
        self._built_at = datetime.now(UTC)

        logger.info("BallTree built with %d facilities.", len(facilities))
        return len(facilities)

    def find_nearest(
        self,
        lat: float,
        lon: float,
        level_min: int = 1,
        required_services: list[str] | None = None,
        radius_km: float = 50.0,
        max_results: int = 3,
    ) -> list[dict[str, Any]]:
        """Find nearest facilities matching criteria.

        Uses BallTree KNN with Haversine metric.
        Level filter applied post-KNN (filter, then re-sort by distance).
        ETA calculated at configured ambulance speed.

        Returns empty list (never raises) on any error.
        """
        if self._tree is None or not self._facilities:
            return []

        try:
            # Convert search origin to radians
            origin_rad = np.radians([[lat, lon]])

            # Query KNN — fetch more than max_results to allow post-filtering
            # Use a generous k to account for level filtering
            k = min(max_results * 5, len(self._facilities))
            radius_rad = radius_km / 6371.0  # Earth radius in km

            distances, indices = self._tree.query(origin_rad, k=k)

            # Flatten (query returns 2D arrays)
            distances = distances[0]
            indices = indices[0]

            results: list[dict[str, Any]] = []
            for dist_rad, idx in zip(distances, indices, strict=False):
                if dist_rad > radius_rad:
                    break  # Beyond search radius

                facility = self._facilities[idx]
                distance_km = dist_rad * 6371.0  # Convert radians to km

                # Level filter (post-KNN)
                if facility["level"] < level_min:
                    continue

                # Required services filter
                if required_services:
                    facility_services = set(facility.get("services", []))
                    if not all(s in facility_services for s in required_services):
                        continue

                results.append(
                    {
                        **facility,
                        "distance_km": round(distance_km, 2),
                    }
                )

                if len(results) >= max_results:
                    break

            return results

        except Exception as exc:
            logger.warning("BallTree query failed: %s", exc)
            return []


# ── Module-level singleton ────────────────────────────────────────────────────
# Rebuilt only at startup and on explicit /admin/reload-facilities.
# Not per request.
_ball_tree = BallTreeIndex()


def get_ball_tree() -> BallTreeIndex:
    """Return the module-level BallTree singleton."""
    return _ball_tree
