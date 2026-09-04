#!/bin/bash
# restore_bot.sh - Quick non-interactive bot restore
# Restores BOT_TOKEN from backup and restarts container

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "🔄 Restoring Telegram Bot..."

# Check if already enabled
if ! grep -q "#DISABLED_BOT_TOKEN=" .env 2>/dev/null && grep -q "BOT_TOKEN=" .env 2>/dev/null; then
    echo "ℹ️ Bot is already enabled"
    exit 0
fi

# Restore token - uncomment the disabled line
if grep -q "#DISABLED_BOT_TOKEN=" .env 2>/dev/null; then
    sed -i.bak 's/#DISABLED_BOT_TOKEN=/BOT_TOKEN=/' .env
    echo "✅ BOT_TOKEN restored (uncommented)"
elif [ -f ".bot_token_backup" ]; then
    BACKUP_TOKEN=$(cat .bot_token_backup)
    if [ -n "$BACKUP_TOKEN" ]; then
        echo "BOT_TOKEN=$BACKUP_TOKEN" >> .env
        echo "✅ BOT_TOKEN restored from backup file"
    fi
else
    echo "❌ No token to restore! Use ./scripts/change_token.sh to set new token"
    exit 1
fi

# Remove disabled marker
rm -f .bot_disabled 2>/dev/null || true

# Restart container WITHOUT rebuild to preserve .env
echo "🔄 Restarting container..."
docker compose restart

echo ""
echo "✅ Bot restored and running!"
echo ""
