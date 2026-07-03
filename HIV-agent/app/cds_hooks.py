import logging
from dataclasses import dataclass
from typing import Any

from .patient_state import get_patient_state
from .repositories import query_evidence_graph_db

logger = logging.getLogger(__name__)


@dataclass
class CDSCard:
    summary: str
    detail: str
    indicator: str  # "info", "warning", "critical"
    source: dict[str, str]  # {"label": str, "url": str}
    suggestions: list[dict[str, Any]]
    override_reasons: list[str]

    def to_dict(self):
        # Truncate summary to 140 chars per CDS Hooks spec
        summary_val = self.summary
        if len(summary_val) > 140:
            logger.warning(f"CDS Card summary exceeds 140 chars: {summary_val}")
            summary_val = summary_val[:137] + "..."

        return {
            "summary": summary_val,
            "detail": self.detail,
            "indicator": self.indicator,
            "source": self.source,
            "suggestions": self.suggestions,
            "override_reasons": self.override_reasons,
        }


class CDSHooksHandler:
    async def handle_patient_view(self, context: dict[str, Any]) -> list[CDSCard]:
        try:
            patient_id = context.get("patientId") or context.get("patient_ref")
            if not patient_id:
                return []

            from .logs import _hash_patient_ref

            patient_ref = (
                _hash_patient_ref({"patient_ref": patient_id})
                if "patientId" in context
                else patient_id
            )

            patient_state = await get_patient_state(patient_ref)

            from .scoring import _compute_patient_scores

            scores = _compute_patient_scores(patient_state)

            cards = []

            # 1. CRITICAL scores first
            for score in scores:
                if score.get("alert_level") == "CRITICAL":
                    cards.append(
                        CDSCard(
                            summary=f"Critical Alert: {score.get('score_result', {}).get('trigger', 'Abnormal value')}",
                            detail=f"Computed {score.get('scorer')} shows critical risk.",
                            indicator="critical",
                            source={"label": score.get("source_guideline", "CDSS"), "url": ""},
                            suggestions=[],
                            override_reasons=[
                                "clinically_irrelevant",
                                "already_actioned",
                                "patient_specific_exception",
                                "incorrect_alert",
                                "duplicate",
                            ],
                        )
                    )

            # 2. Check monitoring due
            active_meds = patient_state.get("active_medications", [])
            for med in active_meds:
                med_name = med.get("name", "")
                if not med_name:
                    continue
                edges = await query_evidence_graph_db(
                    disease="general", entity_name=med_name, top_k=5
                )
                for edge in edges:
                    if edge.get("relation_type", "").lower() == "drug_requires_monitoring":
                        cards.append(
                            CDSCard(
                                summary=f"Monitoring due for {med_name}",
                                detail=f"Patient requires monitoring for {edge.get('target_id')}.",
                                indicator="warning",
                                source={"label": "CDSS Monitoring", "url": ""},
                                suggestions=[],
                                override_reasons=[],
                            )
                        )

            # Return max 3 cards
            return cards[:3]
        except Exception as e:
            logger.warning(f"patient-view hook failed: {e}")
            return []

    async def handle_medication_prescribe(self, context: dict[str, Any]) -> list[CDSCard]:
        try:
            draft_orders = context.get("draftOrders", {})
            draft_med = draft_orders.get("medicationCodeableConcept", {}).get("text", "")
            if not draft_med:
                draft_med = draft_orders.get("medicationReference", {}).get("display", "")

            if not draft_med:
                return []

            patient_id = context.get("patientId", "")
            from .logs import _hash_patient_ref

            patient_ref = (
                _hash_patient_ref({"patient_ref": patient_id}) if patient_id else "unknown"
            )
            patient_state = await get_patient_state(patient_ref)
            active_meds = [
                m.get("name") for m in patient_state.get("active_medications", []) if m.get("name")
            ]

            from .api import _check_drug_interactions

            interactions_res = await _check_drug_interactions([draft_med] + active_meds)

            cards = []

            # Handle interactions
            if interactions_res and isinstance(interactions_res, dict):
                for inter in interactions_res.get("interactions", []):
                    severity = str(inter.get("severity", "")).lower()
                    if severity == "critical":
                        indicator = "critical"
                    elif severity == "warning":
                        indicator = "warning"
                    else:
                        indicator = "info"

                    if indicator in ("critical", "warning"):
                        cards.append(
                            CDSCard(
                                summary=f"Interaction: {draft_med} and {inter.get('interacting_drug', 'active med')}",
                                detail=inter.get("description", "Potential interaction detected."),
                                indicator=indicator,
                                source={"label": "RxNorm", "url": ""},
                                suggestions=[],
                                override_reasons=[
                                    "clinically_irrelevant",
                                    "patient_specific_exception",
                                ],
                            )
                        )

            # Handle contraindications
            edges = await query_evidence_graph_db(disease="general", entity_name=draft_med, top_k=5)
            for edge in edges:
                if edge.get("relation_type", "").lower() == "contraindicated_with":
                    cards.append(
                        CDSCard(
                            summary=f"Contraindicated: {draft_med}",
                            detail=f"Contraindicated with {edge.get('target_id')}.",
                            indicator="critical",
                            source={"label": "Evidence Graph", "url": ""},
                            suggestions=[],
                            override_reasons=["clinically_irrelevant"],
                        )
                    )

            # Return max 2 cards, prioritizing critical
            sorted_cards = sorted(cards, key=lambda c: 0 if c.indicator == "critical" else 1)
            return sorted_cards[:2]
        except Exception as e:
            logger.warning(f"medication-prescribe hook failed: {e}")
            return []
