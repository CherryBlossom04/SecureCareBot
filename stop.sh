#!/bin/bash

# SecureCareBot Stop Script
# Stops all running services

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}=== Stopping SecureCareBot Services ===${NC}"

# Stop Auth Server
echo "Stopping Auth Server..."
pkill -f "uvicorn auth:app" && echo "✓ Auth server stopped" || echo "  (not running)"

# Stop API Server
echo "Stopping API Server..."
pkill -f "uvicorn api:app" && echo "✓ API server stopped" || echo "  (not running)"

# Stop Frontend
echo "Stopping Frontend..."
lsof -ti:8080 | xargs kill -9 2>/dev/null && echo "✓ Frontend stopped" || echo "  (not running)"

echo -e "\n${GREEN}All services stopped${NC}"
