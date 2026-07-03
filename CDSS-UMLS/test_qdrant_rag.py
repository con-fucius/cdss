#!/usr/bin/env python3
"""Test Qdrant RAG service"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from api.services.rag.rag_qdrant import RAGServiceQdrant
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test():
    try:
        logger.info("Initializing RAG service...")
        rag = RAGServiceQdrant()
        
        logger.info("Testing retrieval...")
        results, cuis = await rag.retrieve('warfarin aspirin drug interaction', top_k=5)
        
        logger.info(f"Results: {len(results)}")
        logger.info(f"CUIs: {len(cuis)}")
        
        if results:
            logger.info(f"First result: {results[0]}")
        else:
            logger.error("No results returned!")
            
        if cuis:
            logger.info(f"First CUI: {cuis[0]}")
        else:
            logger.error("No CUIs returned!")
            
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(test())

