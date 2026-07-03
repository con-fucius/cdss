r"""DEPRECATED: do not use as a bulk remediation entrypoint.

This script retries HIV plus four other PDFs in one long process and can
recreate the prior failure mode where one problematic PDF blocks unrelated
foundation work. Keep it only as historical context. Current indexing should
be run per disease with explicit timeouts and separate logs.

Original scope:
Index the remaining 5 diseases in one process. Logs to file.

Run from D:\\Projects\\CDSS\\HIV-agent:
    .\\.venv\\Scripts\\python.exe -m scripts.index_remaining_5
"""

import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import logging
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_PATH = ROOT / "app" / "data" / "index_remaining_5.log"
fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
sh = logging.StreamHandler(sys.stdout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[fh, sh],
)
log = logging.getLogger("index_5")

REMAINING = ["hiv", "diabetes", "cvd", "tb", "mental_health"]


def main():
    # Step 2: manager
    log.info("=" * 60)
    log.info("STEP 2: Manager init (loads bge model — ~4 min once)")
    log.info("=" * 60)
    t = time.time()
    from app.config import DISEASE_CONFIG
    from app.ingest import IngestionManager, list_lancedb_tables

    mgr = IngestionManager()
    log.info("Manager ready in %.1fs. Tables: %s", time.time() - t, list_lancedb_tables(mgr.db))

    # Step 3: index each disease
    summary = []
    for disease in REMAINING:
        log.info("=" * 60)
        log.info("INDEX: %s", disease)
        log.info("=" * 60)
        cfg = DISEASE_CONFIG[disease]
        log.info("Guideline: %s", cfg["guideline_name"])
        log.info("Table name: %s", cfg["table_name"])
        t0 = time.time()
        try:
            table_name, chunk_count = mgr.index_disease(
                disease,
                guideline_name=cfg["guideline_name"],
            )
            elapsed = time.time() - t0
            log.info("OK: %s -> %s, %d chunks in %.1fs", disease, table_name, chunk_count, elapsed)
            summary.append((disease, table_name, chunk_count, elapsed, None))
        except Exception as e:
            elapsed = time.time() - t0
            log.error("FAIL: %s after %.1fs: %s", disease, elapsed, e)
            log.error(traceback.format_exc())
            summary.append((disease, None, 0, elapsed, str(e)))

    # Step 4: final report
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    for disease, table, chunks, elapsed, err in summary:
        if err:
            log.info("  %-15s FAIL: %s", disease, err[:80])
        else:
            log.info("  %-15s OK  %-30s chunks=%-4d t=%.1fs", disease, table, chunks, elapsed)
    log.info("=" * 60)
    log.info("DONE")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
