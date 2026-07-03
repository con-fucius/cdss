import json
import logging
from pathlib import Path

import lancedb
import pyarrow as pa
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


def _list_lancedb_tables(db) -> list[str]:
    tables = db.list_tables()
    if hasattr(tables, "tables"):
        return list(tables.tables)
    return list(tables)


class TableImporter:
    """Imports validated tables into LanceDB.
    Creates table '{disease}_kb_tables'.
    """

    def __init__(
        self, db_path: str = "app/data/cdss_index", validated_dir: str = "app/kb/validated"
    ):
        self.db = lancedb.connect(db_path)
        self.validated_dir = Path(validated_dir)
        # Using a fast embedding model for table rows
        self.encoder = SentenceTransformer("all-MiniLM-L6-v2")

    def _render_row_to_text(self, row: dict, table_title: str) -> str:
        """Convert a structured row to natural language for RAG fallback."""
        parts = []
        for k, v in row.items():
            if v and str(v).strip() and str(v).lower() != "nan":
                parts.append(f"{k}: {v}")
        return f"Table Context: {table_title}. Row details: " + " | ".join(parts)

    def import_disease_tables(self, disease: str):
        """Import all validated tables for a specific disease into LanceDB."""
        disease_dir = self.validated_dir / disease
        if not disease_dir.exists():
            logger.info(f"No validated tables found for {disease}")
            return

        table_name = f"{disease}_kb_tables"
        data_to_insert = []

        for f_path in disease_dir.glob("*.json"):
            with open(f_path) as f:
                table_data = json.load(f)

            t_id = table_data["id"]
            t_type = table_data["type"]
            t_source = f"{table_data['source'].get('file', 'Unknown')} p.{table_data['source'].get('page', '?')}"

            for i, row in enumerate(table_data.get("data", [])):
                nl_text = self._render_row_to_text(
                    row, f"{disease.upper()} {t_type.replace('_', ' ').title()} Table"
                )
                vector = self.encoder.encode(nl_text).tolist()

                data_to_insert.append(
                    {
                        "id": f"{t_id}_row_{i}",
                        "disease": disease,
                        "table_id": t_id,
                        "table_type": t_type,
                        "source_ref": t_source,
                        "raw_json": json.dumps(row),
                        "text": nl_text,
                        "vector": vector,
                    }
                )

        if not data_to_insert:
            logger.info(f"No valid rows to insert for {disease}")
            return

        # Define schema explicitly to ensure vector dimension matches
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("disease", pa.string()),
                pa.field("table_id", pa.string()),
                pa.field("table_type", pa.string()),
                pa.field("source_ref", pa.string()),
                pa.field("raw_json", pa.string()),
                pa.field("text", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 384)),
            ]
        )

        if table_name in _list_lancedb_tables(self.db):
            logger.info(f"Dropping existing table {table_name}")
            self.db.drop_table(table_name)

        logger.info(f"Creating table {table_name} and inserting {len(data_to_insert)} rows...")
        table = self.db.create_table(table_name, data=data_to_insert, schema=schema)

        # P3.4.4 LanceDB ANN index tuning
        # Adjust partitions and sub_vectors based on size
        num_rows = len(data_to_insert)
        num_partitions = max(2, min(num_rows // 50, 256))
        num_sub_vectors = min(96, 384 // 4)  # Max 96 for 384-dim

        if num_rows > 100:
            logger.info(
                f"Creating IVF-PQ index: partitions={num_partitions}, sub_vectors={num_sub_vectors}"
            )
            table.create_index(
                metric="cosine",
                vector_column_name="vector",
                num_partitions=num_partitions,
                num_sub_vectors=num_sub_vectors,
            )

        logger.info(f"Import complete for {disease}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.config import DISEASE_CONFIG

    importer = TableImporter()
    for disease in DISEASE_CONFIG:
        importer.import_disease_tables(disease)
