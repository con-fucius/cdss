#!/bin/bash

# Script to restart the API server with updated environment variables

echo "🔄 Restarting API Server..."
echo ""

# Check if API is running
API_PID=$(ps aux | grep -E "uvicorn.*main:app" | grep -v grep | awk '{print $2}')

if [ ! -z "$API_PID" ]; then
    echo "Found running API process (PID: $API_PID)"
    echo "Stopping it..."
    kill $API_PID
    sleep 2
    
    # Check if it's still running
    if ps -p $API_PID > /dev/null 2>&1; then
        echo "Force killing..."
        kill -9 $API_PID
        sleep 1
    fi
    echo "✅ API stopped"
else
    echo "No running API process found"
fi

echo ""
echo "Starting API server with updated environment..."
echo "API will be available at: http://localhost:8000"
echo "API docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Start the API
poetry run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
