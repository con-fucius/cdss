"""DEPRECATED emergency repair for one Windows FastEmbed cache failure.

Normal development and Docker runtime use the default Hugging Face/FastEmbed
cache locations. Do not run this as part of setup unless you have confirmed a
local cache corruption matching the historical Windows symlink issue.
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOCAL_DIR = ROOT / "app" / "data" / "models" / "bge-base-en-v1.5-onnx-q"
CACHE_DIR = ROOT / "app" / "data" / "fastembed_cache" / "models--qdrant--bge-base-en-v1.5-onnx-q"

# Find the snapshot dir (it's a single dir under snapshots/)
snapshots_root = CACHE_DIR / "snapshots"
if not snapshots_root.exists():
    print(f"No snapshots dir at {snapshots_root}")
    sys.exit(1)
snapshots = list(snapshots_root.iterdir())
if not snapshots:
    print("Snapshot dir is empty")
    sys.exit(1)
snapshot_dir = snapshots[0]
print(f"Snapshot dir: {snapshot_dir}", flush=True)

EXPECTED = [
    "config.json",
    "model_optimized.onnx",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "ort_config.json",
]

copied = 0
for fname in EXPECTED:
    target = snapshot_dir / fname
    if target.exists():
        print(f"  [ok]   {fname}", flush=True)
        continue
    source = LOCAL_DIR / fname
    if not source.exists():
        print(f"  [skip] {fname} (not in local dir)", flush=True)
        continue
    shutil.copy2(source, target)
    print(f"  [copy] {fname} -> {target.name}", flush=True)
    copied += 1

print(f"Done. {copied} file(s) copied.", flush=True)
