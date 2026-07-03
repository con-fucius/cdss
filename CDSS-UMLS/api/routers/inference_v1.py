"""Stable inference endpoint (v1) - Production ready."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services.llms.model_registry import ModelRegistry
from api.services.rag.rag_qdrant import RAGServiceQdrant

logger = logging.getLogger(__name__)

router = APIRouter()
model_registry = ModelRegistry()
rag_service = RAGServiceQdrant()


class InferenceRequest(BaseModel):
    patient_symptoms: str
    patient_history: str | None = None
    model_name: str = "gpt-4"
    use_rag: bool = True
    max_tokens: int | None = 1000


class InferenceResponse(BaseModel):
    recommendation: str
    confidence: float
    supporting_evidence: list[dict[str, str]]
    umls_concepts: list[str]
    model_used: str
    processing_time: float


@router.post("/triage", response_model=InferenceResponse)
async def triage_patient(request: InferenceRequest):
    """Stable triage inference endpoint
    Uses Qdrant-based RAG with UMLS concept embeddings.
    """
    try:
        # Get model
        model = model_registry.get_model(request.model_name)

        # Retrieve relevant context using RAG
        context = []
        umls_concepts = []
        if request.use_rag:
            try:
                context, umls_concepts = await rag_service.retrieve(
                    query=request.patient_symptoms, top_k=5
                )
                logger.info(
                    f"RAG retrieved {len(context)} context documents and {len(umls_concepts)} CUIs"
                )
            except Exception as e:
                logger.error(f"Error in RAG retrieval: {e}", exc_info=True)
                # Continue without RAG context if retrieval fails

        # Generate recommendation
        recommendation = await model.generate(
            prompt=request.patient_symptoms,
            context=context,
            patient_history=request.patient_history,
            max_tokens=request.max_tokens,
        )

        return InferenceResponse(
            recommendation=recommendation["text"],
            confidence=recommendation.get("confidence", 0.8),
            supporting_evidence=context,
            umls_concepts=umls_concepts,
            model_used=request.model_name,
            processing_time=recommendation.get("processing_time", 0.0),
        )
    except Exception as e:
        logger.error(f"Error in triage endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/test-rag")
async def test_rag():
    """Test endpoint to verify RAG service is working."""
    try:
        test_query = "warfarin aspirin drug interaction"

        # Check initialization
        initialized = rag_service.client is not None and rag_service.embedding_model is not None

        if not initialized:
            return {
                "status": "error",
                "error": "RAG service not initialized",
                "rag_service_initialized": False,
            }

        # Test direct Qdrant search using HTTP API (more reliable)
        import httpx
        from sentence_transformers import SentenceTransformer

        from api.config import settings

        test_model = SentenceTransformer(settings.EMBEDDING_MODEL)

        # Test simple search via HTTP API
        test_embedding = test_model.encode(["warfarin"], show_progress_bar=False)[0]
        direct_results = []
        try:
            response = httpx.post(
                f"{settings.QDRANT_URL}/collections/{settings.QDRANT_COLLECTION_NAME}/points/search",
                json={
                    "vector": test_embedding.tolist(),
                    "limit": 3,
                    "with_payload": True,
                    "with_vectors": False,
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                data = response.json()
                direct_results = data.get("result", [])
        except Exception as http_err:
            logger.warning(f"HTTP API test failed: {http_err}")

        # Now test RAG service
        context, umls_concepts = await rag_service.retrieve(query=test_query, top_k=3)

        return {
            "status": "ok",
            "query": test_query,
            "results_count": len(context),
            "cuis_count": len(umls_concepts),
            "context": context[:3],  # Limit for display
            "umls_concepts": umls_concepts[:10],  # Limit for display
            "rag_service_initialized": initialized,
            "direct_search_results": len(direct_results),
            "direct_search_sample": [
                {
                    "cui": r.get("payload", {}).get("cui", "")
                    if isinstance(r, dict)
                    else (r.payload.get("cui", "") if hasattr(r, "payload") else ""),
                    "name": (
                        r.get("payload", {}).get("preferred_name", "")
                        if isinstance(r, dict)
                        else (r.payload.get("preferred_name", "") if hasattr(r, "payload") else "")
                    )[:50],
                    "score": float(
                        r.get("score", 0.0)
                        if isinstance(r, dict)
                        else (r.score if hasattr(r, "score") else 0.0)
                    ),
                }
                for r in direct_results[:3]
            ]
            if direct_results
            else [],
        }
    except Exception as e:
        logger.error(f"Error testing RAG: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__,
            "rag_service_initialized": rag_service.client is not None
            and rag_service.embedding_model is not None,
        }
