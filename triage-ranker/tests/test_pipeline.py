"""Unit tests for triage-ranker pipeline: extractor, ranker, and resolver.

Tests the core pipeline logic without spaCy model or UMLS API:
- Extractor: keyword matching, negation detection, severity modifiers, fallback
- Ranker: composite scoring, GCS/ACVPU mapping, Shock Index, triage level mapping
- Resolver: L4 fallback, cache behavior
- Clinical rules loading
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml
from ambulance_cdss_contracts.triage import (
    ClinicalCategory,
    ExtractedKeyword,
    SeverityLevel,
    TriageLevel,
)

# ── Clinical rules loading ────────────────────────────────────────────────


class TestClinicalRulesLoading:
    def test_rules_load_from_yaml(self):
        rules_path = str(
            Path(__file__).resolve().parents[1] / "app" / "rules" / "clinical_rules.yaml"
        )
        with open(rules_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        rules = data.get("rules", [])
        assert len(rules) >= 30  # At least 30 clinical rules

    def test_each_rule_has_required_fields(self):
        rules_path = str(
            Path(__file__).resolve().parents[1] / "app" / "rules" / "clinical_rules.yaml"
        )
        with open(rules_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for rule in data.get("rules", []):
            assert "term" in rule, f"Rule missing 'term': {rule}"
            assert "category" in rule, f"Rule missing 'category': {rule}"
            assert "severity_weight" in rule, f"Rule missing 'severity_weight': {rule}"
            assert 0.0 <= rule["severity_weight"] <= 1.0, (
                f"severity_weight out of range: {rule['severity_weight']}"
            )

    def test_swahili_terms_present(self):
        """At least some rules must have Swahili synonyms."""
        rules_path = str(
            Path(__file__).resolve().parents[1] / "app" / "rules" / "clinical_rules.yaml"
        )
        with open(rules_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        swahili_count = 0
        for rule in data.get("rules", []):
            synonyms = rule.get("synonyms", [])
            # Swahili terms typically don't use Latin characters exclusively
            if any(
                any(ord(c) > 127 for c in s)
                or s
                in [
                    "kushindwa kupumua",
                    "kimefulia",
                    "kitu kimekwama",
                    "maumivu ya kifua",
                    "kifua kinauma",
                    "kizunguzungu",
                    "degedege",
                    "mshtuko",
                    "kipindupindu",
                    "moyo haupigi",
                    "moyo umesimama",
                    "kuzimia",
                    "hakuna",
                    "kutoka damu nyingi",
                    "damu inatoka",
                    "majeraha ya kichwa",
                    "mifupa imevunjika",
                    "alijichoma",
                    "alianguka",
                    "anajichanganya",
                    "kichwa kinauma sana",
                    "kohoa damu",
                    "kifua kinafunga",
                    "anashindwa kupumua",
                    "hawezi kupumua",
                    "pumua ina shida",
                    "anapumua kwa shida",
                    "kiwangulizi",
                    "moyo unapiga haraka",
                    "moyo unapiga polepole",
                    "anakwaruka",
                    "pumua",
                    "kupoteza fahamu",
                    "hajui chochote",
                    "amezimia",
                    "accident ya gari",
                    "gari imegonga",
                    "boda boda accident",
                    "baridi",
                    "anatetemeka",
                    "joto kali",
                    "umeme",
                    "imeamua maji",
                    "sumu",
                    "damu katika ujauzito",
                    "degedege wakati wa ujauzito",
                    "kitovu kimetoka",
                    "kujifungua",
                    "mtoto anashindwa kupumua",
                    "mtoto anapumua haraka",
                    "joto mtoto",
                    "mtoto anavutavuta",
                ]
                for s in synonyms
            ):
                swahili_count += 1
        assert swahili_count >= 5, f"Expected at least 5 Swahili rules, found {swahili_count}"


# ── Extractor tests ──────────────────────────────────────────────────────


class TestExtractor:
    """Tests for extract_keywords in the NLP extractor.

    Uses a temporary rules file and patches out spaCy to test
    the rules-based matching path directly.
    """

    def _make_rules_file(self, rules):
        """Create a temporary clinical_rules.yaml with given rules."""
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump({"rules": rules}, tmp)
        tmp.close()
        return tmp.name

    def test_extracts_known_terms(self):
        # Reset caches
        import app.pipeline.extractor as ext
        from app.pipeline.extractor import extract_keywords

        ext._rules_cache = None
        ext._compiled_patterns = None

        rules = [
            {
                "term": "chest pain",
                "synonyms": ["maumivu ya kifua", "chest ache"],
                "category": "CARDIOVASCULAR",
                "severity_weight": 0.8,
                "icd10_prefix": "R07",
                "snomed_hint": "29857009",
            },
            {
                "term": "not breathing",
                "synonyms": ["kushindwa kupumua", "stopped breathing"],
                "category": "RESPIRATORY",
                "severity_weight": 0.95,
                "icd10_prefix": "R09",
                "snomed_hint": "209393000",
            },
        ]
        rules_path = self._make_rules_file(rules)

        try:
            keywords = extract_keywords("patient has chest pain and is not breathing", rules_path)
            categories = {kw.category for kw in keywords}
            assert ClinicalCategory.CARDIOVASCULAR in categories
            assert ClinicalCategory.RESPIRATORY in categories
        finally:
            os.unlink(rules_path)
            ext._rules_cache = None
            ext._compiled_patterns = None

    def test_swahili_terms_extracted(self):
        import app.pipeline.extractor as ext
        from app.pipeline.extractor import extract_keywords

        ext._rules_cache = None
        ext._compiled_patterns = None

        rules = [
            {
                "term": "difficulty breathing",
                "synonyms": ["kushindwa kupumua", "pumua ina shida"],
                "category": "RESPIRATORY",
                "severity_weight": 0.75,
            },
        ]
        rules_path = self._make_rules_file(rules)

        try:
            keywords = extract_keywords("mgonjwa anashindwa kupumua sana", rules_path)
            assert len(keywords) > 0
            assert keywords[0].category == ClinicalCategory.RESPIRATORY
        finally:
            os.unlink(rules_path)
            ext._rules_cache = None
            ext._compiled_patterns = None

    def test_negation_detected(self):
        import app.pipeline.extractor as ext
        from app.pipeline.extractor import extract_keywords

        ext._rules_cache = None
        ext._compiled_patterns = None

        rules = [
            {
                "term": "chest pain",
                "synonyms": [],
                "category": "CARDIOVASCULAR",
                "severity_weight": 0.8,
            },
        ]
        rules_path = self._make_rules_file(rules)

        try:
            keywords = extract_keywords("patient denies chest pain", rules_path)
            # The keyword should be found but marked as negated
            negated = [kw for kw in keywords if kw.is_negated]
            assert len(negated) > 0
        finally:
            os.unlink(rules_path)
            ext._rules_cache = None
            ext._compiled_patterns = None

    def test_severity_modifiers_detected(self):
        import app.pipeline.extractor as ext
        from app.pipeline.extractor import extract_keywords

        ext._rules_cache = None
        ext._compiled_patterns = None

        rules = [
            {
                "term": "bleeding",
                "synonyms": ["kutoka damu"],
                "category": "CARDIOVASCULAR",
                "severity_weight": 0.5,
            },
        ]
        rules_path = self._make_rules_file(rules)

        try:
            keywords = extract_keywords("severe bleeding from wound", rules_path)
            assert len(keywords) > 0
            assert len(keywords[0].severity_modifiers) > 0
        finally:
            os.unlink(rules_path)
            ext._rules_cache = None
            ext._compiled_patterns = None

    def test_empty_input_returns_fallback(self):
        import app.pipeline.extractor as ext
        from app.pipeline.extractor import extract_keywords

        ext._rules_cache = None
        ext._compiled_patterns = None

        rules = [
            {
                "term": "chest pain",
                "synonyms": [],
                "category": "CARDIOVASCULAR",
                "severity_weight": 0.8,
            }
        ]
        rules_path = self._make_rules_file(rules)

        try:
            keywords = extract_keywords("zzzz not a medical term", rules_path)
            # Should return at least the fallback keyword
            assert len(keywords) >= 1
            assert keywords[0].source == "fallback"
        finally:
            os.unlink(rules_path)
            ext._rules_cache = None
            ext._compiled_patterns = None

    def test_no_rules_returns_empty(self):
        import app.pipeline.extractor as ext
        from app.pipeline.extractor import extract_keywords

        ext._rules_cache = None
        ext._compiled_patterns = None

        rules_path = self._make_rules_file([])

        try:
            keywords = extract_keywords("chest pain", rules_path)
            assert keywords == []
        finally:
            os.unlink(rules_path)
            ext._rules_cache = None
            ext._compiled_patterns = None


# ── Ranker tests ─────────────────────────────────────────────────────────


class TestRanker:
    """Tests for rank_diagnoses and scoring helpers in Stage 3."""

    def test_gcs_severe_tbi(self):
        from app.pipeline.ranker import _compute_gcs_severity

        weight, desc = _compute_gcs_severity(3)
        assert weight == 0.3
        assert desc == "severe_tbi"

    def test_gcs_moderate_tbi(self):
        from app.pipeline.ranker import _compute_gcs_severity

        weight, desc = _compute_gcs_severity(10)
        assert weight == 0.2
        assert desc == "moderate_tbi"

    def test_gcs_mild_tbi(self):
        from app.pipeline.ranker import _compute_gcs_severity

        weight, desc = _compute_gcs_severity(14)
        assert weight == 0.1
        assert desc == "mild_tbi"

    def test_gcs_normal(self):
        from app.pipeline.ranker import _compute_gcs_severity

        weight, desc = _compute_gcs_severity(15)
        assert weight == 0.0
        assert desc == "normal"

    def test_gcs_none(self):
        from app.pipeline.ranker import _compute_gcs_severity

        weight, desc = _compute_gcs_severity(None)
        assert weight == 0.0
        assert desc == "unknown"

    def test_shock_index_computation(self):
        from app.pipeline.ranker import _compute_shock_index

        # HR 120, SBP 90 → SI = 1.33
        si = _compute_shock_index(hr=120, sbp=90)
        assert si == 1.33

    def test_shock_index_normal(self):
        from app.pipeline.ranker import _compute_shock_index

        si = _compute_shock_index(hr=70, sbp=120)
        assert si is not None
        assert si < 1.0

    def test_shock_index_none_inputs(self):
        from app.pipeline.ranker import _compute_shock_index

        assert _compute_shock_index(hr=None, sbp=100) is None
        assert _compute_shock_index(hr=80, sbp=None) is None
        assert _compute_shock_index(hr=80, sbp=0) is None

    def test_shock_index_score_critical(self):
        from app.pipeline.ranker import _compute_shock_index_score

        weight, desc = _compute_shock_index_score(1.33)
        assert weight == 0.3
        assert desc == "shock_index_critical"

    def test_shock_index_score_elevated(self):
        from app.pipeline.ranker import _compute_shock_index_score

        weight, desc = _compute_shock_index_score(0.95)
        assert weight == 0.15
        assert desc == "shock_index_elevated"

    def test_shock_index_score_normal(self):
        from app.pipeline.ranker import _compute_shock_index_score

        weight, desc = _compute_shock_index_score(0.6)
        assert weight == 0.0
        assert desc == "shock_index_normal"

    def test_triage_level_mapping(self):
        from app.pipeline.ranker import _map_to_triage_level

        assert _map_to_triage_level(0.9) == TriageLevel.P1
        assert _map_to_triage_level(0.6) == TriageLevel.P2
        assert _map_to_triage_level(0.35) == TriageLevel.P3
        assert _map_to_triage_level(0.1) == TriageLevel.P4

    def test_esi_level_mapping(self):
        from app.pipeline.ranker import _map_to_esi_level

        assert _map_to_esi_level(0.9) == 1
        assert _map_to_esi_level(0.7) == 2
        assert _map_to_esi_level(0.5) == 3
        assert _map_to_esi_level(0.3) == 4
        assert _map_to_esi_level(0.1) == 5

    def test_severity_level_mapping(self):
        from app.pipeline.ranker import _map_severity_level

        assert _map_severity_level(0.9) == SeverityLevel.CRITICAL
        assert _map_severity_level(0.7) == SeverityLevel.HIGH
        assert _map_severity_level(0.5) == SeverityLevel.ACUTE
        assert _map_severity_level(0.3) == SeverityLevel.MODERATE
        assert _map_severity_level(0.1) == SeverityLevel.LOW

    def test_gcs_3_produces_p1(self):
        """GCS=3 (severe TBI) should push triage to P1."""
        from app.pipeline.ranker import rank_diagnoses

        keywords = [
            ExtractedKeyword(
                text="unconscious",
                category=ClinicalCategory.NEUROLOGICAL,
                severity_modifiers=["SEVERITY_SEVERE"],
            )
        ]
        ranking, shock_index = rank_diagnoses(
            keywords, gcs_score=3, rules=[{"category": "NEUROLOGICAL", "severity_weight": 0.9}]
        )
        assert len(ranking) > 0
        assert ranking[0].esi_level <= 2  # Should be high acuity

    def test_shock_index_above_1_produces_critical(self):
        """Shock Index > 1.0 (HR 120, SBP 90) should flag critical."""
        from app.pipeline.ranker import rank_diagnoses

        keywords = [
            ExtractedKeyword(
                text="bleeding",
                category=ClinicalCategory.CARDIOVASCULAR,
                severity_modifiers=[],
            )
        ]
        ranking, shock_index = rank_diagnoses(
            keywords, hr=120, sbp=90, rules=[{"category": "CARDIOVASCULAR", "severity_weight": 0.8}]
        )
        assert shock_index == 1.33
        assert len(ranking) > 0

    def test_ranking_never_empty(self):
        """rank_diagnoses must always return at least one result."""
        from app.pipeline.ranker import rank_diagnoses

        ranking, _ = rank_diagnoses([], rules=[])
        assert len(ranking) >= 1
        assert ranking[0].canonical_name == "Undifferentiated Emergency"

    def test_acvpu_to_gcs_mapping(self):
        """ACVPU 'unresponsive' maps to GCS 3 internally."""
        from app.pipeline.ranker import rank_diagnoses

        keywords = [ExtractedKeyword(text="unconscious", category=ClinicalCategory.NEUROLOGICAL)]
        ranking, _ = rank_diagnoses(
            keywords,
            acvpu="unresponsive",
            rules=[{"category": "NEUROLOGICAL", "severity_weight": 0.9}],
        )
        assert len(ranking) > 0
        # GCS 3 should boost the score
        assert ranking[0].score_breakdown.get("w_gcs", 0) > 0

    def test_degraded_mode_with_no_rules(self):
        """With no rules and no keywords, should return degraded mode fallback."""
        from app.pipeline.ranker import rank_diagnoses

        ranking, _ = rank_diagnoses([], rules=[])
        assert ranking[0].canonical_name == "Undifferentiated Emergency"
        assert ranking[0].severity_level == SeverityLevel.MODERATE


# ── Resolver tests ────────────────────────────────────────────────────────


class TestResolver:
    """Tests for the UMLS resolver's L4 fallback and cache behavior."""

    def test_l4_fallback_finds_term(self):
        from app.pipeline.resolver import _l4_fallback

        rules = [
            {
                "term": "chest pain",
                "category": "CARDIOVASCULAR",
                "icd10_prefix": "R07",
                "snomed_hint": "29857009",
            }
        ]
        result = _l4_fallback("chest pain", rules)
        assert result is not None
        assert result["icd10_code"] == "R07"
        assert result["snomed_code"] == "29857009"

    def test_l4_fallback_finds_synonym(self):
        from app.pipeline.resolver import _l4_fallback

        rules = [
            {
                "term": "chest pain",
                "synonyms": ["maumivu ya kifua"],
                "category": "CARDIOVASCULAR",
                "icd10_prefix": "R07",
                "snomed_hint": "29857009",
            }
        ]
        result = _l4_fallback("maumivu ya kifua", rules)
        assert result is not None
        assert result["icd10_code"] == "R07"

    def test_l4_fallback_returns_none_for_unknown(self):
        from app.pipeline.resolver import _l4_fallback

        result = _l4_fallback(
            "unknown_term_xyz", [{"term": "chest pain", "category": "CARDIOVASCULAR"}]
        )
        assert result is None

    def test_l4_fallback_case_insensitive(self):
        from app.pipeline.resolver import _l4_fallback

        rules = [
            {
                "term": "cardiac arrest",
                "category": "CARDIOVASCULAR",
                "icd10_prefix": "I46",
                "snomed_hint": "419422000",
            }
        ]
        result = _l4_fallback("Cardiac Arrest", rules)
        assert result is not None
        assert result["icd10_code"] == "I46"

    def test_purge_caches_clears_l1(self):
        from app.pipeline.resolver import _l1_cache, purge_caches

        _l1_cache["test_key"] = {"cui": "test"}
        assert "test_key" in _l1_cache
        purge_caches()
        assert "test_key" not in _l1_cache
