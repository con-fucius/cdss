"""FastAPI backend for CDSS.

Architecture:
- All live queries route through the Groq/Puter OpenAI-compatible path.
- The pydantic-ai Mistral agent path is explicitly retired; search_agent.py
  retains build_agent() for tests and future reactivation only.
- Evidence graph context is injected in both the online path AND the offline
  path so KB-only mode surfaces validated clinical relationships alongside
  raw retrieved passages.
- Session history is written to Postgres when available, in-memory otherwise.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import (
    DISEASE_CONFIG,
    get_audit_storage_backend,
    get_session_storage_backend,
    validate_patient_salt,
)
from .ddx import DifferentialDiagnosisEngine
from .logs import (
    log_correction,
    log_error,
    log_feedback,
    log_query,
    log_response,
    read_audit_logs_async,
)
from .observability import (
    MetricsMiddleware,
    RateLimitMiddleware,
    configure_tracing,
    metrics_text,
)
from .providers import (
    get_llm_model,
    get_llm_provider,
    provider_auth_header,
    provider_chat_endpoint,
    provider_has_credentials,
    provider_models_url,
    provider_offline,
)
from .retry import with_timeout
from .search_tools import SearchIndex

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
configure_tracing()

MAX_HISTORY_DEPTH = 20
CHAT_STREAM_TIMEOUT_SECONDS = float(os.getenv("CDSS_CHAT_STREAM_TIMEOUT_SECONDS", "120"))
PAGEINDEX_CHAT_TIMEOUT_SECONDS = float(os.getenv("CDSS_PAGEINDEX_CHAT_TIMEOUT_SECONDS", "3"))
TERMINOLOGY_SHADOW_ENABLED = (
    os.getenv("CDSS_TERMINOLOGY_SHADOW_ENABLED", "false").strip().lower() == "true"
)
TERMINOLOGY_SHADOW_RETRIEVAL_ENABLED = (
    os.getenv("CDSS_TERMINOLOGY_SHADOW_RETRIEVAL_ENABLED", "false").strip().lower() == "true"
)
TERMINOLOGY_SHADOW_TIMEOUT_SECONDS = float(
    os.getenv("CDSS_TERMINOLOGY_SHADOW_TIMEOUT_SECONDS", "8")
)
TERMINOLOGY_QUERY_EXPANSION_ENABLED = (
    os.getenv("CDSS_TERMINOLOGY_QUERY_EXPANSION_ENABLED", "false").strip().lower() == "true"
)
AUTO_MEMORY_DISTILLATION_ENABLED = (
    os.getenv("CDSS_AUTO_MEMORY_DISTILLATION_ENABLED", "false").strip().lower() == "true"
)
DRUG_INTERACTION_CHECK_ENABLED = (
    os.getenv("CDSS_DRUG_INTERACTION_CHECK_ENABLED", "false").strip().lower() == "true"
)
DRUG_INTERACTION_CHECK_TIMEOUT_SECONDS = float(
    os.getenv("CDSS_DRUG_INTERACTION_CHECK_TIMEOUT_SECONDS", "4")
)
_search_index: SearchIndex | None = None
_session_history: dict[str, list[dict[str, str]]] = {}
_is_offline_mode: bool = provider_offline()

_DISEASE_QUERY_HINTS: dict[str, list[str]] = {
    "diabetes": [
        "diabetes",
        "diabetic",
        "type 1",
        "type 2",
        "t1dm",
        "t2dm",
        "insulin",
        "metformin",
        "hba1c",
        "blood glucose",
        "glucose",
        "glycaemic",
        "glycemic",
        "dka",
    ],
    "cvd": [
        "hypertension",
        "hypertensive",
        "blood pressure",
        " bp ",
        "mmhg",
        "amlodipine",
        "losartan",
        "enalapril",
        "statin",
        "cardiovascular",
        "heart failure",
        "stroke",
    ],
    "tb": [
        "tuberculosis",
        " tb ",
        "rifampicin",
        "isoniazid",
        "genexpert",
        "sputum",
        "mdr-tb",
        "tpt",
    ],
    "malaria": [
        "malaria",
        "artemether",
        "lumefantrine",
        "artesunate",
        "parasitemia",
        "mrdt",
        "fever",
    ],
    "mental_health": [
        "depression",
        "anxiety",
        "psychosis",
        "suicide",
        "mental health",
        "substance use",
    ],
    "hiv": [
        "hiv",
        "plhiv",
        "art",
        "arv",
        "viral load",
        "cd4",
        "dolutegravir",
        "tenofovir",
        "lamivudine",
        "pmtct",
    ],
}

_SMALLTALK_PATTERNS = {
    "hi",
    "hello",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "yes",
    "no",
    "help",
    "what can you do",
    "who are you",
}

_CLINICAL_INTENT_HINTS = {
    "diagnose",
    "diagnosis",
    "treat",
    "treatment",
    "therapy",
    "manage",
    "management",
    "dose",
    "dosage",
    "target",
    "initiate",
    "start",
    "stop",
    "switch",
    "refer",
    "screen",
    "screening",
    "monitor",
    "monitoring",
    "pregnant",
    "adult",
    "child",
    "patient",
    "symptom",
    "symptoms",
    "contraindication",
    "adverse",
}

_KB_QUERY_TYPE = "arv_regimen_dosing"
_DOSING_QUERY_KEYWORDS = {
    "dose",
    "doses",
    "dosage",
    "dosing",
    "regimen",
    "weight",
    "weight-based",
    "weight based",
    "mg",
    "ml",
    "kg",
    "mmol",
    "tablet",
    "tablets",
}


def _normalise_query_text(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()


def _is_dosing_query(query: str) -> bool:
    """Return True for dosing/regimen-shaped queries that should try KB lookup."""
    normalized = f" {_normalise_query_text(query)} "
    if re.search(r"\b\d+(?:\.\d+)?\s*(kg|mg|ml|mmol)\b", normalized):
        return True
    if any(f" {keyword} " in normalized for keyword in _DOSING_QUERY_KEYWORDS):
        return True
    for cfg in DISEASE_CONFIG.values():
        for keyword in cfg.get("validation_keywords", []):
            key = f" {_normalise_query_text(keyword)} "
            if key in normalized:
                return True
    return False


def _kb_filters_from_query(query: str) -> dict[str, str]:
    """Build exact filters for the minimal HIV ARV dosing KB table."""
    filters: dict[str, str] = {}
    normalized = _normalise_query_text(query)
    weight_match = re.search(r"(?:weight\s*)?(\d+(?:\.\d+)?)\s*kg", normalized)
    if weight_match:
        weight = float(weight_match.group(1))
        filters["weight_band"] = "< 30 kg" if weight < 30 else ">= 30 kg"
    if re.search(r"first\s*line|first-line", normalized):
        filters["line"] = "first-line"
    return filters


def _format_structured_kb_context(result: Any) -> str:
    data = result.data if hasattr(result, "data") else {}
    lines = [
        "[STRUCTURED_KB_RESULT]",
        f"  Disease: {getattr(result, 'disease', 'unknown').upper()}",
        f"  Table type: {getattr(result, 'table_type', _KB_QUERY_TYPE)}",
        f"  Source: [Structured KB: {getattr(result, 'source', '')}]",
    ]
    for key in (
        "weight_band",
        "population",
        "line",
        "regimen",
        "drugs",
        "dose_basis",
        "units",
        "notes",
    ):
        value = data.get(key)
        if not value:
            continue
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        lines.append(f"  {key}: {value}")
    lines.append("[/STRUCTURED_KB_RESULT]")
    return "\n".join(lines)


async def _lookup_structured_kb_context(
    query: str,
    disease: str | None,
    session_id: str,
    query_id: str,
) -> str | None:
    if not _search_index or not disease or not _is_dosing_query(query):
        return None
    try:
        result = await _search_index.lookup_kb(
            query_type=_KB_QUERY_TYPE,
            disease=disease,
            filters=_kb_filters_from_query(query),
            session_id=session_id,
            query_id=query_id,
        )
    except Exception as exc:
        logger.warning("Structured KB lookup failed for %s: %s", disease, exc)
        return None
    if not result:
        return None
    return _format_structured_kb_context(result)


# ─────────────────────────────────────────────────────────────────────────────
# Background tasks
# ─────────────────────────────────────────────────────────────────────────────


async def check_guideline_updates() -> None:
    """HEAD-check configured guideline URLs. Gated behind CDSS_CHECK_GUIDELINE_UPDATES."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        for disease, config in DISEASE_CONFIG.items():
            url = config.get("source_url")
            if not url:
                continue
            try:
                response = await client.head(url)
                logger.info(
                    "Guideline check %s: status=%s etag=%s last_modified=%s",
                    disease,
                    response.status_code,
                    response.headers.get("etag", ""),
                    response.headers.get("last-modified", ""),
                )
            except Exception as exc:
                logger.warning("Guideline URL check failed for %s: %s", disease, exc)


async def check_llm_reachability() -> None:
    global _is_offline_mode
    while True:
        provider = get_llm_provider()
        if not provider_has_credentials(provider):
            _is_offline_mode = True
            await asyncio.sleep(30)
            continue
        url = provider_models_url(provider)
        if not url:
            _is_offline_mode = False
            await asyncio.sleep(60)
            continue
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url, headers=provider_auth_header(provider))
            _is_offline_mode = res.status_code != 200
        except Exception:
            _is_offline_mode = True
        await asyncio.sleep(60)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _search_index, _is_offline_mode
    background_tasks: list[asyncio.Task[Any]] = []

    validate_patient_salt()

    _search_index = SearchIndex()
    logger.info("SearchIndex initialised")

    try:
        model = _search_index._get_embedding_model()
        list(model.embed(["warmup"]))
        logger.info("Embedding model warmed up")
    except Exception as exc:
        logger.warning("Embedding warmup failed: %s", exc)

    try:
        _search_index._get_reranker()
        logger.info("Reranker loaded")
    except Exception as exc:
        logger.warning("Reranker warmup failed: %s", exc)

    _is_offline_mode = provider_offline()

    if os.getenv("CDSS_CHECK_GUIDELINE_UPDATES", "false").lower() == "true":
        background_tasks.append(asyncio.create_task(check_guideline_updates()))

    background_tasks.append(asyncio.create_task(check_llm_reachability()))

    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

        _search_index = None
        try:
            from .db import dispose_engine

            await dispose_engine()
        except Exception as exc:
            logger.warning("Database engine disposal failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="CDSS API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-User-Role", "X-Session-Id"],
)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RateLimitMiddleware)

from .terminology.router import router as terminology_router

app.include_router(terminology_router)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────


class PatientContext(BaseModel):
    patient_type: str | None = "Select..."
    condition: str | None = "Select..."
    comorbidity: str | None = "Select..."
    filters: list[str] = Field(default_factory=list)
    active_conditions: list[str] = Field(default_factory=list)
    clinical_params: dict[str, Any] = Field(default_factory=dict)
    medications: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    session_id: str
    message: str
    context: PatientContext | None = None
    patient_ref_hash: str | None = None


class FeedbackRequest(BaseModel):
    session_id: str
    message_id: str
    feedback_type: str
    note: str | None = ""
    correction: str | None = ""
    sources_used: list[str] = Field(default_factory=list)


class UserCreateRequest(BaseModel):
    external_id: str
    role: str = "CLINICIAN"
    display_name: str = ""


class UserUpdateRequest(BaseModel):
    role: str | None = None
    display_name: str | None = None


class PageIndexQueryRequest(BaseModel):
    query: str
    disease: str | None = None
    top_k: int = Field(default=3, ge=1, le=10)


class StructuredKBQueryRequest(BaseModel):
    disease: str
    query_type: str = Field(default="dosing", min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)


class MemoryCreateRequest(BaseModel):
    session_id: str
    patient_context: dict[str, Any] = Field(default_factory=dict)
    fact_type: str
    fact_text: str
    source_message_ids: list[str] = Field(default_factory=list)


class MemoryListRequest(BaseModel):
    patient_context: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class MemoryDistillRequest(BaseModel):
    session_id: str
    patient_context: dict[str, Any] = Field(default_factory=dict)


class EvidenceQueryRequest(BaseModel):
    disease: str
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class DrugInteractionCheckRequest(BaseModel):
    medications: list[str] = Field(default_factory=list)


class SessionClearRequest(BaseModel):
    patient_context: PatientContext | None = None


class PatientEncounterCreateRequest(BaseModel):
    patient_context: PatientContext
    encounter_type: str = "initial"
    disease_scope: str


class PatientVitalsUpsertRequest(BaseModel):
    patient_ref_hash: str
    encounter_id: str
    vitals: dict[str, Any] = Field(default_factory=dict)


class PatientLabsUpsertRequest(BaseModel):
    patient_ref_hash: str
    encounter_id: str
    labs: list[dict[str, Any]]


class EvidenceNodesRequest(BaseModel):
    disease: str | None = None
    node_type: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class AlertOverrideRequest(BaseModel):
    session_id: str
    patient_ref_hash: str | None = None
    alert_type: str
    alert_level: str
    alert_summary: str
    override_reason: str


class ClinicalScoreRequest(BaseModel):
    scorer: str
    patient_ref: str | None = None
    inputs: dict[str, Any]


class DDxRequest(BaseModel):
    patient_ref: str | None = None
    presenting_symptoms: list[str] = Field(min_length=1)
    duration_days: int | None = None
    vital_signs: dict[str, Any] | None = None
    relevant_labs: dict[str, Any] | None = None
    context: PatientContext | None = None
    target_diseases: list[str] | None = None


class PathwayRunRequest(BaseModel):
    pathway_id: str
    patient_ref: str


class DocumentGenerateRequest(BaseModel):
    document_type: str
    patient_ref: str
    encounter_id: str
    additional_context: str | None = None


class DocumentReviewRequest(BaseModel):
    reviewed_by: str


# ─────────────────────────────────────────────────────────────────────────────
# Role system
# ─────────────────────────────────────────────────────────────────────────────


def get_current_role(x_user_role: str | None = Header(None)) -> str:
    env_role = os.getenv("CDSS_ROLE")
    return (x_user_role or env_role or "CLINICIAN").upper()


def require_admin(role: str = Depends(get_current_role)) -> str:
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin access required")
    return role


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_available_diseases() -> list[str]:
    return _search_index.available_diseases() if _search_index else []


def format_timestamp() -> str:
    return datetime.now().strftime("%H:%M")


def _build_context_block(context: PatientContext) -> str | None:
    payload: dict[str, Any] = {}

    def _meaningful(v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            return v not in ("", "Select...", "None")
        if isinstance(v, (list, dict)):
            return bool(v)
        return True

    for field, value in context.model_dump().items():
        if _meaningful(value):
            payload[field] = value

    if not payload:
        return None

    lines = ["[PATIENT_CONTEXT]"]
    if payload.get("patient_type"):
        lines.append(f"  Population: {payload['patient_type']}")
    if payload.get("condition"):
        lines.append(f"  Condition: {payload['condition']}")
    if payload.get("comorbidity"):
        lines.append(f"  Comorbidity: {payload['comorbidity']}")
    if payload.get("active_conditions"):
        lines.append(f"  Active conditions: {', '.join(payload['active_conditions'])}")
    if payload.get("medications"):
        lines.append(f"  Current medications: {', '.join(payload['medications'])}")
    if payload.get("filters"):
        lines.append(f"  Clinical focus: {', '.join(payload['filters'])}")
    if payload.get("clinical_params"):
        for k, v in payload["clinical_params"].items():
            lines.append(f"  {k}: {v}")
    lines.append("[/PATIENT_CONTEXT]")
    return "\n".join(lines)


def _format_patient_state_context(state: dict[str, Any]) -> str | None:
    if not state:
        return None

    lines = ["[PATIENT_STATE]"]
    encounter = state.get("most_recent_encounter") or {}
    if encounter:
        lines.append(
            f"  Encounter: {encounter.get('encounter_id')} ({encounter.get('encounter_type', 'unknown')})"
        )
        if encounter.get("disease_scope"):
            lines.append(f"  Disease scope: {encounter['disease_scope']}")
        if encounter.get("encounter_date"):
            lines.append(f"  Encounter date: {encounter['encounter_date']}")

    active_conditions = state.get("active_conditions") or []
    if active_conditions:
        lines.append(f"  Active conditions: {', '.join(active_conditions)}")

    active_diagnoses = state.get("active_diagnoses") or []
    if active_diagnoses:
        for diagnosis in active_diagnoses:
            condition = diagnosis.get("condition_name") or diagnosis.get("condition_ref")
            if condition:
                severity = f" ({diagnosis.get('severity')})" if diagnosis.get("severity") else ""
                lines.append(f"  Diagnosis: {condition}{severity}")

    medications = state.get("active_medications") or []
    if medications:
        for medication in medications:
            dose_parts = [
                str(item)
                for item in (
                    medication.get("drug_name"),
                    medication.get("dose"),
                    medication.get("frequency"),
                    medication.get("route"),
                )
                if item
            ]
            lines.append(f"  Medication: {' '.join(dose_parts)}")

    vitals = state.get("most_recent_vitals") or {}
    vital_items = []
    for key, label in (
        ("bp_systolic", "BP systolic"),
        ("bp_diastolic", "BP diastolic"),
        ("heart_rate", "Heart rate"),
        ("respiratory_rate", "Respiratory rate"),
        ("temperature", "Temperature"),
        ("spo2", "SpO2"),
        ("weight_kg", "Weight"),
        ("height_cm", "Height"),
    ):
        if vitals.get(key) is not None:
            vital_items.append(f"{label}: {vitals[key]}")
    if vital_items:
        lines.append(f"  Vitals: {'; '.join(vital_items)}")

    latest_labs = state.get("latest_labs_by_type") or {}
    if latest_labs:
        lab_items = []
        for lab_type, lab in sorted(latest_labs.items()):
            value = lab.get("value")
            unit = lab.get("unit")
            flag = lab.get("flag")
            lab_items.append(
                f"{lab_type}: {value}{f' {unit}' if unit else ''}"
                + (f" ({flag})" if flag and flag != "normal" else "")
            )
        lines.append(f"  Latest labs: {'; '.join(lab_items)}")

    temporal_events = state.get("temporal_events") or {}
    if temporal_events.get("treatment_start_dates"):
        for drug, date_str in temporal_events["treatment_start_dates"].items():
            lines.append(f"  Treatment started: {drug} on {date_str}")
    if temporal_events.get("last_cd4_date"):
        lines.append(f"  Last CD4: {temporal_events['last_cd4_date']}")
    if temporal_events.get("last_viral_load_date"):
        lines.append(f"  Last viral load: {temporal_events['last_viral_load_date']}")

    regimen_history = state.get("regimen_history") or []
    past_meds = [m for m in regimen_history if m.get("status") != "active"]
    if past_meds:
        for med in past_meds:
            stopped = f" (stopped {med['stopped_date']})" if med.get("stopped_date") else ""
            lines.append(f"  Prior medication: {med.get('drug_name', 'unknown')}{stopped}")

    lines.append("[/PATIENT_STATE]")
    return "\n".join(lines)


def _context_as_text(results: list[Any]) -> str:
    blocks = []
    for idx, result in enumerate(results, start=1):
        source = result.guideline_name or f"{result.disease} guidelines"
        blocks.append(f"[{idx}] {source}, {result.section_title}, p.{result.page}\n{result.text}")
    return "\n\n".join(blocks)


def _source_payload(results: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "source": (
                f"{res.guideline_name or res.disease + ' guidelines'}, "
                f"{res.section_title}, p.{res.page}"
            ),
            "text": res.text,
            "disease": res.disease,
            "low_confidence": res.low_confidence,
            "chunk_id": res.chunk_id,
            "parent_id": res.parent_id,
            "confidence": res.score,
        }
        for res in results
    ]


def _extract_hitl_markers(text: str) -> list[dict[str, Any]]:
    import re

    events = []
    pattern = re.compile(r"\[HITL:(CLARIFICATION|MISSING_PARAMS|CONFLICT)(?::([^\]]*))?\]")
    for match in pattern.finditer(text):
        marker_type = match.group(1)
        detail = (match.group(2) or "").strip()
        if marker_type == "CLARIFICATION":
            events.append(
                {
                    "type": "hitl_prompt",
                    "hitl": {
                        "text": detail or "Please clarify your request.",
                        "options": ["Yes", "No", "Provide more details"],
                    },
                }
            )
        elif marker_type == "MISSING_PARAMS":
            events.append(
                {
                    "type": "hitl_prompt",
                    "hitl": {
                        "text": f"Missing required parameters: {detail}. Please provide them.",
                        "options": ["Add parameters"],
                    },
                }
            )
        elif marker_type == "CONFLICT":
            events.append(
                {
                    "type": "hitl_prompt",
                    "hitl": {
                        "text": f"Conflict in guidelines: {detail}. How would you like to proceed?",
                        "options": ["Show both", "Use newest", "Cancel"],
                    },
                }
            )
    return events


def _strip_hitl_markers(text: str) -> str:
    import re

    return re.sub(
        r"\[HITL:(?:CLARIFICATION|MISSING_PARAMS|CONFLICT)(?::[^\]]*)?\]",
        "",
        text,
    ).strip()


def _strip_model_reasoning(text: str) -> str:
    """Fail-safe cleanup for models that leak raw chain-of-thought tags."""
    return re.sub(r"(?is)<think>.*?</think>", "", text).strip()


def _extract_sources_from_messages(messages: list[Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if (
                type(part).__name__ != "ToolReturnPart"
                or getattr(part, "tool_name", "") != "search_guidelines"
            ):
                continue
            content = part.content
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, ValueError):
                    continue
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or "source" not in item:
                    continue
                confidence = item.get("confidence", 1.0)
                sources.append(
                    {
                        "source": item["source"],
                        "text": item.get("text", ""),
                        "disease": item.get("disease", "unknown"),
                        "low_confidence": item.get(
                            "low_confidence",
                            isinstance(confidence, (int, float)) and confidence < 0.45,
                        ),
                        "confidence": confidence,
                        "chunk_id": item.get("chunk_id"),
                        "parent_id": item.get("parent_id"),
                    }
                )
    return sources


def _normalise_disease_id(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return cleaned.replace(" ", "_") if cleaned else ""


def _configured_disease_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {
        "hiv_aids": "hiv",
        "plhiv": "hiv",
        "diabetes_mellitus": "diabetes",
        "type_1_diabetes": "diabetes",
        "type_2_diabetes": "diabetes",
        "t1dm": "diabetes",
        "t2dm": "diabetes",
        "dm": "diabetes",
        "cardiovascular_disease": "cvd",
        "hypertension": "cvd",
        "high_blood_pressure": "cvd",
        "tb": "tb",
        "tuberculosis": "tb",
    }
    for disease, cfg in DISEASE_CONFIG.items():
        candidates = [
            disease,
            cfg.get("display_name", ""),
            cfg.get("table_name", ""),
            *cfg.get("condition_options", []),
            *cfg.get("comorbidity_options", []),
            *cfg.get("validation_keywords", []),
        ]
        for candidate in candidates:
            key = _normalise_disease_id(str(candidate))
            if key:
                aliases.setdefault(key, disease)
        for hint in _DISEASE_QUERY_HINTS.get(disease, []):
            key = _normalise_disease_id(hint)
            if key:
                aliases.setdefault(key, disease)
    return aliases


def _append_unique_disease(
    diseases: list[str],
    available: set,
    value: str,
    aliases: dict[str, str],
) -> None:
    disease = _normalise_disease_id(value)
    if disease in available and disease not in diseases:
        diseases.append(disease)
        return
    mapped = aliases.get(disease)
    if mapped in available and mapped not in diseases:
        diseases.append(mapped)


def _resolve_retrieval_diseases(
    available: list[str],
    context: PatientContext | None,
    query: str = "",
) -> list[str]:
    """Resolve up to three retrieval diseases from query and patient context."""
    available_set = set(available)
    if not available_set:
        return []

    resolved: list[str] = []
    aliases = _configured_disease_aliases()
    for disease in _infer_disease_from_query(query, available):
        _append_unique_disease(resolved, available_set, disease, aliases)

    if context:
        for condition in context.active_conditions:
            _append_unique_disease(resolved, available_set, condition, aliases)
        if context.comorbidity:
            _append_unique_disease(resolved, available_set, context.comorbidity, aliases)

    if not resolved:
        resolved.extend(available[:3])
    return resolved[:3]


def _select_retrieval_disease(
    available: list[str],
    context: PatientContext | None,
    query: str = "",
) -> str | None:
    """Narrow retrieval to one disease only when context is unambiguous."""
    resolved = _resolve_retrieval_diseases(available, context, query)
    return resolved[0] if resolved else None


def _infer_disease_from_query(query: str, available: list[str]) -> list[str]:
    """Infer one or more likely disease targets from the user query."""
    available_set = set(available)
    normalized = f" {re.sub(r'[^a-z0-9]+', ' ', query.lower())} "
    scores: dict[str, int] = {}
    for disease, hints in _DISEASE_QUERY_HINTS.items():
        if disease not in available_set:
            continue
        score = 0
        for hint in hints:
            needle = f" {re.sub(r'[^a-z0-9]+', ' ', hint.lower()).strip()} "
            if needle in normalized:
                score += 1
        if score:
            scores[disease] = score
    if not scores:
        return []
    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], available.index(item[0])),
    )
    top_score = ranked[0][1]
    margin = 1
    return [disease for disease, score in ranked if score >= top_score - margin]


def _is_smalltalk_query(query: str) -> bool:
    """Return True for short conversational turns that should not hit retrieval."""
    normalized = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    if not normalized:
        return True
    available = list(DISEASE_CONFIG)
    if _infer_disease_from_query(normalized, available):
        return False
    if any(f" {hint} " in f" {normalized} " for hint in _CLINICAL_INTENT_HINTS):
        return False
    if normalized in _SMALLTALK_PATTERNS:
        return True
    return bool(len(normalized.split()) <= 4 and any(normalized.startswith(pattern) for pattern in _SMALLTALK_PATTERNS))


async def _query_evidence_context_data(
    disease: list[str] | None,
    query: str,
) -> list[dict[str, Any]]:
    disease_values = disease or []
    if not disease_values:
        return []

    triples: list[dict[str, Any]] = []
    try:
        from .evidence import format_evidence_triples, query_evidence_graph

        for disease_id in disease_values[:3]:
            try:
                results = await query_evidence_graph(
                    disease=disease_id,
                    query=query,
                    top_k=3,
                )
            except Exception as exc:
                logger.warning(
                    "Evidence graph context unavailable for %s: %s",
                    disease_id,
                    exc,
                )
                continue
            for triple in format_evidence_triples(results):
                triples.append({**triple, "disease": disease_id})
    except Exception as exc:
        logger.warning("Evidence graph context unavailable: %s", exc)
        return []
    return triples


def _format_evidence_context_from_triples(triples: list[dict[str, Any]]) -> str:
    lines = []
    for triple in triples:
        lines.append(
            f"[{triple['disease']}] {triple['source']} --{triple['relation']}--> "
            f"{triple['target']} (weight {triple.get('weight')}, source {triple.get('source_ref', '')})"
        )
    return "\n".join(lines)


async def _query_evidence_context(
    disease: list[str] | None,
    query: str,
) -> str:
    """Fetch evidence graph context for one or more disease+query pairs.
    Returns empty string when:
      - no disease targets are resolved
      - graph is empty or unavailable
      - any exception occurs
    Always non-fatal.
    """
    disease_values = disease or []
    if not disease_values:
        return ""

    triples = await _query_evidence_context_data(disease_values, query)
    return _format_evidence_context_from_triples(triples)


async def _query_approved_memory_context(
    context: PatientContext | None,
    limit: int = 8,
) -> str:
    """Fetch approved clinical memory for the current patient context."""
    if not context or get_session_storage_backend() != "postgres":
        return ""
    try:
        from .memory import patient_ref_from_context
        from .repositories import list_long_term_memory

        patient_ref_hash = patient_ref_from_context(context.model_dump())
        rows = await list_long_term_memory(patient_ref_hash)
    except Exception as exc:
        logger.warning("Approved memory context unavailable: %s", exc)
        return ""

    if not rows:
        return ""

    lines = ["[PRIOR_CLINICAL_CONTEXT]"]
    for row in rows[:limit]:
        fact_type = str(row.get("fact_type", "clinical_fact")).strip()
        fact_text = str(row.get("fact_text", "")).strip()
        if fact_text:
            lines.append(f"  - {fact_type}: {fact_text}")
    lines.append("[/PRIOR_CLINICAL_CONTEXT]")
    return "\n".join(lines) if len(lines) > 2 else ""


def _format_pageindex_context(results: list[Any], min_score: float = 0.65) -> str:
    accepted = [row for row in results if getattr(row, "score", 0.0) >= min_score]
    if not accepted:
        return ""
    lines = ["[STRUCTURED_PAGE_CONTEXT]"]
    for row in accepted[:3]:
        summary = str(getattr(row, "summary", "")).strip()
        if not summary:
            summary = str(getattr(row, "text", "")).strip()[:700]
        if not summary:
            continue
        lines.append(
            "  - "
            f"{getattr(row, 'disease', 'unknown')} p.{getattr(row, 'page', 0)} "
            f"({getattr(row, 'section_path', 'Page summary')}): {summary}"
        )
    lines.append("[/STRUCTURED_PAGE_CONTEXT]")
    return "\n".join(lines) if len(lines) > 2 else ""


async def _query_pageindex_context(
    query: str,
    disease: str | None,
) -> str:
    """Search page-level summaries as a bounded pre-LLM retrieval hint."""
    if not _search_index:
        return ""
    try:
        results = await with_timeout(
            _search_index.query_pageindex(query, disease=disease, top_k=3),
            min(CHAT_STREAM_TIMEOUT_SECONDS, PAGEINDEX_CHAT_TIMEOUT_SECONDS),
        )
        return _format_pageindex_context(results)
    except Exception as exc:
        logger.warning("PageIndex context unavailable: %s", exc)
        return ""


def _chat_pageindex_enabled(query: str, disease: str | None) -> bool:
    """Return whether PageIndex should be injected into the chat prompt."""
    mode = os.getenv("CDSS_CHAT_PAGEINDEX_MODE", "off").strip().lower()
    if mode in {"0", "false", "no", "off", "disabled"}:
        return False
    if mode == "always":
        return True
    if mode != "auto":
        logger.warning("Unknown CDSS_CHAT_PAGEINDEX_MODE=%r; disabling chat PageIndex", mode)
        return False
    token_count = len(re.findall(r"\w+", query))
    return bool(disease and token_count >= 5 and not _is_smalltalk_query(query))


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI-compatible provider path
# ─────────────────────────────────────────────────────────────────────────────


def _build_openai_compatible_payload(
    *,
    provider: str,
    query: str,
    context_block: str | None,
    retrieval_results: list[Any],
    history: list[dict[str, str]],
) -> dict[str, Any]:
    from .search_agent import build_system_prompt

    system_content = build_system_prompt(get_available_diseases())
    if context_block:
        system_content = f"{system_content}\n\n{context_block}"

    messages = [
        {"role": "system", "content": system_content},
        *[
            {"role": item["role"], "content": item["content"]}
            for item in history[-MAX_HISTORY_DEPTH:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ],
        {
            "role": "user",
            "content": (
                f"[GUIDELINE_CONTEXT]\n{_context_as_text(retrieval_results)}\n"
                f"[/GUIDELINE_CONTEXT]\n\nQuestion:\n{query}"
            ),
        },
    ]

    payload: dict[str, Any] = {
        "model": get_llm_model(),
        "messages": messages,
        "temperature": 0.1,
        "stream": True,
    }

    reasoning_format = os.getenv("GROQ_REASONING_FORMAT", "hidden").strip()
    if provider == "groq" and reasoning_format:
        payload["reasoning_format"] = reasoning_format

    return payload


async def _stream_openai_compatible_chat(
    *,
    provider: str,
    query: str,
    context_block: str | None,
    retrieval_results: list[Any],
    history: list[dict[str, str]],
) -> AsyncIterator[dict[str, Any]]:
    payload = _build_openai_compatible_payload(
        provider=provider,
        query=query,
        context_block=context_block,
        retrieval_results=retrieval_results,
        history=history,
    )
    endpoint = provider_chat_endpoint(provider)
    headers = {
        **provider_auth_header(provider),
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(
        connect=15.0,
        read=CHAT_STREAM_TIMEOUT_SECONDS,
        write=15.0,
        pool=15.0,
    )
    async with httpx.AsyncClient(timeout=timeout) as client, client.stream(
        "POST",
        endpoint,
        headers=headers,
        json=payload,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            data_line = line.removeprefix("data:").strip()
            if not data_line or data_line == "[DONE]":
                break
            try:
                data = json.loads(data_line)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed provider SSE line: %s", data_line[:200])
                continue
            delta = data.get("choices", [{}])[0].get("delta", {})
            reasoning = (
                delta.get("reasoning")
                or delta.get("reasoning_content")
                or delta.get("reasoning_details")
            )
            if reasoning:
                yield {
                    "type": "reasoning",
                    "summary": reasoning
                    if isinstance(reasoning, str)
                    else json.dumps(reasoning),
                }
            content = delta.get("content")
            if content:
                yield {"type": "chunk", "content": content}


async def _write_session_history(
    session_id: str,
    user_message: str,
    assistant_message: str,
) -> None:
    """Write one exchange to the configured history backend."""
    if get_session_storage_backend() == "postgres":
        try:
            from .repositories import append_session_message

            await append_session_message(session_id, "user", user_message)
            await append_session_message(session_id, "assistant", assistant_message)
        except Exception as exc:
            logger.warning("Postgres session write failed; falling back to memory: %s", exc)
            _mem_append(session_id, user_message, assistant_message)
    else:
        _mem_append(session_id, user_message, assistant_message)


def _mem_append(session_id: str, user_msg: str, assistant_msg: str) -> None:
    if session_id not in _session_history:
        _session_history[session_id] = []
    _session_history[session_id].append({"role": "user", "content": user_msg})
    _session_history[session_id].append({"role": "assistant", "content": assistant_msg})
    _session_history[session_id] = _session_history[session_id][-MAX_HISTORY_DEPTH:]


async def _read_session_history(session_id: str) -> list[dict[str, str]]:
    if get_session_storage_backend() == "postgres":
        try:
            from .repositories import get_session_messages

            return await get_session_messages(session_id, limit=MAX_HISTORY_DEPTH)
        except Exception as exc:
            logger.warning("Postgres session read failed; using in-memory: %s", exc)
    return _session_history.get(session_id, [])[-MAX_HISTORY_DEPTH:]


def _has_patient_context(context: PatientContext | None) -> bool:
    if not context:
        return False
    payload = context.model_dump()
    return any(value not in ("", "Select...", "None", None, [], {}) for value in payload.values())


async def _run_memory_distillation(
    session_id: str,
    context: PatientContext,
) -> dict[str, Any]:
    try:
        from .memory import distill_session_candidates

        created = await with_timeout(
            distill_session_candidates(session_id, context.model_dump()),
            60.0,
        )
        logger.info(
            "Auto memory distillation completed for session %s: %s candidate(s)",
            session_id,
            len(created),
        )
        return {"status": "completed", "created_count": len(created)}
    except TimeoutError:
        logger.warning(
            "Auto memory distillation timed out for session %s",
            session_id,
        )
        return {"status": "degraded", "reason": "timeout"}
    except Exception as exc:
        logger.warning(
            "Auto memory distillation failed for session %s: %s",
            session_id,
            exc,
        )
        return {"status": "degraded", "reason": type(exc).__name__, "detail": str(exc)}


def _queue_memory_distillation(
    session_id: str,
    context: PatientContext | None,
) -> dict[str, Any]:
    if not AUTO_MEMORY_DISTILLATION_ENABLED:
        return {"status": "skipped", "reason": "disabled"}
    if get_session_storage_backend() != "postgres":
        return {
            "status": "degraded",
            "reason": "session_storage_backend",
            "detail": get_session_storage_backend(),
        }
    if not _has_patient_context(context):
        return {"status": "skipped", "reason": "no_patient_context"}

    try:
        asyncio.create_task(_run_memory_distillation(session_id, context))
        return {"status": "queued", "detail": "pending memory distillation"}
    except Exception as exc:
        logger.warning("Auto memory distillation could not be queued: %s", exc)
        return {"status": "degraded", "reason": "queue_failed", "detail": str(exc)}


async def _check_drug_interactions(
    medications: list[str] | None,
) -> dict[str, Any]:
    medication_list = [str(m).strip() for m in (medications or []) if str(m).strip()]
    if not DRUG_INTERACTION_CHECK_ENABLED:
        return {"status": "skipped", "reason": "disabled", "interactions": []}
    if len(medication_list) < 2:
        return {"status": "skipped", "reason": "fewer_than_two_medications", "interactions": []}

    try:
        from .external.clients import RxNormClient

        interactions = await with_timeout(
            RxNormClient(timeout_seconds=DRUG_INTERACTION_CHECK_TIMEOUT_SECONDS).get_interactions(
                medication_list
            ),
            DRUG_INTERACTION_CHECK_TIMEOUT_SECONDS + 1.0,
        )
        if interactions is None:
            return {
                "status": "degraded",
                "reason": "unavailable",
                "medications": medication_list,
                "interactions": [],
            }

        for i in interactions:
            severity = str(i.get("severity", "")).lower()
            if "1" in severity or "contraindicated" in severity:
                i["alert_level"] = "CRITICAL"
            elif "2" in severity:
                i["alert_level"] = "WARNING"
            else:
                i["alert_level"] = "INFO"

        return {
            "status": "ok",
            "medications": medication_list,
            "interactions": interactions,
        }
    except TimeoutError:
        logger.warning("Drug interaction check timed out")
        return {
            "status": "degraded",
            "reason": "timeout",
            "medications": medication_list,
            "interactions": [],
        }
    except Exception as exc:
        logger.warning("Drug interaction check failed: %s", exc)
        return {
            "status": "degraded",
            "reason": type(exc).__name__,
            "detail": str(exc),
            "medications": medication_list,
            "interactions": [],
        }


def _format_drug_interaction_context(interactions: list[dict[str, Any]]) -> str:
    if not interactions:
        return ""
    lines = [
        "[DRUG_INTERACTION_CHECK]",
        "  Source: [Drug Interaction: RxNorm/openFDA, checked at query time]",
    ]
    for item in interactions:
        lines.append(
            "- {drug_a} + {drug_b}: {severity}. {description} [Drug Interaction: {source}]".format(
                drug_a=item.get("drug_a", ""),
                drug_b=item.get("drug_b", ""),
                severity=item.get("severity", "unknown"),
                description=item.get("description", ""),
                source=item.get("source", "RxNorm/openFDA"),
            )
        )
    lines.append("[/DRUG_INTERACTION_CHECK]")
    return "\n".join(lines)


async def _audit_drug_interaction_check(
    session_id: str,
    query_id: str,
    disease: str | None,
    payload: dict[str, Any],
) -> None:
    try:
        from .logs import write_audit_log

        await write_audit_log(
            event_type="DRUG_INTERACTION_CHECK",
            session_id=session_id,
            query_id=query_id,
            disease=disease or "",
            feedback_type="",
            data=payload,
        )
    except Exception as exc:
        logger.warning("Drug interaction audit logging failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health_check(role: str = Depends(get_current_role)):
    lancedb_status = "ok" if _search_index and _search_index._initialized else "error"
    tables_status = "ok"
    indexed_tables: list[str] = []
    missing_tables: list[str] = []
    indexed_diseases: list[str] = []
    missing_diseases: list[str] = []
    pageindex_status = "missing"
    pageindex_detail: dict[str, Any] = {"total": 0, "by_disease": {}}
    database_status = "not_required"
    if _search_index:
        try:
            indexed_tables = _search_index.table_names()
            pageindex_detail = _search_index.pageindex_stats()
            for disease_id, cfg in DISEASE_CONFIG.items():
                table_name = cfg.get("table_name", f"{disease_id}_guidelines")
                has_index = table_name in indexed_tables or (
                    disease_id == "hiv" and "documents" in indexed_tables
                )
                if has_index:
                    indexed_diseases.append(disease_id)
                else:
                    missing_diseases.append(disease_id)
                    missing_tables.append(table_name)
            pageindex_diseases = set(pageindex_detail.get("by_disease", {}))
            indexed_without_pageindex = [
                disease
                for disease in indexed_diseases
                if disease in DISEASE_CONFIG and disease not in pageindex_diseases
            ]
            if pageindex_detail.get("total", 0) <= 0:
                pageindex_status = "missing"
            elif indexed_without_pageindex:
                pageindex_status = "partial"
            else:
                pageindex_status = "ok"
            if missing_diseases and indexed_diseases:
                tables_status = "partial"
            elif missing_diseases:
                tables_status = f"missing: {', '.join(missing_tables)}"
        except Exception as exc:
            tables_status = f"error: {exc}"
            lancedb_status = "error"
    if get_session_storage_backend() == "postgres" or get_audit_storage_backend() == "postgres":
        try:
            from .db import check_database

            database_status = "ok" if await with_timeout(check_database(), 2.0) else "error"
        except Exception as exc:
            database_status = f"error: {exc}"
    llm_online = provider_has_credentials()
    llm_status = "ok" if llm_online else "missing_credentials"
    overall = (
        "error"
        if lancedb_status != "ok"
        else (
            "degraded"
            if (
                llm_status != "ok"
                or tables_status != "ok"
                or database_status not in {"ok", "not_required"}
            )
            else "ok"
        )
    )
    return {
        "status": overall,
        "mode": "agent" if llm_online else "kb_only",
        "role": role,
        "llm_provider": get_llm_provider(),
        "llm_model": get_llm_model(),
        "components": {
            "lancedb": lancedb_status,
            "tables": tables_status,
            "indexed_diseases": indexed_diseases,
            "missing_diseases": missing_diseases,
            "indexed_tables": indexed_tables,
            "missing_tables": missing_tables,
            "pageindex": pageindex_status,
            "pageindex_detail": pageindex_detail,
            "database": database_status,
            "llm_api": llm_status,
        },
    }


@app.get("/metrics")
async def metrics():
    return metrics_text()


@app.get("/phase7/status")
async def phase7_status():
    from .phase7 import PHASE7_STATUS

    return PHASE7_STATUS


@app.get("/health/db")
async def database_health_check(role: str = Depends(get_current_role)):
    try:
        from .db import check_database

        ok = await check_database()
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503, detail=f"Database dependency missing: {exc.name}"
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    return {"status": "ok" if ok else "error", "role": role}


@app.get("/diseases")
async def list_diseases():
    indexed_tables: set = set()
    pageindex_by_disease: dict[str, int] = {}
    graph_by_disease: dict[str, dict[str, int]] = {}
    if _search_index and _search_index._initialized:
        try:
            indexed_tables = set(_search_index.table_names())
            pageindex_by_disease = _search_index.pageindex_stats().get("by_disease", {})
        except Exception:
            pass
    if get_audit_storage_backend() == "postgres" or get_session_storage_backend() == "postgres":
        try:
            from .repositories import evidence_graph_stats

            graph_by_disease = (await evidence_graph_stats()).get("by_disease_detail", {})
        except Exception:
            graph_by_disease = {}
    diseases = []
    for d_id, cfg in DISEASE_CONFIG.items():
        table_name = cfg.get("table_name", f"{d_id}_guidelines")
        source_mode = None
        is_indexed = table_name in indexed_tables
        if is_indexed:
            source_mode = "guideline_table"
        elif d_id == "hiv" and "documents" in indexed_tables:
            is_indexed = True
            source_mode = "legacy_documents"
        chunk_count = None
        table_to_count = table_name if table_name in indexed_tables else None
        if d_id == "hiv" and "documents" in indexed_tables:
            table_to_count = "documents"
        if table_to_count and _search_index:
            with suppress(Exception):
                chunk_count = _search_index.db.open_table(table_to_count).count_rows()
        pageindex_rows = int(pageindex_by_disease.get(d_id, 0) or 0)
        graph_counts = graph_by_disease.get(d_id, {})
        graph_nodes = int(graph_counts.get("nodes", 0) or 0)
        graph_edges = int(graph_counts.get("edges", 0) or 0)
        diseases.append(
            {
                "id": d_id,
                "display_name": cfg["display_name"],
                "guideline": cfg["guideline_name"],
                "status": "indexed" if is_indexed else "not_indexed",
                "source_mode": source_mode,
                "table_name": table_name,
                "guideline_warning": cfg.get("guideline_warning"),
                "chunk_count": chunk_count,
                "pageindex_rows": pageindex_rows,
                "pageindex_status": "ready" if pageindex_rows > 0 else "missing",
                "graph_nodes": graph_nodes,
                "graph_edges": graph_edges,
                "graph_status": "ready" if graph_nodes > 0 else "missing",
            }
        )
    return {"diseases": diseases}


@app.get("/context-options")
async def get_context_options(disease: str = "hiv"):
    if disease not in DISEASE_CONFIG:
        raise HTTPException(status_code=404, detail="Disease not configured")
    cfg = DISEASE_CONFIG[disease]
    return {
        "patient_types": cfg["population_options"],
        "conditions": cfg["condition_options"],
        "comorbidities": cfg["comorbidity_options"],
        "filters": cfg["filter_options"],
        "clinical_params": cfg.get("clinical_params", []),
    }


@app.post("/drug-interactions/check")
async def check_drug_interactions(
    request: DrugInteractionCheckRequest, role: str = Depends(get_current_role)
):
    payload = await _check_drug_interactions(request.medications)
    return {"status": payload.get("status", "ok"), "role": role, **payload}


@app.post("/alerts/override")
async def override_alert(request: AlertOverrideRequest, role: str = Depends(get_current_role)):
    from .repositories import create_alert_override

    return await create_alert_override(
        session_id=request.session_id,
        alert_type=request.alert_type,
        alert_level=request.alert_level,
        alert_summary=request.alert_summary,
        override_reason=request.override_reason,
        clinician_role=role,
        patient_ref=request.patient_ref_hash,
    )


@app.get("/admin/alerts/override-report")
async def get_override_report(role: str = Depends(require_admin)):
    from .repositories import get_alert_override_report

    return await get_alert_override_report()


@app.post("/clinical/score")
async def compute_clinical_score(
    request: ClinicalScoreRequest,
    role: str = Depends(get_current_role),
):
    from .scoring import ClinicalScorer

    scorer_fn = getattr(ClinicalScorer, request.scorer, None)
    if not scorer_fn or not callable(scorer_fn):
        raise HTTPException(status_code=400, detail=f"Unknown scorer: {request.scorer!r}")

    try:
        inp = request.inputs
        # Dispatch matches actual ClinicalScorer method signatures
        if request.scorer == "news2":
            result = scorer_fn(inp)  # news2(vitals: dict)
        elif request.scorer == "egfr_ckd_stage":
            result = scorer_fn(  # egfr_ckd_stage(creatinine, age, sex)
                float(inp["creatinine"]),
                int(inp["age"]),
                str(inp["sex"]),
            )
        elif request.scorer == "who_hiv_stage":
            result = scorer_fn(  # who_hiv_stage(clinical_features, cd4)
                clinical_features=inp.get("clinical_features", []),
                cd4=inp.get("cd4"),
            )
        elif request.scorer == "child_pugh":
            result = scorer_fn(  # child_pugh(labs: dict, clinical: dict)
                labs=inp.get("labs", {}),
                clinical=inp.get("clinical", {}),
            )
        elif request.scorer == "malaria_severity":
            result = scorer_fn(  # malaria_severity(vitals, labs, clinical)
                vitals=inp.get("vitals", {}),
                labs=inp.get("labs", {}),
                clinical=inp.get("clinical", {}),
            )
        elif request.scorer == "diabetes_risk_hba1c":
            result = scorer_fn(  # diabetes_risk_hba1c(hba1c, fpg)
                hba1c=inp.get("hba1c"),
                fpg=inp.get("fpg"),
            )
        elif request.scorer == "cvd_risk_score":
            result = scorer_fn(  # cvd_risk_score(age, sex, bp, chol, smoking, diabetes)
                age=int(inp["age"]),
                sex=str(inp["sex"]),
                bp_systolic=int(inp["bp_systolic"]),
                total_cholesterol=float(inp["total_cholesterol"]),
                smoking=bool(inp.get("smoking", False)),
                diabetes=bool(inp.get("diabetes", False)),
            )
        else:
            raise HTTPException(
                status_code=400, detail=f"No dispatch rule for scorer: {request.scorer!r}"
            )
    except HTTPException:
        raise
    except (KeyError, TypeError) as exc:
        return {"status": "incomplete_inputs", "missing": str(exc)}
    except ValueError as exc:
        return {"status": "incomplete_inputs", "missing": str(exc)}

    # Map score to alert level
    alert_level = "INFO"
    if request.scorer == "news2":
        score = result.get("score", 0)
        if score >= 7:
            alert_level = "CRITICAL"
        elif score >= 5:
            alert_level = "WARNING"
        elif score >= 1:
            alert_level = "INFO"
        else:
            alert_level = "BACKGROUND"
    elif request.scorer == "malaria_severity":
        alert_level = "CRITICAL" if result.get("is_severe") else "INFO"
    elif request.scorer == "who_hiv_stage":
        alert_level = "WARNING" if result.get("stage", 1) >= 3 else "INFO"
    elif request.scorer == "egfr_ckd_stage":
        egfr = float(result.get("egfr", 100))
        if egfr < 30:
            alert_level = "WARNING"
        elif egfr < 45:
            alert_level = "INFO"
        else:
            alert_level = "BACKGROUND"
    elif request.scorer == "diabetes_risk_hba1c":
        alert_level = "WARNING" if result.get("intensification_indicated") else "INFO"
    elif request.scorer == "cvd_risk_score":
        risk_pct = float(result.get("ten_year_risk_pct", 0))
        if risk_pct >= 30:
            alert_level = "WARNING"
        elif risk_pct >= 20:
            alert_level = "INFO"
        else:
            alert_level = "BACKGROUND"

    # Write audit log
    from .logs import write_audit_log

    try:
        await write_audit_log(
            event_type="CLINICAL_SCORE",
            session_id=request.patient_ref or "",
            query_id=str(uuid.uuid4()),
            disease=request.scorer,
            feedback_type="",
            data={"inputs": request.inputs, "result": result, "alert_level": alert_level},
        )
    except Exception as exc:
        logger.warning("Clinical score audit log failed: %s", exc)

    return {
        "score_result": result,
        "alert_level": alert_level,
        "source_guideline": result.get("source_guideline", ""),
    }


@app.post("/patient/encounter")
async def create_patient_encounter(
    request: PatientEncounterCreateRequest,
    role: str = Depends(get_current_role),
):
    from .memory import patient_ref_from_context
    from .repositories import (
        create_encounter,
        upsert_diagnoses,
        upsert_medications,
        upsert_vitals,
    )

    patient_ref_hash = patient_ref_from_context(request.patient_context.model_dump())
    disease_scope = (
        request.disease_scope or (request.patient_context.active_conditions or ["all"])[0] or "all"
    )
    encounter = await create_encounter(
        patient_ref=patient_ref_hash,
        disease_scope=disease_scope,
        encounter_type=request.encounter_type,
        clinician_role=role,
    )

    vitals = {
        "bp_systolic": request.patient_context.clinical_params.get("bp_systolic")
        or request.patient_context.clinical_params.get("systolic_bp"),
        "bp_diastolic": request.patient_context.clinical_params.get("bp_diastolic")
        or request.patient_context.clinical_params.get("diastolic_bp"),
        "heart_rate": request.patient_context.clinical_params.get("heart_rate")
        or request.patient_context.clinical_params.get("pulse"),
        "respiratory_rate": request.patient_context.clinical_params.get("respiratory_rate")
        or request.patient_context.clinical_params.get("rr"),
        "temperature": request.patient_context.clinical_params.get("temperature"),
        "spo2": request.patient_context.clinical_params.get("spo2")
        or request.patient_context.clinical_params.get("o2_saturation"),
        "weight_kg": request.patient_context.clinical_params.get("weight_kg")
        or request.patient_context.clinical_params.get("weight"),
        "height_cm": request.patient_context.clinical_params.get("height_cm")
        or request.patient_context.clinical_params.get("height"),
        "consciousness": request.patient_context.clinical_params.get("consciousness"),
        "supplemental_o2": request.patient_context.clinical_params.get("supplemental_o2"),
        "spo2_scale": request.patient_context.clinical_params.get("spo2_scale"),
    }
    vitals = {key: value for key, value in vitals.items() if value not in (None, "", "Select...")}
    if vitals:
        await upsert_vitals(patient_ref_hash, encounter["encounter_id"], vitals)

    medications = [
        {"drug_name": medication, "status": "active", "prescribed_by": role}
        for medication in request.patient_context.medications
    ]
    if medications:
        await upsert_medications(
            patient_ref_hash,
            encounter["encounter_id"],
            medications,
        )

    diagnoses = [
        {"condition_name": condition, "status": "active"}
        for condition in request.patient_context.active_conditions
    ]
    if diagnoses:
        await upsert_diagnoses(
            patient_ref_hash,
            encounter["encounter_id"],
            diagnoses,
        )

    return {
        "encounter_id": encounter["encounter_id"],
        "patient_ref_hash": patient_ref_hash,
        "disease_scope": disease_scope,
        "encounter_type": encounter["encounter_type"],
    }


@app.get("/patient/state/{patient_ref_hash}")
async def get_patient_state_endpoint(patient_ref_hash: str, role: str = Depends(get_current_role)):
    from .patient_state import get_patient_state

    return await get_patient_state(patient_ref_hash)


@app.post("/patient/vitals")
async def upsert_patient_vitals(
    request: PatientVitalsUpsertRequest,
    role: str = Depends(get_current_role),
):
    from .repositories import upsert_vitals

    return {
        "vitals": await upsert_vitals(
            request.patient_ref_hash, request.encounter_id, request.vitals
        )
    }


@app.post("/patient/labs")
async def upsert_patient_labs(
    request: PatientLabsUpsertRequest,
    role: str = Depends(get_current_role),
):
    from .repositories import upsert_labs

    return {"labs": await upsert_labs(request.patient_ref_hash, request.encounter_id, request.labs)}


@app.get("/guidelines/{disease}/toc")
async def get_guideline_toc(disease: str):
    if not _search_index:
        raise HTTPException(status_code=503, detail="Search index not ready")
    table_names = _search_index._get_table_names(disease)
    if not table_names:
        raise HTTPException(status_code=404, detail="Guideline not found")
    try:
        table_name = table_names[0]
        if table_name == "documents":
            return {"toc": _search_index.legacy_toc()}
        df = _search_index.db.open_table(table_name).search().limit(1000).to_pandas()
        toc = []
        if not df.empty:
            seen: set = set()
            for _, row in df.iterrows():
                pid = str(row.get("parent_id", ""))
                title = str(row.get("section_title", "")).strip()
                if pid and title and pid not in seen:
                    seen.add(pid)
                    toc.append(
                        {
                            "id": pid,
                            "title": title,
                            "level": 1,
                            "page": int(row.get("page", 0) or 0),
                        }
                    )
        return {"toc": toc}
    except Exception as exc:
        logger.error("Failed to generate TOC for %s: %s", disease, exc)
        return {"toc": []}


@app.get("/guidelines/{disease}/section/{section_id}")
async def get_guideline_section(disease: str, section_id: str):
    if not _search_index:
        raise HTTPException(status_code=503, detail="Search index not ready")
    result = _search_index.get_section(section_id, disease)
    if not result:
        raise HTTPException(status_code=404, detail="Section not found")
    return {
        "text": result.parent_text or result.text,
        "title": result.section_title,
        "source": result.guideline_name or f"{result.disease.upper()} Guidelines",
        "page": result.page,
        "source_url": result.source_url,
        "chunk_id": result.chunk_id,
        "parent_id": result.parent_id,
    }


@app.post("/pageindex/query")
async def query_pageindex(request: PageIndexQueryRequest):
    if not _search_index:
        raise HTTPException(status_code=503, detail="Search index not ready")
    results = await _search_index.query_pageindex(
        request.query,
        disease=request.disease,
        top_k=request.top_k,
    )
    return {
        "results": [
            {
                "disease": r.disease,
                "page": r.page,
                "section_path": r.section_path,
                "summary": r.summary,
                "text": r.text,
                "score": r.score,
            }
            for r in results
        ]
    }


@app.get("/pageindex/stats")
async def pageindex_stats(role: str = Depends(get_current_role)):
    if not _search_index:
        raise HTTPException(status_code=503, detail="Search index not ready")
    return _search_index.pageindex_stats()


@app.post("/kb/lookup")
async def lookup_structured_kb(
    request: StructuredKBQueryRequest, role: str = Depends(get_current_role)
):
    if request.disease not in DISEASE_CONFIG:
        raise HTTPException(status_code=404, detail="Disease not configured")
    if not _search_index:
        raise HTTPException(status_code=503, detail="Search index not ready")
    try:
        result = await with_timeout(
            _search_index.lookup_kb(
                query_type=request.query_type,
                disease=request.disease,
                filters=request.filters,
                session_id=f"kb-{uuid.uuid4()}",
                query_id=f"kb-{uuid.uuid4()}",
            ),
            5,
        )
    except Exception as exc:
        logger.warning("Structured KB lookup failed: %s", exc)
        return {
            "status": "degraded",
            "reason": str(exc),
            "disease": request.disease,
            "query_type": request.query_type,
            "filters": request.filters,
        }
    if result is None:
        return {
            "status": "not_found",
            "disease": request.disease,
            "query_type": request.query_type,
            "filters": request.filters,
        }
    return {
        "status": "ok",
        "result": {
            "data": result.data,
            "text": result.text,
            "source": result.source,
            "disease": result.disease,
            "table_type": result.table_type,
            "confidence": result.confidence,
        },
    }


@app.post("/feedback")
async def post_feedback(request: FeedbackRequest):
    await log_feedback(
        session_id=request.session_id,
        message_id=request.message_id,
        feedback_type=request.feedback_type,
        note=request.note or "",
    )
    if request.correction or request.sources_used:
        await log_correction(
            request.session_id,
            request.message_id,
            request.feedback_type,
            request.correction or "",
            request.sources_used,
        )
    if get_session_storage_backend() == "postgres":
        try:
            from .repositories import write_feedback_db

            await write_feedback_db(
                request.session_id,
                request.message_id,
                request.feedback_type,
                request.note or "",
                request.correction or "",
                request.sources_used,
            )
        except Exception as exc:
            logger.warning("Feedback Postgres write failed: %s", exc)
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Shadow UMLS telemetry (fire-and-forget — never touches the live chat path)
# ─────────────────────────────────────────────────────────────────────────────


async def _shadow_umls_telemetry(
    query: str,
    disease: str | None,
    session_id: str,
    query_id: str,
) -> None:
    """Run in the background via asyncio.create_task() only when explicitly enabled
    with CDSS_TERMINOLOGY_SHADOW_ENABLED=true.

    Two shadow operations:
      1. UMLS Query Extraction  — link the user query text to UMLS concepts and
         log the matched CUIs to the audit log.  The result is NOT fed back to
         the LLM or the retrieval pipeline.

      2. Optional UMLS Shadow Retrieval — construct an expanded query using the
         matched concept preferred_names and run a parallel guideline search.
         This is separately gated by CDSS_TERMINOLOGY_SHADOW_RETRIEVAL_ENABLED.

    Both operations are wrapped in a broad try/except so any failure here is
    a silent warning and never bubbles up to the HTTP response.
    """
    try:
        from .logs import write_audit_log
        from .terminology.service import expand_query_with_terminology_details

        expanded_query, concepts = await expand_query_with_terminology_details(
            query=query,
            disease=disease,
        )

        if concepts:
            cui_list = [
                {"cui": c["cui"], "preferred_name": c.get("preferred_name", "")}
                for c in concepts
                if c.get("cui")
            ]
            await write_audit_log(
                event_type="UMLS_QUERY_EXTRACTION",
                session_id=session_id,
                query_id=query_id,
                disease=disease or "",
                feedback_type="",
                data={"concepts": cui_list, "concept_count": len(cui_list)},
            )

        # ── 2. Shadow retrieval ──────────────────────────────────────────
        # Only run when: we have concepts, we have a search index, disease
        # is known (shadow fan-out across all diseases is too expensive), and
        # terminology expansion changed the query.
        if (
            TERMINOLOGY_SHADOW_RETRIEVAL_ENABLED
            and concepts
            and _search_index
            and disease
            and expanded_query != query
        ):
            shadow_query = expanded_query
            shadow_results = await asyncio.wait_for(
                _search_index.search_guidelines(
                    query=shadow_query,
                    disease=disease,
                    session_id=session_id,
                    # Disambiguate shadow query_id so it never collides with
                    # the real query_id in retrieval logs.
                    query_id=f"{query_id}_umls_shadow",
                    k_final=5,
                ),
                timeout=TERMINOLOGY_SHADOW_TIMEOUT_SECONDS,
            )
            shadow_payload = _source_payload(shadow_results)
            await write_audit_log(
                event_type="UMLS_SHADOW_RETRIEVAL",
                session_id=session_id,
                query_id=query_id,
                disease=disease,
                feedback_type="",
                data={
                    "shadow_query": shadow_query,
                    "results_count": len(shadow_payload),
                    "results": shadow_payload,
                },
            )

    except Exception as exc:
        logger.warning("Shadow UMLS telemetry failed (non-fatal): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Chat stream
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    x_user_role: str | None = Header(None),
):
    if not _search_index:
        raise HTTPException(status_code=503, detail="Search index not ready")

    query_id = str(uuid.uuid4())
    available = get_available_diseases()
    get_current_role(x_user_role)  # validates header

    await log_query(
        session_id=request.session_id,
        query_text=request.message,
        disease_targets=available,
        patient_context=request.context.model_dump() if request.context else None,
    )

    history = await _read_session_history(request.session_id)
    context_block = _build_context_block(request.context) if request.context else None
    patient_state_context = None
    patient_state: dict[str, Any] = {}
    if request.patient_ref_hash:
        try:
            from .patient_state import get_patient_state

            patient_state = await get_patient_state(request.patient_ref_hash)
            patient_state_context = _format_patient_state_context(patient_state)
        except Exception as exc:
            logger.warning(
                "Patient state context unavailable for %s: %s",
                request.patient_ref_hash,
                exc,
            )

    patient_scores = []
    if patient_state:
        from .scoring import _compute_patient_scores

        patient_scores = _compute_patient_scores(patient_state)
        if patient_scores:
            score_lines = ["[CLINICAL_SCORE]"]
            for s in patient_scores:
                if s["alert_level"] in ("INFO", "WARNING", "CRITICAL"):
                    score_lines.append(
                        f"  {s['scorer'].upper()}: {s['score_result']} (Alert: {s['alert_level']})"
                    )
            score_lines.append("[/CLINICAL_SCORE]")
            if len(score_lines) > 2:
                score_block = "\n".join(score_lines)
                patient_state_context = (
                    f"{patient_state_context}\n\n{score_block}"
                    if patient_state_context
                    else score_block
                )

    temporal_flags = []
    if patient_state:
        try:
            from .patient_state import detect_temporal_flags

            temporal_flags = detect_temporal_flags(patient_state)
        except Exception as exc:
            logger.warning("Temporal flag detection failed: %s", exc)
        if temporal_flags:
            flag_lines = ["[TEMPORAL_FLAGS]"]
            for flag in temporal_flags:
                flag_lines.append(
                    f"  [{flag['severity'].upper()}] {flag['message']} [{flag['guideline_ref']}]"
                )
            flag_lines.append("[/TEMPORAL_FLAGS]")
            flag_block = "\n".join(flag_lines)
            patient_state_context = (
                f"{patient_state_context}\n\n{flag_block}" if patient_state_context else flag_block
            )

    context_block = patient_state_context or context_block
    disease_targets = _resolve_retrieval_diseases(available, request.context, request.message)
    disease_target = disease_targets[0] if disease_targets else None

    if TERMINOLOGY_SHADOW_ENABLED:
        # Fire-and-forget telemetry is opt-in so terminology cannot affect the
        # live request path by default. It never mutates prompt or retrieval state.
        asyncio.create_task(
            _shadow_umls_telemetry(
                query=request.message,
                disease=disease_target,
                session_id=request.session_id,
                query_id=query_id,
            )
        )

    async def run_stream():
        started_at = time.perf_counter()
        full_text = ""
        sources: list[dict[str, Any]] = []
        stream_context_block = context_block

        try:
            provider = get_llm_provider()

            yield f"data: {json.dumps({'type': 'activity', 'message': 'Received query', 'detail': f'Session {request.session_id[:8]}'})}\n\n"
            yield f"data: {json.dumps({'type': 'activity', 'message': 'Patient context', 'detail': 'Attached' if context_block else 'None'})}\n\n"

            if patient_state:
                diag_count = len(patient_state.get("active_diagnoses") or [])
                med_count = len(patient_state.get("active_medications") or [])
                flag_count = len(temporal_flags)
                detail_parts = [f"{diag_count} diagnoses", f"{med_count} medications"]
                if flag_count:
                    detail_parts.append(f"{flag_count} flags")
                yield f"data: {json.dumps({'type': 'activity', 'message': 'Patient state loaded', 'detail': ', '.join(detail_parts)})}\n\n"

            if patient_scores:
                high_alerts = [
                    s for s in patient_scores if s["alert_level"] in ("WARNING", "CRITICAL")
                ]
                if high_alerts:
                    yield f"data: {json.dumps({'type': 'clinical_score', 'scores': high_alerts})}\n\n"

            if _is_smalltalk_query(request.message):
                full_text = (
                    "Hello. Ask a clinical question or select patient context, "
                    "and I will answer using the indexed Kenya guideline sources."
                )
                latency = (time.perf_counter() - started_at) * 1000
                yield f"data: {json.dumps({'type': 'activity', 'message': 'Conversational turn', 'detail': 'No guideline retrieval needed'})}\n\n"
                yield f"data: {json.dumps({'type': 'chunk', 'content': full_text})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'full_text': full_text, 'timestamp': format_timestamp(), 'latency_ms': round(latency, 2)})}\n\n"
                yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
                await _write_session_history(request.session_id, request.message, full_text)
                await log_response(
                    session_id=request.session_id,
                    query_id=query_id,
                    response_length=len(full_text),
                    sources_cited=[],
                    total_latency_ms=latency,
                )
                return

            memory_context = await _query_approved_memory_context(request.context)
            if memory_context:
                stream_context_block = (
                    f"{stream_context_block}\n\n{memory_context}"
                    if stream_context_block
                    else memory_context
                )
                yield f"data: {json.dumps({'type': 'activity', 'message': 'Prior clinical memory', 'detail': 'Attached'})}\n\n"

            drug_interaction_payload = await _check_drug_interactions(
                request.context.medications if request.context else None
            )
            await _audit_drug_interaction_check(
                request.session_id,
                query_id,
                disease_target,
                drug_interaction_payload,
            )
            drug_interaction_context = _format_drug_interaction_context(
                drug_interaction_payload.get("interactions", [])
            )
            if drug_interaction_context:
                stream_context_block = (
                    f"{stream_context_block}\n\n{drug_interaction_context}"
                    if stream_context_block
                    else drug_interaction_context
                )
                yield f"data: {json.dumps({'type': 'activity', 'message': 'Drug interaction check', 'detail': f'{len(drug_interaction_payload.get('interactions', []))} found'})}\n\n"
                yield f"data: {json.dumps({'type': 'drug_interactions', **drug_interaction_payload})}\n\n"
            elif drug_interaction_payload.get("status") in {"degraded", "ok"}:
                detail = (
                    "unavailable"
                    if drug_interaction_payload.get("status") == "degraded"
                    else "none"
                )
                yield f"data: {json.dumps({'type': 'activity', 'message': 'Drug interaction check', 'detail': detail})}\n\n"
                yield f"data: {json.dumps({'type': 'drug_interactions', **drug_interaction_payload})}\n\n"

            if _chat_pageindex_enabled(request.message, disease_target):
                pageindex_context = await _query_pageindex_context(request.message, disease_target)
                if pageindex_context:
                    stream_context_block = (
                        f"{stream_context_block}\n\n{pageindex_context}"
                        if stream_context_block
                        else pageindex_context
                    )
                    yield f"data: {json.dumps({'type': 'activity', 'message': 'PageIndex', 'detail': 'Attached'})}\n\n"

            # ── Always attempt evidence graph injection regardless of mode ──
            evidence_triples = await _query_evidence_context_data(disease_targets, request.message)
            evidence_context = _format_evidence_context_from_triples(evidence_triples)
            if evidence_triples:
                yield f"data: {json.dumps({'type': 'evidence', 'triples': evidence_triples})}\n\n"
            if evidence_context:
                graph_block = f"[EVIDENCE_GRAPH]\n{evidence_context}\n[/EVIDENCE_GRAPH]"
                stream_context_block = (
                    f"{stream_context_block}\n\n{graph_block}"
                    if stream_context_block
                    else graph_block
                )
                yield f"data: {json.dumps({'type': 'activity', 'message': 'Evidence graph', 'detail': 'Attached'})}\n\n"

            kb_context = ""
            if disease_target:
                kb_context = await _lookup_structured_kb_context(
                    request.message,
                    disease_target,
                    request.session_id,
                    query_id,
                )
                if kb_context:
                    stream_context_block = (
                        f"{kb_context}\n\n{stream_context_block}"
                        if stream_context_block
                        else kb_context
                    )
                    yield f"data: {json.dumps({'type': 'activity', 'message': 'Structured KB', 'detail': 'Attached'})}\n\n"

            search_query = request.message
            expanded_query_for_log: str | None = None

            # ── Offline / KB-only mode ────────────────────────────────────
            if not provider_has_credentials(provider):
                yield f"data: {json.dumps({'type': 'activity', 'message': 'Offline mode', 'detail': f'No {provider} credentials — returning raw guideline passages'})}\n\n"
                results = await with_timeout(
                    _search_index.search_guidelines(
                        query=request.message,
                        disease=disease_targets,
                        session_id=request.session_id,
                        query_id=query_id,
                        k_final=3,
                        search_query=search_query,
                    ),
                    CHAT_STREAM_TIMEOUT_SECONDS,
                )
                sources = _source_payload(results)
                for i, res in enumerate(results):
                    chunk_text = (
                        f"### Source {i + 1}: {res.section_title}\n"
                        f"*{res.disease.upper()} Guidelines, p.{res.page}*\n\n"
                        f"{res.text}\n\n---\n\n"
                    )
                    full_text += chunk_text
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk_text})}\n\n"

                # In offline mode, surface evidence graph context as a text block
                # after retrieved passages so the clinician sees it
                if evidence_context:
                    graph_text = "\n\n### Validated Clinical Relationships\n" + evidence_context
                    full_text += graph_text
                    yield f"data: {json.dumps({'type': 'chunk', 'content': graph_text})}\n\n"

                latency = (time.perf_counter() - started_at) * 1000
                yield f"data: {json.dumps({'type': 'done', 'full_text': full_text, 'timestamp': format_timestamp(), 'latency_ms': round(latency, 2)})}\n\n"
                yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
                await _write_session_history(request.session_id, request.message, full_text)
                await log_response(
                    session_id=request.session_id,
                    query_id=query_id,
                    response_length=len(full_text),
                    sources_cited=sources,
                    total_latency_ms=latency,
                )
                return

            # ── Online provider path (Groq / Puter) ──────────────────────
            if provider not in {"groq", "puter"}:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Unsupported provider: {provider!r}. Set QUERY_LLM_PROVIDER=groq or puter.'})}\n\n"
                return

            use_hyde = bool(
                len(disease_targets) == 1
                and disease_target
                and DISEASE_CONFIG.get(disease_target, {}).get("use_hyde")
            )
            yield f"data: {json.dumps({'type': 'activity', 'message': 'Searching guidelines', 'detail': ', '.join(disease_targets) if disease_targets else 'all'})}\n\n"

            if TERMINOLOGY_QUERY_EXPANSION_ENABLED and disease_target:
                from .terminology.service import expand_query_with_terminology_details

                expanded_query_for_log, concepts = await expand_query_with_terminology_details(
                    query=request.message,
                    disease=disease_target,
                )
                if expanded_query_for_log != request.message:
                    search_query = expanded_query_for_log
                    concept_payload = [
                        {"cui": c.get("cui", ""), "preferred_name": c.get("preferred_name", "")}
                        for c in concepts
                        if c.get("cui")
                    ]
                    if concept_payload:
                        yield f"data: {json.dumps({'type': 'concepts', 'concepts': concept_payload})}\n\n"
                    yield f"data: {json.dumps({'type': 'activity', 'message': 'Terminology expansion', 'detail': str(len(concepts))})}\n\n"

            results = await with_timeout(
                _search_index.search_guidelines(
                    query=request.message,
                    disease=disease_targets,
                    session_id=request.session_id,
                    query_id=query_id,
                    k_final=5,
                    use_hyde=use_hyde,
                    search_query=search_query,
                ),
                CHAT_STREAM_TIMEOUT_SECONDS,
            )
            sources = _source_payload(results)
            yield f"data: {json.dumps({'type': 'activity', 'message': 'Retrieved passages', 'detail': str(len(results))})}\n\n"
            yield f"data: {json.dumps({'type': 'activity', 'message': 'Calling provider', 'detail': f'{provider}: {get_llm_model()}'})}\n\n"

            reasoning_text = ""
            async for provider_event in _stream_openai_compatible_chat(
                provider=provider,
                query=request.message,
                context_block=stream_context_block,
                retrieval_results=results,
                history=history,
            ):
                if provider_event.get("type") == "reasoning":
                    reasoning_text += str(provider_event.get("summary", ""))
                    continue
                if provider_event.get("type") != "chunk":
                    continue
                token = str(provider_event.get("content", ""))
                full_text += token
                yield f"data: {json.dumps({'type': 'chunk', 'content': token})}\n\n"

            if reasoning_text:
                yield f"data: {json.dumps({'type': 'reasoning', 'summary': reasoning_text[:1600]})}\n\n"

            raw_text = full_text or "No response returned."
            cleaned_text = _strip_hitl_markers(_strip_model_reasoning(raw_text))
            if cleaned_text != full_text:
                logger.warning(
                    "Provider leaked hidden reasoning or HITL markers after streaming; "
                    "stored response was sanitized but streamed tokens may already be visible."
                )
                full_text = cleaned_text
            if not full_text:
                full_text = "No response returned."
                yield f"data: {json.dumps({'type': 'chunk', 'content': full_text})}\n\n"

            for hitl_event in _extract_hitl_markers(raw_text):
                yield f"data: {json.dumps(hitl_event)}\n\n"

            latency = (time.perf_counter() - started_at) * 1000
            yield f"data: {json.dumps({'type': 'done', 'full_text': full_text, 'timestamp': format_timestamp(), 'latency_ms': round(latency, 2)})}\n\n"
            yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"

            await _write_session_history(request.session_id, request.message, full_text)
            await log_response(
                session_id=request.session_id,
                query_id=query_id,
                response_length=len(full_text),
                sources_cited=sources,
                total_latency_ms=latency,
            )

        except TimeoutError:
            logger.error("Stream timeout after %ss", CHAT_STREAM_TIMEOUT_SECONDS)
            await log_error(
                request.session_id,
                query_id,
                "TimeoutError",
                f"chat_stream exceeded {CHAT_STREAM_TIMEOUT_SECONDS}s",
                recovery_action="cancelled",
            )
            yield f"data: {json.dumps({'type': 'error', 'message': 'Request timed out before completion.'})}\n\n"
        except Exception as exc:
            logger.error("Stream error: %s", exc, exc_info=True)
            await log_error(request.session_id, query_id, type(exc).__name__, str(exc))
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield 'data: {"type": "stream_end"}\n\n'

    return StreamingResponse(run_stream(), media_type="text/event-stream")


# ─────────────────────────────────────────────────────────────────────────────
# Clinical Documents (Phase E)
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/clinical/documents/generate")
async def generate_clinical_document(
    req: DocumentGenerateRequest, role: str = Depends(get_current_role)
):
    from .documents import ClinicalDocumentGenerator

    generator = ClinicalDocumentGenerator()
    doc = await generator.generate(
        doc_type=req.document_type,
        patient_ref=req.patient_ref,
        encounter_id=req.encounter_id,
        additional_context=req.additional_context,
        search_index=_search_index,
    )
    if "status" in doc and doc["status"] == "error":
        raise HTTPException(status_code=500, detail=doc["message"])
    try:
        from .logs import write_audit_log

        await write_audit_log(
            event_type="DOCUMENT_GENERATED",
            session_id=req.patient_ref,
            query_id=str(uuid.uuid4()),
            disease="documents",
            feedback_type="",
            data={"document_type": req.document_type, "document_id": doc.get("id")},
        )
    except Exception as exc:
        logger.warning("Document generation audit log failed: %s", exc)
    return doc


@app.get("/clinical/documents/{document_id}")
async def get_document(document_id: str, role: str = Depends(get_current_role)):
    from .repositories import get_clinical_document

    doc = await get_clinical_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@app.get("/clinical/documents/patient/{patient_ref}")
async def list_patient_docs(patient_ref: str, role: str = Depends(get_current_role)):
    from .repositories import list_patient_documents

    docs = await list_patient_documents(patient_ref)
    return {"documents": docs}


@app.patch("/clinical/documents/{document_id}/review")
async def review_document(
    document_id: str, req: DocumentReviewRequest, role: str = Depends(get_current_role)
):
    from .repositories import review_clinical_document

    success = await review_clinical_document(document_id, req.reviewed_by)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "recorded"}


# ─────────────────────────────────────────────────────────────────────────────
# Session management
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/sessions/{session_id}/clear")
async def clear_session(
    session_id: str,
    request: SessionClearRequest | None = None,
    role: str = Depends(get_current_role),
):
    memory_status = _queue_memory_distillation(
        session_id,
        request.patient_context if request else None,
    )
    if get_session_storage_backend() == "postgres":
        try:
            from .repositories import clear_session_messages

            await clear_session_messages(session_id)
        except Exception as exc:
            logger.warning("Postgres session clear failed: %s", exc)
    _session_history.pop(session_id, None)
    return {"status": "cleared", "role": role, "memory_distillation": memory_status}


# ─────────────────────────────────────────────────────────────────────────────
# Admin
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/admin/stats")
async def admin_stats(role: str = Depends(require_admin)):
    active_sessions = len(_session_history)
    storage_backend = get_session_storage_backend()
    if get_session_storage_backend() == "postgres":
        try:
            from .repositories import count_active_sessions

            active_sessions = await count_active_sessions()
        except Exception:
            pass

    stats: dict[str, Any] = {
        "active_sessions": active_sessions,
        "session_storage_backend": storage_backend,
        "audit_storage_backend": get_audit_storage_backend(),
        "database": "unknown",
        "users_total": 0,
        "pending_memory_total": 0,
        "approved_memory_total": 0,
        "audit_events_total": 0,
        "evidence_nodes": 0,
        "evidence_edges": 0,
        "indexed_diseases_total": 0,
        "configured_diseases_total": len(DISEASE_CONFIG),
        "pageindex_rows_total": 0,
        "missing_diseases": [],
        "terminology_concepts_total": 0,
        "terminology_status": "unknown",
    }

    try:
        from .db import check_database

        await check_database()
        stats["database"] = "ok"
    except Exception as exc:
        stats["database"] = "error"
        logger.warning("Admin stats: database unavailable: %s", exc)

    try:
        from sqlalchemy import func, select

        from .db import get_session
        from .terminology.models import TerminologyConcept

        async with get_session() as session:
            count = await session.scalar(select(func.count()).select_from(TerminologyConcept))
            stats["terminology_concepts_total"] = int(count or 0)
            stats["terminology_status"] = "ok"
    except Exception as exc:
        stats["terminology_status"] = "unavailable"
        logger.info("Admin stats: terminology tables unavailable: %s", exc)

    if stats["database"] == "ok":
        try:
            from .repositories import (
                count_audit_logs_db,
                count_long_term_memory,
                count_pending_memory,
                count_users,
                evidence_graph_stats,
            )

            stats["users_total"] = await count_users()
            stats["pending_memory_total"] = await count_pending_memory()
            stats["approved_memory_total"] = await count_long_term_memory()
            stats["audit_events_total"] = await count_audit_logs_db()
            evidence_stats = await evidence_graph_stats()
            stats["evidence_nodes"] = int(evidence_stats.get("nodes", 0) or 0)
            stats["evidence_edges"] = int(evidence_stats.get("edges", 0) or 0)
        except Exception as exc:
            logger.warning("Admin stats: database-backed counters unavailable: %s", exc)
    else:
        logger.info("Admin stats: database-backed counters skipped because database is unavailable")

    try:
        indexed = get_available_diseases()
        pageindex = _search_index.pageindex_stats() if _search_index else {"total": 0}
        stats["indexed_diseases_total"] = len(indexed)
        stats["pageindex_rows_total"] = int(pageindex.get("total", 0) or 0)
        stats["missing_diseases"] = [
            disease_id for disease_id in DISEASE_CONFIG if disease_id not in indexed
        ]
    except Exception:
        logger.warning("Admin stats: LanceDB counters unavailable", exc_info=True)

    return {"status": "authorized", "stats": stats}


@app.get("/admin/sessions")
async def admin_sessions(
    limit: int = Query(100, ge=1, le=500),
    role: str = Depends(require_admin),
):
    if get_session_storage_backend() != "postgres":
        return {
            "sessions": [
                {"session_id": sid, "message_count": len(m), "last_seen_at": None}
                for sid, m in list(_session_history.items())[:limit]
            ],
            "storage_backend": "memory",
        }
    try:
        from .repositories import list_sessions

        return {
            "sessions": await list_sessions(limit=limit),
            "storage_backend": "postgres",
        }
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Dependency missing: {exc.name}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Session backend unavailable: {exc}") from exc


@app.get("/admin/users")
async def admin_list_users(role: str = Depends(require_admin)):
    try:
        from .repositories import list_users

        return {"users": await list_users()}
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Dependency missing: {exc.name}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"User backend unavailable: {exc}") from exc


@app.post("/admin/users")
async def admin_create_user(request: UserCreateRequest, role: str = Depends(require_admin)):
    try:
        from .repositories import create_user

        return {"user": await create_user(request.external_id, request.role, request.display_name)}
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Dependency missing: {exc.name}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"User backend unavailable: {exc}") from exc


@app.patch("/admin/users/{user_id}")
async def admin_update_user(
    user_id: str, request: UserUpdateRequest, role: str = Depends(require_admin)
):
    try:
        from .repositories import update_user

        user = await update_user(user_id, role=request.role, display_name=request.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user id") from exc
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Dependency missing: {exc.name}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"User backend unavailable: {exc}") from exc
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": user}


@app.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, role: str = Depends(require_admin)):
    try:
        from .repositories import delete_user

        deleted = await delete_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user id") from exc
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Dependency missing: {exc.name}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"User backend unavailable: {exc}") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "deleted"}


@app.post("/memory/pending")
async def create_memory_candidate(request: MemoryCreateRequest, role: str = Depends(require_admin)):
    from .memory import patient_ref_from_context
    from .repositories import create_pending_memory

    return {
        "memory": await create_pending_memory(
            patient_ref_from_context(request.patient_context),
            request.session_id,
            request.fact_type,
            request.fact_text,
            request.source_message_ids,
        )
    }


@app.post("/memory/pending/list")
async def list_memory_candidates(request: MemoryListRequest, role: str = Depends(require_admin)):
    from .memory import patient_ref_from_context
    from .repositories import list_pending_memory

    return {
        "pending": await list_pending_memory(
            patient_ref_hash=patient_ref_from_context(request.patient_context),
            session_id=request.session_id,
        )
    }


@app.get("/memory/pending/all")
async def list_all_memory_candidates(
    limit: int = Query(100, ge=1, le=500),
    role: str = Depends(require_admin),
):
    from .repositories import list_pending_memory

    rows = await list_pending_memory()
    return {"pending": rows[:limit]}


@app.post("/memory/pending/{memory_id}/approve")
async def approve_memory_candidate(
    memory_id: str,
    x_user_role: str | None = Header(None),
    role: str = Depends(require_admin),
):
    try:
        from .repositories import approve_pending_memory

        approved = await approve_pending_memory(memory_id, approved_by=x_user_role or role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid memory id") from exc
    if approved is None:
        raise HTTPException(status_code=404, detail="Memory candidate not found")
    return {"memory": approved}


@app.post("/memory/long-term/list")
async def list_approved_memory(request: MemoryListRequest, role: str = Depends(require_admin)):
    from .memory import patient_ref_from_context
    from .repositories import list_long_term_memory

    return {
        "memory": await list_long_term_memory(patient_ref_from_context(request.patient_context))
    }


@app.get("/memory/long-term/all")
async def list_all_approved_memory(
    limit: int = Query(100, ge=1, le=500),
    role: str = Depends(require_admin),
):
    from .repositories import list_all_long_term_memory

    return {"memory": await list_all_long_term_memory(limit=limit)}


@app.post("/memory/distill-session")
async def distill_session_memory(request: MemoryDistillRequest, role: str = Depends(require_admin)):
    from .memory import distill_session_candidates

    return {
        "pending": await distill_session_candidates(request.session_id, request.patient_context)
    }


@app.post("/evidence/seed/{disease}")
async def seed_graph(
    disease: str,
    x_user_role: str | None = Header(None),
    role: str = Depends(require_admin),
):
    try:
        from .evidence import seed_evidence_graph

        return await seed_evidence_graph(disease, clinician_id=x_user_role or role)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Evidence seed file not found") from exc


@app.post("/evidence/seed-all")
async def seed_all_graphs(
    x_user_role: str | None = Header(None),
    role: str = Depends(require_admin),
):
    from .evidence import seed_evidence_graph

    seeded: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for disease in DISEASE_CONFIG:
        try:
            seeded[disease] = await seed_evidence_graph(disease, clinician_id=x_user_role or role)
        except Exception as exc:
            errors[disease] = str(exc)
    return {"seeded": seeded, "errors": errors}


@app.post("/evidence/query")
async def query_graph(request: EvidenceQueryRequest, role: str = Depends(get_current_role)):
    from .evidence import query_evidence_graph

    return {
        "results": await query_evidence_graph(
            disease=request.disease, query=request.query, top_k=request.top_k
        )
    }


@app.get("/evidence/stats")
async def graph_stats(role: str = Depends(require_admin)):
    from .repositories import evidence_graph_stats

    return await evidence_graph_stats()


@app.post("/evidence/nodes")
async def graph_nodes(request: EvidenceNodesRequest, role: str = Depends(require_admin)):
    from .repositories import list_evidence_nodes

    return {
        "nodes": await list_evidence_nodes(
            disease=request.disease,
            node_type=request.node_type,
            limit=request.limit,
        )
    }


@app.get("/admin/audit")
async def get_audit_logs(
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    session_id: str | None = None,
    disease: str | None = None,
    feedback_type: str | None = None,
    page: int = 1,
    limit: int = 50,
    role: str = Depends(require_admin),
):
    try:
        return await read_audit_logs_async(
            start_date=start_date,
            end_date=end_date,
            session_id=session_id,
            disease=disease,
            feedback_type=feedback_type,
            page=page,
            limit=limit,
        )
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503, detail=f"Audit dependency missing: {exc.name}"
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Audit backend unavailable: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# _compute_patient_scores: imported from scoring.py (canonical implementation)
# ─────────────────────────────────────────────────────────────────────────────
# See app/scoring.py for the full implementation with correct patient_state
# field names (most_recent_vitals, latest_labs_by_type, active_diagnoses).


# ─────────────────────────────────────────────────────────────────────────────
# Phase C: Differential Diagnosis (DDx)
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/clinical/ddx")
async def run_ddx(request: DDxRequest, role: str = Depends(get_current_role)):

    if not _search_index:
        raise HTTPException(status_code=503, detail="Search index not ready")

    engine = DifferentialDiagnosisEngine()

    async def _stream():
        candidate_count = 0
        try:
            async for event in engine.generate_ddx(request, _search_index):
                if event.get("type") == "ddx_candidates":
                    candidate_count = len(event.get("candidates", []))
                yield f"data: {json.dumps(event)}\n\n"
        except TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Request timed out'})}\n\n"
        except Exception as exc:
            logger.error("DDx stream error: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            try:
                from .logs import write_audit_log

                await write_audit_log(
                    event_type="DDX_REQUEST",
                    session_id=request.patient_ref or "anonymous",
                    query_id=str(uuid.uuid4()),
                    disease="ddx",
                    feedback_type="",
                    data={
                        "symptoms_count": len(request.presenting_symptoms),
                        "target_diseases": request.target_diseases,
                        "candidate_count": candidate_count,
                    },
                )
            except Exception as exc:
                logger.warning("DDx audit log failed: %s", exc)

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ─────────────────────────────────────────────────────────────────────────────
# Phase D: Treatment Pathway Engine
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/clinical/pathways")
async def list_pathways(role: str = Depends(get_current_role)):
    from .pathways import PATHWAY_REGISTRY

    return {
        "pathways": [
            {
                "pathway_id": p.pathway_id,
                "disease": p.disease,
                "pathway_name": p.name,
                "target_population": p.target_population,
                "step_count": len(p.steps),
            }
            for p in PATHWAY_REGISTRY.values()
        ]
    }


@app.post("/clinical/pathway/run")
async def run_pathway(req: PathwayRunRequest, role: str = Depends(get_current_role)):
    from .pathways import PathwayRunner
    from .patient_state import get_patient_state

    patient_state = await get_patient_state(req.patient_ref)
    runner = PathwayRunner()

    async def _stream():
        step_count = 0
        completed_count = 0
        blocking_count = 0
        try:
            async for event in runner.run(req.pathway_id, req.patient_ref, patient_state):
                if event.get("type") == "step":
                    step_count += 1
                    if event.get("status") == "completed":
                        completed_count += 1
                    if event.get("status") in ("current", "blocked"):
                        blocking_count += len(event.get("blocking_inputs", []))
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.error("Pathway run error: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            try:
                from .logs import write_audit_log

                await write_audit_log(
                    event_type="PATHWAY_RUN",
                    session_id=req.patient_ref,
                    query_id=str(uuid.uuid4()),
                    disease=req.pathway_id,
                    feedback_type="",
                    data={
                        "pathway_id": req.pathway_id,
                        "step_count": step_count,
                        "completed_steps_count": completed_count,
                        "blocking_inputs_count": blocking_count,
                    },
                )
            except Exception as exc:
                logger.warning("Pathway audit log failed: %s", exc)

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ─────────────────────────────────────────────────────────────────────────────
# Phase F: CDS Hooks
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/.well-known/cds-services")
async def cds_service_discovery():
    """CDS Hooks v1.0 service manifest."""
    return {
        "services": [
            {
                "hook": "patient-view",
                "title": "CDSS Patient View",
                "description": "Returns clinical alerts, overdue monitoring, and pathway reminders for the active patient.",
                "id": "cdss-patient-view",
                "prefetch": {
                    "patient": "Patient/{{context.patientId}}",
                    "medications": "MedicationRequest?patient={{context.patientId}}&status=active",
                },
            },
            {
                "hook": "medication-prescribe",
                "title": "CDSS Medication Prescribe",
                "description": "Returns critical drug interaction and contraindication alerts for draft prescriptions.",
                "id": "cdss-medication-prescribe",
                "prefetch": {
                    "patient": "Patient/{{context.patientId}}",
                    "activeMedications": "MedicationRequest?patient={{context.patientId}}&status=active",
                    "draftOrder": "MedicationRequest/{{context.draftOrders.0.id}}",
                },
            },
        ]
    }


@app.post("/cds-hooks/patient-view")
async def cds_patient_view(context: dict[str, Any]):
    from .cds_hooks import CDSHooksHandler

    handler = CDSHooksHandler()
    try:
        cards = await with_timeout(handler.handle_patient_view(context), 4.0)
    except Exception as exc:
        logger.warning("patient-view hook failed: %s", exc)
        cards = []
    return {"cards": [c.to_dict() for c in (cards or [])]}


@app.post("/cds-hooks/medication-prescribe")
async def cds_medication_prescribe(context: dict[str, Any]):
    from .cds_hooks import CDSHooksHandler

    handler = CDSHooksHandler()
    try:
        cards = await with_timeout(handler.handle_medication_prescribe(context), 4.0)
    except Exception as exc:
        logger.warning("medication-prescribe hook failed: %s", exc)
        cards = []
    return {"cards": [c.to_dict() for c in (cards or [])]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
