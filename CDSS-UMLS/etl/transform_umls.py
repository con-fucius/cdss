"""Transform UMLS RRF files to structured format."""

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_rrf_file(file_path: str, field_separator: str = "|") -> list[dict]:
    """Parse UMLS RRF file format."""
    records = []

    with open(file_path, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=field_separator)
        for row in reader:
            # RRF files have trailing separators, remove empty fields
            row = [field for field in row if field]
            if row:
                records.append(row)

    return records


def transform_mrconso(records: list[list[str]]) -> list[dict]:
    """Transform MRCONSO (concepts) records."""
    # MRCONSO field positions (simplified)
    # CUI|LAT|TS|LUI|STT|SUI|ISPREF|AUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF
    concepts = []

    for record in records:
        if len(record) >= 15:
            concept = {
                "cui": record[0],
                "language": record[1],
                "term_status": record[2],
                "preferred": record[6] == "Y",
                "source": record[11],
                "term_type": record[12],
                "code": record[13],
                "string": record[14],
            }
            concepts.append(concept)

    return concepts


def transform_mrsty(records: list[list[str]]) -> list[dict]:
    """Transform MRSTY (semantic types) records."""
    # CUI|TUI|STN|STY|ATUI|CVF
    semantic_types = []

    for record in records:
        if len(record) >= 4:
            st = {"cui": record[0], "tui": record[1], "semantic_type": record[3]}
            semantic_types.append(st)

    return semantic_types


def transform_mrrel(records: list[list[str]]) -> list[dict]:
    """Transform MRREL (relations) records."""
    # CUI1|AUI1|STYPE1|REL|CUI2|AUI2|STYPE2|RELA|RUI|SRUI|SAB|SL|RG|DIR|SUPPRESS|CVF
    relations = []

    for record in records:
        if len(record) >= 9:
            relation = {
                "cui1": record[0],
                "cui2": record[4],
                "relation": record[3],
                "relation_label": record[7],
                "source": record[10],
            }
            relations.append(relation)

    return relations


if __name__ == "__main__":
    # Example usage
    data_dir = Path("data/umls")

    if (data_dir / "MRCONSO.RRF").exists():
        records = parse_rrf_file(str(data_dir / "MRCONSO.RRF"))
        concepts = transform_mrconso(records)
        logger.info(f"Transformed {len(concepts)} concepts")
    else:
        logger.warning("UMLS data files not found. Please download first.")
