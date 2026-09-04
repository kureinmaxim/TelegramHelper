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
echo -e "${GREEN}  🚀 TelegramHelper VPS Deploy — Фаза 1                 ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${BOLD}Что установить как основной transport stack?${NC}"
echo ""
echo -e "  ${CYAN}[1]${NC} VLESS-Reality legacy xray.service     — без 3x-ui, бот управляет Xray"
echo -e "  ${CYAN}[2]${NC} VLESS-Reality через 3x-ui + x-ui menu — терминальное меню панели"
echo -e "  ${CYAN}[3]${NC} VLESS-Reality через 3x-ui browser UI  — браузерная панель"
echo -e "  ${CYAN}[4]${NC} NaiveProxy (Caddy)                    — HTTPS-прокси, нужен домен"
echo -e "  ${CYAN}[5]${NC} Mieru (mita)                           — отдельный TCP/UDP порт, default 29999/tcp"
echo ""
echo -e "${YELLOW}Важно: 1/2/3/4 владеют TCP/443. Mieru по умолчанию НЕ занимает 443.${NC}"
echo ""
read -rp "Выбор [1/2/3/4/5] (Enter = 1): " PROTO_CHOICE
PROTO_CHOICE="${PROTO_CHOICE:-1}"
XUI_ACCESS_MODE=""

case "$PROTO_CHOICE" in
    1)
        PROTOCOL="vless"
        echo ""
        echo -e "${YELLOW}Будет установлен legacy host-Xray без 3x-ui.${NC}"
        echo -e "${YELLOW}/provision, /profiles, /my_profile будут работать через vless_config.json + xray.service.${NC}"
        ;;
    2)
        PROTOCOL="xui"
        XUI_ACCESS_MODE="terminal"
        echo ""
        echo -e "${YELLOW}Будет запущен официальный интерактивный installer 3x-ui.${NC}"
        echo -e "${YELLOW}Основное управление панелью: команда x-ui в терминале.${NC}"
        echo -e "${YELLOW}После установки создайте VLESS-Reality inbound и выполните /xui_setup в боте.${NC}"
        ;;
    3)
        PROTOCOL="xui"
        XUI_ACCESS_MODE="browser"
        echo ""
        echo -e "${YELLOW}Будет запущен официальный интерактивный installer 3x-ui.${NC}"
        echo -e "${YELLOW}Основное управление панелью: браузерный URL панели 3x-ui.${NC}"
        echo -e "${YELLOW}После установки создайте VLESS-Reality inbound и выполните /xui_setup в боте.${NC}"
        ;;
    4)
        PROTOCOL="naiveproxy"
        echo ""
        echo -e "${YELLOW}NaiveProxy требует домен с DNS A-записью на этот сервер.${NC}"
        echo -e "${YELLOW}Пример: naive.example.com → $(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP')${NC}"
        echo ""
        read -rp "Домен для NaiveProxy (например naive.example.com): " NAIVE_DOMAIN
        if [[ -z "$NAIVE_DOMAIN" ]]; then
            echo -e "${RED}❌ Домен обязателен для NaiveProxy. Используйте вариант 1/2/3 для VLESS или 5 для Mieru без домена.${NC}"
            exit 1
        fi
        NAIVE_PORT="443"
        echo -e "${YELLOW}⚠️  Сборка Caddy с плагином займёт 3-5 минут (Go компиляция).${NC}"
        ;;
    5)
        PROTOCOL="mieru"
        echo ""
        echo -e "${YELLOW}Mieru будет установлен как mita на отдельный порт.${NC}"
        echo -e "${YELLOW}По умолчанию: ${MIERU_PORT}/${MIERU_PROTOCOL}. Это не конфликтует с 443/tcp VLESS/NaiveProxy и 443/udp Hysteria2.${NC}"
        read -rp "Порт Mieru (Enter = ${MIERU_PORT}): " MIERU_PORT_INPUT
        MIERU_PORT="${MIERU_PORT_INPUT:-$MIERU_PORT}"
        read -rp "Протокол Mieru [tcp/udp] (Enter = tcp): " MIERU_PROTOCOL_INPUT
        MIERU_PROTOCOL="${MIERU_PROTOCOL_INPUT:-tcp}"
        MIERU_PROTOCOL="${MIERU_PROTOCOL,,}"
        if ! [[ "$MIERU_PORT" =~ ^[0-9]+$ ]] || [ "$MIERU_PORT" -lt 1025 ] || [ "$MIERU_PORT" -gt 65535 ]; then
            echo -e "${RED}❌ Порт Mieru должен быть числом 1025..65535.${NC}"
            exit 1
        fi
        if [[ "$MIERU_PROTOCOL" != "tcp" && "$MIERU_PROTOCOL" != "udp" ]]; then
            echo -e "${RED}❌ Протокол Mieru должен быть tcp или udp.${NC}"
            exit 1
        fi
        if [[ "$MIERU_PORT" = "443" ]]; then
            echo -e "${RED}❌ Не используйте 443 для Mieru при fresh install: он конфликтует с VLESS/NaiveProxy/Hysteria2.${NC}"
            exit 1
        fi
        ;;
    *)
        echo -e "${RED}❌ Неверный выбор: $PROTO_CHOICE${NC}"
        exit 1
        ;;
esac

# ── Deployment target dialog ─────────────────────────────────
# Сам скрипт `docker compose up` не выполняет (нет ещё кода проекта на сервере).
# Выбор влияет только на финальные подсказки и на CREDENTIALS.txt.
echo ""
echo -e "${BOLD}Что планируете поднимать после копирования кода?${NC}"
echo ""
echo -e "  ${CYAN}[1]${NC} Только бот (telegram-helper)              — стандартный вариант"
echo -e "  ${CYAN}[2]${NC} Только Dockhand (диагностика)             — добавить панель к работающему боту"
echo -e "  ${CYAN}[3]${NC} Оба контейнера (telegram-helper + dockhand) — полный стек ${YELLOW}(рекомендуется)${NC}"
echo ""
read -rp "Выбор [1/2/3] (Enter = 3): " DEPLOY_CHOICE
DEPLOY_CHOICE="${DEPLOY_CHOICE:-3}"

case "$DEPLOY_CHOICE" in
    1)
        DEPLOY_TARGET="bot"
        DEPLOY_TARGET_HUMAN="Только telegram-helper"
        DEPLOY_CMD="bash scripts/rebuild_bot.sh"
        ;;
    2)
        DEPLOY_TARGET="dockhand"
        DEPLOY_TARGET_HUMAN="Только dockhand"
        DEPLOY_CMD="docker compose up -d --build dockhand"
        echo ""
        echo -e "${YELLOW}⚠️  Dockhand жёстко смотрит на контейнер 'telegram-helper-lite'${NC}"
        echo -e "${YELLOW}   и API http://telegram-helper:8000/health. Без бота UI${NC}"
        echo -e "${YELLOW}   будет показывать 'Container Not Found' / 'API Unreachable'.${NC}"
        echo -e "${YELLOW}   См. DOCKHAND_SETUP.md §4.B.2 — как поднять в одиночку.${NC}"
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

echo -e "${GREEN}  Протокол: $PROTOCOL_HUMAN${NC}"
echo -e "${GREEN}  IP:        $SERVER_IP${NC}"
[ "$PROTOCOL" = "naiveproxy" ] && echo -e "${GREEN}  Домен:     $NAIVE_DOMAIN${NC}"
[ "$PROTOCOL" = "mieru" ] && echo -e "${GREEN}  Mieru:     ${MIERU_PORT}/${MIERU_PROTOCOL}${NC}"
echo -e "${GREEN}  Развор:    $DEPLOY_TARGET_HUMAN${NC}"
echo ""

# ── 1. Swap (critical for 1GB RAM) ──────────────────────────
next "Создание swap 1GB..."
if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' >> /etc/sysctl.conf
    sysctl vm.swappiness=10
    echo -e "${GREEN}✅ Swap 1GB создан${NC}"
else
    echo "Swap уже существует"
    swapon --show
fi

# ── 2. Disable IPv6 (close VPN-egress leak) ─────────────────
# Без этого dual-stack VPS делает egress по IPv6, и геолокатор у клиента
# видит IPv6 сервера → ChatGPT/Spotify/банки решают, что клиент сидит
# из неправильной страны (sing-box client: пилл "IPv6 LEAK DETECTED").
# Возврат: rm /etc/sysctl.d/99-disable-ipv6.conf && sysctl --system.
next "Отключение IPv6 (защита от egress-leak региона)..."
if [ ! -f /etc/sysctl.d/99-disable-ipv6.conf ]; then
    cat > /etc/sysctl.d/99-disable-ipv6.conf <<'EOF'
# IPv6 disabled by deploy_fresh_vps.sh — closes egress leak that lets
# geo-locators see the VPS's IPv6 instead of the tunnel's IPv4 egress.
# To re-enable: rm this file && sysctl --system && restart transports.
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
EOF
    sysctl --system >/dev/null 2>&1 || true
    echo -e "${GREEN}✅ IPv6 отключён системно (/etc/sysctl.d/99-disable-ipv6.conf)${NC}"
else
    echo "IPv6 уже отключён (/etc/sysctl.d/99-disable-ipv6.conf существует)"
fi

# ── 3. System update ────────────────────────────────────────
next "Обновление системы..."
apt-get update -qq
apt-get upgrade -y -qq

# ── 4. Install packages ─────────────────────────────────────
next "Установка пакетов..."
PKGS="curl jq openssl ca-certificates qrencode git python3 python3-pip ufw fail2ban unattended-upgrades"
if [ "$PROTOCOL" = "naiveproxy" ]; then
    PKGS="$PKGS golang-go"
fi
apt-get install -y -qq $PKGS

# ── 5. Install Docker ───────────────────────────────────────
next "Установка Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}✅ Docker установлен${NC}"
else
    echo "Docker уже установлен: $(docker --version)"
fi

# ── 5b. Pre-flight: TCP 443 должен быть свободен ИЛИ уже занят нашим стеком ──
# Цель: не тратить минуты на сборку Caddy, если :443 уже держит 3x-ui/nginx/…
# При повторном запуске скрипта на том же VPS порт часто слушает уже наш
# `xray` или `caddy-naive` — тогда не блокируем (см. ниже).
preflight_tcp443_for_protocol_install() {
    if ! command -v ss >/dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  Утилита ss недоступна — пропускаем pre-flight :443/tcp.${NC}"
        return 0
    fi
    # LISTEN на TCP :443 (IPv4 *:443 / 0.0.0.0:443 или IPv6 [::]:443)
    if ! ss -ltn 2>/dev/null | awk '$1 == "LISTEN" && $4 ~ /:443$/ { f = 1 } END { exit !f }'; then
        return 0
    fi
    # Что-то слушает :443 — повторный запуск разрешаем только если это
    # сервис выбранного режима. Иначе можно случайно поставить второй Xray.
    if [ "$PROTOCOL" = "vless" ] && systemctl is-active --quiet xray 2>/dev/null; then
        echo -e "${YELLOW}⚠️  :443/tcp уже занят xray — считаем это legacy VLESS и продолжаем (повторный запуск?).${NC}"
        ss -ltn 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:443$/ {print}' || true
        return 0
    fi
    if [ "$PROTOCOL" = "xui" ] && systemctl is-active --quiet x-ui 2>/dev/null; then
        echo -e "${YELLOW}⚠️  :443/tcp уже занят x-ui — считаем это 3x-ui mode и продолжаем (повторный запуск?).${NC}"
        ss -ltn 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:443$/ {print}' || true
        return 0
    fi
    if [ "$PROTOCOL" = "naiveproxy" ] && systemctl is-active --quiet caddy-naive 2>/dev/null; then
        echo -e "${YELLOW}⚠️  :443/tcp уже занят caddy-naive — считаем это NaiveProxy и продолжаем (повторный запуск?).${NC}"
        ss -ltn 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:443$/ {print}' || true
        return 0
    fi
    echo -e "${RED}❌ TCP-порт 443 уже занят посторонним процессом.${NC}"
    echo -e "${YELLOW}Текущие слушатели :443:${NC}"
    ss -ltnp 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:443$/ {print}' || ss -ltnp 2>/dev/null | grep ':443' || true
    echo ""
    echo -e "${YELLOW}Освободите порт (остановите 3x-ui/nginx/другой Xray и т.п.) либо используйте другой VPS.${NC}"
    echo -e "${YELLOW}Подсказка: ss -ltnp | grep 443${NC}"
    exit 1
}
if [[ "$PROTOCOL" =~ ^(vless|xui|naiveproxy)$ ]]; then
    preflight_tcp443_for_protocol_install
else
    echo -e "${YELLOW}Mieru выбран на ${MIERU_PORT}/${MIERU_PROTOCOL}: pre-flight TCP/443 пропущен.${NC}"
fi

# ── 6. Install primary protocol ──────────────────────────────
if [ "$PROTOCOL" = "xui" ]; then

    next "Установка 3x-ui panel для VLESS-Reality..."
    echo -e "${YELLOW}Официальный installer 3x-ui интерактивный: задайте порт панели, webBasePath, логин и пароль.${NC}"
    echo -e "${YELLOW}Не используйте 443 как порт панели: 443 нужен VLESS inbound'у Xray.${NC}"
    echo -e "${YELLOW}После установки доступны оба интерфейса: терминальная команда x-ui и браузерная панель.${NC}"
    if systemctl list-unit-files 2>/dev/null | grep -q '^x-ui\.service'; then
        echo "3x-ui уже установлен: x-ui.service найден"
        systemctl enable x-ui || true
        systemctl restart x-ui || true
    else
        bash <(curl -Ls https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh)
    fi
    echo -e "${GREEN}✅ 3x-ui installer завершён${NC}"
    echo -e "${YELLOW}Дальше в панели создайте VLESS-Reality inbound на TCP/443.${NC}"
    echo -e "${YELLOW}После запуска бота выполните: /xui_setup → /xui_status → /provision <telegram_user_id>${NC}"

elif [ "$PROTOCOL" = "vless" ]; then

    next "Установка Xray-core + VLESS-Reality..."
    if ! command -v xray &> /dev/null; then
        bash -c "$(curl -sL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
        echo -e "${GREEN}✅ Xray установлен${NC}"
    else
        echo "Xray уже установлен: $(xray version | head -1)"
    fi

    VLESS_PORT="443"
    UUID=$(xray uuid)
    X25519_OUTPUT=$(/usr/local/bin/xray x25519 2>/dev/null)
    PRIVATE_KEY=$(echo "$X25519_OUTPUT" | grep -i "private" | awk -F': ' '{print $2}' | tr -d ' ')
    PUBLIC_KEY=$(echo "$X25519_OUTPUT"  | grep -i "public"  | awk -F': ' '{print $2}' | tr -d ' ')
    SHORT_ID=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 8)
    # yahoo.com по умолчанию: www.microsoft.com у ряда мобильных операторов
    # блокируется DPI (Reality-хендшейк не доходит до Xray). Сменить потом
    # можно из бота: /vless_set_sni
    SNI="yahoo.com"
    FINGERPRINT="chrome"

    if [ -z "$PRIVATE_KEY" ] || [ -z "$PUBLIC_KEY" ]; then
        echo -e "${YELLOW}⚠️ Повторная генерация ключей...${NC}"
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
    echo -e "${GREEN}✅ Xray VLESS-Reality запущен на TCP/$VLESS_PORT${NC}"

elif [ "$PROTOCOL" = "naiveproxy" ]; then

    next "Сборка Caddy + NaiveProxy..."
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
        echo -e "${GREEN}✅ NaiveProxy (Caddy) запущен на TCP/$NAIVE_PORT${NC}"
    else
        echo -e "${RED}❌ NaiveProxy не запустился!${NC}"
        journalctl -u "$SERVICE_NAME" -n 20
    fi

else  # mieru

    next "Установка Mieru server (mita)..."
    INSTALL_MIERU="/tmp/install_mieru.sh"
    curl -fsSL "https://raw.githubusercontent.com/your-github-user/TelegramHelper/main/scripts/install_mieru.sh" -o "$INSTALL_MIERU"
    chmod +x "$INSTALL_MIERU"
    bash "$INSTALL_MIERU" --port "$MIERU_PORT" --protocol "$MIERU_PROTOCOL"
    echo -e "${GREEN}✅ Mieru/mita установлен. Порт: ${MIERU_PORT}/${MIERU_PROTOCOL}${NC}"
    echo -e "${YELLOW}После запуска бота выполните: /mieru_set_server $SERVER_IP → /mieru_set_port $MIERU_PORT $MIERU_PROTOCOL → /mieru_add_client phone → /mieru_apply → /mieru_start${NC}"

fi

# ── 7. Install & Configure Hysteria2 ────────────────────────
next "Установка и настройка Hysteria2..."
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
    echo -e "${GREEN}✅ Hysteria2 запущен на UDP/$HY2_PORT${NC}"
else
    echo -e "${RED}❌ Hysteria2 не запустился!${NC}"
    journalctl -u hysteria-server -n 10
fi

# ── 8. Firewall + Security ──────────────────────────────────
next "Настройка UFW, Fail2ban..."
ufw default deny incoming
ufw default allow outgoing
ufw allow $SSH_PORT/tcp    # SSH
if [[ "$PROTOCOL" =~ ^(vless|xui|naiveproxy)$ ]]; then
    ufw allow 443/tcp      # VLESS или NaiveProxy
fi
ufw allow 443/udp          # Hysteria2
ufw allow 8000/tcp         # TelegramHelper bot API
ufw allow 993/tcp          # MTProto
[ "$PROTOCOL" = "naiveproxy" ] && ufw allow 80/tcp  # ACME challenge для Let's Encrypt
[ "$PROTOCOL" = "mieru" ] && ufw allow "$MIERU_PORT/$MIERU_PROTOCOL"  # Mieru/mita
if [ "$PROTOCOL" = "xui" ]; then
    echo -e "${YELLOW}Если 3x-ui panel слушает отдельный публичный порт, откройте его вручную:${NC}"
    echo -e "${YELLOW}  ufw allow <PANEL_PORT>/tcp${NC}"
    echo -e "${YELLOW}Для mesh-only панели публично открывать panel port не нужно.${NC}"
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
echo -e "${GREEN}✅ UFW, Fail2ban настроены${NC}"

# ── 9. SSH port ──────────────────────────────────────────────
next "Смена SSH порта на $SSH_PORT..."
if ! grep -q "Port $SSH_PORT" /etc/ssh/sshd_config; then
    sed -i "s/^#*Port .*/Port $SSH_PORT/" /etc/ssh/sshd_config
    systemctl restart ssh || systemctl restart sshd
    echo -e "${GREEN}✅ SSH переведён на порт $SSH_PORT${NC}"
else
    echo "SSH уже на порту $SSH_PORT"
fi

# ── 10. Prepare /opt/TelegramHelper ────────────────────────────
next "Подготовка /opt/TelegramHelper..."
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
# Пустые конфиги под docker bind-mount (compose.yaml) — иначе Docker
# создаст директорию вместо файла и бот упадёт с Errno 21.
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

echo -e "${GREEN}✅ Директория и файлы данных готовы${NC}"

# ── 11. Авто-чистка диска ────────────────────────────────────
# Ставим таймер сразу: build cache и journald растут с первого же билда,
# а упереться в 100% диска на VPS проще, чем кажется (CLEANUP_SERVER.md).
# На этой фазе кода проекта на сервере может ещё не быть — тогда чистка
# включится позже, при деплое (install_telegramhelper_*.sh).
next "Авто-чистка диска..."
MAINT_SH=""
for cand in "$(dirname "$0")/vps_maintenance.sh" "$PROJECT_DIR/scripts/vps_maintenance.sh"; do
    if [ -f "$cand" ]; then MAINT_SH="$cand"; break; fi
done

if [ -n "$MAINT_SH" ]; then
    if bash "$MAINT_SH" --install; then
        MAINT_STATE="включена (вс 04:00 UTC)"
    else
        MAINT_STATE="ОШИБКА — включи вручную: bash scripts/vps_maintenance.sh --install"
    fi
    echo -e "${GREEN}✅ Авто-чистка диска включена${NC}"
else
    MAINT_STATE="включится при деплое кода (install_telegramhelper_*.sh)"
    echo -e "${YELLOW}vps_maintenance.sh ещё не на сервере — чистка включится при деплое кода${NC}"
fi

# ── 12. Summary & Credentials ───────────────────────────────
next "Готово! Сводка:"

if [ "$PROTOCOL" = "xui" ]; then
    PROTO_LINK="Создаётся в панели 3x-ui после настройки VLESS-Reality inbound."
    PROTO_SECTION="━━━ VLESS-Reality via 3x-ui (TCP/443) ━━━━━━━━━━━━━
Source of truth: 3x-ui panel / x-ui.service
Выбранный интерфейс: ${XUI_ACCESS_MODE:-browser}

Важно:
- terminal menu: команда x-ui на VPS
- browser UI: https://<IP>:<PANEL_PORT>/<WEB_PATH>/
- оба интерфейса управляют одним и тем же x-ui.service

Что сделать в панели:
1. Создать VLESS-Reality inbound на TCP/443.
2. Добавить хотя бы одного manual client для проверки.
3. Скопировать URL панели, login, password и inbound id.

Что сделать в TelegramHelper:
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
    PROTO_LINK="Создаётся через /mieru_add_client и /mieru_export после запуска бота."
    PROTO_SECTION="━━━ Mieru / mita (${MIERU_PROTOCOL^^}/${MIERU_PORT}) ━━━━━━━━━━━━━━━━━━━━
Server:   $SERVER_IP
Port:     $MIERU_PORT
Protocol: ${MIERU_PROTOCOL^^}
Service:  mita

Что сделать в TelegramHelper:
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

━━━ Развор контейнеров ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Выбрано: $DEPLOY_TARGET_HUMAN
Команда: $DEPLOY_CMD

━━━ Следующий шаг ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
С локального Mac выполните загрузку проекта и docker compose up.
Инструкция: DEPLOY_GUIDE.md → Фаза 2.
═══════════════════════════════════════════════════════════════
EOF

chmod 600 $PROJECT_DIR/CREDENTIALS.txt

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Фаза 1 завершена!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""

if [ "$PROTOCOL" = "xui" ]; then
    echo -e "  🛠️  3x-ui panel:    $(systemctl is-active x-ui 2>/dev/null || echo unknown)"
    echo -e "  🛡️  VLESS-Reality: создайте inbound TCP/443 в панели"
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
echo -e "  🧹  Авто-чистка:   $MAINT_STATE"
echo -e "  📁  Data dir:      $PROJECT_DIR"
echo ""
echo -e "${YELLOW}📋 Credentials сохранены в: $PROJECT_DIR/CREDENTIALS.txt${NC}"
echo -e "${YELLOW}   cat $PROJECT_DIR/CREDENTIALS.txt — просмотреть${NC}"
echo -e "${YELLOW}   rm $PROJECT_DIR/CREDENTIALS.txt  — удалить после копирования${NC}"
echo ""
echo -e "${CYAN}━━━ Следующий шаг (с Mac): ━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Выбрано к развору:${NC} $DEPLOY_TARGET_HUMAN"
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
echo -e "${CYAN}━━━ Чистка диска (после деплоя кода): ━━━━━━━━━━━━━━━${NC}"
echo "  Состояние: $MAINT_STATE"
echo "  bash scripts/vps_maintenance.sh --install   # еженедельный таймер"
echo "  bash scripts/vps_maintenance.sh --report    # диагностика: что съело диск"
echo -e "  ${YELLOW}Подробнее — CLEANUP_SERVER.md${NC}"

if [ "$PROTOCOL" = "xui" ]; then
    echo ""
    echo -e "${CYAN}━━━ После запуска бота (3x-ui mode): ━━━━━━━━━━━━━━━━━${NC}"
    echo "  1. Управление 3x-ui: команда x-ui или браузерный URL панели."
    echo "  2. Создайте VLESS-Reality inbound на TCP/443."
    echo "  3. В Telegram выполните: /xui_setup"
    echo "  4. Затем: /xui_status && /provision <telegram_user_id>"
elif [ "$PROTOCOL" = "vless" ]; then
    echo ""
    echo -e "${CYAN}━━━ После запуска бота (legacy VLESS mode): ━━━━━━━━━━${NC}"
    echo "  1. В Telegram проверьте: /vless_status"
    echo "  2. Затем выдавайте профили: /provision <telegram_user_id>"
    echo "  3. Пользователь забирает: /my_profile"
elif [ "$PROTOCOL" = "mieru" ]; then
    echo ""
    echo -e "${CYAN}━━━ После запуска бота (Mieru mode): ━━━━━━━━━━━━━━━━${NC}"
    echo "  1. В Telegram проверьте: /mieru_status"
    echo "  2. Создайте клиента: /mieru_add_client phone"
    echo "  3. Примените сервер: /mieru_apply && /mieru_start"
    echo "  4. Выдайте профиль: /mieru_export phone"
fi

if [ "$DEPLOY_TARGET" != "bot" ]; then
    echo ""
    echo -e "${CYAN}━━━ Доступ к Dockhand (с Mac, после старта): ━━━━━━━━${NC}"
    echo ""
    echo "  ssh -L 8501:localhost:8501 -p $SSH_PORT root@$SERVER_IP"
    echo "  # затем открыть в браузере: http://localhost:8501"
    echo ""
    echo -e "  ${YELLOW}Подробнее — DOCKHAND_GUIDE.md / DOCKHAND_SETUP.md${NC}"
fi
