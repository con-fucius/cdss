"""
app/protocols/field_registry.py

Field protocol registry loader — Phase 4.

Deliberately distinct from app/protocols/registry.py (Mode 1's
governance-gated loader). Per app/protocols/schema.py::FieldProtocol
docstring and docs/OUT_OF_SCOPE.md, field protocols are checklist/
reference aids for a trained paramedic operating under clinical
judgment latitude — not a locked, medico-legally binding script. So:

  - No locked / approved_by / approved_date requirement to load.
  - No branch_map / terminal_outcome graph — a FieldProtocol is a flat,
    ordered list of FieldProtocolStep. The field runner (field_runner.py)
    advances through steps but does not hard-fail on skip/reorder the way
    Mode 1 hard-fails on an undefined branch answer — that rule is
    explicitly a Mode 1 property (see docs/GOVERNANCE.md "Mode 1 ...
    Mode 2" boundary test), not a system-wide one.

A field protocol file that is structurally broken (missing step_id,
duplicate step_id, etc.) is still rejected at load — that is a basic
data-integrity floor, not a governance/sign-off requirement, and the two
should not be conflated.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

STOP_WORDS = {"a", "an", "the", "in", "of", "and", "or", "for", "is", "it", "to", "on", "at", "by", "with"}

from .schema import FieldProtocol, FieldProtocolStep

logger = logging.getLogger(__name__)

FIELD_PROTOCOLS_DIR = Path(__file__).resolve().parent / "field"


class FieldProtocolRejectedError(ValueError):
    """Raised when a field protocol JSON file is structurally invalid."""


def _parse_field_protocol(raw: dict, source_path: Path) -> FieldProtocol:
    required_top_level = ["protocol_id", "version", "disease_or_presentation", "steps"]
    missing = [k for k in required_top_level if k not in raw]
    if missing:
        raise FieldProtocolRejectedError(
            f"{source_path.name}: missing required fields {missing}"
        )

    seen_ids: set[str] = set()
    steps: List[FieldProtocolStep] = []
    for i, sraw in enumerate(raw["steps"]):
        step_id = sraw.get("step_id")
        if not step_id:
            raise FieldProtocolRejectedError(
                f"{source_path.name}: step at index {i} missing step_id"
            )
        if step_id in seen_ids:
            raise FieldProtocolRejectedError(
                f"{source_path.name}: duplicate step_id {step_id!r}"
            )
        seen_ids.add(step_id)

        action_type = sraw.get("action_type")
        if action_type not in ("assessment", "intervention", "vitals", "disposition"):
            raise FieldProtocolRejectedError(
                f"{source_path.name}: step {step_id!r} has invalid action_type "
                f"{action_type!r} (must be assessment|intervention|vitals|disposition)"
            )

        steps.append(
            FieldProtocolStep(
                step_id=step_id,
                title=sraw.get("title", ""),
                action_type=action_type,
                description=sraw.get("description", ""),
                guideline_ref=sraw.get("guideline_ref"),
            )
        )

    if not steps:
        raise FieldProtocolRejectedError(f"{source_path.name}: no steps defined")

    return FieldProtocol(
        protocol_id=raw["protocol_id"],
        version=str(raw["version"]),
        disease_or_presentation=raw["disease_or_presentation"],
        steps=steps,
    )


class FieldProtocolRegistry:
    def __init__(self, protocols_dir: Path = FIELD_PROTOCOLS_DIR):
        self._protocols_dir = protocols_dir
        self._active: Dict[str, FieldProtocol] = {}
        self._rejected: List[Dict[str, str]] = []

    def load_all(self) -> None:
        self._active.clear()
        self._rejected.clear()
        if not self._protocols_dir.exists():
            logger.warning(
                "Field protocol directory does not exist: %s", self._protocols_dir
            )
            return

        for path in sorted(self._protocols_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                protocol = _parse_field_protocol(raw, path)
                self._active[protocol.protocol_id] = protocol
                logger.info(
                    "Loaded field protocol %s v%s (%d steps)",
                    protocol.protocol_id,
                    protocol.version,
                    len(protocol.steps),
                )
            except FieldProtocolRejectedError as exc:
                logger.error("Field protocol rejected: %s", exc)
                self._rejected.append({"file": path.name, "reason": str(exc)})
            except (json.JSONDecodeError, KeyError) as exc:
                logger.error("Field protocol file unparseable: %s — %s", path.name, exc)
                self._rejected.append({"file": path.name, "reason": str(exc)})

    def get(self, protocol_id: str) -> Optional[FieldProtocol]:
        return self._active.get(protocol_id)

    def find_by_presentation(self, presentation: str) -> Optional[FieldProtocol]:
        """Match a presentation string against active field protocols.

        Uses keyword-based matching: if all significant words from the
        protocol's disease_or_presentation appear in the presentation
        string (or vice versa), it's a match. This handles natural
        language variations like 'patient found in cardiac arrest,
        unresponsive' matching 'Cardiac arrest / unresponsive patient'.
        """
        p_lower = presentation.strip().lower()
        p_words = set(re.findall(r"\w+", p_lower)) - STOP_WORDS
        for protocol in self._active.values():
            prot_lower = protocol.disease_or_presentation.lower()
            prot_words = set(re.findall(r"\w+", prot_lower)) - STOP_WORDS
            # Match if all protocol keywords appear in presentation, or vice versa
            if prot_words and prot_words <= p_words:
                return protocol
            if p_words and p_words <= prot_words:
                return protocol
            # Fallback: substring match for short presentations
            if prot_lower in p_lower or p_lower in prot_lower:
                return protocol
        return None

    def list_active(self) -> List[Dict[str, object]]:
        return [
            {
                "protocol_id": p.protocol_id,
                "version": p.version,
                "disease_or_presentation": p.disease_or_presentation,
                "step_count": len(p.steps),
            }
            for p in self._active.values()
        ]

    def list_rejected(self) -> List[Dict[str, str]]:
        return list(self._rejected)


# Module-level singleton — loaded at app startup via load_all().
field_registry = FieldProtocolRegistry()
