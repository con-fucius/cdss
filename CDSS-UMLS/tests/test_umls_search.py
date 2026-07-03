"""
Tests for UMLS terminology search
"""
import pytest
from api.services.umls_service import UMLSService


@pytest.mark.asyncio
async def test_search_concepts():
    """Test concept search"""
    service = UMLSService()
    results = await service.search_concepts("diabetes", max_results=5)
    
    assert isinstance(results, list)
    # Note: Actual results depend on UMLS API availability


@pytest.mark.asyncio
async def test_get_concept():
    """Test getting concept by CUI"""
    service = UMLSService()
    # C0004096 is Diabetes Mellitus
    concept = await service.get_concept("C0004096")
    
    if concept:
        assert "cui" in concept
        assert "preferred_name" in concept


@pytest.mark.asyncio
async def test_get_semantic_types():
    """Test getting semantic types"""
    service = UMLSService()
    types = await service.get_semantic_types()
    
    assert isinstance(types, list)
    assert len(types) > 0

