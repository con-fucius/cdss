"""Thin patient-state wrapper for chat and clinical workflows."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def get_patient_state(patient_ref: str) -> Dict[str, Any]:
    """Return aggregated patient state for a hashed patient reference.

    The repository layer owns the Postgres access pattern. This wrapper exists so
    API handlers and future scoring/pathway modules share one non-fatal call
    site. Any repository, database, or mapping failure is logged and degraded to
    an empty dict rather than being allowed to interrupt chat.
    """
    try:
        from .repositories import get_patient_state as repository_get_patient_state

        return await repository_get_patient_state(patient_ref)
    except Exception as exc:
        logger.warning("Patient state unavailable for %s: %s", patient_ref, exc)
        return {}


def detect_temporal_flags(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Scan assembled patient state for overdue monitoring and temporal flags.

    Returns a list of clinical flags, each with severity, message, and
    guideline_ref. Pure function — no async, no external calls.
    """
    if not state:
        return []

    flags: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    temporal = state.get("temporal_events") or {}
    latest_labs = state.get("latest_labs_by_type") or {}
    active_meds = state.get("active_medications") or []
    active_conditions = [c.lower() for c in (state.get("active_conditions") or [])]

    def _parse_date(date_str: str) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None

    # Viral load overdue: > 6 months since last VL for HIV patients
    if "hiv" in active_conditions:
        vl_date_str = temporal.get("last_viral_load_date")
        vl_date = _parse_date(vl_date_str)
        if vl_date is None:
            flags.append({
                "severity": "warning",
                "message": "No viral load recorded — baseline VL required for HIV patients",
                "guideline_ref": "Kenya ARV Guidelines 2022: baseline VL at ART initiation",
            })
        elif (now - vl_date).days > 182:
            months = (now - vl_date).days // 30
            flags.append({
                "severity": "warning",
                "message": f"Viral load overdue — last {months} months ago ({vl_date_str})",
                "guideline_ref": "Kenya ARV Guidelines 2022: VL at 6 months, then annually",
            })

    # CD4 overdue: > 6 months in first year of ART
    if "hiv" in active_conditions:
        cd4_date_str = temporal.get("last_cd4_date")
        cd4_date = _parse_date(cd4_date_str)
        treatment_dates = temporal.get("treatment_start_dates") or {}
        art_start = None
        for drug, date_str in treatment_dates.items():
            if any(kw in drug.lower() for kw in ("dtg", "tenofovir", "tdf", "art", "arv")):
                art_start = _parse_date(date_str)
                break
        if art_start and (now - art_start).days < 365:
            if cd4_date is None:
                flags.append({
                    "severity": "info",
                    "message": "CD4 count recommended within first year of ART",
                    "guideline_ref": "Kenya ARV Guidelines 2022: CD4 at baseline and 6 months",
                })
            elif (now - cd4_date).days > 182:
                flags.append({
                    "severity": "info",
                    "message": f"CD4 count due — last {((now - cd4_date).days // 30)} months ago",
                    "guideline_ref": "Kenya ARV Guidelines 2022: repeat CD4 at 6 months",
                })

    # HbA1c overdue: > 3 months if target not met
    if "diabetes" in active_conditions:
        hba1c_lab = latest_labs.get("hba1c")
        if hba1c_lab:
            hba1c_val = hba1c_lab.get("value")
            hba1c_date_str = hba1c_lab.get("recorded_at")
            hba1c_date = _parse_date(hba1c_date_str)
            if hba1c_val is not None and float(hba1c_val) >= 7.0 and hba1c_date:
                if (now - hba1c_date).days > 90:
                    flags.append({
                        "severity": "warning",
                        "message": f"HbA1c {hba1c_val}% above target — recheck due (last {((now - hba1c_date).days // 30)} months ago)",
                        "guideline_ref": "Kenya DM Guidelines V15 2024: HbA1c every 3 months if above target",
                    })
        else:
            flags.append({
                "severity": "info",
                "message": "No HbA1c recorded — baseline required for diabetic patients",
                "guideline_ref": "Kenya DM Guidelines V15 2024: HbA1c at diagnosis",
            })

    return flags
