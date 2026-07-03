#!/bin/bash

# Quick script to start Qdrant for UMLS CDSS

echo "🚀 Starting Qdrant..."

# Check if Qdrant container already exists
if docker ps -a | grep -q "qdrant"; then
    echo "Found existing Qdrant container, starting it..."
    docker start qdrant
else
    echo "Creating new Qdrant container..."
    docker run -d \
        --name qdrant \
        -p 6333:6333 \
        -p 6334:6334 \
        -v $(pwd)/qdrant_storage:/qdrant/storage \
        qdrant/qdrant:latest
fi

# Wait a moment for Qdrant to start
sleep 3

# Check if it's running
if curl -s http://localhost:6333/health > /dev/null; then
    echo "✅ Qdrant is running and accessible at http://localhost:6333"
    echo ""
    echo "Collection info:"
    curl -s http://localhost:6333/collections | python3 -m json.tool 2>/dev/null || curl -s http://localhost:6333/collections
else
    echo "❌ Qdrant failed to start. Check Docker logs:"
    echo "   docker logs qdrant"
fi
