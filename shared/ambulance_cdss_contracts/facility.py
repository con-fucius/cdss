"""
shared/contracts/facility.py

Pydantic v2 schemas for the Facility Mapper service HTTP API.

These schemas define exactly what ambulance-cdss sends to and receives
from the facility-mapper service. They are the authoritative contract —
any change here must be reflected in both services simultaneously.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FacilitySearchRequest(BaseModel):
    """Request payload for GET /facilities/nearest.

    Contains the incident or unit location and optional filters for
    facility level and required services. The facility mapper uses
    BallTree KNN search with Haversine metric to find nearest
    facilities matching these criteria.
    """

    lat: float = Field(
        description=(
            "Latitude of the search origin (patient location or unit location). "
            "Valid range for Kenya/Uganda/DRC: approximately -5 to 5."
        ),
    )
    lon: float = Field(
        description=(
            "Longitude of the search origin. Valid range for Kenya/Uganda/DRC: "
            "approximately 29 to 42."
        ),
    )
    radius_km: float = Field(
        default=50.0,
        ge=1.0,
        le=200.0,
        description=(
            "Maximum search radius in kilometres. Facilities beyond this "
            "distance are excluded. Default 50km is realistic for Kenya's "
            "rural/urban mix."
        ),
    )
    level_min: int = Field(
        default=1,
        ge=1,
        le=6,
        description=(
            "Minimum facility level (Kenya KEPH levels 1–6). Level 4+ "
            "indicates comprehensive care with surgery, blood bank, ICU. "
            "Level 3 has basic ER with limited surgery."
        ),
    )
    required_services: Optional[List[str]] = Field(
        default=None,
        description=(
            "Services the facility must provide, e.g. ['icu', 'cardiac', "
            "'surgery', 'blood_bank', 'ct_scan']. When specified, only "
            "facilities offering all listed services are returned."
        ),
    )
    max_results: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Maximum number of facilities to return. Default 3, max 10. "
            "Sorted by distance ascending."
        ),
    )


class FacilitySearchByLocationRequest(BaseModel):
    """Request payload for POST /facilities/nearest-by-location.

    Accepts a text location string (e.g. 'Kiambu', 'Nairobi CBD')
    instead of coordinates. The facility mapper geocodes this using
    Nominatim before performing the same BallTree search.
    """

    location: str = Field(
        min_length=2,
        description=(
            "Free-text location string to geocode. E.g. 'Kiambu', "
            "'Nairobi CBD', 'Mombasa Road junction'. The geocoder uses "
            "Nominatim with a bounding box hint for Kenya/Uganda/DRC."
        ),
    )
    radius_km: float = Field(
        default=50.0,
        ge=1.0,
        le=200.0,
        description="Maximum search radius in kilometres after geocoding.",
    )
    level_min: int = Field(
        default=1,
        ge=1,
        le=6,
        description="Minimum facility level (Kenya KEPH levels 1–6).",
    )
    required_services: Optional[List[str]] = Field(
        default=None,
        description="Services the facility must provide.",
    )
    max_results: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of facilities to return.",
    )


class FacilityResult(BaseModel):
    """A single facility returned by the nearest-facility search.

    Includes distance and estimated time of arrival (ETA) calculated
    at the configured ambulance speed (default 60 km/h, configurable
    via AMBULANCE_SPEED_KMH). The ETA formula is documented and
    transparent: (distance_km / speed_kmh) * 60 minutes.
    """

    facility_id: str = Field(
        description="Unique identifier for this facility (e.g. KMHFL facility code)."
    )
    name: str = Field(
        description="Official facility name as registered with the county health ministry."
    )
    county: Optional[str] = Field(
        default=None,
        description="County where the facility is located.",
    )
    level: int = Field(
        description=(
            "Facility level (1–6, Kenya KEPH classification). "
            "Level 4+ = comprehensive care, Level 3 = basic ER."
        ),
    )
    lat: float = Field(
        description="Facility latitude coordinate."
    )
    lon: float = Field(
        description="Facility longitude coordinate."
    )
    phone: Optional[str] = Field(
        default=None,
        description=(
            "Facility phone number for pre-arrival notification. "
            "Used by the dispatcher to call ahead."
        ),
    )
    services: List[str] = Field(
        default_factory=list,
        description=(
            "Services available at this facility, e.g. "
            "['surgery', 'blood_bank', 'icu', 'ct_scan']."
        ),
    )
    distance_km: float = Field(
        description=(
            "Straight-line distance from the search origin to the facility "
            "in kilometres, computed using Haversine formula."
        ),
    )
    eta_minutes: float = Field(
        description=(
            "Estimated time of arrival in minutes, computed as "
            "(distance_km / AMBULANCE_SPEED_KMH) * 60."
        ),
    )
    capacity_status: Optional[str] = Field(
        default=None,
        description=(
            "Current capacity status if available from the facility's "
            "own reporting: e.g. 'available', 'busy', 'full'. "
            "None when the facility mapper does not track capacity."
        ),
    )


class FacilitySearchResponse(BaseModel):
    """Response from GET /facilities/nearest and POST /facilities/nearest-by-location.

    Always includes the data_as_of timestamp so the dispatcher UI can
    display 'Facility data as of [date]' — stale data must be visible,
    not hidden behind a 'last updated' field nobody checks.
    """

    facilities: List[FacilityResult] = Field(
        description=(
            "Nearest facilities matching the search criteria, sorted by "
            "distance ascending. Empty list when no facilities are within "
            "radius or the service is in degraded mode."
        ),
    )
    total_found: int = Field(
        description="Total number of facilities matching the query before max_results limit."
    )
    data_as_of: Optional[str] = Field(
        default=None,
        description=(
            "Timestamp of the most recent facility data import. "
            "Dispatchers must be able to see how current the data is."
        ),
    )
    geocoded_location: Optional[str] = Field(
        default=None,
        description=(
            "The geocoded coordinates when the search was initiated "
            "with a text location (POST /nearest-by-location). "
            "None when coordinates were provided directly."
        ),
    )


class FacilityDetailResponse(BaseModel):
    """Response from GET /facilities/{facility_id}.

    Returns full details for a single facility, including all
    available services and contact information.
    """

    facility: FacilityResult = Field(
        description="Full facility details."
    )
    data_as_of: Optional[str] = Field(
        default=None,
        description="Timestamp of the most recent facility data import.",
    )


class FacilityHealthResponse(BaseModel):
    """Response from GET /health on the facility mapper service.

    Includes operational metadata: facility count, data currency,
    and last load timestamp. This is the health check that
    ambulance-cdss and the dispatcher UI use to display data
    freshness.
    """

    status: str = Field(
        description="Service health status: 'ok' or 'degraded'."
    )
    facility_count: int = Field(
        description="Number of active facilities loaded in the BallTree index."
    )
    data_as_of: Optional[str] = Field(
        default=None,
        description="Identifier of the most recent data source loaded (e.g. 'KMHFL_2024_02').",
    )
    last_loaded_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp of the most recent successful data load.",
    )
    ball_tree_ready: bool = Field(
        description="Whether the BallTree spatial index has been built and is ready for queries."
    )


class DataImportRecord(BaseModel):
    """A record of a data import operation, returned by GET /data-currency.

    Tracks when facility data was loaded, from what source, and how
    many records were processed. This is the audit trail for data
    currency — critical for patient safety.
    """

    source: str = Field(
        description=(
            "Identifier of the data source loaded, e.g. 'KMHFL_2024_02'."
        ),
    )
    record_count: int = Field(
        description="Number of facility records loaded in this import."
    )
    loaded_at: str = Field(
        description="ISO timestamp of when this import was completed."
    )
    loaded_by: Optional[str] = Field(
        default=None,
        description="Identifier of who or what triggered this data load.",
    )


class DataCurrencyResponse(BaseModel):
    """Response from GET /data-currency.

    Returns the full history of data imports so operators can
    verify data freshness and audit load operations.
    """

    imports: List[DataImportRecord] = Field(
        description="Chronological list of data import operations."
    )
    current_source: Optional[str] = Field(
        default=None,
        description="The currently active data source identifier.",
    )
