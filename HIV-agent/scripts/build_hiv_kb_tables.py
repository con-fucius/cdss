r"""Build the minimal HIV structured KB table used by live dosing lookup.

This script intentionally reuses the existing extractor layer instead of adding a
new PDF parsing path. It extracts ARV regimen dosing rows from the configured HIV
ARV guideline PDF, writes one LanceDB table named ``hiv_kb_tables``, and leaves
all other diseases untouched.

Run from ``D:\\Projects\\CDSS\\HIV-agent``:
    python -m scripts.build_hiv_kb_tables
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import DISEASE_CONFIG  # noqa: E402
from app.extractors.pdfplumber_ext import PDFPlumberExtractor  # noqa: E402
from app.extractors.pipeline import ExtractionPipeline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger("build_hiv_kb")

TABLE_NAME = "hiv_kb_tables"
TABLE_TYPE = "arv_regimen_dosing"
PDF_NAME = "Kenya-ARV-Guidelines-2022-Final-1.pdf"
DEFAULT_DB_PATH = ROOT / "app" / "lancedb"
DEFAULT_PDF_PATH = ROOT / "app" / PDF_NAME
DOSING_RE = re.compile(r"kg|mg|ml|dose|dosing|regimen|tablet|TDF|DTG|3TC|ABC|ARV|ART", re.I)


def _normalise_cell(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _rows_to_text(rows: list[list[Any]]) -> str:
    lines = []
    for row in rows:
        cells = [_normalise_cell(cell) for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _parse_weight_band(text: str) -> str:
    lowered = text.lower()
    if re.search(r"<\s*30\s*kg|less than 30 kg|under 30 kg", lowered):
        return "< 30 kg"
    if re.search(
        r">=\s*30\s*kg|≥\s*30\s*kg|30 kg or more|30 kg and above|over 30 kg|above 30 kg", lowered
    ):
        return ">= 30 kg"
    match = re.search(r"(?:weight\s*)?(\d+(?:\.\d+)?)\s*kg", lowered)
    if match:
        weight = float(match.group(1))
        return "< 30 kg" if weight < 30 else ">= 30 kg"
    return ""


def _parse_regimen(text: str) -> str:
    match = re.search(r":\s*([A-Za-z0-9/\+\-\s]+)$", text.strip())
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1).strip())


def _extract_drugs(regimen: str) -> list[str]:
    return sorted(set(re.findall(r"\b[A-Z]{2,3}(?:/r)?\b", regimen)))


def _candidate_rows(extracted_content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen = set()

    for item in extracted_content:
        if item.get("type") != "table":
            continue
        text = str(item.get("text", "")).strip()
        if not text or not DOSING_RE.search(text):
            continue

        page = int(item.get("page", 0) or 0)
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or not DOSING_RE.search(line):
                continue
            weight_band = _parse_weight_band(line)
            regimen = _parse_regimen(line)
            if not weight_band or not regimen:
                continue

            key = (weight_band, regimen)
            if key in seen:
                continue
            seen.add(key)

            drugs = _extract_drugs(regimen)
            row_data: dict[str, Any] = {
                "disease": "hiv",
                "table_type": TABLE_TYPE,
                "weight_band": weight_band,
                "population": "children and adolescents"
                if weight_band == "< 30 kg"
                else "adults and adolescents",
                "line": "first-line",
                "regimen": regimen,
                "drugs": drugs,
                "dose_basis": "weight-based",
                "units": ["kg"],
                "source_page": page,
                "notes": "Minimal structured extraction for live ARV dosing lookup.",
            }
            rows.append(
                {
                    "disease": "hiv",
                    "table_type": TABLE_TYPE,
                    "text": f"For {weight_band}: {regimen}.",
                    "source_ref": f"{PDF_NAME}, p.{page}",
                    "raw_json": json.dumps(row_data, ensure_ascii=False),
                }
            )

    return rows


def _build_rows(pdf_path: Path) -> list[dict[str, Any]]:
    import pdfplumber

    pipeline = ExtractionPipeline()
    extracted = pipeline.extract(str(pdf_path), "hiv")
    rows = _candidate_rows(extracted.content)
    if rows:
        return rows

    with pdfplumber.open(str(pdf_path)) as pdf:
        page_numbers = [page for page in [25] if page <= len(pdf.pages)]
        if not page_numbers:
            page_numbers = list(range(1, min(len(pdf.pages), 60) + 1))
        for page_number in page_numbers:
            page = pdf.pages[page_number - 1]
            content = []
            for table in page.extract_tables():
                text = _rows_to_text(table)
                if text:
                    content.append({"type": "table", "text": text, "page": page_number})
            rows.extend(_candidate_rows(content))

    if rows:
        return rows

    extractor = PDFPlumberExtractor()
    extracted = extractor.extract(str(pdf_path))
    rows = _candidate_rows(extracted.content)
    if not rows:
        raise RuntimeError(f"No HIV ARV dosing rows found in {pdf_path}")
    return rows


def _write_table(db_path: Path, rows: list[dict[str, Any]]) -> None:
    import lancedb

    db = lancedb.connect(str(db_path))
    tables = db.list_tables()
    table_names = list(tables.tables) if hasattr(tables, "tables") else list(tables)
    if TABLE_NAME in table_names:
        db.drop_table(TABLE_NAME)

    table = db.create_table(TABLE_NAME, data=rows)
    try:
        table.create_fts_index("text", replace=True)
    except Exception as exc:
        logger.warning("FTS index creation failed for %s: %s", TABLE_NAME, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build minimal HIV structured KB table")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="LanceDB directory")
    parser.add_argument("--pdf-path", default=str(DEFAULT_PDF_PATH), help="HIV ARV guideline PDF")
    parser.add_argument(
        "--dry-run", action="store_true", help="Extract rows without writing LanceDB"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    pdf_path = Path(args.pdf_path).resolve()
    db_path = Path(args.db_path).resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    if not DISEASE_CONFIG.get("hiv"):
        raise SystemExit("HIV disease configuration is missing")

    rows = _build_rows(pdf_path)
    logger.info("Extracted %d HIV structured KB rows", len(rows))
    for row in rows:
        payload = json.loads(row["raw_json"])
        logger.info(
            "  %s -> %s (source: %s)",
            payload["weight_band"],
            payload["regimen"],
            row["source_ref"],
        )

    if args.dry_run:
        logger.info("Dry run complete; LanceDB table was not written")
        return

    db_path.mkdir(parents=True, exist_ok=True)
    _write_table(db_path, rows)
    logger.info("Wrote %s rows to %s", len(rows), TABLE_NAME)


if __name__ == "__main__":
    main()
