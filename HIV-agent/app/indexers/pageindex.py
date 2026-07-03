"""PageIndex builder for page-level guideline navigation and retrieval.

Fix: _summarise_page now uses an explicitly cheap model per provider
instead of get_llm_model(). A 200-page guideline × LLM call per page
at the expensive model rate would be prohibitive; fast small models
produce adequate page summaries for retrieval purposes.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import lancedb
from fastembed import TextEmbedding

from ..config import DISEASE_CONFIG, get_embedding_model_name
from ..providers import (
    get_llm_provider,
    provider_auth_header,
    provider_chat_endpoint,
    provider_has_credentials,
)

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = os.environ.get("LANCEDB_PATH", str(APP_DIR / "lancedb"))
PAGEINDEX_TABLE = "pageindex_chunks"
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Cheap models for page summarisation — never the expensive reasoning model
_SUMMARISE_MODELS: dict[str, str] = {
    "groq": "llama-3.1-8b-instant",
    "puter": "openai/gpt-4o-mini",
}


@dataclass
class PageIndexRow:
    id: str
    disease: str
    page: int
    section_path: str
    summary: str
    text: str
    vector: list[float]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extractive_summary(text: str, max_chars: int = 700) -> str:
    """Deterministic clinical-page summary when LLM is unavailable."""
    cleaned = _clean_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    selected: list[str] = []
    total = 0
    for sentence in sentences:
        if not sentence:
            continue
        if total + len(sentence) > max_chars and selected:
            break
        selected.append(sentence)
        total += len(sentence) + 1
    return " ".join(selected).strip() or cleaned[:max_chars].rstrip()


def _batched(items: list[str], batch_size: int) -> Iterable[list[str]]:
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]


class PageIndexBuilder:
    """Builds the page-level LanceDB index used before deep chunk retrieval."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        embedding_cache_dir: str | None = None,
        batch_size: int = 32,
    ):
        self.db_path = db_path
        self.db = lancedb.connect(db_path)
        cache_dir = embedding_cache_dir or os.getenv("FASTEMBED_CACHE_DIR")
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            self.embedding_model = TextEmbedding(
                model_name=get_embedding_model_name(),
                cache_dir=cache_dir,
            )
        else:
            self.embedding_model = TextEmbedding(model_name=get_embedding_model_name())
        self.batch_size = batch_size

    async def index_pdf(self, disease: str, pdf_path: str) -> int:
        pages = self._extract_pages(pdf_path)
        if not pages:
            raise RuntimeError(f"PageIndex extracted zero pages from {pdf_path}")

        summaries = [
            await self._summarise_page(disease, page["page"], page["text"]) for page in pages
        ]
        vectors = self._embed_summaries(summaries)

        rows = [
            PageIndexRow(
                id=str(uuid.uuid4()),
                disease=disease,
                page=pages[idx]["page"],
                section_path=pages[idx]["section_path"],
                summary=summaries[idx],
                text=pages[idx]["text"],
                vector=vectors[idx],
            ).__dict__
            for idx in range(len(pages))
        ]

        if PAGEINDEX_TABLE in self._table_names():
            existing_diseases = self._existing_diseases()
            if disease not in existing_diseases:
                table = self.db.open_table(PAGEINDEX_TABLE)
                table.add(rows)
            else:
                existing = self._existing_rows_except(disease)
                self.db.drop_table(PAGEINDEX_TABLE)
                table = self.db.create_table(PAGEINDEX_TABLE, data=[*existing, *rows])
        else:
            table = self.db.create_table(PAGEINDEX_TABLE, data=rows)
        self._create_indexes(
            table,
            vector_dim=len(rows[0]["vector"]),
            row_count=self._table_row_count(table),
        )
        return len(rows)

    def _extract_pages(self, pdf_path: str) -> list[dict[str, Any]]:
        try:
            return self._extract_pages_pypdf(pdf_path)
        except Exception:
            return self._extract_pages_pdfplumber(pdf_path)

    def _extract_pages_pypdf(self, pdf_path: str) -> list[dict[str, Any]]:
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)
        rows = []
        for idx, page in enumerate(reader.pages, start=1):
            raw_text = page.extract_text() or ""
            text = _clean_text(raw_text)
            if text:
                rows.append(
                    {
                        "page": idx,
                        "section_path": self._heading_from_text(raw_text),
                        "text": text,
                    }
                )
        return rows

    def _extract_pages_pdfplumber(self, pdf_path: str) -> list[dict[str, Any]]:
        import pdfplumber

        rows = []
        with pdfplumber.open(pdf_path) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                raw_text = page.extract_text() or ""
                text = _clean_text(raw_text)
                if text:
                    rows.append(
                        {
                            "page": idx,
                            "section_path": self._heading_from_text(raw_text),
                            "text": text,
                        }
                    )
        return rows

    def _heading_from_text(self, text: str) -> str:
        for raw_line in text.splitlines():
            line = _clean_text(raw_line)
            if 8 <= len(line) <= 140 and not line.isdigit():
                return line
        return "Page summary"

    async def _summarise_page(self, disease: str, page: int, text: str) -> str:
        """Summarise one page for retrieval.  Uses the cheap provider model;
        falls back to extractive summary if credentials are missing or call fails.
        """
        provider = get_llm_provider()
        if not provider_has_credentials(provider):
            return _extractive_summary(text)

        summarise_model = _SUMMARISE_MODELS.get(provider)
        if summarise_model is None:
            return _extractive_summary(text)
        disease_name = DISEASE_CONFIG.get(disease, {}).get("display_name", disease)

        prompt = (
            "Summarise this clinical guideline page for retrieval. "
            "Keep disease, patient population, treatments, tests, thresholds, "
            "contraindications, and follow-up actions if present. "
            "Do not add facts not present on the page.\n\n"
            f"Disease: {disease_name}\nPage: {page}\n\n{text[:6000]}"
        )

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    provider_chat_endpoint(provider),
                    headers={
                        **provider_auth_header(provider),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": summarise_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        "max_tokens": 220,
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            return _clean_text(content) or _extractive_summary(text)
        except Exception:
            return _extractive_summary(text)

    def _embed_summaries(self, summaries: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        list(self.embedding_model.embed(["warmup"]))
        for batch in _batched(summaries, self.batch_size):
            vectors.extend(
                vector.tolist()
                for vector in self.embedding_model.embed([f"{_BGE_QUERY_PREFIX}{s}" for s in batch])
            )
        return vectors

    def _existing_rows_except(self, disease: str) -> list[dict[str, Any]]:
        if PAGEINDEX_TABLE not in self._table_names():
            return []
        table = self.db.open_table(PAGEINDEX_TABLE)
        df = table.search().limit(100000).to_pandas()
        if df.empty:
            return []
        return [row.dropna().to_dict() for _, row in df[df["disease"] != disease].iterrows()]

    def _existing_diseases(self) -> set[str]:
        if PAGEINDEX_TABLE not in self._table_names():
            return set()
        table = self.db.open_table(PAGEINDEX_TABLE)
        df = table.search().limit(100000).to_pandas()
        if df.empty or "disease" not in df.columns:
            return set()
        return {str(value) for value in df["disease"].dropna().unique()}

    def _table_row_count(self, table: Any) -> int:
        try:
            return int(table.count_rows())
        except Exception:
            return 0

    def _table_names(self) -> list[str]:
        tables = self.db.list_tables()
        if hasattr(tables, "tables"):
            return list(tables.tables)
        return list(tables)

    def _create_indexes(self, table: Any, vector_dim: int, row_count: int) -> None:
        num_partitions = max(2, int(math.sqrt(max(row_count, 1))))
        num_sub_vectors = max(1, vector_dim // 8)
        with contextlib.suppress(Exception):
            table.create_index(
                metric="cosine",
                vector_column_name="vector",
                num_partitions=num_partitions,
                num_sub_vectors=num_sub_vectors,
                replace=True,
            )
        with contextlib.suppress(Exception):
            table.create_fts_index("summary", replace=True)
