"""
shared/contracts/incident_capture.py

Pydantic v2 schemas for the structured incident capture payload.

This is the payload the dispatcher UI sends to POST /incidents/from-capture —
the enriched incident creation endpoint that bridges System 3's capture
layer with ambulance-cdss's authoritative incident record.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PatientInfo(BaseModel):
    """Structured patient demographic and presentation information.

    Captured by the dispatcher or web listener from the emergency call.
    The triage ranker and ambulance-cdss use these fields to derive
    clinical enrichment (GCS estimate, trauma modifiers) even before
    formal vitals are recorded.
    """

    ageGroup: Optional[str] = Field(
        default=None,
        description=(
            "Age group: 'neonate' (< 28 days), 'infant' (28 days–1yr), "
            "'child' (1–5yr), 'older_child' (5–12yr), 'adolescent' (12–18yr), "
            "'adult' (18–65yr), 'elderly' (> 65yr). Used for paediatric "
            "protocol selection and age-appropriate reference ranges."
        ),
    )
    approxAge: Optional[str] = Field(
        default=None,
        description=(
            "Approximate age as reported by caller. E.g. '34', 'about 70'. "
            "String because callers often give approximate ages."
        ),
    )
    sex: Optional[str] = Field(
        default=None,
        description=(
            "Patient sex: 'male', 'female', 'unknown'. "
            "Relevant for obstetric emergencies and drug dosing."
        ),
    )
    name: Optional[str] = Field(
        default=None,
        description=(
            "Patient name as reported by caller. May be empty if unknown. "
            "This is PII — must not appear in any log output."
        ),
    )
    consciousness: Optional[str] = Field(
        default=None,
        description=(
            "Consciousness level as reported by caller or dispatcher: "
            "'alert', 'confused', 'responds to voice', 'responds to pain', "
            "'unconscious'. Mapped to GCS estimate by ambulance-cdss: "
            "'unconscious' → GCS 3, 'responds to voice' → GCS 9, "
            "'confused' → GCS 13, 'alert' → GCS 15. This is an "
            "approximation documented with limitations — not a clinical "
            "assessment."
        ),
    )
    breathing: Optional[str] = Field(
        default=None,
        description=(
            "Breathing status: 'normal', 'abnormal', 'not breathing'. "
            "Used for protocol branch selection."
        ),
    )
    activelyBleeding: bool = Field(
        default=False,
        description=(
            "Whether the patient is actively bleeding. When true, the "
            "triage ranker request includes a TRAUMA severity modifier "
            "and facility routing adds 'surgery' and 'blood_bank' to "
            "required services."
        ),
    )
    medicalHistory: Optional[str] = Field(
        default=None,
        description=(
            "Relevant medical history as reported by caller. "
            "Free text. May include chronic conditions, allergies, "
            "current medications."
        ),
    )


class IncidentInfo(BaseModel):
    """Incident context and location information.

    Captured from the emergency call or the web listener's form
    observation. The location is the patient's reported position,
    which serves as the initial search origin for facility routing.
    """

    type: Optional[str] = Field(
        default=None,
        description=(
            "Incident type category: 'Medical Emergency', 'Trauma', "
            "'Obstetric Emergency', 'Paediatric Emergency', 'Unknown'."
        ),
    )
    description: str = Field(
        min_length=1,
        max_length=5000,
        description=(
            "Free-text incident description from the caller or dispatcher. "
            "This is the primary input for the triage ranker's NLP pipeline. "
            "May be in English or Swahili."
        ),
    )
    location: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Structured location: {'address': 'Kiambu', 'landmark': 'near the market'}. "
            "The address is geocoded by the facility mapper for nearest-facility search."
        ),
    )
    priority: Optional[str] = Field(
        default=None,
        description=(
            "Dispatcher-assessed priority before protocol matching: "
            "'critical', 'high', 'moderate', 'low'. "
            "Used as a hint — the locked protocol's terminal outcome "
            "overrides this."
        ),
    )


class CaptureMetadata(BaseModel):
    """Metadata about the capture event itself.

    Used for correlation between the capture layer's event log and
    the ambulance-cdss incident record. The capture_correlation_id
    allows tracing back from an incident to the exact web listener
    event that created it.
    """

    source: str = Field(
        default="web_listener",
        description=(
            "Source system that produced this payload: 'web_listener' "
            "(NHS portal injection), 'dispatcher_console' (ambulance-cdss "
            "dispatcher UI), 'manual' (manual entry)."
        ),
    )
    capture_version: Optional[str] = Field(
        default=None,
        description=(
            "Version of the capture payload schema. Used for forward "
            "compatibility when the payload shape evolves."
        ),
    )
    raw_form: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Raw form field values from the web listener, if available. "
            "Preserved for audit purposes but not used by ambulance-cdss."
        ),
    )


class CapturePayload(BaseModel):
    """Structured payload for POST /incidents/from-capture.

    This is the integration point between System 3's capture layer
    (web listener, dispatcher console) and ambulance-cdss. It contains
    all the information needed to create a fully-enriched incident
    with triage enrichment and facility routing, without requiring
    the dispatcher to re-enter information already captured from the
    emergency call.

    The payload maps directly to ambulance-cdss's existing incident
    creation logic — consciousness maps to ACVPU/GCS estimate,
    activelyBleeding maps to trauma protocol trigger, and location
    maps to facility routing search origin.
    """

    dispatchId: str = Field(
        min_length=1,
        description=(
            "Unique identifier for this dispatch event. Echoed back as "
            "capture_correlation_id in the response so the capture layer "
            "can correlate its event log with the ambulance-cdss incident."
        ),
    )
    patientInfo: PatientInfo = Field(
        description="Structured patient demographic and presentation information."
    )
    incidentInfo: IncidentInfo = Field(
        description="Incident context, location, and description."
    )
    metadata: CaptureMetadata = Field(
        default_factory=CaptureMetadata,
        description="Capture event metadata for correlation and audit.",
    )
