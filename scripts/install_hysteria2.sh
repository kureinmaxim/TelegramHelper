#!/bin/bash
# ============================================================================
# Hysteria2 Server Installation Script
# ============================================================================
# Usage:
#   bash scripts/install_hysteria2.sh [--port PORT] [--password PASSWORD] [--masquerade URL]
#
# Defaults:
#   Port: 443, Password: auto-generated, Masquerade: https://www.microsoft.com
# ============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
PORT=443
PASSWORD=""
MASQUERADE="https://www.microsoft.com"
CERT_DIR="/etc/hysteria"
CERT_PATH="${CERT_DIR}/server.crt"
KEY_PATH="${CERT_DIR}/server.key"
CONFIG_PATH="${CERT_DIR}/config.yaml"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --port) PORT="$2"; shift 2;;
        --password) PASSWORD="$2"; shift 2;;
        --masquerade) MASQUERADE="$2"; shift 2;;
        --help)
            echo "Usage: $0 [--port PORT] [--password PASSWORD] [--masquerade URL]"
            exit 0;;
        *) echo "Unknown option: $1"; exit 1;;
    esac
done

# Detect sudo
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Hysteria2 Server Installation${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ============================================================================
# Step 1: Install Hysteria2 binary
# ============================================================================
echo -e "${YELLOW}[1/6] Installing Hysteria2...${NC}"
if command -v hysteria &> /dev/null; then
    CURRENT_VERSION=$(hysteria version 2>/dev/null | head -1)
    echo -e "${GREEN}Hysteria2 already installed: ${CURRENT_VERSION}${NC}"
else
    bash <(curl -fsSL https://get.hy2.sh/)
    echo -e "${GREEN}Hysteria2 installed successfully${NC}"
fi

# ============================================================================
# Step 2: Generate password
# ============================================================================
echo -e "${YELLOW}[2/6] Generating password...${NC}"
if [ -z "$PASSWORD" ]; then
    PASSWORD=$(openssl rand -base64 16 | tr -d '=+/' | head -c 22)
    echo -e "${GREEN}Password generated: ${PASSWORD}${NC}"
else
    echo -e "${GREEN}Using provided password${NC}"
fi

# ============================================================================
# Step 3: Generate TLS certificate
# ============================================================================
echo -e "${YELLOW}[3/6] Generating self-signed TLS certificate...${NC}"
$SUDO mkdir -p "$CERT_DIR"

if [ -f "$CERT_PATH" ] && [ -f "$KEY_PATH" ]; then
    echo -e "${GREEN}Certificate already exists, skipping${NC}"
else
    $SUDO openssl req -x509 -nodes \
        -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
        -keyout "$KEY_PATH" \
        -out "$CERT_PATH" \
        -subj "/CN=www.microsoft.com" \
        -days 36500 2>/dev/null
    echo -e "${GREEN}Certificate generated${NC}"
fi

# ============================================================================
# Step 4: Write server config
# ============================================================================
echo -e "${YELLOW}[4/6] Writing server configuration...${NC}"

# Detect server IP
SERVER_IP=$(curl -s https://api.ipify.org || curl -s https://ifconfig.me/ip || echo "0.0.0.0")
echo -e "  Server IP: ${GREEN}${SERVER_IP}${NC}"
echo -e "  Port: ${GREEN}${PORT}${NC} (UDP)"

$SUDO tee "$CONFIG_PATH" > /dev/null << EOF
listen: :${PORT}

tls:
  cert: ${CERT_PATH}
  key: ${KEY_PATH}

auth:
  type: password
  password: "${PASSWORD}"

masquerade:
  type: proxy
  proxy:
    url: ${MASQUERADE}
    rewriteHost: true
EOF

echo -e "${GREEN}Config written to ${CONFIG_PATH}${NC}"

# ============================================================================
# Step 5: Create systemd service
# ============================================================================
echo -e "${YELLOW}[5/6] Creating systemd service...${NC}"

# Check if official service exists
if [ -f /etc/systemd/system/hysteria-server.service ]; then
    echo -e "${GREEN}Service file already exists${NC}"
else
    $SUDO tee /etc/systemd/system/hysteria-server.service > /dev/null << 'EOF'
[Unit]
Description=Hysteria2 Server
After=network.target

[Service]
ExecStart=/usr/local/bin/hysteria server -c /etc/hysteria/config.yaml
Restart=on-failure
RestartSec=5
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
EOF
    echo -e "${GREEN}Systemd service created${NC}"
fi

$SUDO systemctl daemon-reload
$SUDO systemctl enable hysteria-server

# ============================================================================
# Step 6: Open firewall port (UDP!)
# ============================================================================
echo -e "${YELLOW}[6/6] Configuring firewall...${NC}"

if command -v ufw &> /dev/null; then
    $SUDO ufw allow ${PORT}/udp comment "Hysteria2"
    echo -e "${GREEN}UFW: UDP port ${PORT} opened${NC}"
else
    echo -e "${YELLOW}UFW not found, skipping firewall configuration${NC}"
    echo -e "${YELLOW}Make sure UDP port ${PORT} is open!${NC}"
fi

# ============================================================================
# Fix TLS key permissions for the service user
# ============================================================================
# The official hysteria unit runs as User=hysteria (not root), while openssl
# created the key as root:root 600 → FATAL "tls.key: permission denied". Take
# the real user from the unit file and chown the key; if there is no User= (root) — 600.
SVC_USER="$(grep -oP '^User=\s*\K\S+' /etc/systemd/system/hysteria-server.service 2>/dev/null | head -1)"
$SUDO chmod 644 "$CERT_PATH" 2>/dev/null || true
if [ -n "${SVC_USER:-}" ] && [ "$SVC_USER" != "root" ] && id "$SVC_USER" >/dev/null 2>&1; then
    $SUDO chown "$SVC_USER":"$SVC_USER" "$KEY_PATH" "$CERT_PATH" 2>/dev/null \
        || $SUDO chown "$SVC_USER" "$KEY_PATH" "$CERT_PATH" 2>/dev/null || true
    $SUDO chmod 640 "$KEY_PATH"
    echo -e "${GREEN}TLS key owned by service user: ${SVC_USER}${NC}"
else
    $SUDO chmod 600 "$KEY_PATH"
fi

# ============================================================================
# Start service
# ============================================================================
echo ""
echo -e "${YELLOW}Starting Hysteria2 service...${NC}"
$SUDO systemctl restart hysteria-server
sleep 2

if systemctl is-active --quiet hysteria-server; then
    echo -e "${GREEN}Hysteria2 is running!${NC}"
else
    echo -e "${RED}Failed to start. Check: journalctl -u hysteria-server -n 20${NC}"
fi

# ============================================================================
# Save client config
# ============================================================================
HOME_DIR=$(eval echo ~${SUDO_USER:-$USER})

cat > "${HOME_DIR}/hysteria2_client.txt" << EOF
============================================
  Hysteria2 Client Configuration
============================================

Server:   ${SERVER_IP}
Port:     ${PORT} (UDP)
Password: ${PASSWORD}

URI: hy2://${PASSWORD}@${SERVER_IP}:${PORT}/?insecure=1#Hysteria2

--- Sing-Box Client Config ---
{
  "outbounds": [{
    "type": "hysteria2",
    "server": "${SERVER_IP}",
    "server_port": ${PORT},
    "password": "${PASSWORD}",
    "tls": {
      "enabled": true,
      "insecure": true
    }
  }]
}
EOF

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}  Installation Complete!${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo -e "  Server:   ${GREEN}${SERVER_IP}${NC}"
echo -e "  Port:     ${GREEN}${PORT}/udp${NC}"
echo -e "  Password: ${GREEN}${PASSWORD}${NC}"
echo ""
echo -e "  URI: ${YELLOW}hy2://${PASSWORD}@${SERVER_IP}:${PORT}/?insecure=1#Hysteria2${NC}"
echo ""
echo -e "  Client config saved to: ${GREEN}${HOME_DIR}/hysteria2_client.txt${NC}"
echo ""
echo -e "  Commands:"
echo -e "    Status:  ${BLUE}systemctl status hysteria-server${NC}"
echo -e "    Logs:    ${BLUE}journalctl -u hysteria-server -f${NC}"
echo -e "    Restart: ${BLUE}systemctl restart hysteria-server${NC}"
echo ""
