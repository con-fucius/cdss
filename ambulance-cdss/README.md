# Ambulance CDSS

A separate, purpose-built clinical decision support system for **emergency dispatch and prehospital field care**, distinct from the chronic-disease guideline CDSS at `../HIV-agent`.

## What this is

One shared incident record, two purpose-built consoles:

- **Dispatcher console** (`dispatcher-ui/`) — fixed, validated, criteria-based dispatch scripts ("Mode 1 — Locked"), with narrowly bounded supplementary guideline lookups at explicitly pre-approved insertion points only ("Mode 2 — Guidance"). Out-of-script answers are hard, loud, visible errors — never silently defaulted.
- **Field console** (`field-ui/`) — paramedic-facing field protocol checklist runner, vitals entry with server-computed NEWS2/GCS, unconditional drug/item logging (carried, considered, or administered — see Phase 0.5 in `docs/GOVERNANCE.md`), free-form field log entries, and one-tap handoff summary retrieval.

Both consoles are plain HTML/CSS/JS with no framework and no build step — deliberate, see each `app.js` file header. There is nothing to compile; serve the directory as static files.

## What this is not

This is not the chronic-disease CDSS narrowed down. It is a new system. Nothing here assumes longitudinal patient state, multi-visit history, differential diagnosis workspaces, or a six-disease evidence graph. See `docs/OUT_OF_SCOPE.md` for the full list of deliberate exclusions.

## Repository layout

```
ambulance-cdss/
├── app/
│   ├── main.py                       FastAPI app entrypoint — all HTTP endpoints
│   ├── config.py                     Environment & settings, including resolved
│   │                                 Phase 0/1 decisions (retention days, etc.)
│   ├── db.py                         Async Postgres session management
│   ├── models.py                     SQLAlchemy ORM models (incidents + all logs)
│   ├── repositories.py               Data access layer — one function per write
│   │                                 path, plus dashboard queries and the
│   │                                 retention/purge job
│   ├── handoff.py                    Phase 5 — deterministic handoff summary
│   │                                 (no LLM; structured + plain-text rendering)
│   ├── retry.py                      Retry/timeout helpers
│   ├── observability.py              Metrics + rate limiting middleware
│   ├── protocols/
│   │   ├── schema.py                 DispatchProtocol / ProtocolQuestion /
│   │   │                             TerminalOutcome / FieldProtocol /
│   │   │                             FieldProtocolStep dataclasses
│   │   ├── registry.py               Mode 1 registry loader — enforces locked /
│   │   │                             approved_by / approved_date / non-placeholder
│   │   │                             governance fields and branch integrity
│   │   ├── runner.py                 Mode 1 locked-script runner — hard-fails on
│   │   │                             any undefined answer; never guesses
│   │   ├── field_registry.py         Field protocol loader (Phase 4) — structural
│   │   │                             validation only, no governance gate
│   │   ├── field_runner.py           Field checklist state, reconstructed from
│   │   │                             incident_field_log — no hard-fail on
│   │   │                             skip/reorder (see module docstring)
│   │   ├── dispatch/                 Mode 1 protocol JSON definitions
│   │   └── field/                    Field protocol JSON definitions
│   ├── scoring/
│   │   └── scorers.py                NEWS2, GCS — prehospital-relevant only,
│   │                                 see docs/OUT_OF_SCOPE.md for what is
│   │                                 deliberately excluded (Child-Pugh, CVD
│   │                                 risk charts, etc.)
│   └── external/
│       ├── facility_registry.py      Live facility registry client
│       └── emergency_dispatch.py     Live emergency dispatch/unit client
├── alembic/                          Migrations
│   └── versions/
│       ├── 0001_incidents.py
│       ├── 0002_field_protocol_columns.py
│       └── 0003_administered_column.py
├── dispatcher-ui/                    Static dispatcher console (no build step)
├── field-ui/                         Static field console (no build step)
├── tests/                            pytest suite — see "Running tests" below
└── docs/
    ├── OUT_OF_SCOPE.md               What this system deliberately does not do
    ├── GOVERNANCE.md                 Mode 1 / Mode 2 boundary, protocol
    │                                 authorship and sign-off rules
    └── PHASE_STATUS.md               Live tracking of every phase item and
                                       every open decision
```

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for the backend
- PostgreSQL 14+ (a local instance or container — there is no bundled docker-compose in this repo; provision one yourself and point `DATABASE_URL` at it)
- Any static file server for the two consoles (a one-line Python server is enough — see below)

## Backend setup

From the `ambulance-cdss/` directory:

```bash
# 1. Create and activate a virtual environment, install dependencies
uv venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

uv pip install -e ".[dev]"

# 2. Configure environment
cp .env.example .env
# Edit .env: set DATABASE_URL to your real Postgres instance.
# FACILITY_REGISTRY_BASE_URL and EMERGENCY_DISPATCH_BASE_URL are optional —
# the system runs and degrades gracefully (explicit "manual action required"
# responses, never silent failure) if either is left unset. See
# docs/PHASE_STATUS.md items 0.3/0.4 and each client's module docstring in
# app/external/.

# 3. Run database migrations
uv run alembic upgrade head

# 4. Start the API
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API is now at `http://localhost:8000`. Confirm with:

```bash
curl http://localhost:8000/health
```

A healthy response looks like:

```json
{
  "status": "degraded",
  "database": "ok",
  "active_protocols": 0,
  "rejected_protocols": 3,
  "backtracking_permitted": false
}
```

**`active_protocols: 0` and `rejected_protocols: 3` is currently expected, not a bug.** The three shipped dispatch protocol JSON files (`cardiac_arrest_unresponsive_v1`, `choking_airway_obstruction_v1`, `major_trauma_mva_v1`) carry placeholder governance text pending real named doctor and medical director sign-off — see "Open decisions" below and `docs/GOVERNANCE.md`. They are correctly rejected at load time rather than silently treated as approved. Inspect the rejection reasons with:

```bash
curl http://localhost:8000/protocols
```

Once real `approved_by` / `approved_date` values are filled into those three JSON files (in `app/protocols/dispatch/`), restart the API and they will load as active.

## Serving the consoles

Both `dispatcher-ui/` and `field-ui/` are static directories — no `pnpm`, no `npm`, no build step, because neither has a `package.json` or any build tooling. Serve each with any static file server. The simplest option, from inside each directory:

```bash
# Dispatcher console
cd dispatcher-ui
python -m http.server 5500

# Field console (in a separate terminal)
cd field-ui
python -m http.server 5501
```

Then open `http://localhost:5500` (dispatcher) and `http://localhost:5501` (field) in a browser.

By default both consoles call the API at `http://localhost:8000`. To point at a different API host, set the global before `app.js` loads — for example, add this to `index.html` just above the `<script src="app.js">` tag:

```html
<script>window.AMBULANCE_CDSS_API_BASE = "http://your-api-host:8000";</script>
```

## Running tests

```bash
uv run pytest
```

All tests run against pure logic (protocol parsing, branch walking, scoring, handoff rendering, field checklist state) with no live database required — see each test file's module docstring for what is and is not covered this way, and `docs/PHASE_STATUS.md` for what is exercised manually via HTTP instead (e.g. `GET /incidents/{id}/full`, `GET /dashboard/*`).

## Status

Phases 0 through 6 have backend and (where applicable) UI work in place. See `docs/PHASE_STATUS.md` for the authoritative, item-by-item status of every phase, including which items are fully closed and which remain open pending a decision from outside engineering.

## Open decisions

These are the seven items raised during planning that this codebase is built and waiting on. Engineering has proceeded against documented interim assumptions everywhere possible; nothing is blocked from compiling or running, but the following must be resolved by the relevant people before this system is fit for a real incident:

1. **Dispatch protocol source** — Is this organization adapting an existing licensed criteria-based dispatch system, or authoring protocols in-house from scratch?
2. **Medical director sign-off process** — Who signs off on a protocol before it is marked `locked: true`, and how is that approval recorded? (Mechanically: the protocol JSON's `approved_by` and `approved_date` fields — see `docs/GOVERNANCE.md`.)
3. **Facility registry API contract** — What is the real request/response shape for the facility registry service this system calls to find the nearest appropriate facility?
4. **Emergency dispatch / unit-assignment API contract** — What is the real request/response shape for the service this system calls to assign and notify a responding unit?
5. **Prehospital drug/item formulary** — Resolved: every relevant drug or item a unit carries, considers, or administers must be logged, with no allowlist gate and regardless of whether it was actually given (see `app/config.py::get_prehospital_formulary` for the now-deprecated allowlist mechanism this replaced).
6. **Incident retention duration** — Resolved: 30 days after closure before caller-location PII fields are purged (`INCIDENT_RETENTION_DAYS`).
7. **Backtracking policy on locked dispatch scripts** — Resolved: disallowed on Mode 1 (locked) scripts; permitted on field protocols, which were never governance-locked in the first place (`app/protocols/runner.py::can_backtrack`).

Items 1–4 remain open and are the responsibility of, respectively, the clinical/medical leadership team (1, 2) and the teams owning the two external services (3, 4).
