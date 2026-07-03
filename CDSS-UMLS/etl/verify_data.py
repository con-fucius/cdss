"""
Verify UMLS data loaded into PostgreSQL

This script runs verification queries to ensure data is loaded correctly.
"""
from sqlalchemy import create_engine, text
from api.config import settings
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def format_table(data, headers):
    """Simple table formatter without external dependencies"""
    if not data:
        return ""
    
    # Calculate column widths
    col_widths = [len(str(h)) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
    
    # Create separator
    separator = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    
    # Format header
    header_row = "|" + "|".join(f" {str(h):<{col_widths[i]}} " for i, h in enumerate(headers)) + "|"
    
    # Format rows
    rows = []
    for row in data:
        row_str = "|" + "|".join(f" {str(cell):<{col_widths[i]}} " for i, cell in enumerate(row)) + "|"
        rows.append(row_str)
    
    return "\n".join([separator, header_row, separator] + rows + [separator])


def run_verification():
    """Run verification queries and display results"""
    engine = create_engine(settings.DATABASE_URL)
    
    results = []
    
    try:
        with engine.connect() as conn:
            # 1. Basic counts
            logger.info("Running verification queries...")
            
            # Total concepts
            result = conn.execute(text("SELECT COUNT(*) FROM umls_concepts"))
            total_concepts = result.scalar()
            results.append(["Total Concepts", f"{total_concepts:,}", "~3,181,468"])
            
            # Total relations
            result = conn.execute(text("SELECT COUNT(*) FROM umls_relations"))
            total_relations = result.scalar()
            results.append(["Total Relations", f"{total_relations:,}", "~50-60M"])
            
            # Unique CUIs
            result = conn.execute(text("SELECT COUNT(DISTINCT cui) FROM umls_concepts"))
            unique_cuis = result.scalar()
            results.append(["Unique CUIs", f"{unique_cuis:,}", f"Should match total ({total_concepts:,})"])
            
            # Concepts without preferred_name
            result = conn.execute(text("""
                SELECT COUNT(*) FROM umls_concepts 
                WHERE preferred_name IS NULL OR preferred_name = ''
            """))
            no_preferred = result.scalar()
            results.append(["Concepts without Preferred Name", f"{no_preferred:,}", "0 (all should have)"])
            
            # Concepts with semantic types
            result = conn.execute(text("""
                SELECT COUNT(*) FROM umls_concepts 
                WHERE array_length(semantic_types, 1) > 0
            """))
            with_semantic_types = result.scalar()
            results.append(["Concepts with Semantic Types", f"{with_semantic_types:,}", "~2.5M+"])
            
            # Concepts without semantic types
            result = conn.execute(text("""
                SELECT COUNT(*) FROM umls_concepts 
                WHERE semantic_types IS NULL OR array_length(semantic_types, 1) IS NULL
            """))
            no_semantic_types = result.scalar()
            results.append(["Concepts without Semantic Types", f"{no_semantic_types:,}", "Some (OK)"])
            
            # Concepts with definitions
            result = conn.execute(text("""
                SELECT COUNT(*) FROM umls_concepts 
                WHERE definition IS NOT NULL AND definition != ''
            """))
            with_definitions = result.scalar()
            results.append(["Concepts with Definitions", f"{with_definitions:,}", "~1-2M"])
            
            # Concepts with synonyms
            result = conn.execute(text("""
                SELECT COUNT(*) FROM umls_concepts 
                WHERE array_length(synonyms, 1) > 0
            """))
            with_synonyms = result.scalar()
            results.append(["Concepts with Synonyms", f"{with_synonyms:,}", "Most concepts"])
            
            # Concepts with embeddings
            result = conn.execute(text("""
                SELECT COUNT(*) FROM umls_concepts 
                WHERE embedding IS NOT NULL
            """))
            with_embeddings = result.scalar()
            results.append(["Concepts with Embeddings", f"{with_embeddings:,}", "~3.18M (after generation)"])
            
            # Orphaned relations (cui1)
            result = conn.execute(text("""
                SELECT COUNT(DISTINCT r.cui1) 
                FROM umls_relations r
                LEFT JOIN umls_concepts c ON r.cui1 = c.cui
                WHERE c.cui IS NULL
            """))
            orphaned_cui1 = result.scalar()
            results.append(["Orphaned Relations (cui1)", f"{orphaned_cui1:,}", "0 (data integrity)"])
            
            # Orphaned relations (cui2)
            result = conn.execute(text("""
                SELECT COUNT(DISTINCT r.cui2) 
                FROM umls_relations r
                LEFT JOIN umls_concepts c ON r.cui2 = c.cui
                WHERE c.cui IS NULL
            """))
            orphaned_cui2 = result.scalar()
            results.append(["Orphaned Relations (cui2)", f"{orphaned_cui2:,}", "0 (data integrity)"])
            
            # Top relation types
            result = conn.execute(text("""
                SELECT relation_type, COUNT(*) as count
                FROM umls_relations
                GROUP BY relation_type
                ORDER BY count DESC
                LIMIT 5
            """))
            top_relations = result.fetchall()
            
    except Exception as e:
        logger.error(f"Error running verification: {e}")
        return
    
    # Display results
    print("\n" + "=" * 80)
    print("UMLS DATA VERIFICATION RESULTS")
    print("=" * 80)
    print(format_table(results, ["Metric", "Actual Value", "Expected Value"]))
    
    # Display top relation types
    if top_relations:
        print("\n" + "=" * 80)
        print("TOP 5 RELATION TYPES")
        print("=" * 80)
        rel_results = [[row[0], f"{row[1]:,}"] for row in top_relations]
        print(format_table(rel_results, ["Relation Type", "Count"]))
    
    # Health check summary
    print("\n" + "=" * 80)
    print("HEALTH CHECK SUMMARY")
    print("=" * 80)
    
    issues = []
    warnings = []
    
    if total_concepts < 3000000:
        issues.append(f"⚠️  Total concepts ({total_concepts:,}) is lower than expected (~3.18M)")
    elif total_concepts > 3200000:
        warnings.append(f"ℹ️  Total concepts ({total_concepts:,}) is higher than expected")
    else:
        print("✅ Total concepts: OK")
    
    if no_preferred > 0:
        issues.append(f"❌ {no_preferred:,} concepts without preferred_name (should be 0)")
    else:
        print("✅ All concepts have preferred_name")
    
    if orphaned_cui1 > 0 or orphaned_cui2 > 0:
        issues.append(f"❌ Found orphaned relations: cui1={orphaned_cui1}, cui2={orphaned_cui2}")
    else:
        print("✅ No orphaned relations (data integrity OK)")
    
    if with_embeddings == 0:
        warnings.append("ℹ️  No embeddings found. Run generate_embeddings.py to generate them.")
    elif with_embeddings < total_concepts * 0.9:
        warnings.append(f"⚠️  Only {with_embeddings:,}/{total_concepts:,} concepts have embeddings")
    else:
        print("✅ Embeddings: OK")
    
    if issues:
        print("\n❌ ISSUES FOUND:")
        for issue in issues:
            print(f"  {issue}")
    
    if warnings:
        print("\n⚠️  WARNINGS:")
        for warning in warnings:
            print(f"  {warning}")
    
    if not issues and not warnings:
        print("✅ All checks passed! Data looks good.")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    run_verification()

