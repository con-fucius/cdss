"""
app/protocols/field_runner.py

Field-side protocol runner — Phase 4.

This is deliberately NOT a copy of app/protocols/runner.py's locked-mode
behaviour. Per docs/GOVERNANCE.md's Mode 1/Mode 2 boundary test and
app/protocols/schema.py::FieldProtocol's docstring, a field protocol is a
checklist/reference aid consumed by a trained paramedic exercising
clinical judgment — not a deterministic script whose deviations are
medico-legally significant the way an out-of-script dispatch answer is.

Consequences of that distinction, encoded here:
  - No hard-fail on skipping a step, doing steps out of order, or marking
    a step "not applicable" for this patient. The Mode 1 hard-fail rule
    exists because an unrecognised *answer* to a *locked branching
    question* has no defined next state — that situation has no analogue
    here, because there is no branching: advancing past a step is always
    well-defined (the next step in the ordered list, or done).
  - Every step action a paramedic actually performs is still written to
    incident_field_log (append-only, immutable) via
    app/repositories.py::append_field_log — exactly as it already would
    be without this runner. The runner's job is only to tell the field
    UI what the recommended next step is and to track checklist
    completion state; it is not the source of truth for what happened.
  - There is no "terminal outcome" concept analogous to Mode 1's priority
    code / unit type. A field protocol run ends in either "all steps
    addressed" or "stopped early" (e.g. patient handed off, deteriorated
    to a different protocol) — both are valid, normal endings, not error
    states, and neither is computed by this module; the field unit
    declares completion explicitly (see FieldRunState.mark_step /
    is_complete below) and the disposition itself is recorded as an
    ordinary field_log entry with action_type="disposition".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .schema import FieldProtocol, FieldProtocolStep

VALID_STEP_STATUSES = ("pending", "done", "skipped", "not_applicable")


@dataclass
class FieldStepState:
    step: FieldProtocolStep
    status: str = "pending"  # see VALID_STEP_STATUSES


@dataclass
class FieldRunState:
    """
    In-memory checklist progress for one incident's field protocol run.
    Not persisted as its own table — see module docstring: the durable
    record of what happened is incident_field_log, written independently
    by the field UI/API for every actual action. This state exists purely
    to answer "what's left" for the UI, reconstructable at any time from
    incident_field_log + the protocol definition (see
    rebuild_from_field_log below) rather than needing its own storage.
    """

    protocol: FieldProtocol
    steps: Dict[str, FieldStepState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = {s.step_id: FieldStepState(step=s) for s in self.protocol.steps}

    def mark_step(self, step_id: str, status: str) -> None:
        if status not in VALID_STEP_STATUSES:
            raise ValueError(
                f"Invalid step status {status!r}. Must be one of {VALID_STEP_STATUSES}."
            )
        if step_id not in self.steps:
            raise KeyError(
                f"Step {step_id!r} is not part of protocol {self.protocol.protocol_id!r}"
            )
        self.steps[step_id].status = status

    def next_pending_step(self) -> Optional[FieldProtocolStep]:
        for step in self.protocol.steps:
            if self.steps[step.step_id].status == "pending":
                return step
        return None

    def is_complete(self) -> bool:
        return all(s.status != "pending" for s in self.steps.values())

    def summary(self) -> List[Dict[str, str]]:
        return [
            {
                "step_id": s.step.step_id,
                "title": s.step.title,
                "action_type": s.step.action_type,
                "status": s.status,
                "guideline_ref": s.step.guideline_ref,
            }
            for s in (self.steps[step.step_id] for step in self.protocol.steps)
        ]


def rebuild_from_field_log(
    protocol: FieldProtocol, field_log_entries: List[Dict]
) -> FieldRunState:
    """
    Reconstructs checklist progress from the incident's actual
    incident_field_log rows rather than trusting any client-held state —
    this is what makes the field log the real source of truth (per module
    docstring) rather than this runner's in-memory state. A step is
    considered "done" if any field_log row references its step_id; this
    is intentionally permissive (matches the "no hard-fail on order"
    design) rather than requiring a specific status value in the log
    payload, since paramedics are not required to use this runner at all
    to have their actions count.
    """
    state = FieldRunState(protocol=protocol)
    logged_step_ids = {entry["step_id"] for entry in field_log_entries}
    for step_id in logged_step_ids:
        if step_id in state.steps:
            state.steps[step_id].status = "done"
    return state
