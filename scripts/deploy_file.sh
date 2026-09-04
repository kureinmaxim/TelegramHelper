#!/bin/bash
# Deploy single file to VPS server (Docker version)
# Usage: ./deploy_file.sh <filename>
# Example: ./deploy_file.sh api.py
#
# ⚠️  IMPORTANT: Server runs in Docker!
# This script uses 'docker compose restart' (safe, no rebuild)
# For full rebuild use: docker compose down && docker compose up -d --build

set -e

# Server configuration (from DEPLOY.md)
SERVER="root@YOUR_SERVER_IP"
PORT="YOUR_SSH_PORT"
REMOTE_PATH="/opt/TelegramHelper"

# Get script directory (where TelegramSimple is)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Check argument
if [ -z "$1" ]; then
    echo "Usage: $0 <filename>"
    echo "Example: $0 api.py"
    echo ""
    echo "This will copy the file and restart Docker container."
    exit 1
fi

FILE="$1"
LOCAL_PATH="$SCRIPT_DIR/$FILE"

# Check file exists
if [ ! -f "$LOCAL_PATH" ]; then
    echo "❌ File not found: $LOCAL_PATH"
    exit 1
fi

echo "🚀 Deploying $FILE to VPS (Docker)..."
echo "   From: $LOCAL_PATH"
echo "   To:   $SERVER:$REMOTE_PATH/"
echo ""

# Copy file
echo "📤 Copying $FILE..."
scp -P $PORT "$LOCAL_PATH" "$SERVER:$REMOTE_PATH/"

echo "✅ File copied!"
echo ""

# Ask if need to restart
echo "Options:"
echo "  [r] docker compose restart (quick, no rebuild)"
echo "  [b] docker build --no-cache (full rebuild)"
echo "  [n] No restart"
echo ""

# Loop until valid input
while true; do
    read -p "Choose action [r/b/n]: " REPLY
    
    # Normalize input (lowercase, remove spaces)
    REPLY=$(echo "$REPLY" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
    
    if [[ "$REPLY" == "r" ]]; then
        echo ""
        echo "♻️  Quick restart (docker compose restart)..."
        ssh -p $PORT $SERVER "cd $REMOTE_PATH && docker compose restart"
        echo "✅ Container restarted!"
        break
        
    elif [[ "$REPLY" == "b" ]]; then
        echo ""
        echo "🔨 Full rebuild WITHOUT cache (like change_token.sh)..."
        ssh -p $PORT $SERVER "cd $REMOTE_PATH && docker compose down && docker build --no-cache -t telegram-helper-lite:latest . && docker compose up -d"
        echo "✅ Container rebuilt (no cache) and started!"
        break
        
    elif [[ "$REPLY" == "n" || -z "$REPLY" ]]; then
        echo ""
        echo "ℹ️  File copied, no restart. Restart manually if needed:"
        echo "   ssh -p $PORT $SERVER 'cd $REMOTE_PATH && docker compose restart'"
        break
        
    else
        echo "❌ Invalid input '$REPLY'. Please enter r, b, or n."
    fi
done

echo ""
echo "📋 View logs:"
echo "   ssh -p $PORT $SERVER 'docker logs telegram-helper-lite --tail 20'"
echo ""
echo "🧪 Test: curl http://YOUR_SERVER_IP:8000/health"
