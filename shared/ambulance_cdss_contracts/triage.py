"""
shared/contracts/triage.py

Pydantic v2 schemas for the Triage Ranker service HTTP API.

These schemas define exactly what ambulance-cdss sends to and receives
from the triage-ranker service. They are the authoritative contract —
any change here must be reflected in both services simultaneously.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────


class TriageLevel(str, Enum):
    """Pre-hospital triage priority level (P1 = most urgent)."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class SeverityLevel(str, Enum):
    """Clinical severity classification for a ranked diagnosis."""

    CRITICAL = "critical"
    HIGH = "high"
    ACUTE = "acute"
    MODERATE = "moderate"
    LOW = "low"


class ClinicalCategory(str, Enum):
    """Broad clinical category for an extracted keyword or diagnosis."""

    RESPIRATORY = "RESPIRATORY"
    CARDIOVASCULAR = "CARDIOVASCULAR"
    NEUROLOGICAL = "NEUROLOGICAL"
    TRAUMA = "TRAUMA"
    OBSTETRIC = "OBSTETRIC"
    PAEDIATRIC = "PAEDIATRIC"
    UNKNOWN = "UNKNOWN"


# ── Request schemas ────────────────────────────────────────────────────────────


class TriageRequest(BaseModel):
    """Request payload for POST /triage.

    Contains the emergency incident description and optional clinical
    scores extracted from the caller or dispatcher. The triage ranker
    processes this through its three-stage pipeline (NLP extraction,
    UMLS resolution, composite ranking) to produce ranked diagnoses.
    """

    incident_desc: str = Field(
        min_length=5,
        max_length=5000,
        description=(
            "Free-text description of the emergency presentation. "
            "May be in English or Swahili. The NLP extractor handles "
            "both languages — Swahili terms are matched via clinical_rules.yaml "
            "after spaCy tokenisation."
        ),
    )
    gcs_score: Optional[int] = Field(
        default=None,
        ge=3,
        le=15,
        description=(
            "Glasgow Coma Scale total (3–15). Lower values indicate "
            "more severe neurological impairment. GCS ≤ 8 is a critical "
            "threshold indicating severe traumatic brain injury."
        ),
    )
    acvpu: Optional[str] = Field(
        default=None,
        description=(
            "Consciousness level: A (Alert), C (Confused), "
            "V (Voice responsive), P (Pain responsive), U (Unresponsive). "
            "ACVPU is the pre-hospital alternative to full GCS when "
            "detailed component scoring is not feasible."
        ),
    )
    sbp: Optional[int] = Field(
        default=None,
        ge=30,
        le=300,
        description=(
            "Systolic blood pressure in mmHg. Used to compute Shock Index "
            "(HR/SBP) when heart rate is also provided. SBP < 90 mmHg in "
            "an adult is hypotensive and suggests circulatory compromise."
        ),
    )
    hr: Optional[int] = Field(
        default=None,
        ge=20,
        le=300,
        description=(
            "Heart rate in beats per minute. Tachycardia (HR > 100) "
            "combined with hypotension (SBP < 90) produces Shock Index > 1.0, "
            "a critical marker of haemodynamic instability."
        ),
    )
    include_umls_lookup: bool = Field(
        default=True,
        description=(
            "Whether to attempt UMLS API resolution for extracted entities. "
            "Set to false when the service is known to be in degraded mode "
            "or when canonical medical codes are not needed."
        ),
    )
    semantic_type_filter: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional filter on UMLS semantic types to resolve. "
            "E.g. ['Disease or Syndrome', 'Sign or Symptom']. "
            "When None, all matched types are resolved."
        ),
    )


# ── Extraction schemas ─────────────────────────────────────────────────────────


class ExtractedKeyword(BaseModel):
    """A clinical keyword extracted from the incident description by Stage 1.

    This is the raw output of the NLP extractor before UMLS resolution.
    Each keyword carries its text, matched clinical category, whether it
    was negated (e.g. 'denies chest pain'), and any severity modifiers
    detected (e.g. 'severe', 'acute').
    """

    text: str = Field(
        description="The matched text span from the incident description."
    )
    category: ClinicalCategory = Field(
        description="Clinical category assigned by clinical_rules.yaml matching."
    )
    is_negated: bool = Field(
        default=False,
        description=(
            "True if the keyword appears in a negated context "
            "(e.g. 'denies chest pain', 'hakuna maumivu ya kifua'). "
            "Negated keywords should not contribute to diagnosis ranking."
        ),
    )
    severity_modifiers: List[str] = Field(
        default_factory=list,
        description=(
            "Severity modifiers detected around the keyword, e.g. "
            "'severe', 'critical', 'acute', and Swahili equivalents "
            "('mkali', 'hatari'). These modify the w_rule component."
        ),
    )
    icd10_prefix: Optional[str] = Field(
        default=None,
        description="ICD-10 code prefix from clinical_rules.yaml, if matched."
    )
    snomed_hint: Optional[str] = Field(
        default=None,
        description="SNOMED-CT concept hint from clinical_rules.yaml, if matched."
    )
    source: str = Field(
        default="rules",
        description=(
            "How this keyword was extracted: 'rules' for clinical_rules.yaml "
            "matching, 'spacy' for NLP entity extraction."
        ),
    )


# ── Response schemas ───────────────────────────────────────────────────────────


class DiagnosisRankItem(BaseModel):
    """A ranked diagnosis returned by Stage 3 of the triage pipeline.

    Each item represents a clinical condition the patient may be
    experiencing, ranked by composite severity score. The composite
    score combines rule-based weights, semantic similarity, severity
    modifiers, and scoring system inputs (GCS, Shock Index).
    """

    rank: int = Field(
        description="Ranking position (1 = most likely / most urgent)."
    )
    canonical_name: str = Field(
        description=(
            "Standardised clinical condition name. E.g. 'Acute Myocardial "
            "Infarction', 'Severe Traumatic Brain Injury'."
        ),
    )
    umls_cui: Optional[str] = Field(
        default=None,
        description=(
            "UMLS Concept Unique Identifier. Absent when UMLS resolution "
            "is in degraded mode (L4 fallback only)."
        ),
    )
    snomed_code: Optional[str] = Field(
        default=None,
        description="SNOMED-CT code for this condition, if resolved via UMLS."
    )
    icd10_code: Optional[str] = Field(
        default=None,
        description="ICD-10 code for this condition, if available."
    )
    severity_level: SeverityLevel = Field(
        description="Clinical severity classification for this diagnosis."
    )
    esi_level: int = Field(
        ge=1,
        le=5,
        description=(
            "Emergency Severity Index level (1 = most acute, 5 = least acute). "
            "Mapped from the composite score."
        ),
    )
    score_breakdown: Dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Decomposition of the composite score into its components: "
            "w_rule, w_semantic, w_modifier, w_scoring_system, total."
        ),
    )
    scoring_systems_applied: List[str] = Field(
        default_factory=list,
        description=(
            "Scoring systems that contributed to this diagnosis score, "
            "e.g. ['GCS_NUMERIC', 'SHOCK_INDEX_CRITICAL']."
        ),
    )
    modifier_classes: List[str] = Field(
        default_factory=list,
        description=(
            "Severity modifier classes detected, e.g. "
            "['SEVERITY_CRITICAL', 'ACTIVE']."
        ),
    )


class TriageMetadata(BaseModel):
    """Metadata about the triage processing — timing, cache performance,
    and derived clinical metrics.

    This is returned alongside the ranked diagnoses to support debugging,
    monitoring, and downstream clinical scoring in ambulance-cdss.
    """

    request_id: str = Field(
        description="Unique identifier for this triage request, for traceability."
    )
    processing_times_ms: Dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Processing time per pipeline stage in milliseconds. "
            "E.g. {'extraction': 45.2, 'resolution': 120.5, 'ranking': 12.3}."
        ),
    )
    cache_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Cache hit/miss statistics: L1 hits, L2 hits, L3 calls, L4 fallbacks."
        ),
    )
    shock_index: Optional[float] = Field(
        default=None,
        description=(
            "Computed Shock Index (HR / SBP). Value > 1.0 indicates "
            "haemodynamic instability. Only present when both HR and SBP "
            "were provided in the request."
        ),
    )
    scoring_systems_used: List[str] = Field(
        default_factory=list,
        description="List of scoring systems applied during ranking.",
    )
    inferred_risks: List[str] = Field(
        default_factory=list,
        description=(
            "Risk flags inferred from the input, e.g. "
            "['SEVERE_TBI', 'HAEMODYNAMIC_INSTABILITY']."
        ),
    )


class TriageResponse(BaseModel):
    """Response from POST /triage.

    Contains ranked clinical diagnoses, extracted keywords (with or
    without UMLS enrichment), overall triage level, and processing
    metadata. The triage level and top diagnosis are the primary
    outputs used by ambulance-cdss for incident enrichment.
    """

    diagnosis_ranking: List[DiagnosisRankItem] = Field(
        description=(
            "Ranked list of potential diagnoses, sorted by urgency. "
            "Never empty — falls back to 'Undifferentiated Emergency' "
            "when the pipeline cannot extract meaningful entities."
        ),
    )
    historical_findings: List[DiagnosisRankItem] = Field(
        default_factory=list,
        description=(
            "Diagnoses classified as historical (patient-reported past "
            "conditions, not active). Populated when negation detection "
            "identifies historical mentions."
        ),
    )
    keywords: List[ExtractedKeyword] = Field(
        description=(
            "Raw extracted keywords from Stage 1, before or after UMLS "
            "resolution depending on pipeline state."
        ),
    )
    triage_level: TriageLevel = Field(
        description=(
            "Overall triage priority: P1 (immediate/resuscitation), "
            "P2 (emergent/urgent), P3 (urgent/less urgent), "
            "P4 (non-urgent)."
        ),
    )
    esi_level: int = Field(
        ge=1,
        le=5,
        description=(
            "Emergency Severity Index level mapped from the composite "
            "score. 1 = needs immediate life-saving intervention."
        ),
    )
    degraded_mode: bool = Field(
        default=False,
        description=(
            "True when the service operated in degraded mode — e.g. "
            "UMLS API was unreachable and only L4 fallback rules were "
            "used. Results are still useful but lack canonical "
            "SNOMED/ICD-10 codes."
        ),
    )
    metadata: TriageMetadata = Field(
        description="Processing metadata, timing, and cache statistics."
    )
