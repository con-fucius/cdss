r"""
Download bge-base-en-v1.5 model files to a local directory (no blob/symlink cache).
Bypasses the Windows symlink privilege issue in huggingface_hub's default cache.

Run from D:\Projects\CDSS\HIV-agent:
    .\.venv\Scripts\python.exe -m scripts.download_model_local

Outputs to app/data/models/bge-base-en-v1.5-onnx-q/
"""
import os
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from huggingface_hub import snapshot_download

TARGET = ROOT / "app" / "data" / "models" / "bge-base-en-v1.5-onnx-q"
TARGET.mkdir(parents=True, exist_ok=True)

print(f"Downloading to: {TARGET}", flush=True)
snapshot_download(
    repo_id="qdrant/bge-base-en-v1.5-onnx-q",
    local_dir=str(TARGET),
    local_dir_use_symlinks=False,
    cache_dir=str(TARGET / "_cache"),
    allow_patterns=["*.json", "*.onnx", "*.txt"],
)
print("DONE", flush=True)

# Show what we got
for p in sorted(TARGET.rglob("*")):
    if p.is_file() and "_cache" not in str(p):
        print(f"  {p.relative_to(TARGET)}: {p.stat().st_size / 1024:.1f} KB", flush=True)
