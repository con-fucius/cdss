"""Experimental inference endpoint (v2) - For testing new approaches."""


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services.llms.model_registry import ModelRegistry
from api.services.rag.rag_v2 import RAGServiceV2
from api.services.rag.rag_v3 import RAGServiceV3

router = APIRouter()
model_registry = ModelRegistry()


class InferenceV2Request(BaseModel):
    patient_symptoms: str
    patient_history: str | None = None
    model_name: str = "gpt-4"
    rag_version: str = "v2"  # v2 or v3
    clinical_context_window: int | None = 500
    max_tokens: int | None = 1000
    temperature: float | None = 0.7


class InferenceV2Response(BaseModel):
    recommendation: str
    confidence: float
    supporting_evidence: list[dict[str, str]]
    umls_concepts: list[str]
    semantic_relations: list[dict[str, str]]
    model_used: str
    rag_version: str
    processing_time: float


@router.post("/triage", response_model=InferenceV2Response)
async def triage_patient_v2(request: InferenceV2Request):
    """Experimental triage inference endpoint
    Supports RAG v2 (PGVector) and v3 (Hybrid).
    """
    try:
        # Get model
        model = model_registry.get_model(request.model_name)

        # Get RAG service based on version
        if request.rag_version == "v2":
            rag_service = RAGServiceV2()
        elif request.rag_version == "v3":
            rag_service = RAGServiceV3()
        else:
            raise HTTPException(status_code=400, detail="Invalid RAG version")

        # Retrieve relevant context
        context, umls_concepts, semantic_relations = await rag_service.retrieve(
            query=request.patient_symptoms,
            top_k=5,
            clinical_context_window=request.clinical_context_window,
        )

        # Generate recommendation
        recommendation = await model.generate(
            prompt=request.patient_symptoms,
            context=context,
            patient_history=request.patient_history,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        return InferenceV2Response(
            recommendation=recommendation["text"],
            confidence=recommendation.get("confidence", 0.8),
            supporting_evidence=context,
            umls_concepts=umls_concepts,
            semantic_relations=semantic_relations,
            model_used=request.model_name,
            rag_version=request.rag_version,
            processing_time=recommendation.get("processing_time", 0.0),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
