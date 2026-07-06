"""app/scoring/scorers.py.

Prehospital-relevant scoring only — NEWS2 and Glasgow Coma Scale.
Per docs/OUT_OF_SCOPE.md: no Child-Pugh, no CVD risk charts, no HbA1c
targets, no eGFR/CKD staging. Those are chronic-care/clinic-visit scores
and have no place in an emergency dispatch/field product.

Trauma, obstetric, and paediatric emergency severity criteria are NOT
implemented yet — they are explicitly pending the Phase 0.1 confirmation
of which emergency categories this service's protocols actually cover.
Do not add them speculatively; add them when that confirmation lands.

Ported and trimmed from the chronic-disease CDSS's scoring.py pattern:
deterministic, synchronous, pure Python, no LLM calls, no async, no
network I/O. Raises ValueError on missing required inputs rather than
silently defaulting — a missing input must never produce a false score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ScoringError(ValueError):
    """Raised when required scoring inputs are missing or out of range."""

    def __init__(self, message: str, missing_fields: list[str] | None = None):
        super().__init__(message)
        self.missing_fields = missing_fields or []


@dataclass
class ScoringResult:
    score: int | float
    risk_level: str
    escalation_required: bool
    component_scores: dict[str, int | float] = field(default_factory=dict)
    trigger: str = ""
    source_guideline: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# NEWS2 — National Early Warning Score 2 (NHS, 2017)
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_NEWS2_FIELDS = [
    "respiratory_rate",
    "spo2",
    "bp_systolic",
    "heart_rate",
    "consciousness",
    "temperature",
]


def _score_respiratory_rate(rr: int) -> int:
    if rr <= 8:
        return 3
    if 9 <= rr <= 11:
        return 1
    if 12 <= rr <= 20:
        return 0
    if 21 <= rr <= 24:
        return 2
    return 3  # >= 25


def _score_spo2_scale1(spo2: int) -> int:
    if spo2 <= 91:
        return 3
    if 92 <= spo2 <= 93:
        return 2
    if 94 <= spo2 <= 95:
        return 1
    return 0  # >= 96


def _score_spo2_scale2(spo2: int, supplemental_o2: bool) -> int:
    # Scale 2 used for patients with hypercapnic respiratory failure on
    # target range 88-92%. Simplified per NEWS2 spec table.
    if spo2 <= 83:
        return 3
    if 84 <= spo2 <= 85:
        return 2
    if 86 <= spo2 <= 87:
        return 1
    if 88 <= spo2 <= 92:
        return 0
    if spo2 >= 93 and supplemental_o2:
        return 1
    return 0


def _score_supplemental_o2(supplemental_o2: bool) -> int:
    return 2 if supplemental_o2 else 0


def _score_bp_systolic(bp: int) -> int:
    if bp <= 90:
        return 3
    if 91 <= bp <= 100:
        return 2
    if 101 <= bp <= 110:
        return 1
    if 111 <= bp <= 219:
        return 0
    return 3  # >= 220


def _score_heart_rate(hr: int) -> int:
    if hr <= 40:
        return 3
    if 41 <= hr <= 50:
        return 1
    if 51 <= hr <= 90:
        return 0
    if 91 <= hr <= 110:
        return 1
    if 111 <= hr <= 130:
        return 2
    return 3  # >= 131


def _score_consciousness(level: str) -> int:
    return 0 if level.upper() == "A" else 3


def _score_temperature(temp: float) -> int:
    if temp <= 35.0:
        return 3
    if 35.1 <= temp <= 36.0:
        return 1
    if 36.1 <= temp <= 38.0:
        return 0
    if 38.1 <= temp <= 39.0:
        return 1
    return 2  # >= 39.1


def compute_news2(vitals: dict[str, Any]) -> ScoringResult:
    """NEWS2 score from six physiological parameters.

    Required keys in `vitals`: respiratory_rate (int), spo2 (int),
    bp_systolic (int), heart_rate (int), consciousness (str: A/V/P/U),
    temperature (float). Optional: spo2_scale (1 or 2, default 1),
    supplemental_o2 (bool, default False).

    Raises ScoringError listing missing_fields if any required input
    is absent. Never defaults a missing value to produce a false score.
    """
    missing = [f for f in _REQUIRED_NEWS2_FIELDS if vitals.get(f) is None]
    if missing:
        raise ScoringError(
            f"NEWS2 requires all of {_REQUIRED_NEWS2_FIELDS}; missing: {missing}",
            missing_fields=missing,
        )

    spo2_scale = int(vitals.get("spo2_scale") or 1)
    supplemental_o2 = bool(vitals.get("supplemental_o2") or False)

    components = {
        "respiratory_rate": _score_respiratory_rate(int(vitals["respiratory_rate"])),
        "spo2": (
            _score_spo2_scale2(int(vitals["spo2"]), supplemental_o2)
            if spo2_scale == 2
            else _score_spo2_scale1(int(vitals["spo2"]))
        ),
        "supplemental_o2": _score_supplemental_o2(supplemental_o2),
        "bp_systolic": _score_bp_systolic(int(vitals["bp_systolic"])),
        "heart_rate": _score_heart_rate(int(vitals["heart_rate"])),
        "consciousness": _score_consciousness(str(vitals["consciousness"])),
        "temperature": _score_temperature(float(vitals["temperature"])),
    }

    total = sum(components.values())
    any_param_is_3 = any(v == 3 for v in components.values())

    if total >= 7:
        risk_level = "high"
    elif total >= 5 or any_param_is_3:
        risk_level = "medium"
    else:
        risk_level = "low"

    escalation_required = total >= 5 or any_param_is_3

    trigger_parts = [
        f"{k}={vitals.get(k, 'n/a')} (score {v})" for k, v in components.items() if v >= 2
    ]
    trigger = "; ".join(trigger_parts) if trigger_parts else "No high-scoring parameters"

    return ScoringResult(
        score=total,
        risk_level=risk_level,
        escalation_required=escalation_required,
        component_scores=components,
        trigger=trigger,
        source_guideline="NHS NEWS2 (2017)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Glasgow Coma Scale
# ─────────────────────────────────────────────────────────────────────────────

_GCS_EYE_RANGE = (1, 4)
_GCS_VERBAL_RANGE = (1, 5)
_GCS_MOTOR_RANGE = (1, 6)


def compute_gcs_total(eye: int, verbal: int, motor: int) -> int:
    """Raw GCS total (3-15). Caller is responsible for collecting the three
    component scores (eye 1-4, verbal 1-5, motor 1-6) via a structured
    field-side input — this function only sums and validates range.
    """
    for label, value, (lo, hi) in (
        ("eye", eye, _GCS_EYE_RANGE),
        ("verbal", verbal, _GCS_VERBAL_RANGE),
        ("motor", motor, _GCS_MOTOR_RANGE),
    ):
        if not (lo <= value <= hi):
            raise ScoringError(
                f"GCS {label} component must be in range {lo}-{hi}, got {value}",
                missing_fields=[label],
            )
    return eye + verbal + motor


def interpret_gcs(total: int) -> ScoringResult:
    if total >= 13:
        risk_level = "low"
        escalation_required = False
    elif total >= 9:
        risk_level = "medium"
        escalation_required = True
    else:
        risk_level = "high"
        escalation_required = True

    return ScoringResult(
        score=total,
        risk_level=risk_level,
        escalation_required=escalation_required,
        component_scores={"gcs_total": total},
        trigger=f"GCS total = {total}",
        source_guideline="Glasgow Coma Scale (Teasdale & Jennett, 1974)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Paediatric Early Warning Score (PEWS)
# ─────────────────────────────────────────────────────────────────────────────
# For patients under 16 years. Adapted from Paediatric NEWS / PEWS.
# Scored on: behaviour, respiratory rate, heart rate, systolic BP,
# temperature, and SpO2.

_REQUIRED_PEWS_FIELDS = [
    "behaviour",
    "respiratory_rate",
    "heart_rate",
    "bp_systolic",
    "temperature",
    "spo2",
]


def _score_behaviour(behaviour: str) -> int:
    """Behaviour/response: sleeping, consolable, irritable/restless, agitated.
    Mapped to integer: 0=sleeping/alert, 1=consolable, 2=irritable, 3=agitated.
    """
    b = behaviour.strip().lower()
    mapping = {
        "sleeping": 0, "alert": 0,
        "consolable": 1, "con": 1,
        "irritable": 2, "restless": 2,
        "agitated": 3, "inconsolable": 3,
    }
    return mapping.get(b, 0)


def _pews_respiratory_rate(rr: int) -> int:
    if rr <= 10:
        return 0
    if 11 <= rr <= 15:
        return 1
    if 16 <= rr <= 25:
        return 0
    if 26 <= rr <= 35:
        return 2
    return 3  # >= 36


def _pews_heart_rate(hr: int) -> int:
    if hr <= 60:
        return 0
    if 61 <= hr <= 100:
        return 1
    if 101 <= hr <= 130:
        return 0
    if 131 <= hr <= 160:
        return 2
    return 3  # >= 161


def _pews_sbp(sbp: int) -> int:
    if sbp >= 90:
        return 0
    if 80 <= sbp <= 89:
        return 1
    if 70 <= sbp <= 79:
        return 2
    return 3  # < 70


def _pews_temperature(temp: float) -> int:
    if temp <= 35.0:
        return 2
    if 35.1 <= temp <= 36.0:
        return 1
    if 36.1 <= temp <= 38.0:
        return 0
    if 38.1 <= temp <= 39.0:
        return 1
    return 2  # >= 39.1


def _pews_spo2(spo2: int) -> int:
    if spo2 <= 91:
        return 3
    if 92 <= spo2 <= 93:
        return 2
    if 94 <= spo2 <= 95:
        return 1
    return 0  # >= 96


def compute_pews(vitals: dict[str, Any], age_years: float) -> ScoringResult:
    """Paediatric Early Warning Score for patients under 16.

    Required keys in vitals: behaviour (str), respiratory_rate (int),
    heart_rate (int), bp_systolic (int), temperature (float), spo2 (int).
    age_years: patient age in years (< 16).

    Raises ScoringError listing missing_fields if any required input
    is absent. Returns score 0-16, risk_level low/medium/high.
    """
    if age_years >= 16:
        raise ScoringError(
            f"PEWS is for patients under 16; got age {age_years}",
            missing_fields=["age_years"],
        )
    missing = [f for f in _REQUIRED_PEWS_FIELDS if vitals.get(f) is None]
    if missing:
        raise ScoringError(
            f"PEWS requires all of {_REQUIRED_PEWS_FIELDS}; missing: {missing}",
            missing_fields=missing,
        )

    components = {
        "behaviour": _score_behaviour(str(vitals["behaviour"])),
        "respiratory_rate": _pews_respiratory_rate(int(vitals["respiratory_rate"])),
        "heart_rate": _pews_heart_rate(int(vitals["heart_rate"])),
        "bp_systolic": _pews_sbp(int(vitals["bp_systolic"])),
        "temperature": _pews_temperature(float(vitals["temperature"])),
        "spo2": _pews_spo2(int(vitals["spo2"])),
    }
    total = sum(components.values())
    any_param_is_3 = any(v == 3 for v in components.values())

    if total >= 7 or any_param_is_3:
        risk_level = "high"
    elif total >= 3:
        risk_level = "medium"
    else:
        risk_level = "low"

    escalation_required = total >= 3 or any_param_is_3
    trigger_parts = [
        f"{k}={vitals.get(k, 'n/a')} (score {v})"
        for k, v in components.items() if v >= 2
    ]
    trigger = "; ".join(trigger_parts) if trigger_parts else "No high-scoring parameters"

    return ScoringResult(
        score=total,
        risk_level=risk_level,
        escalation_required=escalation_required,
        component_scores=components,
        trigger=trigger,
        source_guideline="Paediatric Early Warning Score (PEWS)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Revised Trauma Score (RTS)
# ─────────────────────────────────────────────────────────────────────────────
# RTS = 0.9368 × GCS + 0.7326 × SBP + 0.2908 × RR
# (weights per Champion et al. 1989)

def compute_revised_trauma_score(vitals: dict[str, Any]) -> ScoringResult:
    """Revised Trauma Score from GCS, SBP, and RR.

    Required keys: gcs_total (int 3-15), bp_systolic (int), respiratory_rate (int).
    Returns RTS as a float score (0-7.84) and a triage band.
    Raises ScoringError on missing inputs.
    """
    required = ["gcs_total", "bp_systolic", "respiratory_rate"]
    missing = [f for f in required if vitals.get(f) is None]
    if missing:
        raise ScoringError(
            f"RTS requires {required}; missing: {missing}",
            missing_fields=missing,
        )

    gcs = int(vitals["gcs_total"])
    sbp = int(vitals["bp_systolic"])
    rr = int(vitals["respiratory_rate"])

    # Coded values per RTS table (Champion et al. 1989)
    gcs_coded = {3: 0, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6, 10: 7, 11: 8, 12: 9, 13: 10, 14: 11, 15: 12}.get(gcs, 0)
    if sbp == 0:
        sbp_coded = 0
    elif sbp <= 49:
        sbp_coded = 1
    elif sbp <= 75:
        sbp_coded = 2
    elif sbp <= 89:
        sbp_coded = 3
    elif sbp <= 101:
        sbp_coded = 4
    elif sbp <= 110:
        sbp_coded = 5
    elif sbp <= 119:
        sbp_coded = 6
    elif sbp <= 129:
        sbp_coded = 7
    elif sbp <= 144:
        sbp_coded = 8
    elif sbp <= 155:
        sbp_coded = 9
    elif sbp <= 175:
        sbp_coded = 10
    else:
        sbp_coded = 11
    if rr == 0:
        rr_coded = 0
    elif rr <= 5:
        rr_coded = 1
    elif rr <= 9:
        rr_coded = 2
    elif rr <= 10:
        rr_coded = 3
    elif rr <= 13:
        rr_coded = 4
    elif rr <= 20:
        rr_coded = 5
    elif rr <= 29:
        rr_coded = 6
    else:
        rr_coded = 7

    # Continuous RTS
    rts_value = 0.9368 * gcs_coded + 0.7326 * sbp_coded + 0.2908 * rr_coded

    # Triage bands
    if rts_value >= 7.5:
        risk_level = "low"
    elif rts_value >= 6.0:
        risk_level = "medium"
    else:
        risk_level = "high"

    escalation_required = rts_value < 6.0

    return ScoringResult(
        score=round(rts_value, 2),
        risk_level=risk_level,
        escalation_required=escalation_required,
        component_scores={"gcs_coded": gcs_coded, "sbp_coded": sbp_coded, "rr_coded": rr_coded},
        trigger=f"RTS={rts_value:.2f}, GCS={gcs}, SBP={sbp}, RR={rr}",
        source_guideline="Revised Trauma Score (Champion et al. 1989)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shock Index (SI = HR / SBP)
# ─────────────────────────────────────────────────────────────────────────────
# SI > 1.0 suggests significant physiological compromise.
# SI > 0.9 in elderly (>60) is also concerning.

def compute_shock_index(vitals: dict[str, Any]) -> ScoringResult:
    """Shock Index from heart rate and systolic BP.

    Required keys: heart_rate (int), bp_systolic (int > 0).
    Returns the ratio HR/SBP and a risk assessment.
    Raises ScoringError on missing inputs or SBP=0.
    """
    required = ["heart_rate", "bp_systolic"]
    missing = [f for f in required if vitals.get(f) is None]
    if missing:
        raise ScoringError(
            f"Shock Index requires {required}; missing: {missing}",
            missing_fields=missing,
        )

    hr = int(vitals["heart_rate"])
    sbp = int(vitals["bp_systolic"])
    if sbp == 0:
        raise ScoringError(
            "Shock Index requires SBP > 0 (SBP=0 is incompatible with life)",
            missing_fields=["bp_systolic"],
        )

    si = round(hr / sbp, 3)

    if si > 1.0:
        risk_level = "high"
    elif si > 0.9:
        risk_level = "medium"
    else:
        risk_level = "low"

    escalation_required = si > 0.9

    return ScoringResult(
        score=si,
        risk_level=risk_level,
        escalation_required=escalation_required,
        component_scores={"heart_rate": hr, "bp_systolic": sbp},
        trigger=f"SI={si}, HR={hr}, SBP={sbp}",
        source_guideline="Shock Index (Allgöwer & Burri, 1967)",
    )
