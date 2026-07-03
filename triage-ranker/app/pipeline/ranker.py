"""triage-ranker/app/pipeline/ranker.py.

Stage 3 — Composite severity scoring and triage level assignment.

Composite score = w_rule + w_semantic + w_modifier + w_scoring_system

Components:
- w_rule: severity_weight from clinical_rules.yaml for matched terms
- w_semantic: spaCy similarity if UMLS enriched (simplified here)
- w_semantic: 0.0 when no UMLS enrichment available
- w_modifier: severity modifiers from ConText (severe, critical, acute)
- w_scoring_system: GCS component if gcs_score provided;
  Shock Index (hr/sbp) if both provided — Shock Index > 1.0 → critical flag

Maps composite score to triage_level (P1–P4) and esi_level (1–5).

Design constraints:
- Never returns zero results — fallback to 'Undifferentiated Emergency'
- Works in both English and Swahili (via clinical rules)
- Deterministic, synchronous scoring after keyword resolution
"""

from __future__ import annotations

import logging
from typing import Any

from ambulance_cdss_contracts.triage import (
    ClinicalCategory,
    DiagnosisRankItem,
    SeverityLevel,
    TriageLevel,
)

logger = logging.getLogger(__name__)


def _compute_shock_index(hr: int | None, sbp: int | None) -> float | None:
    """Compute Shock Index = HR / SBP. Returns None if inputs invalid."""
    if hr is None or sbp is None or sbp <= 0:
        return None
    return round(hr / sbp, 2)


def _compute_gcs_severity(gcs_score: int | None) -> tuple[float, str]:
    """GCS component score for triage ranking.
    Returns (weight, severity_level_description).
    """
    if gcs_score is None:
        return 0.0, "unknown"
    if gcs_score <= 8:
        return 0.3, "severe_tbi"
    if gcs_score <= 12:
        return 0.2, "moderate_tbi"
    if gcs_score <= 14:
        return 0.1, "mild_tbi"
    return 0.0, "normal"


def _compute_shock_index_score(shock_index: float | None) -> tuple[float, str]:
    """Shock Index component score for triage ranking."""
    if shock_index is None:
        return 0.0, "unknown"
    if shock_index > 1.0:
        return 0.3, "shock_index_critical"
    if shock_index > 0.9:
        return 0.15, "shock_index_elevated"
    return 0.0, "shock_index_normal"


def _map_to_triage_level(composite_score: float) -> TriageLevel:
    """Map composite score (0-1 range) to triage level P1-P4."""
    if composite_score >= 0.8:
        return TriageLevel.P1
    if composite_score >= 0.55:
        return TriageLevel.P2
    if composite_score >= 0.3:
        return TriageLevel.P3
    return TriageLevel.P4


def _map_to_esi_level(composite_score: float) -> int:
    """Map composite score (0-1 range) to ESI level 1-5."""
    if composite_score >= 0.8:
        return 1
    if composite_score >= 0.6:
        return 2
    if composite_score >= 0.4:
        return 3
    if composite_score >= 0.2:
        return 4
    return 5


def _map_severity_level(composite_score: float) -> SeverityLevel:
    """Map composite score to severity classification."""
    if composite_score >= 0.8:
        return SeverityLevel.CRITICAL
    if composite_score >= 0.6:
        return SeverityLevel.HIGH
    if composite_score >= 0.4:
        return SeverityLevel.ACUTE
    if composite_score >= 0.2:
        return SeverityLevel.MODERATE
    return SeverityLevel.LOW


def rank_diagnoses(
    resolved_keywords: list[dict[str, Any]],
    gcs_score: int | None = None,
    acvpu: str | None = None,
    sbp: int | None = None,
    hr: int | None = None,
    rules: list[dict[str, Any]] | None = None,
    degraded_mode: bool = False,
) -> list[DiagnosisRankItem]:
    """Stage 3 — Rank clinical diagnoses from resolved keywords.

    Uses composite scoring: w_rule + w_semantic + w_modifier + w_scoring_system.
    Maps to triage_level (P1-P4) and esi_level (1-5).

    Args:
        resolved_keywords: Keywords from Stage 2 with UMLS resolution.
            Accepts dicts or Pydantic models (e.g. ExtractedKeyword) —
            models are converted via model_dump() automatically.
        gcs_score: Glasgow Coma Scale (3-15)
        acvpu: Consciousness level (A/C/V/P/U)
        sbp: Systolic blood pressure
        hr: Heart rate
        rules: Clinical rules for weight lookup
        degraded_mode: Whether the pipeline is in degraded mode

    Returns:
        Ranked list of DiagnosisRankItem. Never empty.
    """
    # Normalise: convert Pydantic models to dicts for uniform .get() access
    normalised: list[dict[str, Any]] = []
    for kw in resolved_keywords:
        if hasattr(kw, "model_dump"):
            normalised.append(kw.model_dump())
        elif isinstance(kw, dict):
            normalised.append(kw)
        else:
            normalised.append(dict(kw))
    resolved_keywords = normalised

    # Compute scoring system components
    gcs_weight, gcs_desc = _compute_gcs_severity(gcs_score)
    shock_index = _compute_shock_index(hr, sbp)
    si_weight, si_desc = _compute_shock_index_score(shock_index)

    # ACVPU to GCS mapping (approximation)
    acvpu_gcs_map = {
        "a": 15,
        "alert": 15,
        "c": 13,
        "confused": 13,
        "v": 9,
        "voice": 9,
        "responds to voice": 9,
        "p": 7,
        "pain": 7,
        "responds to pain": 7,
        "u": 3,
        "unresponsive": 3,
        "unconscious": 3,
    }
    if gcs_score is None and acvpu:
        gcs_score = acvpu_gcs_map.get(acvpu.lower().strip())
        gcs_weight, gcs_desc = _compute_gcs_severity(gcs_score)

    # Score each keyword
    scored: list[dict[str, Any]] = []
    for kw in resolved_keywords:
        # w_rule: severity weight from clinical rules
        w_rule = 0.0
        category = kw.get("category", ClinicalCategory.UNKNOWN)
        if hasattr(category, "value"):
            category = category.value

        # Look up severity weight from rules
        if rules:
            for rule in rules:
                if rule.get("category") == category:
                    w_rule = rule.get("severity_weight", 0.3)
                    break

        if w_rule == 0.0:
            w_rule = 0.3  # Default moderate weight

        # w_semantic: simplified — non-zero when UMLS resolved
        umls_res = kw.get("umls_resolution")
        w_semantic = 0.1 if (umls_res and umls_res.get("cui")) else 0.0

        # w_modifier: severity modifiers from extraction
        modifiers = kw.get("severity_modifiers", [])
        w_modifier = 0.0
        for mod in modifiers:
            if "CRITICAL" in mod:
                w_modifier += 0.2
            elif "SEVERE" in mod:
                w_modifier += 0.15
            elif "ACTIVE" in mod:
                w_modifier += 0.1

        # Total composite (clamped to 0-1)
        total = min(1.0, w_rule + w_semantic + w_modifier + gcs_weight + si_weight)

        scored.append(
            {
                "text": kw.get("text", ""),
                "category": category,
                "composite_score": total,
                "w_rule": w_rule,
                "w_semantic": w_semantic,
                "w_modifier": w_modifier,
                "w_gcs": gcs_weight,
                "w_shock_index": si_weight,
                "icd10_code": (umls_res or {}).get("icd10_code", kw.get("icd10_prefix", "")),
                "snomed_code": (umls_res or {}).get("snomed_code", kw.get("snomed_hint", "")),
                "cui": (umls_res or {}).get("cui", ""),
            }
        )

    # Sort by composite score descending
    scored.sort(key=lambda x: x["composite_score"], reverse=True)

    # Build DiagnosisRankItem list
    ranking = []
    for i, s in enumerate(scored[:10]):  # Max 10 results
        _map_to_triage_level(s["composite_score"])
        esi_level = _map_to_esi_level(s["composite_score"])
        severity_level = _map_severity_level(s["composite_score"])

        scoring_systems = []
        scoring_systems_applied = []
        if gcs_score is not None:
            scoring_systems.append("GCS_NUMERIC")
            scoring_systems_applied.append("GCS_NUMERIC")
        if si_weight > 0:
            scoring_systems.append("SHOCK_INDEX_CRITICAL")
            scoring_systems_applied.append("SHOCK_INDEX_CRITICAL")

        modifier_classes = []
        for mod in modifiers:
            modifier_classes.append(mod)

        ranking.append(
            DiagnosisRankItem(
                rank=i + 1,
                canonical_name=s["text"].title(),
                umls_cui=s["cui"] or None,
                snomed_code=s["snomed_code"] or None,
                icd10_code=s["icd10_code"] or None,
                severity_level=severity_level,
                esi_level=esi_level,
                score_breakdown={
                    "w_rule": s["w_rule"],
                    "w_semantic": s["w_semantic"],
                    "w_modifier": s["w_modifier"],
                    "w_gcs": s["w_gcs"],
                    "w_shock_index": s["w_shock_index"],
                    "total": s["composite_score"],
                },
                scoring_systems_applied=scoring_systems_applied,
                modifier_classes=modifier_classes,
            )
        )

    # Never return zero results — fallback
    if not ranking:
        ranking.append(
            DiagnosisRankItem(
                rank=1,
                canonical_name="Undifferentiated Emergency",
                umls_cui=None,
                snomed_code=None,
                icd10_code=None,
                severity_level=SeverityLevel.MODERATE,
                esi_level=3,
                score_breakdown={"w_rule": 0.3, "total": 0.3},
                scoring_systems_applied=[],
                modifier_classes=[],
            )
        )

    return ranking, shock_index
