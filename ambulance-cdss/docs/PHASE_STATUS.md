# Phase Status — Ambulance CDSS

## Current State (July 2026)

### Infrastructure
- PostgreSQL 16 via Docker, 14 Alembic migrations
- Redis via Docker, graceful degradation
- FastAPI backend, 50+ endpoints, structured logging
- 4 UIs: dispatcher, field, receiving, admin

### What's Working
- Full incident lifecycle: create → protocol → vitals → medication → handoff → close
- Field protocol runner with step completion tracking
- MedSpaCy clinical NLP with regex fallback (negation, Swahili, Sheng)
- Protocol RAG: keyword + TF-IDF hybrid matching
- Clinical scoring: NEWS2, GCS, PEWS, RTS, Shock Index
- Deterioration detection on successive vitals
- SSE streaming for real-time updates
- Redis caching with graceful degradation
- Structured audit logging (incident_notes table)
- Cross-visible notes (dispatcher ↔ field)
- Multi-casualty support
- Hazard zone awareness
- Hospital diversion exclusion
- Facility stock availability
- County referral network (KEPH levels 1-6)
- Next-of-kin notification (SMS placeholder)
- Weekly incident pattern reporting
- Offline-first write queue with conflict resolution
- Autocomplete suggestions for complaints, medications, addresses
- Dashboard with search, filter, sort, pagination, P1 alerts

### Test Coverage
- 106 comprehensive API tests
- 56 homestretch tests
- 38 functional user-journey tests
- 97 deep backend tests

---

## Migrations

| # | Name | Purpose |
|---|------|---------|
| 0001 | incidents | Core incident data model |
| 0002 | field_protocol_columns | Field protocol selection |
| 0003 | administered_column | Medication administration flag |
| 0004 | eta_and_notes_columns | ETA and notes |
| 0005 | supersede_unit | Dispatch log supersede, unit location |
| 0006 | triage_enrichment | Triage enrichment JSONB |
| 0007 | merge_heads | Branch cleanup |
| 0008 | transcript_accuracy | Transcript text, location accuracy |
| 0009 | audit_events | Audit event log |
| 0010 | casualties | Multi-casualty incident slots |
| 0011 | widen_consciousness | AVPU column width |
| 0012 | next_of_kin | Next-of-kin fields |
| 0013 | structured_notes | Structured notes table |

---

## API Endpoints (50+)

**Health**: GET /health, GET /metrics
**Auth**: POST /auth/dispatcher-login
**Incidents**: POST/GET /incidents, GET /incidents/{id}, GET /incidents/{id}/full, GET /incidents/{id}/timeline
**Protocol**: POST /incidents/{id}/answer, PATCH /incidents/{id}/answer/{log_id}, POST /incidents/{id}/select-protocol
**Field Protocol**: POST /incidents/{id}/field-protocol, GET /incidents/{id}/field-protocol/state, POST /incidents/{id}/field-protocol/step
**Clinical**: POST /incidents/{id}/vitals, POST /incidents/{id}/medication, POST /incidents/{id}/field-log
**Notes**: PATCH /incidents/{id}/notes, GET /incidents/{id}/notes
**Transcript**: PATCH /incidents/{id}/transcript
**Routing**: POST /incidents/{id}/route-facility, POST /incidents/{id}/dispatch-unit
**Handoff**: GET /incidents/{id}/handoff, GET /incidents/{id}/handoff-link, GET /incidents/{id}/export
**Status**: POST /incidents/{id}/status
**Location**: POST /incidents/{id}/unit-location, GET /incidents/{id}/unit-location/latest
**SSE**: GET /incidents/{id}/stream
**NLP**: POST /triage/extract-entities, POST /scoring/compute
**Dashboard**: GET /dashboard/active-incidents, GET /dashboard/stats, GET /dashboard/shift-handover
**E911**: POST /intake/e911-push
**Multi-casualty**: POST/GET/PATCH/DELETE /incidents/{id}/casualties
**Facilities**: POST/GET /facilities/{id}/diversion, POST/GET /facilities/{id}/stock
**Hazard Zones**: GET/POST/DELETE /hazard-zones
**Reports**: GET /reports/weekly, GET /reports/weekly/text
**Next-of-Kin**: POST /incidents/{id}/notify-next-of-kin
**Correction**: POST /incidents/{id}/correction
**Admin**: GET /admin/system-status, GET /admin/audit-log, GET /admin/protocol-status, GET /admin/protocol-audit, GET /admin/governance-status, GET /admin/cache-health, POST /admin/reload-protocols
**Protocols**: GET /protocols, GET /field-protocols, GET /protocols/match
