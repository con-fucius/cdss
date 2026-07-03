"""Build or repair PageIndex rows without re-running full guideline ingestion.

This is intentionally separate from ``app.ingest`` because PageIndex writes can
be repaired independently after a guideline table already exists. Run this from
the project root in a normal PowerShell terminal when LanceDB writes are needed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from app.ingest import DISEASE_PDF_MAP, IngestionManager
from app.indexers.pageindex import PageIndexBuilder
from app.search_tools import SearchIndex


def _normalise_targets(values: list[str]) -> list[str]:
    if "all" in values:
        return list(DISEASE_PDF_MAP)
    unknown = sorted(set(values) - set(DISEASE_PDF_MAP))
    if unknown:
        raise SystemExit(f"Unknown disease key(s): {', '.join(unknown)}")
    return values


def _current_pageindex_diseases() -> set[str]:
    stats = SearchIndex().pageindex_stats()
    return set(stats.get("by_disease", {}))


async def _build_targets(targets: Iterable[str], skip_existing: bool) -> dict[str, int]:
    manager = IngestionManager()
    existing = _current_pageindex_diseases() if skip_existing else set()
    results: dict[str, int] = {}

    for disease in targets:
        if disease in existing:
            print({"pageindex": disease, "status": "skipped_existing"})
            continue
        pdf_path = str(manager.get_pdf_path(disease))
        count = await PageIndexBuilder(
            db_path=manager.db_path,
            embedding_cache_dir=manager.embedding_cache_dir,
        ).index_pdf(disease, pdf_path)
        results[disease] = count
        print({"pageindex": disease, "pages": count})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Build CDSS PageIndex rows")
    parser.add_argument(
        "--disease",
        nargs="+",
        default=["all"],
        choices=[*DISEASE_PDF_MAP.keys(), "all"],
        help="Disease key(s) to build, or all.",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Skip diseases already present in pageindex_chunks.",
    )
    args = parser.parse_args()

    targets = _normalise_targets(args.disease)
    results = asyncio.run(_build_targets(targets, skip_existing=args.missing_only))
    print({"summary": results, "current": SearchIndex().pageindex_stats()})


if __name__ == "__main__":
    main()
