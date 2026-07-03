# Phase Status — Ambulance CDSS

## Phase 0 — Foundation & Decisions

| # | Item | Status | Notes |
|---|---|---|---|
| 0.1 | Confirm dispatch protocol source | **RESOLVED** | In-house authored protocols, reviewed by doctors on the team. Multiple protocols will be provided. |
| 0.2 | Confirm medical director / clinical governance sign-off process | **RESOLVED** | A named doctor + medical director will sign off. Names to be provided per protocol. The `approved_by` / `approved_date` fields in each protocol JSON are the enforcement mechanism — see `docs/GOVERNANCE.md`. |
| 0.3 | Confirm facility registry API contract | **RESOLVED — spec pending** | Confirmed in-house service; real API contract to be provided by the service owner. `app/external/facility_registry.py` is built against a documented interim contract (see file header) — swapping to the real contract is a contained change to that file only. |
| 0.4 | Confirm emergency dispatch/unit-assignment API contract | **RESOLVED — spec pending** | Confirmed in-house service; real API contract to be provided by the service owner. `app/external/emergency_dispatch.py` is built against a documented interim contract (see file header) — same containment. |
| 0.5 | Confirm prehospital drug/item logging scope | **RESOLVED** | Log every relevant drug, item, or unit a unit carries, considers, or administers — logging is unconditional and does not depend on the item being administered. No allowlist/formulary gate. `administered` boolean per row records whether it was actually given. See `app/main.py::add_incident_medication` and `app/models.py::IncidentMedicationGiven`. |
| 0.6 | Repo strategy | **DONE** | New standalone repo at `ambulance-cdss/`, no shared live codebase with the chronic-disease CDSS |
| 0.7 | New Postgres database + FastAPI app shell | **DONE** | See `app/main.py`, `app/db.py`, `app/config.py` |
| 0.8 | Port vendored starting points | **DONE** | `retry.py`, `observability.py` ported and adapted |

**Phase 0 exit criteria:** all confirmations documented; repo running. **Met** — all decisions resolved. The two pending-spec items (0.3, 0.4) are resolved in intent; swapping to the real contracts requires no changes outside `app/external/` once specs arrive.

---

## Phase 1 — Incident Data Model

| # | Item | Status | Notes |
|---|---|---|---|
| 1.1 | `incidents` table | **DONE** | `alembic/versions/0001_incidents.py`, `app/models.py::Incident` |
| 1.2 | `incident_dispatch_log` table | **DONE** | Append-only, immutable transcript |
| 1.3 | `incident_field_log` table | **DONE** | Append-only paramedic-side log |
| 1.4 | `incident_vitals` table | **DONE** | Includes computed score columns written at insert time |
| 1.5 | `incident_medications_given` table | **DONE** | Resolved per 0.5 — logs everything unconditionally; `administered` column (migration 0003) records per-item administration status |
| 1.6 | `guidance_lookup_log` table | **DONE** | Separate from `incident_dispatch_log` by design |
| 1.7 | Alembic migration | **DONE** | Migrations 0001 (all tables), 0002 (field protocol columns), 0003 (administered column on medications) |
| 1.8 | Repository functions | **DONE** | `app/repositories.py` |
| 1.9 | Incident retention policy | **RESOLVED + DONE** | Resolved: 30 days. `INCIDENT_RETENTION_DAYS=30` in `.env.example`, enforced in `validate_startup_config()` in production. `purge_expired_incidents()` in `repositories.py` is implemented (not a stub — purges `caller_location_*` PII fields and stamps `pii_purged_at`). Not yet scheduled — wire to a cron/periodic task before production. |

**Phase 1 exit criteria:** migration runs clean; can create an incident, log a dispatch answer, log a field action, retrieve full assembled incident via one call; retention policy documented and enforced. **Fully met.**

---

## Phase 2 — Protocol Engine (Mode 1)

| # | Item | Status | Notes |
|---|---|---|---|
| 2.1 | `DispatchProtocol`/`ProtocolQuestion`/`TerminalOutcome` schema | **DONE** | `app/protocols/schema.py` |
| 2.2 | Registry loader with governance enforcement | **DONE** | `app/protocols/registry.py` — rejects on missing governance fields or dangling branch targets |
| 2.3 | Locked-mode runner, hard-fail on undefined branch | **DONE** | `app/protocols/runner.py` |
| 2.4 | Mode 2 guidance gate (`allow_guidance_lookup`) on schema | **DONE** | Field on `ProtocolQuestion`; `guidance_note` field carries the fixed author-written content per gated question |
| 2.5 | Author 3 proving protocols with full branch coverage | **DONE** | `cardiac_arrest_unresponsive_v1`, `choking_airway_obstruction_v1`, `major_trauma_mva_v1` — all in `app/protocols/dispatch/`. Carry `approved_by`/`approved_date` PLACEHOLDER values pending medical director sign-off per Phase 0.2 — load and validate but **must not be used in production without real sign-off**. |
| 2.6 | Guidance lookup endpoint, logged separately from dispatch log | **DONE** | `POST /incidents/{id}/guidance-lookup` — writes to `guidance_lookup_log` only; 403s loudly if the question isn't gated |
| 2.7 | Full branch coverage tests + hard-fail test, all 3 protocols | **DONE** | `tests/test_protocol_runner.py`, `tests/test_protocol_choking.py`, `tests/test_protocol_trauma.py`, `tests/test_guidance_note_schema.py` |

**Phase 2 exit criteria met.** Protocols not production-approved until 0.2 sign-off is obtained.

---

## Phase 3 — Dispatch Routing

| # | Item | Status | Notes |
|---|---|---|---|
| 3.1 | Unit dispatch endpoint | **DONE** | `POST /incidents/{id}/dispatch-unit` — degrades explicitly, never raises |
| 3.2 | Facility routing endpoint | **DONE** | `POST /incidents/{id}/route-facility` — degrades explicitly |
| 3.3 | Admission report endpoint | **DONE** | `POST /incidents/{id}/report-admission` |
| 3.3b | Backtracking policy | **RESOLVED + DONE** | Resolved: disallowed on locked (Mode 1) dispatch scripts. Field protocols are unaffected — they were never governance-locked and already permit out-of-order step marking. `can_backtrack()` returns `False`; the 403 path in `submit_incident_answer()` is always taken when `is_backtrack=True`. See `tests/test_backtracking_policy.py`. |
| 3.4 | Dispatcher-side UI | **DONE** | `dispatcher-ui/` — static HTML/CSS/JS, no build step. `config.js` sets the API base URL. |
| 3.5 / 3.6 | Guidance lookup + Mode 2/Mode 1 visual separation in UI | **DONE** | Mode 2 uses a distinct purple colour family, always labelled "INFORMATIONAL ONLY", cannot alter the script |

**Phase 3 exit criteria fully met.**

---

## Phase 4 — Field-Side Protocol Runner

| # | Item | Status | Notes |
|---|---|---|---|
| 4.1 | `FieldProtocol`/`FieldProtocolStep` schema | **DONE** | `app/protocols/schema.py` |
| 4.2 | `field_protocol_id`/`field_protocol_version` columns on `incidents` | **DONE** | `alembic/versions/0002_field_protocol_columns.py` |
| 4.3 | Field protocol registry loader | **DONE** | `app/protocols/field_registry.py` — structural validation only, no governance gate |
| 4.4 | Field runner | **DONE** | `app/protocols/field_runner.py` — does NOT hard-fail on skip/reorder; `rebuild_from_field_log` reconstructs state from the append-only log |
| 4.5 | First field protocol | **DONE** | `field_cardiac_arrest_v1` in `app/protocols/field/` |
| 4.6 | Field protocol endpoints | **DONE** | `GET /field-protocols`, `POST/GET /incidents/{id}/field-protocol*`, `POST /incidents/{id}/field-protocol/step` |
| 4.7 | State reconstruction from `incident_field_log` | **DONE** | `rebuild_from_field_log()` |
| 4.8 | Tests | **DONE** | `tests/test_field_protocol.py` |
| 4.9 | Field-unit-side UI | **DONE** | `field-ui/` — tabs: checklist, vitals (NEWS2/GCS inline), medications, field log, incident summary (renders handoff). `config.js` sets API base URL. |
| 4.10 | Medication logging | **DONE — resolved per 0.5** | `POST /incidents/{id}/medication` accepts any drug/item name, no allowlist gate. `administered` boolean per request. `GET /formulary` returns a deprecated notice (the old gated behaviour is removed) and any configured suggestions from `PREHOSPITAL_FORMULARY` as non-binding convenience names only. |

**Phase 4 exit criteria fully met.**

---

## Phase 5 — Handoff Summary

| # | Item | Status | Notes |
|---|---|---|---|
| 5.1 | Deterministic handoff summary assembly | **DONE** | `app/handoff.py::build_handoff_summary` — no LLM, sourced entirely from `repositories.get_incident_full()` |
| 5.2 | Structured + plain-text dual rendering | **DONE** | `HandoffSummary` dataclass + `text_rendering` from the same call |
| 5.3 | Handoff endpoint | **DONE** | `GET /incidents/{id}/handoff` |
| 5.4 | Highest-severity readings across the encounter | **DONE** | `highest_news2`, `lowest_gcs` computed across full `vitals_history` |
| 5.5 | Consumption from field UI | **DONE** | `field-ui/app.js::refreshIncidentSummary` — primary; falls back to `/full` if no dispatch outcome yet |
| 5.6 | Unit tests | **DONE** | `tests/test_handoff.py` |

**Phase 5 exit criteria fully met.**

---

## Phase 6 — Dashboards

| # | Item | Status | Notes |
|---|---|---|---|
| 6.1 | Active incidents view | **DONE** | `GET /dashboard/active-incidents` — P1 first, oldest first within tier |
| 6.2 | Rolling-window stats | **DONE** | `GET /dashboard/stats?window_hours=N` (default 24, max 168) |
| 6.3 | Read-only, no new write paths | **DONE** | Pure SELECT aggregation over `incidents` table |
| 6.4 | Bounded query cost | **DONE** | `limit` (1–500) and `window_hours` (1–168) validated with 422 on violation |
| 6.5 | Unit tests | **DONE** | `tests/test_dashboard.py` — `_priority_sort` severity order, aggregation logic, status/priority bucketing |
| 6.6 | Dashboard UI | **NOT STARTED** | API-only for now. `dispatcher-ui/`'s transcript sidebar is incident-scoped; a fleet-wide control-room view is a separate future deliverable. |

**Phase 6 exit criteria met for API layer.** Dashboard UI is a future deliverable.

---

## What was deliberately built against an interim assumption (not blocked, but flagged)

- `app/external/facility_registry.py` and `app/external/emergency_dispatch.py`: interim contracts documented in file headers. Swapping to real contracts (0.3/0.4) is a contained change to those two files only.
- Mode 2 `guidance_note` is a fixed, author-written string — not a search/retrieval result. See `docs/OUT_OF_SCOPE.md`.
- `purge_expired_incidents()` is implemented but not scheduled. Wire to a periodic task (e.g. APScheduler, cron, or a Celery beat job) before production deployment.
- All three proving protocols carry PLACEHOLDER sign-off fields. They must be signed by the named medical director (per 0.2) before use in production.
