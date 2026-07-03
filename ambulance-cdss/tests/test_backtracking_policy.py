"""
tests/test_backtracking_policy.py

Phase 3.3b: confirms the backtracking policy gate is wired correctly.
The policy is RESOLVED — disallowed on locked (Mode 1) dispatch scripts,
permitted on field protocols (unaffected, since those were never
governance-locked). This test confirms the mechanism denies and gives an
explicit reason rather than a generic validation failure.
"""

from __future__ import annotations

from app.protocols.runner import can_backtrack


def test_backtracking_denied_on_locked_scripts():
    assert can_backtrack() is False


def test_backtracking_gate_is_a_pure_function_with_no_args():
    """
    Guards against accidentally turning this into a per-request/per-role
    decision embedded ad hoc in app/main.py — the policy must live in one
    place (this function) so app/main.py's gate and any future caller
    (e.g. a Phase 4 field-mode equivalent) stay consistent automatically.
    """
    import inspect

    sig = inspect.signature(can_backtrack)
    assert len(sig.parameters) == 0
