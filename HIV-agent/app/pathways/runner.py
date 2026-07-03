import logging
from typing import AsyncIterator, Dict, Any

from .registry import PATHWAY_REGISTRY
from ..repositories import query_evidence_graph_db

logger = logging.getLogger(__name__)

class PathwayRunner:
    async def run(self, pathway_id: str, patient_ref: str, patient_state: Dict[str, Any]) -> AsyncIterator[Dict]:
        pathway = PATHWAY_REGISTRY.get(pathway_id)
        if not pathway:
            yield {"type": "error", "message": "Unknown pathway"}
            return
            
        if not pathway.steps:
            yield {"type": "warning", "message": "Pathway definition pending"}
            return
            
        completed_steps = []
        current_step = None
        monitoring_due = []
        
        # Check monitoring for active medications
        active_meds = patient_state.get("active_medications", [])
        for med in active_meds:
            med_name = med.get("name", "")
            if not med_name:
                continue
            edges = await query_evidence_graph_db(disease=pathway.disease, entity_name=med_name, top_k=5)
            for edge in edges:
                if edge.get("relation_type", "").lower() == "drug_requires_monitoring":
                    # Simplified check: just flag it as needed
                    monitoring_due.append({
                        "drug": med_name,
                        "monitoring_target": edge.get("target_id")
                    })

        for i, step in enumerate(pathway.steps, 1):
            is_complete = False
            try:
                is_complete = step.completion_criteria(patient_state)
            except Exception as e:
                logger.error(f"Error evaluating completion criteria for {step.step_id}: {e}")
                
            if step.contraindication_check:
                # Check for contraindications
                edges = await query_evidence_graph_db(disease=pathway.disease, entity_name=step.contraindication_check, top_k=5)
                for edge in edges:
                    if edge.get("relation_type", "").lower() == "contraindicated_with":
                        yield {
                            "type": "contraindication",
                            "drug": step.contraindication_check,
                            "condition": edge.get("target_id"),
                            "source_ref": edge.get("source_id", "Unknown")
                        }
            
            if is_complete:
                status = "completed"
                completed_steps.append(step.step_id)
            elif current_step is None:
                status = "current"
                current_step = {
                    "step_id": step.step_id,
                    "name": step.name,
                    "blocking_inputs": step.blocking_inputs
                }
            else:
                status = "blocked"
                
            yield {
                "type": "step",
                "step_number": i,
                "step_id": step.step_id,
                "name": step.name,
                "status": status,
                "blocking_inputs": step.blocking_inputs if not is_complete else [],
                "guideline_ref": step.guideline_ref
            }
            
        next_actions = []
        if current_step:
            next_actions = current_step.get("blocking_inputs", [])
            
        yield {
            "type": "pathway_summary",
            "completed_steps": completed_steps,
            "current_step": current_step,
            "next_actions": next_actions,
            "monitoring_due": monitoring_due
        }
