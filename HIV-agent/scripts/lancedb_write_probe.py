"""Host-side LanceDB write probe.

Use this when Windows or sandbox permissions are suspected. It exercises the
actual local LanceDB writer by creating a tiny table under a unique directory.
The generated directory is ignored by .gitignore.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import lancedb


def _list_tables(db) -> list[str]:
    tables = db.list_tables()
    if hasattr(tables, "tables"):
        return list(tables.tables)
    return list(tables)


def main() -> None:
    path = Path(f"cdss_lance_probe_host_{uuid.uuid4().hex[:8]}").resolve()
    path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(path))
    table = db.create_table(
        "probe",
        data=[
            {
                "id": "probe-1",
                "text": "host lancedb write probe",
                "vector": [0.1, 0.2, 0.3],
            }
        ],
    )
    print({"path": str(path), "tables": _list_tables(db), "rows": len(table.to_pandas())})


if __name__ == "__main__":
    main()
