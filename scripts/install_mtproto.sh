#!/bin/bash
# ============================================================================
# MTProto Proxy Server Installation Script
# ============================================================================
# Usage:
#   bash scripts/install_mtproto.sh [--mode MODE] [--port PORT] [--domain DOMAIN] [--workers N]
#
# Defaults:
#   Mode: ee_split, Port: 993, Domain: google.com, Workers: 2
# ============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
PORT=993
DOMAIN="google.com"
WORKERS=2
MODE="ee_split"
BUILD_DIR="/opt/MTProxy"
BINARY_PATH="/usr/local/bin/mtproto-proxy"
CONFIG_DIR="/etc/mtproto-proxy"
SECRET_FILE="${CONFIG_DIR}/proxy-secret"
MULTI_CONF="${CONFIG_DIR}/proxy-multi.conf"
SERVICE_NAME="mtproto-proxy"
STATS_PORT=2398

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --port) PORT="$2"; shift 2;;
        --domain) DOMAIN="$2"; shift 2;;
        --workers) WORKERS="$2"; shift 2;;
        --mode) MODE="$2"; shift 2;;
        --help)
            echo "Usage: $0 [--mode dd_inline|ee_split] [--port PORT] [--domain DOMAIN] [--workers N]"
            exit 0;;
        *) echo "Unknown option: $1"; exit 1;;
    esac
done

if [[ "$MODE" != "dd_inline" && "$MODE" != "ee_split" ]]; then
    echo "Unsupported mode: $MODE"
    echo "Use: dd_inline or ee_split"
    exit 1
fi

# Detect sudo
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  MTProto Proxy Installation${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ============================================================================
# Step 1: Install build dependencies
# ============================================================================
echo -e "${YELLOW}[1/7] Installing build dependencies...${NC}"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq git curl cron build-essential libssl-dev zlib1g-dev xxd
echo -e "${GREEN}Dependencies installed${NC}"
$SUDO systemctl enable --now cron >/dev/null 2>&1 || true

# ============================================================================
# Step 2: Clone and build MTProxy
# ============================================================================
echo -e "${YELLOW}[2/7] Building MTProto proxy from source...${NC}"
if [ -f "$BINARY_PATH" ]; then
    echo -e "${GREEN}MTProto proxy already installed: ${BINARY_PATH}${NC}"
else
    if [ -d "$BUILD_DIR" ]; then
        $SUDO rm -rf "$BUILD_DIR"
    fi
    git clone https://github.com/TelegramMessenger/MTProxy.git "$BUILD_DIR"
    cd "$BUILD_DIR" && make -j$(nproc)
    $SUDO cp "${BUILD_DIR}/objs/bin/mtproto-proxy" "$BINARY_PATH"
    $SUDO chmod +x "$BINARY_PATH"
    cd -
    echo -e "${GREEN}MTProto proxy compiled and installed${NC}"
fi

# ============================================================================
# Step 3: Fetch Telegram proxy config files
# ============================================================================
echo -e "${YELLOW}[3/7] Fetching Telegram proxy config...${NC}"
$SUDO mkdir -p "$CONFIG_DIR"

curl -s https://core.telegram.org/getProxySecret -o /tmp/proxy-secret
$SUDO mv /tmp/proxy-secret "$SECRET_FILE"
echo -e "  proxy-secret: ${GREEN}${SECRET_FILE}${NC}"

curl -s https://core.telegram.org/getProxyConfig -o /tmp/proxy-multi.conf
$SUDO mv /tmp/proxy-multi.conf "$MULTI_CONF"
echo -e "  proxy-multi.conf: ${GREEN}${MULTI_CONF}${NC}"

# ============================================================================
# Step 4: Generate secret
# ============================================================================
echo -e "${YELLOW}[4/7] Generating fake-TLS secret...${NC}"

# Generate 16 random bytes as hex (32 hex chars)
SECRET_RANDOM=$(head -c 16 /dev/urandom | xxd -ps -c 32)
# Encode domain to hex
DOMAIN_HEX=$(echo -n "$DOMAIN" | xxd -ps -c 256)

if [[ "$MODE" == "ee_split" ]]; then
    SERVER_SECRET="${SECRET_RANDOM}"
    CLIENT_SECRET="ee${SECRET_RANDOM}${DOMAIN_HEX}"
    SERVICE_DOMAIN_FLAG="-D ${DOMAIN}"
else
    SERVER_SECRET="dd${SECRET_RANDOM}${DOMAIN_HEX}"
    CLIENT_SECRET="${SERVER_SECRET}"
    SERVICE_DOMAIN_FLAG=""
fi

echo -e "  Domain: ${GREEN}${DOMAIN}${NC}"
echo -e "  Mode: ${GREEN}${MODE}${NC}"
echo -e "  Server secret: ${GREEN}${SERVER_SECRET}${NC}"
echo -e "  Client secret: ${GREEN}${CLIENT_SECRET}${NC}"

# Detect server IP
SERVER_IP=$(curl -s https://api.ipify.org || curl -s https://ifconfig.me/ip || echo "0.0.0.0")
echo -e "  Server IP: ${GREEN}${SERVER_IP}${NC}"

# ============================================================================
# Step 5: Create systemd service
# ============================================================================
echo -e "${YELLOW}[5/7] Creating systemd service...${NC}"

$SUDO tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=MTProto Proxy
After=network.target

[Service]
Type=simple
ExecStart=${BINARY_PATH} -u nobody -p ${STATS_PORT} -H ${PORT} -S ${SERVER_SECRET} ${SERVICE_DOMAIN_FLAG} --aes-pwd ${SECRET_FILE} ${MULTI_CONF} -M ${WORKERS} --nat-info ${SERVER_IP}:${SERVER_IP}
Restart=on-failure
RestartSec=5
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable ${SERVICE_NAME}
echo -e "${GREEN}Systemd service created and enabled${NC}"

# ============================================================================
# Step 6: Open firewall port (TCP!)
# ============================================================================
echo -e "${YELLOW}[6/7] Configuring firewall...${NC}"

if command -v ufw &> /dev/null; then
    $SUDO ufw allow ${PORT}/tcp comment "MTProto Proxy"
    echo -e "${GREEN}UFW: TCP port ${PORT} opened${NC}"
else
    echo -e "${YELLOW}UFW not found, skipping firewall configuration${NC}"
    echo -e "${YELLOW}Make sure TCP port ${PORT} is open!${NC}"
fi

# ============================================================================
# Step 7: Setup cron for daily proxy-secret refresh
# ============================================================================
echo -e "${YELLOW}[7/7] Setting up daily config refresh...${NC}"

# Remove old cron entries, add new one
(crontab -l 2>/dev/null | grep -v 'getProxySecret'; \
 echo "0 3 * * * curl -s https://core.telegram.org/getProxySecret -o ${SECRET_FILE} && curl -s https://core.telegram.org/getProxyConfig -o ${MULTI_CONF} && systemctl restart ${SERVICE_NAME}") | crontab -
echo -e "${GREEN}Cron: daily refresh at 03:00${NC}"

# ============================================================================
# Start service
# ============================================================================
echo ""
echo -e "${YELLOW}Starting MTProto proxy service...${NC}"
$SUDO systemctl restart ${SERVICE_NAME}
sleep 2

if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}MTProto proxy is running!${NC}"
else
    echo -e "${RED}Failed to start. Check: journalctl -u ${SERVICE_NAME} -n 20${NC}"
fi

# ============================================================================
# Save client config
# ============================================================================
HOME_DIR=$(eval echo ~${SUDO_USER:-$USER})

cat > "${HOME_DIR}/mtproto_client.txt" << EOF
============================================
  MTProto Proxy Client Configuration
============================================

Server:  ${SERVER_IP}
Port:    ${PORT} (TCP)
Mode:    ${MODE}
Secret:  ${CLIENT_SECRET}
Domain:  ${DOMAIN} (fake-TLS)

tg:// link (paste in Telegram):
tg://proxy?server=${SERVER_IP}&port=${PORT}&secret=${CLIENT_SECRET}

HTTPS link (open in browser):
https://t.me/proxy?server=${SERVER_IP}&port=${PORT}&secret=${CLIENT_SECRET}
EOF

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}  Installation Complete!${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo -e "  Mode:    ${GREEN}${MODE}${NC}"
echo -e "  Server:  ${GREEN}${SERVER_IP}${NC}"
echo -e "  Port:    ${GREEN}${PORT}/tcp${NC}"
echo -e "  Domain:  ${GREEN}${DOMAIN}${NC} (fake-TLS)"
echo -e "  Secret:  ${GREEN}${CLIENT_SECRET}${NC}"
echo ""
echo -e "  tg:// link:"
echo -e "  ${YELLOW}tg://proxy?server=${SERVER_IP}&port=${PORT}&secret=${CLIENT_SECRET}${NC}"
echo ""
echo -e "  HTTPS link:"
echo -e "  ${YELLOW}https://t.me/proxy?server=${SERVER_IP}&port=${PORT}&secret=${CLIENT_SECRET}${NC}"
echo ""
echo -e "  Client config saved to: ${GREEN}${HOME_DIR}/mtproto_client.txt${NC}"
echo ""
echo -e "  Commands:"
echo -e "    Status:  ${BLUE}systemctl status ${SERVICE_NAME}${NC}"
echo -e "    Logs:    ${BLUE}journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "    Restart: ${BLUE}systemctl restart ${SERVICE_NAME}${NC}"
echo ""
