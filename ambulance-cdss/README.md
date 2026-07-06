# Ambulance CDSS

Emergency dispatch and prehospital field care clinical decision support system.

## Architecture

Four consoles sharing one incident record:

- **Dispatcher** (`dispatcher-ui/`) — call intake with Leaflet map, NLP entity extraction, locked-script protocol runner, pre-arrival instructions (bilingual EN/SW), facility routing, handoff delivery
- **Field** (`field-ui/`) — paramedic protocol checklist, vitals with NEWS2/GCS scoring, medication logging, voice commands, GPS tracking, facility routing
- **Receiving** (`receiving-ui/`) — incoming patient alert with ETA countdown, handoff summary, ER acknowledgement, live SSE updates
- **Admin** (`admin-ui/`) — system status, protocol governance, audit log, cache health

Backend: FastAPI + PostgreSQL + Redis. Frontends: plain HTML/CSS/JS, no build step.

## Quick Start

> **Note:** Update paths below to match your local clone location.

```bash
# Terminal 1 — Docker infrastructure
docker run -d --name ambulance-postgres -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ambulance_cdss_dev -p 5432:5432 postgres:16-alpine
docker run -d --name ambulance-redis -p 6379:6379 redis:alpine

# Terminal 2 — Backend (from ambulance-cdss/)
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 3+ — Frontends (open in browser or serve with any static server)
# Windows:
start dispatcher-ui\index.html
start field-ui\index.html
start admin-ui\index.html
# Receiving UI needs incident params:
# receiving-ui/index.html?id={incident_id}&token={token}

# Linux/Mac:
open dispatcher-ui/index.html
open field-ui/index.html
open admin-ui/index.html
```

**Docker services:** PostgreSQL on `localhost:5432`, Redis on `localhost:6379`.
**Backend:** `http://localhost:8000` (API + admin dashboard).
**Frontends:** open HTML files directly or serve via `python -m http.server` in each directory.

## Key Features

### Protocol RAG
Hybrid retrieval: MedSpaCy entity extraction + keyword mapping + TF-IDF similarity.
Matches chief complaints to protocols in <100ms. No LLM in the dispatch path.

### Clinical Scoring
NEWS2, GCS, PEWS, Revised Trauma Score, Shock Index. Deterioration detection
triggers alerts when NEWS2 increases by >=3 points between readings.

### Structured Notes
Auditable, cross-visible notes between dispatcher and field. Every note has
timestamp, author, role, and type. Append-only design preserves complete audit trail.

### County Referral Routing
KEPH levels 1-6, county filtering, triage-based routing (P1→Level 4+),
hospital diversion exclusion, facility stock awareness.

### Multi-Casualty Support
Single incident with multiple casualty slots, individual triage scores,
unified dispatch for multiple units.

### Offline-First
Write queue with chronological sync, 409 conflict handling, protocol state
merge on reconnection. Works during network dropouts.

## Testing

```bash
# Comprehensive API tests (106 tests)
python tests/comprehensive_tests.py

# Homestretch tests (56 tests)
python tests/homestretch_tests.py

# Functional user-journey tests (38 tests)
python tests/functional_tests.py
```

## Out of Scope

See `docs/OUT_OF_SCOPE.md` — differential diagnosis, UMLS normalization,
longitudinal patient state, CDS Hooks, broad formulary checking are
deliberately excluded.
