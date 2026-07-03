"""
Clear UMLS data from PostgreSQL database

This script safely clears UMLS concepts and relations tables.
Optionally preserves clinical_documents if you want to keep RAG data.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from api.config import settings
import logging
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def clear_umls_data(keep_clinical_docs: bool = True):
    """
    Clear UMLS concepts and relations from the database.
    
    Args:
        keep_clinical_docs: If True, preserves clinical_documents table (for RAG embeddings)
    """
    try:
        engine = create_engine(settings.DATABASE_URL)
        
        # Test connection first
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            logger.error("=" * 60)
            logger.error("Database Connection Failed")
            logger.error("=" * 60)
            logger.error(f"Error: {e}")
            logger.error("\nTroubleshooting Steps:")
            logger.error("\n1. Check PostgreSQL service status:")
            logger.error("   brew services list | grep postgres")
            logger.error("\n2. Start PostgreSQL:")
            logger.error("   brew services start postgresql@14")
            logger.error("   # If that fails, try:")
            logger.error("   pg_ctl -D /opt/homebrew/var/postgresql@14 start")
            logger.error("   # or")
            logger.error("   pg_ctl -D /usr/local/var/postgresql@14 start")
            logger.error("\n3. Initialize database (if needed):")
            logger.error("   initdb /opt/homebrew/var/postgresql@14")
            logger.error("\n4. Create database (if it doesn't exist):")
            logger.error("   createdb umls_cdss")
            logger.error("\n5. Check DATABASE_URL in .env file:")
            db_url_parts = settings.DATABASE_URL.split('@')
            if len(db_url_parts) > 0:
                logger.error(f"   Current: {db_url_parts[0]}@***")
            logger.error("\n6. Test connection manually:")
            logger.error("   psql -h localhost -p 5432 -U user -d umls_cdss")
            logger.error("=" * 60)
            raise
    except Exception as e:
        if "Connection refused" in str(e) or "connection" in str(e).lower():
            logger.error("\n" + "=" * 60)
            logger.error("PostgreSQL is not running or not accessible")
            logger.error("=" * 60)
            logger.error("Please start PostgreSQL first, then try again.")
            logger.error("=" * 60)
        raise
    
    try:
        logger.info("=" * 60)
        logger.info("Clearing UMLS Data from Database")
        logger.info("=" * 60)
        
        with engine.begin() as conn:
            # Get counts before deletion
            concepts_count = conn.execute(text("SELECT COUNT(*) FROM umls_concepts")).scalar()
            relations_count = conn.execute(text("SELECT COUNT(*) FROM umls_relations")).scalar()
            clinical_docs_count = conn.execute(text("SELECT COUNT(*) FROM clinical_documents")).scalar() if keep_clinical_docs else 0
            
            logger.info(f"\nCurrent data counts:")
            logger.info(f"  Concepts: {concepts_count:,}")
            logger.info(f"  Relations: {relations_count:,}")
            if keep_clinical_docs:
                logger.info(f"  Clinical Documents: {clinical_docs_count:,} (will be preserved)")
            
            # Confirm deletion
            if concepts_count == 0 and relations_count == 0:
                logger.info("\n✓ Database is already empty. Nothing to clear.")
                return
            
            logger.info(f"\n⚠️  About to delete:")
            logger.info(f"  - {concepts_count:,} concepts")
            logger.info(f"  - {relations_count:,} relations")
            if not keep_clinical_docs:
                logger.info(f"  - {clinical_docs_count:,} clinical documents")
            
            # Delete in order (relations first due to potential foreign key constraints)
            logger.info("\nDeleting relations...")
            conn.execute(text("DELETE FROM umls_relations"))
            logger.info("✓ Relations deleted")
            
            logger.info("Deleting concepts...")
            conn.execute(text("DELETE FROM umls_concepts"))
            logger.info("✓ Concepts deleted")
            
            if not keep_clinical_docs:
                logger.info("Deleting clinical documents...")
                conn.execute(text("DELETE FROM clinical_documents"))
                logger.info("✓ Clinical documents deleted")
            
            # Verify deletion
            remaining_concepts = conn.execute(text("SELECT COUNT(*) FROM umls_concepts")).scalar()
            remaining_relations = conn.execute(text("SELECT COUNT(*) FROM umls_relations")).scalar()
            
            logger.info("\n" + "=" * 60)
            logger.info("Deletion Complete")
            logger.info("=" * 60)
            logger.info(f"Remaining concepts: {remaining_concepts:,}")
            logger.info(f"Remaining relations: {remaining_relations:,}")
            
            if remaining_concepts == 0 and remaining_relations == 0:
                logger.info("\n✓ Database cleared successfully!")
            else:
                logger.warning(f"\n⚠️  Warning: Some data remains in the database")
        
    except Exception as e:
        logger.error(f"Error clearing database: {e}")
        raise


def reset_sequences():
    """
    Reset PostgreSQL sequences (useful if you want to reset auto-incrementing IDs).
    Note: UUIDs are used, so this may not be necessary, but included for completeness.
    """
    engine = create_engine(settings.DATABASE_URL)
    
    try:
        with engine.begin() as conn:
            # Reset sequences if any exist
            # Since we're using UUIDs, this may not be needed, but included for completeness
            logger.info("Resetting sequences (if any)...")
            # No sequences to reset for UUID-based tables
            logger.info("✓ Sequences checked (UUID-based tables don't use sequences)")
    except Exception as e:
        logger.warning(f"Note: {e}")


if __name__ == "__main__":
    import sys
    
    # Check for command-line arguments
    keep_docs = True
    if len(sys.argv) > 1 and sys.argv[1] == "--clear-all":
        keep_docs = False
        logger.warning("⚠️  Will also delete clinical_documents!")
    
    try:
        clear_umls_data(keep_clinical_docs=keep_docs)
        reset_sequences()
        
        logger.info("\n" + "=" * 60)
        logger.info("Next Steps:")
        logger.info("=" * 60)
        logger.info("1. Regenerate processed data: poetry run python etl/combine_umls.py")
        logger.info("2. Load data into database: poetry run python etl/load_processed_data.py")
        logger.info("3. Generate embeddings: poetry run python etl/generate_embeddings.py")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Failed to clear database: {e}")
        sys.exit(1)

