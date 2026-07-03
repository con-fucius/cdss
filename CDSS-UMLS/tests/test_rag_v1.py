"""
Tests for RAG v1 (BM25 + embeddings)
"""
import pytest
from api.services.rag.rag_v1 import RAGServiceV1


@pytest.mark.asyncio
async def test_rag_v1_retrieve():
    """Test RAG v1 retrieval"""
    rag = RAGServiceV1()
    
    # Index some test documents
    test_docs = [
        {
            "text": "Diabetes is a chronic condition characterized by high blood sugar.",
            "source": "test_doc_1",
            "umls_concepts": ["C0004096"]
        },
        {
            "text": "Hypertension is high blood pressure.",
            "source": "test_doc_2",
            "umls_concepts": ["C0020538"]
        }
    ]
    
    await rag.index_documents(test_docs)
    
    # Retrieve
    results, concepts = await rag.retrieve("diabetes", top_k=1)
    
    assert isinstance(results, list)
    assert isinstance(concepts, list)


@pytest.mark.asyncio
async def test_rag_v1_empty_index():
    """Test RAG v1 with empty index"""
    rag = RAGServiceV1()
    results, concepts = await rag.retrieve("test query")
    
    assert results == []
    assert concepts == []

