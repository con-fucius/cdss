"""UMLS Terminology search and lookup endpoints."""


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services.umls_service import UMLSService

router = APIRouter()
umls_service = UMLSService()


class ConceptSearchRequest(BaseModel):
    query: str
    max_results: int | None = 10
    semantic_types: list[str] | None = None


class ConceptResponse(BaseModel):
    cui: str
    preferred_name: str
    definition: str | None
    semantic_types: list[str]
    synonyms: list[str]


@router.post("/search", response_model=list[ConceptResponse])
async def search_concepts(request: ConceptSearchRequest):
    """Search UMLS concepts by term."""
    try:
        results = await umls_service.search_concepts(
            query=request.query,
            max_results=request.max_results,
            semantic_types=request.semantic_types,
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/concept/{cui}", response_model=ConceptResponse)
async def get_concept(cui: str):
    """Get concept details by CUI."""
    try:
        concept = await umls_service.get_concept(cui)
        if not concept:
            raise HTTPException(status_code=404, detail="Concept not found")
        return concept
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/semantic-types")
async def get_semantic_types():
    """Get list of available semantic types."""
    return await umls_service.get_semantic_types()
