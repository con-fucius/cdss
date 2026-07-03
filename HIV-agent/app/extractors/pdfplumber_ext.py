"""
PDFPlumber-based extractor (Fallback 2).

Phase 0 fix: Previously extracted tables only — zero narrative text.
Now extracts both page text (as sections) and tables, giving the
HierarchicalIndexer real content to work with in the fallback path.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import pdfplumber

from .base import BaseExtractor, ExtractedDocument

logger = logging.getLogger(__name__)


class PDFPlumberExtractor(BaseExtractor):
    def extract(self, pdf_path: str) -> ExtractedDocument:
        sections: List[Dict[str, Any]] = []

        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_num = i + 1

                # ── Narrative text ─────────────────────────────────────
                text = page.extract_text()
                if text and text.strip():
                    sections.append(
                        {
                            "text": text.strip(),
                            "title": f"Page {page_num}",
                            "page": page_num,
                            "type": "section",
                        }
                    )

                # ── Tables ─────────────────────────────────────────────
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue
                    rows = [
                        " | ".join(
                            str(cell).strip() if cell is not None else ""
                            for cell in row
                        )
                        for row in table
                        if any(cell for cell in row)
                    ]
                    if len(rows) > 1:
                        sections.append(
                            {
                                "text": "\n".join(rows),
                                "title": f"Table, p.{page_num}",
                                "page": page_num,
                                "type": "table",
                            }
                        )

        quality = self.get_quality_score(sections)
        logger.info(
            "PDFPlumberExtractor: %d items, quality=%.2f", len(sections), quality
        )
        return ExtractedDocument(
            content=sections,
            quality_score=quality,
            extractor_name="PDFPlumber",
            metadata={
                "sections": sum(
                    1 for s in sections if s["type"] == "section"
                ),
                "tables": sum(1 for s in sections if s["type"] == "table"),
            },
        )

    def get_quality_score(self, content: Any) -> float:
        if not content:
            return 0.1
        has_tables = any(c.get("type") == "table" for c in content)
        has_sections = any(c.get("type") == "section" for c in content)
        score = 0.2
        if has_sections:
            score += 0.2
        if has_tables:
            score += 0.1
        return score
