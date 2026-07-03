"""
Base RAG service interface
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple


class BaseRAGService(ABC):
    """Base class for RAG implementations"""
    
    @abstractmethod
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        **kwargs
    ) -> Tuple[List[Dict[str, str]], List[str]]:
        """
        Retrieve relevant context for a query
        
        Returns:
            Tuple of (context_documents, umls_concepts)
        """
        pass
    
    @abstractmethod
    async def index_documents(self, documents: List[Dict[str, str]]):
        """Index documents for retrieval"""
        pass

