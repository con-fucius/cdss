# Out of Scope for Ambulance CDSS

This document exists so that scope decisions made deliberately are not mistaken for gaps later. If something below is missing, it is missing on purpose.

## Explicitly excluded from this build

- **Differential diagnosis workspace.** A paramedic or dispatcher does not differentially diagnose against a guideline corpus at the point of an emergency call. They follow a protocol based on presentation. No DDx engine, no evidence-graph-driven hypothesis ranking, no LLM synthesis of ranked conditions.
- **Multi-disease evidence graph (HIV, diabetes, CVD, TB, malaria, mental health chronic-care content).** Not reused. If a future need arises for clinical reasoning over chronic disease content in this product, that is a new, deliberate scope decision — not a default.
- **Terminology/UMLS normalization subsystem.** A prehospital protocol set is small, finite, and already uses standardized terminology chosen by the protocol authors. Concept normalization across a large guideline corpus is solving a problem this product does not have.
- **Longitudinal patient state (multi-visit history, multi-table chronic patient record).** An ambulance encounter is a single, short, often first-and-only contact. The data model here is an **incident** — short-lived, lifecycle-bound, not a longitudinal patient chart.
- **Chronic-disease scoring** (Child-Pugh, CVD 10-year risk charts, HbA1c target assessment, eGFR/CKD staging). These are clinic-visit and chronic-management scores. Out of scope. Only NEWS2, GCS, and trauma/obstetric/paediatric emergency criteria (if confirmed in scope per Phase 0.1) are relevant here.
- **Document generation beyond one handoff summary type.** No referral letters, no patient-facing summaries, no general clinical notes. One deterministic, template-filled handoff summary at facility handoff. No LLM required for it.
- **Admin dashboards beyond the minimum governance/observability set** defined in Phase 6 of the implementation plan (protocol version history, guidance-lookup usage frequency, dispatch/routing latency metrics, out-of-script error counts).
- **CDS Hooks / EHR integration surface.** Not relevant to this product's user (dispatcher, paramedic) or workflow (no EHR chart is open at the point of an emergency call).
- **Drug interaction checking against a broad formulary.** Scoped strictly to the narrow prehospital medication list confirmed in Phase 0.5, if and when field protocols involve administering medication on scene.

## Why this list exists

The chronic-disease CDSS accumulated scope by reasonable-sounding increments until it stopped serving a specific user well. This list is the explicit contract against that happening again. Any addition to this product's scope should be checked against this list first — if it's here, it needs a deliberate, written decision to bring it in, not a quiet feature add.
