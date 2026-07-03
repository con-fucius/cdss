"""
Ingestion pipeline for CDSS.

Phase 0 fixes:
- create_index called with explicit vector_column_name="vector" (LanceDB 0.17+)
- log_init wrapped correctly for both sync and async call sites
- All six diseases configured with confirmed PDF paths
- Ingestion aborts and reports clearly if chunk count is zero
- PDF path discovery is config-driven; no hardcoding in callers
"""

from __future__ import annotations

import asyncio
import os
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lancedb
import pandas as pd
from fastembed import TextEmbedding

from .config import DISEASE_CONFIG
from .extractors.pipeline import ExtractionPipeline
from .chunkers.hierarchical import HierarchicalIndexer
from .schema import IndexedChunk
from .logs import log_init
from .indexers.pageindex import PageIndexBuilder

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("LANCEDB_PATH", str(APP_DIR / "lancedb"))
DOCS_DIR = APP_DIR / "docs"

# Confirmed PDF paths relative to DOCS_DIR — verified against actual directory listing
DISEASE_PDF_MAP: Dict[str, str] = {
    "hiv": "HIV-AIDS/Kenya HIV Prevention and Treatment Guidelines 2022.pdf",
    "diabetes": "Diabetes Mellitus/National Clinical Guidelines on Management of Diabetes Mellitus  V15 2024.pdf",
    "cvd": "Cardiovascular Disease/Kenya National Guidelines for The Management of Cardiovascular Diseases.pdf",
    "tb": "TB/Integrated Guideline For Tuberculosis, Leprosy And Lung Disease 2021.pdf",
    "malaria": "Malaria/National Guidelines for the Diagnosis, Treatment and Prevention of Malaria 3rd Edition 2010.pdf",
    "mental_health": "Mental Health/National Clinical Guideline for Management of Common Mental Disorders.pdf",
}


def list_lancedb_tables(db) -> List[str]:
    """Return LanceDB table names using the non-deprecated API."""
    tables = db.list_tables()
    if hasattr(tables, "tables"):
        return list(tables.tables)
    return list(tables)


class IngestionManager:
    def __init__(self, db_path: str = DB_PATH, embedding_cache_dir: Optional[str] = None):
        self.db_path = db_path
        self.db = lancedb.connect(db_path)
        self.extraction_pipeline = ExtractionPipeline()
        if embedding_cache_dir is None:
            embedding_cache_dir = os.environ.get("FASTEMBED_CACHE_DIR")
        self.embedding_cache_dir = embedding_cache_dir
        if self.embedding_cache_dir:
            Path(self.embedding_cache_dir).mkdir(parents=True, exist_ok=True)
            self.embedding_model = TextEmbedding(
                model_name="BAAI/bge-base-en-v1.5",
                cache_dir=self.embedding_cache_dir,
            )
        else:
            self.embedding_model = TextEmbedding(model_name="BAAI/bge-base-en-v1.5")

    def get_pdf_path(self, disease: str) -> Path:
        """Return the confirmed absolute PDF path for a disease."""
        rel = DISEASE_PDF_MAP.get(disease.lower())
        if not rel:
            raise ValueError(f"No PDF mapping for disease: {disease!r}")
        path = DOCS_DIR / rel
        if not path.exists():
            raise FileNotFoundError(
                f"PDF not found for {disease!r}: {path}\n"
                "Check DISEASE_PDF_MAP in ingest.py matches the actual file."
            )
        return path

    def index_disease(
        self,
        disease: str,
        pdf_path: Optional[str] = None,
        guideline_name: Optional[str] = None,
    ) -> Tuple[str, int]:
        """
        Index one disease guideline into LanceDB.

        Returns (table_name, chunk_count).
        Raises RuntimeError if chunk count is zero — do not proceed silently.
        """
        start_time = time.time()
        table_name = f"{disease.lower()}_guidelines"

        resolved_pdf = pdf_path or str(self.get_pdf_path(disease))
        resolved_name = guideline_name or DISEASE_CONFIG.get(
            disease.lower(), {}
        ).get("guideline_name", f"{disease.upper()} Guidelines")

        logger.info("Ingesting %s from %s", disease, resolved_pdf)

        # 1. Extract
        extraction_result = self.extraction_pipeline.extract(resolved_pdf, disease)
        logger.info(
            "Extraction: extractor=%s quality=%.2f items=%d",
            extraction_result.extractor_name,
            extraction_result.quality_score,
            len(extraction_result.content),
        )

        # 2. Hierarchical chunking
        indexer = HierarchicalIndexer(disease, resolved_name)
        indexed_chunks: List[IndexedChunk] = indexer.process(extraction_result.content)

        if not indexed_chunks:
            raise RuntimeError(
                f"Ingestion produced 0 chunks for {disease!r} using "
                f"{extraction_result.extractor_name}. "
                "Check extractor output and HierarchicalIndexer.process()."
            )
        logger.info("Chunking: %d chunks produced", len(indexed_chunks))

        # 3. Embed
        texts = [c.text for c in indexed_chunks]
        embeddings = list(self.embedding_model.embed(texts))

        # 4. Build LanceDB rows
        data = [
            chunk.to_dict(vector=embeddings[i].tolist())
            for i, chunk in enumerate(indexed_chunks)
        ]

        # 5. Write table (drop existing so schema is always clean)
        if table_name in list_lancedb_tables(self.db):
            self.db.drop_table(table_name)
        table = self.db.create_table(table_name, data=data)

        # 6. Index creation — explicit vector_column_name required in LanceDB 0.17+
        try:
            table.create_index(
                metric="cosine",
                vector_column_name="vector",
                num_partitions=32,
                num_sub_vectors=16,
                replace=True,
            )
            logger.info("IVF-PQ vector index created for %s", table_name)
        except Exception as exc:
            # Non-fatal: vector search still works via flat scan on small tables
            logger.warning(
                "Vector index creation failed for %s (flat scan will be used): %s",
                table_name,
                exc,
            )

        try:
            table.create_fts_index("text", replace=True)
            logger.info("FTS index created for %s", table_name)
        except Exception as exc:
            logger.warning("FTS index creation failed for %s: %s", table_name, exc)

        latency = (time.time() - start_time) * 1000
        logger.info(
            "Ingestion complete: disease=%s table=%s chunks=%d latency=%.0fms",
            disease,
            table_name,
            len(indexed_chunks),
            latency,
        )

        # 7. Audit log — safe in both sync and async contexts
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                log_init(
                    event="success",
                    disease=disease,
                    doc_name=Path(resolved_pdf).name,
                    chunk_count=len(indexed_chunks),
                    latency_ms=latency,
                    extractor_used=extraction_result.extractor_name,
                    quality_score=extraction_result.quality_score,
                )
            )
        except RuntimeError:
            # No running loop — called from a script; run synchronously
            asyncio.run(
                log_init(
                    event="success",
                    disease=disease,
                    doc_name=Path(resolved_pdf).name,
                    chunk_count=len(indexed_chunks),
                    latency_ms=latency,
                    extractor_used=extraction_result.extractor_name,
                    quality_score=extraction_result.quality_score,
                )
            )

        # 8. PageIndex — page-level retrieval surface for Phase 2.
        try:
            page_count = self._run_async(
                PageIndexBuilder(
                    db_path=self.db_path,
                    embedding_cache_dir=self.embedding_cache_dir,
                ).index_pdf(disease.lower(), resolved_pdf)
            )
            logger.info("PageIndex complete: disease=%s pages=%d", disease, page_count)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"PageIndex failed for {disease}: {exc}") from exc

        return table_name, len(indexed_chunks)

    def _run_async(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError(
            "IngestionManager.index_disease is synchronous; call it outside an "
            "active event loop so PageIndex can complete before returning."
        )

    def index_all(self, diseases: Optional[List[str]] = None) -> Dict[str, int]:
        """
        Index all configured diseases (or a subset).
        Returns {disease: chunk_count}. Logs but does not raise on per-disease failure.
        """
        targets = diseases or list(DISEASE_PDF_MAP.keys())
        results: Dict[str, int] = {}
        for disease in targets:
            try:
                _, count = self.index_disease(disease)
                results[disease] = count
            except Exception as exc:
                logger.error("Failed to index %s: %s", disease, exc)
                results[disease] = 0
        return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser(description="CDSS ingestion pipeline")
    parser.add_argument(
        "--disease",
        nargs="+",
        choices=list(DISEASE_PDF_MAP.keys()) + ["all"],
        default=["all"],
        help="Disease(s) to index. Use 'all' for every configured disease.",
    )
    parser.add_argument(
        "--db-path",
        default=DB_PATH,
        help="Path to LanceDB directory",
    )
    args = parser.parse_args()

    targets = list(DISEASE_PDF_MAP.keys()) if "all" in args.disease else args.disease
    manager = IngestionManager(db_path=args.db_path)
    summary = manager.index_all(diseases=targets)

    print("\nIngestion summary:")
    for disease, count in summary.items():
        status = "✓" if count > 0 else "✗ FAILED"
        print(f"  {status}  {disease}: {count} chunks")


# Legacy compatibility shim
def index_data(
    pdf_path: str,
    disease: str = "hiv",
    guideline_name: str = "Kenya National Guidelines",
):
    """DEPRECATED: use IngestionManager.index_disease directly."""
    manager = IngestionManager()
    table_name, _ = manager.index_disease(disease, pdf_path, guideline_name)
    return manager.db.open_table(table_name)


if __name__ == "__main__":
    main()
