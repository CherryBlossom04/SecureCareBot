#!/bin/bash

# SecureCareBot Startup Script
# This script starts all required services: MongoDB, Auth Server, API Server, and Frontend

set -e  # Exit on error

PROJECT_DIR="/home/sowmya/Documents/main-project"
BACKEND_DIR="$PROJECT_DIR/secure_carebot_v1"
FRONTEND_DIR="$PROJECT_DIR/frontend_v1"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== SecureCareBot Startup ===${NC}"

# 1. Check and start MongoDB
echo -e "\n${YELLOW}[1/4] Checking MongoDB...${NC}"
if ! pgrep -x "mongod" > /dev/null; then
    echo "Starting MongoDB..."
    sudo systemctl start mongod
    sleep 2
else
    echo "✓ MongoDB already running"
fi

# Verify MongoDB is accessible
if mongosh --eval "db.version()" --quiet > /dev/null 2>&1; then
    echo "✓ MongoDB is accessible"
else
    echo -e "${RED}✗ MongoDB connection failed${NC}"
    exit 1
fi

# 2. Start Auth Server (Port 8001)
echo -e "\n${YELLOW}[2/4] Starting Auth Server (Port 8001)...${NC}"
cd "$BACKEND_DIR"

# Kill existing auth server
pkill -f "uvicorn auth:app" 2>/dev/null || true
sleep 1

# Start auth server in background
source venv/bin/activate
nohup uvicorn auth:app --port 8001 --host 0.0.0.0 > logs/auth.log 2>&1 &
AUTH_PID=$!
echo "✓ Auth server started (PID: $AUTH_PID)"
echo "  Logs: $BACKEND_DIR/logs/auth.log"
sleep 2

# 3. Start API Server (Port 8000)
echo -e "\n${YELLOW}[3/4] Starting API Server (Port 8000)...${NC}"

# Kill existing API server
pkill -f "uvicorn api:app" 2>/dev/null || true
sleep 1

# Start API server in background
nohup uvicorn api:app --port 8000 --host 0.0.0.0 > logs/api.log 2>&1 &
API_PID=$!
echo "✓ API server started (PID: $API_PID)"
echo "  Logs: $BACKEND_DIR/logs/api.log"
deactivate
sleep 2

# 4. Start Frontend (Port 8080)
echo -e "\n${YELLOW}[4/4] Starting Frontend (Port 8080)...${NC}"
cd "$FRONTEND_DIR"

# Kill existing frontend server
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
sleep 1

# Start frontend in background
nohup npm run dev > ../secure_carebot_v1/logs/frontend.log 2>&1 &
FRONTEND_PID=$!
echo "✓ Frontend started (PID: $FRONTEND_PID)"
echo "  Logs: $BACKEND_DIR/logs/frontend.log"
sleep 3

# Summary
echo -e "\n${GREEN}=== All Services Started ===${NC}"
echo -e "Auth API:   http://127.0.0.1:8001"
echo -e "Main API:   http://127.0.0.1:8000"
echo -e "Frontend:   http://localhost:8080"
echo ""
echo -e "Showing live logs (Ctrl+C to exit, services will keep running)..."
echo -e "To stop all services: ${YELLOW}./stop.sh${NC}"
echo ""
sleep 2

# Tail all logs in the terminal
tail -f "$BACKEND_DIR/logs/auth.log" "$BACKEND_DIR/logs/api.log" "$BACKEND_DIR/logs/frontend.log"
