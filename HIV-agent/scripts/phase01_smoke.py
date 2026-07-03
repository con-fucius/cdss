r"""DEPRECATED DIAGNOSTIC: Phase 0.1 Malaria-only indexing smoke.

This script is retained as historical evidence of the Malaria indexing pass,
not as the current foundation workflow. It writes reports under app/data/ and
does not validate Postgres CRUD, approved memory, evidence graph upserts, or
live API behavior. Prefer the repository/API smoke checks and targeted
PageIndex/indexing commands for current remediation.

Original scope:
Phase 0.1 diagnostic for Malaria (smallest PDF first).
Indexes Malaria end-to-end, then verifies the table.
Run from D:\Projects\CDSS\HIV-agent in PowerShell:

    .\.venv\Scripts\python.exe -m scripts.phase01_smoke

Writes a report to app/data/phase01_malaria_report.txt
"""

from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import logging
import sys
import time
import traceback
from pathlib import Path

# Make `app` importable when run as `python -m scripts.phase01_smoke`
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT_PATH = ROOT / "app" / "data" / "phase01_malaria_report.txt"
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("phase01_smoke")


def section(title: str) -> None:
    log.info("=" * 70)
    log.info("STEP: %s", title)
    log.info("=" * 70)


def main() -> int:
    report_lines: list[str] = []
    report_lines.append("Phase 0.1 smoke report — Malaria\n")
    report_lines.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    report_lines.append(f"Python: {sys.version.split()[0]}\n\n")

    overall_start = time.time()

    # 1. Imports
    section("1. Imports")
    try:
        from app.config import DISEASE_CONFIG
        from app.ingest import DISEASE_PDF_MAP, IngestionManager, list_lancedb_tables

        report_lines.append("[OK] Imports clean\n\n")
    except Exception as e:
        report_lines.append(f"[FAIL] Import error: {e}\n{traceback.format_exc()}\n")
        REPORT_PATH.write_text("".join(report_lines))
        log.error("Import failed: %s", e)
        return 1

    # 2. PDF exists
    section("2. PDF path check")
    rel = DISEASE_PDF_MAP["malaria"]
    pdf_path = ROOT / "app" / "docs" / rel
    log.info("PDF: %s", pdf_path)
    log.info(
        "Exists: %s, Size: %.2f MB",
        pdf_path.exists(),
        pdf_path.stat().st_size / 1e6 if pdf_path.exists() else 0,
    )
    report_lines.append(f"PDF path: {pdf_path}\nExists: {pdf_path.exists()}\n\n")
    if not pdf_path.exists():
        report_lines.append("[FAIL] PDF missing\n")
        REPORT_PATH.write_text("".join(report_lines))
        return 1

    # 3. Connect to lancedb
    section("3. IngestionManager init (bge-base-en-v1.5 model load)")
    try:
        t0 = time.time()
        mgr = IngestionManager()
        log.info(
            "Manager created in %.1fs. Tables: %s", time.time() - t0, list_lancedb_tables(mgr.db)
        )
        report_lines.append(f"IngestionManager init: {time.time() - t0:.1f}s\n")
        report_lines.append(f"Existing lancedb tables: {list_lancedb_tables(mgr.db)}\n\n")
    except Exception as e:
        report_lines.append(f"[FAIL] IngestionManager init: {e}\n{traceback.format_exc()}\n")
        REPORT_PATH.write_text("".join(report_lines))
        return 1

    # 4. Index Malaria only
    section("4. Index Malaria (this is the slow step)")
    t_idx = time.time()
    try:
        table_name, chunk_count = mgr.index_disease(
            "malaria",
            guideline_name=DISEASE_CONFIG["malaria"]["guideline_name"],
        )
        idx_seconds = time.time() - t_idx
        log.info("Indexed in %.1fs, table=%s, chunks=%d", idx_seconds, table_name, chunk_count)
        report_lines.append(
            f"Indexing latency: {idx_seconds:.1f}s\n"
            f"Table: {table_name}\n"
            f"Chunk count: {chunk_count}\n\n"
        )
    except Exception as e:
        report_lines.append(f"[FAIL] Indexing: {e}\n{traceback.format_exc()}\n")
        REPORT_PATH.write_text("".join(report_lines))
        return 1

    if chunk_count == 0:
        report_lines.append("[FAIL] Zero chunks produced\n")
        REPORT_PATH.write_text("".join(report_lines))
        return 1

    # 5. Verify the table
    section("5. Verify table contents")
    try:
        table = mgr.db.open_table(table_name)
        n_rows = table.count_rows()
        log.info("Table has %d rows", n_rows)
        report_lines.append(f"Table row count: {n_rows}\n")

        if n_rows > 0:
            sample = table.search().limit(1).to_list()
            if sample:
                row = sample[0]
                disease_val = row.get("disease")
                guidline_name_val = row.get("guideline_name")
                content_type = row.get("content_type")
                page = row.get("page")
                text_preview = (row.get("text") or "")[:200]
                log.info(
                    "Sample row: disease=%s guideline=%s content_type=%s page=%s",
                    disease_val,
                    guidline_name_val,
                    content_type,
                    page,
                )
                report_lines.append(
                    f"Sample row:\n"
                    f"  disease: {disease_val}\n"
                    f"  guideline_name: {guidline_name_val}\n"
                    f"  content_type: {content_type}\n"
                    f"  page: {page}\n"
                    f"  text preview: {text_preview!r}\n\n"
                )

        # Type breakdown
        all_rows = table.to_pandas() if n_rows < 5000 else None
        if all_rows is not None and "content_type" in all_rows.columns:
            type_counts = all_rows["content_type"].value_counts().to_dict()
            report_lines.append(f"Content type breakdown: {type_counts}\n\n")
        elif all_rows is not None and "type" in all_rows.columns:
            type_counts = all_rows["type"].value_counts().to_dict()
            report_lines.append(f"Type breakdown: {type_counts}\n\n")

        # Pass/fail thresholds
        ok = n_rows > 50 and disease_val == "malaria"
        report_lines.append(f"PASS threshold (>50 rows, disease=='malaria'): {ok}\n\n")
    except Exception as e:
        report_lines.append(f"[FAIL] Verify: {e}\n{traceback.format_exc()}\n")
        REPORT_PATH.write_text("".join(report_lines))
        return 1

    # 6. Final
    total = time.time() - overall_start
    report_lines.append(f"Total wall clock: {total:.1f}s\n")
    report_lines.append(f"Report: {REPORT_PATH}\n")
    REPORT_PATH.write_text("".join(report_lines))
    log.info("=" * 70)
    log.info("DONE. Total %.1fs. Report: %s", total, REPORT_PATH)
    log.info("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
