import json
import logging
from pathlib import Path
from typing import Any

try:
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter
except ImportError:
    # Handle environment without docling if necessary, though user wants the code written
    DocumentConverter = None

logger = logging.getLogger(__name__)


class TableExtractor:
    """Runs Docling on PDFs to extract tables as structured JSON.
    Outputs to kb/raw/{disease}/{table_id}.json.
    """

    def __init__(self, raw_out_dir: str = "app/kb/raw"):
        self.raw_out_dir = Path(raw_out_dir)
        if DocumentConverter:
            self.converter = DocumentConverter(allowed_formats=[InputFormat.PDF])
        else:
            self.converter = None
            logger.warning("Docling is not installed. Extractor will not function.")

    def _classify_table(self, headers: list[str], text_content: str) -> str:
        """Heuristically classify table based on headers and content."""
        headers_lower = [str(h).lower() for h in headers if h]
        headers_str = " ".join(headers_lower)

        if any(
            w in headers_str
            for w in [
                "regimen",
                "drug",
                "arv",
                "medication",
                "first-line",
                "second-line",
                "act",
                "artemether",
                "artesunate",
                "lumefantrine",
            ]
        ):
            return "regimen"
        if any(
            w in headers_str
            for w in ["dose", "weight", "dosing", "kg", "mg", "tablet", "body weight"]
        ):
            return "dosing"
        if any(
            w in headers_str
            for w in [
                "criteria",
                "threshold",
                "fpg",
                "hba1c",
                "target",
                "parasitemia",
                "rdt",
                "smear",
            ]
        ):
            return "diagnostic_criteria"
        if any(w in headers_str for w in ["monitoring", "schedule", "follow-up", "month", "day"]):
            return "monitoring"
        return "reference_values"

    def _compute_quality_score(
        self, table_type: str, num_cols: int, num_rows: int, headers: list[str]
    ) -> dict[str, Any]:
        """Compute quality metrics for the extracted table."""
        headers_lower = [str(h).lower() for h in headers if h]
        score = {
            "num_columns": num_cols,
            "num_rows": num_rows,
            "expected_headers_present": False,
            "status": "degraded",
        }

        # Simple quality threshold
        if num_cols < 2 or num_rows < 2:
            return score

        if table_type == "regimen":
            if any("drug" in h or "regimen" in h for h in headers_lower):
                score["expected_headers_present"] = True
        elif table_type == "diagnostic_criteria":
            if any(
                "value" in h or "target" in h or "threshold" in h or "criteria" in h
                for h in headers_lower
            ):
                score["expected_headers_present"] = True
        elif table_type == "dosing":
            if any("dose" in h or "weight" in h for h in headers_lower):
                score["expected_headers_present"] = True
        else:
            # For general tables, just having enough rows/cols is okay
            score["expected_headers_present"] = True

        if score["expected_headers_present"]:
            score["status"] = "ok"

        return score

    def process_pdf(self, pdf_path: str, disease: str):
        """Extract tables from a single PDF and save to raw directory."""
        if not self.converter:
            logger.error("Docling not available.")
            return

        out_dir = self.raw_out_dir / disease
        out_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Converting {pdf_path} for {disease}...")
        result = self.converter.convert(pdf_path)
        doc = result.document

        table_count = 0
        for i, table in enumerate(doc.tables):
            # Extract structured data
            df = table.export_to_dataframe()
            if df.empty:
                continue

            headers = list(df.columns)
            table_type = self.classify_table(headers, df.to_string())
            quality = self._compute_quality_score(table_type, len(df.columns), len(df), headers)

            # Prepare structured JSON
            table_data = {
                "id": f"{disease}_table_{i}",
                "disease": disease,
                "type": table_type,
                "source": {
                    "file": Path(pdf_path).name,
                    # We might not have exact page number from all formats, try to get it if available
                    "page": getattr(table.prov[0], "page_no", 1) if table.prov else None,
                },
                "quality": quality,
                "schema": {"columns": headers},
                "data": df.to_dict(orient="records"),
            }

            out_file = out_dir / f"{table_data['id']}.json"
            with open(out_file, "w") as f:
                json.dump(table_data, f, indent=2)

            table_count += 1

        logger.info(f"Extracted {table_count} tables for {disease}")
