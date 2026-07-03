"""Hierarchical indexer: maps extractor output to IndexedChunk hierarchy.

Phase 0 fixes:
- Table section_title uses item title (caption or positional), not hardcoded "Table"
- parent_text accumulates full section body across all items in the section,
  not just the first item's text
- section_number extracted from title prefix when available (e.g. "4.2 Dosing")
- page_text type (PyPDF fallback) handled as narrative section
- markdown type (old PyMuPDF blob) handled gracefully; now PyMuPDF emits
  proper section/table items so this is a safety net only
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from ..schema import IndexedChunk
from .semantic import SemanticChunker

# Match leading section numbers like "4", "4.2", "4.2.1" at start of title
_SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+")


def _extract_section_number(title: str) -> tuple[str, str]:
    """Return (section_number, clean_title).
    e.g. "4.2 Dosing regimens" → ("4.2", "Dosing regimens").
    """
    m = _SECTION_NUM_RE.match(title or "")
    if m:
        return m.group(1), title[m.end() :].strip()
    return "", title


class HierarchicalIndexer:
    def __init__(self, disease: str, guideline_name: str) -> None:
        self.disease = disease
        self.guideline_name = guideline_name
        self.chunker = SemanticChunker()

    def process(self, extracted_content: list[dict[str, Any]]) -> list[IndexedChunk]:
        """Map extractor items to IndexedChunks.

        Level 2 (parent): a section — its parent_text is the *full accumulated
        body* of all items belonging to that section, giving the agent full
        context when it retrieves a child chunk.

        Level 3 (child): semantic sub-chunks of the section body.

        Tables are self-parented atomic chunks.
        """
        indexed_chunks: list[IndexedChunk] = []

        current_section_title = "General"
        current_section_number = ""
        current_section_id = str(uuid.uuid4())
        current_section_page = 0
        # Accumulate ALL text items belonging to the current section
        current_section_body_parts: list[str] = []

        def _flush_section() -> None:
            """Emit chunks for the accumulated section."""
            if not current_section_body_parts:
                return
            full_body = "\n\n".join(current_section_body_parts)
            semantic_chunks = self.chunker.chunk(full_body)
            for chunk_text in semantic_chunks:
                if not chunk_text.strip():
                    continue
                indexed_chunks.append(
                    IndexedChunk(
                        text=chunk_text,
                        parent_text=full_body,
                        parent_id=current_section_id,
                        disease=self.disease,
                        guideline_name=self.guideline_name,
                        section_title=current_section_title,
                        section_number=current_section_number,
                        page=current_section_page,
                        content_type="narrative",
                    )
                )

        for item in extracted_content:
            text: str = (item.get("text") or "").strip()
            item_type: str = item.get("type", "")
            page: int = int(item.get("page") or 0)
            item_title: str = (item.get("title") or "").strip()

            # ── Table: atomic, self-parented ──────────────────────────
            if item_type == "table":
                if not text:
                    continue
                table_id = str(uuid.uuid4())
                sec_num, clean_title = _extract_section_number(item_title)
                display_title = clean_title or f"Table, p.{page}"
                indexed_chunks.append(
                    IndexedChunk(
                        text=text,
                        parent_text=text,
                        parent_id=table_id,
                        chunk_id=table_id,
                        disease=self.disease,
                        guideline_name=self.guideline_name,
                        section_title=display_title,
                        section_number=sec_num,
                        page=page,
                        content_type="table",
                    )
                )
                continue

            # ── Section header: start a new parent ───────────────────
            if item_type == "section":
                if item_title and item_title != current_section_title:
                    # New section boundary — flush previous
                    _flush_section()
                    current_section_body_parts = []

                    current_section_number, clean = _extract_section_number(item_title)
                    current_section_title = clean or item_title
                    current_section_id = str(uuid.uuid4())
                    current_section_page = page

                # Accumulate body text
                if text:
                    current_section_body_parts.append(text)
                continue

            # ── page_text (PyPDF fallback) and markdown (safety net) ─
            if item_type in ("page_text", "markdown"):
                if text:
                    current_section_body_parts.append(text)
                    if page and not current_section_page:
                        current_section_page = page
                continue

        # Flush the final section
        _flush_section()

        return indexed_chunks
