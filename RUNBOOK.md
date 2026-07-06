# Ambulance CDSS — Operational Runbook

## Quick Start (Docker Compose)

```bash
# 1. Start all services
docker compose up -d

# 2. Verify health
curl http://localhost:8000/health    # ambulance-cdss
curl http://localhost:8100/health    # triage-ranker
curl http://localhost:8001/health    # facility-mapper
```

## Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Ambulance CDSS API | `http://localhost:8000` | Main incident record + dispatch engine |
| Dispatcher Console | `http://localhost:5500` | Dispatcher static UI (open dispatcher-ui/index.html) |
| Field Console | `http://localhost:5501` | Paramedic field UI (open field-ui/index.html) |
| Triage Ranker | `http://localhost:8100` | Clinical triage enrichment |
| Facility Mapper | `http://localhost:8001` | Geospatial facility routing |
| PostgreSQL | `localhost:5432` | Shared database |

## First-Time Setup

### Load Facility Data
```bash
# After first docker compose up, load Kenya health facility data:
docker compose exec facility-mapper python -m scripts.load_facilities --source /path/to/data.csv
```

### Reload Protocols
```bash
# Hot-reload dispatch and field protocol registries without restart:
curl -X POST http://localhost:8000/admin/reload-protocols \
  -H "X-Admin-Key: your-admin-key"
```

### Reload Clinical Rules (Triage Ranker)
```bash
# Reload clinical_rules.yaml without restart:
curl -X POST http://localhost:8100/admin/rules/reload \
  -H "X-Admin-Key: your-admin-key"
```

## Manual Operations

### Run PII Purge Manually
```bash
curl -X POST http://localhost:8000/admin/purge-expired-incidents \
  -H "X-Admin-Key: your-admin-key"
```

### Check Purge Status
```bash
curl http://localhost:8000/admin/purge-status \
  -H "X-Admin-Key: your-admin-key"
```

### Protocol Audit (Sign-Off Status)
```bash
curl http://localhost:8000/admin/protocol-audit \
  -H "X-Admin-Key: your-admin-key"
```

## Troubleshooting

### Service won't start
1. Check `docker compose logs <service-name>` for errors
2. Verify `.env` files are configured (copy from `.env.example`)
3. Ensure PostgreSQL is healthy: `docker compose ps postgres`

### Protocols not loading
- Check `GET /admin/protocol-status` — rejected protocols show rejection reasons
- Ensure `approved_by` is not a placeholder value ("Dev Setup", "TBD", etc.)
- See `docs/SIGN_OFF_CHECKLIST.md` for the sign-off process

### Facility Mapper returns empty results
- Verify facility data has been loaded: `GET /data-currency`
- Check BallTree is built: `GET /ready` should return 200
- Reload if needed: `POST /admin/reload-facilities`

### Triage Ranker degraded mode
- This is normal when UMLS API is not configured
- System falls back to local regex + clinical rules extraction
- Check `GET /health` for UMLS reachability status

## Environment Variables

See `postgres.env.example` for database credentials.
Each service has its own `.env.example` — copy to `.env` and configure.

### Key Variables
- `DATABASE_URL` — PostgreSQL connection string
- `ENVIRONMENT` — `development` or `production`
- `DISPATCHER_CREDENTIALS` — JSON of dispatcher username:pin_hash pairs
- `ADMIN_API_KEY` — API key for /admin/* endpoints
- `PURGE_SCHEDULE_ENABLED` — Enable automatic PII purge scheduler
- `TRIAGE_RANKER_BASE_URL` — URL of the triage-ranker service
- `FACILITY_REGISTRY_BASE_URL` — URL of the facility-mapper service
