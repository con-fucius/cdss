"""
triage-ranker/app/schemas.py

Re-exports shared contracts from the ambulance-cdss-contracts package.
The triage ranker uses these schemas for request validation and response
serialization.
"""

from ambulance_cdss_contracts.triage import (
    ClinicalCategory,
    DiagnosisRankItem,
    ExtractedKeyword,
    SeverityLevel,
    TriageLevel,
    TriageMetadata,
    TriageRequest,
    TriageResponse,
)

__all__ = [
    "ClinicalCategory",
    "DiagnosisRankItem",
    "ExtractedKeyword",
    "SeverityLevel",
    "TriageLevel",
    "TriageMetadata",
    "TriageRequest",
    "TriageResponse",
]
