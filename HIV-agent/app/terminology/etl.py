"""app/terminology/etl.py.

ETL adapter: reads UMLS-repo processed output and loads it into
the CDSS Postgres terminology tables.

What this file does
-------------------
1. Reads concepts.jsonl and relations.csv produced by CDSS-UMLS/etl/combine_umls.py.
2. Filters to a clinically scoped subset relevant to the six CDSS diseases.
3. Bulk-upserts into terminology_concepts, terminology_aliases, and
   terminology_relations using INSERT ON CONFLICT DO UPDATE.

What this file does NOT do
--------------------------
- It does not import anything from the CDSS-UMLS repo at runtime.
  The UMLS repo is run offline to produce processed files; this adapter
  reads only its output artifacts (JSONL / CSV).  No runtime coupling.
- It does not write to guideline_chunk_concepts — that happens during
  ingestion via annotate_chunks().
- It does not call the UMLS REST API.
  UMLSService in the UMLS repo has a broken semantic_types parameter
  (passes TUIs as 'sabs') and an unclosed httpx.AsyncClient.
  We do not transplant that class.

Usage
-----
From the project root:
    uv run python -m app.terminology.etl \
        --concepts /path/to/data/umls/processed/concepts.jsonl \
        --relations /path/to/data/umls/processed/relations.csv \
        [--disease-filter]    # restrict to clinically scoped concepts only
        [--limit 100000]      # for development / smoke-test runs

Environment
-----------
Requires DATABASE_URL in app/.env.
Run after Alembic migration 0005 has been applied.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# ─────────────────────────────────────────────────────────────────────────────
# Clinical scope filter
# UMLS has ~3 million concepts.  We load only concepts whose semantic types
# overlap with what is clinically relevant for the six CDSS diseases.
# This keeps the Postgres table at a manageable size (<500 k rows typically).
# ─────────────────────────────────────────────────────────────────────────────

# TUI codes for semantic types we keep
CLINICAL_TUIS: set[str] = {
    "T047",  # Disease or Syndrome
    "T048",  # Mental or Behavioral Dysfunction
    "T191",  # Neoplastic Process
    "T190",  # Anatomical Abnormality
    "T033",  # Finding
    "T184",  # Sign or Symptom
    "T034",  # Laboratory or Test Result
    "T059",  # Laboratory Procedure
    "T060",  # Diagnostic Procedure
    "T061",  # Therapeutic or Preventive Procedure
    "T116",  # Amino Acid, Peptide, or Protein
    "T121",  # Pharmacologic Substance
    "T109",  # Organic Chemical
    "T200",  # Clinical Drug
    "T074",  # Medical Device
    "T031",  # Body Substance
    "T023",  # Body Part, Organ, or Organ Component
    "T046",  # Pathologic Function
    "T039",  # Physiologic Function
    "T042",  # Organ or Tissue Function
    "T126",  # Enzyme
    "T125",  # Hormone
    "T129",  # Immunologic Factor
    "T130",  # Antibiotic
}

# Preferred sources — we prefer MSH and SNOMED for concept names
PREFERRED_SOURCES = {"MSH", "SNOMEDCT_US", "ICD10CM", "RXNORM", "ICD10"}

# Relation types we retain — clinically interpretable only
KEPT_RELATION_TYPES = {"RB", "RN", "CHD", "PAR", "RO", "SIB"}

# Relation labels we definitely want regardless of relation type
KEPT_RELATION_LABELS = {
    "may_treat",
    "treats",
    "has_ingredient",
    "ingredient_of",
    "may_prevent",
    "prevents",
    "is_diagnosed_by",
    "diagnoses",
    "associated_with",
    "has_manifestation",
    "manifestation_of",
    "has_contraindication",
    "contraindicated_with",
}

# Batch sizes for bulk upsert
_CONCEPT_BATCH = 2000
_ALIAS_BATCH = 5000
_RELATION_BATCH = 5000


def _semantic_type_values(concept: dict[str, Any]) -> list[str]:
    """Return searchable semantic type names plus TUI codes from source JSON."""
    values: set[str] = set()
    for item in concept.get("semantic_types") or []:
        if item:
            values.add(str(item).strip())
    for item in concept.get("semantic_type_details") or []:
        if not isinstance(item, dict):
            continue
        for key in ("tui", "semantic_type"):
            value = item.get(key)
            if value:
                values.add(str(value).strip())
    return sorted(v for v in values if v)[:40]


# ─────────────────────────────────────────────────────────────────────────────
# Readers
# ─────────────────────────────────────────────────────────────────────────────


def _iter_concepts(
    path: Path,
    disease_filter: bool,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    """Stream concepts.jsonl and yield those that pass the clinical scope filter."""
    count = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                concept = json.loads(line)
            except json.JSONDecodeError:
                continue

            if disease_filter:
                stypes = set(concept.get("semantic_types") or [])
                tuis = {
                    st.get("tui")
                    for st in concept.get("semantic_type_details") or []
                    if st.get("tui")
                }
                if not (stypes | tuis) & CLINICAL_TUIS:
                    # Check by TUI codes directly
                    tui_codes = {
                        st["tui"]
                        for st in (concept.get("semantic_type_details") or [])
                        if "tui" in st
                    }
                    if not tui_codes & CLINICAL_TUIS:
                        continue

            yield concept
            count += 1
            if limit and count >= limit:
                break

    logger.info("Streamed %d concepts from %s", count, path.name)


def _iter_relations(
    path: Path,
    kept_cuis: set[str] | None,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    """Stream relations.csv and yield clinically relevant rows."""
    count = 0
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rel_type = (row.get("relation") or "").strip()
            rel_label = (row.get("relation_label") or "").strip().lower()
            cui1 = (row.get("cui1") or "").strip()
            cui2 = (row.get("cui2") or "").strip()

            if not (cui1 and cui2):
                continue
            if rel_type not in KEPT_RELATION_TYPES and rel_label not in KEPT_RELATION_LABELS:
                continue
            if kept_cuis and (cui1 not in kept_cuis or cui2 not in kept_cuis):
                continue

            yield {
                "cui1": cui1,
                "cui2": cui2,
                "relation_type": rel_type,
                "relation_label": row.get("relation_label") or "",
                "source_sab": (row.get("source") or "").strip(),
            }
            count += 1
            if limit and count >= limit:
                break

    logger.info("Streamed %d relations from %s", count, path.name)


# ─────────────────────────────────────────────────────────────────────────────
# Bulk upsert helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _upsert_concepts(
    concepts: list[dict[str, Any]],
) -> set[str]:
    """Bulk-upsert a batch of concepts. Returns the set of CUIs loaded."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from ..db import get_session
    from .models import TerminologyAlias, TerminologyConcept

    cui_set: set[str] = set()
    alias_rows: list[dict[str, Any]] = []

    concept_rows = []
    for c in concepts:
        cui = (c.get("cui") or "").strip()
        preferred_name = (c.get("preferred_name") or c.get("preferred_term") or "").strip()
        if not cui or not preferred_name:
            continue

        # Best definition: prefer MSH, then any non-empty
        defs = c.get("definitions") or []
        definition = ""
        for d in defs:
            if isinstance(d, dict) and d.get("definition"):
                if d.get("source") in PREFERRED_SOURCES or not definition:
                    definition = d["definition"]
                    if d.get("source") in PREFERRED_SOURCES:
                        break

        concept_rows.append(
            {
                "cui": cui,
                "preferred_name": preferred_name[:500],
                "definition": definition[:4000] if definition else None,
                "semantic_types": _semantic_type_values(c),
                "synonyms": (c.get("synonyms") or [])[:50],
                "codes": (c.get("codes") or [])[:30],
                "sources": (c.get("sources") or [])[:20],
            }
        )
        cui_set.add(cui)

        # Build alias rows: preferred_name + synonyms
        for alias_str in [preferred_name] + (c.get("synonyms") or [])[:30]:
            alias_str = (alias_str or "").strip()
            if alias_str and len(alias_str) >= 3:
                alias_rows.append({"cui": cui, "alias": alias_str[:500], "source_sab": ""})

    if not concept_rows:
        return cui_set

    async with get_session() as session:
        stmt = pg_insert(TerminologyConcept).values(concept_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["cui"],
            set_={
                "preferred_name": stmt.excluded.preferred_name,
                "definition": stmt.excluded.definition,
                "semantic_types": stmt.excluded.semantic_types,
                "synonyms": stmt.excluded.synonyms,
                "codes": stmt.excluded.codes,
                "sources": stmt.excluded.sources,
            },
        )
        await session.execute(stmt)

        # Alias rows — deduplicate in the batch first
        seen_alias = set()
        deduped_aliases = []
        for row in alias_rows:
            key = (row["cui"], row["alias"].lower())
            if key not in seen_alias:
                seen_alias.add(key)
                deduped_aliases.append(row)

        if deduped_aliases:
            for i in range(0, len(deduped_aliases), _ALIAS_BATCH):
                batch = deduped_aliases[i : i + _ALIAS_BATCH]
                alias_stmt = pg_insert(TerminologyAlias).values(batch)
                alias_stmt = alias_stmt.on_conflict_do_nothing(constraint="uq_alias_cui_alias")
                await session.execute(alias_stmt)

        await session.commit()

    return cui_set


async def _upsert_relations(relations: list[dict[str, Any]]) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from ..db import get_session
    from .models import TerminologyRelation

    if not relations:
        return

    async with get_session() as session:
        stmt = pg_insert(TerminologyRelation).values(relations)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_tr_triple_source",
            set_={"relation_label": stmt.excluded.relation_label},
        )
        await session.execute(stmt)
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Main ETL entry point
# ─────────────────────────────────────────────────────────────────────────────


async def load_umls(
    concepts_path: Path,
    relations_path: Path,
    disease_filter: bool = True,
    limit: int | None = None,
    skip_relations: bool = False,
) -> dict[str, int]:
    """Load UMLS processed output into Postgres terminology tables.

    concepts_path:  Path to concepts.jsonl from combine_umls.py output.
    relations_path: Path to relations.csv from combine_umls.py output.
    disease_filter: When True, restrict to CLINICAL_TUIS scope (~500 k concepts).
                    When False, load everything (~3 M concepts — use only if you
                    have abundant disk and time).
    limit:          Stop after this many concepts (for dev/smoke-test).
    skip_relations: Skip relation loading (useful for initial concept-only load).

    Returns: {concepts_loaded, aliases_loaded, relations_loaded}
    """
    if not concepts_path.exists():
        raise FileNotFoundError(f"concepts.jsonl not found: {concepts_path}")
    if not relations_path.exists() and not skip_relations:
        raise FileNotFoundError(f"relations.csv not found: {relations_path}")

    logger.info("=" * 60)
    logger.info("UMLS ETL: loading into CDSS Postgres")
    logger.info("  concepts:  %s", concepts_path)
    logger.info("  relations: %s", relations_path)
    logger.info("  disease_filter: %s", disease_filter)
    logger.info("  limit: %s", limit or "none")
    logger.info("=" * 60)

    # ── Concepts ──────────────────────────────────────────────────────
    total_concepts = 0
    loaded_cuis: set[str] = set()
    batch: list[dict[str, Any]] = []

    for concept in _iter_concepts(concepts_path, disease_filter, limit):
        batch.append(concept)
        if len(batch) >= _CONCEPT_BATCH:
            cuis = await _upsert_concepts(batch)
            loaded_cuis |= cuis
            total_concepts += len(cuis)
            batch = []
            if total_concepts % 10000 == 0:
                logger.info("  %d concepts loaded so far", total_concepts)

    if batch:
        cuis = await _upsert_concepts(batch)
        loaded_cuis |= cuis
        total_concepts += len(cuis)

    logger.info("Concepts loaded: %d", total_concepts)

    # ── Relations ──────────────────────────────────────────────────────
    total_relations = 0
    if not skip_relations and relations_path.exists():
        rel_batch: list[dict[str, Any]] = []
        for relation in _iter_relations(relations_path, loaded_cuis, limit):
            rel_batch.append(relation)
            if len(rel_batch) >= _RELATION_BATCH:
                await _upsert_relations(rel_batch)
                total_relations += len(rel_batch)
                rel_batch = []
        if rel_batch:
            await _upsert_relations(rel_batch)
            total_relations += len(rel_batch)

    logger.info("Relations loaded: %d", total_relations)
    logger.info("=" * 60)
    logger.info("ETL complete.")
    logger.info("=" * 60)

    return {
        "concepts_loaded": total_concepts,
        "relations_loaded": total_relations,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Load UMLS processed output into CDSS Postgres terminology tables"
    )
    parser.add_argument(
        "--concepts",
        required=True,
        help="Path to concepts.jsonl (output of combine_umls.py)",
    )
    parser.add_argument(
        "--relations",
        required=True,
        help="Path to relations.csv (output of combine_umls.py)",
    )
    parser.add_argument(
        "--no-disease-filter",
        action="store_true",
        help="Load all UMLS concepts, not just the clinical scope subset",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N concepts (for development / smoke test)",
    )
    parser.add_argument(
        "--skip-relations",
        action="store_true",
        help="Skip relation loading",
    )
    args = parser.parse_args()

    result = asyncio.run(
        load_umls(
            concepts_path=Path(args.concepts),
            relations_path=Path(args.relations),
            disease_filter=not args.no_disease_filter,
            limit=args.limit,
            skip_relations=args.skip_relations,
        )
    )
    print(
        f"\nDone. concepts_loaded={result['concepts_loaded']} "
        f"relations_loaded={result['relations_loaded']}"
    )


if __name__ == "__main__":
    main()
