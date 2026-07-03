"""
Unit tests for facility-mapper/scripts/load_facilities.py

Tests the parser and validator functions without a database:
- _validate_coordinates for Kenya/Uganda/DRC bounds
- _parse_facility_row for column name variants, edge cases, level clamping
- _load_csv and _load_json for file parsing
"""

from __future__ import annotations

import csv
import json
import os
import tempfile

import pytest

from scripts.load_facilities import (
    _load_csv,
    _load_json,
    _parse_facility_row,
    _validate_coordinates,
)


# ── Coordinate validation ─────────────────────────────────────────────────


class TestValidateCoordinates:
    """Tests for _validate_coordinates — Kenya/Uganda/DRC bounding box."""

    def test_nairobi_is_valid(self):
        assert _validate_coordinates(-1.2921, 36.8219) is True

    def test_mombasa_is_valid(self):
        assert _validate_coordinates(-4.0435, 39.6682) is True

    def test_kampala_is_valid(self):
        assert _validate_coordinates(0.3476, 32.5825) is True

    def test_kinshasa_is_valid(self):
        assert _validate_coordinates(-4.4419, 15.2663) is False  # outside lon

    def test_london_is_invalid(self):
        assert _validate_coordinates(51.5074, -0.1278) is False

    def test_new_york_is_invalid(self):
        assert _validate_coordinates(40.7128, -74.0060) is False

    def test_boundary_south_inclusive(self):
        assert _validate_coordinates(-5.0, 36.0) is True

    def test_boundary_south_exclusive(self):
        assert _validate_coordinates(-5.001, 36.0) is False

    def test_boundary_north_inclusive(self):
        assert _validate_coordinates(5.0, 36.0) is True

    def test_boundary_north_exclusive(self):
        assert _validate_coordinates(5.001, 36.0) is False

    def test_boundary_west_inclusive(self):
        assert _validate_coordinates(0.0, 29.0) is True

    def test_boundary_west_exclusive(self):
        assert _validate_coordinates(0.0, 28.999) is False

    def test_boundary_east_inclusive(self):
        assert _validate_coordinates(0.0, 42.0) is True

    def test_boundary_east_exclusive(self):
        assert _validate_coordinates(0.0, 42.001) is False

    def test_origin_is_valid(self):
        assert _validate_coordinates(0.0, 0.0) is False  # lon 0 is outside 29-42


# ── Facility row parser ───────────────────────────────────────────────────


class TestParseFacilityRow:
    """Tests for _parse_facility_row — parsing, normalization, and rejection."""

    def _minimal_row(self, **overrides):
        """Return a minimal valid row dict with optional overrides."""
        row = {
            "facility_id": "F001",
            "name": "Test Hospital",
            "lat": -1.29,
            "lon": 36.82,
        }
        row.update(overrides)
        return row

    # ── facility_id handling ──────────────────────────────────────────

    def test_valid_row(self):
        result = _parse_facility_row(self._minimal_row())
        assert result is not None
        assert result["facility_id"] == "F001"
        assert result["name"] == "Test Hospital"
        assert result["lat"] == -1.29
        assert result["lon"] == 36.82

    def test_empty_facility_id_rejected(self):
        assert _parse_facility_row(self._minimal_row(facility_id="")) is None

    def test_whitespace_facility_id_rejected(self):
        assert _parse_facility_row(self._minimal_row(facility_id="   ")) is None

    def test_id_field_fallback(self):
        """When facility_id is missing, fall back to 'id'."""
        row = self._minimal_row()
        del row["facility_id"]
        row["id"] = "F002"
        result = _parse_facility_row(row)
        assert result is not None
        assert result["facility_id"] == "F002"

    def test_facility_id_stripped(self):
        result = _parse_facility_row(self._minimal_row(facility_id="  F001  "))
        assert result["facility_id"] == "F001"

    # ── name handling ─────────────────────────────────────────────────

    def test_empty_name_rejected(self):
        assert _parse_facility_row(self._minimal_row(name="")) is None

    def test_whitespace_name_rejected(self):
        assert _parse_facility_row(self._minimal_row(name="   ")) is None

    def test_facility_name_fallback(self):
        """When name is missing, fall back to 'facility_name'."""
        row = self._minimal_row()
        del row["name"]
        row["facility_name"] = "Fallback Hospital"
        result = _parse_facility_row(row)
        assert result is not None
        assert result["name"] == "Fallback Hospital"

    def test_name_stripped(self):
        result = _parse_facility_row(self._minimal_row(name="  Test Hospital  "))
        assert result["name"] == "Test Hospital"

    # ── coordinate handling ───────────────────────────────────────────

    def test_invalid_lat_rejected(self):
        assert _parse_facility_row(self._minimal_row(lat="not_a_number")) is None

    def test_invalid_lon_rejected(self):
        assert _parse_facility_row(self._minimal_row(lon="not_a_number")) is None

    def test_none_lat_rejected(self):
        assert _parse_facility_row(self._minimal_row(lat=None)) is None

    def test_out_of_bounds_coords_rejected(self):
        assert _parse_facility_row(self._minimal_row(lat=51.5, lon=-0.1)) is None

    def test_string_coords_parsed(self):
        result = _parse_facility_row(self._minimal_row(lat="-1.29", lon="36.82"))
        assert result is not None
        assert result["lat"] == -1.29
        assert result["lon"] == 36.82

    def test_latitude_fallback_field(self):
        row = self._minimal_row()
        del row["lat"]
        row["latitude"] = -1.29
        result = _parse_facility_row(row)
        assert result is not None
        assert result["lat"] == -1.29

    def test_longitude_fallback_field(self):
        row = self._minimal_row()
        del row["lon"]
        row["longitude"] = 36.82
        result = _parse_facility_row(row)
        assert result is not None
        assert result["lon"] == 36.82

    def test_zero_coords_rejected(self):
        """(0, 0) is outside the lon bounds (29-42)."""
        assert _parse_facility_row(self._minimal_row(lat=0.0, lon=0.0)) is None

    # ── level handling ────────────────────────────────────────────────

    def test_level_parsed(self):
        result = _parse_facility_row(self._minimal_row(level=5))
        assert result["level"] == 5

    def test_level_default_when_missing(self):
        result = _parse_facility_row(self._minimal_row())
        assert result["level"] == 1

    def test_level_facility_level_fallback(self):
        row = self._minimal_row()
        row["facility_level"] = 4
        result = _parse_facility_row(row)
        assert result["level"] == 4

    def test_level_clamped_to_max(self):
        result = _parse_facility_row(self._minimal_row(level=10))
        assert result["level"] == 6

    def test_level_clamped_to_min(self):
        result = _parse_facility_row(self._minimal_row(level=0))
        assert result["level"] == 1

    def test_level_negative_clamped(self):
        result = _parse_facility_row(self._minimal_row(level=-3))
        assert result["level"] == 1

    def test_level_invalid_string_defaults(self):
        result = _parse_facility_row(self._minimal_row(level="high"))
        assert result["level"] == 1

    def test_level_none_defaults(self):
        result = _parse_facility_row(self._minimal_row(level=None))
        assert result["level"] == 1

    def test_level_string_number_parsed(self):
        result = _parse_facility_row(self._minimal_row(level="3"))
        assert result["level"] == 3

    def test_level_float_truncated(self):
        """Float level is truncated to int via int()."""
        result = _parse_facility_row(self._minimal_row(level=4.7))
        assert result["level"] == 4

    # ── services handling ─────────────────────────────────────────────

    def test_services_comma_separated(self):
        result = _parse_facility_row(self._minimal_row(services="surgery,icu,maternity"))
        assert result["services"] == ["surgery", "icu", "maternity"]

    def test_services_list_input(self):
        result = _parse_facility_row(self._minimal_row(services=["surgery", "icu"]))
        assert result["services"] == ["surgery", "icu"]

    def test_services_empty_string(self):
        result = _parse_facility_row(self._minimal_row(services=""))
        assert result["services"] == []

    def test_services_whitespace_trimmed(self):
        result = _parse_facility_row(self._minimal_row(services=" surgery , icu "))
        assert result["services"] == ["surgery", "icu"]

    def test_services_with_empty_entries(self):
        result = _parse_facility_row(self._minimal_row(services="surgery,,icu,"))
        assert result["services"] == ["surgery", "icu"]

    def test_services_missing(self):
        result = _parse_facility_row(self._minimal_row())
        assert result["services"] == []

    def test_services_service_types_fallback(self):
        """When services is missing, fall back to 'service_types'."""
        row = self._minimal_row(service_types="surgery,icu")
        result = _parse_facility_row(row)
        assert result["services"] == ["surgery", "icu"]

    def test_services_none_input(self):
        result = _parse_facility_row(self._minimal_row(services=None))
        assert result["services"] == []

    def test_services_integer_input(self):
        """Non-string, non-list types should fall back to empty list."""
        result = _parse_facility_row(self._minimal_row(services=123))
        assert result["services"] == []

    # ── is_active handling ────────────────────────────────────────────

    def test_is_active_true(self):
        result = _parse_facility_row(self._minimal_row(is_active=True))
        assert result["is_active"] is True

    def test_is_active_false(self):
        result = _parse_facility_row(self._minimal_row(is_active=False))
        assert result["is_active"] is False

    def test_is_active_string_true(self):
        result = _parse_facility_row(self._minimal_row(is_active="true"))
        assert result["is_active"] is True

    def test_is_active_string_false(self):
        result = _parse_facility_row(self._minimal_row(is_active="false"))
        assert result["is_active"] is False

    def test_is_active_string_one(self):
        result = _parse_facility_row(self._minimal_row(is_active="1"))
        assert result["is_active"] is True

    def test_is_active_string_zero(self):
        result = _parse_facility_row(self._minimal_row(is_active="0"))
        assert result["is_active"] is False

    def test_is_active_yes(self):
        result = _parse_facility_row(self._minimal_row(is_active="yes"))
        assert result["is_active"] is True

    def test_is_active_no(self):
        result = _parse_facility_row(self._minimal_row(is_active="no"))
        assert result["is_active"] is False

    def test_is_active_default_true(self):
        """When is_active is missing, default to True."""
        result = _parse_facility_row(self._minimal_row())
        assert result["is_active"] is True

    def test_is_active_integer_one(self):
        """Integer 1 is treated as true via str() conversion."""
        result = _parse_facility_row(self._minimal_row(is_active=1))
        assert result["is_active"] is True

    def test_is_active_integer_zero(self):
        result = _parse_facility_row(self._minimal_row(is_active=0))
        assert result["is_active"] is False

    # ── optional fields ───────────────────────────────────────────────

    def test_county_parsed(self):
        result = _parse_facility_row(self._minimal_row(county="Nairobi"))
        assert result["county"] == "Nairobi"

    def test_county_missing(self):
        result = _parse_facility_row(self._minimal_row())
        assert result["county"] is None

    def test_phone_parsed(self):
        result = _parse_facility_row(self._minimal_row(phone="020-1234567"))
        assert result["phone"] == "020-1234567"

    def test_phone_telephone_fallback(self):
        row = self._minimal_row()
        row["telephone"] = "020-7654321"
        result = _parse_facility_row(row)
        assert result["phone"] == "020-7654321"

    def test_phone_missing(self):
        result = _parse_facility_row(self._minimal_row())
        assert result["phone"] is None

    # ── complete row with all fields ──────────────────────────────────

    def test_complete_row(self):
        row = {
            "facility_id": "F099",
            "name": "Kenyatta Hospital",
            "county": "Nairobi",
            "level": 5,
            "lat": -1.2984,
            "lon": 36.8165,
            "phone": "020-6992000",
            "services": "surgery,icu,maternity",
            "is_active": True,
        }
        result = _parse_facility_row(row)
        assert result is not None
        assert result["facility_id"] == "F099"
        assert result["name"] == "Kenyatta Hospital"
        assert result["county"] == "Nairobi"
        assert result["level"] == 5
        assert result["lat"] == -1.2984
        assert result["lon"] == 36.8165
        assert result["phone"] == "020-6992000"
        assert result["services"] == ["surgery", "icu", "maternity"]
        assert result["is_active"] is True


# ── CSV file loading ──────────────────────────────────────────────────────


class TestLoadCSV:
    """Tests for _load_csv — reading facilities from CSV files."""

    def _write_csv(self, tmp_path, rows, filename="facilities.csv"):
        """Write rows to a CSV file and return its path."""
        path = tmp_path / filename
        if not rows:
            path.write_text("facility_id,name,lat,lon\n", encoding="utf-8")
        else:
            fieldnames = list(rows[0].keys())
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        return str(path)

    def test_load_valid_csv(self, tmp_path):
        rows = [
            {"facility_id": "F001", "name": "Hospital A", "lat": "-1.29", "lon": "36.82", "level": "4"},
            {"facility_id": "F002", "name": "Hospital B", "lat": "-1.30", "lon": "36.83", "level": "3"},
        ]
        path = self._write_csv(tmp_path, rows)
        facilities = _load_csv(path)
        assert len(facilities) == 2
        assert facilities[0]["facility_id"] == "F001"
        assert facilities[1]["facility_id"] == "F002"

    def test_csv_skips_invalid_rows(self, tmp_path):
        rows = [
            {"facility_id": "F001", "name": "Valid", "lat": "-1.29", "lon": "36.82"},
            {"facility_id": "", "name": "No ID", "lat": "-1.29", "lon": "36.82"},
            {"facility_id": "F003", "name": "Out of bounds", "lat": "51.5", "lon": "-0.1"},
        ]
        path = self._write_csv(tmp_path, rows)
        facilities = _load_csv(path)
        assert len(facilities) == 1
        assert facilities[0]["facility_id"] == "F001"

    def test_csv_empty_file(self, tmp_path):
        path = self._write_csv(tmp_path, [])
        facilities = _load_csv(path)
        assert facilities == []

    def test_csv_only_invalid_rows(self, tmp_path):
        rows = [
            {"facility_id": "", "name": "No ID", "lat": "-1.29", "lon": "36.82"},
            {"facility_id": "F002", "name": "Out", "lat": "99", "lon": "99"},
        ]
        path = self._write_csv(tmp_path, rows)
        facilities = _load_csv(path)
        assert facilities == []

    def test_csv_services_comma_separated(self, tmp_path):
        rows = [
            {"facility_id": "F001", "name": "H", "lat": "-1.29", "lon": "36.82",
             "services": "surgery,icu"},
        ]
        path = self._write_csv(tmp_path, rows)
        facilities = _load_csv(path)
        assert facilities[0]["services"] == ["surgery", "icu"]


# ── JSON file loading ─────────────────────────────────────────────────────


class TestLoadJSON:
    """Tests for _load_json — reading facilities from JSON files."""

    def _write_json(self, tmp_path, data, filename="facilities.json"):
        """Write data to a JSON file and return its path."""
        path = tmp_path / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_load_flat_list(self, tmp_path):
        data = [
            {"facility_id": "F001", "name": "Hospital A", "lat": -1.29, "lon": 36.82},
            {"facility_id": "F002", "name": "Hospital B", "lat": -1.30, "lon": 36.83},
        ]
        path = self._write_json(tmp_path, data)
        facilities = _load_json(path)
        assert len(facilities) == 2

    def test_load_nested_facilities_key(self, tmp_path):
        data = {"facilities": [
            {"facility_id": "F001", "name": "Hospital A", "lat": -1.29, "lon": 36.82},
        ]}
        path = self._write_json(tmp_path, data)
        facilities = _load_json(path)
        assert len(facilities) == 1

    def test_load_nested_results_key(self, tmp_path):
        data = {"results": [
            {"facility_id": "F001", "name": "Hospital A", "lat": -1.29, "lon": 36.82},
        ]}
        path = self._write_json(tmp_path, data)
        facilities = _load_json(path)
        assert len(facilities) == 1

    def test_json_skips_invalid_rows(self, tmp_path):
        data = [
            {"facility_id": "F001", "name": "Valid", "lat": -1.29, "lon": 36.82},
            {"facility_id": "", "name": "No ID", "lat": -1.29, "lon": 36.82},
        ]
        path = self._write_json(tmp_path, data)
        facilities = _load_json(path)
        assert len(facilities) == 1
        assert facilities[0]["facility_id"] == "F001"

    def test_json_empty_list(self, tmp_path):
        path = self._write_json(tmp_path, [])
        facilities = _load_json(path)
        assert facilities == []

    def test_json_services_as_list(self, tmp_path):
        data = [{"facility_id": "F001", "name": "H", "lat": -1.29, "lon": 36.82,
                 "services": ["surgery", "icu"]}]
        path = self._write_json(tmp_path, data)
        facilities = _load_json(path)
        assert facilities[0]["services"] == ["surgery", "icu"]

    def test_json_level_clamped(self, tmp_path):
        data = [{"facility_id": "F001", "name": "H", "lat": -1.29, "lon": 36.82, "level": 99}]
        path = self._write_json(tmp_path, data)
        facilities = _load_json(path)
        assert facilities[0]["level"] == 6

    def test_json_unknown_nested_key_returns_empty(self, tmp_path):
        """JSON with an unrecognized wrapper key (e.g. 'data') yields no facilities."""
        data = {"data": [{"facility_id": "F001", "name": "H", "lat": -1.29, "lon": 36.82}]}
        path = self._write_json(tmp_path, data)
        facilities = _load_json(path)
        assert facilities == []
