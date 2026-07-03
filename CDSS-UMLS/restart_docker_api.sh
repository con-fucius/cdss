#!/bin/bash

# Script to restart the Docker API with updated environment variables

echo "🔄 Restarting Docker API with updated .env file..."
echo ""

cd infrastructure/docker

# Stop the API container
echo "Stopping API container..."
docker-compose stop api

# Remove the API container (to force recreation with new env vars)
echo "Removing API container..."
docker-compose rm -f api

# Start the API container (will read .env from project root)
echo "Starting API container with updated environment..."
docker-compose up -d api

# Wait a moment for it to start
sleep 3

# Show logs
echo ""
echo "API logs (last 20 lines):"
docker-compose logs --tail 20 api

echo ""
echo "✅ API container restarted"
echo "Check if OPENAI_API_KEY is loaded:"
docker-compose exec -T api env | grep OPENAI_API_KEY | sed 's/=.*/=***HIDDEN***/' || echo "⚠️  OPENAI_API_KEY not found in container"

echo ""
echo "Test the API:"
echo "curl http://localhost:8000/health/"
