# CDSS Stack: LanceDB + PageIndex + Postgres
## Tool Selection, Memory Architecture & Development Plan
**Date:** 30 May 2026 | **Status:** Active development, 50 open issues, foundation partially broken

---

## Part 1: Why These Three. Exactly.

Before assigning roles, the right question is: what problems actually exist in this system that need infrastructure to solve? There are four:

1. **Retrieval** — finding the right passage from ~6 disease guidelines when a clinician asks a question
2. **Reasoning** — navigating complex, hierarchically-structured guideline documents for structured clinical questions
3. **Persistence** — storing clinical relationships, audit trails, session state, and evidence that must survive restarts
4. **Memory** — maintaining context across turns within a session, and accumulating validated clinical knowledge across sessions

Each tool owns exactly one of these problems. None of them overlap. This is the architecture.

---

## Part 2: Tool Roles — No Ambiguity, No Overlap

### LanceDB: Retrieval only
LanceDB's job in this system is exactly what it does today: store embedded guideline chunks and answer approximate-nearest-neighbor queries against them. It does this well. It is serverless, embedded, zero-operational-overhead, and already integrated.

**What it handles:**
- Dense vector search across all 6 disease tables (`hiv_guidelines`, `diabetes_guidelines`, etc.)
- BM25 full-text search fallback (via `create_fts_index`)
- Hybrid reranking via the BGE cross-encoder already wired in `search_tools.py`
- HyDE query expansion (already implemented, just has the wrong endpoint — fix Issue 5)
- The `_search_guideline_table` method and the `SearchIndex` singleton

**What it does NOT do:**
- Store relationships, edges, or structured clinical logic — that is Postgres
- Handle structured document reasoning — that is PageIndex
- Manage session history or cross-session memory — that is Postgres

**Nothing changes in LanceDB's role.** It is correctly placed already.

---

### PageIndex: Structured reasoning over guideline documents
PageIndex is a vectorless, reasoning-based RAG system that builds a hierarchical tree index from documents and uses an LLM to navigate that tree. It achieves 98.7% accuracy on FinanceBench for structured documents. Kenya clinical guidelines are exactly this kind of document: they have explicit chapter → section → subsection → table hierarchies, numbered algorithms, dosing tables, and diagnostic criteria that cross-reference each other.

**The problem it solves that LanceDB cannot:**
LanceDB's vector search retrieves by semantic similarity. A query like "What is the weight-based dosing of rifampicin for a 22kg child with drug-susceptible TB?" is not a similarity problem — it is a navigation problem. The answer is in a specific table on a specific page inside a specific section. LanceDB will retrieve chunks that are *semantically near* this question, which may or may not contain the actual table. PageIndex navigates to the table directly.

**What it handles:**
- Complex structured queries: dosing tables, diagnostic algorithms, monitoring schedules
- Questions that require reading a specific section in full, not a retrieved fragment
- Questions where the answer depends on document structure: "What is listed under Step 3 of the TB treatment algorithm?"
- Queries where LanceDB's retrieved chunks consistently score low or trigger HITL clarification markers

**What it does NOT do:**
- Replace LanceDB for broad, exploratory queries ("What does the HIV guideline say about ART initiation?")
- Handle session memory or cross-session state
- Store anything — it is stateless and reads directly from the source PDFs

**Critical practical constraint:** PageIndex makes LLM calls on every retrieval step. This is a real cost. Every query routed to PageIndex consumes LLM tokens for tree navigation in addition to the final answer generation. At the query volumes of a busy clinic — 200-500 queries per day — this is manageable but must be tracked. Do not route every query to PageIndex. Route only queries where structure matters.

**Query routing logic (in `search_agent.py`):**

```python
STRUCTURED_QUERY_SIGNALS = [
    "dosing", "dose", "mg/kg", "weight-based",
    "algorithm", "step", "stage", "table",
    "monitoring schedule", "frequency", "interval",
    "criteria for", "definition of",
    "first-line", "second-line", "regimen",
    "contraindicated", "interaction",
]

def should_use_pageindex(query: str, context: Optional[PatientContext]) -> bool:
    """
    Route to PageIndex when the query requires structural navigation.
    Route to LanceDB when the query is exploratory or cross-document.
    """
    q = query.lower()
    has_structural_signal = any(signal in q for signal in STRUCTURED_QUERY_SIGNALS)
    has_specific_params = context and any(
        context.clinical_params.get(k) for k in ["weight", "cd4_count", "egfr", "hba1c"]
    )
    # Cross-disease queries always go to LanceDB (PageIndex operates on one doc at a time)
    has_comorbidity = context and context.comorbidity not in (None, "", "None")
    if has_comorbidity:
        return False
    return has_structural_signal or bool(has_specific_params)
```

---

### Postgres: Everything that must be durable, queryable, and correct

Postgres is the system's source of truth for all structured, persistent state. It replaces SQLite for the audit log (already specified in the previous plan), and hosts the evidence graph and session memory tables.

**What it handles:**
- `audit_logs` — every query, retrieval, response, tool call, feedback event
- `clinical_edges` — the typed evidence graph (contraindications, first-line regimens, monitoring requirements)
- `concepts` — canonical clinical concept registry
- `session_memory` — persistent cross-session clinical memory (explained fully in Part 3)
- `guidelines` — guideline registry with version tracking

**What it does NOT do:**
- Cache query results or speed up repeated lookups — that is application-layer logic
- Store embeddings or vectors — that is LanceDB
- Navigate document structure — that is PageIndex

---

## Part 3: Memory — The Real Architecture

This is the most underspecified part of clinical AI and the part most systems get wrong. Let me be exact about what "memory" means in this context and what each level requires.

### The clinical setting

This system will be used by:
- A clinician at a Level 4-6 hospital or large clinic
- Querying about a specific patient in a specific encounter
- Possibly returning to the same patient's case across multiple sessions (follow-up visits)
- In a shared workstation environment where multiple clinicians may use the same system

This is not a chatbot. It is a clinical decision support tool. Memory architecture must reflect that.

### Four memory levels

**Level 1: Conversation turn context (in-memory, ephemeral)**

This already exists. `_session_history` in `api.py` is a `Dict[str, deque]` that holds the last 20 messages per session. It lives in process memory and dies on restart.

This is correct and sufficient for within-session multi-turn conversation. A clinician asks a follow-up question ("What about if the patient also has hepatitis B?") and the agent needs to know what was discussed two turns ago. The `deque(maxlen=20)` handles this. Do not over-engineer it.

**Current bug:** The Mistral pydantic-ai path passes `message_history=history` correctly. The Groq/Puter path does NOT — it builds a single-turn request and discards history. Fix this by prepending the last N messages from the session history deque into the `messages` array in `_run_openai_compatible_chat`.

**Level 2: Session patient context (in-memory + request-scoped)**

This already exists as `PatientContext` passed on each request. The clinician's selected patient type, condition, comorbidity, and clinical parameters are injected into the query as a `[PATIENT_CONTEXT: ...]` block. This is correct.

**Current bug:** `has_context` only checks that some fields are non-empty, but the context payload is serialised to JSON and prepended as a raw string block. This means the agent never receives structured context it can reason about — it receives a string containing a JSON dump. The better approach is to inject context as a structured part of the system message or as a dedicated tool result, not prepended to the user message. This is a medium-priority fix.

**Level 3: Cross-session patient case memory (Postgres, persistent)**

This does not exist yet. This is the important one.

A clinician sees a patient with HIV + TB comorbidity today. They prescribe a regimen. Three months later, the same patient returns. The clinician opens the CDSS and queries about viral load monitoring. The system currently has no memory of the previous encounter. It starts fresh. This is a patient safety gap.

This is not a framework problem. It does not require Cognee, Letta, or any memory management library. It requires a well-designed Postgres table and a retrieval pattern.

```sql
CREATE TABLE session_memory (
    memory_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     TEXT NOT NULL,           -- the session that created this memory
    patient_ref    TEXT,                    -- anonymised patient identifier (hash or MRN)
    disease_scope  TEXT NOT NULL,           -- "hiv" | "tb" | "hiv_tb" (comorbid)
    memory_type    TEXT NOT NULL,           -- "active_regimen" | "lab_result" |
                                            -- "clinical_decision" | "contraindication_noted" |
                                            -- "monitoring_due" | "follow_up_required"
    content        TEXT NOT NULL,           -- human-readable summary of the memory
    structured_data JSONB,                  -- machine-readable structured content
    source_query   TEXT,                    -- the query that generated this memory
    clinician_role TEXT,                    -- role of the clinician who created it
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMPTZ,             -- NULL = permanent; set for time-limited memories
    review_status  TEXT DEFAULT 'active',   -- "active" | "superseded" | "discharged"
    superseded_by  UUID REFERENCES session_memory(memory_id)
);

CREATE INDEX idx_memory_patient   ON session_memory(patient_ref);
CREATE INDEX idx_memory_disease   ON session_memory(disease_scope);
CREATE INDEX idx_memory_type      ON session_memory(memory_type);
CREATE INDEX idx_memory_expires   ON session_memory(expires_at)
    WHERE expires_at IS NOT NULL;
```

**How it works in practice:**

At the end of every clinician-LLM exchange, the response is analysed (a lightweight, cheap LLM call — not the expensive reasoning model) to extract structured clinical facts. These are written to `session_memory` if they meet a confidence threshold.

Examples of what gets written:
- "Patient prescribed TDF/DTG/3TC first-line ART, treatment-naive adult" → `memory_type: active_regimen`
- "CD4 count 187 cells/mm³ on 2026-04-15" → `memory_type: lab_result`
- "Rifampicin contraindicated noted due to drug interaction" → `memory_type: contraindication_noted`
- "Viral load monitoring due in 6 months" → `memory_type: monitoring_due`

On subsequent sessions for the same patient (identified by anonymised patient reference), relevant memories are retrieved and injected into the system prompt as structured context before the query is answered.

**Critical design constraints:**
- `patient_ref` must be an anonymised identifier, never a name or MRN in plaintext. Use `SHA-256(MRN + site_salt)` where `site_salt` is a per-deployment environment variable.
- Memory extraction runs on a cheap, fast model (Groq's smaller model or Mistral Small) — never the expensive reasoning model.
- Memory items have a `review_status` field. When a new regimen is prescribed, the old one is marked `superseded`. This handles the case where the patient stops Lisinopril and restarts — both records exist, the old one is marked superseded, the new one is active.
- `expires_at` is used for time-limited memories: "viral load monitoring due in 6 months" expires after 6 months.
- No memory is served to the LLM unless `review_status = 'active'`.

**Level 4: System-wide clinical knowledge accumulation (Postgres evidence graph)**

This is the typed evidence graph described in the first plan. It is not patient-specific — it is the curated, validated store of clinical relationships that makes every query better. It is written by humans (or validated by humans) and never by raw LLM output.

This is already fully specified. Do not rebuild it.

---

## Part 4: PageIndex Integration — Exact Implementation

### Where it plugs in

PageIndex integrates as a **fourth agent tool** in `search_agent.py`, alongside `search_guidelines`, `get_section`, and `lookup_kb`. The agent decides which tool to call based on the query signal analysis above. PageIndex is never called automatically — it is called when the agent judges that structural navigation is needed.

### Setup

```bash
pip install pageindex --break-system-packages
# Or:
uv add pageindex
```

### Indexing (run once per guideline, at ingestion time)

```python
# app/pageindex_store.py
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
import pageindex

DOCS_DIR = Path(__file__).resolve().parent / "docs"
INDEX_DIR = Path(__file__).resolve().parent / "pageindex_indexes"
INDEX_DIR.mkdir(exist_ok=True)

DISEASE_PDF_MAP = {
    "hiv":          "HIV-AIDS/Kenya-ARV-Guidelines-2022-Final-1.pdf",
    "diabetes":     "Diabetes Mellitus/Kenya-DM-Guidelines-V15-2024.pdf",
    "cvd":          "Cardiovascular Disease/Kenya-CVD-Guidelines-2024.pdf",
    "tb":           "TB/Kenya-TB-Guidelines-Oct-2025.pdf",
    "malaria":      "Malaria/Kenya-Malaria-Guidelines-2016.pdf",
    "mental_health":"Mental Health/Kenya-Mental-Health-Guidelines.pdf",
}

def get_index_path(disease: str) -> Path:
    return INDEX_DIR / f"{disease}_pageindex"

def build_pageindex(disease: str, force: bool = False) -> None:
    """Build a PageIndex tree for one disease guideline. Run at ingestion time."""
    pdf_rel = DISEASE_PDF_MAP.get(disease)
    if not pdf_rel:
        raise ValueError(f"No PDF mapping for disease: {disease}")
    pdf_path = DOCS_DIR / pdf_rel
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    index_path = get_index_path(disease)
    if index_path.exists() and not force:
        return  # Already built
    index = pageindex.build(str(pdf_path))
    index.save(str(index_path))

def load_pageindex(disease: str) -> Optional[pageindex.Index]:
    """Load a pre-built PageIndex for a disease. Returns None if not built."""
    index_path = get_index_path(disease)
    if not index_path.exists():
        return None
    return pageindex.load(str(index_path))

def query_pageindex(disease: str, query: str, llm_api_key: str) -> Optional[str]:
    """
    Run a structured query against a disease's PageIndex.
    Returns extracted text or None if index not available.
    Uses cheap model for tree navigation — not the expensive reasoning model.
    """
    index = load_pageindex(disease)
    if not index is None:
        return None
    result = index.query(
        query=query,
        llm_api_key=llm_api_key,
        model="mistral-small-latest",  # Cheap navigation model
        max_tokens=512,
    )
    return result.text if result else None
```

### Agent tool registration

```python
# In search_agent.py, inside build_agent():

@agent.tool
async def query_structured_guideline(
    ctx: RunContext[SearchDeps],
    query: str,
    disease: str,
) -> Optional[Dict[str, Any]]:
    """
    Navigate a guideline document's structure directly to find a specific
    section, table, dosing chart, or algorithm step.

    Use this when:
    - The query asks for a specific dosing table, weight-based chart, or algorithm step.
    - The query asks for a numbered criterion or diagnostic threshold.
    - Vector search results have been weak or have triggered HITL flags.
    - The query requires reading an entire section, not a retrieved fragment.

    Do NOT use for broad, exploratory, or cross-disease queries.
    """
    from .pageindex_store import query_pageindex

    api_key = os.getenv("MISTRAL_API_KEY") or os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return None

    text = await asyncio.to_thread(query_pageindex, disease, query, api_key)
    if not text:
        return None

    return {
        "text": text,
        "source": f"{DISEASE_CONFIG.get(disease, {}).get('guideline_name', disease.upper() + ' Guidelines')} [structured navigation]",
        "disease": disease,
        "retrieval_method": "pageindex",
        "low_confidence": False,
    }
```

### Ingestion integration

Add a `build_all_pageindexes()` call to `ingest.py` at the end of each disease's ingestion run:

```python
from .pageindex_store import build_pageindex

# At the end of ingest_disease():
try:
    build_pageindex(disease, force=False)
    logger.info("PageIndex built for %s", disease)
except Exception as e:
    logger.warning("PageIndex build failed for %s: %s — continuing without it", disease, e)
```

PageIndex failure is non-fatal. The system degrades to LanceDB-only retrieval if a PageIndex is not available.

---

## Part 5: Postgres Migration — Complete Steps

This picks up exactly where the previous plan left off. The previous plan gave you the `logs.py` rewrite, the `api.py` lifespan update, and the `dev_reset.py` script. This extends that with the full schema including memory tables, and the exact migration sequence.

### Full Postgres schema (run once, on first startup)

```sql
-- audit_logs (migrated from SQLite)
CREATE TABLE IF NOT EXISTS audit_logs (
    id             BIGSERIAL PRIMARY KEY,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type     TEXT NOT NULL,
    session_id     TEXT,
    query_id       TEXT,
    disease        TEXT,
    feedback_type  TEXT,
    log_data       JSONB
);
CREATE INDEX IF NOT EXISTS idx_audit_session  ON audit_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_event    ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_ts       ON audit_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_disease  ON audit_logs(disease);

-- session_memory (new — cross-session clinical memory)
CREATE TABLE IF NOT EXISTS session_memory (
    memory_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     TEXT NOT NULL,
    patient_ref    TEXT,
    disease_scope  TEXT NOT NULL,
    memory_type    TEXT NOT NULL,
    content        TEXT NOT NULL,
    structured_data JSONB,
    source_query   TEXT,
    clinician_role TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMPTZ,
    review_status  TEXT NOT NULL DEFAULT 'active',
    superseded_by  UUID REFERENCES session_memory(memory_id)
);
CREATE INDEX IF NOT EXISTS idx_mem_patient  ON session_memory(patient_ref);
CREATE INDEX IF NOT EXISTS idx_mem_disease  ON session_memory(disease_scope);
CREATE INDEX IF NOT EXISTS idx_mem_type     ON session_memory(memory_type);
CREATE INDEX IF NOT EXISTS idx_mem_status   ON session_memory(review_status);
CREATE INDEX IF NOT EXISTS idx_mem_expires  ON session_memory(expires_at)
    WHERE expires_at IS NOT NULL;

-- concepts (evidence graph nodes)
CREATE TABLE IF NOT EXISTS concepts (
    concept_id    TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    concept_type  TEXT NOT NULL,
    disease_scope TEXT NOT NULL,
    snomed_code   TEXT,
    loinc_code    TEXT,
    atc_code      TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    review_status TEXT DEFAULT 'draft'
);

-- clinical_edges (evidence graph)
CREATE TABLE IF NOT EXISTS clinical_edges (
    edge_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_concept     TEXT NOT NULL REFERENCES concepts(concept_id),
    target_concept     TEXT NOT NULL REFERENCES concepts(concept_id),
    relation_type      TEXT NOT NULL,
    guideline_id       TEXT NOT NULL,
    section_id         TEXT,
    section_title      TEXT,
    page_number        INTEGER,
    guideline_version  TEXT NOT NULL,
    guideline_year     INTEGER NOT NULL,
    source_url         TEXT,
    population_scope   TEXT,
    age_min_years      REAL,
    age_max_years      REAL,
    condition_scope    TEXT,
    severity_scope     TEXT,
    comorbidity_scope  TEXT,
    temporal_qualifier TEXT,
    lab_condition      TEXT,
    confidence_level   TEXT DEFAULT 'guideline',
    review_status      TEXT DEFAULT 'draft',
    jurisdiction       TEXT DEFAULT 'KEN',
    local_override     BOOLEAN DEFAULT FALSE,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    created_by         TEXT DEFAULT 'extraction_pipeline',
    reviewed_by        TEXT,
    reviewed_at        TIMESTAMPTZ,
    notes              TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_source    ON clinical_edges(source_concept);
CREATE INDEX IF NOT EXISTS idx_edges_target    ON clinical_edges(target_concept);
CREATE INDEX IF NOT EXISTS idx_edges_relation  ON clinical_edges(relation_type);
CREATE INDEX IF NOT EXISTS idx_edges_guideline ON clinical_edges(guideline_id);
CREATE INDEX IF NOT EXISTS idx_edges_review    ON clinical_edges(review_status);

-- guidelines registry
CREATE TABLE IF NOT EXISTS guidelines (
    guideline_id       TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    disease_scope      TEXT NOT NULL,
    version_label      TEXT,
    year               INTEGER,
    source_url         TEXT,
    local_path         TEXT,
    indexed_at         TIMESTAMPTZ,
    chunk_count        INTEGER,
    extraction_quality TEXT,
    is_active          BOOLEAN DEFAULT TRUE,
    guideline_warning  TEXT
);
```

### Migration sequence from here (pick up from previous plan Step 7)

**Step 8 — Seed the guidelines registry from DISEASE_CONFIG**

```python
# scripts/seed_guidelines.py
import asyncio, asyncpg, os
from dotenv import load_dotenv; load_dotenv("app/.env")
from app.config import DISEASE_CONFIG

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    for d_id, cfg in DISEASE_CONFIG.items():
        await conn.execute("""
            INSERT INTO guidelines (guideline_id, display_name, disease_scope, source_url, guideline_warning, is_active)
            VALUES ($1, $2, $3, $4, $5, TRUE)
            ON CONFLICT (guideline_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    source_url = EXCLUDED.source_url,
                    guideline_warning = EXCLUDED.guideline_warning
        """,
        f"KEN_{d_id.upper()}",
        cfg["display_name"],
        d_id,
        cfg.get("source_url", ""),
        cfg.get("guideline_warning"),
        )
    await conn.close()
    print("Guidelines seeded.")

asyncio.run(main())
```

**Step 9 — Add memory extraction to the chat stream**

In `api.py`, after the agent response is complete in `run_stream()`, add a lightweight memory extraction call:

```python
# After full_text is assembled and before log_response():
if request.context and request.context.model_dump().get("patient_type") not in (None, "Select..."):
    asyncio.create_task(
        _extract_and_store_memory(
            session_id=request.session_id,
            query=request.message,
            response=full_text,
            context=request.context,
            patient_ref=_hash_patient_ref(request.context),
        )
    )
```

```python
# In api.py — memory extraction function
import hashlib

def _hash_patient_ref(context: PatientContext) -> Optional[str]:
    """
    Derive an anonymised patient reference from context.
    In a real deployment this would hash an MRN or encounter ID.
    For now, hash the combination of condition + comorbidity as a proxy.
    """
    key_fields = {
        "condition": context.condition,
        "patient_type": context.patient_type,
    }
    site_salt = os.getenv("CDSS_PATIENT_SALT", "dev-salt-change-in-production")
    payload = json.dumps(key_fields, sort_keys=True) + site_salt
    return hashlib.sha256(payload.encode()).hexdigest()[:32]

async def _extract_and_store_memory(
    session_id: str,
    query: str,
    response: str,
    context: PatientContext,
    patient_ref: Optional[str],
) -> None:
    """
    Run lightweight memory extraction on the completed response.
    Uses cheap model. Writes structured facts to session_memory.
    Non-blocking — called as a background task.
    """
    if not patient_ref:
        return

    extraction_prompt = f"""
Extract clinical facts from this CDSS interaction. Return JSON only.

Query: {query}
Response excerpt: {response[:800]}
Patient context: {json.dumps(context.model_dump(exclude_none=True))}

Return a JSON array of objects with these fields:
- memory_type: one of "active_regimen" | "lab_result" | "clinical_decision" |
  "contraindication_noted" | "monitoring_due" | "follow_up_required"
- content: one plain-English sentence summarising the fact
- structured_data: key clinical values as a flat dict (may be empty)
- expires_days: integer days until this memory expires, or null for permanent

Return [] if no extractable clinical facts are present. JSON only, no prose.
"""
    try:
        provider = get_llm_provider()
        # Always use the cheapest available model for extraction
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider == "groq":
                endpoint = "https://api.groq.com/openai/v1/chat/completions"
                model = "llama-3.1-8b-instant"
            elif provider == "puter":
                endpoint = "https://api.puter.com/puterai/openai/v1/chat/completions"
                model = "gpt-4o-mini"
            else:
                endpoint = "https://api.mistral.ai/v1/chat/completions"
                model = "mistral-small-latest"

            res = await client.post(
                endpoint,
                headers={**provider_auth_header(provider), "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": extraction_prompt}],
                    "max_tokens": 400,
                    "temperature": 0.0,
                },
            )
            if res.status_code != 200:
                return
            raw = res.json()["choices"][0]["message"]["content"]
            raw = raw.strip().lstrip("```json").rstrip("```").strip()
            facts = json.loads(raw)
            if not isinstance(facts, list):
                return
    except Exception as e:
        logger.warning("Memory extraction failed: %s", e)
        return

    from .logs import _pool
    if not _pool:
        return

    async with _pool.acquire() as conn:
        for fact in facts:
            if not isinstance(fact, dict) or "content" not in fact:
                continue
            expires_days = fact.get("expires_days")
            expires_at = None
            if isinstance(expires_days, int) and expires_days > 0:
                from datetime import timedelta
                expires_at = datetime.utcnow() + timedelta(days=expires_days)
            await conn.execute("""
                INSERT INTO session_memory
                (session_id, patient_ref, disease_scope, memory_type, content,
                 structured_data, source_query, clinician_role)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            session_id,
            patient_ref,
            context.condition or "unknown",
            fact.get("memory_type", "clinical_decision"),
            fact["content"],
            json.dumps(fact.get("structured_data", {})),
            query,
            "CLINICIAN",
            )
```

**Step 10 — Retrieve memories at query time**

Add a memory retrieval step inside `run_stream()`, before building `full_message`:

```python
# Retrieve relevant memories for this patient
patient_ref = _hash_patient_ref(request.context) if request.context else None
prior_memories = []
if patient_ref and _pool:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT memory_type, content, structured_data, created_at
            FROM session_memory
            WHERE patient_ref = $1
              AND review_status = 'active'
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY created_at DESC
            LIMIT 8
        """, patient_ref)
        prior_memories = [dict(r) for r in rows]

if prior_memories:
    memory_block = "\n".join(
        f"- [{m['memory_type']}] {m['content']}" for m in prior_memories
    )
    full_message = f"[PRIOR_CLINICAL_CONTEXT:\n{memory_block}\n]\n{full_message}"
```

---

## Part 6: Exact Development Sequence — Where to Pick Up

The previous plan specified two sequences: 50-issue remediation (Tiers 0-4) and Phase 3 work. This plan adds a third sequence on top, beginning only after Tier 0-1 issues are closed.

### Gate 0: Foundation must work before anything new (unchanged from previous plan)

Fix in order: Issue 17 (system prompt), Issue 50 (index content), Issue 8 (create_index), Issue 29 (SSE parser).

Then Tier 1: Issues 2, 3, 49, 16/28/47.

Do not touch Postgres migration, PageIndex, or memory until a basic query works end-to-end.

### Gate 1: Postgres migration (previous plan Steps 1–7, now extended)

Complete Steps 1–7 from the previous plan exactly as specified. Then:

**Step 8:** Seed guidelines registry (script above).

**Step 9:** Verify `_pool` is accessible in `api.py` — import from `logs.py`, do not duplicate.

**Step 10:** Add `session_memory` table to the schema initialisation in `logs.py`'s `init_db_pool()`. Run `dev_reset.py` to apply.

### Gate 2: PageIndex integration

Only begin after:
- At least one disease is fully indexed in LanceDB (HIV is the priority — it has the most content)
- End-to-end query succeeds: clinician types question → agent calls `search_guidelines` → response streams to UI
- Issues 5 (HyDE endpoint), 4 (async LanceDB calls) are fixed

Then:
1. Install `pageindex` via `uv add pageindex`
2. Create `app/pageindex_store.py` as above
3. Add PDF path mappings — verify each PDF actually exists in `app/docs/`
4. Run `build_pageindex("hiv")` manually and confirm it completes without error
5. Register `query_structured_guideline` tool in `search_agent.py`
6. Test with 5 structured queries against HIV guideline (dosing tables, algorithm steps)
7. If working, run `build_pageindex()` for all 6 diseases and add to `ingest.py`

### Gate 3: Memory system

Only begin after PageIndex is tested for at least one disease.

1. Add `session_memory` DDL to `init_db_pool()` in `logs.py`
2. Add `_hash_patient_ref()` to `api.py`
3. Add `_extract_and_store_memory()` background task
4. Add memory retrieval block to `run_stream()` before `full_message` is assembled
5. Test: run two queries with the same patient context in two separate sessions; verify memories are retrieved in the second session
6. Verify `patient_ref` hashing: confirm that the same context always produces the same hash, and different contexts produce different hashes
7. Set `CDSS_PATIENT_SALT` to a real random value in `.env` — never use the default in deployment

### Gate 4: Evidence graph seeding (parallel to Gate 3)

Seed the `concepts` and `clinical_edges` tables manually for HIV (highest priority, most content). Use the relation vocabulary and sample edges from the first architecture plan. Mark all edges as `review_status = 'draft'`. Review and validate — change to `review_status = 'validated'` only after clinical review. Register `query_evidence_graph` as the fifth agent tool.

---

## Part 7: New Issues Found in This Read

These are additional issues not in the original 50, found from reading the full source files in this session.

**61. `_run_openai_compatible_chat` discards session history entirely.** The Groq and Puter paths build a two-message conversation (system + user) with no history. The conversation context from `_session_history[session_id]` is loaded but never used in these paths. This means every query to Groq or Puter is answered without any prior conversation context, breaking multi-turn clinical workflows for those providers.

**62. Memory extraction will silently fail if `_pool` is imported before it is initialised.** `_extract_and_store_memory` imports `_pool` from `logs.py` at call time. If called before `init_db_pool()` completes in the lifespan (e.g. a very fast first request), `_pool` is `None` and memory is dropped without error. Add a startup check: set an `_db_ready` flag in `logs.py` after `init_db_pool()` completes, and guard all Postgres writes behind it.

**63. `check_guideline_updates` is called as a one-shot task in the lifespan, not on a timer.** It checks URLs once at startup and never again. The comment says "check every minute" but there is no `asyncio.sleep` loop — unlike `check_llm_reachability` which does have a loop. Either add the loop or remove the task entirely (recommended until URLs are confirmed valid).

**64. `get_llm_model()` for Groq returns `qwen/qwen3-32b` by default.** This model requires `reasoning_format` to be set to get meaningful output. Without it, the model may output its reasoning inline with the answer, which the frontend will display as garbage. Either change the default to `llama-3.3-70b-versatile` (which does not require special formatting) or ensure `GROQ_REASONING_FORMAT=parsed` is set in `.env` for Qwen models.

**65. `_session_history` is a process-level dict — multiple uvicorn workers share nothing.** If the server is run with `--workers 2` (even by accident), two requests from the same session can land on different workers with no shared history. Since this is a development system running single-worker this is low priority, but should be noted for when deployment is hardened. The fix is to move session history to Postgres or a fast key-value store — but not now.

**66. `PatientContext` field `medications` is `Optional[str]` but should be `Optional[List[str]]`.** A clinician might enter multiple active medications. Storing them as a comma-separated string makes the structured_data extraction in memory fragile. Change to `List[str]` with an empty list default and update the frontend context serialisation to match.

**67. `pageindex` tree build is synchronous and CPU-bound.** `build_pageindex()` will block the event loop if called from an async context (e.g. lifespan startup). Wrap in `asyncio.to_thread()` if called from async code, or call it exclusively from the sync `ingest.py` script.

**68. The `CDSS_PATIENT_SALT` environment variable has no validation.** If it is left as the default `"dev-salt-change-in-production"` in a non-development deployment, patient references are computable by anyone who knows the salt. Add a startup assertion: `assert os.getenv("CDSS_PATIENT_SALT") != "dev-salt-change-in-production" or os.getenv("ENVIRONMENT") == "development"`.

---

## Part 8: The Stack, Summarised

| Layer | Tool | Role | Status |
|---|---|---|---|
| Vector retrieval | LanceDB | Broad similarity search across 6 disease tables | In place, bugs being fixed |
| Structured reasoning | PageIndex | Navigate specific sections, tables, algorithms | Not yet integrated |
| Durable persistence | Postgres 16 | Audit, evidence graph, session memory | Migration in progress |
| In-memory session | `deque` in `api.py` | Within-session conversation history | In place, Groq/Puter path broken |
| Cross-session memory | Postgres `session_memory` | Clinical facts across patient encounters | Not yet built |
| Query routing | `should_use_pageindex()` in agent | Decide LanceDB vs PageIndex per query | Not yet built |

No Redis. No OpenMetadata. No memory framework. Three tools, four clear roles, no dead weight.
