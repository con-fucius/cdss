"""app/protocols/protocol_rag.py

Lightweight protocol RAG for ambulance dispatch.

Design constraints (from docs/OUT_OF_SCOPE.md and IMPLEMENTATION PLAN.txt):
- No LLM in the dispatch path (patient safety risk)
- No UMLS normalization (protocols use standardized terminology)
- No DDx workspace (paramedics follow protocols, not differentials)
- Always active, no external dependencies
- Sub-100ms response time
- Graceful degradation if MedSpaCy or sklearn unavailable

Architecture:
  Layer 1: MedSpaCy entity extraction (clinical NER + negation)
  Layer 2: Concept-to-protocol mapping (pre-built knowledge graph)
  Layer 3: TF-IDF cosine similarity (fallback when entity matching is weak)

The protocol set is small and finite (8 dispatch + 7 field protocols).
This module loads once at startup and serves fast in-memory lookups.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── MedSpaCy setup ──────────────────────────────────────────────────────────

_medspacy_nlp = None
_has_medspacy = False

try:
    import medspacy

    _medspacy_nlp = medspacy.load(
        "en_core_web_sm",
        enable=["medspacy_target_matcher", "medspacy_context", "medspacy_sentencizer"],
    )
    _has_medspacy = True
    logger.info("MedSpaCy loaded for protocol RAG")
except Exception as exc:
    logger.warning("MedSpaCy unavailable for protocol RAG: %s", exc)

# ── TF-IDF setup ────────────────────────────────────────────────────────────

_tfidf_vectorizer = None
_tfidf_matrix = None
_protocol_ids: list[str] = []
_has_tfidf = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    _has_tfidf = True
except ImportError:
    logger.info("sklearn unavailable — TF-IDF fallback disabled")

# ── Clinical concept → protocol mapping ─────────────────────────────────────
# Pre-built knowledge graph from protocol author specifications.
# Maps MedSpaCy entity labels to protocol IDs with weights.
# This is the core "offline-authored" retrieval knowledge.

CONCEPT_PROTOCOL_MAP: dict[str, list[tuple[str, float]]] = {
    # Cardiovascular
    "CARDIAC_ARREST": [
        ("cardiac_arrest_unresponsive_v1", 1.0),
        ("field_cardiac_arrest_v1", 0.9),
    ],
    "CHEST_PAIN": [
        ("respiratory_distress_v1", 0.6),
    ],
    "MYOCARDIAL_INFARCTION": [
        ("respiratory_distress_v1", 0.7),
    ],
    "ARRHYTHMIA": [
        ("respiratory_distress_v1", 0.5),
    ],
    "STROKE": [
        ("respiratory_distress_v1", 0.6),
    ],
    "HEMORRHAGE": [
        ("major_trauma_mva_v1", 0.7),
        ("field_major_trauma_v1", 0.6),
    ],
    "SHOCK": [
        ("major_trauma_mva_v1", 0.6),
        ("unresponsive_breathing_v1", 0.5),
    ],
    # Respiratory
    "RESPIRATORY_DISTRESS": [
        ("respiratory_distress_v1", 1.0),
        ("field_respiratory_distress_v1", 0.9),
    ],
    "RESPIRATORY_FAILURE": [
        ("cardiac_arrest_unresponsive_v1", 0.8),
        ("respiratory_distress_v1", 0.7),
        ("field_respiratory_distress_v1", 0.6),
    ],
    "CHOKING": [
        ("choking_airway_obstruction_v1", 1.0),
    ],
    "STRIDOR": [
        ("respiratory_distress_v1", 0.7),
    ],
    "ASTHMA_EXACERBATION": [
        ("respiratory_distress_v1", 0.8),
        ("field_respiratory_distress_v1", 0.7),
    ],
    # Neurological
    "UNCONSCIOUSNESS": [
        ("unresponsive_breathing_v1", 1.0),
        ("field_unresponsive_breathing_v1", 0.9),
        ("cardiac_arrest_unresponsive_v1", 0.7),
    ],
    "SEIZURE": [
        ("unresponsive_breathing_v1", 0.8),
        ("field_unresponsive_breathing_v1", 0.7),
    ],
    "HEAD_INJURY": [
        ("major_trauma_mva_v1", 0.7),
        ("field_major_trauma_v1", 0.6),
    ],
    "CONFUSION": [
        ("unresponsive_breathing_v1", 0.5),
    ],
    # Trauma
    "MOTOR_VEHICLE_ACCIDENT": [
        ("major_trauma_mva_v1", 1.0),
        ("field_trauma_moi_v1", 0.9),
    ],
    "PENETRATING_TRAUMA": [
        ("major_trauma_mva_v1", 0.9),
        ("field_major_trauma_v1", 0.8),
    ],
    "GUNSHOT_WOUND": [
        ("major_trauma_mva_v1", 0.9),
        ("field_major_trauma_v1", 0.8),
    ],
    "FALL": [
        ("major_trauma_mva_v1", 0.7),
        ("field_trauma_moi_v1", 0.6),
    ],
    "BURN": [
        ("major_trauma_mva_v1", 0.7),
        ("field_major_trauma_v1", 0.6),
    ],
    # Obstetric
    "PREGNANCY": [
        ("obstetric_emergency_v1", 0.8),
        ("field_obstetric_v1", 0.7),
    ],
    "OBSTETRIC_HEMORRHAGE": [
        ("obstetric_emergency_v1", 1.0),
        ("field_obstetric_v1", 0.9),
    ],
    "ECLAMPSIA": [
        ("obstetric_emergency_v1", 1.0),
        ("field_obstetric_v1", 0.9),
    ],
    # Paediatric
    "PAEDIATRIC_RESPIRATORY_FAILURE": [
        ("paediatric_respiratory_v1", 1.0),
        ("field_paediatric_respiratory_v1", 0.9),
    ],
    "PAEDIATRIC_CHOKING": [
        ("choking_airway_obstruction_v1", 0.8),
    ],
    "PAEDIATRIC_FEVER": [
        ("paediatric_respiratory_v1", 0.6),
        ("field_paediatric_respiratory_v1", 0.5),
    ],
    "PAEDIATRIC_SEIZURE": [
        ("paediatric_respiratory_v1", 0.8),
        ("field_paediatric_respiratory_v1", 0.7),
    ],
    "DIZZINESS": [
        ("unresponsive_breathing_v1", 0.4),
    ],
}

# Keyword fallback — comprehensive覆盖 of common emergency phrases
# Sorted longest-first to avoid partial matches
KEYWORD_PROTOCOL_MAP: dict[str, list[tuple[str, float]]] = {
    # Cardiac / Respiratory
    "not breathing": [("cardiac_arrest_unresponsive_v1", 0.9), ("field_cardiac_arrest_v1", 0.8), ("respiratory_distress_v1", 0.7)],
    "no pulse": [("cardiac_arrest_unresponsive_v1", 1.0), ("field_cardiac_arrest_v1", 0.9)],
    "stopped breathing": [("cardiac_arrest_unresponsive_v1", 0.9), ("field_cardiac_arrest_v1", 0.8)],
    "cardiac arrest": [("cardiac_arrest_unresponsive_v1", 1.0), ("field_cardiac_arrest_v1", 0.9)],
    "heart stopped": [("cardiac_arrest_unresponsive_v1", 0.9)],
    "collapsed": [("cardiac_arrest_unresponsive_v1", 0.8), ("field_cardiac_arrest_v1", 0.7), ("unresponsive_breathing_v1", 0.5)],
    "unconscious": [("unresponsive_breathing_v1", 0.8), ("field_unresponsive_breathing_v1", 0.7)],
    "unresponsive": [("unresponsive_breathing_v1", 0.8), ("field_unresponsive_breathing_v1", 0.7)],
    "seizure": [("unresponsive_breathing_v1", 0.7), ("field_unresponsive_breathing_v1", 0.6)],
    "convulsion": [("unresponsive_breathing_v1", 0.7)],
    "fitting": [("unresponsive_breathing_v1", 0.6)],
    "chest pain": [("respiratory_distress_v1", 0.6), ("field_respiratory_distress_v1", 0.5)],
    "difficulty breathing": [("respiratory_distress_v1", 1.0), ("field_respiratory_distress_v1", 0.9)],
    "short of breath": [("respiratory_distress_v1", 0.9), ("field_respiratory_distress_v1", 0.8)],
    "breathing difficulty": [("respiratory_distress_v1", 0.9)],
    "wheezing": [("respiratory_distress_v1", 0.7), ("field_respiratory_distress_v1", 0.6)],
    "asthma": [("respiratory_distress_v1", 0.7)],
    "choking": [("choking_airway_obstruction_v1", 1.0)],
    "airway obstruction": [("choking_airway_obstruction_v1", 0.9)],
    # Trauma
    "car accident": [("major_trauma_mva_v1", 0.9), ("field_trauma_moi_v1", 0.8)],
    "car crash": [("major_trauma_mva_v1", 0.9), ("field_trauma_moi_v1", 0.8)],
    "road accident": [("major_trauma_mva_v1", 0.8), ("field_trauma_moi_v1", 0.7)],
    "road traffic": [("major_trauma_mva_v1", 0.8)],
    "stab wound": [("major_trauma_mva_v1", 0.8), ("field_major_trauma_v1", 0.7)],
    "knife wound": [("major_trauma_mva_v1", 0.7), ("field_major_trauma_v1", 0.6)],
    "penetrating": [("major_trauma_mva_v1", 0.7)],
    "gunshot": [("major_trauma_mva_v1", 0.9), ("field_major_trauma_v1", 0.8)],
    "bullet wound": [("major_trauma_mva_v1", 0.8)],
    "bleeding": [("major_trauma_mva_v1", 0.6), ("field_major_trauma_v1", 0.5)],
    "severe bleeding": [("major_trauma_mva_v1", 0.8), ("field_major_trauma_v1", 0.7)],
    "heavy bleeding": [("major_trauma_mva_v1", 0.7)],
    "fall": [("major_trauma_mva_v1", 0.5), ("field_trauma_moi_v1", 0.4)],
    "burn": [("major_trauma_mva_v1", 0.6), ("field_major_trauma_v1", 0.5)],
    # Obstetric
    "pregnant": [("obstetric_emergency_v1", 0.7), ("field_obstetric_v1", 0.6)],
    "pregnancy": [("obstetric_emergency_v1", 0.7), ("field_obstetric_v1", 0.6)],
    "eclampsia": [("obstetric_emergency_v1", 0.9), ("field_obstetric_v1", 0.8)],
    # Paediatric
    "child": [("paediatric_respiratory_v1", 0.5), ("field_paediatric_respiratory_v1", 0.4)],
    "baby": [("paediatric_respiratory_v1", 0.5), ("field_paediatric_respiratory_v1", 0.4)],
    "infant": [("paediatric_respiratory_v1", 0.5), ("field_paediatric_respiratory_v1", 0.4)],
    # Swahili
    "kushindwa kupumua": [("respiratory_distress_v1", 0.9), ("field_respiratory_distress_v1", 0.8)],
    "kupumua kwa shida": [("respiratory_distress_v1", 0.9)],
    "maumivu ya kifua": [("respiratory_distress_v1", 0.7)],
    "mshtuko": [("unresponsive_breathing_v1", 0.7)],
    "kutokwa na damu": [("major_trauma_mva_v1", 0.7), ("field_major_trauma_v1", 0.6)],
    "ameanguka": [("major_trauma_mva_v1", 0.5), ("field_trauma_moi_v1", 0.4)],
}


class ProtocolRAG:
    """Lightweight protocol retrieval for emergency dispatch.

    Always available. No external dependencies at query time.
    Sub-100ms response for the small finite protocol set.
    """

    def __init__(self):
        self._protocols: dict[str, dict] = {}
        self._indexed = False

    def index(self, protocols: list[dict]) -> None:
        """Build indices from loaded protocol definitions.

        Called once at startup after registry.load_all().
        """
        self._protocols = {p["protocol_id"]: p for p in protocols}

        # Build TF-IDF index for fallback similarity
        if _has_tfidf and protocols:
            corpus = []
            _protocol_ids.clear()
            for p in protocols:
                triggers = " ".join(p.get("triggers", []))
                desc = p.get("description", "")
                keywords = " ".join(p.get("keywords", []))
                corpus.append(f"{triggers} {desc} {keywords}".lower())
                _protocol_ids.append(p["protocol_id"])

            if corpus:
                global _tfidf_vectorizer, _tfidf_matrix
                _tfidf_vectorizer = TfidfVectorizer(
                    stop_words="english", ngram_range=(1, 2), max_features=500
                )
                _tfidf_matrix = _tfidf_vectorizer.fit_transform(corpus)

        self._indexed = True
        logger.info(
            "Protocol RAG indexed: %d protocols, MedSpaCy=%s, TF-IDF=%s",
            len(protocols),
            _has_medspacy,
            _has_tfidf,
        )

    def match(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Match a chief complaint against protocols.

        Returns list of {protocol_id, score, method, name, description}.
        Always returns results — degrades gracefully at each layer.
        """
        if not self._indexed:
            return []

        scores: dict[str, float] = {}
        methods: dict[str, list[str]] = {}

        def _add(pid: str, score: float, method: str) -> None:
            if pid not in scores:
                scores[pid] = 0.0
                methods[pid] = []
            scores[pid] += score
            methods[pid].append(method)

        # Layer 1: MedSpaCy entity extraction (fastest, most precise)
        if _has_medspacy and _medspacy_nlp is not None:
            try:
                doc = _medspacy_nlp(query)
                for ent in doc.ents:
                    label = getattr(ent._, "category", ent.label_)
                    if label in CONCEPT_PROTOCOL_MAP:
                        for pid, weight in CONCEPT_PROTOCOL_MAP[label]:
                            if pid in self._protocols:
                                # Boost non-negated entities
                                negated = getattr(ent._, "negated", False)
                                effective_weight = weight * (0.3 if negated else 1.0)
                                _add(pid, effective_weight, f"medspacy:{label}")

                # Also check individual token entities
                for ent in doc.ents:
                    label = getattr(ent._, "category", ent.label_)
                    # Handle compound entities
                    if "RESPIRATORY" in label and "FAILURE" in label:
                        for pid, w in CONCEPT_PROTOCOL_MAP.get("RESPIRATORY_FAILURE", []):
                            if pid in self._protocols:
                                _add(pid, w * 0.8, "medspacy:compound")
            except Exception as exc:
                logger.debug("MedSpaCy extraction failed: %s", exc)

        # Layer 2: Keyword fallback (always works, no deps)
        query_lower = query.lower()
        for keyword, mappings in KEYWORD_PROTOCOL_MAP.items():
            if keyword in query_lower:
                for pid, weight in mappings:
                    if pid in self._protocols:
                        _add(pid, weight, f"keyword:{keyword}")

        # Layer 3: TF-IDF similarity (fallback when layers 1-2 are weak)
        if _has_tfidf and _tfidf_vectorizer is not None and _tfidf_matrix is not None:
            max_score = max(scores.values()) if scores else 0.0
            if max_score < 0.5:  # Only use TF-IDF when concept matching is weak
                try:
                    query_vec = _tfidf_vectorizer.transform([query_lower])
                    similarities = cosine_similarity(query_vec, _tfidf_matrix).flatten()
                    for idx, sim in enumerate(similarities):
                        if sim > 0.05 and idx < len(_protocol_ids):
                            pid = _protocol_ids[idx]
                            if pid in self._protocols:
                                _add(pid, float(sim) * 0.3, "tfidf")
                except Exception as exc:
                    logger.debug("TF-IDF matching failed: %s", exc)

        # Sort by score and return top_k
        sorted_pids = sorted(scores.keys(), key=lambda p: scores[p], reverse=True)
        results = []
        for pid in sorted_pids[:top_k]:
            proto = self._protocols.get(pid, {})
            results.append({
                "protocol_id": pid,
                "score": round(scores[pid], 3),
                "methods": methods[pid],
                "name": proto.get("name", pid),
                "description": proto.get("description", ""),
                "version": proto.get("version", ""),
            })

        return results

    def is_available(self) -> bool:
        return self._indexed


# Singleton
protocol_rag = ProtocolRAG()
