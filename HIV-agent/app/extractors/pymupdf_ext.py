"""
PyMuPDF-based PDF extractor (Fallback 1).

Phase 0 fix: Previously returned one giant markdown blob typed as "markdown"
which HierarchicalIndexer never matched, producing zero chunks.
Now parses markdown headers into proper "section" items and tables into
"table" items, matching what HierarchicalIndexer expects.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

import pymupdf4llm

from .base import BaseExtractor, ExtractedDocument

logger = logging.getLogger(__name__)

# Match ATX-style markdown headers: # Title, ## Title, ### Title
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$")
# Detect markdown table rows
_TABLE_ROW_RE = re.compile(r"^\|.+\|$")


def _parse_markdown_into_sections(
    md_text: str,
) -> List[Dict[str, Any]]:
    """
    Split a markdown string into section and table items.
    Each section starts at a header line and runs until the next header.
    Contiguous table rows are collected into table items.
    """
    sections: List[Dict[str, Any]] = []
    current_title = "General"
    current_lines: List[str] = []
    table_lines: List[str] = []
    in_table = False
    # pymupdf4llm does not give page numbers per section; page 0 marks unknown.
    # The chunk will still carry correct text content
    page = 0

    def flush_section() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append(
                {
                    "text": body,
                    "title": current_title,
                    "page": page,
                    "type": "section",
                }
            )

    def flush_table() -> None:
        body = "\n".join(table_lines).strip()
        if body and len(table_lines) > 1:
            sections.append(
                {"text": body, "title": f"Table", "page": page, "type": "table"}
            )

    for line in md_text.splitlines():
        header_match = _HEADER_RE.match(line)
        is_table_row = bool(_TABLE_ROW_RE.match(line.rstrip()))

        if header_match:
            # Flush any pending table
            if in_table:
                flush_table()
                table_lines = []
                in_table = False
            # Flush previous section body
            flush_section()
            current_lines = []
            current_title = header_match.group(2).strip()
        elif is_table_row:
            if not in_table:
                # Flush current section up to here
                flush_section()
                current_lines = []
                in_table = True
            table_lines.append(line)
        else:
            if in_table:
                # Table ended
                flush_table()
                table_lines = []
                in_table = False
            current_lines.append(line)

    # Flush any remaining content
    if in_table:
        flush_table()
    flush_section()

    return sections


class PyMuPDFExtractor(BaseExtractor):
    def extract(self, pdf_path: str) -> ExtractedDocument:
        md_text: str = pymupdf4llm.to_markdown(pdf_path)
        sections = _parse_markdown_into_sections(md_text)

        quality = self.get_quality_score(sections)
        logger.info(
            "PyMuPDFExtractor: %d items, quality=%.2f", len(sections), quality
        )
        return ExtractedDocument(
            content=sections,
            quality_score=quality,
            extractor_name="PyMuPDF4LLM",
            metadata={"length": len(md_text), "items": len(sections)},
        )

    def get_quality_score(self, content: Any) -> float:
        if isinstance(content, str):
            # Called with raw text during construction
            has_headers = "#" in content
            has_tables = "|" in content
            score = 0.4
            if has_headers:
                score += 0.15
            if has_tables:
                score += 0.15
            return score
        # Called with parsed sections list
        if not content:
            return 0.0
        has_tables = any(c.get("type") == "table" for c in content)
        has_sections = any(c.get("type") == "section" for c in content)
        score = 0.4
        if has_sections:
            score += 0.15
        if has_tables:
            score += 0.15
        return score
