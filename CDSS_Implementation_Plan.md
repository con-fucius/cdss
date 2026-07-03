# CDSS Implementation Plan
**Current state → Operational maturity**
*No fluff. Every item is a concrete code change with a known file target.*

---

## Phase 0 — Foundation Repair
*Nothing in Phase 1+ is attempted until every item here is closed.*

### 0.1 System Prompt Delivery
- `search_agent.py` `build_agent()` — confirm `system_prompt=build_system_prompt(available_diseases)` is passed at `Agent()` construction, not assigned as an attribute post-construction
- `search_agent.py` — call `build_system_prompt()` once and cache the result; do not rebuild on every request
- `api.py` `chat_stream()` — verify `build_agent(available)` receives the live `available_diseases` list, not a stale captured value

### 0.2 Content Indexing
- `ingest.py` — audit every PDF path in `DISEASE_DISEASE_PDF_MAP`; confirm each file exists under `app/docs/`
- `ingest.py` `create_index()` — fix LanceDB 0.17+ signature: pass `vector_column_name="vector"` explicitly; remove any positional-only call pattern
- `ingest.py` — run ingestion for HIV first; confirm `hiv_guidelines` table exists in LanceDB before proceeding to other diseases
- `ingest.py` — run ingestion for all six diseases in sequence: diabetes, cvd, tb, malaria, mental_health
- `ingest.py` — after each disease, log chunk count and extraction quality to stdout; abort and report if chunk count is zero
- `app/docs/` — confirm TB PDF is the October 2025 version; if `nltp.co.ke` URL is inaccessible, use local copy and update `config.py` `source_url`
- `app/docs/` — confirm Malaria PDF is present and readable; add `guideline_warning` display in `/diseases` response

### 0.3 SSE Parser
- `frontend/src/hooks/useChat.js` — verify SSE parser splits on `\n\n`, strips `data:` prefix, and handles multi-line events correctly
- `frontend/src/hooks/useChat.js` — verify `hitl_prompt`, `activity`, `sources`, `reasoning`, `chunk`, `done`, `error`, `stream_end` event types are all handled
- `frontend/src/hooks/useChat.js` — add explicit guard: if `JSON.parse` throws on an event, log the raw string and continue; never let a single malformed event crash the stream

### 0.4 BGE Instruction Prefix
- `search_tools.py` `_search_guideline_table()` — embed query with BGE instruction prefix `"Represent this sentence for searching relevant passages: "` only for vector embedding call
- `search_tools.py` — pass raw `query` (no prefix) to any FTS/BM25 search call; the prefix is already correct in the current code; confirm it is not being passed to `table.search(query, query_type="fts")`

### 0.5 Cross-Encoder Threshold
- `search_tools.py` `_search_guideline_table()` — remove or disable the `score < 0.0` threshold that incorrectly filters cross-encoder results
- `search_tools.py` — cross-encoder raw scores are logits; either apply sigmoid normalisation `1 / (1 + exp(-score))` and threshold at 0.4, or pass all reranked results and let top-k selection handle filtering
- `search_tools.py` `_row_to_chunk()` — for cosine-distance LanceDB results, normalise to similarity with `score = 1.0 - distance`; current `1.0 / (1.0 + distance)` is for L2 distance; confirm which metric the index uses and apply the correct normalisation
- `search_tools.py` `_row_to_chunk()` — update `low_confidence` threshold after normalisation is corrected; recalibrate against known good queries before setting a value

### 0.6 Audit Log Path
- `logs.py` — confirm `AUDIT_DB_PATH` uses `Path(__file__).resolve().parent / "data" / "audit.db"`; not a relative path
- `api.py` `post_feedback()` — remove inline `import sqlite3` and inline `from .logs import AUDIT_DB_PATH`; import at module top
- `api.py` — remove any duplicated `sqlite3.connect()` call inside `post_feedback()`; route all audit writes through `_write_audit_log()` in `logs.py`

### 0.7 PatientContext Schema Alignment
- `api.py` `PatientContext` — add `medications: List[str] = []` (not `Optional[str]`); change from string to list
- `api.py` `PatientContext` — retain existing fields `patient_type`, `condition`, `comorbidity`, `filters`, `active_conditions`, `clinical_params`; do not remove any
- `frontend/src/` context serialisation — confirm frontend sends `medications` as an array, not a comma-separated string
- `api.py` `chat_stream()` — update `context_payload` injection: serialise `clinical_params` as structured key-value pairs in the prepended block, not a raw JSON dump string
- `api.py` `chat_stream()` — inject patient context as a named section in the system message or as a structured prefix block, not appended raw to the user message string

### 0.8 HyDE Endpoint
- `search_tools.py` `_generate_hyde_hypothesis()` — replace hardcoded `https://api.mistral.ai/v1/chat/completions` with provider-aware endpoint using `get_llm_provider()` and `provider_auth_header()` from `api.py`
- `search_tools.py` — move provider resolution helpers (`get_llm_provider`, `provider_auth_header`) to a shared `app/providers.py` module so both `api.py` and `search_tools.py` can import them without circular dependency
- `search_tools.py` `_generate_hyde_hypothesis()` — use `mistral-small-latest` or equivalent cheap model for HyDE; never use the expensive reasoning model

### 0.9 Async Wrappers
- `search_tools.py` `get_section()` — this is a sync method called from the async agent tool; wrap the LanceDB table scan in `asyncio.to_thread()`
- `search_tools.py` `lookup_kb()` — already async; confirm the `table.search().where().to_pandas()` call inside is wrapped in `asyncio.to_thread()`

### 0.10 Source Extraction Verification
- `api.py` `run_stream()` pydantic-ai path — add structured logging of `type(part).__name__` and `part.tool_name` for every message part; run one test query and confirm `ToolReturnPart` is being hit
- `api.py` — confirm `part.content` for `search_guidelines` tool return is a `list` of dicts at runtime; if it is a serialised string, add `json.loads()` before iterating

### 0.11 Guideline URL Validation
- `config.py` — verify HIV `source_url` returns HTTP 200; it is the only one with high confidence
- `config.py` — for TB: test `https://nltp.co.ke/wp-content/uploads/2025/10/Kenya-TB-Guidelines-2025.pdf`; if unreachable, set `source_url` to local PDF path
- `config.py` — for Malaria: confirm 2016 MOH PDF URL; add `guideline_warning` if not already present
- `config.py` — for CVD and DM: test configured URLs; replace with local paths if unreachable; do not leave 404 URLs in production config
- `config.py` — for Mental Health: `source_url` is empty string; set to local PDF path once PDF is confirmed present in `app/docs/Mental Health/`
- `api.py` `check_guideline_updates()` — gate entire function behind `CDSS_CHECK_GUIDELINE_UPDATES=true`; default must be `false`; confirmed this env var already exists but default is unclear in code

### 0.12 End-to-End Smoke Test
- Send one query per disease via `curl` or the frontend; confirm each returns chunks, sources, and a non-empty agent response
- Confirm `audit_logs` table has rows after each query
- Confirm no `[HITL:MISSING_PARAMS]` or empty retrieval on basic first-line treatment questions

---

## Phase 1 — Postgres Migration
*Begin only after Phase 0 smoke test passes for at least HIV.*

### 1.1 Container Setup
- Run `docker run -d --name cdss-postgres -e POSTGRES_USER=cdss_user -e POSTGRES_PASSWORD=cdss_pass -e POSTGRES_DB=cdss_dev -p 5432:5432 -v cdss-pgdata:/var/lib/postgresql/data postgres:16`
- Verify with `docker exec -it cdss-postgres psql -U cdss_user -d cdss_dev -c "SELECT version();"`
- Add `DATABASE_URL=postgresql://cdss_user:cdss_pass@localhost:5432/cdss_dev` to `app/.env`
- Rotate `MISTRAL_API_KEY` immediately if it has been exposed in any committed file; confirm `app/.env` is in `.gitignore`

### 1.2 Dependencies
- Add `asyncpg>=0.30.0` and `psycopg2-binary>=2.9.9` to `pyproject.toml`
- Run `uv sync`
- Confirm `import asyncpg` succeeds in a Python shell inside the venv

### 1.3 Shared Provider Module
- Create `app/providers.py` — move `get_llm_provider()`, `get_llm_model()`, `provider_has_credentials()`, `provider_models_url()`, `provider_auth_header()`, `provider_offline()` from `api.py` into this module
- Update `api.py` to import from `app.providers`
- Update `search_tools.py` `_generate_hyde_hypothesis()` to import from `app.providers` (fixes Phase 0.8)
- Confirm no circular import: `providers.py` imports only `os`

### 1.4 Full Postgres Schema
- Create `scripts/init_db.py` — connects via asyncpg and runs the full DDL:
  - `audit_logs` table with BIGSERIAL PK, JSONB `log_data`, indexes on `session_id`, `event_type`, `timestamp DESC`, `disease`
  - `session_memory` table with UUID PK, `patient_ref TEXT`, `disease_scope`, `memory_type`, `content`, `structured_data JSONB`, `expires_at TIMESTAMPTZ`, `review_status DEFAULT 'active'`, `superseded_by UUID REFERENCES session_memory`; indexes on `patient_ref`, `disease_scope`, `memory_type`, `review_status`, partial index on `expires_at WHERE expires_at IS NOT NULL`
  - `concepts` table with `concept_id TEXT PK`, `display_name`, `concept_type`, `disease_scope`, `snomed_code`, `loinc_code`, `atc_code`, `review_status DEFAULT 'draft'`
  - `clinical_edges` table with UUID PK, FK to `concepts` for source and target, full provenance columns, `review_status DEFAULT 'draft'`, `local_override BOOLEAN DEFAULT FALSE`; indexes on source, target, relation_type, guideline_id, review_status
  - `guidelines` table with `guideline_id TEXT PK`, all metadata columns, `is_active BOOLEAN DEFAULT TRUE`
- Run `python scripts/init_db.py` and confirm all tables created

### 1.5 Rewrite `logs.py`
- Replace all `sqlite3` imports and calls with `asyncpg`
- Add module-level `_pool: Optional[asyncpg.Pool] = None` and `_db_ready: bool = False`
- Add `async def init_db_pool()` — creates pool with `min_size=1, max_size=5`, runs DDL (idempotent CREATE IF NOT EXISTS), sets `_db_ready = True`
- Add `async def close_db_pool()` — calls `await _pool.close()`
- Rewrite `_write_audit_log()` as async — acquires connection from pool, executes parameterised INSERT with `$1..$6`, uses JSONB for `log_data`
- Update all `log_*` functions to call `await _write_audit_log(...)` and guard behind `if not _db_ready: return`
- Remove `init_audit_db()` sync function entirely
- Remove `AUDIT_DB_PATH` export — nothing should reference SQLite path after this
- Keep `print_recent_logs()` as a sync debug helper but rewrite to use `psycopg2` directly (for CLI use outside async context)

### 1.6 Update `api.py` Lifespan
- Import `init_db_pool`, `close_db_pool` from `logs.py`
- In `lifespan()`: call `await init_db_pool()` before `SearchIndex()` initialisation
- In `lifespan()` cleanup: call `await close_db_pool()`
- Remove `import sqlite3` from module top — no longer needed
- Remove `from .logs import AUDIT_DB_PATH` — no longer exists
- Rewrite `post_feedback()` — remove inline sqlite3 block; call `await _write_audit_log("CORRECTION_LOG", ...)` directly
- Rewrite `get_audit_logs()` — replace sqlite3 query with asyncpg parameterised fetch using `$1..$N` placeholders; use `ILIKE` for disease filter; return JSONB `log_data` already parsed

### 1.7 Dev Reset Script
- Create `dev_reset.py` at project root (not inside `app/`)
- Imports: `asyncio`, `asyncpg`, `os`, `shutil`, `pathlib`, `dotenv`
- `reset_postgres()` — connects, executes `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`, closes
- `reset_lancedb()` — `shutil.rmtree("app/lancedb")` then `Path("app/lancedb").mkdir()`
- `reset_pageindex()` — `shutil.rmtree("app/pageindex_indexes", ignore_errors=True)` then recreate
- `main()` — runs all three, prints confirmation, reminds to re-run ingestion
- Run with `uv run python dev_reset.py`

### 1.8 Guidelines Registry Seeding
- Create `scripts/seed_guidelines.py` — iterates `DISEASE_CONFIG`, inserts one row per disease into `guidelines` table using `ON CONFLICT DO UPDATE`
- Run after `init_db.py`; confirm six rows in `guidelines` table

### 1.9 Validation
- Start the API server; confirm no SQLite import errors in startup log
- Send one query; confirm `audit_logs` has a new row in Postgres
- Run `docker exec -it cdss-postgres psql -U cdss_user -d cdss_dev -c "SELECT event_type, session_id FROM audit_logs ORDER BY timestamp DESC LIMIT 5;"`
- Run `dev_reset.py`; confirm all tables are gone and LanceDB directory is empty; re-run ingestion and smoke test

---

## Phase 2 — PageIndex Integration
*Begin only after Phase 1 validation passes.*

### 2.1 Installation
- Add `pageindex` to `pyproject.toml` dependencies
- Run `uv sync`
- Confirm `import pageindex` succeeds

### 2.2 PDF Inventory
- List every PDF under `app/docs/` for all six disease subdirectories
- For each disease, confirm exactly one primary guideline PDF exists and is not password-protected
- Record actual filenames — do not assume they match the disease key; they may have version strings
- Update `DISEASE_PDF_MAP` in `pageindex_store.py` with confirmed paths

### 2.3 `app/pageindex_store.py`
- Create module with:
  - `DISEASE_PDF_MAP: Dict[str, str]` — relative paths from `app/docs/` for all six diseases
  - `INDEX_DIR = Path(__file__).resolve().parent / "pageindex_indexes"`; create on import
  - `get_index_path(disease: str) -> Path`
  - `build_pageindex(disease: str, force: bool = False) -> None` — sync, CPU-bound; checks if index exists before building; raises `FileNotFoundError` if PDF missing; logs build time
  - `load_pageindex(disease: str) -> Optional[Any]` — returns loaded index or None if not built
  - `query_pageindex(disease: str, query: str, api_key: str, model: str) -> Optional[str]` — sync wrapper; returns extracted text or None; catches all exceptions and logs warning

### 2.4 Ingestion Integration
- `ingest.py` — after each disease's LanceDB ingestion completes, call `build_pageindex(disease, force=False)`
- `ingest.py` — wrap in try/except; PageIndex build failure is non-fatal; log warning and continue
- `ingest.py` — log PageIndex index path and whether it was built fresh or already existed

### 2.5 Query Router
- Create `app/query_router.py`
- Define `STRUCTURED_QUERY_SIGNALS: List[str]` — explicit list of terms that indicate structural navigation is needed: `["dosing", "dose", "mg/kg", "weight-based", "algorithm", "step", "stage", "table", "monitoring schedule", "frequency", "interval", "criteria for", "definition of", "first-line", "second-line", "third-line", "regimen", "contraindicated", "interaction", "schedule", "chart", "figure"]`
- `def should_use_pageindex(query: str, context: Optional[PatientContext], disease: Optional[str]) -> bool`:
  - Return `False` if disease is None (cross-disease query; LanceDB handles multi-table fan-out)
  - Return `False` if context has a non-None, non-"None" comorbidity (cross-disease reasoning required)
  - Return `True` if any structural signal present in lowercased query
  - Return `True` if context has a specific numeric clinical parameter (weight, CD4 count, eGFR, HbA1c)
  - Return `False` otherwise

### 2.6 Agent Tool Registration
- `search_agent.py` `build_agent()` — register fifth tool `query_structured_guideline`:
  - Parameters: `query: str`, `disease: str`
  - Calls `should_use_pageindex()` as an internal guard; returns `None` immediately if router says no
  - Resolves API key from `providers.get_llm_provider()` and `providers.provider_auth_header()`
  - Uses cheap model for PageIndex navigation: `mistral-small-latest` or `llama-3.1-8b-instant` (not the expensive reasoning model)
  - Wraps `query_pageindex()` in `asyncio.to_thread()` — it is synchronous and CPU-bound
  - Returns dict with `text`, `source`, `disease`, `retrieval_method: "pageindex"`, `low_confidence: False`
  - Returns `None` if PageIndex not available for that disease — agent falls back to `search_guidelines`
- Update agent system prompt `BASE_PROMPT` — add guidance: "Use `query_structured_guideline` for dosing tables, weight-based charts, diagnostic algorithms, and specific numbered steps. Use `search_guidelines` for broad or exploratory questions."

### 2.7 Build and Test
- Run `build_pageindex("hiv", force=True)` manually from Python shell; confirm it completes without error and index file exists
- Send five structured HIV queries via the API: one dosing table query, one algorithm step query, one monitoring schedule query, one contraindication query, one regimen eligibility query
- Confirm `query_structured_guideline` tool is called (visible in agent activity events in SSE stream)
- Confirm response contains text navigated from the document structure, not a vector-retrieved fragment
- Build PageIndex for remaining five diseases; run two structured queries per disease

---

## Phase 3 — Cross-Session Memory
*Begin only after Phase 2 testing passes for at least HIV and TB.*

### 3.1 Patient Reference Hashing
- Add `CDSS_PATIENT_SALT` to `app/.env` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`; this value must never be the default string
- `api.py` — add startup assertion in `lifespan()`: `assert os.getenv("CDSS_PATIENT_SALT", "dev-salt") != "dev-salt" or os.getenv("ENVIRONMENT") == "development"`
- `api.py` — add `_hash_patient_ref(context: PatientContext) -> Optional[str]`:
  - Extract `patient_type` and `condition` as key fields
  - Concatenate with `CDSS_PATIENT_SALT`
  - Return `hashlib.sha256(payload.encode()).hexdigest()[:32]`
  - Return `None` if both key fields are `None` or `"Select..."`

### 3.2 Memory Extraction
- `api.py` — add `async def _extract_and_store_memory(session_id, query, response, context, patient_ref)`:
  - Guard: return immediately if `patient_ref` is None or `_db_ready` is False
  - Build extraction prompt requesting JSON array of `{memory_type, content, structured_data, expires_days}`
  - Valid `memory_type` values: `"active_regimen"`, `"lab_result"`, `"clinical_decision"`, `"contraindication_noted"`, `"monitoring_due"`, `"follow_up_required"`
  - Call cheapest available model: Groq `llama-3.1-8b-instant`, Puter `gpt-4o-mini`, Mistral `mistral-small-latest`; max_tokens 400, temperature 0.0
  - Strip markdown fences before `json.loads()`; validate result is a list
  - For each valid fact: compute `expires_at` from `expires_days` if non-null; INSERT into `session_memory`
  - Wrap entire function in try/except; log warning on failure; never raise
- `api.py` `run_stream()` — at response completion (after `done` event emitted, before `log_response()`): call `asyncio.create_task(_extract_and_store_memory(...))` — non-blocking, never awaited in the stream path

### 3.3 Memory Retrieval
- `api.py` `run_stream()` — add memory retrieval block before `full_message` is assembled:
  - Compute `patient_ref = _hash_patient_ref(request.context)` if context is present
  - If `patient_ref` and `_db_ready`: acquire pool connection, fetch up to 8 active non-expired memories ordered by `created_at DESC`
  - If memories found: prepend `[PRIOR_CLINICAL_CONTEXT:\n- [type] content\n...]` block to `full_message`
  - If no memories: proceed without block; do not log this as a warning

### 3.4 Memory Supersession
- `api.py` `_extract_and_store_memory()` — before inserting `active_regimen` type memories: check if an existing active memory of the same type exists for this `patient_ref` and `disease_scope`
- If found: UPDATE existing row to `review_status = 'superseded'`, set `superseded_by = new_memory_id`; then insert new row
- This handles the regimen change case: old regimen is preserved in history, new regimen is active

### 3.5 Memory API Endpoints
- `api.py` — add `GET /sessions/{session_id}/memory` (admin-only): returns all active memories for a session's patient reference; used for debugging and clinical audit
- `api.py` — add `DELETE /sessions/{session_id}/memory/{memory_id}` (admin-only): sets `review_status = 'discharged'` on a specific memory; does not delete the row

### 3.6 Validation
- Run two separate sessions with identical patient context (same `patient_type` + `condition`)
- In session 1: query about ART initiation for HIV adult; confirm memory row is written to `session_memory`
- In session 2: query about viral load monitoring; confirm `[PRIOR_CLINICAL_CONTEXT]` block appears in `full_message` (log it); confirm agent response references the prior regimen context
- Confirm `expires_at` is set correctly for `monitoring_due` type memories
- Confirm `superseded_by` chain works: prescribe regimen A, then prescribe regimen B; confirm regimen A row has `review_status = 'superseded'`

---

## Phase 4 — Evidence Graph
*Can run in parallel with Phase 3 after Phase 2 testing passes.*

### 4.1 `app/evidence_graph.py`
- Create module with `EvidenceGraph` class backed by asyncpg pool (imported from `logs._pool`)
- Methods:
  - `async def get_contraindications(drug_concept, population=None) -> List[ClinicalEdge]`
  - `async def get_first_line_regimens(condition, population=None, comorbidity=None) -> List[ClinicalEdge]`
  - `async def get_required_monitoring(regimen_or_drug) -> List[ClinicalEdge]`
  - `async def get_evidence_chain(edge_ids: List[str]) -> List[ClinicalEdge]`
  - `async def get_disease_coverage(disease: str) -> Dict`
- All queries filter `review_status != 'deprecated'`; queries surfaced to clinicians filter `review_status = 'validated'` only
- `ClinicalEdge` dataclass with all fields from schema

### 4.2 Concept Seeding — HIV (manual, validated)
- Create `scripts/seed_concepts_hiv.py` — inserts canonical concepts for HIV domain:
  - Conditions: `HIV_DISEASE`, `HIV_TB_COINFECTION`, `HIV_HBV_COINFECTION`, `HIV_PREGNANCY`
  - Drugs/Regimens: `TDF_DTG_3TC`, `TDF_3TC_EFV`, `AZT_3TC_DTG`, `COTRIMOXAZOLE_PROPHYLAXIS`
  - Labs: `CD4_COUNT`, `VIRAL_LOAD`, `HBsAg`, `CREATININE`, `ALT`
  - All `review_status = 'validated'`

### 4.3 Edge Seeding — HIV (manual, validated, Kenya ARV 2022)
- Create `scripts/seed_edges_hiv.py` — inserts typed edges with full provenance:
  - `TDF_DTG_3TC` → `HIV_DISEASE` via `regimen_first_line_for_condition` [Adult, Treatment-naive, p.xx]
  - `HIV_TB_COINFECTION` → `TB_TREATMENT_FIRST` via `regimen_first_line_for_condition` [with temporal qualifier `before_art`]
  - `DTG` → `HIV_PREGNANCY` via `drug_requires_monitoring` [First trimester, note: DTG updated to acceptable per Kenya ARV 2022 after WHO 2021 update]
  - `CD4_LT_200` → `OI_PROPHYLAXIS_REQUIRED` via `finding_increases_suspicion_of`
  - `TDF_DTG_3TC` → `HBsAg` via `regimen_requires_lab_before_initiation`
  - All edges: `guideline_id = 'KEN_HIV_2022'`, `jurisdiction = 'KEN'`, `review_status = 'draft'`
- Clinical review step: have each edge verified against the actual guideline page; change to `review_status = 'validated'` only after review

### 4.4 Agent Tool Registration
- `search_agent.py` `build_agent()` — register sixth tool `query_evidence_graph`:
  - Parameters: `query_type: str` (one of: `contraindications`, `first_line_regimens`, `monitoring`, `evidence_chain`), `concept: str`, `population: Optional[str] = None`, `comorbidity: Optional[str] = None`
  - Instantiates `EvidenceGraph()`; calls appropriate method
  - Returns only `review_status = 'validated'` edges
  - Returns empty list (not error) if concept not found
- Update `BASE_PROMPT` — add guidance: "Use `query_evidence_graph` for validated contraindication checks, first-line regimen confirmation, and monitoring requirement lookups. These results are pre-validated against Kenya guidelines and take precedence over retrieval for those specific query types."

### 4.5 Concept and Edge Seeding — Remaining Diseases
- Create `scripts/seed_concepts_diabetes.py`, `seed_concepts_tb.py`, `seed_concepts_cvd.py`, `seed_concepts_malaria.py`, `seed_concepts_mental_health.py`
- Create corresponding `seed_edges_*.py` for each — following same pattern as HIV
- Malaria edges all carry `notes = "2016 guideline; verify against current WHO/KEMRI recommendations"`
- All edges remain `review_status = 'draft'` until reviewed
- Run all seed scripts; confirm row counts in `concepts` and `clinical_edges`

### 4.6 Coverage Auditor Endpoint
- `api.py` — add `GET /admin/graph/coverage` (admin-only):
  - Accepts optional `?disease=hiv` query param
  - Returns total edge count, breakdown by `relation_type`, breakdown by `review_status`, list of diseases with zero validated edges
- `api.py` — add `GET /admin/graph/stale` (admin-only): returns edges where `guideline_year < 2020` and `review_status = 'validated'` — these are candidates for re-review

### 4.7 Evidence Graph Source Type in SSE
- `api.py` `run_stream()` — when `query_evidence_graph` tool returns results, emit them in the `sources` SSE event with `source_type: "evidence_graph"` (distinct from `source_type: "retrieval"`)
- `frontend/src/hooks/useChat.js` — handle `source_type` field in sources array; pass through to component
- `frontend/src/components/` sources panel — render evidence graph sources with different visual treatment (e.g. different label or indicator) from retrieval sources

---

## Phase 5 — Query Routing and Retrieval Maturity
*After Phases 3 and 4 are both passing.*

### 5.1 Multi-Disease Fan-Out
- `search_tools.py` `search_guidelines()` — for queries where `disease=None`, the current code loops through all tables sequentially; wrap individual table searches with `asyncio.gather()` for parallel execution
- `search_tools.py` — after gather, merge results and re-sort by score before top-k selection
- `search_agent.py` `search_guidelines` tool — update docstring to explicitly state when to pass `disease=None` vs a specific disease key; the agent currently underuses the cross-disease path

### 5.2 HyDE A/B Testing
- `search_tools.py` — add `use_hyde` to `RetrievedChunk` as a boolean field indicating whether HyDE was used for this result
- `logs.py` `log_retrieval()` — add `hyde_used: bool` parameter; log it
- Build a 50-query test set for HIV (10 per category: first-line, dosing, monitoring, contraindications, comorbidity); run each with and without HyDE; compare `top_score` and manual relevance
- Gate HyDE on per-disease `use_hyde` flag in `DISEASE_CONFIG` (already exists); only enable for diseases where A/B shows >5% improvement in top score

### 5.3 Embedding Model Warm-Up
- `api.py` `lifespan()` — after `SearchIndex()` initialisation: call `_search_index._get_embedding_model()` to force load; call `list(_search_index._get_embedding_model().embed(["warmup"]))` to force JIT compilation
- Confirm first real user query no longer has >2s additional latency from model initialisation

### 5.4 Request Timeout
- `api.py` `run_stream()` pydantic-ai path — wrap `agent.run_stream()` in `asyncio.wait_for(..., timeout=120.0)`; catch `asyncio.TimeoutError`; emit `{"type": "error", "message": "Request timed out"}` SSE event
- `frontend/src/hooks/useChat.js` — add `AbortController` with 90-second timeout to the SSE fetch call; on timeout, call `controller.abort()` and emit local error state

### 5.5 LLM Retry
- Create `app/retry.py` — implement `async def retry_with_backoff(coro, max_attempts=3, base_delay=1.0)`: on 429 or 5xx, wait `base_delay * 2^attempt + jitter` before retry; re-raise on final failure
- `api.py` `_run_openai_compatible_chat()` — wrap the `client.post()` call with `retry_with_backoff()`
- `search_tools.py` `_generate_hyde_hypothesis()` — wrap with `retry_with_backoff()`; HyDE failure should not break the search path (already has a fallback but retry improves success rate)

### 5.6 LanceDB Score Normalisation Audit
- After confirmed index metric (cosine vs L2), write a small benchmark script: embed 10 known queries, fetch top 5 results, print raw `_distance` values and current normalised scores
- Confirm `low_confidence` threshold at `score < 0.5` (post-normalisation) is calibrated correctly; adjust if needed based on empirical distribution

### 5.7 Connection Pool Right-Sizing
- `logs.py` `init_db_pool()` — set `min_size=1, max_size=5` for development; add `CDSS_DB_POOL_MAX` env var for override in production
- Add `command_timeout=10` to pool to prevent hung queries from blocking indefinitely

---

## Phase 6 — Role System and Admin Surface
*Can begin in parallel with Phase 5.*

### 6.1 Role Header in Stream
- `frontend/src/lib/api.js` `streamRequest()` — add `X-User-Role` header to the SSE fetch call; it is missing from the streaming path while all other requests carry it

### 6.2 Health Endpoint Role
- `api.py` `health_check()` — confirm `role: role` is already in the response dict; it is; confirm the frontend reads and displays it

### 6.3 Admin Audit UI
- `frontend/src/` — add an admin-only route guarded by role check
- Implement a simple paginated table view over `GET /admin/audit` with filters for `disease`, `event_type`, `date_range`
- Implement `GET /admin/graph/coverage` display: table of disease → edge count → validated count → draft count

### 6.4 HITL Response Handling
- `api.py` `run_stream()` pydantic-ai path — HITL marker extraction is present but buffer management is fragile; rewrite: scan `full_text` for complete `[HITL:...\]` spans rather than scanning `hitl_buffer` incrementally; this avoids the boundary-split problem
- `frontend/src/hooks/useChat.js` — confirm `hitl_prompt` events set HITL state correctly and render the prompt UI
- Test all three HITL types: `CLARIFICATION`, `MISSING_PARAMS`, `CONFLICT`; confirm each renders the correct UI response

### 6.5 New Conversation Session ID
- `frontend/src/hooks/useChat.js` `handleNewConversation()` — generate a new `sessionId` (new `crypto.randomUUID()`); update the state or ref holding `sessionId`; call `DELETE /sessions/{oldSessionId}/clear` with the old ID before switching
- Confirm that after new conversation, subsequent queries use the new session ID in audit logs

---

## Phase 7 — Groq Qwen Reasoning Format
*Small, isolated, high-impact correctness fix.*

### 7.1 Qwen Model Output Guard
- `api.py` `get_llm_model()` — if provider is `groq` and model is `qwen/*`, check `GROQ_REASONING_FORMAT` env var; if not set, change default model to `llama-3.3-70b-versatile` (does not require reasoning format)
- Document in `SETUP.md`: "If using Qwen models via Groq, set `GROQ_REASONING_FORMAT=parsed` in `.env` to prevent inline reasoning output"
- `api.py` `_run_openai_compatible_chat()` — `reasoning_format` is already conditionally added when env var is set; confirm this branch is actually hit at runtime with a log line

---

## Phase 8 — Deployment Hardening
*Final phase before any production or clinic use.*

### 8.1 Environment Guards
- `api.py` `lifespan()` — add assertion: `CDSS_PATIENT_SALT` must not equal the development default when `ENVIRONMENT != "development"`
- `api.py` — add `ENVIRONMENT` env var check; if `production`, enforce: CORS origins must not include `localhost`, debug logging must be off, `CDSS_CHECK_GUIDELINE_UPDATES` must be explicitly set
- `app/.env` — add `ENVIRONMENT=development`; document that this must be changed to `production` before any clinic deployment

### 8.2 Graceful Shutdown
- Run uvicorn with `--timeout-graceful-shutdown 10` in the startup command or `pyproject.toml` script
- `api.py` — confirm `lifespan()` cleanup runs `close_db_pool()` and sets `_search_index = None`

### 8.3 Multi-Worker Safety
- Document that the system must run single-worker (`--workers 1`) until `_session_history` is moved to Postgres
- Add `_session_history` migration to Postgres as a `sessions` table: `session_id TEXT PK`, `history JSONB`, `updated_at TIMESTAMPTZ`
- This unblocks multi-worker deployment and survives process restarts

### 8.4 Schema Versioning
- Run `alembic init alembic` in the project root
- Create `alembic/env.py` with asyncpg connection; do not write migrations yet — infrastructure only
- Document: all future schema changes go through Alembic; `scripts/init_db.py` is for first-run only

### 8.5 Guideline Staleness Check
- Enable `CDSS_CHECK_GUIDELINE_UPDATES=true` only after all source URLs are confirmed valid
- Add proper timer loop to `check_guideline_updates()`: wrap body in `while True:` with `await asyncio.sleep(86400)` (once daily, not once a minute)
- Store last-seen `etag` and `last_modified` per disease in `guidelines` table; only write audit event if values change

### 8.6 Malaria Guideline Upgrade
- Check for updated Kenya Malaria Treatment Guidelines (current is 2016)
- If a post-2021 version is available from MOH or KEMRI, obtain it, replace the PDF, re-run ingestion, and re-run PageIndex build
- Update `config.py` — remove `guideline_warning` if updated to a current version; retain if still 2016
- Update any `clinical_edges` seeded from the 2016 guideline: set `review_status = 'deprecated'`; seed new edges from updated guideline

---

## Summary Table

| Phase | Prerequisite | Key Deliverable |
|---|---|---|
| 0 | None | All queries return valid responses end-to-end |
| 1 | Phase 0 smoke test | Postgres replaces SQLite; audit log durable |
| 2 | Phase 1 | PageIndex integrated; structured queries answered accurately |
| 3 | Phase 2 for HIV + TB | Cross-session clinical memory live |
| 4 | Phase 2 | Evidence graph seeded and queryable by agent |
| 5 | Phases 3 + 4 | Parallel retrieval, HyDE A/B, retry, timeouts |
| 6 | Phase 1 | Role system complete, admin UI, HITL stable |
| 7 | Phase 1 | Groq Qwen output correct |
| 8 | All phases | Safe for clinic deployment |
