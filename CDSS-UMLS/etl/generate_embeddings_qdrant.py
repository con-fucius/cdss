"""
Generate embeddings for UMLS concepts and store in Qdrant vector database

This script reads embeddable_text.jsonl, generates embeddings using sentence-transformers,
and stores them in Qdrant for fast similarity search.
"""
import json
from pathlib import Path
from api.config import settings
from sentence_transformers import SentenceTransformer
import logging
from tqdm import tqdm
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from typing import List, Dict, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def init_qdrant_client() -> QdrantClient:
    """Initialize Qdrant client"""
    try:
        if settings.QDRANT_API_KEY:
            client = QdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY
            )
        else:
            client = QdrantClient(url=settings.QDRANT_URL)
        
        # Test connection
        collections = client.get_collections()
        logger.info(f"✓ Connected to Qdrant at {settings.QDRANT_URL}")
        logger.info(f"  Existing collections: {[c.name for c in collections.collections]}")
        return client
    except Exception as e:
        logger.error(f"Failed to connect to Qdrant: {e}")
        raise


def ensure_collection(client: QdrantClient, collection_name: str, vector_size: int):
    """Ensure Qdrant collection exists, create if it doesn't"""
    try:
        # Check if collection exists
        collections = client.get_collections()
        collection_exists = any(c.name == collection_name for c in collections.collections)
        
        if not collection_exists:
            logger.info(f"Creating collection '{collection_name}' with vector size {vector_size}...")
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=Distance.COSINE
                )
            )
            logger.info(f"✓ Collection '{collection_name}' created")
        else:
            logger.info(f"Collection '{collection_name}' already exists")
            
            # Verify vector size matches
            collection_info = client.get_collection(collection_name)
            existing_size = collection_info.config.params.vectors.size
            if existing_size != vector_size:
                logger.warning(
                    f"Collection vector size ({existing_size}) doesn't match "
                    f"expected size ({vector_size}). Consider recreating the collection."
                )
    except Exception as e:
        logger.error(f"Error ensuring collection: {e}")
        raise


def generate_embeddings_to_qdrant(
    jsonl_path: Path,
    batch_size: int = 128,
    upload_batch_size: int = 1000
):
    """
    Generate embeddings for concepts and store in Qdrant.
    
    Args:
        jsonl_path: Path to embeddable_text.jsonl file
        batch_size: Number of texts to encode at once (for sentence-transformers)
        upload_batch_size: Number of points to upload to Qdrant before committing
    """
    if not jsonl_path.exists():
        logger.error(f"Embeddable text file not found: {jsonl_path}")
        return
    
    # Initialize Qdrant client
    client = init_qdrant_client()
    
    # Ensure collection exists
    ensure_collection(client, settings.QDRANT_COLLECTION_NAME, settings.VECTOR_DIMENSION)
    
    # Load embedding model
    logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
    model = SentenceTransformer(settings.EMBEDDING_MODEL)
    model_dimension = model.get_sentence_embedding_dimension()
    logger.info(f"✓ Model loaded (dimension: {model_dimension})")
    
    # Verify dimension matches config
    if model_dimension != settings.VECTOR_DIMENSION:
        logger.warning(
            f"Model dimension ({model_dimension}) "
            f"does not match config ({settings.VECTOR_DIMENSION}). "
            f"Using model dimension."
        )
        vector_dimension = model_dimension
    else:
        vector_dimension = settings.VECTOR_DIMENSION
    
    # Count total lines for progress
    logger.info("Counting records...")
    total_lines = sum(1 for _ in open(jsonl_path, 'r'))
    logger.info(f"Found {total_lines:,} records to process")
    
    # Get existing collection size to resume from (for auto-increment IDs)
    try:
        collection_info = client.get_collection(settings.QDRANT_COLLECTION_NAME)
        next_id = collection_info.points_count
        logger.info(f"Resuming from ID {next_id:,} (collection has {next_id:,} existing points)")
    except Exception:
        next_id = 0
        logger.info("Starting with ID 0 (new collection)")
    
    # Map CUI -> ID for stable identity and upsert support
    cui_to_id: Dict[str, int] = {}
    
    # Load existing CUI->ID mappings from collection (for upsert support)
    if next_id > 0:
        logger.info("Loading existing CUI->ID mappings from collection...")
        try:
            # Scroll through all existing points to build CUI->ID mapping
            # This ensures we can upsert existing CUIs correctly
            offset = None
            loaded_count = 0
            while True:
                scroll_result = client.scroll(
                    collection_name=settings.QDRANT_COLLECTION_NAME,
                    limit=10000,  # Load in batches of 10k
                    offset=offset,
                    with_payload=True,
                    with_vectors=False
                )
                points, next_offset = scroll_result
                
                for point in points:
                    if 'cui' in point.payload:
                        cui_to_id[point.payload['cui']] = point.id
                        loaded_count += 1
                
                if next_offset is None:
                    break
                offset = next_offset
            
            logger.info(f"Loaded {len(cui_to_id):,} existing CUI->ID mappings from {loaded_count:,} points")
        except Exception as e:
            logger.warning(f"Could not load existing mappings: {e}. Will assign new IDs (may cause duplicates if CUIs already exist).")
    
    processed_count = 0
    uploaded_count = 0
    skipped_count = 0
    error_count = 0
    
    # Batch processing
    batch_texts = []
    batch_data = []  # Store (cui, text) tuples
    upload_batch = []  # Points to upload to Qdrant
    
    logger.info("=" * 60)
    logger.info("Generating embeddings and uploading to Qdrant...")
    logger.info("=" * 60)
    
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
                batch_data.append((cui, text_content, data))
                
                # Generate embeddings when batch is full
                if len(batch_texts) >= batch_size:
                    embeddings = model.encode(
                        batch_texts,
                        show_progress_bar=False,
                        batch_size=batch_size,
                        convert_to_numpy=True
                    )
                    
                    # Prepare points for Qdrant
                    for (cui, text, full_data), embedding in zip(batch_data, embeddings):
                        # Get or assign auto-increment ID for this CUI
                        if cui not in cui_to_id:
                            cui_to_id[cui] = next_id
                            next_id += 1
                        point_id = cui_to_id[cui]
                        
                        point = PointStruct(
                            id=point_id,  # Auto-increment integer ID
                            vector=embedding.tolist(),
                            payload={
                                "cui": cui,  # Stable medical identity via CUI
                                "text": text,  # Embeddable text (without codes)
                                "preferred_name": full_data.get("preferred_name", ""),
                                "semantic_types": full_data.get("semantic_types", []),
                                "synonyms": full_data.get("synonyms", [])[:10],  # Limit synonyms
                                "codes": full_data.get("codes", []),  # Codes stored as metadata (not embedded)
                            }
                        )
                        upload_batch.append(point)
                    
                    # Upload to Qdrant when batch is full
                    if len(upload_batch) >= upload_batch_size:
                        try:
                            client.upsert(
                                collection_name=settings.QDRANT_COLLECTION_NAME,
                                points=upload_batch
                            )
                            uploaded_count += len(upload_batch)
                            logger.debug(f"Uploaded batch: {uploaded_count:,} points")
                        except Exception as e:
                            logger.error(f"Error uploading batch: {e}")
                            error_count += len(upload_batch)
                        
                        upload_batch = []
                    
                    # Reset batches
                    batch_texts = []
                    batch_data = []
                    processed_count += len(embeddings)
                
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
            
            for (cui, text, full_data), embedding in zip(batch_data, embeddings):
                # Get or assign auto-increment ID for this CUI
                if cui not in cui_to_id:
                    cui_to_id[cui] = next_id
                    next_id += 1
                point_id = cui_to_id[cui]
                
                point = PointStruct(
                    id=point_id,  # Auto-increment integer ID
                    vector=embedding.tolist(),
                    payload={
                        "cui": cui,  # Stable medical identity via CUI
                        "text": text,  # Embeddable text (without codes)
                        "preferred_name": full_data.get("preferred_name", ""),
                        "semantic_types": full_data.get("semantic_types", []),
                        "synonyms": full_data.get("synonyms", [])[:10],
                        "codes": full_data.get("codes", []),  # Codes stored as metadata (not embedded)
                    }
                )
                upload_batch.append(point)
            
            processed_count += len(embeddings)
        
        # Upload remaining points
        if upload_batch:
            try:
                client.upsert(
                    collection_name=settings.QDRANT_COLLECTION_NAME,
                    points=upload_batch
                )
                uploaded_count += len(upload_batch)
            except Exception as e:
                logger.error(f"Error uploading final batch: {e}")
                error_count += len(upload_batch)
    
    # Get final collection info
    collection_info = client.get_collection(settings.QDRANT_COLLECTION_NAME)
    
    logger.info("=" * 60)
    logger.info("Embedding generation and upload complete!")
    logger.info("=" * 60)
    logger.info(f"Processed: {processed_count:,} concepts")
    logger.info(f"Uploaded to Qdrant: {uploaded_count:,} points")
    logger.info(f"Skipped (empty text): {skipped_count:,}")
    if error_count > 0:
        logger.warning(f"Errors: {error_count:,}")
    
    logger.info(f"\nCollection '{settings.QDRANT_COLLECTION_NAME}' info:")
    logger.info(f"  Total points: {collection_info.points_count:,}")
    logger.info(f"  Vector size: {collection_info.config.params.vectors.size}")
    logger.info(f"  Distance metric: {collection_info.config.params.vectors.distance}")
    logger.info("=" * 60)


def verify_qdrant_setup(client: QdrantClient, collection_name: str, sample_size: int = 5):
    """Verify that embeddings were uploaded correctly"""
    try:
        logger.info(f"\nVerifying Qdrant collection '{collection_name}'...")
        
        # Get collection info
        collection_info = client.get_collection(collection_name)
        logger.info(f"✓ Collection exists: {collection_info.points_count:,} points")
        
        # Sample some points
        if collection_info.points_count > 0:
            scroll_result = client.scroll(
                collection_name=collection_name,
                limit=sample_size,
                with_payload=True,
                with_vectors=False
            )
            points = scroll_result[0] if scroll_result else None
            
            if points and len(points) > 0:
                logger.info(f"\nSample points (first {min(sample_size, len(points))}):")
                # Get vector dimension from collection config
                try:
                    vector_dim = collection_info.config.params.vectors.size
                except (AttributeError, TypeError):
                    vector_dim = "unknown"
                
                for point in points[:sample_size]:
                    payload = point.payload if hasattr(point, 'payload') else {}
                    logger.info(f"  CUI: {point.id}")
                    logger.info(f"    Preferred Name: {payload.get('preferred_name', 'N/A')}")
                    logger.info(f"    Semantic Types: {payload.get('semantic_types', [])[:3]}")
                    # Note: Vector not loaded since with_vectors=False
                    logger.info(f"    Vector: stored (dimension: {vector_dim})")
            else:
                logger.warning("  No points found in collection (collection may be empty)")
        
        logger.info("✓ Verification complete")
        
    except Exception as e:
        logger.error(f"Error verifying Qdrant setup: {e}")


if __name__ == "__main__":
    import sys
    
    embeddable_text_path = Path("data/umls/processed/embeddable_text.jsonl")
    
    if len(sys.argv) > 1:
        embeddable_text_path = Path(sys.argv[1])
    
    if not embeddable_text_path.exists():
        logger.error(f"File not found: {embeddable_text_path}")
        logger.info("Usage: python generate_embeddings_qdrant.py [path_to_embeddable_text.jsonl]")
        sys.exit(1)
    
    try:
        generate_embeddings_to_qdrant(embeddable_text_path)
        
        # Verify setup
        client = init_qdrant_client()
        verify_qdrant_setup(client, settings.QDRANT_COLLECTION_NAME)
        
    except Exception as e:
        logger.error(f"Failed to generate embeddings: {e}")
        sys.exit(1)

