#!/bin/bash
# ============================================================
# API Key Manager for TelegramSimple
# Manage the API key for ApiAi integration
# ============================================================

set -e

# Colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"

# Generate a key
generate_key() {
    python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
    openssl rand -hex 32 2>/dev/null || \
    head -c 32 /dev/urandom | xxd -p
}

# Read the current key
get_current_key() {
    if [[ -f "$ENV_FILE" ]]; then
        grep "^API_SECRET_KEY=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d "'"
    fi
}

# Write the key
set_key() {
    local key="$1"

    if [[ ! -f "$ENV_FILE" ]]; then
        echo -e "${YELLOW}⚠️  .env not found, creating from example.env...${NC}"
        if [[ -f "$PROJECT_DIR/example.env" ]]; then
            cp "$PROJECT_DIR/example.env" "$ENV_FILE"
        else
            touch "$ENV_FILE"
        fi
    fi

    # Update existing key or append
    if grep -q "^API_SECRET_KEY=" "$ENV_FILE" 2>/dev/null; then
        # macOS/Linux sed compatibility
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^API_SECRET_KEY=.*|API_SECRET_KEY=$key|" "$ENV_FILE"
        else
            sed -i "s|^API_SECRET_KEY=.*|API_SECRET_KEY=$key|" "$ENV_FILE"
        fi
    else
        echo "API_SECRET_KEY=$key" >> "$ENV_FILE"
    fi
}

# Write the URL
set_url() {
    local url="$1"

    if grep -q "^API_URL=" "$ENV_FILE" 2>/dev/null; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^API_URL=.*|API_URL=$url|" "$ENV_FILE"
        else
            sed -i "s|^API_URL=.*|API_URL=$url|" "$ENV_FILE"
        fi
    else
        echo "API_URL=$url" >> "$ENV_FILE"
    fi
}

# Show current info
show_info() {
    local key=$(get_current_key)
    local url=$(grep "^API_URL=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2)

    echo ""
    echo -e "${BLUE}🔐 API for ApiAi${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [[ -n "$key" ]]; then
        echo -e "${GREEN}📍 URL:${NC} ${url:-not set}"
        echo -e "${GREEN}🔑 Key:${NC} $key"
        echo ""
        echo -e "${YELLOW}📋 Copy these values into ApiAi:${NC}"
        echo "   Settings → API keys → Telegram Bot API"
    else
        echo -e "${RED}❌ API key is not set${NC}"
        echo "   Run: $0 generate"
    fi
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

# Help
show_help() {
    echo ""
    echo -e "${BLUE}🔧 API Key Manager${NC}"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  generate    Generate a new API key"
    echo "  show        Show the current key"
    echo "  set <key>   Set a specific key"
    echo "  reset       Reset and generate a new key"
    echo "  init        Initialise (generate if no key)"
    echo ""
    echo "Examples:"
    echo "  $0 generate     # New key"
    echo "  $0 show         # Show"
    echo "  $0 init         # First-time setup"
    echo ""
}

# Main
case "${1:-}" in
    generate|new)
        echo -e "${BLUE}🔄 Generating a new API key...${NC}"
        NEW_KEY=$(generate_key)
        set_key "$NEW_KEY"
        # Detect Public IP
        PUBLIC_IP=$(curl -s ifconfig.me || echo "localhost")
        set_url "http://$PUBLIC_IP:8000/ai_query"
        echo -e "${GREEN}✅ New key generated!${NC}"
        show_info
        echo -e "${YELLOW}⚠️  Remember to restart the containers:${NC}"
        echo "   docker compose down && docker compose up -d"
        ;;

    show|info)
        show_info
        ;;

    set)
        if [[ -z "${2:-}" ]]; then
            echo -e "${RED}❌ Provide a key: $0 set <key>${NC}"
            exit 1
        fi
        set_key "$2"
        echo -e "${GREEN}✅ Key set!${NC}"
        show_info
        ;;

    reset)
        echo -e "${YELLOW}⚠️  Resetting API key...${NC}"
        NEW_KEY=$(generate_key)
        set_key "$NEW_KEY"
        echo -e "${GREEN}✅ Key reset and a new one generated!${NC}"
        show_info
        echo -e "${YELLOW}⚠️  Remember to:${NC}"
        echo "   1. Restart containers: docker compose down && docker compose up -d"
        echo "   2. Update the key in ApiAi"
        ;;

    init)
        CURRENT_KEY=$(get_current_key)
        if [[ -z "$CURRENT_KEY" || "$CURRENT_KEY" == "your_very_long_random_secret_key_here_64_chars_minimum" ]]; then
            echo -e "${BLUE}🔄 Initialising API key...${NC}"
            NEW_KEY=$(generate_key)
            set_key "$NEW_KEY"
            # Detect Public IP
            PUBLIC_IP=$(curl -s ifconfig.me || echo "localhost")
            set_url "http://$PUBLIC_IP:8000/ai_query"
            echo -e "${GREEN}✅ API key generated!${NC}"
            show_info
        else
            echo -e "${GREEN}✅ API key is already configured${NC}"
            show_info
        fi
        ;;

    *)
        show_help
        ;;
esac
