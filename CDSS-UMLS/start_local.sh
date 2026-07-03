#!/bin/bash

# Start UMLS CDSS locally without Docker
# This script starts all required services and the API

set -e

echo "🚀 Starting UMLS CDSS (Local Mode)"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if Poetry is installed
if ! command -v poetry &> /dev/null; then
    echo -e "${RED}❌ Poetry is not installed. Please install it first:${NC}"
    echo "curl -sSL https://install.python-poetry.org | python3 -"
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found. Creating template...${NC}"
    cat > .env << 'EOF'
# Database Configuration
DATABASE_URL=postgresql://umls_user:umls_password@localhost:5432/umls_cdss

# Redis Configuration
REDIS_URL=redis://localhost:6379/0
CACHE_TTL=3600

# Qdrant Configuration
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_NAME=umls_concepts

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4

# UMLS Configuration
UMLS_API_KEY=your_umls_api_key_here
UMLS_API_URL=https://uts-ws.nlm.nih.gov/rest

# RAG Configuration
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
VECTOR_DIMENSION=384
TOP_K_RESULTS=5

# CORS Configuration
CORS_ORIGINS=["http://localhost:3000","http://localhost:8000"]

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/cdss.log
EOF
    echo -e "${YELLOW}⚠️  Please edit .env file and add your API keys${NC}"
fi

# Create logs directory
mkdir -p logs

# Check and start PostgreSQL (macOS with Homebrew)
if command -v brew &> /dev/null; then
    if brew services list | grep -q "postgresql@16.*started"; then
        echo -e "${GREEN}✓ PostgreSQL is running${NC}"
    else
        echo -e "${YELLOW}⚠️  Starting PostgreSQL...${NC}"
        brew services start postgresql@16 2>/dev/null || echo -e "${YELLOW}⚠️  Could not start PostgreSQL. Please start it manually.${NC}"
        sleep 2
    fi
fi

# Check and start Redis (macOS with Homebrew)
if command -v brew &> /dev/null; then
    if brew services list | grep -q "redis.*started"; then
        echo -e "${GREEN}✓ Redis is running${NC}"
    else
        echo -e "${YELLOW}⚠️  Starting Redis...${NC}"
        brew services start redis 2>/dev/null || echo -e "${YELLOW}⚠️  Could not start Redis. Please start it manually.${NC}"
        sleep 1
    fi
fi

# Check Redis connection
if command -v redis-cli &> /dev/null; then
    if redis-cli ping &> /dev/null; then
        echo -e "${GREEN}✓ Redis is accessible${NC}"
    else
        echo -e "${RED}❌ Redis is not responding. Please start Redis manually.${NC}"
    fi
fi

# Check and start Qdrant (using Docker as fallback)
if docker ps | grep -q qdrant; then
    echo -e "${GREEN}✓ Qdrant is running${NC}"
elif docker ps -a | grep -q qdrant; then
    echo -e "${YELLOW}⚠️  Starting existing Qdrant container...${NC}"
    docker start qdrant
    sleep 2
else
    echo -e "${YELLOW}⚠️  Starting Qdrant in Docker...${NC}"
    docker run -d --name qdrant -p 6333:6333 -p 6334:6334 qdrant/qdrant:latest 2>/dev/null || echo -e "${YELLOW}⚠️  Could not start Qdrant. Please start it manually.${NC}"
    sleep 2
fi

# Check Qdrant health
if curl -s http://localhost:6333/health > /dev/null; then
    echo -e "${GREEN}✓ Qdrant is accessible${NC}"
else
    echo -e "${RED}❌ Qdrant is not responding. Please check if it's running.${NC}"
fi

# Check if dependencies are installed
if [ ! -d ".venv" ] && [ ! -d "$(poetry env info -p 2>/dev/null)" ]; then
    echo -e "${YELLOW}⚠️  Installing dependencies...${NC}"
    poetry install
fi

echo ""
echo -e "${GREEN}✓ All services are ready!${NC}"
echo ""
echo "Starting API server..."
echo "API will be available at: http://localhost:8000"
echo "API docs will be available at: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run the API
poetry run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
