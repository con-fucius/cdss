"""
Clear Qdrant collection for UMLS embeddings

This script deletes all embeddings from the Qdrant collection.
Useful when you need to regenerate embeddings with different settings.
"""
import sys
from api.config import settings
from qdrant_client import QdrantClient
import logging

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


def clear_qdrant_collection(collection_name: str = None, delete_collection: bool = False):
    """
    Clear all points from a Qdrant collection.
    
    Args:
        collection_name: Name of collection to clear (defaults to settings.QDRANT_COLLECTION_NAME)
        delete_collection: If True, delete the entire collection. If False, just clear all points.
    """
    if collection_name is None:
        collection_name = settings.QDRANT_COLLECTION_NAME
    
    client = init_qdrant_client()
    
    try:
        # Check if collection exists
        collections = client.get_collections()
        collection_exists = any(c.name == collection_name for c in collections.collections)
        
        if not collection_exists:
            logger.warning(f"Collection '{collection_name}' does not exist. Nothing to clear.")
            return
        
        # Get collection info
        collection_info = client.get_collection(collection_name)
        points_count = collection_info.points_count
        
        logger.info("=" * 60)
        logger.info("Clearing Qdrant Collection")
        logger.info("=" * 60)
        logger.info(f"Collection: {collection_name}")
        logger.info(f"Current points: {points_count:,}")
        
        if points_count == 0:
            logger.info("✓ Collection is already empty. Nothing to clear.")
            return
        
        if delete_collection:
            logger.info(f"\n⚠️  About to DELETE collection '{collection_name}' ({points_count:,} points)")
            logger.info("This will remove the collection entirely.")
            client.delete_collection(collection_name)
            logger.info(f"✓ Collection '{collection_name}' deleted")
        else:
            logger.info(f"\n⚠️  About to DELETE all {points_count:,} points from '{collection_name}'")
            logger.info("The collection will remain but will be empty.")
            
            # Delete all points using scroll and delete in batches
            from qdrant_client.models import PointIdsList
            deleted_count = 0
            batch_size = 10000
            
            while True:
                # Scroll to get point IDs
                scroll_result = client.scroll(
                    collection_name=collection_name,
                    limit=batch_size,
                    with_payload=False,
                    with_vectors=False
                )
                points, next_offset = scroll_result
                
                if not points:
                    break
                
                # Extract point IDs
                point_ids = [point.id for point in points]
                
                # Delete batch
                client.delete(
                    collection_name=collection_name,
                    points_selector=PointIdsList(points=point_ids)
                )
                deleted_count += len(point_ids)
                logger.info(f"  Deleted {deleted_count:,} / {points_count:,} points...")
                
                if next_offset is None:
                    break
            
            logger.info(f"✓ Deleted all {deleted_count:,} points from '{collection_name}'")
            
            # Verify deletion
            collection_info = client.get_collection(collection_name)
            remaining = collection_info.points_count
            if remaining == 0:
                logger.info(f"✓ Verified: Collection is now empty (0 points)")
            else:
                logger.warning(f"⚠ Warning: {remaining:,} points still remain.")
        
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Failed to clear Qdrant collection: {e}")
        raise


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Clear Qdrant embedding collection")
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help=f"Collection name to clear (default: {settings.QDRANT_COLLECTION_NAME})"
    )
    parser.add_argument(
        "--delete-collection",
        action="store_true",
        help="Delete the entire collection instead of just clearing points"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt"
    )
    
    args = parser.parse_args()
    
    collection_name = args.collection or settings.QDRANT_COLLECTION_NAME
    
    try:
        if not args.yes:
            print(f"\n⚠️  WARNING: This will clear all embeddings from '{collection_name}'")
            if args.delete_collection:
                print("   The collection will be DELETED entirely.")
            else:
                print("   All points will be deleted, but the collection will remain.")
            response = input("\nAre you sure you want to continue? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("Cancelled.")
                sys.exit(0)
        
        clear_qdrant_collection(collection_name, args.delete_collection)
        
        logger.info("\n" + "=" * 60)
        logger.info("Next Steps:")
        logger.info("=" * 60)
        logger.info("1. Regenerate embeddings: poetry run python etl/generate_embeddings_qdrant.py")
        logger.info("=" * 60)
        
    except KeyboardInterrupt:
        logger.info("\nCancelled by user.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to clear Qdrant collection: {e}")
        sys.exit(1)

