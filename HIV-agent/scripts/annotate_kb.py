"""
scripts/annotate_kb.py

Offline Knowledge Base Annotation Script
=========================================

Reads every guideline chunk and pageindex summary from LanceDB and annotates
them with matched UMLS concepts via TerminologyService.link_text().
Results are written in batches to the guideline_chunk_concepts Postgres table.

Safety guarantees
-----------------
- This script is COMPLETELY OFFLINE from the live FastAPI server.
- It does not modify LanceDB, search_guidelines(), or any live chat path.
- It is safe to run while the server is running; all writes go to Postgres only.
- The script is IDEMPOTENT: re-running it will upsert, not duplicate.
- Progress is logged but not checkpointed. If interrupted, rerun the same command;
  existing (chunk_id, CUI) rows are updated instead of duplicated.

Usage
-----
    uv run python scripts/annotate_kb.py [--disease hiv] [--dry-run] [--batch-size 50]

Arguments
---------
    --disease <id>    Annotate only one disease (e.g. "hiv"). Default: all.
    --dry-run         Run annotation without writing to Postgres.
    --batch-size <n>  Number of concepts to upsert per DB round-trip. Default: 50.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Bootstrap: put the project root on sys.path so relative package imports work
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("annotate_kb")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _upsert_batch(
    rows: List[Dict],
    dry_run: bool,
) -> int:
    """Upsert a batch of concept rows into guideline_chunk_concepts.
    Returns the number of rows attempted."""
    if not rows:
        return 0
    if dry_run:
        logger.info("Dry run: would upsert %d concept rows", len(rows))
        return 0

    from app.db import get_session
    from app.terminology.models import GuidelineChunkConcept
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with get_session() as session:
        stmt = pg_insert(GuidelineChunkConcept).values(rows)
        # On conflict, update confidence and annotation_source (idempotent)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_gcc_chunk_cui",
            set_={
                "confidence": stmt.excluded.confidence,
                "annotation_source": stmt.excluded.annotation_source,
            },
        )
        await session.execute(stmt)
        await session.commit()

    return len(rows)


async def _annotate_table(
    index,
    term_svc,
    table_name: str,
    disease: str,
    batch_size: int,
    dry_run: bool,
) -> int:
    """
    Annotate one LanceDB guideline table.
    Returns total concepts written.
    """
    logger.info("Annotating table '%s' for disease '%s'", table_name, disease)
    try:
        df = await asyncio.to_thread(
            lambda: index.db.open_table(table_name).search().limit(200_000).to_pandas()
        )
    except Exception as exc:
        logger.error("Failed to open table '%s': %s", table_name, exc)
        return 0

    if df.empty:
        logger.warning("Table '%s' is empty — nothing to annotate.", table_name)
        return 0

    total = len(df)
    logger.info("  Loaded %d chunks from '%s'", total, table_name)

    written = 0
    batch: List[Dict] = []

    for idx, (_, row) in enumerate(df.iterrows()):
        chunk_id = str(row.get("chunk_id", "")).strip()
        text = str(row.get("text", "")).strip()

        if not chunk_id or not text:
            continue

        try:
            concepts = await term_svc.link_text(text=text, disease=disease)
        except Exception as exc:
            logger.warning("  link_text failed for chunk '%s': %s", chunk_id, exc)
            continue

        for c in concepts:
            if not c.get("cui"):
                continue
            batch.append({
                "chunk_id": chunk_id,
                "cui": c["cui"],
                "preferred_name": c.get("preferred_name", "")[:500],
                "disease": disease,
                "confidence": float(c.get("confidence", 1.0)),
                "annotation_source": c.get("annotation_source", "exact_alias"),
            })

        if len(batch) >= batch_size:
            written += await _upsert_batch(batch, dry_run)
            batch = []

        if (idx + 1) % 100 == 0:
            logger.info("  Progress: %d/%d chunks processed", idx + 1, total)

    # Flush remaining
    if batch:
        written += await _upsert_batch(batch, dry_run)

    logger.info("  Done: %d concept-rows written for '%s'", written, table_name)
    return written


async def _annotate_pageindex(
    index,
    term_svc,
    disease: str,
    batch_size: int,
    dry_run: bool,
) -> int:
    """
    Annotate the pageindex_chunks LanceDB table.
    chunk_id is synthesised as 'page_<disease>_<page>' since pageindex rows
    have no native chunk_id — they are page-level summaries.
    """
    PAGEINDEX_TABLE = "pageindex_chunks"

    if PAGEINDEX_TABLE not in index.table_names():
        logger.info("No '%s' table found — skipping pageindex annotation.", PAGEINDEX_TABLE)
        return 0

    logger.info("Annotating pageindex for disease '%s'", disease)

    # Filter by disease in Python after load — avoids raw SQL string injection
    try:
        df = await asyncio.to_thread(
            lambda: index.db.open_table(PAGEINDEX_TABLE).search().limit(200_000).to_pandas()
        )
    except Exception as exc:
        logger.error("Failed to open '%s': %s", PAGEINDEX_TABLE, exc)
        return 0

    if df.empty or "disease" not in df.columns:
        logger.warning("pageindex_chunks is empty or missing 'disease' column.")
        return 0

    df = df[df["disease"].str.lower() == disease.lower()]
    if df.empty:
        logger.info("  No pageindex rows found for disease '%s'.", disease)
        return 0

    logger.info("  Found %d pageindex rows for '%s'", len(df), disease)

    written = 0
    batch: List[Dict] = []

    for _, row in df.iterrows():
        page = int(row.get("page", 0) or 0)
        # Use summary if present and non-empty, fall back to text
        summary = str(row.get("summary", "")).strip()
        text = str(row.get("text", "")).strip()
        content = summary or text
        if not content:
            continue

        # Synthesised chunk_id — stable and queryable by the X-Ray endpoint
        chunk_id = f"page_{disease}_{page}"

        try:
            concepts = await term_svc.link_text(text=content, disease=disease)
        except Exception as exc:
            logger.warning("  link_text failed for pageindex chunk '%s': %s", chunk_id, exc)
            continue

        for c in concepts:
            if not c.get("cui"):
                continue
            batch.append({
                "chunk_id": chunk_id,
                "cui": c["cui"],
                "preferred_name": c.get("preferred_name", "")[:500],
                "disease": disease,
                "confidence": float(c.get("confidence", 1.0)),
                "annotation_source": c.get("annotation_source", "exact_alias"),
            })

        if len(batch) >= batch_size:
            written += await _upsert_batch(batch, dry_run)
            batch = []

    if batch:
        written += await _upsert_batch(batch, dry_run)

    logger.info("  Done: %d concept-rows written for pageindex '%s'", written, disease)
    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(disease_filter: Optional[str], dry_run: bool, batch_size: int) -> None:
    from app.config import DISEASE_CONFIG
    from app.search_tools import SearchIndex
    from app.terminology.service import TerminologyService

    if dry_run:
        logger.info("DRY RUN MODE — no data will be written to Postgres.")

    # Initialise the singleton SearchIndex
    try:
        index = SearchIndex()
    except Exception as exc:
        logger.error("Failed to initialise SearchIndex (is LanceDB available?): %s", exc)
        sys.exit(1)

    term_svc = TerminologyService()

    diseases = [disease_filter] if disease_filter else list(DISEASE_CONFIG.keys())
    # Validate user-supplied disease
    if disease_filter and disease_filter not in DISEASE_CONFIG:
        logger.error("Unknown disease '%s'. Valid options: %s", disease_filter, list(DISEASE_CONFIG.keys()))
        sys.exit(1)

    grand_total = 0

    for disease in diseases:
        logger.info("=== Starting annotation for disease: %s ===", disease)

        # 1. Guideline chunks
        table_names = index._get_table_names(disease)
        if not table_names:
            logger.warning("No LanceDB tables found for disease '%s' — skipping.", disease)
        else:
            for table_name in table_names:
                written = await _annotate_table(
                    index, term_svc, table_name, disease, batch_size, dry_run
                )
                grand_total += written

        # 2. Pageindex summaries (Vector 5: concept-aware pageindex)
        written = await _annotate_pageindex(
            index, term_svc, disease, batch_size, dry_run
        )
        grand_total += written

        logger.info("=== Completed annotation for disease: %s ===", disease)

    logger.info("ANNOTATION COMPLETE. Total concept-rows written: %d", grand_total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Annotate CDSS knowledge base with UMLS concepts.")
    parser.add_argument("--disease", type=str, default=None, help="Annotate one disease only (e.g. 'hiv').")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to Postgres.")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for Postgres upsert (default: 50).")
    args = parser.parse_args()

    asyncio.run(main(
        disease_filter=args.disease,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    ))
