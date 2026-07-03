"""
Generate embeddings for UMLS concepts and store in PostgreSQL

This script reads embeddable_text.jsonl, generates embeddings using sentence-transformers,
and stores them in the umls_concepts table using pgvector.
"""
import json
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from api.config import settings
from sentence_transformers import SentenceTransformer
import logging
from tqdm import tqdm
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def ensure_embedding_column(engine):
    """Ensure the embedding column exists in umls_concepts table"""
    try:
        with engine.begin() as conn:  # Use begin() for automatic transaction management
            # Check if column exists
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='umls_concepts' AND column_name='embedding'
            """))
            
            if result.fetchone() is None:
                logger.info("Adding embedding column to umls_concepts table...")
                conn.execute(text("""
                    ALTER TABLE umls_concepts 
                    ADD COLUMN embedding vector(384)
                """))
                logger.info("✓ Embedding column added")
            else:
                logger.info("Embedding column already exists")
                
            # Check if index exists
            result = conn.execute(text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename='umls_concepts' AND indexname='idx_umls_concepts_embedding'
            """))
            
            if result.fetchone() is None:
                logger.info("Creating vector index for embeddings...")
                try:
                    conn.execute(text("""
                        CREATE INDEX idx_umls_concepts_embedding 
                        ON umls_concepts 
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100)
                    """))
                    logger.info("✓ Vector index created")
                except Exception as e:
                    logger.warning(f"Could not create ivfflat index (may need more data): {e}")
                    logger.info("Will create index after more embeddings are generated")
            else:
                logger.info("Vector index already exists")
                
    except Exception as e:
        logger.error(f"Error ensuring embedding column: {e}")
        raise


def generate_embeddings(
    jsonl_path: Path,
    batch_size: int = 128,
    update_batch_size: int = 1000
):
    """
    Generate embeddings for concepts and store in PostgreSQL.
    
    Args:
        jsonl_path: Path to embeddable_text.jsonl file
        batch_size: Number of texts to encode at once (for sentence-transformers)
        update_batch_size: Number of embeddings to update in DB before committing
    """
    if not jsonl_path.exists():
        logger.error(f"Embeddable text file not found: {jsonl_path}")
        return
    
    # Initialize database connection
    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    
    # Ensure embedding column exists
    ensure_embedding_column(engine)
    
    # Load embedding model
    logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
    model = SentenceTransformer(settings.EMBEDDING_MODEL)
    logger.info(f"✓ Model loaded (dimension: {model.get_sentence_embedding_dimension()})")
    
    # Verify dimension matches config
    if model.get_sentence_embedding_dimension() != settings.VECTOR_DIMENSION:
        logger.warning(
            f"Model dimension ({model.get_sentence_embedding_dimension()}) "
            f"does not match config ({settings.VECTOR_DIMENSION}). "
            f"Using model dimension."
        )
    
    # Count total lines for progress
    logger.info("Counting records...")
    total_lines = sum(1 for _ in open(jsonl_path, 'r'))
    logger.info(f"Found {total_lines:,} records to process")
    
    processed_count = 0
    updated_count = 0
    skipped_count = 0
    error_count = 0
    
    # Batch processing
    batch_texts = []
    batch_cuis = []
    update_batch = []
    
    logger.info("=" * 60)
    logger.info("Generating embeddings...")
    
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(tqdm(f, total=total_lines, desc="Processing"), 1):
            try:
                data = json.loads(line.strip())
                cui = data['cui']
                text_content = data['text']
                
                # Skip if text is empty
                if not text_content or not text_content.strip():
                    skipped_count += 1
                    continue
                
                batch_texts.append(text_content)
                batch_cuis.append(cui)
                
                # Generate embeddings when batch is full
                if len(batch_texts) >= batch_size:
                    embeddings = model.encode(
                        batch_texts,
                        show_progress_bar=False,
                        batch_size=batch_size,
                        convert_to_numpy=True
                    )
                    
                    # Add to update batch
                    for cui_item, embedding in zip(batch_cuis, embeddings):
                        update_batch.append((cui_item, embedding))
                    
                    # Update database when update batch is full
                    if len(update_batch) >= update_batch_size:
                        updated = _update_embeddings_in_db(engine, update_batch)
                        updated_count += updated
                        update_batch = []
                    
                    batch_texts = []
                    batch_cuis = []
                
                processed_count += 1
                
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error at line {line_num}: {e}")
                error_count += 1
                continue
            except Exception as e:
                logger.error(f"Error processing line {line_num}: {e}")
                error_count += 1
                continue
        
        # Process remaining batch
        if batch_texts:
            embeddings = model.encode(
                batch_texts,
                show_progress_bar=False,
                batch_size=batch_size,
                convert_to_numpy=True
            )
            
            for cui_item, embedding in zip(batch_cuis, embeddings):
                update_batch.append((cui_item, embedding))
        
        # Update remaining embeddings
        if update_batch:
            updated = _update_embeddings_in_db(engine, update_batch)
            updated_count += updated
    
    # Create index if it doesn't exist (after we have some data)
    logger.info("Creating vector index (if needed)...")
    try:
        with engine.begin() as conn:  # Use begin() for automatic transaction management
            result = conn.execute(text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename='umls_concepts' AND indexname='idx_umls_concepts_embedding'
            """))
            
            if result.fetchone() is None:
                # Check if we have enough data for ivfflat (needs at least 1000 rows)
                result = conn.execute(text("""
                    SELECT COUNT(*) FROM umls_concepts WHERE embedding IS NOT NULL
                """))
                count = result.scalar()
                
                if count >= 1000:
                    conn.execute(text("""
                        CREATE INDEX idx_umls_concepts_embedding 
                        ON umls_concepts 
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100)
                    """))
                    logger.info("✓ Vector index created")
                else:
                    logger.info(f"Not enough embeddings ({count}) for ivfflat index. Will create later.")
    except Exception as e:
        logger.warning(f"Could not create index: {e}")
    
    logger.info("=" * 60)
    logger.info("Embedding generation complete!")
    logger.info(f"Processed: {processed_count:,} records")
    logger.info(f"Updated: {updated_count:,} embeddings")
    logger.info(f"Skipped: {skipped_count:,} (empty text)")
    if error_count > 0:
        logger.warning(f"Errors: {error_count:,}")


def _update_embeddings_in_db(engine, update_batch):
    """
    Update embeddings in database for a batch of CUIs.
    
    Args:
        engine: SQLAlchemy engine
        update_batch: List of (cui, embedding) tuples
    
    Returns:
        Number of successfully updated records
    """
    updated = 0
    try:
        with engine.begin() as conn:  # Use begin() for automatic transaction management
            for cui, embedding in update_batch:
                try:
                    # Convert numpy array to list and format for pgvector
                    embedding_list = embedding.tolist()
                    embedding_str = '[' + ','.join(map(str, embedding_list)) + ']'
                    
                    # Update the embedding for this CUI
                    result = conn.execute(
                        text("""
                            UPDATE umls_concepts 
                            SET embedding = :embedding::vector
                            WHERE cui = :cui
                        """),
                        {"embedding": embedding_str, "cui": cui}
                    )
                    
                    if result.rowcount > 0:
                        updated += 1
                    
                except Exception as e:
                    logger.debug(f"Error updating embedding for {cui}: {e}")
                    continue
            
    except Exception as e:
        logger.error(f"Error in batch update: {e}")
        # Transaction will be rolled back automatically by begin() context manager
    
    return updated


def verify_embeddings(engine, sample_size: int = 10):
    """Verify that embeddings were generated correctly"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"""
                SELECT cui, preferred_name, 
                       array_length(embedding::text::float[], 1) as dim
                FROM umls_concepts 
                WHERE embedding IS NOT NULL 
                LIMIT {sample_size}
            """))
            
            rows = result.fetchall()
            if rows:
                logger.info(f"\nSample embeddings (first {len(rows)}):")
                for row in rows:
                    logger.info(f"  {row[0]}: {row[1][:50]}... (dim: {row[2]})")
                
                # Count total
                result = conn.execute(text("""
                    SELECT COUNT(*) FROM umls_concepts WHERE embedding IS NOT NULL
                """))
                total = result.scalar()
                logger.info(f"\nTotal concepts with embeddings: {total:,}")
                return True
            else:
                logger.warning("No embeddings found in database")
                return False
                
    except Exception as e:
        logger.error(f"Error verifying embeddings: {e}")
        return False


def main():
    """Main function to generate embeddings"""
    logger.info("Starting embedding generation process")
    logger.info("=" * 60)
    
    # Set up paths
    data_dir = Path("data/umls/processed")
    embeddable_file = data_dir / "embeddable_text.jsonl"
    
    if not embeddable_file.exists():
        logger.error(f"Embeddable text file not found: {embeddable_file}")
        return
    
    # Generate embeddings
    generate_embeddings(
        embeddable_file,
        batch_size=128,  # Encoding batch size
        update_batch_size=1000  # DB update batch size
    )
    
    # Verify embeddings
    logger.info("=" * 60)
    logger.info("Verifying embeddings...")
    engine = create_engine(settings.DATABASE_URL)
    verify_embeddings(engine)
    
    logger.info("=" * 60)
    logger.info("Embedding generation process completed!")


if __name__ == "__main__":
    main()

