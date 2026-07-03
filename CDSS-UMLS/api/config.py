"""
Configuration settings for the UMLS CDSS application
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings"""
    
    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "UMLS CDSS"
    VERSION: str = "1.0.0"
    
    # Database Settings
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/umls_cdss"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    
    # Redis Cache Settings
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL: int = 3600
    
    # LLM Settings
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4"
    LLAMA_MODEL_PATH: str = ""
    MED42_API_KEY: str = ""
    FALCON_API_KEY: str = ""
    
    # RAG Settings
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    VECTOR_DIMENSION: int = 384
    TOP_K_RESULTS: int = 5
    
    # Qdrant Vector Database Settings
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""  # Optional, for cloud Qdrant
    QDRANT_COLLECTION_NAME: str = "umls_concepts"
    
    # UMLS Settings
    UMLS_API_KEY: str = ""
    UMLS_API_URL: str = "https://uts-ws.nlm.nih.gov/rest"
    
    # ICD-11 Settings
    # See: https://icd.who.int/docs/icd-api/APIDoc-Version2/
    ICD11_CLIENT_ID: str = ""
    ICD11_CLIENT_SECRET: str = ""
    ICD11_API_URL: str = "https://id.who.int/icd"
    ICD11_TOKEN_URL: str = "https://icdaccessmanagement.who.int/connect/token"  # Official OAuth2 token endpoint
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000"]
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/cdss.log"
    
    # Evaluation
    EVAL_DATASET_PATH: str = "data/eval_dataset.json"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

