"""
Unified schema for CDSS document indexing.
Defines the IndexedChunk structure used across extractors, chunkers, and LanceDB.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import uuid

@dataclass
class IndexedChunk:
    text: str               # Level 3 chunk text (indexed, embedded)
    parent_text: str        # Level 2 section full text (returned to agent)
    disease: str            # "hiv" | "diabetes" | "cvd" | "tb" | "mental_health"
    guideline_name: str     # "Kenya ARV Guidelines 2022"
    section_title: str      # "First-line ART for Adults"
    page: int
    
    chunk_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None          # Level 2 section ID
    guideline_version: str = "1.0"
    guideline_year: int = 2022
    source_url: str = "https://www.health.go.ke/"
    section_number: str = ""
    content_type: str = "narrative"          # "narrative"|"table"|"list"|"criteria"|"algorithm"
    population_tags: List[str] = field(default_factory=list)   # ["adult", "treatment-naive"]
    clinical_tags: List[str] = field(default_factory=list)     # ["first-line", "regimen", "dosing"]
    extraction_quality: str = "full"         # "full"|"degraded"
    
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, vector: Optional[List[float]] = None) -> Dict[str, Any]:
        """Convert to dictionary for LanceDB ingestion."""
        d = {
            "chunk_id": self.chunk_id,
            "parent_id": self.parent_id,
            "disease": self.disease,
            "guideline_name": self.guideline_name,
            "guideline_version": self.guideline_version,
            "guideline_year": self.guideline_year,
            "source_url": self.source_url,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "page": self.page,
            "content_type": self.content_type,
            "population_tags": self.population_tags,
            "clinical_tags": self.clinical_tags,
            "text": self.text,
            "parent_text": self.parent_text,
            "extraction_quality": self.extraction_quality,
        }
        if vector is not None:
            d["vector"] = vector
        return d
