# Protocol Governance

This document is the authority record for how dispatch protocols enter production. It exists because Mode 1 (locked dispatch scripts) carries medico-legal weight that the rest of this system does not.

## The two-mode boundary

**Mode 1 — Locked script.** Deterministic, versioned, signed-off-by-medical-director decision tree. Used for criteria-based dispatch: question sequence, branch logic, terminal outcome (priority code, recommended unit type, pre-arrival instructions) are all fixed data, not dynamically generated. No LLM call, no evidence-graph lookup, no free-text deviation anywhere inside this path. An out-of-script answer is a hard error, never a silent default.

**Mode 2 — Guidance.** Bounded, informational-only, supplementary guideline lookup. Available **only** at insertion points the protocol author explicitly marks with `allow_guidance_lookup: true` on a specific `ProtocolQuestion`. Using it cannot alter the priority code, the unit type, or the branch the script has already committed to. Every use is logged separately in `guidance_lookup_log`, visually distinct in the dispatcher UI, and reviewable independent of the locked-script transcript.

**The test for which mode something belongs in:** does this affect the priority code, the dispatched unit type, or the pre-arrival instruction given to the caller? If yes — Mode 1, locked, signed off. If no — it may be Mode 2.

## Protocol authorship and sign-off (decision required — see `PHASE_STATUS.md` item 0.2)

Before any protocol is marked `locked: true` in the registry, it must have, at minimum:

1. **Author** — who wrote the question sequence and branch logic.
2. **Clinical reviewer** — a person with prehospital/EMS clinical authority who reviewed every branch path for correctness.
3. **Medical director approval** — final sign-off recorded with name and date.
4. **Version number** — semantic or date-based, immutable once approved.

This metadata is stored directly on the `DispatchProtocol` record (`approved_by`, `approved_date`, `version`) and is **snapshotted into the incident record** at the moment a call starts using that protocol version. If the protocol is edited after a call has started, the in-progress call continues using the version it started with — never the edited version.

## What "locked" enforces in code

- `protocols/registry.py` refuses to load a protocol file as active unless `locked: true` and all four governance fields above are present and non-empty.
- `protocols/runner.py` in locked mode raises on any answer that does not match a defined `branch_map` entry. It does not guess, default, or fall through.
- Backtracking ("go back a question") during a live locked-script call either is disabled outright or creates a new logged branch event rather than overwriting prior answers — this decision must be made explicitly (see `PHASE_STATUS.md` item 3.3) and is not left ambiguous.

## Open governance decisions tracked here

- [ ] Confirm whether this organization is adapting an existing licensed criteria-based dispatch system, or authoring protocols in-house from scratch. This changes who "the author" is in practice.
- [ ] Confirm exact medical director sign-off workflow (is it a manual record entered by an admin, or a more formal external approval process this system should integrate with later).
- [ ] Confirm backtracking policy for live locked-script calls.
