#!/bin/bash
# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
# 🚀 AUTO SETUP VPS — VLESS-Reality + Hysteria2
# ═══════════════════════════════════════════════════════════════
#
# This script automatically configures a fresh Debian 12 VPS:
# - minimal mode: Xray + Hysteria2 (manual management)
# - full mode: Docker + TelegramHelper + Xray + Hysteria2 (via the bot)
#
# Both protocols run at the same time:
#   VLESS-Reality — TCP (masquerades as HTTPS)
#   Hysteria2     — UDP (QUIC-based, high throughput)
#
# Usage:
#   ./auto_setup_vps.sh --host 123.45.67.89 --password "your_pass"
#   ./auto_setup_vps.sh --host 123.45.67.89 --mode full --password "pass"
#   ./auto_setup_vps.sh --host 123.45.67.89 --no-hysteria2 --password "pass"
#
# Or via an environment variable:
#   SSH_PASS="your_pass" ./auto_setup_vps.sh --host 123.45.67.89
#
# ═══════════════════════════════════════════════════════════════

set -e
umask 077

# Output colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Defaults
SSH_HOST=""
SSH_PORT="22"
SSH_USER="root"
SSH_PASSWORD="${SSH_PASS:-}"
INSTALL_MODE="minimal"  # minimal or full
BOT_TOKEN=""
ADMIN_ID=""
OUTPUT_DIR="./vless_configs"
VLESS_PORT="443"

# Hysteria2 (enabled by default)
HY2_ENABLE="true"
HY2_PORT="443"          # UDP port (does not conflict with VLESS TCP 443)
HY2_PASSWORD=""          # Auto-generate if empty

# AI providers (optional)
ANTHROPIC_KEY=""
OPENAI_KEY=""

# Nginx + Certbot (optional)
NGINX_ENABLE="false"
NGINX_DOMAIN=""
NGINX_EMAIL=""
NGINX_HTTPS_PORT=""
NGINX_UPSTREAM_HOST="127.0.0.1"
NGINX_UPSTREAM_PORT="8000"

# Headscale (optional, full mode)
HEADSCALE_ENABLE="false"
HEADSCALE_DOMAIN=""
HA_DOMAIN=""

# Disk auto-cleanup via scripts/vps_maintenance.sh
# (enabled by default — installs a weekly Docker prune systemd timer
# + journald cap 500M; see POST_DEPLOY.md §11)
WITH_MAINTENANCE="true"

print_banner() {
    echo -e "${CYAN}"
    echo "═══════════════════════════════════════════════════════════════"
    echo "   🚀 AUTO SETUP VPS — VLESS-Reality + Hysteria2"
    echo "═══════════════════════════════════════════════════════════════"
    echo -e "${NC}"
}

print_help() {
    echo -e "${GREEN}Usage:${NC}"
    echo "  $0 [OPTIONS]"
    echo ""
    echo -e "${GREEN}Required:${NC}"
    echo "  --host, -h HOST       Server IP or domain"
    echo "  --password, -p PASS   SSH password (or env SSH_PASS)"
    echo ""
    echo -e "${GREEN}Optional:${NC}"
    echo "  --port PORT           SSH port (default: 22)"
    echo "  --user USER           SSH user (default: root)"
    echo "  --mode MODE           Install mode:"
    echo "                          minimal - Xray + Hysteria2 (default)"
    echo "                          full    - Docker + TelegramHelper + Xray + Hysteria2"
    echo "  --vless-port PORT     VLESS TCP port (default: 443)"
    echo "  --hy2-port PORT       Hysteria2 UDP port (default: 443)"
    echo "  --hy2-password PASS   Hysteria2 password (default: auto)"
    echo "  --no-hysteria2        Do not install Hysteria2"
    echo ""
    echo -e "${GREEN}full-mode options:${NC}"
    echo "  --bot-token TOKEN     Telegram Bot Token (from @BotFather)"
    echo "  --admin-id ID         Your Telegram User ID"
    echo "  --anthropic-key KEY   Anthropic API key (optional)"
    echo "  --openai-key KEY      OpenAI API key (optional)"
    echo ""
    echo -e "${GREEN}Nginx + Certbot (optional, full mode):${NC}"
    echo "  --nginx               Install and configure Nginx + SSL"
    echo "  --nginx-domain DOMAIN HTTPS domain (e.g. api.example.com)"
    echo "  --nginx-email EMAIL   Email for Let's Encrypt"
    echo "  --nginx-https-port    Nginx HTTPS port (default: 443, or 8443 on conflict)"
    echo "  --nginx-upstream-port API port (default: 8000)"
    echo ""
    echo -e "${GREEN}Headscale (optional, full mode):${NC}"
    echo "  --headscale           Install Headscale (self-hosted Tailscale)"
    echo "  --headscale-domain D  Headscale domain (e.g. headscale.example.com)"
    echo "  --ha-domain DOMAIN    Home Assistant domain (e.g. ha.example.com)"
    echo ""
    echo -e "${GREEN}Extra:${NC}"
    echo "  --output DIR          Directory to save configs"
    echo "  --no-maintenance      Skip disk auto-cleanup (installed by default:"
    echo "                          weekly docker prune systemd timer +"
    echo "                          journald cap 500M, see POST_DEPLOY.md §11)"
    echo "  --with-maintenance    Explicitly enable (already the default)"
    echo "  --help                Show this help"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  # Minimal install (Xray + Hysteria2)"
    echo "  $0 --host 123.45.67.89 --password 'mypass'"
    echo ""
    echo "  # VLESS only (no Hysteria2)"
    echo "  $0 --host 123.45.67.89 --no-hysteria2 --password 'mypass'"
    echo ""
    echo "  # Hysteria2 on another port"
    echo "  $0 --host 123.45.67.89 --hy2-port 8443 --password 'mypass'"
    echo ""
    echo "  # Full install with Telegram bot"
    echo "  $0 --host 123.45.67.89 --mode full \\"
    echo "     --bot-token '123456:ABC...' --admin-id 987654321 \\"
    echo "     --password 'mypass'"
    echo ""
    echo "  # Via environment variable"
    echo "  SSH_PASS='mypass' $0 --host 123.45.67.89 --mode full"
}

check_dependencies() {
    echo -e "${BLUE}📦 Checking dependencies...${NC}"
    
    # Check sshpass
    if ! command -v sshpass &> /dev/null; then
        echo -e "${YELLOW}⚠️ sshpass not found${NC}"
        echo ""
        
        # Detect OS
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo "On macOS install via Homebrew:"
            echo -e "${CYAN}  brew install hudochenkov/sshpass/sshpass${NC}"
        elif [[ -f /etc/debian_version ]]; then
            echo "On Debian/Ubuntu:"
            echo -e "${CYAN}  sudo apt-get install sshpass${NC}"
        elif [[ -f /etc/redhat-release ]]; then
            echo "On CentOS/RHEL:"
            echo -e "${CYAN}  sudo yum install sshpass${NC}"
        else
            echo "Install sshpass for your OS"
        fi
        echo ""
        exit 1
    fi
    
    # Check ssh
    if ! command -v ssh &> /dev/null; then
        echo -e "${RED}❌ ssh not found. Install the OpenSSH client.${NC}"
        exit 1
    fi
    
    # Check scp
    if ! command -v scp &> /dev/null; then
        echo -e "${RED}❌ scp not found. Install the OpenSSH client.${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✅ All dependencies installed${NC}"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --host|-h)
                SSH_HOST="$2"
                shift 2
                ;;
            --port)
                SSH_PORT="$2"
                shift 2
                ;;
            --user)
                SSH_USER="$2"
                shift 2
                ;;
            --password|-p)
                SSH_PASSWORD="$2"
                shift 2
                ;;
            --mode)
                INSTALL_MODE="$2"
                shift 2
                ;;
            --vless-port)
                VLESS_PORT="$2"
                shift 2
                ;;
            --bot-token)
                BOT_TOKEN="$2"
                shift 2
                ;;
            --admin-id)
                ADMIN_ID="$2"
                shift 2
                ;;
            --anthropic-key)
                ANTHROPIC_KEY="$2"
                shift 2
                ;;
            --openai-key)
                OPENAI_KEY="$2"
                shift 2
                ;;
            --nginx)
                NGINX_ENABLE="true"
                shift 1
                ;;
            --nginx-domain)
                NGINX_DOMAIN="$2"
                shift 2
                ;;
            --nginx-email)
                NGINX_EMAIL="$2"
                shift 2
                ;;
            --nginx-https-port)
                NGINX_HTTPS_PORT="$2"
                shift 2
                ;;
            --hy2-port)
                HY2_PORT="$2"
                shift 2
                ;;
            --hy2-password)
                HY2_PASSWORD="$2"
                shift 2
                ;;
            --no-hysteria2)
                HY2_ENABLE="false"
                shift 1
                ;;
            --nginx-upstream-port)
                NGINX_UPSTREAM_PORT="$2"
                shift 2
                ;;
            --headscale)
                HEADSCALE_ENABLE="true"
                shift 1
                ;;
            --headscale-domain)
                HEADSCALE_DOMAIN="$2"
                shift 2
                ;;
            --ha-domain)
                HA_DOMAIN="$2"
                shift 2
                ;;
            --output)
                OUTPUT_DIR="$2"
                shift 2
                ;;
            --with-maintenance)
                WITH_MAINTENANCE="true"
                shift 1
                ;;
            --no-maintenance)
                WITH_MAINTENANCE="false"
                shift 1
                ;;
            --help)
                print_help
                exit 0
                ;;
            *)
                echo -e "${RED}❌ Unknown option: $1${NC}"
                print_help
                exit 1
                ;;
        esac
    done
}

validate_args() {
    local has_error=0
    
    if [[ -z "$SSH_HOST" ]]; then
        echo -e "${RED}❌ --host is required${NC}"
        has_error=1
    fi
    
    if [[ -z "$SSH_PASSWORD" ]]; then
        echo -e "${RED}❌ --password (or SSH_PASS) is required${NC}"
        has_error=1
    fi

    if [[ "$INSTALL_MODE" == "full" && "$NGINX_ENABLE" == "true" ]]; then
        if [[ -z "$NGINX_DOMAIN" || -z "$NGINX_EMAIL" ]]; then
            echo -e "${RED}❌ --nginx requires --nginx-domain and --nginx-email${NC}"
            has_error=1
        fi
    fi

    if [[ "$INSTALL_MODE" != "full" && "$NGINX_ENABLE" == "true" ]]; then
        echo -e "${RED}❌ Nginx is available only in full mode${NC}"
        has_error=1
    fi
    
    if [[ "$INSTALL_MODE" != "minimal" && "$INSTALL_MODE" != "full" ]]; then
        echo -e "${RED}❌ Invalid mode: $INSTALL_MODE (allowed: minimal, full)${NC}"
        has_error=1
    fi
    
    if [[ "$INSTALL_MODE" == "full" ]]; then
        if [[ -z "$BOT_TOKEN" ]]; then
            echo -e "${RED}❌ full mode requires --bot-token${NC}"
            has_error=1
        fi
        if [[ -z "$ADMIN_ID" ]]; then
            echo -e "${RED}❌ full mode requires --admin-id${NC}"
            has_error=1
        fi
    fi

    if [[ "$HEADSCALE_ENABLE" == "true" ]]; then
        if [[ "$INSTALL_MODE" != "full" ]]; then
            echo -e "${RED}❌ Headscale is available only in full mode${NC}"
            has_error=1
        fi
        if [[ -z "$HEADSCALE_DOMAIN" ]]; then
            echo -e "${RED}❌ --headscale requires --headscale-domain${NC}"
            has_error=1
        fi
    fi
    
    if [[ $has_error -eq 1 ]]; then
        echo ""
        print_help
        exit 1
    fi
}

ssh_cmd() {
    sshpass -p "$SSH_PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "$@"
}

scp_cmd() {
    sshpass -p "$SSH_PASSWORD" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -P "$SSH_PORT" "$@"
}

test_connection() {
    echo -e "${BLUE}🔗 Testing connection to $SSH_HOST...${NC}"
    
    if ! ssh_cmd "echo 'Connection OK'" &> /dev/null; then
        echo -e "${RED}❌ Could not connect to the server${NC}"
        echo "   Check IP, port, user, and password"
        exit 1
    fi
    
    echo -e "${GREEN}✅ Connection OK!${NC}"
    
    # Fetch server info
    echo -e "${BLUE}📋 Server info:${NC}"
    ssh_cmd "uname -a && cat /etc/os-release | head -2"
}

run_installation() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}   📦 Starting install in mode: ${YELLOW}$INSTALL_MODE${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # Create a temporary install script
    local install_script
    if [[ "$INSTALL_MODE" == "minimal" ]]; then
        install_script=$(create_minimal_install_script)
    else
        install_script=$(create_full_install_script)
    fi
    
    # Copy and run the script
    echo -e "${BLUE}📤 Uploading install script to the server...${NC}"
    echo "$install_script" | ssh_cmd "cat > /tmp/install_vless.sh && chmod +x /tmp/install_vless.sh"
    
    echo -e "${BLUE}⚙️ Starting install (this may take several minutes)...${NC}"
    echo ""
    
    # Run the install
    ssh_cmd "bash /tmp/install_vless.sh"
    
    echo ""
    echo -e "${GREEN}✅ Install finished!${NC}"
}

create_minimal_install_script() {
    # Pass SSH port for UFW and Fail2ban
    cat << SCRIPT_EOF
#!/bin/bash
# Minimal install: Xray + Hysteria2

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Parameters passed from the local script
SSH_PORT_FOR_UFW="$SSH_PORT"
HOME_DIR="\$HOME"
VLESS_PORT="$VLESS_PORT"
HY2_ENABLE="$HY2_ENABLE"
HY2_PORT="$HY2_PORT"
HY2_PASSWORD="$HY2_PASSWORD"

# Detect whether sudo is needed
if [ "\$(id -u)" -ne 0 ]; then
    SUDO="sudo"
    echo -e "\${YELLOW}⚠️ Running as a regular user, using sudo\${NC}"
else
    SUDO=""
fi

# Step counter
TOTAL_STEPS=6
if [ "\$HY2_ENABLE" = "true" ]; then
    TOTAL_STEPS=8
fi
STEP=0
next_step() { STEP=\$((STEP+1)); echo -e "\${YELLOW}[\${STEP}/\${TOTAL_STEPS}] \$1\${NC}"; }

echo -e "\${GREEN}=== Minimal VLESS-Reality + Hysteria2 install ===\${NC}"

# 1. System update
next_step "Updating the system..."
\$SUDO apt-get update -qq
\$SUDO apt-get upgrade -y -qq

# 2. Base packages + security tools
next_step "Installing packages..."
\$SUDO apt-get install -y -qq curl jq openssl ca-certificates qrencode ufw fail2ban unattended-upgrades

# 3. Install Xray
next_step "Installing Xray-core..."
if ! command -v xray &> /dev/null; then
    \$SUDO bash -c "\$(curl -sL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
else
    echo "Xray is already installed"
fi

# 4. Generate VLESS keys
next_step "Generating VLESS keys..."
UUID=\$(xray uuid)
X25519_OUTPUT=\$(/usr/local/bin/xray x25519 2>/dev/null)
PRIVATE_KEY=\$(echo "\$X25519_OUTPUT" | grep -i "private" | awk -F': ' '{print \$2}' | tr -d ' ')
PUBLIC_KEY=\$(echo "\$X25519_OUTPUT" | grep -i "public" | awk -F': ' '{print \$2}' | tr -d ' ')
SHORT_ID=\$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 8)
SERVER_IP=\$(curl -s https://api.ipify.org || curl -s https://ifconfig.me/ip)

# Verify keys were generated
if [ -z "\$PRIVATE_KEY" ] || [ -z "\$PUBLIC_KEY" ]; then
    echo -e "\${YELLOW}⚠️ Regenerating keys...\${NC}"
    X25519_OUTPUT=\$(/usr/local/bin/xray x25519)
    PRIVATE_KEY=\$(echo "\$X25519_OUTPUT" | head -1 | awk -F': ' '{print \$2}' | tr -d ' ')
    PUBLIC_KEY=\$(echo "\$X25519_OUTPUT" | tail -1 | awk -F': ' '{print \$2}' | tr -d ' ')
fi

echo "UUID: \$UUID"
echo "Private Key: [hidden; stored on server only]"
echo "Public Key: \${PUBLIC_KEY:0:10}..."

# yahoo.com by default: www.microsoft.com is blocked by DPI on some mobile operators
# Change later from the bot: /vless_set_sni
SNI="yahoo.com"
FINGERPRINT="chrome"
PORT=\$VLESS_PORT

# 5. Create Xray config
next_step "Configuring and starting Xray..."
\$SUDO tee /usr/local/etc/xray/config.json > /dev/null << EOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "port": \$PORT,
    "protocol": "vless",
    "settings": {
      "clients": [{
        "id": "\$UUID",
        "flow": "xtls-rprx-vision"
      }],
      "decryption": "none"
    },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "show": false,
        "dest": "\${SNI}:443",
        "xver": 0,
        "serverNames": ["\$SNI"],
        "privateKey": "\$PRIVATE_KEY",
        "shortIds": ["\$SHORT_ID"]
      }
    }
  }],
  "outbounds": [{"protocol": "freedom", "tag": "direct"}]
}
EOF

\$SUDO xray -test -config /usr/local/etc/xray/config.json
\$SUDO systemctl enable xray
\$SUDO systemctl restart xray

# ── Hysteria2 Installation ──
if [ "\$HY2_ENABLE" = "true" ]; then
    next_step "Installing Hysteria2..."
    if ! command -v hysteria &> /dev/null; then
        bash <(curl -fsSL https://get.hy2.sh/)
    else
        echo "Hysteria2 is already installed"
    fi

    next_step "Configuring and starting Hysteria2..."

    # Generate Hysteria2 password
    if [ -z "\$HY2_PASSWORD" ]; then
        HY2_PASSWORD=\$(openssl rand -base64 16 | tr -d '=+/' | head -c 22)
    fi

    # TLS certificate for Hysteria2
    HY2_CERT_DIR="/etc/hysteria"
    \$SUDO mkdir -p "\$HY2_CERT_DIR"
    if [ ! -f "\$HY2_CERT_DIR/server.crt" ] || [ ! -f "\$HY2_CERT_DIR/server.key" ]; then
        \$SUDO openssl req -x509 -nodes \
            -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
            -keyout "\$HY2_CERT_DIR/server.key" \
            -out "\$HY2_CERT_DIR/server.crt" \
            -subj "/CN=www.microsoft.com" \
            -days 36500 2>/dev/null
    fi

    # Hysteria2 config
    \$SUDO tee "\$HY2_CERT_DIR/config.yaml" > /dev/null << EOF
listen: :\$HY2_PORT

tls:
  cert: \$HY2_CERT_DIR/server.crt
  key: \$HY2_CERT_DIR/server.key

auth:
  type: password
  password: "\$HY2_PASSWORD"

masquerade:
  type: proxy
  proxy:
    url: https://www.microsoft.com
    rewriteHost: true
EOF

    # systemd service
    if [ ! -f /etc/systemd/system/hysteria-server.service ]; then
        \$SUDO tee /etc/systemd/system/hysteria-server.service > /dev/null << 'SVCEOF'
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
SVCEOF
    fi

    \$SUDO systemctl daemon-reload
    \$SUDO systemctl enable hysteria-server
    \$SUDO systemctl restart hysteria-server
    sleep 2

    if systemctl is-active --quiet hysteria-server; then
        echo -e "\${GREEN}✅ Hysteria2 started on UDP port \$HY2_PORT\${NC}"
    else
        echo -e "\${YELLOW}⚠️ Hysteria2 failed to start, check: journalctl -u hysteria-server -n 20\${NC}"
    fi

    HY2_LINK="hy2://\${HY2_PASSWORD}@\${SERVER_IP}:\${HY2_PORT}/?insecure=1#Hysteria2"
fi

# Security Hardening
next_step "Configuring security..."

# UFW Firewall
\$SUDO ufw default deny incoming
\$SUDO ufw default allow outgoing
\$SUDO ufw allow \$SSH_PORT_FOR_UFW/tcp   # SSH
\$SUDO ufw allow \$PORT/tcp               # VLESS (TCP)
if [ "\$HY2_ENABLE" = "true" ]; then
    \$SUDO ufw allow \$HY2_PORT/udp         # Hysteria2 (UDP)
fi
\$SUDO ufw --force enable

# Fail2ban with the SSH port
\$SUDO tee /etc/fail2ban/jail.local > /dev/null << JAILEOF
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3

[sshd]
enabled = true
port = \$SSH_PORT_FOR_UFW
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 24h
JAILEOF
\$SUDO systemctl enable fail2ban
\$SUDO systemctl restart fail2ban

# Unattended security updates
echo 'APT::Periodic::Update-Package-Lists "1";' | \$SUDO tee /etc/apt/apt.conf.d/20auto-upgrades > /dev/null
echo 'APT::Periodic::Unattended-Upgrade "1";' | \$SUDO tee -a /etc/apt/apt.conf.d/20auto-upgrades > /dev/null

# Kernel hardening
\$SUDO tee -a /etc/sysctl.conf > /dev/null << 'SYSEOF'
# Security hardening
net.ipv4.conf.all.rp_filter = 1
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
SYSEOF
\$SUDO sysctl -p 2>/dev/null || true

echo -e "\${GREEN}✅ UFW, Fail2ban, and unattended upgrades configured\${NC}"

# Write the client config file
VLESS_LINK="vless://\${UUID}@\${SERVER_IP}:\${PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=\${SNI}&fp=\${FINGERPRINT}&pbk=\${PUBLIC_KEY}&sid=\${SHORT_ID}&type=tcp#VPS-Reality"

cat > \$HOME_DIR/vless_client_config.txt << EOF
═══════════════════════════════════════════════════════════════
       🛡️  VLESS-Reality + ⚡ Hysteria2 — Client Config
═══════════════════════════════════════════════════════════════

━━━ VLESS-Reality (TCP) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Server:      \$SERVER_IP
🔌 Port:        \$PORT (TCP)
🆔 UUID:        \$UUID
🔑 Public Key:  \$PUBLIC_KEY
🏷️ Short ID:    \$SHORT_ID
🌐 SNI:         \$SNI
🎭 Fingerprint: \$FINGERPRINT

🔗 VLESS Link:
\$VLESS_LINK
EOF

if [ "\$HY2_ENABLE" = "true" ]; then
    cat >> \$HOME_DIR/vless_client_config.txt << EOF

━━━ Hysteria2 (UDP) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Server:      \$SERVER_IP
🔌 Port:        \$HY2_PORT (UDP)
🔑 Password:    \$HY2_PASSWORD

🔗 Hysteria2 URI:
\$HY2_LINK
EOF
fi

cat >> \$HOME_DIR/vless_client_config.txt << EOF

═══════════════════════════════════════════════════════════════
EOF

# Save JSON config
cat > \$HOME_DIR/vless_client_config.json << EOF
{
  "server": "\$SERVER_IP",
  "port": \$PORT,
  "uuid": "\$UUID",
  "public_key": "\$PUBLIC_KEY",
  "short_id": "\$SHORT_ID",
  "sni": "\$SNI",
  "fingerprint": "\$FINGERPRINT",
  "vless_link": "\$VLESS_LINK",
  "hysteria2": {
    "enabled": \$([ "\$HY2_ENABLE" = "true" ] && echo "true" || echo "false"),
    "port": \$HY2_PORT,
    "password": "\${HY2_PASSWORD:-}",
    "hy2_link": "\${HY2_LINK:-}"
  }
}
EOF

echo ""
echo -e "\${GREEN}═══════════════════════════════════════════════════════════════\${NC}"
echo -e "\${GREEN}   ✅ Install finished!\${NC}"
echo -e "\${GREEN}═══════════════════════════════════════════════════════════════\${NC}"
echo ""
echo "📄 Config saved to: \$HOME_DIR/vless_client_config.txt"
echo ""
cat \$HOME_DIR/vless_client_config.txt
SCRIPT_EOF
}

create_full_install_script() {
    # Escape variables that must be interpolated
    cat << SCRIPT_EOF
#!/bin/bash
# Full install: Docker + TelegramHelper + Xray + Hysteria2

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

BOT_TOKEN="$BOT_TOKEN"
ADMIN_ID="$ADMIN_ID"
SSH_USER="$SSH_USER"
SSH_PORT_FOR_UFW="$SSH_PORT"
HOME_DIR="\$HOME"
ANTHROPIC_KEY="$ANTHROPIC_KEY"
OPENAI_KEY="$OPENAI_KEY"
NGINX_ENABLE="$NGINX_ENABLE"
NGINX_DOMAIN="$NGINX_DOMAIN"
NGINX_EMAIL="$NGINX_EMAIL"
NGINX_HTTPS_PORT="$NGINX_HTTPS_PORT"
NGINX_UPSTREAM_HOST="$NGINX_UPSTREAM_HOST"
NGINX_UPSTREAM_PORT="$NGINX_UPSTREAM_PORT"
VLESS_PORT="$VLESS_PORT"
HY2_ENABLE="$HY2_ENABLE"
HY2_PORT="$HY2_PORT"
HY2_PASSWORD="$HY2_PASSWORD"
HEADSCALE_ENABLE="$HEADSCALE_ENABLE"
HEADSCALE_DOMAIN="$HEADSCALE_DOMAIN"
HA_DOMAIN="$HA_DOMAIN"

# Detect whether sudo is needed
if [ "\$(id -u)" -ne 0 ]; then
    SUDO="sudo"
    echo -e "\${YELLOW}⚠️ Running as a regular user, using sudo\${NC}"
else
    SUDO=""
fi

# Step counter
TOTAL_STEPS=9
if [ "\$HY2_ENABLE" = "true" ]; then
    TOTAL_STEPS=11
fi
STEP=0
next_step() { STEP=\$((STEP+1)); echo -e "\${YELLOW}[\${STEP}/\${TOTAL_STEPS}] \$1\${NC}"; }

echo -e "\${GREEN}=== Full VLESS-Reality + Hysteria2 + TelegramHelper install ===\${NC}"

# 1. System update
next_step "Updating the system..."
\$SUDO apt-get update -qq
\$SUDO apt-get upgrade -y -qq

# 2. Base packages + security tools
next_step "Installing packages..."
\$SUDO apt-get install -y -qq curl jq openssl ca-certificates qrencode git python3 python3-pip ufw fail2ban unattended-upgrades

# 3. Install Docker
next_step "Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | \$SUDO sh
    \$SUDO systemctl enable docker
    \$SUDO systemctl start docker
    if [ "\$(id -u)" -ne 0 ]; then
        \$SUDO usermod -aG docker \$USER
        echo -e "\${YELLOW}⚠️ User added to the docker group. Re-login required.\${NC}"
    fi
else
    echo "Docker is already installed"
fi

# 4. Install Xray
next_step "Installing Xray-core..."
if ! command -v xray &> /dev/null; then
    \$SUDO bash -c "\$(curl -sL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
else
    echo "Xray is already installed"
fi

# 5. Generate VLESS keys
next_step "Generating VLESS keys..."
UUID=\$(xray uuid)
X25519_OUTPUT=\$(/usr/local/bin/xray x25519 2>/dev/null)
PRIVATE_KEY=\$(echo "\$X25519_OUTPUT" | grep -i "private" | awk -F': ' '{print \$2}' | tr -d ' ')
PUBLIC_KEY=\$(echo "\$X25519_OUTPUT" | grep -i "public" | awk -F': ' '{print \$2}' | tr -d ' ')
SHORT_ID=\$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 8)
SERVER_IP=\$(curl -s https://api.ipify.org || curl -s https://ifconfig.me/ip)

if [ -z "\$PRIVATE_KEY" ] || [ -z "\$PUBLIC_KEY" ]; then
    echo -e "\${YELLOW}⚠️ Regenerating keys...\${NC}"
    X25519_OUTPUT=\$(/usr/local/bin/xray x25519)
    PRIVATE_KEY=\$(echo "\$X25519_OUTPUT" | head -1 | awk -F': ' '{print \$2}' | tr -d ' ')
    PUBLIC_KEY=\$(echo "\$X25519_OUTPUT" | tail -1 | awk -F': ' '{print \$2}' | tr -d ' ')
fi

echo "UUID: \$UUID"
echo "Private Key: [hidden; stored on server only]"
echo "Public Key: \${PUBLIC_KEY:0:10}..."

# Generate encryption keys
API_KEY=\$(python3 -c "import secrets; print(secrets.token_hex(32))")
HMAC_KEY=\$(openssl rand -hex 32)
ENC_KEY=\$(python3 -c "import secrets; print(secrets.token_hex(32))")

# yahoo.com by default: www.microsoft.com is blocked by DPI on some mobile operators
# Change later from the bot: /vless_set_sni
SNI="yahoo.com"
FINGERPRINT="chrome"
PORT=\$VLESS_PORT

# 6. Configure Xray + TelegramHelper
next_step "Configuring Xray and TelegramHelper..."
\$SUDO tee /usr/local/etc/xray/config.json > /dev/null << EOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "port": \$PORT,
    "protocol": "vless",
    "settings": {
      "clients": [{
        "id": "\$UUID",
        "flow": "xtls-rprx-vision"
      }],
      "decryption": "none"
    },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "show": false,
        "dest": "\${SNI}:443",
        "xver": 0,
        "serverNames": ["\$SNI"],
        "privateKey": "\$PRIVATE_KEY",
        "shortIds": ["\$SHORT_ID"]
      }
    }
  }],
  "outbounds": [{"protocol": "freedom", "tag": "direct"}]
}
EOF

\$SUDO xray -test -config /usr/local/etc/xray/config.json
\$SUDO systemctl enable xray
\$SUDO systemctl restart xray

# Prepare TelegramHelper
PROJECT_DIR="/opt/TelegramHelper"
\$SUDO mkdir -p \$PROJECT_DIR
if [ "\$(id -u)" -ne 0 ]; then
    \$SUDO chown -R \$USER:\$USER \$PROJECT_DIR
fi

# Create .env for the bot
cat > \$PROJECT_DIR/.env << EOF
BOT_TOKEN=\$BOT_TOKEN
ADMIN_USER_IDS=\$ADMIN_ID
API_SECRET_KEY=\$API_KEY
HMAC_SECRET=\$HMAC_KEY
ENCRYPTION_KEY=\$ENC_KEY
API_URL=http://\$SERVER_IP:8000/ai_query
EOF

# Add AI keys if provided
if [ -n "\$ANTHROPIC_KEY" ]; then
    echo "ANTHROPIC_API_KEY=\$ANTHROPIC_KEY" >> \$PROJECT_DIR/.env
    echo "DEFAULT_AI_PROVIDER=anthropic" >> \$PROJECT_DIR/.env
    echo "ANTHROPIC_MODEL=claude-3-5-sonnet-20241022" >> \$PROJECT_DIR/.env
fi
if [ -n "\$OPENAI_KEY" ]; then
    echo "OPENAI_API_KEY=\$OPENAI_KEY" >> \$PROJECT_DIR/.env
    if [ -z "\$ANTHROPIC_KEY" ]; then
        echo "DEFAULT_AI_PROVIDER=openai" >> \$PROJECT_DIR/.env
    fi
    echo "OPENAI_MODEL=gpt-4o" >> \$PROJECT_DIR/.env
fi

# Create vless_config.json
cat > \$PROJECT_DIR/vless_config.json << EOF
{
  "enabled": true,
  "server": "\$SERVER_IP",
  "port": \$PORT,
  "uuid": "\$UUID",
  "public_key": "\$PUBLIC_KEY",
  "private_key": "\$PRIVATE_KEY",
  "short_id": "\$SHORT_ID",
  "sni": "\$SNI",
  "fingerprint": "\$FINGERPRINT",
  "flow": "xtls-rprx-vision"
}
EOF

# Create app_keys.json (IMPORTANT: create BEFORE docker compose!)
cat > \$PROJECT_DIR/app_keys.json << EOF
{
  "app_keys": {
    "apiai-v3": {
      "api_key": "\$API_KEY",
      "encryption_key": "\$ENC_KEY"
    }
  },
  "default": {
    "api_key": "\$API_KEY",
    "encryption_key": "\$ENC_KEY"
  }
}
EOF

echo '{}' > \$PROJECT_DIR/users.json
chmod 600 \$PROJECT_DIR/app_keys.json \$PROJECT_DIR/users.json \$PROJECT_DIR/vless_config.json 2>/dev/null || true

# ── Hysteria2 Installation ──
if [ "\$HY2_ENABLE" = "true" ]; then
    next_step "Installing Hysteria2..."
    if ! command -v hysteria &> /dev/null; then
        bash <(curl -fsSL https://get.hy2.sh/)
    else
        echo "Hysteria2 is already installed"
    fi

    next_step "Configuring and starting Hysteria2..."

    # Generate Hysteria2 password
    if [ -z "\$HY2_PASSWORD" ]; then
        HY2_PASSWORD=\$(openssl rand -base64 16 | tr -d '=+/' | head -c 22)
    fi

    # TLS certificate
    HY2_CERT_DIR="/etc/hysteria"
    \$SUDO mkdir -p "\$HY2_CERT_DIR"
    if [ ! -f "\$HY2_CERT_DIR/server.crt" ] || [ ! -f "\$HY2_CERT_DIR/server.key" ]; then
        \$SUDO openssl req -x509 -nodes \
            -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
            -keyout "\$HY2_CERT_DIR/server.key" \
            -out "\$HY2_CERT_DIR/server.crt" \
            -subj "/CN=www.microsoft.com" \
            -days 36500 2>/dev/null
    fi

    # Hysteria2 config
    \$SUDO tee "\$HY2_CERT_DIR/config.yaml" > /dev/null << EOF
listen: :\$HY2_PORT

tls:
  cert: \$HY2_CERT_DIR/server.crt
  key: \$HY2_CERT_DIR/server.key

auth:
  type: password
  password: "\$HY2_PASSWORD"

masquerade:
  type: proxy
  proxy:
    url: https://www.microsoft.com
    rewriteHost: true
EOF

    # systemd service
    if [ ! -f /etc/systemd/system/hysteria-server.service ]; then
        \$SUDO tee /etc/systemd/system/hysteria-server.service > /dev/null << 'SVCEOF'
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
SVCEOF
    fi

    \$SUDO systemctl daemon-reload
    \$SUDO systemctl enable hysteria-server
    \$SUDO systemctl restart hysteria-server
    sleep 2

    if systemctl is-active --quiet hysteria-server; then
        echo -e "\${GREEN}✅ Hysteria2 started on UDP port \$HY2_PORT\${NC}"
    else
        echo -e "\${YELLOW}⚠️ Hysteria2 failed to start, check: journalctl -u hysteria-server -n 20\${NC}"
    fi

    HY2_LINK="hy2://\${HY2_PASSWORD}@\${SERVER_IP}:\${HY2_PORT}/?insecure=1#Hysteria2"

    # Create hysteria2_config.json for TelegramHelper
    cat > \$PROJECT_DIR/hysteria2_config.json << EOF
{
  "enabled": true,
  "server": "\$SERVER_IP",
  "port": \$HY2_PORT,
  "password": "\$HY2_PASSWORD",
  "sni": "www.microsoft.com",
  "insecure": true,
  "up_mbps": 0,
  "down_mbps": 0,
  "obfs_type": "",
  "obfs_password": "",
  "cert_path": "/etc/hysteria/server.crt",
  "key_path": "/etc/hysteria/server.key",
  "masquerade": "https://www.microsoft.com",
  "clients": []
}
EOF
    chmod 600 \$PROJECT_DIR/hysteria2_config.json 2>/dev/null || true
fi

# Nginx + SSL (optional)
if [ "\$NGINX_ENABLE" = "true" ]; then
    next_step "Installing Nginx + SSL..."
    \$SUDO apt-get install -y -qq nginx certbot
    \$SUDO systemctl enable nginx
    \$SUDO systemctl start nginx

    WEBROOT="/var/www/certbot"
    \$SUDO mkdir -p "\$WEBROOT"

    if [ -z "\$NGINX_HTTPS_PORT" ]; then
        if [ "\$PORT" = "443" ]; then
            NGINX_HTTPS_PORT="8443"
            echo -e "\${YELLOW}⚠️ VLESS uses 443, Nginx will listen on 8443\${NC}"
        else
            NGINX_HTTPS_PORT="443"
        fi
    fi

    \$SUDO tee /etc/nginx/sites-available/telegramsimple.conf > /dev/null << EOF_NGX
server {
    listen 80;
    server_name \$NGINX_DOMAIN;

    location /.well-known/acme-challenge/ {
        root \$WEBROOT;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen \$NGINX_HTTPS_PORT ssl http2;
    server_name \$NGINX_DOMAIN;

    ssl_certificate /etc/letsencrypt/live/\$NGINX_DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/\$NGINX_DOMAIN/privkey.pem;

    client_max_body_size 20m;

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_pass http://\$NGINX_UPSTREAM_HOST:\$NGINX_UPSTREAM_PORT;
    }
}
EOF_NGX

    \$SUDO ln -sf /etc/nginx/sites-available/telegramsimple.conf /etc/nginx/sites-enabled/telegramsimple.conf
    \$SUDO nginx -t
    \$SUDO systemctl reload nginx

    \$SUDO certbot certonly --webroot -w "\$WEBROOT" -d "\$NGINX_DOMAIN" -m "\$NGINX_EMAIL" --agree-tos --non-interactive
    \$SUDO systemctl reload nginx
fi

# Security Hardening
next_step "Configuring security..."

# UFW Firewall
\$SUDO ufw default deny incoming
\$SUDO ufw default allow outgoing
\$SUDO ufw allow \$SSH_PORT_FOR_UFW/tcp   # SSH
\$SUDO ufw allow \$PORT/tcp               # VLESS (TCP)
\$SUDO ufw allow 8000/tcp                 # API
if [ "\$HY2_ENABLE" = "true" ]; then
    \$SUDO ufw allow \$HY2_PORT/udp         # Hysteria2 (UDP)
fi
if [ "\$NGINX_ENABLE" = "true" ]; then
    \$SUDO ufw allow 80/tcp
    \$SUDO ufw allow \$NGINX_HTTPS_PORT/tcp
fi
\$SUDO ufw --force enable

# Fail2ban
\$SUDO tee /etc/fail2ban/jail.local > /dev/null << JAILEOF
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3

[sshd]
enabled = true
port = \$SSH_PORT_FOR_UFW
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 24h
JAILEOF
\$SUDO systemctl enable fail2ban
\$SUDO systemctl restart fail2ban

# Unattended upgrades
echo 'APT::Periodic::Update-Package-Lists "1";' | \$SUDO tee /etc/apt/apt.conf.d/20auto-upgrades > /dev/null
echo 'APT::Periodic::Unattended-Upgrade "1";' | \$SUDO tee -a /etc/apt/apt.conf.d/20auto-upgrades > /dev/null

# Kernel hardening
\$SUDO tee -a /etc/sysctl.conf > /dev/null << 'SYSEOF'
# Security hardening
net.ipv4.conf.all.rp_filter = 1
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
SYSEOF
\$SUDO sysctl -p 2>/dev/null || true

echo -e "\${GREEN}✅ UFW, Fail2ban, and unattended upgrades configured\${NC}"

# Finalize
next_step "Finalizing..."

VLESS_LINK="vless://\${UUID}@\${SERVER_IP}:\${PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=\${SNI}&fp=\${FINGERPRINT}&pbk=\${PUBLIC_KEY}&sid=\${SHORT_ID}&type=tcp#VPS-Reality"

# Client config (text)
cat > \$HOME_DIR/vless_client_config.txt << EOF
═══════════════════════════════════════════════════════════════
       🛡️  VLESS-Reality + ⚡ Hysteria2 — Client Config
═══════════════════════════════════════════════════════════════

━━━ VLESS-Reality (TCP) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Server:      \$SERVER_IP
🔌 Port:        \$PORT (TCP)
🆔 UUID:        \$UUID
🔑 Public Key:  \$PUBLIC_KEY
🏷️ Short ID:    \$SHORT_ID
🌐 SNI:         \$SNI
🎭 Fingerprint: \$FINGERPRINT

🔗 VLESS Link:
\$VLESS_LINK
EOF

if [ "\$HY2_ENABLE" = "true" ]; then
    cat >> \$HOME_DIR/vless_client_config.txt << EOF

━━━ Hysteria2 (UDP) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Server:      \$SERVER_IP
🔌 Port:        \$HY2_PORT (UDP)
🔑 Password:    \$HY2_PASSWORD

🔗 Hysteria2 URI:
\$HY2_LINK
EOF
fi

cat >> \$HOME_DIR/vless_client_config.txt << EOF

═══════════════════════════════════════════════════════════════
🔐 API Keys (for TelegramHelper):

API_SECRET_KEY: \$API_KEY
ENCRYPTION_KEY: \$ENC_KEY
HMAC_SECRET:    \$HMAC_KEY

🩺 Dockhand (Diagnostics):
SSH Tunnel: ssh -L 8501:localhost:8501 -p \$SSH_PORT_FOR_UFW \$SSH_USER@\$SERVER_IP
URL:        http://localhost:8501

═══════════════════════════════════════════════════════════════
EOF

# Client config (JSON)
cat > \$HOME_DIR/vless_client_config.json << EOF
{
  "server": "\$SERVER_IP",
  "port": \$PORT,
  "uuid": "\$UUID",
  "public_key": "\$PUBLIC_KEY",
  "short_id": "\$SHORT_ID",
  "sni": "\$SNI",
  "fingerprint": "\$FINGERPRINT",
  "vless_link": "\$VLESS_LINK",
  "api_secret_key": "\$API_KEY",
  "encryption_key": "\$ENC_KEY",
  "hmac_secret": "\$HMAC_KEY",
  "hysteria2": {
    "enabled": \$([ "\$HY2_ENABLE" = "true" ] && echo "true" || echo "false"),
    "port": \$HY2_PORT,
    "password": "\${HY2_PASSWORD:-}",
    "hy2_link": "\${HY2_LINK:-}"
  }
}
EOF

# === HEADSCALE (self-hosted Tailscale) ===
if [ "\$HEADSCALE_ENABLE" = "true" ]; then
    echo -e "\${GREEN}[Headscale] Installing Headscale...\${NC}"

    # Create directories
    mkdir -p /opt/headscale/config /opt/headscale/data

    # Download default config
    curl -sL https://raw.githubusercontent.com/juanfont/headscale/main/config-example.yaml \
        -o /opt/headscale/config/config.yaml

    # Patch config with domain
    sed -i "s|server_url:.*|server_url: https://\$HEADSCALE_DOMAIN|" /opt/headscale/config/config.yaml
    sed -i "s|listen_addr:.*|listen_addr: 0.0.0.0:8080|" /opt/headscale/config/config.yaml

    # Start Headscale container
    docker run -d --name headscale \
        --restart always \
        -v /opt/headscale/config:/etc/headscale \
        -v /opt/headscale/data:/var/lib/headscale \
        -p 127.0.0.1:8080:8080 \
        headscale/headscale:latest serve

    # Wait for startup
    sleep 5

    # Create default user
    docker exec headscale headscale users create main_user 2>/dev/null || true
    echo -e "\${GREEN}[Headscale] ✅ Headscale started\${NC}"

    # Configure Nginx SNI routing if Nginx is enabled
    if [ "\$NGINX_ENABLE" = "true" ]; then
        echo -e "\${GREEN}[Headscale] Configuring Nginx SNI routing...\${NC}"

        # Install stream module
        \$SUDO apt-get install -y -qq libnginx-mod-stream 2>/dev/null || true

        # Nginx stream SNI config
        cat > /etc/nginx/conf.d/stream_sni.conf << 'NGINX_SNI_EOF'
stream {
    map \\\$ssl_preread_server_name \\\$backend {
        \$HEADSCALE_DOMAIN  headscale_backend;
NGINX_SNI_EOF

        if [ -n "\$HA_DOMAIN" ]; then
            echo "        \$HA_DOMAIN         ha_backend;" >> /etc/nginx/conf.d/stream_sni.conf
        fi

        cat >> /etc/nginx/conf.d/stream_sni.conf << 'NGINX_SNI_EOF2'
        default              api_backend;
    }
    upstream headscale_backend { server 127.0.0.1:8080; }
NGINX_SNI_EOF2

        if [ -n "\$HA_DOMAIN" ]; then
            echo "    upstream ha_backend        { server 127.0.0.1:8123; }" >> /etc/nginx/conf.d/stream_sni.conf
        fi

        cat >> /etc/nginx/conf.d/stream_sni.conf << 'NGINX_SNI_EOF3'
    upstream api_backend       { server 127.0.0.1:8000; }
    server {
        listen 8443;
        listen [::]:8443;
        proxy_pass \\\$backend;
        ssl_preread on;
    }
}
NGINX_SNI_EOF3

        # HTTP vhost for Certbot
        cat > /etc/nginx/sites-available/headscale.conf << NGINX_HS_EOF
server {
    listen 80;
    server_name \$HEADSCALE_DOMAIN;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \\\$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \\\$host;
    }
}
NGINX_HS_EOF
        ln -sf /etc/nginx/sites-available/headscale.conf /etc/nginx/sites-enabled/
        nginx -t && systemctl reload nginx

        # Certbot for Headscale domain
        if [ -n "\$NGINX_EMAIL" ]; then
            certbot --nginx -d \$HEADSCALE_DOMAIN --non-interactive --agree-tos -m \$NGINX_EMAIL 2>/dev/null || true
        fi
        echo -e "\${GREEN}[Headscale] ✅ Nginx SNI routing configured\${NC}"
    fi

    # Save Headscale info
    cat > \$HOME_DIR/headscale_info.txt << HS_INFO_EOF
=== Headscale Info ===
URL: https://\$HEADSCALE_DOMAIN
Container: headscale

Generate Pre-Auth key:
  docker exec headscale headscale users list
  docker exec headscale headscale preauthkeys create --user 1 --reusable --expiration 24h
  # --user = numeric ID from users list, not username

Connect client:
  tailscale up --login-server https://\$HEADSCALE_DOMAIN --authkey <KEY>
HS_INFO_EOF
    echo -e "\${GREEN}[Headscale] Info saved to \$HOME_DIR/headscale_info.txt\${NC}"
fi

echo ""
echo -e "\${GREEN}═══════════════════════════════════════════════════════════════\${NC}"
echo -e "\${GREEN}   ✅ Full install finished!\${NC}"
echo -e "\${GREEN}═══════════════════════════════════════════════════════════════\${NC}"
echo ""
echo "📄 Config: \$HOME_DIR/vless_client_config.txt"
echo "📁 Project: \$PROJECT_DIR"
echo ""
echo "Next steps:"
echo "1. Copy the TelegramHelper project to \$PROJECT_DIR"
echo "2. Run: cd \$PROJECT_DIR && docker compose up -d --build"
echo ""
cat \$HOME_DIR/vless_client_config.txt
SCRIPT_EOF
}

download_config() {
    echo ""
    echo -e "${BLUE}📥 Downloading config from the server...${NC}"

    # Create the directory
    mkdir -p "$OUTPUT_DIR"

    # Resolve home directory on the server
    REMOTE_HOME=$(ssh_cmd 'echo $HOME')

    # Download text config
    scp_cmd "$SSH_USER@$SSH_HOST:${REMOTE_HOME}/vless_client_config.txt" "$OUTPUT_DIR/vless_config_${SSH_HOST}.txt"

    # Download JSON config
    scp_cmd "$SSH_USER@$SSH_HOST:${REMOTE_HOME}/vless_client_config.json" "$OUTPUT_DIR/vless_config_${SSH_HOST}.json"

    echo -e "${GREEN}✅ Config saved to:${NC}"
    echo "   📄 $OUTPUT_DIR/vless_config_${SSH_HOST}.txt"
    echo "   📄 $OUTPUT_DIR/vless_config_${SSH_HOST}.json"
    echo ""

    # Suggest encrypting
    echo -e "${YELLOW}💡 For a safer transfer, encrypt the config:${NC}"
    echo "   python3 scripts/secure_config_transfer.py encrypt $OUTPUT_DIR/vless_config_${SSH_HOST}.json"
}

print_final_summary() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}   🎉 All done!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${CYAN}Installed protocols:${NC}"
    echo "  🛡️  VLESS-Reality  — TCP port $VLESS_PORT"
    if [[ "$HY2_ENABLE" == "true" ]]; then
        echo "  ⚡ Hysteria2      — UDP port $HY2_PORT"
    fi
    if [[ "$WITH_MAINTENANCE" == "true" ]]; then
        echo "  🧹 Auto-cleanup   — systemd timer (Sun 04:00 UTC)"
    fi
    echo ""
    echo "You can now:"
    echo "1. Import the links into an app (Hiddify, v2rayNG, NekoRay)"
    echo "2. Use the config from the file"
    echo ""

    local SSH_CMD="ssh"
    if [[ "$SSH_PORT" != "22" ]]; then
        SSH_CMD="ssh -p $SSH_PORT"
    fi
    SSH_CMD="$SSH_CMD $SSH_USER@$SSH_HOST"

    if [[ "$INSTALL_MODE" == "full" ]]; then
        echo "To start the bot on the server:"
        echo "  $SSH_CMD"
        echo "  cd /opt/TelegramHelper && docker compose up -d --build"
        echo ""
        echo "Available bot commands:"
        echo "  /vless_status — VLESS-Reality status"
        if [[ "$HY2_ENABLE" == "true" ]]; then
            echo "  /hy2_status   — Hysteria2 status"
        fi
    fi
}

install_maintenance() {
    if [[ "$WITH_MAINTENANCE" != "true" ]]; then
        echo -e "${YELLOW}⚠️  Disk auto-cleanup disabled (--no-maintenance)${NC}"
        return 0
    fi

    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local local_script="$script_dir/vps_maintenance.sh"

    if [[ ! -f "$local_script" ]]; then
        echo -e "${YELLOW}⚠️  $local_script not found — skipping auto-cleanup install${NC}"
        return 0
    fi

    echo ""
    echo -e "${BLUE}🧹 Installing disk auto-cleanup (vps_maintenance.sh)...${NC}"

    if ! scp_cmd "$local_script" "$SSH_USER@$SSH_HOST:/tmp/vps_maintenance.sh" &> /dev/null; then
        echo -e "${YELLOW}⚠️  Could not copy vps_maintenance.sh — skipping${NC}"
        return 0
    fi

    # Script requires root; if SSH_USER is not root — try via sudo
    ssh_cmd "chmod +x /tmp/vps_maintenance.sh && \
        if [ \$(id -u) -eq 0 ]; then bash /tmp/vps_maintenance.sh --install; \
        else sudo bash /tmp/vps_maintenance.sh --install; fi" \
        || echo -e "${YELLOW}⚠️  Auto-cleanup install failed (non-critical; install manually: ./scripts/vps_maintenance.sh --install)${NC}"
}

cleanup() {
    echo -e "${BLUE}🧹 Cleaning temporary files on the server...${NC}"
    ssh_cmd "rm -f /tmp/install_vless.sh /tmp/vps_maintenance.sh" 2>/dev/null || true
}

main() {
    print_banner
    parse_args "$@"
    validate_args
    check_dependencies
    test_connection
    run_installation
    install_maintenance
    download_config
    cleanup
    print_final_summary
}

# Handle Ctrl+C
trap cleanup EXIT

main "$@"

