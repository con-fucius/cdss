# Ambulance CDSS

Emergency dispatch and prehospital field care clinical decision support system.

## Architecture

Four consoles sharing one incident record:

- **Dispatcher** (`dispatcher-ui/`) — call intake with Leaflet map, NLP entity extraction, locked-script protocol runner, pre-arrival instructions (bilingual EN/SW), facility routing, handoff delivery
- **Field** (`field-ui/`) — paramedic protocol checklist, vitals with NEWS2/GCS scoring, medication logging, voice commands, GPS tracking, facility routing
- **Receiving** (`receiving-ui/`) — incoming patient alert with ETA countdown, handoff summary, ER acknowledgement, live SSE updates
- **Admin** (`admin-ui/`) — system status, protocol governance, audit log, cache health

Backend: FastAPI + PostgreSQL + Redis. Frontends: plain HTML/CSS/JS, no build step.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/con-fucius/cdss.git
cd cdss/ambulance-cdss

# Create virtual environment (Python 3.11+)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# Install dependencies
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
copy .env.example .env        # Windows
# cp .env.example .env        # Linux/Mac
```

Edit `.env` — the defaults work for local development with Docker services below.

### 3. Start infrastructure (Terminal 1)

```bash
docker run -d --name ambulance-postgres -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ambulance_cdss_dev -p 5432:5432 postgres:16-alpine
docker run -d --name ambulance-redis -p 6379:6379 redis:alpine
```

### 4. Start backend (Terminal 2)

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Backend runs at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

### 5. Open frontends (Terminal 3+)

Open these HTML files in your browser:

| Console | Path | Notes |
|---------|------|-------|
| Dispatcher | `dispatcher-ui/index.html` | Login with any username/PIN |
| Field | `field-ui/index.html` | Login with any Unit ID/PIN |
| Admin | `admin-ui/index.html` | No login required |
| Receiving | `receiving-ui/index.html?id=INCIDENT_ID&token=TOKEN` | Get URL from dispatcher handoff |

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
