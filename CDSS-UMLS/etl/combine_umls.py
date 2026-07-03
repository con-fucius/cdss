"""ETL script to combine UMLS RRF files and generate:
- concepts.jsonl (one object per CUI)
- relations.csv (graph edges)
- embeddable_text.jsonl (for RAG)
- neo4j_relations.csv (Neo4j-ready format).
"""

import atexit
import csv
import json
import logging
import warnings
from collections import defaultdict
from pathlib import Path

import pandas as pd
from tqdm import tqdm

# Suppress multiprocessing resource tracker warnings (harmless)
warnings.filterwarnings("ignore", category=UserWarning, module="multiprocessing.resource_tracker")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# RRF file field positions
# MRCONSO: CUI|LAT|TS|LUI|STT|SUI|ISPREF|AUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF
# MRREL: CUI1|AUI1|STYPE1|REL|CUI2|AUI2|STYPE2|RELA|RUI|SRUI|SAB|SL|RG|DIR|SUPPRESS|CVF
# MRSTY: CUI|TUI|STN|STY|ATUI|CVF
# MRDEF: CUI|AUI|ATUI|SATUI|SAB|DEF|SUPPRESS|CVF|SOURCE|CONTEXT
# MRHIER: CUI|AUI|CXN|HTYPE|PTR|HCD|CVF|SAB|REL|RELA|PAUI|SAUI|SCUI|SDUI|SAB|TTY|SUPPRESS|CVF
# MRSAT: CUI|LUI|SUI|AUI|SATUI|ATN|SAB|ATV|SUPPRESS|CVF
# SRDEF: RT|UI|STY_RL|STN|DEF|EX|UN|NH|AB|CH|PN|PM|SY|SN|AQ|HL|HT|CX|HN|UI2|MR|RN|DA|RE|SA|SL|ST|VS|CF|CV|PO|IN|PZ|SH|SN2|DC|RL|SP|SQ|SU|ST2|TT|PT|QT|NT|CODE|PTV|PTT|PTD|PTA|PTB|PTL|PTN|PTX|PTY|PTZ|UI3|UI4|UI5|UI6|UI7|UI8|UI9|UI10|UI11|UI12|UI13|UI14|UI15|UI16|UI17|UI18|UI19|UI20|UI21|UI22|UI23|UI24|UI25|UI26|UI27|UI28|UI29|UI30|UI31|UI32|UI33|UI34|UI35|UI36|UI37|UI38|UI39|UI40|UI41|UI42|UI43|UI44|UI45|UI46|UI47|UI48|UI49|UI50


def find_split_rrf_files(base_path: Path) -> list[Path]:
    """Find all parts of a split RRF file (e.g., MRHIER.RRF.aa, MRHIER.RRF.ab, etc.).

    Also checks in 2024AA-full/2024AA/META/ directory for files that might be there.

    Returns list of file paths in order (.aa, .ab, .ac, etc.)
    """
    if base_path.exists():
        return [base_path]

    # Check for split files in same directory
    parent = base_path.parent
    base_name = base_path.name
    split_files = []

    # Common split file extensions
    extensions = ["aa", "ab", "ac", "ad", "ae", "af", "ag", "ah", "ai", "aj", "ak", "al"]

    for ext in extensions:
        split_path = parent / f"{base_name}.{ext}"
        if split_path.exists() and not split_path.name.endswith(".gz"):
            split_files.append(split_path)
        elif not split_path.exists():
            # Stop at first missing extension (files are sequential)
            break

    # If not found, check in 2024AA-full/2024AA/META/ directory
    if not split_files:
        meta_dir = parent / "2024AA-full" / "2024AA" / "META"
        if meta_dir.exists():
            # Check for single file
            meta_path = meta_dir / base_name
            if meta_path.exists():
                return [meta_path]

            # Check for split files
            for ext in extensions:
                split_path = meta_dir / f"{base_name}.{ext}"
                if split_path.exists() and not split_path.name.endswith(".gz"):
                    split_files.append(split_path)
                elif not split_path.exists() and ext == "aa":
                    # If .aa doesn't exist, no split files
                    break

    return sorted(split_files) if split_files else []


def parse_rrf_file(file_path: Path, field_separator: str = "|") -> list[list[str]]:
    """Parse UMLS RRF file format (handles both single files and split files).

    IMPORTANT: Preserves field positions by NOT filtering empty fields.
    RRF files use fixed-position fields, so empty fields must be preserved
    to maintain correct field indices (e.g., field [14] must always be STR).

    If file doesn't exist, will look for split files (e.g., file.aa, file.ab, etc.)
    """
    # Check for split files if main file doesn't exist
    files_to_parse = find_split_rrf_files(file_path)

    if not files_to_parse:
        logger.warning(f"File not found: {file_path}")
        return []

    if len(files_to_parse) > 1:
        logger.info(f"Found {len(files_to_parse)} split files for {file_path.name}")

    records = []
    total_size_mb = 0

    for file_to_parse in files_to_parse:
        # Estimate file size for progress bar
        try:
            file_size_mb = file_to_parse.stat().st_size / (1024 * 1024)
            total_size_mb += file_size_mb
            if len(files_to_parse) == 1:
                logger.info(f"  File size: {file_size_mb:.1f} MB")
        except:
            pass

        with open(file_to_parse, encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f, delimiter=field_separator)

            # Use tqdm for progress tracking
            desc = (
                f"  Parsing {file_to_parse.name}"
                if len(files_to_parse) > 1
                else f"  Parsing {file_path.name}"
            )
            reader = tqdm(
                reader,
                desc=desc,
                unit=" lines",
                unit_scale=True,
                miniters=10000,  # Update every 10k lines for better performance
                smoothing=0.1,  # Smooth rate calculation
            )

            for row in reader:
                # Strip whitespace but preserve empty fields to maintain field positions
                # This is critical because RRF files use fixed-position fields
                row = [field.strip() for field in row]
                # Only skip completely empty rows
                if any(row):
                    records.append(row)

    if len(files_to_parse) > 1:
        logger.info(
            f"  ✓ Parsed {len(records):,} records from {len(files_to_parse)} files ({total_size_mb:.1f} MB total)"
        )
    else:
        logger.info(f"  ✓ Parsed {len(records):,} records from {file_path.name}")
    return records


def load_mrconso(records: list[list[str]]) -> dict[str, dict]:
    """Load MRCONSO (concepts) - group by CUI."""
    # Placeholder values that should not be used as preferred names
    PLACEHOLDER_VALUES = {"N", "0", "3", "256", "9", ""}

    # Source priority for preferred name selection (higher priority first)
    # MSH (MeSH) is generally most authoritative for medical terms
    # NOTE: SOURCE_PRIORITY is ONLY used for choosing the preferred term/name.
    # It does NOT filter definitions - all definitions from all sources are included.
    SOURCE_PRIORITY = ["MSH", "SNOMEDCT_US", "ICD10CM", "RXNORM", "ICD10", "CPT"]

    concepts = defaultdict(
        lambda: {
            "cui": "",
            "preferred_name": "",
            "preferred_source": "",
            "synonyms": [],
            "codes": [],
            "sources": set(),
            "term_types": set(),
            "languages": set(),
        }
    )

    logger.info("Processing MRCONSO records...")
    for record in tqdm(records, desc="MRCONSO"):
        if len(record) < 15:
            continue

        cui = record[0]
        language = record[1]
        is_pref = record[6] == "Y"
        source = record[11] if len(record) > 11 else ""
        term_type = record[12] if len(record) > 12 else ""
        code = record[13] if len(record) > 13 else ""
        string = record[14] if len(record) > 14 else ""

        concepts[cui]["cui"] = cui
        concepts[cui]["languages"].add(language)
        concepts[cui]["sources"].add(source)
        concepts[cui]["term_types"].add(term_type)

        if code:
            concepts[cui]["codes"].append({"code": code, "source": source, "term_type": term_type})

        # Improved preferred name selection
        if is_pref and string:
            current_pref = concepts[cui]["preferred_name"]
            current_source = concepts[cui].get("preferred_source", "")

            # Skip placeholder values
            if string in PLACEHOLDER_VALUES:
                # Still add to synonyms if not already there
                if string not in concepts[cui]["synonyms"]:
                    concepts[cui]["synonyms"].append(string)
            # If no preferred name yet, use this one (if not placeholder)
            elif not current_pref or current_pref in PLACEHOLDER_VALUES:
                concepts[cui]["preferred_name"] = string
                concepts[cui]["preferred_source"] = source
            # If we have a preferred name, check if this source is higher priority
            elif source in SOURCE_PRIORITY:
                current_priority = (
                    SOURCE_PRIORITY.index(source) if current_source in SOURCE_PRIORITY else 999
                )
                new_priority = SOURCE_PRIORITY.index(source)
                if new_priority < current_priority:
                    concepts[cui]["preferred_name"] = string
                    concepts[cui]["preferred_source"] = source
                    # Move old preferred name to synonyms if not already there
                    if current_pref not in concepts[cui]["synonyms"]:
                        concepts[cui]["synonyms"].append(current_pref)
        elif string and string not in concepts[cui]["synonyms"]:
            concepts[cui]["synonyms"].append(string)

    # Convert sets to lists for JSON serialization
    for cui, data in concepts.items():
        data["sources"] = sorted(list(data["sources"]))
        data["term_types"] = sorted(list(data["term_types"]))
        data["languages"] = sorted(list(data["languages"]))

    logger.info(f"  Processed {len(concepts):,} unique concepts")
    return dict(concepts)


def load_srdef(data_dir: Path) -> dict[str, str]:
    """Load SRDEF (Semantic Network definitions) to map TUI -> Semantic Type Name.

    SRDEF format: RT|UI|STY_RL|STN|DEF|...
    - RT: Record Type (STY for Semantic Type)
    - UI: Unique Identifier (TUI like T116)
    - STY_RL: Semantic Type or Relation Label (the human-readable name)

    Returns: Dict mapping TUI -> Semantic Type Name
    """
    tui_to_name = {}

    # Try to find SRDEF in common locations
    # Note: SRDEF can be either SRDEF.RRF or just SRDEF (extracted from 2024aa-otherks.nlm)
    srdef_paths = [
        data_dir / "SRDEF",  # Extracted from 2024aa-otherks.nlm
        data_dir / "SRDEF.RRF",
        data_dir / "NET" / "SRDEF",
        data_dir / "NET" / "SRDEF.RRF",
        data_dir / "2024AA-full" / "2024AA" / "NET" / "SRDEF",
        data_dir / "2024AA-full" / "2024AA" / "NET" / "SRDEF.RRF",
    ]

    srdef_path = None
    for path in srdef_paths:
        if path.exists():
            srdef_path = path
            break

    if not srdef_path:
        logger.warning("=" * 60)
        logger.warning("⚠️  SRDEF FILE NOT FOUND")
        logger.warning("=" * 60)
        logger.warning("SRDEF.RRF file was not found in any of the expected locations:")
        for path in srdef_paths:
            logger.warning(f"  - {path}")
        logger.warning("")
        logger.warning("Semantic types will use TUI codes (e.g., 'T047') instead of")
        logger.warning("human-readable names (e.g., 'Disease or Syndrome').")
        logger.warning("=" * 60)
        return {}

    logger.info(f"Loading SRDEF from {srdef_path}...")
    records = parse_rrf_file(srdef_path)

    for record in tqdm(records, desc="SRDEF"):
        if len(record) < 3:
            continue

        rt = record[0].strip()  # Record Type
        ui = record[1].strip()  # TUI
        sty_rl = record[2].strip() if len(record) > 2 else ""  # Semantic Type Name

        # Only process Semantic Type records (RT == "STY")
        if rt == "STY" and ui and sty_rl:
            tui_to_name[ui] = sty_rl

    logger.info(f"  Loaded {len(tui_to_name):,} TUI -> Name mappings from SRDEF")
    return tui_to_name


def load_mrsty(records: list[list[str]], tui_to_name: dict[str, str]) -> dict[str, list[dict]]:
    """Load MRSTY (semantic types) - group by CUI.

    MRSTY format: CUI|TUI|STN|STY|ATUI|CVF
    Uses TUI -> Name mapping from SRDEF for human-readable names

    Args:
        records: MRSTY records
        tui_to_name: Mapping of TUI -> Semantic Type Name (from SRDEF)
    """
    semantic_types = defaultdict(list)

    logger.info("Processing MRSTY records...")
    for record in tqdm(records, desc="MRSTY"):
        if len(record) < 2:
            continue

        cui = record[0].strip()
        tui = record[1].strip() if len(record) > 1 else ""

        if cui and tui:
            # Try to get semantic type name from:
            # 1. STY field in MRSTY (if populated)
            # 2. TUI mapping from SRDEF
            # 3. Fallback to TUI code
            semantic_type_name = None

            # Check STY field first
            if len(record) > 3 and record[3].strip():
                semantic_type_name = record[3].strip()
            # Use TUI mapping
            elif tui in tui_to_name:
                semantic_type_name = tui_to_name[tui]
            # Fallback to TUI code
            else:
                semantic_type_name = tui

            semantic_types[cui].append({"tui": tui, "semantic_type": semantic_type_name})

    logger.info(f"  Processed semantic types for {len(semantic_types):,} concepts")
    return dict(semantic_types)


def load_mrdef(
    records: list[list[str]], allowed_sources: list[str] = None
) -> dict[str, list[dict]]:
    """Load MRDEF (definitions) - group by CUI.

    Args:
        records: List of MRDEF records (parsed RRF format)
        allowed_sources: Optional list of source abbreviations to filter by.
                       If None (default), all sources are included (no filtering).
                       This parameter allows explicit control over source filtering.

    Returns:
        Dict mapping CUI -> List of definition dicts with 'definition' and 'source' keys
    """
    definitions = defaultdict(list)

    logger.info("Processing MRDEF records...")
    for record in tqdm(records, desc="MRDEF"):
        if len(record) < 6:
            continue

        cui = record[0]
        definition = record[5] if len(record) > 5 else ""
        source = record[4] if len(record) > 4 else ""

        # Only process non-empty definitions
        if not definition or not definition.strip():
            continue

        # Apply source filter if specified
        if allowed_sources is not None and source not in allowed_sources:
            continue

        definitions[cui].append({"definition": definition.strip(), "source": source})

    logger.info(f"  Processed definitions for {len(definitions):,} concepts")
    if allowed_sources is not None:
        logger.info(f"  Filtered to sources: {allowed_sources}")
    else:
        logger.info("  No source filtering applied (all sources included)")
    return dict(definitions)


def load_mrrel(records: list[list[str]]) -> list[dict]:
    """Load MRREL (relations)."""
    relations = []

    logger.info("Processing MRREL records...")
    for record in tqdm(records, desc="MRREL"):
        if len(record) < 9:
            continue

        cui1 = record[0]
        cui2 = record[4]
        relation = record[3]
        relation_label = record[7] if len(record) > 7 else ""
        source = record[10] if len(record) > 10 else ""

        if cui1 and cui2 and relation:
            relations.append(
                {
                    "cui1": cui1,
                    "cui2": cui2,
                    "relation": relation,
                    "relation_label": relation_label or relation,
                    "source": source,
                }
            )

    logger.info(f"  Processed {len(relations):,} relations")
    return relations


def stream_mrrel_to_csv(file_path: Path, output_file: Path) -> int:
    """Stream MRREL directly to CSV without loading into memory.

    Returns: Number of relations written
    """
    import csv

    files_to_parse = find_split_rrf_files(file_path)
    if not files_to_parse:
        logger.warning(f"MRREL file not found: {file_path}")
        return 0

    logger.info("Streaming MRREL to CSV (memory-efficient mode)...")

    relation_count = 0
    with open(output_file, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(
            out_f, fieldnames=["cui1", "cui2", "relation", "relation_label", "source"]
        )
        writer.writeheader()

        for file_to_parse in files_to_parse:
            with open(file_to_parse, encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f, delimiter="|")

                desc = (
                    f"  Streaming {file_to_parse.name}"
                    if len(files_to_parse) > 1
                    else f"  Streaming {file_path.name}"
                )
                reader = tqdm(
                    reader, desc=desc, unit=" lines", unit_scale=True, miniters=10000, smoothing=0.1
                )

                for row in reader:
                    row = [field.strip() for field in row]

                    if len(row) < 9 or not any(row):
                        continue

                    cui1 = row[0]
                    cui2 = row[4]
                    relation = row[3]
                    relation_label = row[7] if len(row) > 7 else ""
                    source = row[10] if len(row) > 10 else ""

                    if cui1 and cui2 and relation:
                        writer.writerow(
                            {
                                "cui1": cui1,
                                "cui2": cui2,
                                "relation": relation,
                                "relation_label": relation_label or relation,
                                "source": source,
                            }
                        )
                        relation_count += 1

    logger.info(f"  ✓ Streamed {relation_count:,} relations to {output_file.name}")
    return relation_count


def load_mrhier_from_file(file_path: Path) -> dict[str, list[dict]]:
    """Load MRHIER (hierarchical relationships) - STREAMING MODE to avoid memory issues.

    MRHIER format: CUI|AUI|CXN|HTYPE|PTR|HCD|CVF|SAB|REL|RELA|PAUI|SAUI|SCUI|SDUI|SAB|TTY|SUPPRESS|CVF
    - CUI: Concept Unique Identifier
    - PTR: Path to root (hierarchical path)
    - HCD: Hierarchical code
    - SAB: Source abbreviation
    - REL/RELA: Relationship type

    Returns: Dict mapping CUI -> List of hierarchical relationship dicts
    """
    hierarchies = defaultdict(list)

    # Check for split files
    files_to_parse = find_split_rrf_files(file_path)
    if not files_to_parse:
        logger.warning(f"MRHIER file not found: {file_path}")
        return {}

    logger.info("Processing MRHIER records (streaming mode)...")

    for file_to_parse in files_to_parse:
        with open(file_to_parse, encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f, delimiter="|")

            desc = (
                f"  Processing {file_to_parse.name}"
                if len(files_to_parse) > 1
                else f"  Processing {file_path.name}"
            )
            reader = tqdm(
                reader, desc=desc, unit=" lines", unit_scale=True, miniters=10000, smoothing=0.1
            )

            for row in reader:
                # Strip whitespace but preserve empty fields
                row = [field.strip() for field in row]

                if len(row) < 6 or not any(row):
                    continue

                cui = row[0]
                ptr = row[4] if len(row) > 4 else ""  # Path to root
                hcd = row[5] if len(row) > 5 else ""  # Hierarchical code
                source = row[7] if len(row) > 7 else ""
                rel = row[8] if len(row) > 8 else ""
                rela = row[9] if len(row) > 9 else ""

                if cui:
                    hierarchies[cui].append(
                        {
                            "path_to_root": ptr,
                            "hierarchical_code": hcd,
                            "source": source,
                            "relation": rel,
                            "relation_label": rela,
                        }
                    )

    logger.info(f"  Processed hierarchies for {len(hierarchies):,} concepts")
    return dict(hierarchies)


def load_mrsat_from_file(file_path: Path) -> dict[str, list[dict]]:
    """Load MRSAT (additional attributes) - STREAMING MODE to avoid memory issues.

    MRSAT format: CUI|LUI|SUI|AUI|SATUI|ATN|SAB|ATV|SUPPRESS|CVF
    - CUI: Concept Unique Identifier
    - ATN: Attribute name
    - ATV: Attribute value
    - SAB: Source abbreviation

    Returns: Dict mapping CUI -> List of attribute dicts
    """
    attributes = defaultdict(list)

    # Check for split files
    files_to_parse = find_split_rrf_files(file_path)
    if not files_to_parse:
        logger.warning(f"MRSAT file not found: {file_path}")
        return {}

    logger.info("Processing MRSAT records (streaming mode)...")

    # Diagnostic counters
    total_rows = 0
    skipped_too_short = 0
    skipped_empty_row = 0
    skipped_missing_cui = 0
    skipped_missing_atn = 0
    skipped_missing_atv = 0
    processed_count = 0

    for file_to_parse in files_to_parse:
        with open(file_to_parse, encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f, delimiter="|")

            desc = (
                f"  Processing {file_to_parse.name}"
                if len(files_to_parse) > 1
                else f"  Processing {file_path.name}"
            )
            reader = tqdm(
                reader, desc=desc, unit=" lines", unit_scale=True, miniters=10000, smoothing=0.1
            )

            for row in reader:
                total_rows += 1
                # Strip whitespace but preserve empty fields
                row = [field.strip() for field in row]

                if len(row) < 8:
                    skipped_too_short += 1
                    continue
                if not any(row):
                    skipped_empty_row += 1
                    continue

                cui = row[0]
                atn = row[8] if len(row) > 8 else ""  # Attribute name
                atv = row[10] if len(row) > 10 else ""  # Attribute value
                source = row[9] if len(row) > 9 else ""

                # Track why records are being skipped
                if not cui:
                    skipped_missing_cui += 1
                    continue
                if not atn:
                    skipped_missing_atn += 1
                    continue
                if not atv:
                    skipped_missing_atv += 1
                    continue

                # All required fields present
                attributes[cui].append(
                    {"attribute_name": atn, "attribute_value": atv, "source": source}
                )
                processed_count += 1

    # Log diagnostic information
    logger.info("  MRSAT Processing Summary:")
    logger.info(f"    Total rows processed: {total_rows:,}")
    logger.info(f"    Rows with < 8 fields: {skipped_too_short:,}")
    logger.info(f"    Empty rows: {skipped_empty_row:,}")
    logger.info(f"    Rows missing CUI: {skipped_missing_cui:,}")
    logger.info(f"    Rows missing ATN: {skipped_missing_atn:,}")
    logger.info(f"    Rows missing ATV: {skipped_missing_atv:,}")
    logger.info(f"    Successfully processed: {processed_count:,}")
    logger.info(f"  Processed attributes for {len(attributes):,} concepts")
    return dict(attributes)


def combine_concepts(
    concepts: dict[str, dict],
    semantic_types: dict[str, list[dict]],
    definitions: dict[str, list[str]],
    hierarchies: dict[str, list[dict]] = None,
    attributes: dict[str, list[dict]] = None,
) -> dict[str, dict]:
    """Combine all concept data by CUI (memory-efficient dictionary approach)."""
    logger.info("Combining concept data...")

    combined = {}
    no_semantic_type = 0
    no_preferred_term = 0
    no_definition = 0

    for cui, concept_data in tqdm(concepts.items(), desc="Combining"):
        # Get preferred name, with fallback to first meaningful synonym
        preferred_name = concept_data["preferred_name"]
        if not preferred_name or preferred_name in {"N", "0", "3", "256", "9", ""}:
            # Try to find a meaningful synonym (not a placeholder)
            PLACEHOLDER_VALUES = {"N", "0", "3", "256", "9", ""}
            meaningful_synonyms = [
                s
                for s in concept_data.get("synonyms", [])
                if s and s not in PLACEHOLDER_VALUES and len(s) > 2
            ]
            preferred_name = (
                meaningful_synonyms[0]
                if meaningful_synonyms
                else (concept_data["synonyms"][0] if concept_data["synonyms"] else "")
            )
        preferred_term = preferred_name

        # Get semantic types
        st_list = semantic_types.get(cui, [])
        semantic_types_list = [st["semantic_type"] for st in st_list]

        # Get definitions - filter out empty definitions
        def_list = definitions.get(cui, [])
        # Only count non-empty definitions (filter out empty strings)
        def_list = [
            d for d in def_list if d and isinstance(d, dict) and d.get("definition", "").strip()
        ]

        # Get hierarchies and attributes (optional)
        hier_list = hierarchies.get(cui, []) if hierarchies else []
        attr_list = attributes.get(cui, []) if attributes else []

        # Validation counters - only count non-empty definitions
        if not semantic_types_list:
            no_semantic_type += 1
        if not preferred_term:
            no_preferred_term += 1
        if not def_list:  # Only count if list is empty or all definitions are empty
            no_definition += 1

        combined[cui] = {
            "cui": cui,
            "preferred_name": preferred_name,
            "preferred_term": preferred_term,
            "synonyms": concept_data["synonyms"]
            if isinstance(concept_data["synonyms"], list)
            else [],
            "semantic_types": semantic_types_list if isinstance(semantic_types_list, list) else [],
            "semantic_type_details": st_list if isinstance(st_list, list) else [],
            "definitions": def_list if isinstance(def_list, list) else [],
            "codes": concept_data["codes"],
            "sources": concept_data["sources"],
            "term_types": concept_data["term_types"],
            "languages": concept_data["languages"],
            "hierarchies": hier_list,  # Hierarchical relationships from MRHIER
            "attributes": attr_list,  # Additional attributes from MRSAT
        }

    # ---------------- VALIDATION ----------------
    logger.info("\n" + "=" * 60)
    logger.info("VALIDATION RESULTS")
    logger.info("=" * 60)
    logger.info(f"Total concepts: {len(combined):,}")
    logger.info(f"Concepts with no semantic type: {no_semantic_type:,}")
    logger.info(f"Concepts with no preferred term: {no_preferred_term:,}")
    logger.info(f"Concepts with no definitions (non-empty): {no_definition:,}")
    logger.info("=" * 60 + "\n")

    logger.info(f"  Combined {len(combined):,} concepts")
    return combined


def generate_embedding_text(concept: dict) -> str:
    """Generate embeddable text for RAG from concept data.

    Note: Codes (CUI, ICD, etc.) are excluded from embeddings and stored as metadata
    to focus embeddings on semantic content rather than identifiers.
    """
    parts = []

    # Preferred name
    if concept["preferred_name"]:
        parts.append(f"Concept: {concept['preferred_name']}")

    # Definitions
    if concept["definitions"]:
        def_text = " | ".join([d["definition"] for d in concept["definitions"]])
        parts.append(f"Definition: {def_text}")

    # Semantic types
    if concept["semantic_types"]:
        st_text = ", ".join(concept["semantic_types"])
        parts.append(f"Semantic types: {st_text}")

    # Synonyms
    if concept["synonyms"]:
        syn_text = ", ".join(concept["synonyms"][:10])  # Limit to first 10
        parts.append(f"Also known as: {syn_text}")

    # Codes are NOT included in embedding text - stored as metadata instead

    return " | ".join(parts)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Combine UMLS RRF files into processed formats")
    parser.add_argument(
        "--skip-hier",
        action="store_true",
        help="Skip MRHIER (hierarchical relationships) to save memory",
    )
    parser.add_argument(
        "--skip-sat", action="store_true", help="Skip MRSAT (additional attributes) to save memory"
    )
    parser.add_argument(
        "--skip-optional",
        action="store_true",
        help="Skip both MRHIER and MRSAT (equivalent to --skip-hier --skip-sat)",
    )
    args = parser.parse_args()

    # Handle --skip-optional flag
    if args.skip_optional:
        args.skip_hier = True
        args.skip_sat = True

    data_dir = Path("data/umls")
    output_dir = Path("data/umls/processed")
    output_dir.mkdir(exist_ok=True)

    # File paths (required)
    mrconso_path = data_dir / "MRCONSO.RRF"
    mrrel_path = data_dir / "MRREL.RRF"
    mrsty_path = data_dir / "MRSTY.RRF"
    mrdef_path = data_dir / "MRDEF.RRF"

    # File paths (optional - hierarchical and attribute data)
    mrhier_path = data_dir / "MRHIER.RRF"
    mrsat_path = data_dir / "MRSAT.RRF"

    # Check required files exist
    for path in [mrconso_path, mrrel_path, mrsty_path, mrdef_path]:
        if not path.exists() and not find_split_rrf_files(path):
            raise FileNotFoundError(f"Required file not found: {path}")

    logger.info("=" * 60)
    logger.info("UMLS ETL: Combining RRF Files")
    logger.info("=" * 60)

    # Load TUI -> Semantic Type Name mapping (from SRDEF or hardcoded)
    logger.info("\n" + "=" * 60)
    logger.info("Loading TUI -> Semantic Type Name Mapping")
    logger.info("=" * 60)
    tui_to_name = load_srdef(data_dir)

    # Parse RRF files
    logger.info("\n" + "=" * 60)
    logger.info("Parsing RRF Files")
    logger.info("=" * 60)
    mrconso_records = parse_rrf_file(mrconso_path)
    parse_rrf_file(mrrel_path)
    mrsty_records = parse_rrf_file(mrsty_path)
    mrdef_records = parse_rrf_file(mrdef_path)

    # Load and transform
    logger.info("\n" + "=" * 60)
    logger.info("Loading and Transforming Data")
    logger.info("=" * 60)
    concepts = load_mrconso(mrconso_records)
    semantic_types = load_mrsty(mrsty_records, tui_to_name)
    definitions = load_mrdef(mrdef_records)

    # Stream relations directly to CSV to save memory (don't keep in memory)
    relations_file = output_dir / "relations.csv"
    logger.info("\nStreaming relations to CSV (memory-efficient)...")
    relation_count = stream_mrrel_to_csv(mrrel_path, relations_file)

    # Load optional data (using streaming mode to avoid memory issues)
    hierarchies = {}
    attributes = {}

    if args.skip_hier:
        logger.info("Skipping MRHIER (hierarchical relationships) - --skip-hier flag set")
    elif find_split_rrf_files(mrhier_path) or mrhier_path.exists():
        logger.info("\nLoading MRHIER (hierarchical relationships) - streaming mode...")
        hierarchies = load_mrhier_from_file(mrhier_path)
    else:
        logger.info("MRHIER not found - skipping hierarchical relationships")

    if args.skip_sat:
        logger.info("Skipping MRSAT (additional attributes) - --skip-sat flag set")
    elif find_split_rrf_files(mrsat_path) or mrsat_path.exists():
        logger.info("\nLoading MRSAT (additional attributes) - streaming mode...")
        attributes = load_mrsat_from_file(mrsat_path)
    else:
        logger.info("MRSAT not found - skipping additional attributes")

    # Combine concepts (using pandas merge logic)
    combined_concepts = combine_concepts(
        concepts, semantic_types, definitions, hierarchies, attributes
    )

    # ---------------- EXPORT CSV ----------------
    # Export as CSV for easy inspection (matches user's expected format)
    logger.info("\n" + "=" * 60)
    logger.info("Exporting umls_concepts.csv...")
    concepts_csv_file = output_dir / "umls_concepts.csv"

    # Convert to DataFrame for CSV export (in chunks to avoid memory issues)
    logger.info("Converting to DataFrame (this may take a moment)...")
    concepts_list = []
    for cui, concept in tqdm(combined_concepts.items(), desc="Preparing CSV data"):
        concepts_list.append(
            {
                "CUI": cui,
                "preferred_name": concept["preferred_name"],
                "preferred_term": concept.get("preferred_term", concept["preferred_name"]),
                "synonyms": concept["synonyms"],
                "semantic_types": concept["semantic_types"],
                "definitions": concept["definitions"],
            }
        )

    logger.info("Creating DataFrame and writing CSV...")
    concepts_df = pd.DataFrame(concepts_list)
    concepts_df.to_csv(concepts_csv_file, index=False)
    logger.info(f"✓ Created {concepts_csv_file} with {len(concepts_df):,} concepts")

    # Output 1: concepts.jsonl (one object per CUI)
    logger.info("\n" + "=" * 60)
    logger.info("Generating concepts.jsonl...")
    concepts_file = output_dir / "concepts.jsonl"
    with open(concepts_file, "w", encoding="utf-8") as f:
        for cui, concept in tqdm(combined_concepts.items(), desc="Writing concepts"):
            json.dump(concept, f, ensure_ascii=False)
            f.write("\n")
    logger.info(f"✓ Created {concepts_file} with {len(combined_concepts):,} concepts")

    # Output 2: relations.csv (graph edges)
    # Already created during streaming, just verify
    logger.info("\n" + "=" * 60)
    logger.info("Relations CSV status...")
    relations_file = output_dir / "relations.csv"

    if relation_count > 0:
        logger.info(
            f"✓ Relations already written to {relations_file.name} ({relation_count:,} relations)"
        )
    else:
        logger.warning("No relations found")

    # Output 3: embeddable_text.jsonl (for RAG)
    logger.info("\n" + "=" * 60)
    logger.info("Generating embeddable_text.jsonl...")
    embeddable_file = output_dir / "embeddable_text.jsonl"
    with open(embeddable_file, "w", encoding="utf-8") as f:
        for cui, concept in tqdm(combined_concepts.items(), desc="Writing embeddable text"):
            text = generate_embedding_text(concept)
            doc = {
                "cui": cui,
                "text": text,
                "preferred_name": concept["preferred_name"],
                "semantic_types": concept["semantic_types"],
                "synonyms": concept["synonyms"][:10],  # Limit to first 10
                "definitions": concept["definitions"],  # Store definitions as metadata
                "codes": concept["codes"],  # Store codes as metadata, not in embedding
            }
            json.dump(doc, f, ensure_ascii=False)
            f.write("\n")
    logger.info(f"✓ Created {embeddable_file} with {len(combined_concepts):,} documents")

    # Output 4: neo4j_relations.csv (Neo4j-ready format)
    # Stream from relations.csv to avoid loading into memory
    logger.info("\n" + "=" * 60)
    logger.info("Generating neo4j_relations.csv...")
    neo4j_relations_file = output_dir / "neo4j_relations.csv"

    if relation_count > 0 and relations_file.exists():
        with (
            open(relations_file, encoding="utf-8") as in_f,
            open(neo4j_relations_file, "w", newline="", encoding="utf-8") as out_f,
        ):
            reader = csv.DictReader(in_f)
            writer = csv.DictWriter(
                out_f,
                fieldnames=[
                    ":START_ID(Concept)",
                    ":END_ID(Concept)",
                    ":TYPE",
                    "relation_label",
                    "source",
                ],
            )
            writer.writeheader()

            for relation in tqdm(reader, desc="Converting to Neo4j format", total=relation_count):
                writer.writerow(
                    {
                        ":START_ID(Concept)": relation["cui1"],
                        ":END_ID(Concept)": relation["cui2"],
                        ":TYPE": relation["relation"],
                        "relation_label": relation["relation_label"],
                        "source": relation["source"],
                    }
                )
        logger.info(f"✓ Created {neo4j_relations_file}")
    else:
        logger.warning("No relations to convert for Neo4j")

    # Output 5: neo4j_concepts.csv (Neo4j nodes)
    logger.info("\n" + "=" * 60)
    logger.info("Generating neo4j_concepts.csv...")
    neo4j_concepts_file = output_dir / "neo4j_concepts.csv"
    with open(neo4j_concepts_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                ":ID(Concept)",
                "preferred_name",
                "semantic_types",
                "definition",
                "synonym_count",
            ],
        )
        writer.writeheader()
        for cui, concept in tqdm(combined_concepts.items(), desc="Writing Neo4j concepts"):
            # Get first definition if available
            definition = concept["definitions"][0]["definition"] if concept["definitions"] else ""
            # Join semantic types
            st_str = ";".join(concept["semantic_types"][:5])  # Limit to 5 for CSV
            writer.writerow(
                {
                    ":ID(Concept)": cui,
                    "preferred_name": concept["preferred_name"][:500],  # Limit length
                    "semantic_types": st_str,
                    "definition": definition[:1000] if definition else "",  # Limit length
                    "synonym_count": len(concept["synonyms"]),
                }
            )
    logger.info(f"✓ Created {neo4j_concepts_file} with {len(combined_concepts):,} Neo4j concepts")

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("ETL Complete!")
    logger.info("=" * 60)
    logger.info(f"✅ concepts.jsonl → {len(combined_concepts):,} concepts")
    logger.info(f"✅ relations.csv → {relation_count:,} graph edges")
    logger.info(f"✅ embeddable_text.jsonl → {len(combined_concepts):,} documents for RAG")
    logger.info(f"✅ neo4j_relations.csv → {relation_count:,} Neo4j-ready relations")
    logger.info(f"✅ neo4j_concepts.csv → {len(combined_concepts):,} Neo4j-ready concepts")
    logger.info(f"\nOutput directory: {output_dir.absolute()}")

    # File sizes
    logger.info("\nGenerated files:")
    for file in [
        concepts_file,
        relations_file,
        embeddable_file,
        neo4j_relations_file,
        neo4j_concepts_file,
    ]:
        if file.exists():
            size_mb = file.stat().st_size / (1024 * 1024)
            logger.info(f"  {file.name}: {size_mb:.2f} MB")


def cleanup():
    """Cleanup function to properly close resources."""
    try:
        # Clean up any remaining tqdm instances
        tqdm._instances.clear()
    except:
        pass


if __name__ == "__main__":
    # Register cleanup function
    atexit.register(cleanup)

    try:
        main()
    finally:
        # Ensure cleanup runs
        cleanup()
