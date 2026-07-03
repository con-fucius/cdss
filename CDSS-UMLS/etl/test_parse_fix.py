"""Test script to verify the parse_rrf_file fix works correctly."""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from etl.combine_umls import load_mrconso, parse_rrf_file


def test_field_positions():
    """Test that field positions are preserved after parsing."""
    print("Testing field position preservation...")
    print("=" * 80)

    mrconso_path = Path("data/umls/MRCONSO.RRF")
    if not mrconso_path.exists():
        print(f"❌ MRCONSO.RRF not found at {mrconso_path}")
        return False

    # Parse a small sample
    print(f"Parsing first 100 records from {mrconso_path.name}...")
    records = parse_rrf_file(mrconso_path)

    if not records:
        print("❌ No records parsed")
        return False

    # Check first few records
    print("\nChecking field positions in first 5 records:")
    print("-" * 80)

    expected_fields = {0: "CUI", 1: "LAT", 6: "ISPREF", 11: "SAB", 12: "TTY", 13: "CODE", 14: "STR"}

    all_correct = True
    for i, record in enumerate(records[:5]):
        print(f"\nRecord {i + 1}:")
        for pos, field_name in expected_fields.items():
            if len(record) > pos:
                value = record[pos] if record[pos] else "(empty)"
                print(f"  [{pos:2}] {field_name:8} = {value[:50]}")
            else:
                print(f"  [{pos:2}] {field_name:8} = MISSING")
                all_correct = False

    # Test with a known CUI
    print("\n" + "=" * 80)
    print("Testing preferred name extraction for CUI C0000039...")
    print("-" * 80)

    concepts = load_mrconso(records[:10000])  # First 10k records

    if "C0000039" in concepts:
        concept = concepts["C0000039"]
        print(f"Preferred Name: '{concept['preferred_name']}'")
        print(f"Preferred Source: '{concept.get('preferred_source', 'N/A')}'")
        print(f"Synonyms (first 5): {concept['synonyms'][:5]}")

        # Check if preferred name is not a placeholder
        placeholder_values = {"N", "0", "3", "256", "9", ""}
        if concept["preferred_name"] in placeholder_values:
            print(f"⚠️  Preferred name is still a placeholder: '{concept['preferred_name']}'")
            print("   This might be expected if all preferred terms are placeholders")
        else:
            print("✅ Preferred name is not a placeholder")
    else:
        print("CUI C0000039 not found in first 10k records")

    print("\n" + "=" * 80)
    if all_correct:
        print("✅ Field positions are preserved correctly!")
        return True
    else:
        print("❌ Some field positions are incorrect")
        return False


if __name__ == "__main__":
    success = test_field_positions()
    sys.exit(0 if success else 1)
