"""Docling-based PDF extractor.

Phase 0 fixes:
- prov guard checks for empty list, not just None
- label comparison uses .value or str() correctly for DocItemLabel enum
- section type set on header items so HierarchicalIndexer receives "section" items
- Tables extracted separately with proper page attribution
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from .base import BaseExtractor, ExtractedDocument

logger = logging.getLogger(__name__)


class DoclingExtractor(BaseExtractor):
    def __init__(self) -> None:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = os.getenv("CDSS_DOCLING_OCR", "false").strip().lower() == "true"
        pipeline_options.do_table_structure = (
            os.getenv("CDSS_DOCLING_TABLE_STRUCTURE", "false").strip().lower() == "true"
        )

        self.converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )

    def _page_no(self, item: Any) -> int:
        """Safely extract page number from a Docling item."""
        prov = getattr(item, "prov", None)
        if prov and len(prov) > 0:
            return int(getattr(prov[0], "page_no", 0) or 0)
        return 0

    def _label_str(self, item: Any) -> str:
        """Return a normalised label string from a Docling item.
        DocItemLabel is an enum; .value gives the plain string e.g. 'section_header'.
        str() on the enum returns 'DocItemLabel.SECTION_HEADER' which breaks comparisons.
        """
        label = getattr(item, "label", None)
        if label is None:
            return ""
        # Prefer .value (enum member); fall back to str and strip the class prefix
        value = getattr(label, "value", None)
        if value is not None:
            return str(value).lower()
        return str(label).split(".")[-1].lower()

    def extract(self, pdf_path: str) -> ExtractedDocument:
        result = self.converter.convert(pdf_path)
        doc = result.document

        sections: list[dict[str, Any]] = []
        current_title = "General"

        for item, level in doc.iterate_items():
            text = getattr(item, "text", None)
            if not text or not str(text).strip():
                continue

            label = self._label_str(item)
            page = self._page_no(item)

            is_header = "section_header" in label or label in ("title", "heading")

            if is_header:
                # Update running section title
                current_title = str(text).strip().splitlines()[0][:120]
                # Emit as a "section" item so HierarchicalIndexer picks it up
                sections.append(
                    {
                        "text": str(text),
                        "title": current_title,
                        "level": level,
                        "page": page,
                        "type": "section",
                    }
                )
            else:
                # Narrative body item — emit under current section title
                sections.append(
                    {
                        "text": str(text),
                        "title": current_title,
                        "level": level,
                        "page": page,
                        "type": "section",
                    }
                )

        # Extract tables separately
        for table in getattr(doc, "tables", []):
            page = self._page_no(table)
            try:
                md = table.export_to_markdown()
            except Exception:
                md = ""
            if md.strip():
                # Try to get a caption; fall back to positional label
                caption = ""
                with contextlib.suppress(Exception):
                    caption = table.caption_text(doc) or ""
                sections.append(
                    {
                        "text": md,
                        "title": caption or f"Table, p.{page}",
                        "page": page,
                        "type": "table",
                    }
                )

        quality = self.get_quality_score(sections)
        logger.info("DoclingExtractor: %d items, quality=%.2f", len(sections), quality)
        return ExtractedDocument(
            content=sections,
            quality_score=quality,
            extractor_name="Docling",
            metadata={"total_items": len(sections)},
        )

    def get_quality_score(self, content: list[dict[str, Any]]) -> float:
        if not content:
            return 0.0
        has_tables = any(c["type"] == "table" for c in content)
        has_sections = any(c["type"] == "section" for c in content)
        score = 0.4
        if has_sections:
            score += 0.3
        if has_tables:
            score += 0.3
        return score
