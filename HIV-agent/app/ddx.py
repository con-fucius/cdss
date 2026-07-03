import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from .providers import (
    get_llm_provider,
    provider_auth_header,
    provider_chat_endpoint,
    provider_offline,
)
from .repositories import query_evidence_graph_db
from .search_tools import SearchIndex

logger = logging.getLogger(__name__)


class DifferentialDiagnosisEngine:
    def __init__(self):
        pass

    async def generate_ddx(self, request: Any, search_index: SearchIndex) -> AsyncIterator[dict]:
        target_diseases = request.target_diseases
        if not target_diseases:
            from .config import DISEASE_CONFIG

            target_diseases = list(DISEASE_CONFIG.keys())

        # Stage 1: Evidence Graph Traversal
        candidates: dict[str, dict[str, Any]] = {}

        for symptom in request.presenting_symptoms:
            for disease in target_diseases:
                edges = await query_evidence_graph_db(disease=disease, entity_name=symptom, top_k=5)
                for edge in edges:
                    # Look for symptom -> condition
                    # either by relation_type or target type
                    rel = str(edge.get("relation_type", "")).lower()
                    target = str(edge.get("target_id", ""))

                    if (
                        "suggest" in rel
                        or "indicat" in rel
                        or "manifest" in rel
                        or "symptom" in rel
                    ):
                        if target not in candidates:
                            candidates[target] = {
                                "condition": target,
                                "disease_scope": disease,
                                "matched_symptoms": [],
                                "weight": 0,
                            }
                        if symptom not in candidates[target]["matched_symptoms"]:
                            candidates[target]["matched_symptoms"].append(symptom)
                            candidates[target]["weight"] += 1

        # Sort and take top 5
        sorted_candidates = sorted(
            list(candidates.values()), key=lambda x: x["weight"], reverse=True
        )[:5]

        yield {"type": "ddx_candidates", "candidates": sorted_candidates}

        # Stage 2: Guideline Criteria Retrieval
        criteria_context = []
        for cand in sorted_candidates:
            condition = cand["condition"]
            disease = cand["disease_scope"]

            # Retrieve from PageIndex
            results = await search_index.query_pageindex(
                query=f"{condition} diagnostic criteria", disease=disease, top_k=2
            )

            # We don't have a structured extraction yet for met/missing without LLM,
            # but we can yield the raw criteria chunks.
            yield {
                "type": "ddx_criteria",
                "condition": condition,
                "criteria_chunks": [r.get("text", "") for r in results],
            }

            criteria_context.append(
                {"condition": condition, "chunks": [r.get("text", "") for r in results]}
            )

        # Stage 3: LLM Synthesis
        if provider_offline():
            yield {"type": "warning", "message": "Offline mode active. LLM synthesis skipped."}
            yield {"type": "ddx_done", "ranked_differential": [], "recommended_investigations": []}
            return

        provider = get_llm_provider()
        if not provider:
            yield {"type": "error", "message": "LLM Provider not configured."}
            return

        system_prompt = (
            "You are an expert clinical diagnostic AI. Generate a ranked differential diagnosis.\n"
            "Every clinical claim must be cited using [Source: guideline_name, section].\n"
            "If no guideline section supports a hypothesis, do not include it in the differential.\n"
            "Start with 'AI_GENERATED: TRUE'."
        )

        user_msg = (
            f"Presenting symptoms: {', '.join(request.presenting_symptoms)}\n"
            f"Vitals: {json.dumps(request.vital_signs or {})}\n"
            f"Labs: {json.dumps(request.relevant_labs or {})}\n\n"
            "Candidate Conditions and Guideline Criteria:\n"
        )
        for c in criteria_context:
            user_msg += f"\nCondition: {c['condition']}\n"
            for chunk in c["chunks"]:
                user_msg += f"- {chunk}\n"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        from .providers import _stream_openai_compatible_chat

        url = provider_chat_endpoint(provider)
        headers = provider_auth_header(provider)

        payload = {
            "model": provider.get("model", "llama3-8b-8192"),
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        }

        full_content = ""
        try:
            async for chunk_text in _stream_openai_compatible_chat(url, headers, payload):
                full_content += chunk_text
                yield {"type": "chunk", "content": chunk_text}
        except Exception as e:
            logger.error("DDx LLM stream failed: %s", e)
            yield {"type": "error", "message": str(e)}

        yield {
            "type": "ddx_done",
            "ranked_differential": [{"condition": c["condition"]} for c in sorted_candidates],
            "recommended_investigations": [],
        }
