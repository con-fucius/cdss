"""
Base class for PDF extractors.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class ExtractedDocument:
    content: List[Dict[str, Any]]  # List of sections/tables
    quality_score: float
    extractor_name: str
    metadata: Dict[str, Any]

class BaseExtractor(ABC):
    @abstractmethod
    def extract(self, pdf_path: str) -> ExtractedDocument:
        """Extract content from PDF."""
        pass

    @abstractmethod
    def get_quality_score(self, content: Any) -> float:
        """Calculate quality score for the extraction."""
        pass
