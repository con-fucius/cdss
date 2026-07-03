"""app/protocols/schema.py.

Dataclasses for both protocol modes.

DispatchProtocol — Mode 1, locked. Every field here exists to make the
governance rules in docs/GOVERNANCE.md enforceable in code, not just on
paper: `locked`, `approved_by`, `approved_date`, `version` are all
required-with-no-default specifically so a protocol cannot be loaded as
active without them being explicitly set by whoever authors the file.

FieldProtocol — paramedic-side. Deliberately more permissive: field
protocols may reference guideline content for clinician judgment support
(per the established distinction that a trained field clinician operates
under different latitude than a locked dispatch script answered by a
call-taking team — see docs/GOVERNANCE.md). Implemented in Phase 4 — see
app/protocols/field_registry.py (loader) and app/protocols/field_runner.py
(checklist state, reconstructed from incident_field_log).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TerminalOutcome:
    priority_code: str
    recommended_unit_type: str
    pre_arrival_instructions: list[str] = field(default_factory=list)


@dataclass
class ProtocolQuestion:
    question_id: str
    text: str
    answer_type: str  # "yes_no" | "select" | "numeric"
    options: list[str] = field(default_factory=list)
    # answer value -> next question_id, OR a terminal outcome key
    # resolved by the registry/runner against `terminal_outcomes`.
    branch_map: dict[str, str] = field(default_factory=dict)
    is_terminal: bool = False
    # Mode 2 gate — see docs/GOVERNANCE.md. False (locked, no guidance) by
    # default; must be explicitly set true by the protocol author for any
    # question where supplementary lookup is permitted.
    allow_guidance_lookup: bool = False
    # Mode 2 content. Deliberately a fixed, author-written string per
    # question — NOT a search query against any corpus. Per
    # docs/OUT_OF_SCOPE.md, this product has no evidence graph and no
    # guideline search engine to query; "guidance lookup" here means
    # surfacing a short, pre-written supplementary note the protocol
    # author attached to this exact question, never a live retrieval.
    # Empty/None if allow_guidance_lookup is False; the registry does not
    # require this field to be set even when the gate is true (an author
    # may gate the question for future use before writing the note), but
    # the runner/endpoint returns a clear "no guidance authored" result
    # rather than null when absent.
    guidance_note: str | None = None


@dataclass
class DispatchProtocol:
    """Mode 1 — locked criteria-based dispatch script."""

    protocol_id: str
    version: str
    chief_complaint_trigger: list[str]
    questions: dict[str, ProtocolQuestion]
    terminal_outcomes: dict[str, TerminalOutcome]
    entry_question_id: str

    # Governance fields — see docs/GOVERNANCE.md. All four are required
    # with no default. A protocol JSON file missing any of these fails to
    # parse, by design, rather than loading with a governance gap silently
    # present.
    locked: bool
    approved_by: str
    approved_date: str  # ISO date string

    def is_governance_complete(self) -> bool:
        """True only if locked, approved_by, approved_date, and version are
        all non-empty AND approved_by/approved_date do not contain the
        literal word "PLACEHOLDER" (case-insensitive).

        The placeholder check exists because the three protocols authored
        so far were written with deliberate PLACEHOLDER text in these
        fields pending the real named doctor + medical director sign-off
        (Phase 0.1/0.2, resolved as in-house authorship with named
        approvers still to be supplied). Without this check, a non-empty
        placeholder string satisfies a plain truthiness/strip() test and
        the protocol would load as locked=true and be selectable for real
        incidents — silently contradicting docs/GOVERNANCE.md's claim that
        "a protocol cannot be loaded as active without [governance fields]
        being explicitly set". Until real names/dates are substituted in
        the three existing protocol JSON files, they are REJECTED at
        startup (see app/protocols/registry.py list_rejected()) rather
        than silently treated as approved.
        """
        if not (
            self.locked
            and self.approved_by.strip()
            and self.approved_date.strip()
            and self.version.strip()
        ):
            return False
        if "placeholder" in self.approved_by.lower():
            return False
        return "placeholder" not in self.approved_date.lower()


@dataclass
class FieldProtocolStep:
    """One ordered checklist item in a FieldProtocol (Phase 4, implemented —
    see app/protocols/field_registry.py and app/protocols/field_runner.py).
    """

    step_id: str
    title: str
    action_type: str  # "assessment" | "intervention" | "vitals" | "disposition"
    description: str = ""
    guideline_ref: str | None = None


@dataclass
class FieldProtocol:
    protocol_id: str
    version: str
    disease_or_presentation: str
    steps: list[FieldProtocolStep] = field(default_factory=list)
