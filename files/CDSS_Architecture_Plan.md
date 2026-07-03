# CDSS Architecture Critique, Fact-Check & Implementation Plan
**Date:** 30 May 2026  
**Scope:** Kenya National Clinical Guidelines CDSS — HIV/AIDS, Diabetes Mellitus, CVD, TB, Malaria, Mental Health  
**Status assessed:** Post-Phase 2, Pre-Phase 3 — foundation is largely built; 50 catalogued bugs remain open

---

## Part 1: Critique & Challenge of the READ & REPORT Document

### What the document gets right

The strategic direction is sound. The rejection of premature graph databases, the hybrid retrieval-plus-typed-relations architecture, the insistence on provenance-bearing edges, the staged evolution from retrieval assistant to pathway engine — all of this is clinically and architecturally correct. The document correctly identifies that most "knowledge graph" projects in healthcare fail by visualizing immaturity rather than building computable substance first.

The five goals (regimen navigator, pathway runner, evidence traceability, coverage auditor, patient-state model) are correctly prioritised. The patient-state model observation — that it may be more important than any graph — is especially astute and is the most under-discussed point in the document.

### What the document gets wrong or overstates

**1. "CQL 1.5.3 is the current published version."**  
**Status: Partially outdated.** CQL 1.5.3 remains the current normative published standard (package `hl7.cql#1.5.3`, generated 2025-03-07), and it is an ANSI Normative Standard. However, CQL 2.0.0 is now in active trial-use ballot as a continuous CI build. For new implementations starting in 2026, you should build against 1.5.3 (the stable, normative baseline) while being aware that 2.0.0 is approaching. The document's citation of 1.5.3 is correct but should acknowledge the imminent successor. The source URL `https://cql.hl7.org/` is accurate.

**2. "FHIR CPG methodology represents recommendations as event-condition-action rules using PlanDefinition."**  
**Status: Correct, but version is stale.** The document cites CPG STU1 (`v1.0.0`). The current published version is CPG STU2 (`v2.0.0`), published 2023+ and currently the authoritative release. The URL in the document (`https://www.hl7.org/fhir/uv/cpg/methodology.html`) now resolves to v2.0.0. The ECA-rule-via-PlanDefinition characterisation holds in v2.0.0. The document's framing is correct; the version reference is stale.

**3. "CDS Hooks is the standard for surfacing CDS into workflow contexts."**  
**Status: Correct, but version information is missing.** CDS Hooks 2.0.1 and CDS Hooks Library 1.0.1 were published in early 2025 and are the current stable releases. The source URL in the document (`https://hl7.github.io/cds-hooks-hl7-site/`) is the legacy community site. The authoritative current URL is `https://cds-hooks.hl7.org/` (HL7 published stable) and `https://cds-hooks.org/specification/current/` (CI build). The document should be updated to cite v2.0.1.

**4. "SNOMED CT's Snowstorm and Snowstorm Lite are the terminology server options."**  
**Status: Correct.** Snowstorm (Elasticsearch-backed, enterprise scale) and Snowstorm Lite (lightweight, FHIR-only, fast, small footprint, Docker-deployable) are confirmed as the current SNOMED International options. For this project's scale (6 diseases, Kenya context), Snowstorm Lite is the correct starting point. The document does not note that SNOMED CT membership/licensing is required to use the full release — this is a real operational constraint for a Kenya deployment. SNOMED International does offer free membership to LMIC countries in certain cases; this should be investigated.

**5. "A graph without provenance becomes fiction."**  
**Status: Correct and important.** This is the strongest sentence in the document. It should be repeated at the top of every engineering spec. No edge may enter the graph without a `source_guideline`, `section_id`, `guideline_version`, and `review_status`. This is non-negotiable.

**6. "Detailed official engineering docs from elite hospitals are scarce."**  
**Status: Accurate but incomplete.** The referenced papers (AMPEL at Leipzig, Ochsner sepsis CDS, Inselspital) are real. However, the document presents them as implementation patterns without noting their key architectural lesson: all three built CDS *on top of existing EHR infrastructure* (HL7 v2, FHIR, proprietary), not as standalone systems. This project has no EHR integration surface yet. That limits which patterns actually apply. The value of those references is conceptual validation, not architectural blueprint.

**7. The document frames SNOMED CT as a near-term requirement.**  
**Challenge:** For this system at its current maturity, SNOMED CT integration is a Phase 3-4 concern at earliest, not a prerequisite. Before SNOMED is useful, you need: (a) a stable chunk schema, (b) a working typed relation store, (c) validated clinical content for all 6 diseases. Adding a terminology server before the retrieval pipeline works correctly and the content is indexed is premature. The document hedges on this but should be more explicit: **do not touch terminology services until Phase 2 is complete.**

**8. "Do we need a graph database? Probably not."**  
**Status: Correct for current maturity.** This recommendation is right. A typed relational or document-backed edge store is sufficient through at least Phase 1-2. The document correctly identifies the sequence: typed relation store → graph API semantics → graph-native storage (only if justified by multi-hop traversal needs). This codebase is not at the junction yet.

**9. The document proposes six goals but the codebase has 50 open bugs.**  
**Critical challenge:** The most dangerous thing this document does is discuss Phase 1 evidence graphs while the Phase 0 foundation has 50 catalogued defects — including the system prompt never reaching the model (Issue 17/48), sources never being extracted correctly (Issue 49), and no content indexed in LanceDB (Issue 50). **No Phase 1 work should begin until the 50 open issues are closed.** The document implicitly assumes Phase 0 is done. It is not.

---

## Part 2: Verified Technical Claims Summary

| Claim | Status | Correction if needed |
|---|---|---|
| CQL current version is 1.5.3 | ✅ Correct (normative) | CQL 2.0.0-ballot is in CI — note both |
| FHIR CPG methodology → PlanDefinition + ECA rules | ✅ Correct | Update version citation to STU2 (v2.0.0) |
| CDS Hooks = CDS-into-workflow standard | ✅ Correct | Current stable = v2.0.1 (published early 2025) |
| Snowstorm + Snowstorm Lite = SNOMED server options | ✅ Correct | LMIC licensing must be confirmed for Kenya |
| LanceDB `create_index` needs column name (`vector_column_name=`) | ✅ Confirmed | Issue 8 is a real bug; `vector_column_name="vector"` required in 0.17+ |
| FHIR terminology: CodeSystem, ValueSet, ConceptMap | ✅ Correct | FHIR R4 / R4B terminology service specs are stable |
| Graph DB is premature for current maturity | ✅ Correct | Typed relation store + provenance is sufficient for Phase 1 |

---

## Part 3: What the Codebase Actually Is (Ground Truth)

After reading every file, the system is:

**Working:**
- Disease config schema (`config.py`) — 6 diseases configured, well-structured
- Extraction pipeline (`extractors/`) — Docling → PyMuPDF → PDFPlumber → PyPDF chain exists
- Hierarchical chunker (`chunkers/`) — parent-child structure exists
- `IndexedChunk` schema (`schema.py`) — well-designed, carries provenance fields
- Structured logging (`logs.py`) — SQLite audit trail wired in
- API skeleton (`api.py`) — all endpoints defined, streaming chat implemented
- Agent skeleton (`search_agent.py`) — tools registered, deps injection used
- 50 real bugs catalogued in `issues.txt`

**Broken or not yet functional:**
- System prompt never delivered to model (Issue 17) — **most critical single bug**
- No content indexed in LanceDB — system serves zero clinical content (Issue 50)
- `create_index()` wrong signature — index creation will fail (Issue 8)
- BGE instruction prefix polluting BM25 component (Issue 2)
- Cross-encoder threshold broken (Issue 3)
- SSE parser broken in frontend (Issue 29)
- HITL state never populated from SSE stream (Issue 30)
- Sources extraction from `new_messages()` unverified (Issue 49)
- `PatientContext` schema misaligned between frontend and backend (Issues 16/28/47)
- `localStorage` used where `sessionStorage` required (Issue 26)
- TB and Malaria source URLs are 404s (Issues 20/21)
- CVD and DM source URLs are guesses (Issue 22)
- Agent action log hardcoded, not wired (Issue 31)
- `handleNewConversation` doesn't clear server-side session (Issue 46)

---

## Part 4: Concrete Architecture for the Evidence Graph Layer

### 4.1 Scope Decision

The v1 evidence graph is **not a separate database**. It is a typed edge table stored in SQLite alongside the audit log, with a Python accessor class that exposes graph-semantics queries. It is populated by a curated extraction pipeline, not by LLM output.

This stays in SQLite until there is genuine need for multi-hop traversal or graph analytics. At that point — and only then — migrate to Neo4j or a similar graph-native store.

### 4.2 Evidence Graph Schema

```sql
-- Clinical concept nodes (canonical, normalized)
CREATE TABLE concepts (
    concept_id     TEXT PRIMARY KEY,           -- "HIV_DISEASE" | "TDF_DTG_3TC" | "CD4_COUNT"
    display_name   TEXT NOT NULL,              -- "HIV Disease"
    concept_type   TEXT NOT NULL,              -- "condition"|"drug"|"regimen"|"lab"|"symptom"|"procedure"
    disease_scope  TEXT NOT NULL,              -- "hiv"|"diabetes"|"cvd"|"tb"|"malaria"|"mental_health"
    snomed_code    TEXT,                       -- Optional; populate in Phase 3
    loinc_code     TEXT,                       -- Optional; for labs
    atc_code       TEXT,                       -- Optional; for drugs/regimens
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    review_status  TEXT DEFAULT 'draft'        -- "draft"|"validated"|"deprecated"
);

-- Typed clinical relationship edges
CREATE TABLE clinical_edges (
    edge_id            TEXT PRIMARY KEY,       -- UUID
    source_concept     TEXT NOT NULL REFERENCES concepts(concept_id),
    target_concept     TEXT NOT NULL REFERENCES concepts(concept_id),
    relation_type      TEXT NOT NULL,          -- See relation vocabulary below
    direction          TEXT DEFAULT 'directed',-- "directed"|"bidirectional"

    -- Provenance (mandatory — no edge without this)
    guideline_id       TEXT NOT NULL,          -- "KEN_ARV_2022" | "KEN_DM_2024"
    section_id         TEXT,                   -- chunk_id or parent_id from LanceDB
    section_title      TEXT,
    page_number        INTEGER,
    guideline_version  TEXT NOT NULL,
    guideline_year     INTEGER NOT NULL,
    source_url         TEXT,

    -- Clinical qualifiers (most edges need at least one)
    population_scope   TEXT,                   -- "adult"|"child_u10"|"pregnant"|"adolescent"
    age_min_years      REAL,
    age_max_years      REAL,
    condition_scope    TEXT,                   -- "treatment_naive"|"treatment_experienced"
    severity_scope     TEXT,                   -- "mild"|"moderate"|"severe"
    comorbidity_scope  TEXT,                   -- "hiv_tb"|"hiv_ckd"
    temporal_qualifier TEXT,                   -- "before_treatment"|"after_failure"|"during_followup"
    lab_condition      TEXT,                   -- "cd4_lt_200"|"hba1c_gt_9"

    -- Editorial metadata
    confidence_level   TEXT DEFAULT 'guideline', -- "guideline"|"expert_consensus"|"inferred"
    review_status      TEXT DEFAULT 'draft',      -- "draft"|"validated"|"deprecated"
    jurisdiction       TEXT DEFAULT 'KEN',
    local_override     INTEGER DEFAULT 0,         -- 1 = local formulary override
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by         TEXT DEFAULT 'extraction_pipeline',
    reviewed_by        TEXT,
    reviewed_at        DATETIME,
    notes              TEXT
);

-- Relation type vocabulary (enforced via CHECK constraint)
-- ALTER TABLE clinical_edges ADD CONSTRAINT valid_relation CHECK (
--   relation_type IN (
--     'symptom_suggests_condition',
--     'condition_requires_test',
--     'drug_contraindicated_in_context',
--     'regimen_first_line_for_condition',
--     'regimen_second_line_for_condition',
--     'finding_increases_suspicion_of',
--     'condition_has_complication',
--     'drug_requires_monitoring',
--     'recommendation_applies_to_population',
--     'statement_sourced_from_section',
--     'drug_interaction_with',
--     'condition_contraindicates_drug',
--     'test_confirms_condition',
--     'regimen_requires_lab_before_initiation',
--     'condition_managed_by_regimen',
--     'escalation_after_failure'
--   )
-- );

-- Guideline registry (one row per indexed guideline)
CREATE TABLE guidelines (
    guideline_id       TEXT PRIMARY KEY,       -- "KEN_ARV_2022"
    display_name       TEXT NOT NULL,
    disease_scope      TEXT NOT NULL,
    version_label      TEXT,
    year               INTEGER,
    source_url         TEXT,
    local_path         TEXT,                   -- path to PDF in app/docs/
    indexed_at         DATETIME,
    chunk_count        INTEGER,
    extraction_quality TEXT,                   -- "full"|"degraded"
    is_active          INTEGER DEFAULT 1,
    guideline_warning  TEXT                    -- for outdated guidelines (e.g. Malaria 2016)
);
```

### 4.3 Relation Vocabulary by Disease

**HIV/AIDS (Kenya ARV 2022)**
- `TDF_DTG_3TC` → `FIRST_LINE_HIV_ADULT` via `regimen_first_line_for_condition` [Adult, Treatment-naive]
- `TDF_DTG_3TC` → `PREGNANCY` via `drug_contraindicated_in_context` [First trimester, qualifier: teratogenicity risk for DTG - note: WHO 2021 updated this; Kenya 2022 reflects updated guidance]
- `CD4_LT_200` → `OI_PROPHYLAXIS_REQUIRED` via `finding_increases_suspicion_of`
- `HIV_TB_COINFECTION` → `TB_TREATMENT_FIRST` via `regimen_first_line_for_condition` [With HIV comorbidity]
- `DTG` → `HEPATITIS_B` via `drug_requires_monitoring` [HBV co-infection; TDF covers both]

**Diabetes Mellitus (Kenya DM V15 2024)**
- `TYPE2_DM` → `METFORMIN` via `regimen_first_line_for_condition` [Adult, eGFR >30]
- `METFORMIN` → `CKD_STAGE_4_5` via `drug_contraindicated_in_context` [eGFR <30]
- `HBA1C_GT_7` → `PHARMACOTHERAPY_INTENSIFICATION` via `condition_requires_test`
- `TYPE2_DM_PREGNANCY` → `INSULIN` via `regimen_first_line_for_condition` [Gestational]
- `METFORMIN` → `B12_LEVEL` via `drug_requires_monitoring` [Long-term use]

**Cardiovascular Disease (Kenya CVD 2024)**
- `HYPERTENSION` → `LIFESTYLE_MODIFICATION` via `regimen_first_line_for_condition` [All populations]
- `HYPERTENSION_CKD` → `ACE_INHIBITOR_ARB` via `regimen_first_line_for_condition`
- `STATIN` → `LIVER_FUNCTION_TEST` via `drug_requires_monitoring`
- `HEART_FAILURE` → `ACE_INHIBITOR` via `regimen_first_line_for_condition`
- `PREGNANCY_HYPERTENSION` → `ACE_INHIBITOR` via `drug_contraindicated_in_context`

**Tuberculosis (Kenya TB Guidelines)**
- `DS_TB_ADULT` → `2HRZE_4HR` via `regimen_first_line_for_condition` [Drug-susceptible]
- `MDR_TB` → `BDQLINEZOLID_REGIMEN` via `regimen_first_line_for_condition`
- `TB_HIV` → `ART_WITHIN_2_WEEKS` via `recommendation_applies_to_population`
- `RIFAMPICIN` → `LIVER_FUNCTION_TEST` via `drug_requires_monitoring`
- `RIFAMPICIN` → `ORAL_CONTRACEPTIVES` via `drug_interaction_with`

**Malaria (Kenya Guidelines 2016 — flagged outdated)**
- `UNCOMPLICATED_MALARIA` → `AL_ARTEMETHER_LUMEFANTRINE` via `regimen_first_line_for_condition` [Adult, Child >5kg]
- `SEVERE_MALARIA` → `IV_ARTESUNATE` via `regimen_first_line_for_condition`
- `MALARIA_PREGNANCY_FIRST_TRIMESTER` → `AL` via `drug_contraindicated_in_context` [Quinine preferred T1]
- `SP_IPT` → `PREGNANCY` via `regimen_first_line_for_condition` [Prevention; IPTp]
- ⚠️ All Malaria edges carry `guideline_warning = "2016 guideline; verify against current WHO/KEMRI recommendations"`

**Mental Health (Kenya Common Mental Disorders Guideline)**
- `DEPRESSION_MODERATE_SEVERE` → `SSRI` via `regimen_first_line_for_condition` [Adult]
- `PSYCHOSIS` → `ANTIPSYCHOTIC` via `regimen_first_line_for_condition`
- `SUICIDE_RISK_HIGH` → `EMERGENCY_REFERRAL` via `condition_requires_test`
- `SSRI` → `PREGNANCY` via `drug_requires_monitoring` [Risk-benefit; not absolute CI]
- `SUBSTANCE_USE` → `MOTIVATIONAL_INTERVIEW` via `regimen_first_line_for_condition`

### 4.4 Evidence Graph Accessor Class (Python, SQLite-backed)

```python
# app/evidence_graph.py

from __future__ import annotations
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

GRAPH_DB_PATH = Path(__file__).resolve().parent / "data" / "evidence_graph.db"

@dataclass
class ClinicalEdge:
    edge_id: str
    source_concept: str
    target_concept: str
    relation_type: str
    guideline_id: str
    section_id: Optional[str]
    section_title: Optional[str]
    page_number: Optional[int]
    population_scope: Optional[str]
    temporal_qualifier: Optional[str]
    lab_condition: Optional[str]
    confidence_level: str
    review_status: str
    notes: Optional[str]

class EvidenceGraph:
    """
    Typed relation store for clinical edges.
    Exposes graph-semantics queries over SQLite.
    Not a graph database. Justified upgrade path: once multi-hop 
    traversal across >3 edge types becomes a real query pattern,
    migrate to Neo4j or similar.
    """

    def __init__(self, db_path: Path = GRAPH_DB_PATH):
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS concepts (
                concept_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                concept_type TEXT NOT NULL,
                disease_scope TEXT NOT NULL,
                snomed_code TEXT,
                loinc_code TEXT,
                atc_code TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                review_status TEXT DEFAULT 'draft'
            );
            CREATE TABLE IF NOT EXISTS clinical_edges (
                edge_id TEXT PRIMARY KEY,
                source_concept TEXT NOT NULL,
                target_concept TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                guideline_id TEXT NOT NULL,
                section_id TEXT,
                section_title TEXT,
                page_number INTEGER,
                guideline_version TEXT NOT NULL,
                guideline_year INTEGER NOT NULL,
                source_url TEXT,
                population_scope TEXT,
                age_min_years REAL,
                age_max_years REAL,
                condition_scope TEXT,
                severity_scope TEXT,
                comorbidity_scope TEXT,
                temporal_qualifier TEXT,
                lab_condition TEXT,
                confidence_level TEXT DEFAULT 'guideline',
                review_status TEXT DEFAULT 'draft',
                jurisdiction TEXT DEFAULT 'KEN',
                local_override INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT DEFAULT 'extraction_pipeline',
                reviewed_by TEXT,
                reviewed_at DATETIME,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS guidelines (
                guideline_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                disease_scope TEXT NOT NULL,
                version_label TEXT,
                year INTEGER,
                source_url TEXT,
                local_path TEXT,
                indexed_at DATETIME,
                chunk_count INTEGER,
                extraction_quality TEXT,
                is_active INTEGER DEFAULT 1,
                guideline_warning TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_edges_source ON clinical_edges(source_concept);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON clinical_edges(target_concept);
            CREATE INDEX IF NOT EXISTS idx_edges_relation ON clinical_edges(relation_type);
            CREATE INDEX IF NOT EXISTS idx_edges_disease ON clinical_edges(guideline_id);
            CREATE INDEX IF NOT EXISTS idx_edges_population ON clinical_edges(population_scope);
            CREATE INDEX IF NOT EXISTS idx_edges_review ON clinical_edges(review_status);
        """)
        conn.commit()
        conn.close()

    def get_contraindications(
        self,
        drug_concept: str,
        population: Optional[str] = None,
    ) -> List[ClinicalEdge]:
        """Return all contraindications for a drug, optionally filtered by population."""
        sql = """
            SELECT * FROM clinical_edges
            WHERE source_concept = ?
            AND relation_type IN ('drug_contraindicated_in_context', 'condition_contraindicates_drug')
            AND review_status != 'deprecated'
        """
        params: list = [drug_concept]
        if population:
            sql += " AND (population_scope IS NULL OR population_scope = ?)"
            params.append(population)
        return self._query_edges(sql, params)

    def get_first_line_regimens(
        self,
        condition: str,
        population: Optional[str] = None,
        comorbidity: Optional[str] = None,
    ) -> List[ClinicalEdge]:
        """Return validated first-line regimens for a condition."""
        sql = """
            SELECT * FROM clinical_edges
            WHERE target_concept = ?
            AND relation_type IN ('regimen_first_line_for_condition', 'condition_managed_by_regimen')
            AND review_status = 'validated'
        """
        params: list = [condition]
        if population:
            sql += " AND (population_scope IS NULL OR population_scope = ?)"
            params.append(population)
        if comorbidity:
            sql += " AND (comorbidity_scope IS NULL OR comorbidity_scope LIKE ?)"
            params.append(f"%{comorbidity}%")
        return self._query_edges(sql, params)

    def get_required_monitoring(self, regimen_or_drug: str) -> List[ClinicalEdge]:
        """Return monitoring requirements before or during a regimen."""
        sql = """
            SELECT * FROM clinical_edges
            WHERE source_concept = ?
            AND relation_type IN ('drug_requires_monitoring', 'regimen_requires_lab_before_initiation')
            AND review_status != 'deprecated'
        """
        return self._query_edges(sql, [regimen_or_drug])

    def get_evidence_chain(self, edge_ids: List[str]) -> List[ClinicalEdge]:
        """Return full provenance for a set of edges — used by the explainability panel."""
        placeholders = ",".join("?" * len(edge_ids))
        sql = f"SELECT * FROM clinical_edges WHERE edge_id IN ({placeholders})"
        return self._query_edges(sql, edge_ids)

    def get_disease_coverage(self, disease: str) -> dict:
        """Return coverage stats for admin/auditor use."""
        conn = self._connect()
        try:
            c = conn.cursor()
            c.execute("""
                SELECT relation_type, review_status, COUNT(*) as cnt
                FROM clinical_edges
                WHERE guideline_id LIKE ?
                GROUP BY relation_type, review_status
            """, [f"%{disease.upper()}%"])
            rows = c.fetchall()
            return {f"{r['relation_type']}:{r['review_status']}": r['cnt'] for r in rows}
        finally:
            conn.close()

    def _query_edges(self, sql: str, params: list) -> List[ClinicalEdge]:
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [
                ClinicalEdge(
                    edge_id=r["edge_id"],
                    source_concept=r["source_concept"],
                    target_concept=r["target_concept"],
                    relation_type=r["relation_type"],
                    guideline_id=r["guideline_id"],
                    section_id=r["section_id"],
                    section_title=r["section_title"],
                    page_number=r["page_number"],
                    population_scope=r["population_scope"],
                    temporal_qualifier=r["temporal_qualifier"],
                    lab_condition=r["lab_condition"],
                    confidence_level=r["confidence_level"],
                    review_status=r["review_status"],
                    notes=r["notes"],
                )
                for r in rows
            ]
        finally:
            conn.close()
```

---

## Part 5: Concrete Implementation Plan

### Prerequisite: Close the 50 Open Issues First

**This is not optional.** No evidence graph work, no Phase 1 features, no regimen navigator until the foundation actually functions. The 50 issues in `issues.txt` are prioritised below.

---

### Tier 0 — System-Killing Bugs (Fix before anything else)

These are bugs that make the system produce no output or wrong output for every request.

| # | File | Issue | Fix |
|---|---|---|---|
| 17 | `search_agent.py` | System prompt never delivered to model | Pass `system_prompt=build_system_prompt(...)` at `Agent()` construction; remove attribute assignment |
| 50 | `lancedb/` | No content indexed; all searches return empty | Run `ingest.py` against all 6 disease PDFs in `app/docs/`; validate each index |
| 8 | `ingest.py` | `create_index()` wrong signature for LanceDB 0.17 | Change to `table.create_index("vector", metric="cosine", num_partitions=32, num_sub_vectors=16)` or use `IVF_PQ` config class |
| 29 | `App.jsx` | SSE parser splits on `data:` prefix, not `\n\n` | Rewrite SSE parser: buffer input, split on `\n\n`, extract `data:` field per event, parse individually |

---

### Tier 1 — High-Impact Bugs (Break key features for most queries)

| # | File | Issue | Fix |
|---|---|---|---|
| 2 | `search_tools.py` | BGE instruction prefix pollutes BM25 | Embed query with prefix; pass raw query string to FTS/BM25; use `query_vector` + `query_str` separately |
| 3 | `search_tools.py` | Cross-encoder threshold `< 0.0` incorrect | Remove threshold until calibrated, or apply sigmoid: `1 / (1 + exp(-score))` then threshold at 0.4 |
| 48 | `search_agent.py` | All system-prompt-dependent features broken | Covered by fix to Issue 17; verify after fix |
| 49 | `api.py` | Sources extraction from `new_messages()` unverified | Add explicit test: log `type(part.content)` and value; adjust extraction if string-serialised |
| 16 | `api.py` | `PatientContext` schema misaligned with frontend | Align `PatientContext` Pydantic model: add `active_conditions: List[str]`, `clinical_params: Dict[str, Any]`, `medications: Optional[str]`; update context injection logic |
| 28/47 | `App.jsx` | Frontend sends wrong context shape | After backend model is fixed, update frontend context serialisation to match |

---

### Tier 2 — Important Bugs (Break specific features)

| # | File | Issue | Fix |
|---|---|---|---|
| 1 | `search_tools.py` | `pandas` imported at bottom of file | Move `import pandas as pd` to module top |
| 9 | `logs.py` | `AUDIT_DB_PATH` relative path breaks by working dir | Change to `Path(__file__).resolve().parent / "data" / "audit.db"` |
| 10 | `api.py` | Hardcoded audit path inconsistent with `logs.py` | Import `AUDIT_DB_PATH` from `logs.py`; remove inline redefinition |
| 11 | `api.py` | Mid-function imports | Move to module top |
| 12/13 | `api.py` | HITL marker parsing fragile and broken | Cap `hitl_buffer` at 512 chars (last N only); rewrite extraction to find complete `[HITL:...\]` span across buffer boundary |
| 15 | `api.py` | Reachability check only for Mistral cloud | Check the configured provider endpoint, not hardcoded Mistral URL |
| 20 | `config.py` | TB guideline URL 404 | Update to confirmed NTLD 2021/2025 URL; verify PDF is accessible from `app/docs/TB/` |
| 21 | `config.py` | Malaria URL unconfirmed | Set to the actual PDF path in `app/docs/Malaria/`; add `guideline_warning` for 2016 date |
| 22 | `config.py` | CVD and DM source URLs guessed | Confirm actual MOH portal URLs or set to empty string; use `app/docs/` path for local ingestion |
| 30 | `App.jsx` | HITL state never populated from SSE stream | Add `hitl_prompt` event handling in SSE parser: `if (data.type === 'hitl_prompt') setHitl(data.hitl)` |
| 31 | `App.jsx` | Agent action log hardcoded | Emit `activity` event type from SSE; wire into `agentActions` state |
| 26 | `App.jsx` | `localStorage` used throughout | Replace `localStorage` with `sessionStorage` for all STORAGE_KEYS; patient context must never be stored |
| 34 | `App.jsx` | `low_confidence` checked at wrong level | Change to `data.sources.some(s => s.low_confidence)` |
| 36 | `App.jsx` | Role never sent in health check | Return role from `CDSS_ROLE` env var in `/health` response without requiring header on same request |
| 46 | `App.jsx` | `handleNewConversation` doesn't clear server session | Call `DELETE /sessions/{sessionId}/clear` then generate new UUID |

---

### Tier 3 — Content and Data Bugs

| # | File | Issue | Fix |
|---|---|---|---|
| 23 | `chunkers/hierarchical.py` | Table `section_title` hardcoded as `"Table"` | Extract Docling table caption; fall back to `f"Table, p.{page}"` |
| 24 | `chunkers/hierarchical.py` | `parent_text` is only section heading, not full body | Accumulate all items between section headers; set `parent_text` to accumulated full-section text |
| 25 | `chunkers/hierarchical.py` | `section_number` never populated | Extract section number from Docling output; store in `IndexedChunk.section_number` |
| 7 | `ingest.py` | Async log call inside sync context | Replace with `asyncio.run(log_init(...))` when called from sync context |
| 4 | `search_tools.py` | Sync LanceDB calls inside async `lookup_kb` | Wrap with `asyncio.to_thread()` for production; acceptable in local dev |
| 6 | `search_tools.py` | `get_section()` sync in async context | Wrap with `asyncio.to_thread()` or make async |
| 5 | `search_tools.py` | HyDE makes hardcoded cloud API call | Wire HyDE to same provider/endpoint as agent; or disable until unified |

---

### Tier 4 — Frontend Cleanup (Lower risk, but remove dead code)

| # | File | Issue | Fix |
|---|---|---|---|
| 27 | `App.jsx` | `PatientContextPanel` rendered on all pages | Conditionally render only on Chat and QueryBuilder pages |
| 32 | `App.jsx` | `onCiteClick` does `console.log` only | Scroll to and highlight corresponding source in panel |
| 33 | `App.jsx` | Inline citation HTML not rendered by ReactMarkdown | Add `rehype-raw` plugin or use custom text-node component for citation replacement |
| 35 | `App.jsx` | Input enabled even when `!isInitialized` | Disable input when `isInitialized` is false |
| 38 | `App.jsx` | `GuidelinesBrowserPage` fetches HIV TOC before diseases loaded | Initialize `selectedDisease` to `null`; fetch TOC only on explicit disease selection |
| 39 | `App.jsx` | `handleSend` in `useEffect` without deps | Wrap `handleSend` in `useCallback` with proper deps |
| 40 | `App.jsx` | `HITL_QUESTIONS` dead constant | Remove |
| 41 | `App.jsx` | `DEFAULT_CONTEXT_OPTIONS` dead constant | Remove |
| 42-45 | `package.json` | 4 unused dependencies | Remove `react-router-dom`, `@tanstack/react-query`, `axios`, `lucide-react` |

---

### Phase 3 Work (After all 50 issues closed)

The following are Phase 3 items from the original plan that remain untouched. They are correctly scoped and should be addressed in this order:

**P3.1 — HyDE Query Expansion**
- Wire HyDE to configured provider (not hardcoded Mistral cloud URL — Issue 5)
- A/B test on 50 known queries; only ship if Recall@5 improves >5%
- Toggle per-disease via `DISEASE_CONFIG.use_hyde`; already exists in config
- The per-disease toggle is already implemented and working in `search_tools.py`; the only bug is the hardcoded cloud endpoint

**P3.2 — Guidelines Browser (already partially built)**
- `/guidelines/{disease}/toc` endpoint: exists and works for structured tables; fix legacy TOC for HIV
- Frontend `GuidelinesBrowserPage`: exists; fix Issue 38 (null initialization)

**P3.3 — Role System**
- Already architecturally wired in `api.py` via `X-User-Role` header and `require_admin` dependency
- Fix Issue 36: return role from `CDSS_ROLE` env var in `/health` response

**P3.4 — Performance Benchmarking**
- Cannot benchmark until content is indexed (Issue 50)
- Build 50-query test set per disease from known clinical scenarios
- Instrument each pipeline stage separately

---

## Part 6: Evidence Graph — Workflow Integration Points

### How the evidence graph connects to the existing system (without modifying it)

**Connection point 1: `lookup_kb` tool in `search_agent.py`**
Add a fourth agent tool alongside `search_guidelines`, `get_section`, and `lookup_kb`:

```python
@agent.tool
async def query_evidence_graph(
    ctx: RunContext[SearchDeps],
    query_type: str,         # "contraindications" | "first_line_regimens" | "monitoring"
    concept: str,            # "HIV_DISEASE" | "METFORMIN"
    population: Optional[str] = None,
    comorbidity: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query the typed clinical relationship store for validated edges.
    Use for: contraindication checks, first-line regimen lookup, 
    monitoring requirements, and evidence chain explanation.
    More reliable than free-text retrieval for these specific query types.
    """
    graph = EvidenceGraph()
    if query_type == "contraindications":
        edges = graph.get_contraindications(concept, population)
    elif query_type == "first_line_regimens":
        edges = graph.get_first_line_regimens(concept, population, comorbidity)
    elif query_type == "monitoring":
        edges = graph.get_required_monitoring(concept)
    else:
        return []
    return [
        {
            "source": e.source_concept,
            "target": e.target_concept,
            "relation": e.relation_type,
            "guideline": e.guideline_id,
            "section": e.section_title,
            "page": e.page_number,
            "population": e.population_scope,
            "qualifier": e.temporal_qualifier,
            "confidence": e.confidence_level,
            "review_status": e.review_status,
            "notes": e.notes,
        }
        for e in edges
        if e.review_status == "validated"  # Never serve draft edges to clinicians
    ]
```

**Connection point 2: Coverage auditor endpoint (new admin route)**

```python
@app.get("/admin/graph/coverage")
async def graph_coverage(disease: Optional[str] = None, role: str = Depends(require_admin)):
    graph = EvidenceGraph()
    return {
        "coverage": graph.get_disease_coverage(disease or "all"),
        "total_edges": ...,
        "validated_edges": ...,
        "draft_edges": ...,
        "deprecated_edges": ...,
    }
```

**Connection point 3: `sources` SSE event enriched with graph edges**
When the agent uses `query_evidence_graph`, the result edges should appear in the sources SSE payload with `source_type: "evidence_graph"` (distinct from `source_type: "retrieval"` for LanceDB chunks). The frontend can then render these with different visual treatment — a deliberate, validated edge vs. a retrieved text passage.

---

## Part 7: What Not To Build (Hard Constraints)

These items are explicitly prohibited regardless of how the request is framed:

1. **Do not add LLM-generated edges to the evidence graph.** Every edge must be extractable from the guideline text with a human reviewer verifying it. LLM-extracted-and-auto-published triples are the most dangerous anti-pattern in clinical CDS.

2. **Do not build a graph visualisation as a primary interface.** The document is right on this. If and when graph views are built, they serve one of four specific explainability purposes: "why this recommendation", "why not regimen B", "what evidence drove this answer", "what relationships affected this flag".

3. **Do not introduce SNOMED CT integration before Phase 2 is complete.** The `snomed_code`, `loinc_code`, `atc_code` fields exist in the schema as nullable placeholders. Leave them null until retrieval is solid and all 6 diseases are indexed and validated.

4. **Do not deploy the evidence graph in production with `review_status = 'draft'` edges.** The `query_evidence_graph` tool already enforces `review_status == "validated"`. This filter must be present in every query that surfaces results to clinicians.

5. **Do not build `regimen navigator`, `pathway runner`, or `evidence traceability panel` (Goals 1-3 from the document) before the 50 open issues are closed.** The foundation must work before the next layer is added.

---

## Part 8: Priority Sequence (What to do, in order)

```
1. Fix Issues 17 (system prompt) and 50 (index content) — nothing works without these
2. Fix Issue 8 (create_index signature) — needed before re-indexing
3. Run ingestion against all 6 disease PDFs in app/docs/
4. Fix Issue 29 (SSE parser) — frontend receives no events correctly without this
5. Fix Tier 1 issues (2, 3, 49, 16/28/47)
6. Fix Tier 2 issues in bulk — most are 5-10 line changes
7. Fix Tier 3 content/data issues — affects citation quality
8. Fix Tier 4 frontend cleanup — low risk, removes dead code
9. End-to-end test: 10 queries per disease, verify citation quality
10. Initialize evidence graph schema and seed with validated edges for HIV (highest coverage)
11. Register `query_evidence_graph` as fourth agent tool
12. Seed remaining 5 diseases with validated edges (in parallel, manual review required)
13. Add `/admin/graph/coverage` endpoint
14. Wire evidence graph `source_type` into SSE sources payload and frontend
15. Begin Phase 3 (HyDE A/B test, performance benchmarking, role system)
```

---

## Part 9: The One Thing That Matters Most Right Now

The system prompt is never delivered to the model. This means the agent has no instructions, no citation format, no HITL signals, no disease context, no safety rules, and no response formatting guidance. Everything the system is supposed to do — grounded citations, source attribution, HITL markers, clinical safety — depends on this. It is a one-line fix:

```python
# In search_agent.py, build_agent():

# WRONG (current):
agent = Agent("mistral:mistral-small-latest", deps_type=SearchDeps, name="clinical_cdss_agent")
# ... later ...
agent.system_prompt = build_system_prompt(available_diseases)  # This does nothing

# CORRECT:
agent = Agent(
    "mistral:mistral-small-latest",
    deps_type=SearchDeps,
    system_prompt=build_system_prompt(available_diseases),  # Pass at construction
    name="clinical_cdss_agent",
    defer_model_check=True,
)
```

Fix this. Then index content. Then run a query. The rest follows.
