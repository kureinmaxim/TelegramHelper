#!/bin/bash
# disable_bot.sh - Disables Telegram bot for security
# Restore: ./scripts/change_token.sh
#
# Usage: ./scripts/disable_bot.sh
#
# What it does:
# 1. Backs up current BOT_TOKEN
# 2. Removes BOT_TOKEN from .env
# 3. Restarts container (bot stops, API keeps working)
# 4. Creates .bot_disabled marker

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}🔒 Disable Telegram Bot${NC}"
echo "This will stop the Telegram bot for security."
echo "API endpoints will remain active."
echo ""
echo "To restore, run: ./scripts/change_token.sh"
echo ""

# Check if already disabled
if [ -f ".bot_disabled" ]; then
    echo -e "${YELLOW}Bot is already disabled.${NC}"
    echo "To restore, run: ./scripts/change_token.sh"
    exit 0
fi

# Confirm
read -p "Disable the bot? [y/N]: " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# Backup current token
if [ -f ".env" ]; then
    CURRENT_TOKEN=$(grep "^BOT_TOKEN=" .env | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    if [ -n "$CURRENT_TOKEN" ]; then
        echo "$CURRENT_TOKEN" > .bot_token_backup
        chmod 600 .bot_token_backup
        echo -e "${GREEN}✅ Token backed up to .bot_token_backup${NC}"
    fi
fi

# Remove BOT_TOKEN from .env
if [ -f ".env" ]; then
    # Comment out BOT_TOKEN instead of removing
    sed -i.bak 's/^BOT_TOKEN=/#DISABLED_BOT_TOKEN=/' .env
    echo -e "${GREEN}✅ BOT_TOKEN disabled in .env${NC}"
fi

# Create marker file
echo "Disabled at: $(date)" > .bot_disabled
chmod 600 .bot_disabled

# Restart container
echo "Restarting container..."
if command -v docker &> /dev/null; then
    docker compose down 2>/dev/null || true
    docker compose up -d --build
    echo -e "${GREEN}✅ Container restarted${NC}"
else
    echo -e "${YELLOW}Docker not found. Please restart manually.${NC}"
fi

echo ""
echo -e "${GREEN}🔒 Bot disabled successfully!${NC}"
echo ""
echo "API is still accessible at http://localhost:8000"
echo "Telegram bot is no longer responding."
echo ""
echo "To restore:"
echo "  ./scripts/change_token.sh"
