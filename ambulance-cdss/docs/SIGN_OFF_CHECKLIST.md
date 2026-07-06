# Protocol Sign-Off Checklist

## Purpose

This checklist ensures every dispatch protocol file has undergone proper clinical
governance review before being loaded into the active registry. Protocols with
placeholder governance values ("Dev Setup", "TBD", etc.) are **blocked from
loading** at startup — see `DispatchProtocol._BLOCKED_GOVERNANCE_VALUES` in
`app/protocols/schema.py`.

---

## Required Fields in Protocol JSON

| Field | Description | Valid Example |
|-------|-------------|---------------|
| `protocol_id` | Unique identifier | `cardiac_arrest_unresponsive_v1` |
| `version` | Semantic version | `1.0.0` |
| `approved_by` | Name and title of medical director | `Dr. Jane Mwangi, Medical Director` |
| `approved_date` | ISO date of approval | `2026-06-15` |
| `disease_or_presentation` | Human-readable clinical presentation | `Cardiac arrest — unresponsive, no pulse` |

---

## Blocked Governance Values

The following values in `approved_by` or `approved_date` will cause the protocol
to be **rejected** at startup:

```
dev setup, tbd, todo, draft, placeholder, test, example,
not approved, pending, unknown, <none>, n/a, na, null
```

---

## Sign-Off Process

### Step 1: Draft Protocol
- Write the protocol JSON file in `app/protocols/dispatch/` or `app/protocols/field/`
- Use placeholder values for `approved_by` and `approved_date`
- Test locally — the protocol will be **blocked** in production but usable in dev

### Step 2: Clinical Review
- Medical director reviews the protocol questions, valid answers, and terminal outcomes
- Verifies pre-arrival instructions are clinically appropriate
- Confirms priority code assignment logic

### Step 3: Sign-Off
- Replace placeholder governance values with real sign-off:
  ```json
  {
    "approved_by": "Dr. Jane Mwangi, Emergency Medicine Director",
    "approved_date": "2026-06-15"
  }
  ```

### Step 4: Deploy
- Commit the signed-off protocol file
- Restart the server or call `POST /admin/reload-protocols`
- Verify via `GET /admin/protocol-audit` that the protocol shows as `status: active`

---

## Audit Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /admin/protocol-audit` | Lists all protocols with governance fields and blocked values |
| `GET /admin/protocol-status` | Returns active and rejected protocols with rejection reasons |
| `GET /protocols` | Lists active and rejected dispatch protocols |
| `GET /field-protocols` | Lists active and rejected field protocols |

---

## Emergency Override

In a genuine emergency, protocols with placeholder governance values can be
**temporarily** loaded by removing the value from `_BLOCKED_GOVERNANCE_VALUES`
in `schema.py`. This is a code change that requires restart and should be
documented in the audit log.
