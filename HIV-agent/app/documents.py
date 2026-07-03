import json
import logging
from typing import Any

import httpx

from .patient_state import get_patient_state
from .providers import (
    get_llm_provider,
    provider_auth_header,
    provider_chat_endpoint,
    provider_offline,
)
from .repositories import create_clinical_document

logger = logging.getLogger(__name__)

_DISTILL_MODELS: dict[str, str] = {
    "groq": "llama-3.1-8b-instant",
    "puter": "openai/gpt-4o-mini",
}


class ClinicalDocumentGenerator:
    async def generate(
        self,
        doc_type: str,
        patient_ref: str,
        encounter_id: str,
        additional_context: str | None,
        search_index: Any,
    ) -> dict[str, Any]:
        if doc_type != "sbar":
            return {"status": "not_implemented", "document_type": doc_type}

        patient_state = await get_patient_state(patient_ref)
        scores = []
        try:
            from .scoring import _compute_patient_scores

            scores = _compute_patient_scores(patient_state)
        except Exception as e:
            logger.error(f"Failed to compute scores for doc generation: {e}")

        # Retrieve guideline sections
        active_conditions = patient_state.get("active_diagnoses", [])
        primary_condition = (
            active_conditions[0].get("name", "general") if active_conditions else "general"
        )

        guideline_context = ""
        guideline_citations = []
        if search_index:
            try:
                results = await search_index.search_guidelines(
                    query=f"{primary_condition} management",
                    disease=primary_condition,
                    top_k=3,
                    session_id="doc_gen",
                )
                for res in results:
                    text = res.get("text", "")
                    src = res.get("source", "Unknown")
                    guideline_context += f"- [{src}] {text}\n"
                    guideline_citations.append({"source": src})
            except Exception as e:
                logger.error(f"Guideline search failed for doc: {e}")

        # Assemble prompt
        system_prompt = (
            "You are an expert clinical assistant writing a medical document.\n"
            "Every clinical claim must be cited using [Source: guideline_name, section].\n"
            "Add AI_GENERATED: TRUE as the first line.\n"
            "Add REQUIRES_CLINICIAN_REVIEW: TRUE as the last line."
        )

        user_msg = (
            f"Generate an SBAR document for this patient.\n"
            f"Patient State: {json.dumps(patient_state)}\n"
            f"Recent Scores: {json.dumps(scores)}\n"
            f"Additional Context: {additional_context or 'None'}\n"
            f"Relevant Guidelines:\n{guideline_context}\n"
            "Please format the document clearly with sections: Situation, Background, Assessment, Recommendation."
        )

        if provider_offline():
            # Return a mock document
            content = "AI_GENERATED: TRUE\n[OFFLINE MOCK SBAR]\nREQUIRES_CLINICIAN_REVIEW: TRUE"
            doc = await create_clinical_document(
                document_type=doc_type,
                patient_ref=patient_ref,
                encounter_id=encounter_id,
                content=content,
                requires_clinician_review=True,
                guideline_citations=guideline_citations,
            )
            return doc

        provider = get_llm_provider()
        if not provider:
            return {"status": "error", "message": "No LLM provider available"}

        url = provider_chat_endpoint(provider)
        headers = provider_auth_header(provider)
        model_name = _DISTILL_MODELS.get(
            provider.get("name", ""), provider.get("model", "llama3-8b-8192")
        )

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Document generation LLM call failed: {e}")
            return {"status": "error", "message": str(e)}

        # Ensure required tokens are present
        if "AI_GENERATED: TRUE" not in content:
            content = "AI_GENERATED: TRUE\n" + content
        if "REQUIRES_CLINICIAN_REVIEW: TRUE" not in content:
            content = content + "\nREQUIRES_CLINICIAN_REVIEW: TRUE"

        doc = await create_clinical_document(
            document_type=doc_type,
            patient_ref=patient_ref,
            encounter_id=encounter_id,
            content=content,
            requires_clinician_review=True,
            guideline_citations=guideline_citations,
        )
        return doc
