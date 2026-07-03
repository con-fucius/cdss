"""Seed baseline evidence graph rows from app/data/concepts.

This is an operational bootstrap step, not a migration. Migrations create the
tables; this script loads the packaged guideline-derived graph seeds into
Postgres so local and Docker reviewers see the same readiness state.
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

from app.config import DISEASE_CONFIG
from app.evidence import seed_evidence_graph
from app.repositories import evidence_graph_stats


def _normalise_targets(values: list[str]) -> list[str]:
    if "all" in values:
        return list(DISEASE_CONFIG)
    unknown = sorted(set(values) - set(DISEASE_CONFIG))
    if unknown:
        raise SystemExit(f"Unknown disease key(s): {', '.join(unknown)}")
    return values


async def _seed_targets(targets: Iterable[str], clinician_id: str) -> dict[str, dict[str, int]]:
    seeded: dict[str, dict[str, int]] = {}
    for disease in targets:
        seeded[disease] = await seed_evidence_graph(disease, clinician_id=clinician_id)
        print({"evidence": disease, **seeded[disease]})
    return seeded


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed CDSS evidence graph rows")
    parser.add_argument(
        "--disease",
        nargs="+",
        default=["all"],
        choices=[*DISEASE_CONFIG.keys(), "all"],
        help="Disease key(s) to seed, or all.",
    )
    parser.add_argument(
        "--clinician-id",
        default=os.getenv("CDSS_SEED_CLINICIAN_ID", "system-bootstrap"),
        help="Audit clinician id stored on seeded edges.",
    )
    args = parser.parse_args()

    targets = _normalise_targets(args.disease)

    async def run() -> None:
        seeded = await _seed_targets(targets, clinician_id=args.clinician_id)
        print({"summary": seeded, "current": await evidence_graph_stats()})

    asyncio.run(run())


if __name__ == "__main__":
    main()
