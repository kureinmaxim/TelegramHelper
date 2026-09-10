#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🚀 DEPLOY FRESH VPS — Full mode (1GB RAM optimized)
# ═══════════════════════════════════════════════════════════════
# Target: Debian 12, 1 vCPU, 1GB RAM, 10GB Disk
# Stack:  Docker + TelegramHelper + [legacy VLESS | 3x-ui VLESS | NaiveProxy | Mieru] + Hysteria2 + MTProto
#
# Usage:
#   1. First login to a fresh VPS via the provider default SSH port
#   2. Run: bash deploy_fresh_vps.sh
#   3. After the server stage, use SSH port YOUR_SSH_PORT for new connections
# ═══════════════════════════════════════════════════════════════

set -e
umask 077

SERVER_IP="${SERVER_IP:-}"
SSH_PORT="22"
HY2_PORT="443"
MIERU_PORT="29999"
MIERU_PROTOCOL="tcp"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

STEP=0
TOTAL=12
next() { STEP=$((STEP+1)); echo -e "\n${CYAN}[$STEP/$TOTAL] $1${NC}"; }

# ── Protocol selection dialog ────────────────────────────────
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  🚀 TelegramHelper VPS Deploy — Phase 1                ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${BOLD}What to install as the main transport stack?${NC}"
echo ""
echo -e "  ${CYAN}[1]${NC} VLESS-Reality legacy xray.service     — no 3x-ui, the bot owns Xray"
echo -e "  ${CYAN}[2]${NC} VLESS-Reality via 3x-ui + x-ui menu   — terminal panel menu"
echo -e "  ${CYAN}[3]${NC} VLESS-Reality via 3x-ui browser UI    — browser panel"
echo -e "  ${CYAN}[4]${NC} NaiveProxy (Caddy)                    — HTTPS proxy, needs a domain"
echo -e "  ${CYAN}[5]${NC} Mieru (mita)                           — dedicated TCP/UDP port, default 29999/tcp"
echo ""
echo -e "${YELLOW}Note: 1/2/3/4 own TCP/443. Mieru does NOT take 443 by default.${NC}"
echo ""
read -rp "Choice [1/2/3/4/5] (Enter = 1): " PROTO_CHOICE
PROTO_CHOICE="${PROTO_CHOICE:-1}"
XUI_ACCESS_MODE=""

case "$PROTO_CHOICE" in
    1)
        PROTOCOL="vless"
        echo ""
        echo -e "${YELLOW}Legacy host-Xray will be installed without 3x-ui.${NC}"
        echo -e "${YELLOW}/provision, /profiles, /my_profile will work via vless_config.json + xray.service.${NC}"
        ;;
    2)
        PROTOCOL="xui"
        XUI_ACCESS_MODE="terminal"
        echo ""
        echo -e "${YELLOW}The official interactive 3x-ui installer will run.${NC}"
        echo -e "${YELLOW}Primary panel control: the x-ui command in the terminal.${NC}"
        echo -e "${YELLOW}After install, create a VLESS-Reality inbound and run /xui_setup in the bot.${NC}"
        ;;
    3)
        PROTOCOL="xui"
        XUI_ACCESS_MODE="browser"
        echo ""
        echo -e "${YELLOW}The official interactive 3x-ui installer will run.${NC}"
        echo -e "${YELLOW}Primary panel control: the 3x-ui browser URL.${NC}"
        echo -e "${YELLOW}After install, create a VLESS-Reality inbound and run /xui_setup in the bot.${NC}"
        ;;
    4)
        PROTOCOL="naiveproxy"
        echo ""
        echo -e "${YELLOW}NaiveProxy needs a domain with a DNS A record pointing at this server.${NC}"
        echo -e "${YELLOW}Example: naive.example.com → $(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP')${NC}"
        echo ""
        read -rp "Domain for NaiveProxy (e.g. naive.example.com): " NAIVE_DOMAIN
        if [[ -z "$NAIVE_DOMAIN" ]]; then
            echo -e "${RED}❌ Domain is required for NaiveProxy. Use option 1/2/3 for VLESS or 5 for Mieru without a domain.${NC}"
            exit 1
        fi
        NAIVE_PORT="443"
        echo -e "${YELLOW}⚠️  Building Caddy with the plugin takes 3-5 minutes (Go compile).${NC}"
        ;;
    5)
        PROTOCOL="mieru"
        echo ""
        echo -e "${YELLOW}Mieru will be installed as mita on a dedicated port.${NC}"
        echo -e "${YELLOW}Default: ${MIERU_PORT}/${MIERU_PROTOCOL}. This does not clash with 443/tcp VLESS/NaiveProxy or 443/udp Hysteria2.${NC}"
        read -rp "Mieru port (Enter = ${MIERU_PORT}): " MIERU_PORT_INPUT
        MIERU_PORT="${MIERU_PORT_INPUT:-$MIERU_PORT}"
        read -rp "Mieru protocol [tcp/udp] (Enter = tcp): " MIERU_PROTOCOL_INPUT
        MIERU_PROTOCOL="${MIERU_PROTOCOL_INPUT:-tcp}"
        MIERU_PROTOCOL="${MIERU_PROTOCOL,,}"
        if ! [[ "$MIERU_PORT" =~ ^[0-9]+$ ]] || [ "$MIERU_PORT" -lt 1025 ] || [ "$MIERU_PORT" -gt 65535 ]; then
            echo -e "${RED}❌ Mieru port must be a number 1025..65535.${NC}"
            exit 1
        fi
        if [[ "$MIERU_PROTOCOL" != "tcp" && "$MIERU_PROTOCOL" != "udp" ]]; then
            echo -e "${RED}❌ Mieru protocol must be tcp or udp.${NC}"
            exit 1
        fi
        if [[ "$MIERU_PORT" = "443" ]]; then
            echo -e "${RED}❌ Do not use 443 for Mieru on a fresh install: it clashes with VLESS/NaiveProxy/Hysteria2.${NC}"
            exit 1
        fi
        ;;
    *)
        echo -e "${RED}❌ Invalid choice: $PROTO_CHOICE${NC}"
        exit 1
        ;;
esac

# ── Deployment target dialog ─────────────────────────────────
# This script does not run `docker compose up` (project code is not on the server yet).
# The choice only affects the final hints and CREDENTIALS.txt.
echo ""
echo -e "${BOLD}What will you bring up after copying the code?${NC}"
echo ""
echo -e "  ${CYAN}[1]${NC} Bot only (telegram-helper)                 — standard option"
echo -e "  ${CYAN}[2]${NC} Dockhand only (diagnostics)                — add the panel to a running bot"
echo -e "  ${CYAN}[3]${NC} Both containers (telegram-helper + dockhand) — full stack ${YELLOW}(recommended)${NC}"
echo ""
read -rp "Choice [1/2/3] (Enter = 3): " DEPLOY_CHOICE
DEPLOY_CHOICE="${DEPLOY_CHOICE:-3}"

case "$DEPLOY_CHOICE" in
    1)
        DEPLOY_TARGET="bot"
        DEPLOY_TARGET_HUMAN="telegram-helper only"
        DEPLOY_CMD="bash scripts/rebuild_bot.sh"
        ;;
    2)
        DEPLOY_TARGET="dockhand"
        DEPLOY_TARGET_HUMAN="dockhand only"
        DEPLOY_CMD="docker compose up -d --build dockhand"
        echo ""
        echo -e "${YELLOW}⚠️  Dockhand hard-codes container 'telegram-helper-lite'${NC}"
        echo -e "${YELLOW}   and API http://telegram-helper:8000/health. Without the bot the UI${NC}"
        echo -e "${YELLOW}   will show 'Container Not Found' / 'API Unreachable'.${NC}"
        echo -e "${YELLOW}   See DOCKHAND_SETUP.md §4.B.2 — how to run it alone.${NC}"
        ;;
    *)
        DEPLOY_TARGET="both"
        DEPLOY_TARGET_HUMAN="telegram-helper + dockhand"
        DEPLOY_CMD="bash scripts/rebuild_bot.sh && docker compose up -d --build dockhand"
        ;;
esac

# Detect server IP if not set
if [[ -z "$SERVER_IP" ]]; then
    SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s api.ipify.org 2>/dev/null || echo "YOUR_SERVER_IP")
fi

echo ""
case "$PROTOCOL" in
    xui)
        if [ "$XUI_ACCESS_MODE" = "terminal" ]; then
            PROTOCOL_HUMAN="VLESS-Reality via 3x-ui (x-ui terminal menu)"
        else
            PROTOCOL_HUMAN="VLESS-Reality via 3x-ui (browser panel)"
        fi
        ;;
    vless) PROTOCOL_HUMAN="VLESS-Reality legacy host-Xray" ;;
    naiveproxy) PROTOCOL_HUMAN="NaiveProxy" ;;
    mieru) PROTOCOL_HUMAN="Mieru (mita ${MIERU_PORT}/${MIERU_PROTOCOL})" ;;
    *) PROTOCOL_HUMAN="$PROTOCOL" ;;
esac

echo -e "${GREEN}  Protocol: $PROTOCOL_HUMAN${NC}"
echo -e "${GREEN}  IP:        $SERVER_IP${NC}"
[ "$PROTOCOL" = "naiveproxy" ] && echo -e "${GREEN}  Domain:    $NAIVE_DOMAIN${NC}"
[ "$PROTOCOL" = "mieru" ] && echo -e "${GREEN}  Mieru:     ${MIERU_PORT}/${MIERU_PROTOCOL}${NC}"
echo -e "${GREEN}  Deploy:    $DEPLOY_TARGET_HUMAN${NC}"
echo ""

# ── 1. Swap (critical for 1GB RAM) ──────────────────────────
next "Creating 1GB swap..."
if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' >> /etc/sysctl.conf
    sysctl vm.swappiness=10
    echo -e "${GREEN}✅ Swap 1GB created${NC}"
else
    echo "Swap already exists"
    swapon --show
fi

# ── 2. Disable IPv6 (close VPN-egress leak) ─────────────────
# Without this a dual-stack VPS egresses over IPv6, and the client's geo-locator
# sees the server IPv6 → ChatGPT/Spotify/banks decide the client is in the
# wrong country (sing-box client: "IPv6 LEAK DETECTED" pill).
# Revert: rm /etc/sysctl.d/99-disable-ipv6.conf && sysctl --system.
next "Disabling IPv6 (region egress-leak protection)..."
if [ ! -f /etc/sysctl.d/99-disable-ipv6.conf ]; then
    cat > /etc/sysctl.d/99-disable-ipv6.conf <<'EOF'
# IPv6 disabled by deploy_fresh_vps.sh — closes egress leak that lets
# geo-locators see the VPS's IPv6 instead of the tunnel's IPv4 egress.
# To re-enable: rm this file && sysctl --system && restart transports.
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
EOF
    sysctl --system >/dev/null 2>&1 || true
    echo -e "${GREEN}✅ IPv6 disabled system-wide (/etc/sysctl.d/99-disable-ipv6.conf)${NC}"
else
    echo "IPv6 already disabled (/etc/sysctl.d/99-disable-ipv6.conf exists)"
fi

# ── 3. System update ────────────────────────────────────────
next "Updating the system..."
apt-get update -qq
apt-get upgrade -y -qq

# ── 4. Install packages ─────────────────────────────────────
next "Installing packages..."
PKGS="curl jq openssl ca-certificates qrencode git python3 python3-pip ufw fail2ban unattended-upgrades"
if [ "$PROTOCOL" = "naiveproxy" ]; then
    PKGS="$PKGS golang-go"
fi
apt-get install -y -qq $PKGS

# ── 5. Install Docker ───────────────────────────────────────
next "Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}✅ Docker installed${NC}"
else
    echo "Docker already installed: $(docker --version)"
fi

# ── 5b. Pre-flight: TCP 443 must be free OR already held by our stack ──
# Goal: do not spend minutes building Caddy if :443 is already held by 3x-ui/nginx/…
# On a re-run of this script on the same VPS the port is often already listening
# as our `xray` or `caddy-naive` — then do not block (see below).
preflight_tcp443_for_protocol_install() {
    if ! command -v ss >/dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  ss is unavailable — skipping pre-flight :443/tcp.${NC}"
        return 0
    fi
    # LISTEN on TCP :443 (IPv4 *:443 / 0.0.0.0:443 or IPv6 [::]:443)
    if ! ss -ltn 2>/dev/null | awk '$1 == "LISTEN" && $4 ~ /:443$/ { f = 1 } END { exit !f }'; then
        return 0
    fi
    # Something is listening on :443 — allow a re-run only if it is the
    # service of the selected mode. Otherwise we might accidentally install a second Xray.
    if [ "$PROTOCOL" = "vless" ] && systemctl is-active --quiet xray 2>/dev/null; then
        echo -e "${YELLOW}⚠️  :443/tcp is already held by xray — treating as legacy VLESS and continuing (re-run?).${NC}"
        ss -ltn 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:443$/ {print}' || true
        return 0
    fi
    if [ "$PROTOCOL" = "xui" ] && systemctl is-active --quiet x-ui 2>/dev/null; then
        echo -e "${YELLOW}⚠️  :443/tcp is already held by x-ui — treating as 3x-ui mode and continuing (re-run?).${NC}"
        ss -ltn 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:443$/ {print}' || true
        return 0
    fi
    if [ "$PROTOCOL" = "naiveproxy" ] && systemctl is-active --quiet caddy-naive 2>/dev/null; then
        echo -e "${YELLOW}⚠️  :443/tcp is already held by caddy-naive — treating as NaiveProxy and continuing (re-run?).${NC}"
        ss -ltn 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:443$/ {print}' || true
        return 0
    fi
    echo -e "${RED}❌ TCP port 443 is already taken by a foreign process.${NC}"
    echo -e "${YELLOW}Current :443 listeners:${NC}"
    ss -ltnp 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:443$/ {print}' || ss -ltnp 2>/dev/null | grep ':443' || true
    echo ""
    echo -e "${YELLOW}Free the port (stop 3x-ui/nginx/another Xray, etc.) or use another VPS.${NC}"
    echo -e "${YELLOW}Hint: ss -ltnp | grep 443${NC}"
    exit 1
}
if [[ "$PROTOCOL" =~ ^(vless|xui|naiveproxy)$ ]]; then
    preflight_tcp443_for_protocol_install
else
    echo -e "${YELLOW}Mieru selected on ${MIERU_PORT}/${MIERU_PROTOCOL}: TCP/443 pre-flight skipped.${NC}"
fi

# ── 6. Install primary protocol ──────────────────────────────
if [ "$PROTOCOL" = "xui" ]; then

    next "Installing 3x-ui panel for VLESS-Reality..."
    echo -e "${YELLOW}The official 3x-ui installer is interactive: set panel port, webBasePath, login and password.${NC}"
    echo -e "${YELLOW}Do not use 443 as the panel port: 443 is needed for the VLESS Xray inbound.${NC}"
    echo -e "${YELLOW}After install both interfaces are available: the x-ui terminal command and the browser panel.${NC}"
    if systemctl list-unit-files 2>/dev/null | grep -q '^x-ui\.service'; then
        echo "3x-ui already installed: x-ui.service found"
        systemctl enable x-ui || true
        systemctl restart x-ui || true
    else
        bash <(curl -Ls https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh)
    fi
    echo -e "${GREEN}✅ 3x-ui installer finished${NC}"
    echo -e "${YELLOW}Next in the panel: create a VLESS-Reality inbound on TCP/443.${NC}"
    echo -e "${YELLOW}After the bot starts, run: /xui_setup → /xui_status → /provision <telegram_user_id>${NC}"

elif [ "$PROTOCOL" = "vless" ]; then

    next "Installing Xray-core + VLESS-Reality..."
    if ! command -v xray &> /dev/null; then
        bash -c "$(curl -sL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
        echo -e "${GREEN}✅ Xray installed${NC}"
    else
        echo "Xray already installed: $(xray version | head -1)"
    fi

    VLESS_PORT="443"
    UUID=$(xray uuid)
    X25519_OUTPUT=$(/usr/local/bin/xray x25519 2>/dev/null)
    PRIVATE_KEY=$(echo "$X25519_OUTPUT" | grep -i "private" | awk -F': ' '{print $2}' | tr -d ' ')
    PUBLIC_KEY=$(echo "$X25519_OUTPUT"  | grep -i "public"  | awk -F': ' '{print $2}' | tr -d ' ')
    SHORT_ID=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 8)
    # yahoo.com by default: www.microsoft.com is DPI-blocked by some mobile
    # operators (the Reality handshake never reaches Xray). Change later
    # from the bot: /vless_set_sni
    SNI="yahoo.com"
    FINGERPRINT="chrome"

    if [ -z "$PRIVATE_KEY" ] || [ -z "$PUBLIC_KEY" ]; then
        echo -e "${YELLOW}⚠️ Regenerating keys...${NC}"
        X25519_OUTPUT=$(/usr/local/bin/xray x25519)
        PRIVATE_KEY=$(echo "$X25519_OUTPUT" | head -1 | awk -F': ' '{print $2}' | tr -d ' ')
        PUBLIC_KEY=$(echo "$X25519_OUTPUT"  | tail -1 | awk -F': ' '{print $2}' | tr -d ' ')
    fi

    tee /usr/local/etc/xray/config.json > /dev/null << EOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "port": $VLESS_PORT,
    "protocol": "vless",
    "settings": {
      "clients": [{"id": "$UUID", "flow": "xtls-rprx-vision"}],
      "decryption": "none"
    },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "show": false,
        "dest": "${SNI}:443",
        "xver": 0,
        "serverNames": ["$SNI"],
        "privateKey": "$PRIVATE_KEY",
        "shortIds": ["$SHORT_ID"]
      }
    }
  }],
  "outbounds": [{"protocol": "freedom", "tag": "direct"}]
}
EOF

    xray -test -config /usr/local/etc/xray/config.json
    chmod o+w /usr/local/etc/xray/
    chmod o+w /usr/local/etc/xray/config.json
    systemctl enable xray
    systemctl restart xray
    echo -e "${GREEN}✅ Xray VLESS-Reality running on TCP/$VLESS_PORT${NC}"

elif [ "$PROTOCOL" = "naiveproxy" ]; then

    next "Building Caddy + NaiveProxy..."
    CADDY_BIN="/usr/local/bin/caddy-naive"
    CADDY_DIR="/etc/caddy-naive"
    SERVICE_NAME="caddy-naive"
    NAIVE_USERNAME="naive-$(tr -dc 'a-z0-9' </dev/urandom | head -c 6)"
    NAIVE_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
    NAIVE_EMAIL="admin@${NAIVE_DOMAIN}"

    export PATH="$PATH:/root/go/bin"
    if ! command -v xcaddy >/dev/null 2>&1; then
        go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
    fi

    if [[ ! -x "$CADDY_BIN" ]]; then
        tmpdir="$(mktemp -d)"
        pushd "$tmpdir" >/dev/null
        xcaddy build \
            --output "$CADDY_BIN" \
            --with github.com/caddyserver/forwardproxy=github.com/klzgrad/forwardproxy@naive
        popd >/dev/null
        rm -rf "$tmpdir"
    fi

    mkdir -p "$CADDY_DIR" /var/lib/${SERVICE_NAME}

    tee "${CADDY_DIR}/Caddyfile" > /dev/null << EOF
{
    email ${NAIVE_EMAIL}
    order forward_proxy before file_server
    auto_https disable_redirects
}

:${NAIVE_PORT}, ${NAIVE_DOMAIN} {
    log {
        output stdout
        level INFO
    }
    forward_proxy {
        basic_auth ${NAIVE_USERNAME} ${NAIVE_PASSWORD}
        hide_ip
        hide_via
        probe_resistance
    }
}
EOF

    tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null << EOF
[Unit]
Description=NaiveProxy via Caddy forwardproxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${CADDY_BIN} run --config ${CADDY_DIR}/Caddyfile --adapter caddyfile
ExecReload=${CADDY_BIN} reload --config ${CADDY_DIR}/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576
WorkingDirectory=/var/lib/${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"
    sleep 2

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "${GREEN}✅ NaiveProxy (Caddy) running on TCP/$NAIVE_PORT${NC}"
    else
        echo -e "${RED}❌ NaiveProxy failed to start!${NC}"
        journalctl -u "$SERVICE_NAME" -n 20
    fi

else  # mieru

    next "Installing Mieru server (mita)..."
    INSTALL_MIERU="/tmp/install_mieru.sh"
    curl -fsSL "https://raw.githubusercontent.com/your-github-user/TelegramHelper/main/scripts/install_mieru.sh" -o "$INSTALL_MIERU"
    chmod +x "$INSTALL_MIERU"
    bash "$INSTALL_MIERU" --port "$MIERU_PORT" --protocol "$MIERU_PROTOCOL"
    echo -e "${GREEN}✅ Mieru/mita installed. Port: ${MIERU_PORT}/${MIERU_PROTOCOL}${NC}"
    echo -e "${YELLOW}After the bot starts, run: /mieru_set_server $SERVER_IP → /mieru_set_port $MIERU_PORT $MIERU_PROTOCOL → /mieru_add_client phone → /mieru_apply → /mieru_start${NC}"

fi

# ── 7. Install & Configure Hysteria2 ────────────────────────
next "Installing and configuring Hysteria2..."
if ! command -v hysteria &> /dev/null; then
    bash <(curl -fsSL https://get.hy2.sh/)
fi

HY2_PASSWORD=$(openssl rand -base64 16 | tr -d '=+/' | head -c 22)
HY2_CERT_DIR="/etc/hysteria"
mkdir -p "$HY2_CERT_DIR"

if [ ! -f "$HY2_CERT_DIR/server.crt" ]; then
    openssl req -x509 -nodes \
        -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
        -keyout "$HY2_CERT_DIR/server.key" \
        -out   "$HY2_CERT_DIR/server.crt" \
        -subj "/CN=www.microsoft.com" \
        -days 36500 2>/dev/null
fi

tee "$HY2_CERT_DIR/config.yaml" > /dev/null << EOF
listen: :$HY2_PORT

tls:
  cert: $HY2_CERT_DIR/server.crt
  key: $HY2_CERT_DIR/server.key

auth:
  type: password
  password: "$HY2_PASSWORD"

masquerade:
  type: proxy
  proxy:
    url: https://www.microsoft.com
    rewriteHost: true
EOF

tee /etc/systemd/system/hysteria-server.service > /dev/null << 'EOF'
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

systemctl daemon-reload
systemctl enable hysteria-server
systemctl restart hysteria-server
sleep 2

if systemctl is-active --quiet hysteria-server; then
    echo -e "${GREEN}✅ Hysteria2 running on UDP/$HY2_PORT${NC}"
else
    echo -e "${RED}❌ Hysteria2 failed to start!${NC}"
    journalctl -u hysteria-server -n 10
fi

# ── 8. Firewall + Security ──────────────────────────────────
next "Configuring UFW, Fail2ban..."
ufw default deny incoming
ufw default allow outgoing
ufw allow $SSH_PORT/tcp    # SSH
if [[ "$PROTOCOL" =~ ^(vless|xui|naiveproxy)$ ]]; then
    ufw allow 443/tcp      # VLESS or NaiveProxy
fi
ufw allow 443/udp          # Hysteria2
ufw allow 8000/tcp         # TelegramHelper bot API
ufw allow 993/tcp          # MTProto
[ "$PROTOCOL" = "naiveproxy" ] && ufw allow 80/tcp  # ACME challenge for Let's Encrypt
[ "$PROTOCOL" = "mieru" ] && ufw allow "$MIERU_PORT/$MIERU_PROTOCOL"  # Mieru/mita
if [ "$PROTOCOL" = "xui" ]; then
    echo -e "${YELLOW}If the 3x-ui panel listens on a separate public port, open it by hand:${NC}"
    echo -e "${YELLOW}  ufw allow <PANEL_PORT>/tcp${NC}"
    echo -e "${YELLOW}For a mesh-only panel you do not need to open the panel port publicly.${NC}"
fi
ufw --force enable

tee /etc/fail2ban/jail.local > /dev/null << EOF
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3

[sshd]
enabled = true
port = $SSH_PORT
backend = systemd
journalmatch = _SYSTEMD_UNIT=ssh.service
maxretry = 3
bantime = 24h
EOF
systemctl enable fail2ban
systemctl restart fail2ban

echo 'APT::Periodic::Update-Package-Lists "1";' | tee    /etc/apt/apt.conf.d/20auto-upgrades > /dev/null
echo 'APT::Periodic::Unattended-Upgrade "1";'   | tee -a /etc/apt/apt.conf.d/20auto-upgrades > /dev/null
echo -e "${GREEN}✅ UFW, Fail2ban configured${NC}"

# ── 9. SSH port ──────────────────────────────────────────────
next "Changing SSH port to $SSH_PORT..."
if ! grep -q "Port $SSH_PORT" /etc/ssh/sshd_config; then
    sed -i "s/^#*Port .*/Port $SSH_PORT/" /etc/ssh/sshd_config
    systemctl restart ssh || systemctl restart sshd
    echo -e "${GREEN}✅ SSH moved to port $SSH_PORT${NC}"
else
    echo "SSH already on port $SSH_PORT"
fi

# ── 10. Prepare /opt/TelegramHelper ────────────────────────────
next "Preparing /opt/TelegramHelper..."
PROJECT_DIR="/opt/TelegramHelper"
mkdir -p $PROJECT_DIR

API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
HMAC_KEY=$(openssl rand -hex 32)
ENC_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Protocol-specific config files
if [ "$PROTOCOL" = "xui" ]; then
    echo '{}' > $PROJECT_DIR/vless_config.json
    echo '{}' > $PROJECT_DIR/naiveproxy_config.json
elif [ "$PROTOCOL" = "vless" ]; then
    cat > $PROJECT_DIR/vless_config.json << EOF
{
  "enabled": true,
  "server": "$SERVER_IP",
  "port": $VLESS_PORT,
  "uuid": "$UUID",
  "public_key": "$PUBLIC_KEY",
  "private_key": "$PRIVATE_KEY",
  "short_id": "$SHORT_ID",
  "sni": "$SNI",
  "fingerprint": "$FINGERPRINT",
  "flow": "xtls-rprx-vision"
}
EOF
    echo '{}' > $PROJECT_DIR/naiveproxy_config.json
elif [ "$PROTOCOL" = "naiveproxy" ]; then
    echo '{}' > $PROJECT_DIR/vless_config.json
    cat > $PROJECT_DIR/naiveproxy_config.json << EOF
{
  "enabled": true,
  "domain": "$NAIVE_DOMAIN",
  "server": "$SERVER_IP",
  "port": $NAIVE_PORT,
  "username": "$NAIVE_USERNAME",
  "password": "$NAIVE_PASSWORD",
  "scheme": "https",
  "local_socks_port": 10808,
  "padding": true,
  "caddyfile_path": "/etc/caddy-naive/Caddyfile",
  "service_name": "caddy-naive"
}
EOF
else
    echo '{}' > $PROJECT_DIR/vless_config.json
    echo '{}' > $PROJECT_DIR/naiveproxy_config.json
    cat > $PROJECT_DIR/mieru_config.json << EOF
{
  "enabled": true,
  "server": "$SERVER_IP",
  "port_bindings": [
    {
      "port": $MIERU_PORT,
      "protocol": "${MIERU_PROTOCOL^^}"
    }
  ],
  "mtu": 1400,
  "multiplexing": "MULTIPLEXING_LOW",
  "handshake_mode": "HANDSHAKE_STANDARD",
  "socks5_port": 10810,
  "rpc_port": 8964,
  "logging_level": "INFO",
  "service_name": "mita",
  "clients": []
}
EOF
fi

cat > $PROJECT_DIR/app_keys.json << EOF
{
  "app_keys": {
    "apiai-v3": {
      "api_key": "$API_KEY",
      "encryption_key": "$ENC_KEY"
    }
  },
  "default": {
    "api_key": "$API_KEY",
    "encryption_key": "$ENC_KEY"
  }
}
EOF

echo '{}' > $PROJECT_DIR/users.json
echo '{}' > $PROJECT_DIR/hysteria2_config.json
echo '{}' > $PROJECT_DIR/mtproto_config.json
echo '{}' > $PROJECT_DIR/headscale_config.json
# Empty configs for docker bind-mount (compose.yaml) — otherwise Docker
# creates a directory instead of a file and the bot dies with Errno 21.
echo '{}' > $PROJECT_DIR/tuic_config.json
echo '{}' > $PROJECT_DIR/anytls_config.json
echo '{}' > $PROJECT_DIR/xhttp_config.json
echo '{}' > $PROJECT_DIR/xui_config.json
if [ ! -f "$PROJECT_DIR/mieru_config.json" ]; then
    echo '{}' > $PROJECT_DIR/mieru_config.json
fi
touch $PROJECT_DIR/bot.log

chmod 600 $PROJECT_DIR/app_keys.json $PROJECT_DIR/users.json \
          $PROJECT_DIR/vless_config.json $PROJECT_DIR/naiveproxy_config.json \
          $PROJECT_DIR/hysteria2_config.json $PROJECT_DIR/mtproto_config.json \
          $PROJECT_DIR/headscale_config.json \
          $PROJECT_DIR/tuic_config.json $PROJECT_DIR/anytls_config.json \
          $PROJECT_DIR/xhttp_config.json $PROJECT_DIR/xui_config.json \
          $PROJECT_DIR/mieru_config.json
chmod 640 $PROJECT_DIR/bot.log

echo -e "${GREEN}✅ Directory and data files are ready${NC}"

# ── 11. Disk auto-cleanup ────────────────────────────────────
# Install the timer now: build cache and journald grow from the first build,
# and hitting 100% disk on a VPS is easier than it looks (CLEANUP_SERVER.md).
# At this phase project code may not be on the server yet — then cleanup
# will be enabled later, at deploy (install_telegramhelper_*.sh).
next "Disk auto-cleanup..."
MAINT_SH=""
for cand in "$(dirname "$0")/vps_maintenance.sh" "$PROJECT_DIR/scripts/vps_maintenance.sh"; do
    if [ -f "$cand" ]; then MAINT_SH="$cand"; break; fi
done

if [ -n "$MAINT_SH" ]; then
    if bash "$MAINT_SH" --install; then
        MAINT_STATE="enabled (Sun 04:00 UTC)"
    else
        MAINT_STATE="ERROR — enable by hand: bash scripts/vps_maintenance.sh --install"
    fi
    echo -e "${GREEN}✅ Disk auto-cleanup enabled${NC}"
else
    MAINT_STATE="will enable when code is deployed (install_telegramhelper_*.sh)"
    echo -e "${YELLOW}vps_maintenance.sh is not on the server yet — cleanup will enable when code is deployed${NC}"
fi

# ── 12. Summary & Credentials ───────────────────────────────
next "Done! Summary:"

if [ "$PROTOCOL" = "xui" ]; then
    PROTO_LINK="Created in the 3x-ui panel after configuring the VLESS-Reality inbound."
    PROTO_SECTION="━━━ VLESS-Reality via 3x-ui (TCP/443) ━━━━━━━━━━━━━
Source of truth: 3x-ui panel / x-ui.service
Selected interface: ${XUI_ACCESS_MODE:-browser}

Notes:
- terminal menu: x-ui command on the VPS
- browser UI: https://<IP>:<PANEL_PORT>/<WEB_PATH>/
- both interfaces control the same x-ui.service

What to do in the panel:
1. Create a VLESS-Reality inbound on TCP/443.
2. Add at least one manual client for a check.
3. Copy the panel URL, login, password and inbound id.

What to do in TelegramHelper:
/xui_setup
/xui_status
/provision <telegram_user_id>
/profiles <telegram_user_id>"
elif [ "$PROTOCOL" = "vless" ]; then
    PROTO_LINK="vless://${UUID}@${SERVER_IP}:${VLESS_PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=${SNI}&fp=${FINGERPRINT}&pbk=${PUBLIC_KEY}&sid=${SHORT_ID}&type=tcp#VPS-Reality"
    PROTO_SECTION="━━━ VLESS-Reality legacy host-Xray (TCP/443) ━━━━━━━
UUID:        $UUID
Public Key:  $PUBLIC_KEY
Private Key: $PRIVATE_KEY
Short ID:    $SHORT_ID
SNI:         $SNI
Fingerprint: $FINGERPRINT

VLESS Link:
$PROTO_LINK"
elif [ "$PROTOCOL" = "naiveproxy" ]; then
    PROTO_LINK="naive+https://${NAIVE_USERNAME}:${NAIVE_PASSWORD}@${NAIVE_DOMAIN}:${NAIVE_PORT}#TelegramHelper-NaiveProxy"
    PROTO_SECTION="━━━ NaiveProxy (TCP/443) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Domain:   $NAIVE_DOMAIN
Username: $NAIVE_USERNAME
Password: $NAIVE_PASSWORD
Service:  caddy-naive

Client URI:
$PROTO_LINK"
else
    PROTO_LINK="Created via /mieru_add_client and /mieru_export after the bot starts."
    PROTO_SECTION="━━━ Mieru / mita (${MIERU_PROTOCOL^^}/${MIERU_PORT}) ━━━━━━━━━━━━━━━━━━━━
Server:   $SERVER_IP
Port:     $MIERU_PORT
Protocol: ${MIERU_PROTOCOL^^}
Service:  mita

What to do in TelegramHelper:
/mieru_status
/mieru_add_client phone
/mieru_apply
/mieru_start
/mieru_export phone"
fi

HY2_LINK="hy2://${HY2_PASSWORD}@${SERVER_IP}:${HY2_PORT}/?insecure=1#Hysteria2"

cat > $PROJECT_DIR/CREDENTIALS.txt << EOF
═══════════════════════════════════════════════════════════════
  🛡️  VPS Deploy Credentials — $(date +%Y-%m-%d)
  📍  Server: $SERVER_IP
  🔐  Protocol on 443: $PROTOCOL_HUMAN
═══════════════════════════════════════════════════════════════

$PROTO_SECTION

━━━ Hysteria2 (UDP/443) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Password: $HY2_PASSWORD

Hysteria2 URI:
$HY2_LINK

━━━ API Security ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API Key:        $API_KEY
HMAC Key:       $HMAC_KEY
Encryption Key: $ENC_KEY

━━━ Container deploy ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Selected: $DEPLOY_TARGET_HUMAN
Command:  $DEPLOY_CMD

━━━ Next step ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
From the local Mac, upload the project and run docker compose up.
Guide: DEPLOY_GUIDE.md → Phase 2.
═══════════════════════════════════════════════════════════════
EOF

chmod 600 $PROJECT_DIR/CREDENTIALS.txt

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Phase 1 finished!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""

if [ "$PROTOCOL" = "xui" ]; then
    echo -e "  🛠️  3x-ui panel:    $(systemctl is-active x-ui 2>/dev/null || echo unknown)"
    echo -e "  🛡️  VLESS-Reality: create inbound TCP/443 in the panel"
elif [ "$PROTOCOL" = "vless" ]; then
    echo -e "  🛡️  VLESS-Reality: TCP/443 — $(systemctl is-active xray)"
elif [ "$PROTOCOL" = "naiveproxy" ]; then
    echo -e "  🔐  NaiveProxy:    TCP/443 — $(systemctl is-active caddy-naive)"
else
    echo -e "  🕵️  Mieru:         ${MIERU_PROTOCOL^^}/$MIERU_PORT — $(systemctl is-active mita 2>/dev/null || echo unknown)"
fi
echo -e "  ⚡  Hysteria2:     UDP/443 — $(systemctl is-active hysteria-server)"
echo -e "  🔥  UFW:           $(ufw status | head -1)"
echo -e "  🛑  Fail2ban:      $(systemctl is-active fail2ban)"
echo -e "  💾  Swap:          $(swapon --show --noheadings | awk '{print $3}')"
echo -e "  🧹  Auto-cleanup:  $MAINT_STATE"
echo -e "  📁  Data dir:      $PROJECT_DIR"
echo ""
echo -e "${YELLOW}📋 Credentials saved in: $PROJECT_DIR/CREDENTIALS.txt${NC}"
echo -e "${YELLOW}   cat $PROJECT_DIR/CREDENTIALS.txt — view${NC}"
echo -e "${YELLOW}   rm $PROJECT_DIR/CREDENTIALS.txt  — delete after copying${NC}"
echo ""
echo -e "${CYAN}━━━ Next step (from Mac): ━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Selected for deploy:${NC} $DEPLOY_TARGET_HUMAN"
echo ""
echo "  cd /Users/<USER>/Project/ProjectPython/TelegramHelper"
echo "  cp example.env .env.deploy && nano .env.deploy"
echo ""
echo "  rsync -avz -e 'ssh -p $SSH_PORT' \\"
echo "    --exclude 'venv' --exclude '__pycache__' --exclude '.env' \\"
echo "    --exclude 'app_keys.json' --exclude 'users.json' --exclude '*.log' \\"
echo "    --exclude '.git' ./ root@$SERVER_IP:$PROJECT_DIR/"
echo ""
echo "  scp -P $SSH_PORT .env.deploy root@$SERVER_IP:$PROJECT_DIR/.env"
echo "  ssh -p $SSH_PORT root@$SERVER_IP 'cd $PROJECT_DIR && $DEPLOY_CMD'"
echo ""
echo -e "${CYAN}━━━ Disk cleanup (after code deploy): ━━━━━━━━━━━━━━${NC}"
echo "  State: $MAINT_STATE"
echo "  bash scripts/vps_maintenance.sh --install   # weekly timer"
echo "  bash scripts/vps_maintenance.sh --report    # diagnose: what ate the disk"
echo -e "  ${YELLOW}Details — CLEANUP_SERVER.md${NC}"

if [ "$PROTOCOL" = "xui" ]; then
    echo ""
    echo -e "${CYAN}━━━ After the bot starts (3x-ui mode): ━━━━━━━━━━━━━━━${NC}"
    echo "  1. Control 3x-ui: x-ui command or the browser panel URL."
    echo "  2. Create a VLESS-Reality inbound on TCP/443."
    echo "  3. In Telegram run: /xui_setup"
    echo "  4. Then: /xui_status && /provision <telegram_user_id>"
elif [ "$PROTOCOL" = "vless" ]; then
    echo ""
    echo -e "${CYAN}━━━ After the bot starts (legacy VLESS mode): ━━━━━━━━${NC}"
    echo "  1. In Telegram check: /vless_status"
    echo "  2. Then issue profiles: /provision <telegram_user_id>"
    echo "  3. The user fetches: /my_profile"
elif [ "$PROTOCOL" = "mieru" ]; then
    echo ""
    echo -e "${CYAN}━━━ After the bot starts (Mieru mode): ━━━━━━━━━━━━━━━${NC}"
    echo "  1. In Telegram check: /mieru_status"
    echo "  2. Create a client: /mieru_add_client phone"
    echo "  3. Apply the server: /mieru_apply && /mieru_start"
    echo "  4. Export the profile: /mieru_export phone"
fi

if [ "$DEPLOY_TARGET" != "bot" ]; then
    echo ""
    echo -e "${CYAN}━━━ Dockhand access (from Mac, after start): ━━━━━━━━${NC}"
    echo ""
    echo "  ssh -L 8501:localhost:8501 -p $SSH_PORT root@$SERVER_IP"
    echo "  # then open in the browser: http://localhost:8501"
    echo ""
    echo -e "  ${YELLOW}Details — DOCKHAND_GUIDE.md / DOCKHAND_SETUP.md${NC}"
fi
