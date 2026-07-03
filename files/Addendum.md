TOOL SELECTION VERDICT



\*\*LanceDB stays exactly where it is.\*\* Retrieval via approximate nearest-neighbour search across six disease tables. Nothing changes about its role — it already does this correctly in the code. The bugs are in the indexing call and the async wrappers, not the architecture.



\*\*PageIndex fills the gap LanceDB genuinely cannot close.\*\* PageIndex builds a hierarchical tree index from documents and uses an LLM to reason over that tree, the way a human expert would scan a table of contents to find the right section — there is no vector database and no chunking in the pipeline. This matters for Kenya guidelines because dosing tables, TB treatment algorithms, and diagnostic criteria are structural, not semantic. A question like "what is the weight-based rifampicin dose for a 22kg child?" is a navigation problem, not a similarity problem. PageIndex costs LLM API tokens on every retrieval step — this is real and is addressed by a query routing function that only sends structurally complex queries to PageIndex and keeps broad exploratory queries on LanceDB.



\*\*Postgres owns everything durable.\*\* Audit logs (migrating from SQLite), evidence graph edges, the session memory table. PostgreSQL is the more defensible default when teams need durable state, auditability, and operational simplicity — it is often less defensible to choose Redis once the workload becomes business-critical.



\## The memory architecture is the new core decision



The plan defines four levels precisely matched to the clinical setting — a Level 4-6 Kenyan hospital with returning patients across encounters:



\*\*Level 1\*\* (within-session turns): the existing `deque` in `api.py` — correct, keep it.



\*\*Level 2\*\* (per-request patient context): the existing `PatientContext` — correct, but the injection method is wrong (JSON dump prepended as a string rather than structured context). Fix it.



\*\*Level 3\*\* (cross-session patient memory): a new `session\_memory` Postgres table. At the end of every exchange, a cheap model extracts structured clinical facts (active regimen, lab results, contraindications noted, monitoring due) and writes them tagged to an anonymised patient reference. On the next session for the same patient, those memories are retrieved and injected before the query runs. This is not a framework. It is a well-designed table and fifteen lines of extraction logic.



\*\*Level 4\*\* (system-wide validated clinical knowledge): the evidence graph — already specified, not repeated.



\## Eight new issues found in this read (61–68)



The most operationally dangerous: the Groq and Puter paths silently discard all session history on every request (Issue 61), `check\_guideline\_updates` is a one-shot call not a timer loop (Issue 63), and the `CDSS\_PATIENT\_SALT` default has no production guard (Issue 68 — a patient safety concern).



\## Where to pick up



Gate 0 (Tiers 0–1 from the first plan) must complete before touching any of this. Then Gates 1 through 4 in the document above: Postgres migration → PageIndex integration → memory system → evidence graph seeding. The sequence is strict. Nothing in Gate 2 begins until a full end-to-end query works.





CORRECTION



The Groq and Puter paths are explicitly structured differently from the Mistral/pydantic-ai path for a reason that is visible in the code itself: they bypass the agent entirely and call `\_run\_openai\_compatible\_chat` directly. This is not an accident or oversight — it is a deliberate parallel path for provider testing that does not need the full agent loop. Flagging "history is discarded" as a bug ignores that these are testing paths where stateless single-turn behaviour is precisely what you want when isolating provider output quality.



Similarly, `check\_guideline\_updates` being one-shot at startup rather than a timer loop is arguably correct during active development — you want a single check on boot to log URL validity, not a noisy background task hammering potentially broken URLs every 60 seconds while you're fixing other things. The comment about "check every minute" is a future intent note, not a broken implementation.



Let me go through everything I flagged with the same critical eye and distinguish what is actually wrong from what is intentional design that I misread.



\*\*Issues that were genuine bugs and stand:\*\*



\- \*\*61 — wrong.\*\* History discarded in Groq/Puter paths is intentional. These are provider test paths. Withdraw it.

\- \*\*62 — stands.\*\* `\_pool` being `None` silently dropping memory writes is a real race condition on fast startup. A readiness flag costs nothing and prevents silent data loss.

\- \*\*63 — wrong.\*\* One-shot guideline URL check is appropriate for dev. Withdraw it.

\- \*\*64 — stands, but reframed.\*\* The Groq default model `qwen/qwen3-32b` producing inline reasoning without `reasoning\_format` is a real misconfiguration that will corrupt frontend output. This is not about the testing path — it affects what a real user sees. The fix is either a documented env var requirement or a safer default model.

\- \*\*65 — stands as a note, not a bug.\*\* Single-worker assumption is fine for now. Worth documenting for deployment.

\- \*\*66 — stands.\*\* `medications: Optional\[str]` genuinely should be `List\[str]` for the memory extraction to produce useful structured data. A comma-separated string makes downstream parsing fragile for a field the memory system will depend on.

\- \*\*67 — stands.\*\* `build\_pageindex()` is synchronous and CPU-bound. If it ever gets called from the lifespan or any async context it will block the event loop. The fix is one line.

\- \*\*68 — stands.\*\* No production guard on `CDSS\_PATIENT\_SALT` is a real patient safety concern. A default salt makes patient references deterministic and computable by anyone.



\*\*From the original 50 issues — ones I should reconsider:\*\*



Issue 31 (agent action log hardcoded) — after reading `api.py` properly, `activity` events are already being emitted dynamically from `run\_stream()` for each real step. The "hardcoded" label was inaccurate. The Groq/Puter path emits real activity events. The Mistral path does too. This is not broken — it was already fixed before the issues list was written.



Issue 46 (handleNewConversation doesn't generate new session ID) — this may also be intentional. Keeping the session ID stable across "new conversation" clears the server-side history but preserves the client's identity for audit continuity. Whether this is right depends on the UX intent. Worth raising as a design question, not flagging as a bug.



The core discipline here: reading code critically means asking "why is it this way" before "is it wrong." Parallel provider paths, one-shot startup checks, and deliberate statelessness in test paths are all signs of intentional design, not negligence. I should have applied that test before listing them.







IMPLEMENTATION PLAN REVIEW



Eight phases, strict gates, every item mapped to a specific file and function. A few decisions worth highlighting:



\*\*Phase 0 is the longest.\*\* Twelve subsections covering thirteen distinct files. This is correct — the foundation has more broken surface area than any subsequent phase. Nothing in Phase 1+ is structurally complex; almost everything there is additive. Phase 0 is the hard part.



\*\*The `providers.py` extraction (Phase 1.3) is load-bearing.\*\* It is listed under Postgres migration because it is needed to fix the HyDE endpoint (Phase 0.8), and doing it as part of Phase 1 avoids doing it twice. It also eliminates the circular import risk if `search\_tools.py` ever needs to call `api.py` helpers directly.



\*\*Phase 4 (evidence graph) and Phase 3 (memory) are parallel\*\* after Phase 2 passes. Neither blocks the other. If you have a second engineering context, these can run simultaneously.



\*\*The edge seeding scripts stay in `draft` status deliberately.\*\* Every edge going into `clinical\_edges` for a Kenya deployment needs a clinician to read the actual guideline page it claims to cite before it gets marked `validated`. The scripts automate the data entry; the review is human. This is not a workflow detail — it is the difference between a CDSS and a liability.



\*\*Phase 8 exists and is non-optional.\*\* The patient salt assertion, the single-worker documentation, the Alembic initialisation, and the malaria guideline upgrade are all things that will be skipped under time pressure and will cause problems the first week of clinic use.

