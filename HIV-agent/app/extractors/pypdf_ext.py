"""
PyPDF-based PDF extractor (Fallback 3).
"""

from pypdf import PdfReader
from typing import List, Dict, Any
from .base import BaseExtractor, ExtractedDocument

class PyPDFExtractor(BaseExtractor):
    def extract(self, pdf_path: str) -> ExtractedDocument:
        reader = PdfReader(pdf_path)
        sections = []
        
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                sections.append({
                    "text": text,
                    "page": i + 1,
                    "type": "page_text"
                })
        
        quality = self.get_quality_score(sections)
        
        return ExtractedDocument(
            content=sections,
            quality_score=quality,
            extractor_name="PyPDF",
            metadata={"pages": len(reader.pages), "quality": "degraded"}
        )

    def get_quality_score(self, content: List[Dict[str, Any]]) -> float:
        """Low quality score as it lacks structure."""
        if not content:
            return 0.0
        return 0.3
