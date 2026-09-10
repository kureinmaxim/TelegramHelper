#!/bin/bash
# =============================================================================
# vless_sync.sh - Fetch VLESS-Reality client configuration
#
# Usage:
#   ./vless_sync.sh              # Human-readable output
#   ./vless_sync.sh --json       # JSON for automation
#   ./vless_sync.sh --qr         # Show QR code (requires qrencode)
#
# This script reads /usr/local/etc/xray/config.json and extracts:
# - Private Key → Public Key (via xray x25519 -i)
# - UUID, Short ID, Port, SNI, Fingerprint
# - Generates a VLESS://... client link
# =============================================================================

set -e

# Output colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Paths
XRAY_CONFIG="/usr/local/etc/xray/config.json"
XRAY_BIN="/usr/local/bin/xray"

# Flags
OUTPUT_JSON=false
SHOW_QR=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --json)
            OUTPUT_JSON=true
            ;;
        --qr)
            SHOW_QR=true
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --json    Output in JSON format"
            echo "  --qr      Show QR code (requires qrencode)"
            echo "  --help    Show this help"
            exit 0
            ;;
    esac
done

# Check for jq
if ! command -v jq &> /dev/null; then
    if [ "$OUTPUT_JSON" = false ]; then
        echo -e "${RED}Error: jq is not installed${NC}"
        echo "Install: apt install jq"
    fi
    exit 1
fi

# Check for config
if [ ! -f "$XRAY_CONFIG" ]; then
    if [ "$OUTPUT_JSON" = false ]; then
        echo -e "${RED}Error: Xray configuration not found${NC}"
        echo "Expected path: $XRAY_CONFIG"
    else
        echo '{"error": "Config not found", "path": "'"$XRAY_CONFIG"'"}'
    fi
    exit 1
fi

# Check xray
if [ ! -x "$XRAY_BIN" ]; then
    # Try PATH
    XRAY_BIN=$(which xray 2>/dev/null || echo "")
    if [ -z "$XRAY_BIN" ]; then
        if [ "$OUTPUT_JSON" = false ]; then
            echo -e "${RED}Error: xray not found${NC}"
            echo "Install Xray or set the correct path"
        else
            echo '{"error": "xray binary not found"}'
        fi
        exit 1
    fi
fi

# Read config
CONFIG=$(cat "$XRAY_CONFIG")

# Extract data from config
# Look in inbounds -> streamSettings -> realitySettings
PRIVATE_KEY=$(echo "$CONFIG" | jq -r '.inbounds[0].streamSettings.realitySettings.privateKey // empty')
SHORT_IDS=$(echo "$CONFIG" | jq -r '.inbounds[0].streamSettings.realitySettings.shortIds[0] // empty')
SERVER_NAMES=$(echo "$CONFIG" | jq -r '.inbounds[0].streamSettings.realitySettings.serverNames[0] // empty')

# UUID from inbounds -> settings -> clients
UUID=$(echo "$CONFIG" | jq -r '.inbounds[0].settings.clients[0].id // empty')

# Port
PORT=$(echo "$CONFIG" | jq -r '.inbounds[0].port // 443')

# Fingerprint (if present)
FINGERPRINT="chrome"

# Check that we found the private key
if [ -z "$PRIVATE_KEY" ]; then
    if [ "$OUTPUT_JSON" = false ]; then
        echo -e "${RED}Error: Private Key not found in config${NC}"
        echo "Check the Xray config structure"
    else
        echo '{"error": "Private key not found in config"}'
    fi
    exit 1
fi

# Derive public key from private
# Note: different xray versions print differently:
# - old: "Public key: xxx"
# - some: "Password: xxx"
# - others: just two lines - private key and public key
XRAY_OUTPUT=$("$XRAY_BIN" x25519 -i "$PRIVATE_KEY" 2>&1)

# Try different parse formats
# Format 1: "Public key: xxx"
PUBLIC_KEY=$(echo "$XRAY_OUTPUT" | grep -i "public" | head -1 | sed 's/.*: *//' | tr -d ' \n\r')

# Format 2: if not found, look for "Password: xxx"
if [ -z "$PUBLIC_KEY" ]; then
    PUBLIC_KEY=$(echo "$XRAY_OUTPUT" | grep -i "password" | head -1 | sed 's/.*: *//' | tr -d ' \n\r')
fi

# Format 3: if output is just two lines (private and public), take the second
if [ -z "$PUBLIC_KEY" ]; then
    # Take the second non-empty line
    PUBLIC_KEY=$(echo "$XRAY_OUTPUT" | grep -v "^$" | sed -n '2p' | tr -d ' \n\r')
fi

# Format 4: if -i prints only public, take the first line
if [ -z "$PUBLIC_KEY" ]; then
    PUBLIC_KEY=$(echo "$XRAY_OUTPUT" | head -1 | tr -d ' \n\r')
fi

if [ -z "$PUBLIC_KEY" ]; then
    if [ "$OUTPUT_JSON" = false ]; then
        echo -e "${RED}Error: Failed to get Public Key${NC}"
        echo "Command: $XRAY_BIN x25519 -i \"$PRIVATE_KEY\""
        echo ""
        echo "xray output:"
        echo "$XRAY_OUTPUT"
    else
        echo '{"error": "Failed to derive public key", "output": "'"$(echo "$XRAY_OUTPUT" | head -5 | sed 's/"/\\"/g' | tr -d '\n')"'"}'
    fi
    exit 1
fi

# Detect server IP
SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || \
            curl -s --max-time 5 https://ifconfig.me 2>/dev/null || \
            hostname -I 2>/dev/null | awk '{print $1}' || \
            echo "YOUR_SERVER_IP")

# SNI (Server Name Indicator)
SNI="${SERVER_NAMES:-yahoo.com}"

# Short ID
SHORT_ID="${SHORT_IDS:-}"

# Generate VLESS link
# Format: vless://uuid@server:port?encryption=none&flow=xtls-rprx-vision&security=reality&sni=sni&fp=fingerprint&pbk=public_key&sid=short_id&type=tcp#name
VLESS_LINK="vless://${UUID}@${SERVER_IP}:${PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=${SNI}&fp=${FINGERPRINT}&pbk=${PUBLIC_KEY}&sid=${SHORT_ID}&type=tcp#ApiAi-VPS"

# Output
if [ "$OUTPUT_JSON" = true ]; then
    # JSON output for automation
    cat <<EOF
{
    "server": "$SERVER_IP",
    "port": $PORT,
    "uuid": "$UUID",
    "public_key": "$PUBLIC_KEY",
    "short_id": "$SHORT_ID",
    "sni": "$SNI",
    "fingerprint": "$FINGERPRINT",
    "flow": "xtls-rprx-vision",
    "vless_link": "$VLESS_LINK"
}
EOF
else
    # Human-readable output
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}           🛡️  VLESS-Reality Configuration for Client          ${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${CYAN}Copy these values into ApiAi → Settings → Reality:${NC}"
    echo ""
    echo -e "${YELLOW}📍 Server:${NC}      ${SERVER_IP}"
    echo -e "${YELLOW}🔌 Port:${NC}        ${PORT}"
    echo -e "${YELLOW}🆔 UUID:${NC}        ${UUID}"
    echo -e "${YELLOW}🔑 Public Key:${NC}  ${PUBLIC_KEY}"
    echo -e "${YELLOW}🏷️  Short ID:${NC}    ${SHORT_ID}"
    echo -e "${YELLOW}🌐 SNI:${NC}         ${SNI}"
    echo -e "${YELLOW}🎭 Fingerprint:${NC} ${FINGERPRINT}"
    echo ""
    echo -e "${GREEN}───────────────────────────────────────────────────────────────${NC}"
    echo -e "${CYAN}🔗 VLESS Link (for Hiddify/Foxray/v2rayNG):${NC}"
    echo ""
    echo -e "${BLUE}${VLESS_LINK}${NC}"
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"

    # QR code if requested
    if [ "$SHOW_QR" = true ]; then
        if command -v qrencode &> /dev/null; then
            echo ""
            echo -e "${CYAN}📱 QR code for mobile clients:${NC}"
            echo ""
            qrencode -t ANSIUTF8 "$VLESS_LINK"
        else
            echo -e "${YELLOW}For a QR code install: apt install qrencode${NC}"
        fi
    fi
fi
