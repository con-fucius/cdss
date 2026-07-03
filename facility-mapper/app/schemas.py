"""
facility-mapper/app/schemas.py

Re-exports shared contracts from the ambulance-cdss-contracts package.

The facility mapper service uses these schemas for request validation
and response serialization, ensuring type-safe HTTP contracts between
ambulance-cdss and facility-mapper.
"""

from ambulance_cdss_contracts.facility import (
    DataCurrencyResponse,
    DataImportRecord,
    FacilityDetailResponse,
    FacilityHealthResponse,
    FacilityResult,
    FacilitySearchByLocationRequest,
    FacilitySearchRequest,
    FacilitySearchResponse,
)

__all__ = [
    "DataCurrencyResponse",
    "DataImportRecord",
    "FacilityDetailResponse",
    "FacilityHealthResponse",
    "FacilityResult",
    "FacilitySearchByLocationRequest",
    "FacilitySearchRequest",
    "FacilitySearchResponse",
]
