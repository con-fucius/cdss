# E911 / AML Webhook Contract

## Overview

The Ambulance CDSS accepts E911 (Enhanced 911) and AML (Advanced Mobile Location)
location push payloads via a single webhook endpoint. This document defines the
contract so that telephony carriers, PSAP (Public Safety Answering Point) systems,
and AML gateway vendors can integrate.

---

## Endpoint

```
POST /intake/e911-push
Content-Type: application/json
```

**No authentication required** — this endpoint is called by trusted infrastructure
(carrier gateway, PSAP integration). IP allowlisting or mTLS should be configured
at the network layer.

---

## Request Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `lat` | `float` | **yes** | WGS84 latitude (decimal degrees, e.g. `-1.2921`) |
| `lon` | `float` | **yes** | WGS84 longitude (decimal degrees, e.g. `36.8219`) |
| `caller_number` | `string` | no | Calling party number (E.164 format preferred) |
| `accuracy_m` | `float` | no | Location accuracy radius in metres (lower is better) |
| `incident_id` | `string` | no | If known, links location to an existing incident |
| `chief_complaint` | `string` | no | Caller's reported problem; used for protocol matching |

### Example Request

```json
{
  "lat": -1.2921,
  "lon": 36.8219,
  "caller_number": "+254700123456",
  "accuracy_m": 25.0,
  "chief_complaint": "chest pain"
}
```

---

## Response Schema

### Location update to existing incident (`incident_id` provided)

```json
{
  "incident_id": "550e8400-e29b-41d4-a716-446655440000",
  "created": false
}
```

### New incident created (`incident_id` omitted)

```json
{
  "incident_id": "550e8400-e29b-41d4-a716-446655440001",
  "created": true,
  "protocol_matched": true
}
```

---

## Behaviour

1. **With `incident_id`**: Updates the existing incident's `caller_location_lat`,
   `caller_location_lon`, and `location_accuracy_m` columns. Returns `created: false`.

2. **Without `incident_id`**: Creates a new incident with status `received`. If
   `chief_complaint` is provided, attempts protocol matching against the locked
   dispatch protocol registry. Stores `location_accuracy_m` on the new incident.

3. **Error responses**:
   - `404` — incident not found (when `incident_id` provided but unknown)
   - `422` — validation error (missing `lat`/`lon`, invalid types)

---

## Integration Notes

- The `accuracy_m` value is persisted on the `incidents` table column
  `location_accuracy_m` (Alembic migration `0008_transcript_and_accuracy_columns`).
- If the caller is already in a call with the dispatcher, the dispatcher should
  pass the `incident_id` to link the E911 location to the active incident.
- Protocol matching uses the same chief complaint matching logic as
  `POST /incidents` — see `registry.match_by_chief_complaint()`.
