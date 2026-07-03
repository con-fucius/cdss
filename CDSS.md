CDSS

Ministry of Health Guidelines, Standards \& Policies Portal - http://guidelines.health.go.ke/#/

Guidelines for Malaria Epidemic Preparedness \& Response in Kenya - https://measuremalaria.cpc.unc.edu/wp-content/uploads/2020/05/Kenya-Malaria-EPR-Guidelines\_Final-Signed.pdf

National Guidelines for the Diagnosis, Treatment, \& Prevention of Malaria in KE -

http://guidelines.health.go.ke:8000/media/Kenya\_Malaria\_Tx\_Guideline\_2010.pdf

https://thecompassforsbc.org/project-examples/national-guidelines-diagnosis-treatment-and-prevention-malaria-kenya-third-edition

National Guidelines for Diagnosis, Treatment and Prevention of Malaria for Health Workers - National Guidelines for Diagnosis, Treatment and Prevention of Malaria for Health Workers (Record no. 17752)

Clinical Guidelines for Management and Referral of Common Conditions at Levels 4–6 Hospitals - https://extranet.who.int/ncdccs/Data/ken\_D1\_clinical%20guidelines%20for%20management%20and%20referral%20of%20common%20conditions.pdf

National Clinical Guidelines for Management of Common Mental Disorders - https://mental.health.go.ke/download/national-clinical-guidelines-for-management-of-common-mental-disorders/



Others:

http://guidelines.health.go.ke/#/category/55/480/meta

http://guidelines.health.go.ke/#/category/55/534/meta

http://guidelines.health.go.ke/#/category/10/528/meta







HIV/AIDS:











Collab Flow

1\. Don't run any git commands. Don't create any task or implementation plan markdown artifacts.

2\. Think critically, review thoroughly, challenge and refine where possible - don't blindly trust my assessment. Criticize and challenge where wrong.

3\. Don't assume, ask where unsure. Don't make assumptions!

4\. We are not adding any new features or overhauling - instead build on top of what exists and bringing system to life.

5\. Instead of deleting, mark components, modules and files that are unneeded as deprecated.

6\. Some issues may not be in the specific lines I highlighted because I have been remediating issues so some things might have changed.

7\. Be thorough and rigorous — critically review, challenge and criticize proposed fixes, refine and improve them.

8\. Explore, refer and be guided by latest official documentation and authoritative resources on the web as of May 2026

9\. No shortcuts, stubs, mocks, dummy or placeholders. Implement exhaustively and integrate with actuals.





Task Scope

Disease Scope: Cardiovascular Diseases, Diabetes Mellitus, HIV/AIDs (already built), Malaria, Mental Health and TB

PDF Guidelines: See path HIV-agent\\app\\docs



Architecture Decisions

| Decision | Choice | Notes |

|---|---|---|

| Session history | In-memory (dev), Redis-backed (prod) | Session ID on every request |

| Patient context | Client-side only (dev), anonymized log (prod) | Never store raw params server-side in dev |

| Roles | Single-user now, admin/clinician distinction wired in but not enforced | Feature-flagged, not commented-out |

| Thread safety | Stateless agent per request, shared read-only vectorstore singleton | Flag: PROD-BLOCKING until done |

| Disease isolation | Per-disease LanceDB tables | 'hiv\_guidelines', 'diabetes\_guidelines', etc. |

| LanceDB | Upgrade to 0.14+, drop LangChain wrapper, native SDK | Breaking change, do first |

| Extraction | Docling → pymupdf4llm → pdfplumber+pymupdf → PyPDF | With quality scoring at each stage |

| Embeddings | 'BAAI/bge-base-en-v1.5' | Replace 'all-MiniLM-L6-v2' |

| Retrieval | Hybrid (vector + BM25) with score thresholding | LanceDB native |

| Reranking | 'BAAI/bge-reranker-base' | Between search and agent |

| Chunking | Hierarchical parent-child, semantic breakpoints | Level 2 (section) returned, Level 3 indexed |

| Conversation history | Full history passed per request | 'message\_history' param in pydantic-ai |

| HITL | Real signals only (low score, missing params, conflict) | Delete random trigger |

| Sources | Inline citations + source panel | Full metadata from tool to UI |

| Logging | Structured JSON, every query/tool/feedback | Replace no-op 'logs.py' |

| Export | PDF with citations, patient context, timestamp | Not markdown |

| Index validation | Quality gate on every ingest | Pre-swap validation |



Actual Task - Start Here

\---

~~PHASE 0 — Foundation Rebuild~~

~~Goal: Fix every broken thing before adding anything. No new features ship until Phase 0 is done. This is the prod-blocking phase.~~



~~---~~

~~P0.1 — Dependency Overhaul~~

~~- P0.1.1 Update 'pyproject.toml':~~

&#x20; ~~- 'lancedb' → '>=0.14.0' (pin to latest stable, currently 0.16.x)~~

&#x20; ~~- Remove 'langchain-community' LanceDB import entirely~~

&#x20; ~~- Add 'docling' (primary extractor)~~

&#x20; ~~- Add 'pymupdf4llm' (fallback extractor)~~

&#x20; ~~- Add 'pdfplumber' (table extraction fallback)~~

&#x20; ~~- Add 'fastembed' or upgrade 'sentence-transformers' to '>=3.0'~~

&#x20; ~~- Add 'rerankers\[cross-encoders]' (BGE reranker)~~

&#x20; ~~- Add 'structlog' (structured JSON logging)~~

&#x20; ~~- Update 'langchain' to '>=0.3' (needed only for remaining LangChain text splitters, then remove)~~

&#x20; ~~- Add 'tiktoken' (token counting for chunking validation)~~

&#x20; ~~- Note: 'langchain\_community.vectorstores.LanceDB' is being deleted entirely — do not update it~~

~~- P0.1.2 Verify pydantic-ai version against current API — 'agent.run\_stream()', 'message\_history', 'new\_messages()' interfaces. Adjust if needed.~~

~~- P0.1.3 Freeze 'requirements.txt' from updated 'pyproject.toml' and verify clean install in fresh venv.~~



~~---~~

~~P0.2 — Logging Infrastructure~~

~~Do this second so everything built after it has real logging.~~

~~- P0.2.1 Rewrite 'logs.py' using 'structlog'. Configure JSON output with timestamps, log level, module name.~~

~~- P0.2.2 Define log schema:~~

&#x20; ~~'''~~

&#x20; ~~QUERY\_LOG: session\_id, timestamp, query\_text, disease\_targets, patient\_context\_hash, context\_fields\_used~~

&#x20; ~~RETRIEVAL\_LOG: session\_id, query\_id, tool\_name, search\_query, chunks\_returned, top\_score, latency\_ms~~

&#x20; ~~TOOL\_LOG: session\_id, query\_id, tool\_call\_index, tool\_name, params, result\_length~~

&#x20; ~~RESPONSE\_LOG: session\_id, query\_id, response\_length, sources\_cited, total\_latency\_ms~~

&#x20; ~~FEEDBACK\_LOG: session\_id, message\_id, feedback\_type, note, timestamp~~

&#x20; ~~INIT\_LOG: event (start/success/fail), disease, doc\_name, chunk\_count, latency\_ms, extractor\_used, quality\_score~~

&#x20; ~~ERROR\_LOG: session\_id, query\_id, error\_type, traceback, recovery\_action~~

&#x20; ~~'''~~

~~- P0.2.3 Create 'log\_query()', 'log\_retrieval()', 'log\_response()', 'log\_feedback()', 'log\_init()', 'log\_error()' helpers. All async-safe.~~

~~- P0.2.4 Wire logging into 'api.py' at every request boundary. Patient context: hash the raw params (SHA-256), log hash + field names only, never values.~~



~~---~~

~~P0.3 — Ingestion Pipeline Rebuild ('ingest.py')~~

~~- P0.3.1 Create 'extractors/' module with:~~

&#x20; ~~- 'DoclingExtractor' — primary, returns 'DocumentStructure' (sections, tables as structured JSON, reading order)~~

&#x20; ~~- 'PyMuPDFExtractor' — fallback 1, returns markdown with table-to-markdown~~

&#x20; ~~- 'PDFPlumberExtractor' — fallback 2 (tables only, merged with pymupdf text)~~

&#x20; ~~- 'PyPDFExtractor' — fallback 3, text only, flags result as 'quality=degraded'~~

&#x20; ~~- Each extractor has a 'quality\_score()' method: measures text density, table count, section depth, expected keyword presence per disease~~

&#x20; ~~- 'ExtractionPipeline.extract(pdf\_path, disease)' tries chain in order, returns first result above quality threshold, logs which extractor succeeded~~

~~- P0.3.2 Create 'chunkers/' module:~~

&#x20; ~~- 'SemanticChunker' — uses embedding similarity between adjacent sentences to find breakpoints (via 'semantic-text-splitter'). Produces Level 3 chunks (\~300-400 tokens, not chars).~~

&#x20; ~~- 'HierarchicalIndexer' — takes Docling structural output, maps Level 1 (chapter) / Level 2 (section) / Level 3 (sub-chunk). Stores parent-child relationships.~~

&#x20; ~~- For non-Docling extractions: falls back to 'RecursiveCharacterTextSplitter' at section boundaries where possible, with a warning log.~~

~~- P0.3.3 Define unified chunk schema:~~

&#x20; ~~'''python~~

&#x20; ~~@dataclass~~

&#x20; ~~class IndexedChunk:~~

&#x20;     ~~chunk\_id: str           uuid~~

&#x20;     ~~parent\_id: str          Level 2 section ID~~

&#x20;     ~~disease: str            "hiv" | "diabetes" | "cvd" | "tb" | "mental\_health"~~

&#x20;     ~~guideline\_name: str     "Kenya ARV Guidelines 2022"~~

&#x20;     ~~guideline\_version: str  "2022"~~

&#x20;     ~~guideline\_year: int~~

&#x20;     ~~source\_url: str         MOH portal URL~~

&#x20;     ~~section\_number: str     "4.2"~~

&#x20;     ~~section\_title: str      "First-line ART for Adults"~~

&#x20;     ~~page: int~~

&#x20;     ~~content\_type: str       "narrative"|"table"|"list"|"criteria"|"algorithm"~~

&#x20;     ~~population\_tags: list   \["adult", "treatment-naive"]~~

&#x20;     ~~clinical\_tags: list     \["first-line", "regimen", "dosing"]~~

&#x20;     ~~text: str               Level 3 chunk text (indexed, embedded)~~

&#x20;     ~~parent\_text: str        Level 2 section full text (returned to agent)~~

&#x20;     ~~vector: list\[float]     embedded Level 3 chunk~~

&#x20;     ~~extraction\_quality: str  "full"|"degraded"~~

&#x20; ~~'''~~

~~- P0.3.4 Parameterize 'TABLE\_NAME' → '{disease}\_guidelines' per disease. Remove hardcoded '"documents"'.~~

~~- P0.3.5 Per-disease 'index\_disease(disease: str, pdf\_path: str, db\_path: str)' function. Replaces 'index\_data()'.~~

~~- P0.3.6 Build ANN + FTS indexes explicitly after table creation:~~

&#x20; ~~'''python~~

&#x20; ~~table.create\_index(metric="cosine", num\_partitions=32, num\_sub\_vectors=16)~~

&#x20; ~~table.create\_fts\_index("text", replace=True)~~

&#x20; ~~'''~~

~~- P0.3.7 Index validation suite:~~

&#x20; ~~- Chunk count within expected range per disease~~

&#x20; ~~- Required clinical terms present (disease-specific keyword list)~~

&#x20; ~~- Top-5 retrieval on 10 known queries returns expected section titles~~

&#x20; ~~- Schema validation: all columns present, no null vectors~~

&#x20; ~~- If validation fails: do not swap to new table, keep old, raise 'IndexValidationError', log~~

~~- P0.3.8 Warm-up embedding call at init: embed a dummy string to trigger JIT compilation before first real query.~~



~~---~~

~~P0.4 — Embedding Model Upgrade~~

~~- P0.4.1 Replace 'all-MiniLM-L6-v2' with 'BAAI/bge-base-en-v1.5' everywhere (ingest, search tools, agent init).~~

~~- P0.4.2 Use instruction prefix for query embedding: '"Represent this sentence for searching relevant passages: {query}"'. Do NOT use prefix for document embedding.~~

~~- P0.4.3 Ensure model is loaded once at startup as a singleton, not reinstantiated per request.~~



~~---~~

~~P0.5 — Search Tools Rebuild ('search\_tools.py')~~

~~- P0.5.1 Delete 'faq\_index', 'faq\_vindex', 'embeddings' globals. Delete 'set\_search\_index()'. Delete 'text\_search()'. Delete 'vector\_search()'.~~

~~- P0.5.2 Create 'SearchIndex' singleton class:~~

&#x20; ~~- Holds: 'db: lancedb.LanceDBConnection', 'tables: dict\[str, lancedb.Table]', 'embeddings\_model', 'reranker'~~

&#x20; ~~- Initialized once at startup from 'api.py' lifespan~~

&#x20; ~~- Read-only after init — safe to share across requests~~

~~- P0.5.3 Create 'RetrievedChunk' dataclass per schema above.~~

~~- P0.5.4 Implement 'search\_guidelines(query: str, disease: str | None, content\_type: str | None, k\_initial: int = 20, k\_final: int = 5) -> list\[RetrievedChunk]':~~

&#x20; ~~- If 'disease' specified: search that table only~~

&#x20; ~~- If 'disease=None': fan out to all available tables, merge results by score, deduplicate~~

&#x20; ~~- Hybrid search (vector + BM25) with RRF fusion — LanceDB native~~

&#x20; ~~- Apply score threshold: if top score < 0.5, return empty with 'low\_confidence=True' signal~~

&#x20; ~~- Cross-encoder rerank top-20 → return top-5~~

&#x20; ~~- For each returned chunk: fetch parent section text from table by 'parent\_id'~~

&#x20; ~~- Log retrieval: query, disease, scores, latency~~

~~- P0.5.5 Implement 'get\_section(section\_id: str, disease: str) -> RetrievedChunk | None' — direct fetch by ID, no embedding needed.~~

~~- P0.5.6 Expose these as pydantic-ai tools with proper type annotations and docstrings (the docstring is the tool description the agent sees).~~

~~- P0.5.7 Handle 'low\_confidence' case: tool returns a structured signal that the agent can use to ask for clarification rather than hallucinating on poor retrieval.~~



~~---~~

~~P0.6 — Agent Rebuild ('search\_agent.py')~~

~~- P0.6.1 Remove dependency on 'search\_tools.set\_search\_index()'. Agent receives the 'SearchIndex' singleton via dependency injection (pydantic-ai 'deps' pattern), not global mutation.~~

~~- P0.6.2 Restructure system prompt:~~

&#x20; ~~'''python~~

&#x20; ~~BASE\_PROMPT = """\[core rules: citation format \[Guideline Name, §section, p.page],~~

&#x20; ~~uncertainty handling, safety rules, response formatting by query type]"""~~

&#x20;

&#x20; ~~DISEASE\_CONTEXT\_MAP = {~~

&#x20;     ~~"hiv": "...",~~

&#x20;     ~~"diabetes": "...",~~

&#x20;     ~~"cvd": "...",~~

&#x20; ~~}~~

&#x20;

&#x20; ~~def build\_system\_prompt(available\_diseases: list\[str]) -> str:~~

&#x20;     ~~disease\_context = "\\n".join(\[DISEASE\_CONTEXT\_MAP\[d] for d in available\_diseases])~~

&#x20;     ~~return f"{BASE\_PROMPT}\\n\\n Available Knowledge Bases\\n{disease\_context}"~~

&#x20; ~~'''~~

~~- P0.6.3 Register tools: 'search\_guidelines', 'get\_section'. Tools call 'SearchIndex' methods from injected deps.~~

~~- P0.6.4 Agent is instantiated per-request — NOT as a global. The 'SearchIndex' singleton is passed as deps.~~

~~- P0.6.5 System prompt built once at startup from available diseases (detected from LanceDB table names), passed at agent instantiation.~~



~~---~~

~~P0.7 — API Rebuild ('api.py')~~

~~- P0.7.1 Remove global '\_agent'. Replace with global '\_search\_index: SearchIndex' (read-only singleton).~~

~~- P0.7.2 Per-request agent instantiation:~~

&#x20; ~~'''python~~

&#x20; ~~@app.post("/chat/stream")~~

&#x20; ~~async def chat\_stream(request: ChatRequest):~~

&#x20;     ~~agent = build\_agent(\_search\_index)   lightweight, no IO~~

&#x20;     ~~...~~

&#x20; ~~'''~~

~~- P0.7.3 Add 'session\_id' to 'ChatRequest'. In-memory session store: 'dict\[str, list\[ModelMessage]]'. Pass stored history to 'agent.run\_stream(message\_history=history\[session\_id])'.~~

~~- P0.7.4 Update 'ChatRequest':~~

&#x20; ~~'''python~~

&#x20; ~~class ChatRequest(BaseModel):~~

&#x20;     ~~session\_id: str~~

&#x20;     ~~message: str~~

&#x20;     ~~context: Optional\[PatientContext] = None~~

&#x20;     ~~history: Optional\[list\[MessageDict]] = None   client sends back for stateless fallback~~

&#x20; ~~'''~~

~~- P0.7.5 Streaming response must emit sources event after 'done':~~

&#x20; ~~'''~~

&#x20; ~~data: {"type": "chunk", "content": "..."}~~

&#x20; ~~data: {"type": "done", "full\_text": "...", "timestamp": "..."}~~

&#x20; ~~data: {"type": "sources", "sources": \[...RetrievedChunk metadata...]}~~

&#x20; ~~data: {"type": "stream\_end"}~~

&#x20; ~~'''~~

&#x20; ~~Sources are extracted from 'result.new\_messages()' — parse tool call results to extract 'RetrievedChunk' objects returned by 'search\_guidelines'.~~

~~- P0.7.6 Replace 'build\_context\_prefix()' string injection with structured patient context added to agent's first system message or prepended as a structured block, not string concatenation.~~

~~- P0.7.7 Remove 'HITL\_QUESTIONS' random triggering from API response handling. HITL signals come from the agent's output (the agent emits a '\[NEEDS\_CLARIFICATION: ...]' marker or structured output when it detects missing params or low-confidence retrieval).~~

~~- P0.7.8 Add '/admin/\*' route prefix. For now, all admin routes return 200 with stub data but are architecturally separated. Auth middleware placeholder — 'x-admin-token' header checked, always passes in dev.~~

~~- P0.7.9 Add '/diseases' endpoint — returns list of available diseases from LanceDB table names + metadata (version, chunk count, last indexed).~~

~~- P0.7.10 Add '/feedback' POST endpoint — logs feedback with session ID, message ID, type, note. Returns 200.~~

~~- P0.7.11 Thread safety audit: verify no mutable shared state outside '\_search\_index'. Document any remaining risks.~~



~~---~~

~~P0.8 — Frontend Phase 0 ('App.jsx')~~

~~- P0.8.1 Generate 'session\_id' (UUID) on app load, persist in 'sessionStorage' (not 'localStorage' — lost on tab close, appropriate for local dev). Include in every API request.~~

~~- P0.8.2 Patient context: move from 'QueryBuilderPage' local state to a persistent 'PatientContextPanel' component. Context is session-scoped state at 'App' level, injected into every 'ChatRequest'. Never sent to any storage.~~

~~- P0.8.3 Wire 'sources' event from SSE stream into message state. 'SourcesDisplay' component now has real data.~~

~~- P0.8.4 Inline citation click: '\[ARV Guidelines 2022, §4.2, p.47]' → highlights corresponding source in panel.~~

~~- P0.8.5 Agent action log: replace hardcoded strings with events parsed from SSE stream. Add a 'tool\_call' event type to SSE that fires when the agent invokes a tool, with actual tool name and query. Render these in 'AgentActionLog'.~~

~~- P0.8.6 Remove 'HITL\_QUESTIONS' random triggering. HITL panel renders only when SSE emits a 'hitl\_prompt' event from the agent.~~

~~- P0.8.7 Handle 'low\_confidence' signal from sources: show a warning banner on the message — "Retrieval confidence was low for this query. Verify against source."~~

~~- P0.8.8 Export: replace markdown export with PDF export using 'jspdf' + 'html2canvas'. Include: timestamp, session ID, patient context fields (not values), citations list, message thread.~~

~~- P0.8.9 Add '/diseases' fetch on app load. Store available diseases in app state.~~



\---

~~PHASE 1 — Multi-Disease~~

~~Goal: Diabetes + CVD added. Disease-aware UI and routing. Source panel complete.~~

~~---~~

&#x20;~~P1.1 — PDF Acquisition and Extraction Verification~~

~~- P1.1.1 Download DM Guidelines V15 2024, CVD Guidelines 2024, TB (NTLD Oct 2025). Verify each is text-extractable (run Docling, check quality score > threshold). Document extraction quality per PDF.~~

~~- P1.1.2 For any PDF that scores 'degraded': attempt OCR via Docling's built-in OCR. If still degraded: flag for manual section extraction of highest-value content.~~

~~- P1.1.3 Run ingestion for each disease. Verify index validation passes.~~

~~- P1.1.4 Run 10 known queries per disease, verify top-5 retrieval includes correct sections.~~



~~---~~

&#x20;~~P1.2 — Disease-Specific Content Configuration~~

~~- P1.2.1 Define 'DISEASE\_CONFIG' dict:~~

&#x20; ~~'''python~~

&#x20; ~~DISEASE\_CONFIG = {~~

&#x20;     ~~"hiv": {~~

&#x20;         ~~"display\_name": "HIV/AIDS",~~

&#x20;         ~~"guideline\_name": "Kenya ARV Guidelines 2022",~~

&#x20;         ~~"table\_name": "hiv\_guidelines",~~

&#x20;         ~~"population\_options": \["Adult", "Adolescent (10-19)", "Child (<10)", "Infant (<1)"],~~

&#x20;         ~~"condition\_options": \["Treatment-naive", "Treatment-experienced", "Pregnant", "Breastfeeding"],~~

&#x20;         ~~"comorbidity\_options": \["TB", "Hepatitis B", "Hepatitis C", "CKD", "Diabetes"],~~

&#x20;         ~~"filter\_options": \["First-line", "Second-line", "Prophylaxis", "Monitoring", "PMTCT"],~~

&#x20;         ~~"clinical\_params": \["CD4 count", "Viral load", "WHO stage"],~~

&#x20;         ~~"validation\_keywords": \["TDF", "DTG", "lamivudine", "CD4", "viral load"],~~

&#x20;     ~~},~~

&#x20;     ~~"diabetes": {~~

&#x20;         ~~"display\_name": "Diabetes Mellitus",~~

&#x20;         ~~"guideline\_name": "Kenya DM Guidelines V15 2024",~~

&#x20;         ~~"table\_name": "diabetes\_guidelines",~~

&#x20;         ~~"population\_options": \["Adult", "Elderly (>65)", "Pregnant", "Child/Adolescent"],~~

&#x20;         ~~"condition\_options": \["Type 1 DM", "Type 2 DM", "Gestational DM", "DM with complications"],~~

&#x20;         ~~"comorbidity\_options": \["Hypertension", "CKD", "Heart failure", "HIV", "TB"],~~

&#x20;         ~~"filter\_options": \["Diagnosis", "Pharmacotherapy", "Insulin", "Monitoring", "Complications"],~~

&#x20;         ~~"clinical\_params": \["HbA1c", "FPG", "eGFR", "BMI"],~~

&#x20;         ~~"validation\_keywords": \["HbA1c", "metformin", "insulin", "FPG", "SMBG"],~~

&#x20;     ~~},~~

&#x20;     ~~"cvd": { ... },~~

&#x20;     ~~"tb": { ... },~~

&#x20; ~~}~~

&#x20; ~~'''~~

~~- P1.2.2 '/context-options?disease=hiv' endpoint returns disease-specific options from 'DISEASE\_CONFIG'. Replaces hardcoded HIV-only response.~~



~~---~~

~~P1.3 — Agent Multi-Disease Routing~~

~~- P1.3.1 System prompt lists all available diseases and their scope. Agent decides which disease knowledge base(s) to query per tool call.~~

~~- P1.3.2 'search\_guidelines' accepts 'disease: str | None'. Agent passes 'None' when query is disease-ambiguous (triggers multi-table fan-out with score-based disease attribution).~~

~~- P1.3.3 For comorbidity queries (patient has HIV and DM): agent calls 'search\_guidelines' twice, one per disease, synthesizes both results with explicit source attribution per disease.~~

~~- P1.3.4 Test: 15 cross-disease queries. Verify correct disease routing for unambiguous queries. Verify both sources cited for comorbidity queries.~~



~~---~~

~~P1.4 — Frontend Multi-Disease UI~~

~~- P1.4.1 Sidebar: add Knowledge Base page (new nav item). No "About" page yet — that content moves into KB page.~~

~~- P1.4.2 Knowledge Base page:~~

&#x20; ~~- Shows each indexed disease: name, guideline version, last indexed date, chunk count, extraction quality badge~~

&#x20; ~~- Status indicators: 'indexed' (green), 'degraded' (yellow), 'not indexed' (grey)~~

&#x20; ~~- Data from '/diseases' endpoint~~

&#x20; ~~- Admin-only controls (hidden in single-user mode): "Re-index", "Upload PDF" — stubbed, not functional yet~~

~~- P1.4.3 Patient Context Panel (persistent, collapsible):~~

&#x20; ~~- Position: right sidebar or top-of-chat collapsible~~

&#x20; ~~- Fields: Active conditions (multi-select from available diseases), plus disease-specific clinical params per active condition~~

&#x20; ~~- Example: selecting "HIV" surfaces CD4/VL fields; selecting "DM" surfaces HbA1c/eGFR fields~~

&#x20; ~~- Current medications (free text, comma-separated)~~

&#x20; ~~- All fields are session-only, never sent to server in dev~~

&#x20; ~~- Context summary shown as a compact chip row below the chat header~~

~~- P1.4.4 Query Builder redesign:~~

&#x20; ~~- Step 1: Disease selector (single or multi for comorbidity)~~

&#x20; ~~- Step 2: Disease-specific context selectors — loaded from '/context-options?disease=X'~~

&#x20; ~~- Step 3: Clinical parameters (from patient context if already set, editable here)~~

&#x20; ~~- Step 4: Filter options (disease-specific)~~

&#x20; ~~- Step 5: Question text~~

&#x20; ~~- Preview shows structured query string~~

~~- P1.4.5 Source panel multi-disease differentiation: sources tagged by disease with distinct colour coding. If response draws from both HIV and DM guidelines, sources grouped by disease.~~

~~- P1.4.6 Chat header subtitle updates dynamically: "Querying: HIV/AIDS, Diabetes Mellitus" based on what the agent searched.~~

~~- P1.4.7 Sample questions on empty chat state: disease-agnostic + filterable by disease.~~



~~---~~

~~P1.5 — Conversation History~~

~~- P1.5.1 In-memory session store in 'api.py': 'dict\[str, deque\[ModelMessage]]' with max depth (e.g., 20 message pairs — configurable).~~

~~- P1.5.2 Every '/chat/stream' call: load history for 'session\_id', pass to agent, store updated history after response.~~

~~- P1.5.3 '/sessions/{session\_id}/clear' DELETE endpoint — clears history for session.~~

~~- P1.5.4 Frontend: "New conversation" button calls clear endpoint then generates new session ID.~~

~~- P1.5.5 Document production path: Redis-backed session store. Keyed by 'session\_id', TTL 24h. Serialized 'ModelMessage' list. No code changes needed at the agent level — just swap the store implementation.~~



\---

~~PHASE 2 — Structured KB + Operational Maturity~~

~~Goal: Deterministic lookups for Type 1 queries. Real audit trail. Feedback loop. Admin foundation.~~



~~---~~

~~P2.1 — Structured KB: Table Extraction Pipeline~~

~~- P2.1.1 Create 'kb/' module:~~

&#x20; ~~- 'TableExtractor' — runs Docling on PDF, extracts all tables as structured JSON. For each table: type classification (regimen, dosing, diagnostic\_criteria, monitoring, reference\_values), disease tag, source metadata.~~

&#x20; ~~- Quality score per table: column count, row count, presence of expected headers (drug names for regimen tables, threshold values for diagnostic tables).~~

&#x20; ~~- Output: 'kb/raw/{disease}/{table\_id}.json' — unvalidated extracted tables.~~

~~- P2.1.2 Schema validator per table type:~~

&#x20; ~~- 'RegimenTable': must have columns for population, drugs, frequency, notes. Each drug must match a drug name in a reference list.~~

&#x20; ~~- 'DiagnosticTable': must have threshold values (numeric with units).~~

&#x20; ~~- 'DosingTable': must have weight or age ranges, dose, frequency.~~

&#x20; ~~- Failed validation → flagged for manual review, NOT auto-imported.~~

~~- P2.1.3 Manual review tool: minimal CLI or JSON editor script that shows the raw extracted table alongside the PDF page screenshot (generated from pymupdf). Reviewer corrects and saves to 'kb/validated/{disease}/{table\_id}.json'.~~

~~- P2.1.4 Import validated tables into a separate LanceDB table '{disease}\_kb\_tables'. Schema stores the JSON and a natural-language rendering of each table row (for fallback RAG).~~

~~- P2.1.5 Priority tables to validate first:~~

&#x20; ~~- HIV: first-line regimens, second-line regimens, pediatric dosing, prophylaxis thresholds~~

&#x20; ~~- DM: diagnostic criteria (FPG, HbA1c, OGTT thresholds), first-line pharmacotherapy by patient type~~

&#x20; ~~- CVD: BP targets by risk category, lipid targets, antihypertensive drug selection~~



~~---~~

~~P2.2 — Structured KB: Lookup Tool~~

~~- P2.2.1 'lookup\_kb(query\_type: str, disease: str, filters: dict) -> KBResult | None':~~

&#x20; ~~- 'query\_type': '"regimen" | "dosing" | "diagnostic\_criteria" | "monitoring\_threshold"'~~

&#x20; ~~- Exact match first, then fuzzy (relax one filter at a time)~~

&#x20; ~~- Returns: structured data + natural language rendering + source metadata + 'confidence: "structured"|"rag\_fallback"'~~

~~- P2.2.2 Register as third agent tool alongside 'search\_guidelines' and 'get\_section'.~~

~~- P2.2.3 Agent uses this first for Type 1 queries (exact lookups). Falls back to 'search\_guidelines' if 'lookup\_kb' returns 'None'.~~

~~- P2.2.4 Response includes confidence tier: "Answer from structured knowledge base: \[ARV 2022, §4.2, Table 4.1]" vs "Answer retrieved from guideline text: \[ARV 2022, §4.2, p.47]". Different visual treatment in UI.~~



~~---~~

~~P2.3 — Audit Log~~

~~- P2.3.1 All structured logs from P0.2 are now queryable. Add SQLite as a lightweight log store for dev (JSON logs → 'audit.db'). Prod: ship logs to CloudWatch or a proper log aggregator.~~

~~- P2.3.2 '/admin/audit' GET endpoint: query logs by date range, session, disease, feedback type. Returns paginated JSON.~~

~~- P2.3.3 Admin Audit Log page in frontend:~~

&#x20; ~~- Table of queries with: timestamp, disease(s), query preview, response latency, feedback~~

&#x20; ~~- Filter by date, disease, feedback (positive/negative/flagged)~~

&#x20; ~~- Row expand → full query, full response, sources cited, patient context field names (not values)~~

&#x20; ~~- Export CSV~~

~~- P2.3.4 Guideline update detection: cron job (or startup check) that sends HEAD request to each PDF source URL, compares ETag/Last-Modified. If changed: log 'GUIDELINE\_UPDATE\_DETECTED' event, surface alert on KB Status page.~~



~~---~~

~~P2.4 — Feedback and Corrections~~

~~- P2.4.1 Feedback model expanded:~~

&#x20; ~~'''python~~

&#x20; ~~class FeedbackSubmission(BaseModel):~~

&#x20;     ~~session\_id: str~~

&#x20;     ~~message\_id: str~~

&#x20;     ~~feedback\_type: str   "accurate"|"inaccurate"|"outdated"|"incomplete"|"other"~~

&#x20;     ~~note: Optional\[str]~~

&#x20;     ~~correction: Optional\[str]   what the correct answer should be~~

&#x20;     ~~sources\_used: list\[str]     chunk IDs from the response~~

&#x20; ~~'''~~

~~- P2.4.2 '/feedback' POST stores to audit log + 'feedback.db'.~~

~~- P2.4.3 Frontend: expand message feedback beyond thumbs up/down. After thumbs down: modal with reason selector (inaccurate/outdated/incomplete/other) + optional text field + optional correction field.~~

~~- P2.4.4 Admin Feedback page: see all flagged responses, grouped by type. Corrections feed into KB update queue for next re-index cycle.~~



~~---~~

~~P2.5 — Real HITL Implementation~~

~~- P2.5.1 Define agent HITL signals in system prompt:~~

&#x20; ~~- If retrieval returns 'low\_confidence=True': agent outputs '\[HITL:CLARIFICATION]' marker with a specific clarification question.~~

&#x20; ~~- If required clinical params are absent from query and patient context: agent outputs '\[HITL:MISSING\_PARAMS: CD4, viral\_load]'.~~

&#x20; ~~- If retrieved chunks conflict: agent outputs '\[HITL:CONFLICT: regimen differs for \[population A] vs \[population B]]'.~~

~~- P2.5.2 SSE parser in 'api.py' detects '\[HITL:...]' markers in streamed response. Emits a 'hitl\_prompt' SSE event with the structured question.~~

~~- P2.5.3 Frontend renders HITL prompt below the (incomplete or paused) response. User responds via buttons or short text input. Response is sent as a follow-up in the same session.~~

~~- P2.5.4 Test: 10 queries designed to trigger each HITL signal. Verify correct triggering, correct follow-up handling.~~



~~---~~

~~P2.6 — Offline Fallback Mode~~

~~- P2.6.1 Health check monitors LLM API reachability on startup and periodically.~~

~~- P2.6.2 If LLM unreachable: API returns '"mode": "kb\_only"' on '/health'.~~

~~- P2.6.3 In KB-only mode: '/chat/stream' runs 'search\_guidelines' and 'lookup\_kb' directly (no agent), returns top-3 retrieved sections as formatted text with full source metadata. No synthesis.~~

~~- P2.6.4 Frontend shows "Offline mode — showing guideline sections directly" banner. Source panel shows all retrieved sections, not just cited ones.~~



\---

PHASE 3 — Advanced Retrieval + Production Hardening

Goal: Quality and reliability maximized. Prod-ready.



\---

P3.1 — HyDE Query Expansion (measure first)

\- P3.1.1 Implement HyDE as optional pipeline step: generate a hypothetical guideline answer to the query using a lightweight LLM call, embed the hypothesis, use that embedding for vector search.

\- P3.1.2 A/B test against baseline: same 50-query test set, measure Recall@5 with and without HyDE. Ship only if Recall@5 improves by >5%.

\- P3.1.3 HyDE is disease-aware: the hypothetical generation prompt includes the disease context so the hypothesis is in the right clinical domain.

\- P3.1.4 HyDE adds \~200ms latency. Toggle per disease. Disable for queries that already have high keyword specificity (drug names, dosages — BM25 already handles these well).



\---

P3.2 — Guidelines Browser

\- P3.2.1 '/guidelines/{disease}/toc' endpoint: returns table of contents (chapter → section hierarchy) from stored Level 1/2 chunk metadata in LanceDB.

\- P3.2.2 Frontend Guidelines Browser page: tree view of guideline structure. Click a section → opens chat pre-scoped to that section (adds section metadata as a filter to the query context).

\- P3.2.3 Section view: clicking a section shows the stored 'parent\_text' (full section content) alongside a "Ask about this section" chat input.

\- P3.2.4 Works in offline mode — no LLM required.



\---

P3.3 — Role System Foundation

\- P3.3.1 Define 'UserRole' enum: 'CLINICIAN | ADMIN'. Stored in session context (in dev: always 'CLINICIAN', configurable via env var 'CDSS\_ROLE=admin').

\- P3.3.2 Admin-only routes enforce role check. '/admin/\*' returns '403' if not admin.

\- P3.3.3 Frontend: admin-only UI elements hidden via role check. Currently: KB Status page "Re-index" and "Upload PDF" buttons, Audit Log page, Feedback corrections page.

\- P3.3.4 Document the production auth path: JWT tokens, role claim in payload, middleware validates per request. No implementation yet — design documented.



\---

P3.4 — Performance Benchmarking and Tuning

\- P3.4.1 Build a query latency benchmark: 50 representative queries, measure per-stage latency (embed, retrieve, rerank, LLM TTFT, LLM completion).

\- P3.4.2 Profile and optimize each stage. Expected targets: embed <80ms, retrieve+rerank <200ms, LLM TTFT <1s (Mistral local).

\- P3.4.3 If embedding is a bottleneck: evaluate 'fastembed' (ONNX-based, 2-3x faster than sentence-transformers for inference).

\- P3.4.4 LanceDB ANN index tuning: adjust 'num\_partitions' and 'num\_sub\_vectors' based on actual table sizes.

\- P3.4.5 Response length tuning: enforce per-query-type response length in system prompt. Measure actual lengths, adjust.



\---

P3.5 — Production Deployment Architecture (Document + Stub)

\- P3.5.1 Document: Redis session store implementation (drop-in replacement for in-memory dict). Key schema, TTL policy, serialization.

\- P3.5.2 Document: AWS deployment topology — SKIP THIS

\- P3.5.3 Document: CORS lockdown for production (replace 'allow\_origins=\["\*"]').

\- P3.5.4 Document: rate limiting per session/IP for production.

\- P3.5.5 Implement health check endpoint that verifies: LanceDB reachable, all expected disease tables present and valid, embedding model loaded, LLM API reachable. Returns per-component status.



\---





















Let me plan what needs to be done:Phase 0 — Foundation (immediate, before new diseases)Backend changes:



Upgrade LanceDB to 0.14+ (drop LangChain wrapper, native SDK)

Replace PyPDFLoader with pymupdf4llm (Docling is heavier to set up; pymupdf4llm is a good interim)



Implement hierarchical chunking with parent-child storage



Switch embedding to BAAI/bge-base-en-v1.5

Implement hybrid search (vector + BM25/FTS) with score thresholding

Fix search tool return type to include full metadata

Fix agent to pass conversation history

Wire sources from search tool → API response → frontend source panel

Parameterize TABLE\_NAME by disease (not hardcoded "documents")

Add structured JSON logging

Remove HITL random triggering

Add index validation

Fix thread safety (per-request agent, session store)

Multi-disease support

Frontend changes:



Disease selector (session-level)

Dynamic context options based on selected disease

Sources panel that actually renders

Agent action log reflecting real tool calls

Patient context sidebar

Conversation history passed with every request

New pages: KB Status, Audit Log, Guidelines Browser

Let me think about the scope for now. The user said "continue" after a very detailed planning discussion. I think they want me to start implementing. Let me start with Phase 0 - the most critical foundation fixes.Let me plan the implementation:

New project structure: Rename/restructure the project from "HIV-agent" to "CDSS" concept

Backend core rewrites: ingest.py, search\_tools.py, search\_agent.py, api.py, logs.py

Frontend rewrites: App.jsx major overhaul







Questions Still Open



Session persistence: For conversation history, do you want sessions to persist across browser refreshes (stored server-side in a session store) or be ephemeral (in-memory, lost on refresh)? For local dev, in-memory is fine. For production, Redis or PostgreSQL-backed sessions.

Patient context data handling: When a clinician enters patient parameters (CD4 count, HbA1c), are you comfortable logging these (anonymized)? Or should patient context be strictly client-side only, injected into the query at the frontend and never stored server-side?

Admin vs clinician roles: Is there a distinction? Index management, KB updates, audit log access — these should be admin-gated. Or is this all single-user for now?

DM guideline PDF access: Can you download http://guidelines.health.go.ke:8000/media/National\_DM\_Guidelines\_Version\_15\_2024\_Signed-compressed.pdf and verify it's text-extractable (not scanned)? Run a quick PyPDF extract and eyeball the output before committing to the ingestion pipeline design.

Other diseases — which are confirmed? Before designing the router and KB schema, lock down which diseases are in scope for Phase 1 and Phase 2. Each needs a confirmed, downloadable PDF from the MOH portal. Hypertension, TB, Malaria are likely candidates — but verify the PDFs are accessible and text-extractable before committing.







CDSS

Ministry of Health Guidelines, Standards \& Policies Portal - http://guidelines.health.go.ke/#/

Guidelines for Malaria Epidemic Preparedness \& Response in Kenya - https://measuremalaria.cpc.unc.edu/wp-content/uploads/2020/05/Kenya-Malaria-EPR-Guidelines\_Final-Signed.pdf

National Guidelines for the Diagnosis, Treatment, \& Prevention of Malaria in KE -

http://guidelines.health.go.ke:8000/media/Kenya\_Malaria\_Tx\_Guideline\_2010.pdf

https://thecompassforsbc.org/project-examples/national-guidelines-diagnosis-treatment-and-prevention-malaria-kenya-third-edition

National Guidelines for Diagnosis, Treatment and Prevention of Malaria for Health Workers - National Guidelines for Diagnosis, Treatment and Prevention of Malaria for Health Workers (Record no. 17752)

Clinical Guidelines for Management and Referral of Common Conditions at Levels 4–6 Hospitals - https://extranet.who.int/ncdccs/Data/ken\_D1\_clinical%20guidelines%20for%20management%20and%20referral%20of%20common%20conditions.pdf

National Clinical Guidelines for Management of Common Mental Disorders - https://mental.health.go.ke/download/national-clinical-guidelines-for-management-of-common-mental-disorders/



Others:

http://guidelines.health.go.ke/#/category/55/480/meta

http://guidelines.health.go.ke/#/category/55/534/meta

http://guidelines.health.go.ke/#/category/10/528/meta

