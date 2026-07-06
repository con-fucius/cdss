"""app/nlp_extractor.py.

MedSpaCy-based clinical NLP extraction for emergency dispatch transcripts.

Pipeline:
  1. medspacy TargetMatcher — rule-based clinical entity extraction with
     negation detection, severity modifiers, and clinical context
  2. spaCy NER — general-purpose named entity recognition for locations,
     people, and organizations
  3. ConText — negation/uncertainty/historical modifier detection
  4. Regex fallback — structured value extraction (BP, HR, RR, GCS)

The model is loaded ONCE at import time and cached. In production the
Dockerfile must bake the spaCy model into the container — it is NOT
downloaded at runtime. If medspacy or the model is unavailable, the
module degrades to pure regex (same as the original implementation but
now behind the same interface).

PHI handling: this module processes caller transcripts. It must NEVER
log the raw transcript. All logging is limited to entity counts and
confidence scores.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Model loading (once at import) ──────────────────────────────────────────

_MEDSPACY_AVAILABLE = False
_nlp: Any = None  # medspacy spaCy Language instance

try:
    import medspacy  # noqa: F401
    import spacy  # noqa: F401

    # Load medspacy with target_matcher, context, and sentencizer
    # The model name is configurable via env; default is en_core_web_sm
    # which ships with medspacy. For production, use en_core_web_md.
    _nlp = medspacy.load(
        "en_core_web_sm",
        enable=["medspacy_target_matcher", "medspacy_context", "medspacy_sentencizer"],
    )
    _MEDSPACY_AVAILABLE = True
    logger.info("MedSpaCy pipeline loaded successfully")
except Exception as exc:
    logger.warning(
        "MedSpaCy not available — falling back to regex extraction: %s", exc
    )


# ── Clinical entity rules ───────────────────────────────────────────────────
# These TargetRules define what clinical entities to extract from
# emergency dispatch transcripts. Each rule has a pattern, a label,
# and optional attributes for context detection.
#
# Categories match the implementation plan Phase 2:
# RESPIRATORY, CARDIOVASCULAR, NEUROLOGICAL, TRAUMA, OBSTETRIC, PAEDIATRIC


@dataclass
class ClinicalRule:
    """A clinical entity extraction rule."""

    pattern: str
    label: str
    category: str
    severity_weight: float = 0.5
    icd10_prefix: str = ""
    snomed_hint: str = ""
    synonyms: list[str] = field(default_factory=list)
    is_negated: bool = False
    modifiers: list[str] = field(default_factory=list)


# Emergency dispatch clinical rules — covers Kenya's top emergency
# presentations as specified in the implementation plan Phase 2.2.
CLINICAL_RULES: list[ClinicalRule] = [
    # ── CARDIOVASCULAR ──────────────────────────────────────────────────
    ClinicalRule(
        pattern="cardiac arrest",
        label="CARDIAC_ARREST",
        category="CARDIOVASCULAR",
        severity_weight=1.0,
        icd10_prefix="I46",
        snomed_hint="419000005",
        synonyms=["heart stopped", "no pulse", "not breathing", "collapsed"],
    ),
    ClinicalRule(
        pattern="chest pain",
        label="CHEST_PAIN",
        category="CARDIOVASCULAR",
        severity_weight=0.7,
        icd10_prefix="R07",
        snomed_hint="29857009",
        synonyms=["chest tightness", "angina", "crushing chest", "pressure in chest"],
    ),
    ClinicalRule(
        pattern="heart attack",
        label="MYOCARDIAL_INFARCTION",
        category="CARDIOVASCULAR",
        severity_weight=0.9,
        icd10_prefix="I21",
        snomed_hint="22298006",
        synonyms=["myocardial infarction", "MI", "stemi", "nstemi"],
    ),
    ClinicalRule(
        pattern="irregular heartbeat",
        label="ARRHYTHMIA",
        category="CARDIOVASCULAR",
        severity_weight=0.6,
        icd10_prefix="I49",
        snomed_hint="69994004",
        synonyms=["irregular pulse", "palpitations", "fast heart", "heart racing"],
    ),
    ClinicalRule(
        pattern="stroke",
        label="STROKE",
        category="CARDIOVASCULAR",
        severity_weight=0.9,
        icd10_prefix="I63",
        snomed_hint="230690007",
        synonyms=[
            "face drooping", "arm weakness", "speech difficulty",
            "slurred speech", "FAST", "cerebrovascular accident",
        ],
    ),
    ClinicalRule(
        pattern="severe bleeding",
        label="HEMORRHAGE",
        category="CARDIOVASCULAR",
        severity_weight=0.8,
        icd10_prefix="R58",
        snomed_hint="422587007",
        synonyms=[
            "heavy bleeding", "blood everywhere", "hemorrhage",
            "bleeding heavily", "cannot stop bleeding",
        ],
    ),

    # ── RESPIRATORY ─────────────────────────────────────────────────────
    ClinicalRule(
        pattern="difficulty breathing",
        label="RESPIRATORY_DISTRESS",
        category="RESPIRATORY",
        severity_weight=0.7,
        icd10_prefix="R06",
        snomed_hint="67362008",
        synonyms=[
            "short of breath", "can't breathe", "unable to breathe",
            "breathing difficulty", "dyspnea", "wheezing",
        ],
    ),
    ClinicalRule(
        pattern="not breathing",
        label="RESPIRATORY_FAILURE",
        category="RESPIRATORY",
        severity_weight=1.0,
        icd10_prefix="R09",
        snomed_hint="409600008",
        synonyms=["stopped breathing", "no breathing", "apnea", "respiratory arrest"],
    ),
    ClinicalRule(
        pattern="choking",
        label="CHOKING",
        category="RESPIRATORY",
        severity_weight=0.8,
        icd10_prefix="T78",
        snomed_hint="195967002",
        synonyms=["airway obstruction", "food stuck", "can't swallow", "choking on food"],
    ),
    ClinicalRule(
        pattern="stridor",
        label="STRIDOR",
        category="RESPIRATORY",
        severity_weight=0.75,
        icd10_prefix="R06",
        snomed_hint="49727002",
        synonyms=["high-pitched breathing", "noisy breathing"],
    ),
    ClinicalRule(
        pattern="asthma",
        label="ASTHMA_EXACERBATION",
        category="RESPIRATORY",
        severity_weight=0.6,
        icd10_prefix="J46",
        snomed_hint="56018004",
        synonyms=["asthma attack", "asthma exacerbation", "bronchospasm"],
    ),

    # ── NEUROLOGICAL ────────────────────────────────────────────────────
    ClinicalRule(
        pattern="unconscious",
        label="UNCONSCIOUSNESS",
        category="NEUROLOGICAL",
        severity_weight=0.9,
        icd10_prefix="R40",
        snomed_hint="371631003",
        synonyms=["unresponsive", "not responding", "passed out", "fainted", "collapsed"],
    ),
    ClinicalRule(
        pattern="seizure",
        label="SEIZURE",
        category="NEUROLOGICAL",
        severity_weight=0.8,
        icd10_prefix="R56",
        snomed_hint="91168004",
        synonyms=["convulsion", "fit", "shaking", "epilepsy", "convulsing", "foaming"],
    ),
    ClinicalRule(
        pattern="head injury",
        label="HEAD_INJURY",
        category="NEUROLOGICAL",
        severity_weight=0.7,
        icd10_prefix="S09",
        snomed_hint="118865004",
        synonyms=["hit head", "head trauma", "knocked out", "banged head"],
    ),
    ClinicalRule(
        pattern="confusion",
        label="CONFUSION",
        category="NEUROLOGICAL",
        severity_weight=0.5,
        icd10_prefix="R41",
        snomed_hint="414916001",
        synonyms=["disoriented", "doesn't know where they are", "altered mental status"],
    ),

    # ── TRAUMA ──────────────────────────────────────────────────────────
    ClinicalRule(
        pattern="car accident",
        label="MOTOR_VEHICLE_ACCIDENT",
        category="TRAUMA",
        severity_weight=0.7,
        icd10_prefix="V89",
        snomed_hint="281004003",
        synonyms=["car crash", "road accident", "hit by car", "MVA", "RTA", "road traffic"],
    ),
    ClinicalRule(
        pattern="stab wound",
        label="PENETRATING_TRAUMA",
        category="TRAUMA",
        severity_weight=0.8,
        icd10_prefix="S31",
        snomed_hint="216700007",
        synonyms=["knife wound", "penetrating injury", "stabbing"],
    ),
    ClinicalRule(
        pattern="gunshot",
        label="GUNSHOT_WOUND",
        category="TRAUMA",
        severity_weight=0.9,
        icd10_prefix="S35",
        snomed_hint="118849005",
        synonyms=["bullet wound", "shot", "gunshot wound", "GSW", "shooting"],
    ),
    ClinicalRule(
        pattern="fall",
        label="FALL",
        category="TRAUMA",
        severity_weight=0.5,
        icd10_prefix="W19",
        snomed_hint="71388004",
        synonyms=["fell down", "fall from height", "tripped and fell"],
    ),
    ClinicalRule(
        pattern="burn",
        label="BURN",
        category="TRAUMA",
        severity_weight=0.6,
        icd10_prefix="T30",
        snomed_hint="49532008",
        synonyms=["burned", "scalded", "fire", "flames", "thermal injury"],
    ),

    # ── OBSTETRIC ───────────────────────────────────────────────────────
    ClinicalRule(
        pattern="pregnant",
        label="PREGNANCY",
        category="OBSTETRIC",
        severity_weight=0.6,
        icd10_prefix="Z34",
        snomed_hint="276521008",
        synonyms=["pregnant woman", "expecting mother", "antepartum"],
    ),
    ClinicalRule(
        pattern="heavy bleeding in pregnancy",
        label="OBSTETRIC_HEMORRHAGE",
        category="OBSTETRIC",
        severity_weight=0.9,
        icd10_prefix="O46",
        snomed_hint="237148007",
        synonyms=["bleeding during pregnancy", "vaginal bleeding", "antepartum hemorrhage"],
    ),
    ClinicalRule(
        pattern="eclampsia",
        label="ECLAMPSIA",
        category="OBSTETRIC",
        severity_weight=1.0,
        icd10_prefix="O15",
        snomed_hint="398254005",
        synonyms=["seizure in pregnancy", "pregnancy seizure", "convulsion in pregnancy"],
    ),
    ClinicalRule(
        pattern="cord prolapse",
        label="CORD_PROLAPSE",
        category="OBSTETRIC",
        severity_weight=1.0,
        icd10_prefix="O69",
        snomed_hint="276526003",
        synonyms=["umbilical cord prolapse", "cord out", "prolapsed cord"],
    ),
    ClinicalRule(
        pattern="difficulty breathing in pregnancy",
        label="OBSTETRIC_RESPIRATORY",
        category="OBSTETRIC",
        severity_weight=0.8,
        icd10_prefix="O99",
        snomed_hint="195662009",
        synonyms=["breathless pregnant", "short of breath pregnant"],
    ),

    # ── PAEDIATRIC ──────────────────────────────────────────────────────
    ClinicalRule(
        pattern="child not breathing",
        label="PAEDIATRIC_RESPIRATORY_FAILURE",
        category="PAEDIATRIC",
        severity_weight=1.0,
        icd10_prefix="R06",
        snomed_hint="70076002",
        synonyms=["baby not breathing", "infant apnea", "child stopped breathing"],
    ),
    ClinicalRule(
        pattern="child choking",
        label="PAEDIATRIC_CHOKING",
        category="PAEDIATRIC",
        severity_weight=0.8,
        icd10_prefix="T78",
        snomed_hint="428617000",
        synonyms=["baby choking", "child swallowed something", "infant choking"],
    ),
    ClinicalRule(
        pattern="fever in child",
        label="PAEDIATRIC_FEVER",
        category="PAEDIATRIC",
        severity_weight=0.4,
        icd10_prefix="R50",
        snomed_hint="386661006",
        synonyms=["high temperature", "febrile child", "baby fever", "child fever"],
    ),
    ClinicalRule(
        pattern="child seizure",
        label="PAEDIATRIC_SEIZURE",
        category="PAEDIATRIC",
        severity_weight=0.8,
        icd10_prefix="R56",
        snomed_hint="429984005",
        synonyms=["baby fitting", "child convulsion", "febrile seizure", "infant seizure"],
    ),

    # ── SWAHILI TERMS ──────────────────────────────────────────────────
    # These are essential for Kenya — see implementation plan Phase 2.2
    ClinicalRule(
        pattern="mshtuko",
        label="SHOCK",
        category="CARDIOVASCULAR",
        severity_weight=0.9,
        icd10_prefix="R57",
        snomed_hint="300711005",
        synonyms=["mshtuko wa moyo"],
    ),
    ClinicalRule(
        pattern="maumivu ya kifua",
        label="CHEST_PAIN",
        category="CARDIOVASCULAR",
        severity_weight=0.7,
        icd10_prefix="R07",
        snomed_hint="29857009",
        synonyms=["maumivu ya kifua kikuu"],
    ),
    ClinicalRule(
        pattern="kizunguzungu",
        label="DIZZINESS",
        category="NEUROLOGICAL",
        severity_weight=0.4,
        icd10_prefix="R42",
        snomed_hint="404640003",
        synonyms=["kichefuchefu"],
    ),
    ClinicalRule(
        pattern="kushindwa kupumua",
        label="RESPIRATORY_DISTRESS",
        category="RESPIRATORY",
        severity_weight=0.7,
        icd10_prefix="R06",
        snomed_hint="67362008",
        synonyms=["kupumua kwa shida", "hawezi kupumua"],
    ),
    ClinicalRule(
        pattern="kutokwa na damu",
        label="HEMORRHAGE",
        category="CARDIOVASCULAR",
        severity_weight=0.8,
        icd10_prefix="R58",
        snomed_hint="422587007",
        synonyms=["damu nyingi", "kutokwa damu"],
    ),
    ClinicalRule(
        pattern="vifaranga vya ubongo",
        label="STROKE",
        category="CARDIOVASCULAR",
        severity_weight=0.9,
        icd10_prefix="I63",
        snomed_hint="230690007",
        synonyms=["mapigo ya moyo"],
    ),
    ClinicalRule(
        pattern="mapigo ya moyo",
        label="ARRHYTHMIA",
        category="CARDIOVASCULAR",
        severity_weight=0.6,
        icd10_prefix="I49",
        snomed_hint="69994004",
        synonyms=["mapigo ya moyo yasiyo ya kawaida"],
    ),

    # ── SEVERITY MODIFIERS ──────────────────────────────────────────────
    # These modify nearby entities — detected by ConText
    ClinicalRule(
        pattern="severe",
        label="SEVERITY_MODIFIER",
        category="MODIFIER",
        severity_weight=0.3,
    ),
    ClinicalRule(
        pattern="critical",
        label="SEVERITY_MODIFIER",
        category="MODIFIER",
        severity_weight=0.4,
    ),
    ClinicalRule(
        pattern="acute",
        label="SEVERITY_MODIFIER",
        category="MODIFIER",
        severity_weight=0.2,
    ),
    ClinicalRule(
        pattern="mild",
        label="SEVERITY_MODIFIER",
        category="MODIFIER",
        severity_weight=-0.2,
    ),
    ClinicalRule(
        pattern="improving",
        label="SEVERITY_MODIFIER",
        category="MODIFIER",
        severity_weight=-0.3,
    ),
]

# ── Regex patterns for structured values ────────────────────────────────────

_BP_PATTERN = re.compile(
    r"\b(?:bp|blood\s+pressure)\s*(?:is|was|=|:)?\s*(\d{2,3})\s*(?:over|/|x)\s*(\d{2,3})\b",
    re.IGNORECASE,
)
_HR_PATTERN = re.compile(
    r"\b(?:heart\s+rate|hr|pulse)\s*(?:is|was|=|:)?\s*(\d{2,3})\b",
    re.IGNORECASE,
)
_RR_PATTERN = re.compile(
    r"\b(?:respiratory\s+rate|rr|breathing\s+rate)\s*(?:is|was|=|:)?\s*(\d{1,3})\b",
    re.IGNORECASE,
)
_GCS_PATTERN = re.compile(
    r"\b(?:gcs|glasgow\s+coma\s+scale)\s*(?:is|was|=|:)?\s*(\d{1,2})\b",
    re.IGNORECASE,
)
_TEMP_PATTERN = re.compile(
    r"\b(?:temp|temperature)\s*(?:is|was|=|:)?\s*(\d{2,3}(?:\.\d)?)\s*°?\s*c?\b",
    re.IGNORECASE,
)
_AGE_PATTERN = re.compile(
    r"\b(\d{1,3})\s*(?:year|yr|yo|month|mo|day|d)\s*(?:old|o/?ld)?\b",
    re.IGNORECASE,
)
_SPO2_PATTERN = re.compile(
    r"\b(?:s(?:at|po2)|oxygen\s+sat(?:uration)?)\s*(?:is|was|=|:)?\s*(\d{1,3})\s*%?\b",
    re.IGNORECASE,
)

# ── Negation patterns ───────────────────────────────────────────────────────

_NEGATION_PATTERNS = re.compile(
    r"\b(?:no|not|without|denies?|denying|negative\s+for|absent|nil|none)\b",
    re.IGNORECASE,
)


def _extract_regex_values(transcript: str) -> dict[str, Any]:
    """Extract structured clinical values via regex. Works independently
    of MedSpaCy and serves as both a complementary layer and fallback.
    """
    vitals: dict[str, Any] = {}

    bp = _BP_PATTERN.search(transcript)
    if bp:
        vitals["bp_systolic"] = int(bp.group(1))
        vitals["bp_diastolic"] = int(bp.group(2))

    hr = _HR_PATTERN.search(transcript)
    if hr:
        vitals["heart_rate"] = int(hr.group(1))

    rr = _RR_PATTERN.search(transcript)
    if rr:
        vitals["respiratory_rate"] = int(rr.group(1))

    gcs = _GCS_PATTERN.search(transcript)
    if gcs:
        vitals["gcs_total"] = int(gcs.group(1))

    temp = _TEMP_PATTERN.search(transcript)
    if temp:
        vitals["temperature"] = float(temp.group(1))

    spo2 = _SPO2_PATTERN.search(transcript)
    if spo2:
        val = int(spo2.group(1))
        if 0 <= val <= 100:
            vitals["spo2"] = val

    age = _AGE_PATTERN.search(transcript)
    if age:
        vitals["age_mentioned"] = int(age.group(1))

    return vitals


def _check_negation(transcript: str, span_start: int, span_end: int) -> bool:
    """Check if an entity span is preceded by a negation word within 15 characters
    on the same side of a clause boundary (comma, semicolon, period).

    This is narrower than MedSpaCy's ConText but avoids false negatives
    like "no pulse" being marked negated because "not breathing," appeared
    earlier in the same sentence.
    """
    # Look back only 15 characters — avoids cross-clause false positives
    lookback_start = max(0, span_start - 15)
    lookback = transcript[lookback_start : span_start].lower()

    # Don't cross clause boundaries — if a comma/semicolon/period appears
    # between the negation word and the entity, it's likely a different clause
    for boundary in (",", ";", ".", "!", "?"):
        if boundary in lookback:
            lookback = lookback[lookback.rindex(boundary) + 1 :]

    return bool(_NEGATION_PATTERNS.search(lookback))


def _extract_entities_medspacy(transcript: str) -> list[dict[str, Any]]:
    """Extract clinical entities using MedSpaCy pipeline.

    Returns a list of dicts with keys:
      - text: matched text
      - label: entity label
      - category: clinical category
      - severity_weight: weight for scoring
      - negated: whether the entity is negated
      - start, end: character offsets
    """
    if _nlp is None:
        return []

    doc = _nlp(transcript)
    entities = []

    for ent in doc.ents:
        # Map MedSpaCy entity attributes to our format
        label = ent.label_
        category = getattr(ent._, "category", "UNKNOWN")
        severity = getattr(ent._, "severity_weight", 0.5)
        negated = getattr(ent._, "negated", False)

        # Check if negated via context attributes
        if not negated and hasattr(ent._, "is_negated"):
            negated = getattr(ent._, "is_negated", False)

        entities.append({
            "text": ent.text,
            "label": label,
            "category": category,
            "severity_weight": severity,
            "negated": negated,
            "start": ent.start_char,
            "end": ent.end_char,
            "source": "medspacy",
        })

    return entities


def _extract_entities_regex(transcript: str) -> list[dict[str, Any]]:
    """Fallback: extract clinical entities via keyword matching + regex.

    Uses CLINICAL_RULES as a keyword dictionary. Less precise than
    MedSpaCy but works without the model.
    """
    import re as _re

    entities = []
    transcript_lower = transcript.lower()

    for rule in CLINICAL_RULES:
        # Check main pattern and synonyms
        all_patterns = [rule.pattern.lower()] + [s.lower() for s in rule.synonyms]
        for pattern in all_patterns:
            # Use word boundary matching to avoid false positives from
            # substring matches (e.g. "mi" matching in "maumivu")
            if len(pattern) <= 3:
                # Short patterns need strict word boundaries
                match = _re.search(r'\b' + _re.escape(pattern) + r'\b', transcript_lower)
            else:
                # Longer patterns use simple substring match
                idx = transcript_lower.find(pattern)
                match = type('obj', (object,), {'start': lambda self: idx, 'end': lambda self: idx + len(pattern)})() if idx != -1 else None

            if match is not None:
                start_idx = match.start()
                end_idx = match.end()
                # Check negation
                negated = _check_negation(transcript, start_idx, end_idx)
                # Skip negated entities unless they're modifiers
                if negated and rule.category != "MODIFIER":
                    entities.append({
                        "text": transcript[start_idx:end_idx],
                        "label": rule.label,
                        "category": rule.category,
                        "severity_weight": rule.severity_weight,
                        "negated": True,
                        "start": start_idx,
                        "end": end_idx,
                        "source": "regex_negated",
                    })
                    break
                entities.append({
                    "text": transcript[start_idx:end_idx],
                    "label": rule.label,
                    "category": rule.category,
                    "severity_weight": rule.severity_weight,
                    "negated": False,
                    "start": start_idx,
                    "end": end_idx,
                    "source": "regex",
                })
                break

    return entities


def extract_clinical_entities(
    transcript: str, *, try_llm: bool = True
) -> dict[str, Any]:
    """Main extraction function. Combines MedSpaCy NER, regex structured
    values, and keyword matching into a unified result.

    When try_llm=True and the LLM client is configured, falls back to
    LLM extraction if the local pipeline finds no entities and confidence
    is low.

    Returns:
      - vitals: structured clinical values (BP, HR, RR, GCS, etc.)
      - entities: list of extracted clinical entities with metadata
      - chief_complaint_suggestion: top clinical complaint
      - location_text: extracted location
      - confidence: extraction confidence score
      - degraded_mode: True if MedSpaCy was not available
    """
    if not transcript or not transcript.strip():
        return {
            "vitals": {},
            "entities": [],
            "chief_complaint_suggestion": None,
            "location_text": None,
            "confidence": 0.0,
            "degraded_mode": True,
        }

    # Step 1: Extract structured values (always works)
    vitals = _extract_regex_values(transcript)

    # Step 2: Extract clinical entities via MedSpaCy or regex fallback
    if _MEDSPACY_AVAILABLE and _nlp is not None:
        entities = _extract_entities_medspacy(transcript)
        degraded = False
        # When MedSpaCy finds nothing, also run regex as fallback
        if not entities:
            entities = _extract_entities_regex(transcript)
    else:
        entities = _extract_entities_regex(transcript)
        degraded = True

    # Also extract entities from regex as complementary layer
    regex_entities = _extract_entities_regex(transcript)

    # Merge: add regex-only entities that MedSpaCy missed
    medspacy_labels = {e["label"] for e in entities}
    for re_ent in regex_entities:
        if re_ent["label"] not in medspacy_labels and not re_ent.get("negated"):
            entities.append(re_ent)

    # Sort all entities by severity (descending) for output
    entities.sort(key=lambda e: e.get("severity_weight", 0), reverse=True)

    # Determine chief complaint from highest-severity non-negated entity
    active_entities = [e for e in entities if not e.get("negated")]

    chief_complaint_suggestion = None
    confidence = 0.3  # base confidence

    if active_entities:
        # Priority overrides for emergency dispatch:
        # 1. Cardiac arrest indicators (no pulse + not breathing) win over respiratory failure
        # 2. Trauma mechanism (car crash, fall, stabbing) wins over associated injuries
        labels = {e["label"] for e in active_entities}
        categories = {e["category"] for e in active_entities}

        if "CARDIAC_ARREST" in labels or (
            "NO_PULSE" in labels and "RESPIRATORY_FAILURE" in labels
        ):
            top = next(e for e in active_entities if e["label"] == "CARDIAC_ARREST") \
                if "CARDIAC_ARREST" in labels else active_entities[0]
        elif "TRAUMA" in categories and any(
            e["label"] in ("MOTOR_VEHICLE_ACCIDENT", "FALL", "PENETRATING_TRAUMA",
                           "GUNSHOT_WOUND", "BURN")
            for e in active_entities
        ):
            # Trauma mechanism wins over associated injuries (bleeding, pain)
            trauma_entities = [e for e in active_entities if e["category"] == "TRAUMA"]
            top = trauma_entities[0]
        else:
            top = active_entities[0]
        chief_complaint_suggestion = top["label"].lower().replace("_", " ")
        # Confidence scales with severity weight
        severity = top.get("severity_weight", 0.5)
        confidence = max(confidence, 0.4 + severity * 0.5)

    # Boost confidence when structured vitals are extracted
    if vitals:
        confidence = max(confidence, 0.6)
    if len(vitals) >= 3:
        confidence = max(confidence, 0.7)
    # GCS is a high-value clinical measure — single GCS alone warrants 0.7
    if "gcs_total" in vitals:
        confidence = max(confidence, 0.7)

    # Step 4: Location extraction (Kenya-specific landmarks)
    kenyan_landmarks = [
        "kenyatta", "muhimbili", "karen", "nairobi hospital", "garden city",
        "westlands", "kibera", "eastleigh", "upper hill", "loresho",
        "kisumu hospital", "nakuru", "eldoret", "mombasa hospital",
        "coast general", "kenyatta national",
    ]
    transcript_lower = transcript.lower()
    location_text = None
    for landmark in kenyan_landmarks:
        if landmark in transcript_lower:
            idx = transcript_lower.index(landmark)
            # Extract the landmark and any immediately following word that
            # forms part of the place name (e.g. "Kenyatta Hospital").
            end = idx + len(landmark)
            remaining = transcript[end : end + 30].strip()
            # If the next word starts with a capital letter or is a known
            # suffix, include it (e.g. "Hospital", "National").
            suffix_words = {"hospital", "national", "centre", "center", "area"}
            if remaining:
                next_word = remaining.split()[0].lower().rstrip(",.;:!")
                if next_word in suffix_words or (
                    remaining[0].isupper() and next_word.isalpha()
                ):
                    end = end + len(remaining.split()[0])
            location_text = transcript[idx:end].strip()
            confidence = max(confidence, 0.5)
            break

    # Step 5: Boost confidence if MedSpaCy extracted with ConText context
    if not degraded and active_entities:
        confidence = min(confidence + 0.1, 0.95)

    # Step 6: LLM fallback — when local extraction finds nothing useful,
    # try the configured LLM for entity extraction.
    llm_used = False
    if try_llm and not active_entities and confidence < 0.5:
        try:
            from .external.llm_client import LLMClient
            llm = LLMClient()
            if llm.is_configured:
                import asyncio
                llm_result = asyncio.get_event_loop().run_until_complete(
                    llm.extract_entities(transcript)
                )
                if llm_result is not None:
                    llm_used = True
                    # Merge LLM vitals
                    llm_vitals = llm_result.get("vitals") or {}
                    for k, v in llm_vitals.items():
                        if k not in vitals and v is not None:
                            vitals[k] = v

                    # Merge LLM entities
                    llm_entities = llm_result.get("entities") or []
                    existing_labels = {e["label"] for e in entities}
                    for ent in llm_entities:
                        if ent.get("label") not in existing_labels:
                            ent["source"] = "llm"
                            entities.append(ent)
                    entities.sort(
                        key=lambda e: e.get("severity_weight", 0), reverse=True
                    )

                    # Use LLM chief complaint if we still have none
                    if chief_complaint_suggestion is None and llm_result.get(
                        "chief_complaint"
                    ):
                        chief_complaint_suggestion = llm_result["chief_complaint"]

                    # Use LLM location if we still have none
                    if location_text is None and llm_result.get("location_text"):
                        location_text = llm_result["location_text"]

                    # Use LLM confidence as a floor
                    llm_conf = llm_result.get("confidence", 0)
                    if llm_conf > confidence:
                        confidence = llm_conf

                    logger.info("LLM fallback contributed to extraction")
        except Exception as exc:
            logger.warning("LLM fallback in nlp_extractor failed: %s", exc)

    logger.info(
        "NLP extraction: %d entities, degraded=%s, confidence=%.2f, llm=%s",
        len(active_entities),
        degraded,
        confidence,
        llm_used,
    )

    return {
        "vitals": vitals,
        "entities": entities,
        "chief_complaint_suggestion": chief_complaint_suggestion,
        "location_text": location_text,
        "confidence": confidence,
        "degraded_mode": degraded,
        "llm_used": llm_used,
    }
