"""Conftest for triage-ranker tests — adds shared/ to sys.path."""

import sys
from pathlib import Path

# Add shared contracts to path so ambulance_cdss_contracts is importable
_shared_dir = str(Path(__file__).resolve().parents[2] / "shared")
if _shared_dir not in sys.path:
    sys.path.insert(0, _shared_dir)
