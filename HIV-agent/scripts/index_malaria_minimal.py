r"""Minimal one-shot: index Malaria, log to file, exit.
Skips the smoke diagnostic; just proves the pipeline writes a table.

Run from D:\Projects\CDSS\HIV-agent:
    .\.venv\Scripts\python.exe -m scripts.index_malaria_minimal
"""

import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Both logs and stdout — for redundancy against bash tool quirks
LOG_PATH = ROOT / "app" / "data" / "index_malaria_minimal.log"
file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
stream_handler = logging.StreamHandler(sys.stdout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[file_handler, stream_handler],
)
log = logging.getLogger("index_malaria")

# Step 2: imports
log.info("=" * 60)
log.info("STEP 2: Imports")
log.info("=" * 60)
t = time.time()
from app.config import DISEASE_CONFIG
from app.ingest import IngestionManager, list_lancedb_tables

log.info("Imports OK in %.1fs", time.time() - t)

# Step 3: connect
log.info("=" * 60)
log.info("STEP 3: IngestionManager init (loads bge model)")
log.info("=" * 60)
t = time.time()
mgr = IngestionManager()
log.info("Manager ready in %.1fs. Tables: %s", time.time() - t, list_lancedb_tables(mgr.db))

# Step 4: index
log.info("=" * 60)
log.info("STEP 4: index_disease('malaria')")
log.info("=" * 60)
t = time.time()
table_name, chunk_count = mgr.index_disease(
    "malaria",
    guideline_name=DISEASE_CONFIG["malaria"]["guideline_name"],
)
log.info("Indexed in %.1fs. table=%s chunks=%d", time.time() - t, table_name, chunk_count)

# Step 5: verify
log.info("=" * 60)
log.info("STEP 5: Verify")
log.info("=" * 60)
table = mgr.db.open_table(table_name)
n_rows = table.count_rows()
log.info("table=%s rows=%d", table_name, n_rows)
if n_rows > 0:
    sample = table.search().limit(1).to_list()[0]
    log.info(
        "sample row: disease=%s guideline_name=%s content_type=%s page=%s",
        sample.get("disease"),
        sample.get("guideline_name"),
        sample.get("content_type"),
        sample.get("page"),
    )
    log.info("sample text: %r", (sample.get("text") or "")[:120])

log.info("=" * 60)
log.info("DONE. rows=%d chunks=%d", n_rows, chunk_count)
log.info("=" * 60)
