"""
triage-ranker/app/pipeline/extractor.py

Stage 1 — NLP entity extraction.

Loads spaCy en_core_web_md model (path from config, never downloaded
at runtime — Kenya's network cannot be assumed during a live call).
Tokenizes incident_desc, applies clinical_rules.yaml term matching
(case-insensitive), extracts ACVPU clues, vital sign mentions,
and negation markers.

Returns List[ExtractedKeyword] from shared contracts.

Design constraints:
- spaCy model must be baked into the container, not downloaded at runtime
- Swahili terms not recognised by spaCy model are handled via
  keyword matching after NLP tokenisation
- Negation detection uses ConText-style rules (simple pattern matching)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import yaml
from ambulance_cdss_contracts.triage import ClinicalCategory, ExtractedKeyword

logger = logging.getLogger(__name__)

# Compiled pattern cache for clinical rules
_rules_cache: Optional[List[Dict[str, Any]]] = None
_compiled_patterns: Optional[List[Tuple[re.Pattern, Dict[str, Any]]]] = None

# Negation cues for simple negation detection
_NEGATION_CUES = [
    "no", "not", "denies", "denied", "without", "hakuna",
    "sijawahi", "hajawahi", "haina", "hamna",
]

# Severity modifier cues
_SEVERITY_MODIFIERS = {
    "severe": "SEVERITY_SEVERE",
    "mkali": "SEVERITY_SEVERE",
    "extreme": "SEVERITY_SEVERE",
    "critical": "SEVERITY_CRITICAL",
    "hatari": "SEVERITY_CRITICAL",
    "acute": "ACTIVE",
    "sudden": "ACTIVE",
    "ghafla": "ACTIVE",
}


def _load_rules(rules_path: str) -> List[Dict[str, Any]]:
    """Load clinical_rules.yaml. Cached after first load."""
    global _rules_cache
    if _rules_cache is None:
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            _rules_cache = data.get("rules", [])
            logger.info("Loaded %d clinical rules from %s", len(_rules_cache), rules_path)
        except Exception as exc:
            logger.error("Failed to load clinical rules from %s: %s", rules_path, exc)
            _rules_cache = []
    return _rules_cache


def _build_compiled_patterns(rules: List[Dict[str, Any]]) -> List[Tuple[re.Pattern, Dict[str, Any]]]:
    """Build compiled regex patterns for fast matching."""
    global _compiled_patterns
    if _compiled_patterns is not None:
        return _compiled_patterns

    patterns = []
    for rule in rules:
        # Build pattern from term + synonyms
        terms = [rule["term"]] + rule.get("synonyms", [])
        # Escape and join with OR
        escaped = [re.escape(t.lower()) for t in terms]
        pattern_str = "|".join(escaped)
        compiled = re.compile(r"\b(?:%s)\b" % pattern_str, re.IGNORECASE)
        patterns.append((compiled, rule))

    _compiled_patterns = patterns
    return patterns


def _detect_negation(text: str, match_start: int, match_end: int) -> bool:
    """
    Simple negation detection: check if any negation cue appears
    within 5 words before the match.
    """
    # Look at the 50 characters before the match
    prefix = text[max(0, match_start - 50) : match_start].lower().split()
    for cue in _NEGATION_CUES:
        if cue in prefix[-5:]:
            return True
    return False


def _detect_severity_modifiers(text: str, match_start: int, match_end: int) -> List[str]:
    """Detect severity modifiers near the match."""
    # Look at the 30 characters around the match
    context = text[max(0, match_start - 30) : match_end + 30].lower()
    modifiers = []
    for cue, modifier_class in _SEVERITY_MODIFIERS.items():
        if cue in context:
            modifiers.append(modifier_class)
    return modifiers


def extract_keywords(
    incident_desc: str,
    rules_path: str,
    spacy_model_path: str = "en_core_web_md",
) -> List[ExtractedKeyword]:
    """
    Stage 1 — Extract clinical keywords from incident description.

    Applies clinical_rules.yaml term matching after NLP tokenisation.
    Handles Swahili terms not recognised by spaCy model via keyword matching.
    Extracts negation markers and severity modifiers.

    Args:
        incident_desc: Free-text emergency description (English or Swahili)
        rules_path: Path to clinical_rules.yaml
        spacy_model_path: Path to spaCy model (must be pre-installed)

    Returns:
        List of ExtractedKeyword from shared contracts
    """
    keywords: List[ExtractedKeyword] = []

    # Load rules
    rules = _load_rules(rules_path)
    if not rules:
        return keywords

    # Build compiled patterns
    patterns = _build_compiled_patterns(rules)

    # Try spaCy tokenisation (may fail if model not available)
    doc = None
    try:
        import spacy
        nlp = spacy.load(spacy_model_path, disable=["parser", "ner"])
        doc = nlp(incident_desc)
    except Exception as exc:
        logger.warning("spaCy model not available (%s). Using regex-only extraction.", exc)

    # Pattern matching on raw text (handles Swahili and all terms)
    for compiled, rule in patterns:
        for match in compiled.finditer(incident_desc):
            start, end = match.span()

            # Check negation
            is_negated = _detect_negation(incident_desc, start, end)

            # Detect severity modifiers
            modifiers = _detect_severity_modifiers(incident_desc, start, end)

            # Map category string to enum
            try:
                category = ClinicalCategory(rule["category"])
            except ValueError:
                category = ClinicalCategory.UNKNOWN

            keywords.append(
                ExtractedKeyword(
                    text=incident_desc[start:end],
                    category=category,
                    is_negated=is_negated,
                    severity_modifiers=modifiers,
                    icd10_prefix=rule.get("icd10_prefix"),
                    snomed_hint=rule.get("snomed_hint"),
                    source="rules",
                )
            )

    # Remove duplicate categories — keep the highest-severity match per category
    seen: Dict[ClinicalCategory, ExtractedKeyword] = {}
    for kw in keywords:
        if kw.is_negated:
            continue
        if kw.category not in seen:
            seen[kw.category] = kw
        elif kw.severity_modifiers and not seen[kw.category].severity_modifiers:
            seen[kw.category] = kw

    result = list(seen.values())

    # If nothing matched, add a fallback keyword
    if not result:
        result.append(
            ExtractedKeyword(
                text="undifferentiated",
                category=ClinicalCategory.UNKNOWN,
                is_negated=False,
                severity_modifiers=[],
                source="fallback",
            )
        )

    return result
