"""
DEPRECATED DIAGNOSTIC: synthetic LanceDB write probe.

This script can leave Windows-locked Lance temp files when the local LanceDB
writer fails with Access is denied. Use it only as a manual host-side
diagnostic, not as a normal build/check step.

Original scope:
Synthetic Phase 0.1 test: prove the lancedb write path works end-to-end
without needing a PDF. Uses fake chunks, real embedding, real lancedb.

Run from D:\\Projects\\CDSS\\HIV-agent:
    .\\.venv\\Scripts\\python.exe -m scripts.synthetic_index_test
"""
import os
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import sys
import time
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_PATH = ROOT / "app" / "data" / "synthetic_index.log"
fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
sh = logging.StreamHandler(sys.stdout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[fh, sh],
)
log = logging.getLogger("synth")

# Step 2: imports
log.info("=" * 60)
log.info("STEP 2: Imports + manager init")
log.info("=" * 60)
t = time.time()
from app.ingest import IngestionManager, list_lancedb_tables
log.info("Imports OK in %.1fs", time.time() - t)

t = time.time()
mgr = IngestionManager()
log.info("Manager ready in %.1fs", time.time() - t)
log.info("Existing tables: %s", list_lancedb_tables(mgr.db))

# Step 3: build 60 synthetic chunks for malaria
log.info("=" * 60)
log.info("STEP 3: Build 60 synthetic chunks")
log.info("=" * 60)
from app.schema import IndexedChunk
N = 60
chunks = []
for i in range(N):
    chunks.append(
        IndexedChunk(
            text=f"Synthetic malaria chunk {i}: ACT treatment guidelines for uncomplicated malaria in adults. "
                 f"Artemether-lumefantrine (AL) is the first-line therapy. Dose by weight band. "
                 f"Section {i//10 + 1}.{i%10 + 1}, page {i+1}.",
            parent_text=f"Parent section {i//10 + 1} covers dosing and administration.",
            disease="malaria",
            guideline_name="National Guidelines for the Diagnosis, Treatment and Prevention of Malaria (3rd Edition, 2010)",
            section_title=f"Section {i//10 + 1}.{i%10 + 1}",
            page=i + 1,
            section_number=f"{i//10 + 1}.{i%10 + 1}",
            content_type="narrative",
        )
    )
log.info("Built %d chunks", len(chunks))

# Step 4: embed
log.info("=" * 60)
log.info("STEP 4: Embed")
log.info("=" * 60)
t = time.time()
texts = [c.text for c in chunks]
embeddings = list(mgr.embedding_model.embed(texts))
log.info("Embedded %d chunks in %.1fs, dim=%d", len(embeddings), time.time() - t, len(embeddings[0]))

# Step 5: write lancedb
log.info("=" * 60)
log.info("STEP 5: Write lancedb table")
log.info("=" * 60)
table_name = "malaria_guidelines_synthetic"
t = time.time()
if table_name in list_lancedb_tables(mgr.db):
    mgr.db.drop_table(table_name)
data = [
    chunk.to_dict(vector=embeddings[i].tolist())
    for i, chunk in enumerate(chunks)
]
table = mgr.db.create_table(table_name, data=data)
log.info("Created table %s in %.1fs", table_name, time.time() - t)

# Step 6: index
log.info("=" * 60)
log.info("STEP 6: Create IVF-PQ index")
log.info("=" * 60)
t = time.time()
try:
    table.create_index(
        metric="cosine",
        vector_column_name="vector",
        num_partitions=2,
        num_sub_vectors=16,
        replace=True,
    )
    log.info("IVF-PQ index created in %.1fs", time.time() - t)
except Exception as e:
    log.warning("Index failed (non-fatal): %s", e)

# Step 7: FTS
log.info("=" * 60)
log.info("STEP 7: Create FTS index")
log.info("=" * 60)
t = time.time()
try:
    table.create_fts_index("text", replace=True)
    log.info("FTS index created in %.1fs", time.time() - t)
except Exception as e:
    log.warning("FTS failed (non-fatal): %s", e)

# Step 8: verify
log.info("=" * 60)
log.info("STEP 8: Verify")
log.info("=" * 60)
n = table.count_rows()
log.info("rows in %s: %d", table_name, n)
sample = table.search().limit(1).to_list()[0]
log.info("sample disease=%s page=%s", sample.get("disease"), sample.get("page"))
log.info("sample text: %r", (sample.get("text") or "")[:120])

# Step 9: vector search test
log.info("=" * 60)
log.info("STEP 9: Vector search round-trip")
log.info("=" * 60)
t = time.time()
q_emb = list(mgr.embedding_model.embed(["What is the first-line treatment for malaria?"]))[0].tolist()
results = table.search(q_emb).metric("cosine").limit(3).to_list()
log.info("Search took %.1fs, returned %d results", time.time() - t, len(results))
for r in results:
    log.info("  score=%.3f page=%s text=%r",
             1 - r.get("_distance", 0) / 2,
             r.get("page"),
             (r.get("text") or "")[:80])

log.info("=" * 60)
log.info("PASS: synthetic table has %d rows, search works", n)
log.info("=" * 60)
