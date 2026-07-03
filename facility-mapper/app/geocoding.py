"""
facility-mapper/app/geocoding.py

Nominatim geocoding abstraction with in-process TTL cache.

The bridge between text-location callers (e.g. "Kiambu", "Nairobi CBD")
and the lat/lon approach used by BallTree KNN search.

Design constraints:
- 5s timeout, returns None on any failure — never raises
- In-process TTLCache: 1 hour, max 500 entries (location strings repeat
  constantly in a dispatch centre)
- Bounding box hint for Kenya/Uganda/DRC to avoid ambiguous results
- No external HTTP call propagates exceptions to the request path
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import httpx
from cachetools import TTLCache

from .config import get_geocoding_timeout_seconds, get_geocoding_user_agent

logger = logging.getLogger(__name__)

# In-process cache: location strings repeat constantly in a dispatch centre.
# 500 entries at 1 hour TTL is generous for the reuse pattern.
_geocode_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)

# Nominatim bounding box hint for Kenya/Uganda/DRC region.
# Prevents ambiguous results (e.g. "Kiambu" matching a place in another country).
_NOMINATIM_VIEWBOX = "29.0,-5.0,42.0,5.0"


async def geocode(location_text: str) -> Optional[Tuple[float, float]]:
    """
    Geocode a free-text location string to (lat, lon).

    Uses Nominatim with a bounding box hint for the Kenya/Uganda/DRC
    region. Returns None on any failure — never raises. Cached
    in-process for 1 hour to avoid repeat external calls for the same
    location string.

    Args:
        location_text: Free-text location, e.g. "Kiambu", "Nairobi CBD"

    Returns:
        (latitude, longitude) tuple, or None if geocoding failed.
    """
    if not location_text or not location_text.strip():
        return None

    normalised = location_text.strip()

    # Check cache first
    cached = _geocode_cache.get(normalised)
    if cached is not None:
        return cached

    try:
        timeout = get_geocoding_timeout_seconds()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": normalised,
                    "format": "json",
                    "limit": 1,
                    "viewbox": _NOMINATIM_VIEWBOX,
                    "bounded": 1,  # Prefer results within viewbox
                },
                headers={"User-Agent": get_geocoding_user_agent()},
            )
            response.raise_for_status()
            results = response.json()

            if not results:
                logger.info("Geocoding returned no results for: %s", normalised)
                return None

            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])

            result = (lat, lon)
            _geocode_cache[normalised] = result
            return result

    except httpx.TimeoutException:
        logger.warning("Geocoding timed out for: %s", normalised)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Geocoding HTTP error for %s: %s", normalised, exc.response.status_code
        )
        return None
    except Exception as exc:
        logger.warning("Geocoding failed for %s: %s", normalised, exc)
        return None


def clear_cache() -> None:
    """Clear the geocoding cache. Called by /admin/reload-facilities."""
    _geocode_cache.clear()
    logger.info("Geocoding cache cleared.")
