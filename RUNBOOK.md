# RUNBOOK — Ambulance CDSS Ecosystem

## Architecture

```
D:\Projects\CDSS\
├── ambulance-cdss/          ← Authoritative incident record + dispatch engine (port 8000)
├── facility-mapper/         ← Geospatial facility routing service (port 8001)
├── triage-ranker/           ← NLP clinical enrichment service (port 8100)
├── shared/                  ← Shared Pydantic contract schemas
└── compose.yaml             ← Docker Compose orchestration
```

## Startup Sequence

```bash
# 1. Start all services
docker compose up -d

# 2. Verify health
curl http://localhost:8000/health    # Ambulance CDSS
curl http://localhost:8001/health    # Facility Mapper
curl http://localhost:8100/health    # Triage Ranker

# 3. Check readiness (facility mapper may return 503 until BallTree built)
curl http://localhost:8001/ready
```

## Facility Data Load

The Facility Mapper starts empty. Load facility data after first run:

```bash
# Copy facility data into the container
docker compose cp data/facilities.csv facility-mapper:/data/facilities.csv

# Load facilities (idempotent — second run produces same count, no duplicates)
docker compose exec facility-mapper \
  python -m scripts.load_facilities \
  --source /data/facilities.csv \
  --source-name KMHFL_2024_02

# Verify loaded count
curl http://localhost:8001/health
# Should show: {"facility_count": N, "data_as_of": "KMHFL_2024_02", ...}
```

### Data Source
- Kenya Master Health Facility List (KMHFL) is the recommended source for Kenya
- Data currency cadence: quarterly refresh recommended
- Invalid coordinates (outside Kenya/Uganda/DRC bounds) are rejected with a count in the log

## Protocol Reload

Hot-reload both dispatch and field protocol registries without server restart:

```bash
# Reload all protocols
curl -X POST http://localhost:8000/admin/reload-protocols \
  -H "X-Admin-Key: your-admin-key"

# Check protocol status
curl http://localhost:8000/admin/protocol-status \
  -H "X-Admin-Key: your-admin-key"
```

**Important:** Existing in-progress incidents are unaffected — their `dispatch_protocol_snapshot` (taken at incident creation) is the source of truth, not the live registry.

## Clinical Rules Reload

Reload clinical_rules.yaml in the Triage Ranker without restart:

```bash
# Reload rules
curl -X POST http://localhost:8100/admin/rules/reload \
  -H "X-Admin-Key: your-admin-key"

# Purge UMLS caches (if needed)
curl -X DELETE http://localhost:8100/admin/cache \
  -H "X-Admin-Key: your-admin-key"
```

## Manual Purge Trigger

Trigger the PII retention purge (default: 30 days after incident closure):

```bash
curl -X POST http://localhost:8000/admin/purge-expired-incidents \
  -H "X-Admin-Key: your-admin-key"
```

This is not wired to a scheduler — call manually or from an external cron job.

## Running Tests

### Ambulance CDSS unit tests
```bash
cd ambulance-cdss
uv run pytest tests/ -v
```

### Facility Mapper tests (requires seeded DB)
```bash
cd facility-mapper
uv run pytest tests/ -v
```

### Triage Ranker tests (requires spaCy model)
```bash
cd triage-ranker
uv run pytest tests/ -v
```

### Integration tests (requires DATABASE_URL)
```bash
cd ambulance-cdss
DATABASE_URL=postgresql+asyncpg://... uv run pytest tests/integration/ -v
```

## Service URLs (Docker Compose)

| Service | Internal URL | External URL |
|---------|-------------|-------------|
| Ambulance CDSS | http://ambulance-cdss:8000 | http://localhost:8000 |
| Facility Mapper | http://facility-mapper:8001 | http://localhost:8001 |
| Triage Ranker | http://triage-ranker:8100 | http://localhost:8100 |
| PostgreSQL | postgres:5432 | localhost:5432 |

## Troubleshooting

### Facility Mapper returns empty results
1. Check `/health` — is `facility_count` > 0?
2. If 0, run the data loader (see "Facility Data Load" above)
3. If BallTree not ready, check `/ready` endpoint

### Triage Ranker in degraded mode
1. Check `/health` — is `spacy_model_loaded` true?
2. If false, the spaCy model was not downloaded in the Docker build
3. Rebuild: `docker compose build triage-ranker`

### Protocol reload shows rejections
1. Check `/admin/protocol-status` for rejection reasons
2. Common causes: missing governance fields, PLACEHOLDER approval names
3. Fix the protocol JSON file, then reload

### Incidents not matching any protocol
1. Check `/protocols` — is the expected protocol in `active`?
2. If in `rejected`, fix the protocol file
3. Check `chief_complaint_trigger` — does it include the caller's language?
