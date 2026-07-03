"""Base class for PDF extractors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ExtractedDocument:
    content: list[dict[str, Any]]  # List of sections/tables
    quality_score: float
    extractor_name: str
    metadata: dict[str, Any]


class BaseExtractor(ABC):
    @abstractmethod
    def extract(self, pdf_path: str) -> ExtractedDocument:
        """Extract content from PDF."""
        pass

    @abstractmethod
    def get_quality_score(self, content: Any) -> float:
        """Calculate quality score for the extraction."""
        pass
