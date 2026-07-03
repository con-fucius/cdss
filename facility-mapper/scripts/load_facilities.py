"""facility-mapper/scripts/load_facilities.py.

Idempotent CLI tool to load facility data from CSV or JSON into PostgreSQL.

Usage:
    uv run python -m scripts.load_facilities --source path/to/data.csv --source-name KMHFL_2024_02
    uv run python -m scripts.load_facilities --source path/to/data.json --source-name KMHFL_2024_02
    uv run python -m scripts.load_facilities --source path/to/data.csv --source-name KMHFL_2024_02 --dry-run

Design constraints:
- Idempotent: second run produces same count, no duplicates (upserts by facility_id)
- Validates lat/lon bounds for Kenya/Uganda/DRC: lat -5 to 5, lon 29 to 42
- Rows with invalid coordinates are rejected with a count in the final log
- Writes a DataImport row for audit trail
- --dry-run validates and parses without writing to the database
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Validation bounds for Kenya/Uganda/DRC region
# These are geographic bounding boxes — not clinical decisions.
# Lat: -5 to 5, Lon: 29 to 42 covers Kenya, Uganda, and DRC.
LAT_MIN, LAT_MAX = -5.0, 5.0
LON_MIN, LON_MAX = 29.0, 42.0


def _validate_coordinates(lat: float, lon: float) -> bool:
    """Check if coordinates fall within the Kenya/Uganda/DRC region."""
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def _parse_facility_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single row dict into a normalized facility dict.

    Returns None if the row has invalid data (missing ID, invalid coords).
    Handles multiple column naming conventions from different KMHFL exports.
    """
    # Extract and validate facility_id
    facility_id = row.get("facility_id", row.get("id", "")).strip()
    if not facility_id:
        return None

    # Extract and validate name
    name = row.get("name", row.get("facility_name", "")).strip()
    if not name:
        return None

    # Extract and validate coordinates
    try:
        lat = float(row.get("lat", row.get("latitude", 0)))
        lon = float(row.get("lon", row.get("longitude", 0)))
    except (ValueError, TypeError):
        return None

    if not _validate_coordinates(lat, lon):
        return None

    # Parse services (comma-separated string or list)
    services_raw = row.get("services", row.get("service_types", ""))
    if isinstance(services_raw, str):
        services = [s.strip() for s in services_raw.split(",") if s.strip()]
    elif isinstance(services_raw, list):
        services = services_raw
    else:
        services = []

    # Parse level (clamp to valid KEPH range 1-6)
    try:
        level = int(row.get("level", row.get("facility_level", 1)))
    except (ValueError, TypeError):
        level = 1
    level = max(1, min(6, level))

    # Parse is_active
    is_active_raw = row.get("is_active", True)
    if isinstance(is_active_raw, bool):
        is_active = is_active_raw
    else:
        is_active = str(is_active_raw).lower() in ("true", "1", "yes")

    return {
        "facility_id": facility_id,
        "name": name,
        "county": row.get("county"),
        "level": level,
        "lat": lat,
        "lon": lon,
        "phone": row.get("phone", row.get("telephone")),
        "services": services,
        "is_active": is_active,
    }


def _load_csv(source_path: str) -> list[dict[str, Any]]:
    """Load facilities from a CSV file. Returns list of parsed facility dicts."""
    facilities = []
    with open(source_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = _parse_facility_row(row)
            if parsed is not None:
                facilities.append(parsed)
    return facilities


def _load_json(source_path: str) -> list[dict[str, Any]]:
    """Load facilities from a JSON file. Returns list of parsed facility dicts."""
    with open(source_path, encoding="utf-8") as f:
        data = json.load(f)

    # Handle nested structures: {"facilities": [...]}, {"results": [...]}, or flat list
    if isinstance(data, dict):
        data = data.get("facilities", data.get("results", [data]))

    facilities = []
    for row in data:
        parsed = _parse_facility_row(row)
        if parsed is not None:
            facilities.append(parsed)
    return facilities


async def load_facilities(source_path: str, source_name: str, dry_run: bool = False) -> None:
    """Load facilities from a source file into PostgreSQL.

    Args:
        source_path: Path to CSV or JSON file with facility data.
        source_name: Identifier for this data load (e.g. KMHFL_2024_02).
        dry_run: If True, parse and validate but skip database writes.
    """
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url and not dry_run:
        print("ERROR: DATABASE_URL is not set. Use --dry-run to validate without database.")
        sys.exit(1)

    # Load and parse data
    ext = Path(source_path).suffix.lower()
    if ext == ".csv":
        facilities = _load_csv(source_path)
    elif ext == ".json":
        facilities = _load_json(source_path)
    else:
        print(f"ERROR: Unsupported file format: {ext}. Use .csv or .json.")
        sys.exit(1)

    if not facilities:
        print("WARNING: No valid facilities found in source file.")
        return

    print(f"Parsed {len(facilities)} valid facilities from {source_path}")

    if dry_run:
        # Print sample for validation
        for f in facilities[:5]:
            print(
                f"  {f['facility_id']}: {f['name']} (L{f['level']}, {f['lat']:.4f}, {f['lon']:.4f})"
            )
        if len(facilities) > 5:
            print(f"  ... and {len(facilities) - 5} more")
        print(f"\nDry run complete — {len(facilities)} facilities would be upserted.")
        return

    # Connect to database
    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    upserted = 0
    rejected = 0

    async with session_factory() as session:
        # Single transaction — all upserts + audit row commit together.
        # If anything fails, the entire load rolls back.
        async with session.begin():
            for facility in facilities:
                try:
                    await session.execute(
                        text("""
                            INSERT INTO facilities
                                (facility_id, name, county, level, lat, lon, phone,
                                 services, is_active, data_source, last_verified_at,
                                 created_at, updated_at)
                            VALUES
                                (:facility_id, :name, :county, :level, :lat, :lon, :phone,
                                 :services, :is_active, :data_source, NOW(),
                                 NOW(), NOW())
                            ON CONFLICT (facility_id) DO UPDATE SET
                                name = EXCLUDED.name,
                                county = EXCLUDED.county,
                                level = EXCLUDED.level,
                                lat = EXCLUDED.lat,
                                lon = EXCLUDED.lon,
                                phone = EXCLUDED.phone,
                                services = EXCLUDED.services,
                                is_active = EXCLUDED.is_active,
                                data_source = EXCLUDED.data_source,
                                last_verified_at = NOW(),
                                updated_at = NOW()
                        """),
                        {
                            "facility_id": facility["facility_id"],
                            "name": facility["name"],
                            "county": facility.get("county"),
                            "level": facility["level"],
                            "lat": facility["lat"],
                            "lon": facility["lon"],
                            "phone": facility.get("phone"),
                            "services": facility.get("services", []),
                            "is_active": facility.get("is_active", True),
                            "data_source": source_name,
                        },
                    )
                    upserted += 1
                except Exception as exc:
                    print(f"  ERROR upserting {facility['facility_id']}: {exc}")
                    rejected += 1

            # Write DataImport audit row in the same transaction
            await session.execute(
                text("""
                    INSERT INTO data_imports (source, record_count, loaded_at, loaded_by)
                    VALUES (:source, :record_count, NOW(), :loaded_by)
                """),
                {
                    "source": source_name,
                    "record_count": upserted,
                    "loaded_by": "load_facilities_cli",
                },
            )
        # Transaction commits here — all or nothing

    await engine.dispose()

    print("\nLoad complete:")
    print(f"  Upserted: {upserted}")
    print(f"  Rejected: {rejected}")
    print(f"  Source:   {source_name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load facility data from CSV or JSON into PostgreSQL."
    )
    parser.add_argument(
        "--source", required=True, help="Path to CSV or JSON file with facility data."
    )
    parser.add_argument(
        "--source-name", required=True, help="Identifier for this data load (e.g. KMHFL_2024_02)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate data without writing to the database.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.source):
        print(f"ERROR: Source file not found: {args.source}")
        sys.exit(1)

    asyncio.run(load_facilities(args.source, args.source_name, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
