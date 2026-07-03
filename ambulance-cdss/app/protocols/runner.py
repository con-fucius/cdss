"""app/protocols/runner.py.

Locked-mode protocol runner (Mode 1).

The single most important behavioural rule in this entire codebase, per
docs/GOVERNANCE.md: "An out-of-script answer is a hard error, never a
silent default." This module is where that rule lives and is enforced.

submit_answer() either:
  - returns the next ProtocolQuestion to ask, or
  - returns a TerminalOutcome (priority code, unit type, pre-arrival
    instructions), or
  - raises OutOfScriptAnswerError.

It never guesses, never falls through to a "closest match", and never
returns a partial/best-effort result. This is deliberate: a wrong
priority code from a guessed branch is a patient-safety failure, and a
loud, immediate, fully-logged error in the dispatcher UI ("this answer
isn't recognised — please rephrase or select from the options shown") is
categorically safer than the system proceeding on a guess.

Field-mode runner (Phase 4) lives in a separate module by design — see
app/protocols/field_runner.py. This file is Mode 1 (locked dispatch
scripts) only; the hard-fail-on-undefined-answer rule above is
specifically a Mode 1 property and does not apply to field protocols,
which permit out-of-order/skipped steps.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import DispatchProtocol, ProtocolQuestion, TerminalOutcome


class OutOfScriptAnswerError(ValueError):
    """Raised when a submitted answer does not match any branch_map entry
    for the current question. This must propagate to the caller as a
    visible, loud error — never be caught and silently defaulted.
    """

    def __init__(self, question_id: str, submitted_answer: str, valid_answers: list):
        self.question_id = question_id
        self.submitted_answer = submitted_answer
        self.valid_answers = valid_answers
        super().__init__(
            f"Answer {submitted_answer!r} is not valid for question "
            f"{question_id!r}. Valid answers: {valid_answers}"
        )


@dataclass
class RunnerStepResult:
    """Discriminated result: exactly one of next_question / terminal_outcome is set."""

    next_question: ProtocolQuestion | None = None
    terminal_outcome: TerminalOutcome | None = None
    terminal_outcome_id: str | None = None


def get_entry_question(protocol: DispatchProtocol) -> ProtocolQuestion:
    return protocol.questions[protocol.entry_question_id]


def submit_answer(
    protocol: DispatchProtocol,
    current_question_id: str,
    answer: str,
) -> RunnerStepResult:
    """Advance the locked script by one step.

    Raises OutOfScriptAnswerError if `answer` is not a key in the current
    question's branch_map. Raises KeyError if current_question_id is not
    a real question in this protocol (caller error — should never happen
    if the dispatcher UI only ever submits question IDs it was given).
    """
    if current_question_id not in protocol.questions:
        raise KeyError(
            f"Question {current_question_id!r} does not exist in protocol "
            f"{protocol.protocol_id!r} v{protocol.version}"
        )

    question = protocol.questions[current_question_id]

    if answer not in question.branch_map:
        raise OutOfScriptAnswerError(
            question_id=current_question_id,
            submitted_answer=answer,
            valid_answers=list(question.branch_map.keys()),
        )

    target = question.branch_map[answer]

    if target in protocol.terminal_outcomes:
        return RunnerStepResult(
            terminal_outcome=protocol.terminal_outcomes[target],
            terminal_outcome_id=target,
        )

    if target in protocol.questions:
        return RunnerStepResult(next_question=protocol.questions[target])

    # This branch should be unreachable — registry._validate_branch_integrity
    # rejects any protocol with a dangling branch target at load time. If
    # this is ever hit, the registry's validation has a defect, and that
    # is a louder, more useful failure than silently treating it as terminal.
    raise RuntimeError(
        f"Branch target {target!r} for answer {answer!r} on question "
        f"{current_question_id!r} resolves to neither a question nor a "
        f"terminal outcome. This indicates a registry validation defect — "
        f"the protocol should have been rejected at load time."
    )


def can_backtrack() -> bool:
    """Backtracking policy — resolved per docs/GOVERNANCE.md: disallowed on
    locked (Mode 1) dispatch scripts. (Field protocols are unaffected —
    they were never governance-locked and already permit out-of-order
    step marking; see app/protocols/field_runner.py.).

    Returns False. If this policy is ever revisited, update this function
    and, if backtracking is permitted, ensure every backtrack re-answer is
    logged via append_dispatch_answer(..., is_backtrack=True) — never as a
    silent overwrite of the prior answer row. See
    app/repositories.py::append_dispatch_answer.

    Consumed by:
      - app/main.py::submit_incident_answer — rejects any request with
        is_backtrack=True with HTTP 403 while this returns False, rather
        than silently treating it as a normal forward answer.
      - app/main.py::health — exposed as "backtracking_permitted" so a
        dispatcher UI can hide/disable a backtrack control without
        hardcoding the policy itself.
    """
    return False
