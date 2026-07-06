"""app/protocols/registry.py.

Protocol registry loader.

Per docs/GOVERNANCE.md: "registry.py refuses to load a protocol file as
active unless locked: true and all four governance fields are present
and non-empty." This module is exactly where that enforcement lives.

Protocols are loaded once at startup (or on demand for tests) from JSON
files in protocols/dispatch/. A protocol with incomplete governance
metadata is logged as rejected and excluded from the active registry —
it does not crash startup, but it is also not selectable by any incident,
which is the correct failure mode: a half-approved protocol must be
inert, not merely "best effort."
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .schema import DispatchProtocol, ProtocolQuestion, TerminalOutcome
from . import semantic_matcher
from .protocol_rag import protocol_rag

logger = logging.getLogger(__name__)

DISPATCH_PROTOCOLS_DIR = Path(__file__).resolve().parent / "dispatch"


class ProtocolRejectedError(ValueError):
    """Raised when a protocol JSON file is missing required governance fields."""


@dataclass
class ProtocolMatchResult:
    """Result of chief-complaint protocol matching with confidence scoring."""

    protocol: DispatchProtocol
    matched_triggers: list[str]
    confidence: float  # 0.0–1.0, len(matched_triggers) / len(protocol.chief_complaint_trigger)
    alternatives: list[ProtocolMatchResult] = field(default_factory=list)


def _parse_protocol(raw: dict, source_path: Path) -> DispatchProtocol:
    required_top_level = [
        "protocol_id",
        "version",
        "chief_complaint_trigger",
        "questions",
        "terminal_outcomes",
        "entry_question_id",
        "locked",
        "approved_by",
        "approved_date",
    ]
    missing = [k for k in required_top_level if k not in raw]
    if missing:
        raise ProtocolRejectedError(f"{source_path.name}: missing required fields {missing}")

    questions: dict[str, ProtocolQuestion] = {}
    for qid, qraw in raw["questions"].items():
        questions[qid] = ProtocolQuestion(
            question_id=qid,
            text=qraw["text"],
            answer_type=qraw["answer_type"],
            options=qraw.get("options", []),
            branch_map=qraw.get("branch_map", {}),
            is_terminal=qraw.get("is_terminal", False),
            allow_guidance_lookup=qraw.get("allow_guidance_lookup", False),
            guidance_note=qraw.get("guidance_note"),
        )

    terminal_outcomes: dict[str, TerminalOutcome] = {}
    for tid, traw in raw["terminal_outcomes"].items():
        terminal_outcomes[tid] = TerminalOutcome(
            priority_code=traw["priority_code"],
            recommended_unit_type=traw["recommended_unit_type"],
            pre_arrival_instructions=traw.get("pre_arrival_instructions", []),
        )

    protocol = DispatchProtocol(
        protocol_id=raw["protocol_id"],
        version=str(raw["version"]),
        chief_complaint_trigger=raw["chief_complaint_trigger"],
        questions=questions,
        terminal_outcomes=terminal_outcomes,
        entry_question_id=raw["entry_question_id"],
        locked=bool(raw["locked"]),
        approved_by=str(raw["approved_by"]),
        approved_date=str(raw["approved_date"]),
    )

    if not protocol.is_governance_complete():
        raise ProtocolRejectedError(
            f"{source_path.name}: governance fields incomplete "
            f"(locked={protocol.locked}, approved_by={protocol.approved_by!r}, "
            f"approved_date={protocol.approved_date!r}, version={protocol.version!r})"
        )

    _validate_branch_integrity(protocol, source_path)
    _validate_protocol_reachability(protocol, source_path)
    return protocol


def _validate_branch_integrity(protocol: DispatchProtocol, source_path: Path) -> None:
    """Confirm every branch_map target resolves to either a real question_id
    or a real terminal_outcome key. A dangling branch reference is a
    governance defect — the protocol is rejected, not loaded with a hole
    in it that would surface as a runtime crash mid-call.
    """
    valid_targets = set(protocol.questions.keys()) | set(protocol.terminal_outcomes.keys())
    errors: list[str] = []

    if protocol.entry_question_id not in protocol.questions:
        errors.append(f"entry_question_id {protocol.entry_question_id!r} is not a defined question")

    for qid, question in protocol.questions.items():
        for answer, target in question.branch_map.items():
            if target not in valid_targets:
                errors.append(
                    f"question {qid!r} branch_map[{answer!r}] -> {target!r} "
                    f"does not resolve to any question or terminal outcome"
                )

    if errors:
        raise ProtocolRejectedError(f"{source_path.name}: branch integrity check failed: {errors}")


def _validate_protocol_reachability(protocol: DispatchProtocol, source_path: Path) -> None:
    """Confirm every question and every terminal outcome is reachable from
    entry_question_id via branch_map targets. A question or outcome that
    can never be reached — because no branch points at it — is dead code
    in the protocol: an authored question nobody will ever see, or a
    terminal outcome that can never fire. This is a governance defect
    caught at load time, not discovered during a real call.
    """
    # BFS from entry_question_id across branch_map targets
    queue: deque[str] = deque()
    visited: set[str] = set()

    # Entry point must be a defined question (already validated by
    # _validate_branch_integrity, but guard defensively)
    if protocol.entry_question_id in protocol.questions:
        queue.append(protocol.entry_question_id)
        visited.add(protocol.entry_question_id)

    while queue:
        current_id = queue.popleft()
        question = protocol.questions.get(current_id)
        if question is None:
            continue
        for target in question.branch_map.values():
            if target in visited:
                continue
            visited.add(target)
            # If target is a question, continue BFS through it
            if target in protocol.questions:
                queue.append(target)
            # If target is a terminal outcome, we've reached it — no
            # further traversal needed from that node.

    reachable_questions = visited & set(protocol.questions.keys())
    reachable_outcomes = visited & set(protocol.terminal_outcomes.keys())

    unreachable_questions = set(protocol.questions.keys()) - reachable_questions
    unreachable_outcomes = set(protocol.terminal_outcomes.keys()) - reachable_outcomes

    errors: list[str] = []
    if unreachable_questions:
        errors.append(
            f"unreachable questions (never reached from entry_question_id "
            f"{protocol.entry_question_id!r}): {sorted(unreachable_questions)}"
        )
    if unreachable_outcomes:
        errors.append(
            f"unreachable terminal outcomes (no branch points to them): "
            f"{sorted(unreachable_outcomes)}"
        )

    if errors:
        raise ProtocolRejectedError(f"{source_path.name}: reachability check failed: {errors}")


class ProtocolRegistry:
    def __init__(self, protocols_dir: Path = DISPATCH_PROTOCOLS_DIR):
        self._protocols_dir = protocols_dir
        self._active: dict[str, DispatchProtocol] = {}
        self._rejected: list[dict[str, str]] = []

    def load_all(self) -> None:
        self._active.clear()
        self._rejected.clear()
        if not self._protocols_dir.exists():
            logger.warning("Protocol directory does not exist: %s", self._protocols_dir)
            return

        for path in sorted(self._protocols_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                protocol = _parse_protocol(raw, path)
                self._active[protocol.protocol_id] = protocol
                logger.info(
                    "Loaded protocol %s v%s (approved by %s on %s)",
                    protocol.protocol_id,
                    protocol.version,
                    protocol.approved_by,
                    protocol.approved_date,
                )
            except ProtocolRejectedError as exc:
                logger.error("Protocol rejected: %s", exc)
                self._rejected.append({"file": path.name, "reason": str(exc)})
            except (json.JSONDecodeError, KeyError) as exc:
                logger.error("Protocol file unparseable: %s — %s", path.name, exc)
                self._rejected.append({"file": path.name, "reason": str(exc)})

    def build_semantic_index(self) -> None:
        """Build the TF-IDF semantic matcher and RAG index from loaded protocols."""
        protocol_dicts: list[dict] = []
        for p in self._active.values():
            # Build a description from the protocol's terminal outcomes
            description_parts: list[str] = []
            for outcome in p.terminal_outcomes.values():
                description_parts.append(outcome.priority_code.replace("_", " ").lower())
                description_parts.append(outcome.recommended_unit_type.replace("_", " ").lower())
            # Build keywords list from triggers + outcome codes
            keywords: list[str] = []
            for outcome in p.terminal_outcomes.values():
                keywords.append(outcome.priority_code.replace("_", " ").lower())
                keywords.append(outcome.recommended_unit_type.replace("_", " ").lower())
            protocol_dicts.append({
                "protocol_id": p.protocol_id,
                "triggers": p.chief_complaint_trigger,
                "description": " ".join(description_parts),
                "name": p.protocol_id,
                "version": p.version,
                "keywords": keywords,
            })
        semantic_matcher.build_index(protocol_dicts)
        protocol_rag.index(protocol_dicts)

    def match_by_rag(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Return hybrid RAG matches with scores from keyword+BM25+TF-IDF."""
        return protocol_rag.match(query, top_k=top_k)

    def match_by_semantic(self, query: str) -> list[dict[str, Any]]:
        """Return semantic matches with similarity scores."""
        return semantic_matcher.find_best_match(query)

    def get(self, protocol_id: str) -> DispatchProtocol | None:
        return self._active.get(protocol_id)

    def find_by_chief_complaint(self, chief_complaint: str) -> DispatchProtocol | None:
        """Returns the best-matching active protocol for a chief complaint string.

        Matching rules (in order of priority):
        1. A trigger must appear as a whole word or phrase boundary match in
           the chief complaint — not as a substring inside another word.
           "breathing" must not match "not breathing normally" as a substring
           of a non-matching word; but "not breathing" matching inside a longer
           sentence is fine since the trigger itself is a meaningful phrase.
        2. When multiple protocols match, the protocol whose longest trigger
           string matches is preferred (most specific wins).
        3. If two protocols tie on longest trigger, the first alphabetically
           by protocol_id is returned and a warning is logged — this is a
           protocol-authoring defect (overlapping trigger sets) that should
           be resolved by the protocol author.
        """
        import re

        cc_lower = chief_complaint.strip().lower()

        best_protocol: DispatchProtocol | None = None
        best_trigger_len: int = -1
        best_trigger: str = ""

        for protocol in self._active.values():
            for trigger in protocol.chief_complaint_trigger:
                t = trigger.strip().lower()
                if not t:
                    continue
                # Word-boundary check: the trigger must appear as a whole
                # token or phrase within the complaint, not as an arbitrary
                # substring. Using \b around the trigger handles single words;
                # re.escape handles multi-word phrases (spaces are treated as
                # word boundaries between words in the trigger naturally).
                pattern = r"\b" + re.escape(t) + r"\b"
                if not re.search(pattern, cc_lower):
                    continue
                if len(t) > best_trigger_len or (
                    len(t) == best_trigger_len
                    and best_protocol is not None
                    and protocol.protocol_id < best_protocol.protocol_id
                ):
                    if (
                        best_trigger_len == len(t)
                        and best_protocol is not None
                        and best_protocol.protocol_id != protocol.protocol_id
                    ):
                        logger.warning(
                            "Chief complaint %r matches two protocols at the same "
                            "trigger length (%r): %r and %r. This is a protocol-"
                            "authoring defect — trigger sets should not overlap at "
                            "equal specificity. Selecting %r alphabetically.",
                            chief_complaint,
                            t,
                            best_protocol.protocol_id,
                            protocol.protocol_id,
                            min(best_protocol.protocol_id, protocol.protocol_id),
                        )
                    best_protocol = protocol
                    best_trigger_len = len(t)
                    best_trigger = t

        if best_protocol is not None:
            logger.debug(
                "Chief complaint %r matched protocol %r via trigger %r",
                chief_complaint,
                best_protocol.protocol_id,
                best_trigger,
            )
        return best_protocol

    def match_by_chief_complaint(self, chief_complaint: str) -> ProtocolMatchResult | None:
        """Returns a ProtocolMatchResult with confidence scoring for the
        best-matching active protocol, plus any alternative protocols that
        also partially matched.

        Returns None only when zero protocols had any trigger match.

        Confidence is calculated as:
            len(matched_triggers) / len(protocol.chief_complaint_trigger)

        where matched_triggers are the triggers from the winning protocol
        that actually matched the complaint string (not all triggers, just
        the ones that fired). This tells the dispatcher what fraction of
        the protocol's own trigger set matched the complaint.

        alternatives are other protocols with at least one trigger match,
        sorted by confidence descending.
        """
        import re

        cc_lower = chief_complaint.strip().lower()

        # Collect all protocols with at least one trigger match
        all_matches: list[ProtocolMatchResult] = []

        for protocol in self._active.values():
            matched_triggers: list[str] = []
            for trigger in protocol.chief_complaint_trigger:
                t = trigger.strip().lower()
                if not t:
                    continue
                pattern = r"\b" + re.escape(t) + r"\b"
                if re.search(pattern, cc_lower):
                    matched_triggers.append(t)

            if not matched_triggers:
                continue

            total_triggers = len(protocol.chief_complaint_trigger)
            confidence = len(matched_triggers) / total_triggers if total_triggers > 0 else 0.0
            all_matches.append(
                ProtocolMatchResult(
                    protocol=protocol,
                    matched_triggers=matched_triggers,
                    confidence=confidence,
                )
            )

        if not all_matches:
            # Secondary: try protocol RAG (MedSpaCy + TF-IDF) when trigger words fail
            if protocol_rag.is_available():
                rag_results = protocol_rag.match(cc_lower, top_k=3)
                if rag_results and rag_results[0]["score"] > 0.3:
                    best = rag_results[0]
                    best_proto = self._active.get(best["protocol_id"])
                    if best_proto is not None:
                        logger.info(
                            "Chief complaint %r fell back to RAG match: "
                            "%s (score %.3f, methods: %s)",
                            chief_complaint,
                            best["protocol_id"],
                            best["score"],
                            best["methods"],
                        )
                        return ProtocolMatchResult(
                            protocol=best_proto,
                            matched_triggers=[],
                            confidence=best["score"],
                        )

            # Tertiary: try TF-IDF semantic matching
            if semantic_matcher.is_available():
                sem_results = semantic_matcher.find_best_match(cc_lower, top_k=3)
                if sem_results:
                    best_id = sem_results[0]["protocol_id"]
                    best_proto = self._active.get(best_id)
                    if best_proto is not None:
                        logger.info(
                            "Chief complaint %r fell back to TF-IDF match: "
                            "%s (similarity %.3f)",
                            chief_complaint,
                            best_id,
                            sem_results[0]["similarity"],
                        )
                        return ProtocolMatchResult(
                            protocol=best_proto,
                            matched_triggers=[],
                            confidence=sem_results[0]["similarity"],
                        )
            return None

        # Select the winner: longest matching trigger wins (most specific).
        # Ties broken alphabetically by protocol_id (same as find_by_chief_complaint).
        def _sort_key(m: ProtocolMatchResult) -> tuple:
            # Longest trigger first; alphabetically by protocol_id for ties
            longest = max(len(t) for t in m.matched_triggers)
            return (-longest, m.protocol.protocol_id)

        all_matches.sort(key=_sort_key)
        winner = all_matches[0]

        # Build alternatives list (all other matches, sorted by confidence descending)
        alternatives = [m for m in all_matches[1:]]
        alternatives.sort(key=lambda m: (-m.confidence, m.protocol.protocol_id))

        winner.alternatives = alternatives

        if len(all_matches) > 1:
            logger.warning(
                "Chief complaint %r matched %d protocols. Winner: %r "
                "(confidence %.2f). Alternatives: %s",
                chief_complaint,
                len(all_matches),
                winner.protocol.protocol_id,
                winner.confidence,
                [m.protocol.protocol_id for m in alternatives],
            )

        return winner

    def list_active(self) -> list[dict[str, str]]:
        return [
            {
                "protocol_id": p.protocol_id,
                "version": p.version,
                "chief_complaint_trigger": p.chief_complaint_trigger,
                "approved_by": p.approved_by,
                "approved_date": p.approved_date,
            }
            for p in self._active.values()
        ]

    def list_rejected(self) -> list[dict[str, str]]:
        return list(self._rejected)


# Module-level singleton — loaded at app startup via load_all().
registry = ProtocolRegistry()
