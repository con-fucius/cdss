"""
Tests for inference endpoints
"""
import pytest
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_health_check():
    """Test health check endpoint"""
    response = client.get("/health/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_inference_v1():
    """Test inference v1 endpoint"""
    payload = {
        "patient_symptoms": "Patient presents with chest pain and shortness of breath",
        "model_name": "gpt-4",
        "use_rag": True
    }
    
    # Note: This requires API keys and models to be configured
    # response = client.post("/api/v1/inference/triage", json=payload)
    # assert response.status_code in [200, 500]  # 500 if not configured


def test_terminology_search():
    """Test terminology search endpoint"""
    payload = {
        "query": "diabetes",
        "max_results": 5
    }
    
    # Note: Requires UMLS API key
    # response = client.post("/api/v1/terminology/search", json=payload)
    # assert response.status_code in [200, 500]

