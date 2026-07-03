"""
Tests for RAG v2 (PGVector)
"""
import pytest
from api.services.rag.rag_v2 import RAGServiceV2


@pytest.mark.asyncio
async def test_rag_v2_retrieve():
    """Test RAG v2 retrieval"""
    rag = RAGServiceV2()
    
    # Note: This requires a database connection
    # In actual tests, use a test database
    results, concepts, relations = await rag.retrieve("diabetes", top_k=5)
    
    assert isinstance(results, list)
    assert isinstance(concepts, list)
    assert isinstance(relations, list)

