#!/bin/bash
# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
# 🚀 AUTO SETUP VPS — VLESS-Reality + Hysteria2
# ═══════════════════════════════════════════════════════════════
#
# Этот скрипт автоматически настраивает свежий Debian 12 VPS:
# - Режим minimal: Xray + Hysteria2 (для ручного управления)
# - Режим full: Docker + TelegramHelper + Xray + Hysteria2 (через бота)
#
# Оба протокола работают одновременно:
#   VLESS-Reality — TCP (маскируется под HTTPS)
#   Hysteria2     — UDP (QUIC-based, высокая скорость)
#
# Использование:
#   ./auto_setup_vps.sh --host 123.45.67.89 --password "your_pass"
#   ./auto_setup_vps.sh --host 123.45.67.89 --mode full --password "pass"
#   ./auto_setup_vps.sh --host 123.45.67.89 --no-hysteria2 --password "pass"
#
# Или через переменную окружения:
#   SSH_PASS="your_pass" ./auto_setup_vps.sh --host 123.45.67.89
#
# ═══════════════════════════════════════════════════════════════

set -e
umask 077

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Значения по умолчанию
SSH_HOST=""
SSH_PORT="22"
SSH_USER="root"
SSH_PASSWORD="${SSH_PASS:-}"
INSTALL_MODE="minimal"  # minimal или full
BOT_TOKEN=""
ADMIN_ID=""
OUTPUT_DIR="./vless_configs"
VLESS_PORT="443"

# Hysteria2 (по умолчанию включен)
HY2_ENABLE="true"
HY2_PORT="443"          # UDP порт (не конфликтует с VLESS TCP 443)
HY2_PASSWORD=""          # Авто-генерация если пусто

# AI провайдеры (опционально)
ANTHROPIC_KEY=""
OPENAI_KEY=""

# Nginx + Certbot (опционально)
NGINX_ENABLE="false"
NGINX_DOMAIN=""
NGINX_EMAIL=""
NGINX_HTTPS_PORT=""
NGINX_UPSTREAM_HOST="127.0.0.1"
NGINX_UPSTREAM_PORT="8000"

# Headscale (опционально, full режим)
HEADSCALE_ENABLE="false"
HEADSCALE_DOMAIN=""
HA_DOMAIN=""

# Авто-чистка диска через scripts/vps_maintenance.sh
# (по умолчанию включена — ставит systemd-таймер на еженедельный prune Docker
# + кэп journald 500M; см. POST_DEPLOY.md §11)
WITH_MAINTENANCE="true"

print_banner() {
    echo -e "${CYAN}"
    echo "═══════════════════════════════════════════════════════════════"
    echo "   🚀 AUTO SETUP VPS — VLESS-Reality + Hysteria2"
    echo "═══════════════════════════════════════════════════════════════"
    echo -e "${NC}"
}

print_help() {
    echo -e "${GREEN}Использование:${NC}"
    echo "  $0 [OPTIONS]"
    echo ""
    echo -e "${GREEN}Обязательные параметры:${NC}"
    echo "  --host, -h HOST       IP адрес или домен сервера"
    echo "  --password, -p PASS   SSH пароль (или env SSH_PASS)"
    echo ""
    echo -e "${GREEN}Опциональные параметры:${NC}"
    echo "  --port PORT           SSH порт (по умолчанию: 22)"
    echo "  --user USER           SSH пользователь (по умолчанию: root)"
    echo "  --mode MODE           Режим установки:"
    echo "                          minimal - Xray + Hysteria2 (по умолчанию)"
    echo "                          full    - Docker + TelegramHelper + Xray + Hysteria2"
    echo "  --vless-port PORT     Порт VLESS TCP (по умолчанию: 443)"
    echo "  --hy2-port PORT       Порт Hysteria2 UDP (по умолчанию: 443)"
    echo "  --hy2-password PASS   Пароль Hysteria2 (по умолчанию: авто)"
    echo "  --no-hysteria2        Не устанавливать Hysteria2"
    echo ""
    echo -e "${GREEN}Параметры для full режима:${NC}"
    echo "  --bot-token TOKEN     Telegram Bot Token (от @BotFather)"
    echo "  --admin-id ID         Ваш Telegram User ID"
    echo "  --anthropic-key KEY   Anthropic API ключ (опционально)"
    echo "  --openai-key KEY      OpenAI API ключ (опционально)"
    echo ""
    echo -e "${GREEN}Nginx + Certbot (опционально, full режим):${NC}"
    echo "  --nginx               Установить и настроить Nginx + SSL"
    echo "  --nginx-domain DOMAIN Домен для HTTPS (например api.example.com)"
    echo "  --nginx-email EMAIL   Email для Let's Encrypt"
    echo "  --nginx-https-port    HTTPS порт Nginx (по умолчанию: 443 или 8443 при конфликте)"
    echo "  --nginx-upstream-port Порт API (по умолчанию: 8000)"
    echo ""
    echo -e "${GREEN}Headscale (опционально, full режим):${NC}"
    echo "  --headscale           Установить Headscale (self-hosted Tailscale)"
    echo "  --headscale-domain D  Домен для Headscale (напр. headscale.example.com)"
    echo "  --ha-domain DOMAIN    Домен для Home Assistant (напр. ha.example.com)"
    echo ""
    echo -e "${GREEN}Дополнительно:${NC}"
    echo "  --output DIR          Папка для сохранения конфигов"
    echo "  --no-maintenance      Не ставить авто-чистку диска (по умолчанию ставится:"
    echo "                          systemd-таймер на еженедельный docker prune +"
    echo "                          journald cap 500M, см. POST_DEPLOY.md §11)"
    echo "  --with-maintenance    Явно включить (уже по умолчанию)"
    echo "  --help                Показать эту справку"
    echo ""
    echo -e "${YELLOW}Примеры:${NC}"
    echo "  # Минимальная установка (Xray + Hysteria2)"
    echo "  $0 --host 123.45.67.89 --password 'mypass'"
    echo ""
    echo "  # Только VLESS (без Hysteria2)"
    echo "  $0 --host 123.45.67.89 --no-hysteria2 --password 'mypass'"
    echo ""
    echo "  # Hysteria2 на другом порту"
    echo "  $0 --host 123.45.67.89 --hy2-port 8443 --password 'mypass'"
    echo ""
    echo "  # Полная установка с Telegram-ботом"
    echo "  $0 --host 123.45.67.89 --mode full \\"
    echo "     --bot-token '123456:ABC...' --admin-id 987654321 \\"
    echo "     --password 'mypass'"
    echo ""
    echo "  # Через переменную окружения"
    echo "  SSH_PASS='mypass' $0 --host 123.45.67.89 --mode full"
}

check_dependencies() {
    echo -e "${BLUE}📦 Проверка зависимостей...${NC}"
    
    # Проверяем sshpass
    if ! command -v sshpass &> /dev/null; then
        echo -e "${YELLOW}⚠️ sshpass не найден${NC}"
        echo ""
        
        # Определяем ОС
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo "Для macOS установите через Homebrew:"
            echo -e "${CYAN}  brew install hudochenkov/sshpass/sshpass${NC}"
        elif [[ -f /etc/debian_version ]]; then
            echo "Для Debian/Ubuntu:"
            echo -e "${CYAN}  sudo apt-get install sshpass${NC}"
        elif [[ -f /etc/redhat-release ]]; then
            echo "Для CentOS/RHEL:"
            echo -e "${CYAN}  sudo yum install sshpass${NC}"
        else
            echo "Установите sshpass для вашей ОС"
        fi
        echo ""
        exit 1
    fi
    
    # Проверяем ssh
    if ! command -v ssh &> /dev/null; then
        echo -e "${RED}❌ ssh не найден. Установите OpenSSH клиент.${NC}"
        exit 1
    fi
    
    # Проверяем scp
    if ! command -v scp &> /dev/null; then
        echo -e "${RED}❌ scp не найден. Установите OpenSSH клиент.${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✅ Все зависимости установлены${NC}"
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
                echo -e "${RED}❌ Неизвестный параметр: $1${NC}"
                print_help
                exit 1
                ;;
        esac
    done
}

validate_args() {
    local has_error=0
    
    if [[ -z "$SSH_HOST" ]]; then
        echo -e "${RED}❌ Не указан --host${NC}"
        has_error=1
    fi
    
    if [[ -z "$SSH_PASSWORD" ]]; then
        echo -e "${RED}❌ Не указан --password (или SSH_PASS)${NC}"
        has_error=1
    fi

    if [[ "$INSTALL_MODE" == "full" && "$NGINX_ENABLE" == "true" ]]; then
        if [[ -z "$NGINX_DOMAIN" || -z "$NGINX_EMAIL" ]]; then
            echo -e "${RED}❌ Для --nginx нужны --nginx-domain и --nginx-email${NC}"
            has_error=1
        fi
    fi

    if [[ "$INSTALL_MODE" != "full" && "$NGINX_ENABLE" == "true" ]]; then
        echo -e "${RED}❌ Nginx доступен только в режиме full${NC}"
        has_error=1
    fi
    
    if [[ "$INSTALL_MODE" != "minimal" && "$INSTALL_MODE" != "full" ]]; then
        echo -e "${RED}❌ Неверный режим: $INSTALL_MODE (допустимо: minimal, full)${NC}"
        has_error=1
    fi
    
    if [[ "$INSTALL_MODE" == "full" ]]; then
        if [[ -z "$BOT_TOKEN" ]]; then
            echo -e "${RED}❌ Для full режима требуется --bot-token${NC}"
            has_error=1
        fi
        if [[ -z "$ADMIN_ID" ]]; then
            echo -e "${RED}❌ Для full режима требуется --admin-id${NC}"
            has_error=1
        fi
    fi

    if [[ "$HEADSCALE_ENABLE" == "true" ]]; then
        if [[ "$INSTALL_MODE" != "full" ]]; then
            echo -e "${RED}❌ Headscale доступен только в режиме full${NC}"
            has_error=1
        fi
        if [[ -z "$HEADSCALE_DOMAIN" ]]; then
            echo -e "${RED}❌ Для --headscale требуется --headscale-domain${NC}"
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
    echo -e "${BLUE}🔗 Проверка подключения к $SSH_HOST...${NC}"
    
    if ! ssh_cmd "echo 'Connection OK'" &> /dev/null; then
        echo -e "${RED}❌ Не удалось подключиться к серверу${NC}"
        echo "   Проверьте IP, порт, пользователя и пароль"
        exit 1
    fi
    
    echo -e "${GREEN}✅ Подключение успешно!${NC}"
    
    # Получаем информацию о сервере
    echo -e "${BLUE}📋 Информация о сервере:${NC}"
    ssh_cmd "uname -a && cat /etc/os-release | head -2"
}

run_installation() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}   📦 Начинаем установку в режиме: ${YELLOW}$INSTALL_MODE${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # Создаём временный скрипт установки
    local install_script
    if [[ "$INSTALL_MODE" == "minimal" ]]; then
        install_script=$(create_minimal_install_script)
    else
        install_script=$(create_full_install_script)
    fi
    
    # Копируем и выполняем скрипт
    echo -e "${BLUE}📤 Загрузка скрипта установки на сервер...${NC}"
    echo "$install_script" | ssh_cmd "cat > /tmp/install_vless.sh && chmod +x /tmp/install_vless.sh"
    
    echo -e "${BLUE}⚙️ Запуск установки (это может занять несколько минут)...${NC}"
    echo ""
    
    # Выполняем установку
    ssh_cmd "bash /tmp/install_vless.sh"
    
    echo ""
    echo -e "${GREEN}✅ Установка завершена!${NC}"
}

create_minimal_install_script() {
    # Передаём SSH порт для UFW и Fail2ban
    cat << SCRIPT_EOF
#!/bin/bash
# Минимальная установка: Xray + Hysteria2

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Параметры переданные из локального скрипта
SSH_PORT_FOR_UFW="$SSH_PORT"
HOME_DIR="\$HOME"
VLESS_PORT="$VLESS_PORT"
HY2_ENABLE="$HY2_ENABLE"
HY2_PORT="$HY2_PORT"
HY2_PASSWORD="$HY2_PASSWORD"

# Определяем нужен ли sudo
if [ "\$(id -u)" -ne 0 ]; then
    SUDO="sudo"
    echo -e "\${YELLOW}⚠️ Запуск от обычного пользователя, используем sudo\${NC}"
else
    SUDO=""
fi

# Подсчёт шагов
TOTAL_STEPS=6
if [ "\$HY2_ENABLE" = "true" ]; then
    TOTAL_STEPS=8
fi
STEP=0
next_step() { STEP=\$((STEP+1)); echo -e "\${YELLOW}[\${STEP}/\${TOTAL_STEPS}] \$1\${NC}"; }

echo -e "\${GREEN}=== Минимальная установка VLESS-Reality + Hysteria2 ===\${NC}"

# 1. Обновление системы
next_step "Обновление системы..."
\$SUDO apt-get update -qq
\$SUDO apt-get upgrade -y -qq

# 2. Установка базовых пакетов + security tools
next_step "Установка пакетов..."
\$SUDO apt-get install -y -qq curl jq openssl ca-certificates qrencode ufw fail2ban unattended-upgrades

# 3. Установка Xray
next_step "Установка Xray-core..."
if ! command -v xray &> /dev/null; then
    \$SUDO bash -c "\$(curl -sL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
else
    echo "Xray уже установлен"
fi

# 4. Генерация ключей VLESS
next_step "Генерация ключей VLESS..."
UUID=\$(xray uuid)
X25519_OUTPUT=\$(/usr/local/bin/xray x25519 2>/dev/null)
PRIVATE_KEY=\$(echo "\$X25519_OUTPUT" | grep -i "private" | awk -F': ' '{print \$2}' | tr -d ' ')
PUBLIC_KEY=\$(echo "\$X25519_OUTPUT" | grep -i "public" | awk -F': ' '{print \$2}' | tr -d ' ')
SHORT_ID=\$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 8)
SERVER_IP=\$(curl -s https://api.ipify.org || curl -s https://ifconfig.me/ip)

# Проверка что ключи сгенерированы
if [ -z "\$PRIVATE_KEY" ] || [ -z "\$PUBLIC_KEY" ]; then
    echo -e "\${YELLOW}⚠️ Повторная генерация ключей...\${NC}"
    X25519_OUTPUT=\$(/usr/local/bin/xray x25519)
    PRIVATE_KEY=\$(echo "\$X25519_OUTPUT" | head -1 | awk -F': ' '{print \$2}' | tr -d ' ')
    PUBLIC_KEY=\$(echo "\$X25519_OUTPUT" | tail -1 | awk -F': ' '{print \$2}' | tr -d ' ')
fi

echo "UUID: \$UUID"
echo "Private Key: [hidden; stored on server only]"
echo "Public Key: \${PUBLIC_KEY:0:10}..."

# yahoo.com по умолчанию: www.microsoft.com у ряда мобильных операторов
# блокируется DPI. Сменить потом можно из бота: /vless_set_sni
SNI="yahoo.com"
FINGERPRINT="chrome"
PORT=\$VLESS_PORT

# 5. Создание конфигурации Xray
next_step "Конфигурация и запуск Xray..."
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
    next_step "Установка Hysteria2..."
    if ! command -v hysteria &> /dev/null; then
        bash <(curl -fsSL https://get.hy2.sh/)
    else
        echo "Hysteria2 уже установлен"
    fi

    next_step "Конфигурация и запуск Hysteria2..."

    # Генерация пароля Hysteria2
    if [ -z "\$HY2_PASSWORD" ]; then
        HY2_PASSWORD=\$(openssl rand -base64 16 | tr -d '=+/' | head -c 22)
    fi

    # TLS сертификат для Hysteria2
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

    # Конфигурация Hysteria2
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

    # Systemd сервис
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
        echo -e "\${GREEN}✅ Hysteria2 запущен на UDP порту \$HY2_PORT\${NC}"
    else
        echo -e "\${YELLOW}⚠️ Hysteria2 не запустился, проверьте: journalctl -u hysteria-server -n 20\${NC}"
    fi

    HY2_LINK="hy2://\${HY2_PASSWORD}@\${SERVER_IP}:\${HY2_PORT}/?insecure=1#Hysteria2"
fi

# Security Hardening
next_step "Настройка безопасности..."

# UFW Firewall
\$SUDO ufw default deny incoming
\$SUDO ufw default allow outgoing
\$SUDO ufw allow \$SSH_PORT_FOR_UFW/tcp   # SSH
\$SUDO ufw allow \$PORT/tcp               # VLESS (TCP)
if [ "\$HY2_ENABLE" = "true" ]; then
    \$SUDO ufw allow \$HY2_PORT/udp         # Hysteria2 (UDP)
fi
\$SUDO ufw --force enable

# Fail2ban с учётом SSH порта
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

# Автообновления безопасности
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

echo -e "\${GREEN}✅ UFW, Fail2ban, автообновления настроены\${NC}"

# Создаём файл с конфигурацией для клиента
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

# Сохраняем JSON конфиг
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
echo -e "\${GREEN}   ✅ Установка завершена!\${NC}"
echo -e "\${GREEN}═══════════════════════════════════════════════════════════════\${NC}"
echo ""
echo "📄 Конфигурация сохранена в: \$HOME_DIR/vless_client_config.txt"
echo ""
cat \$HOME_DIR/vless_client_config.txt
SCRIPT_EOF
}

create_full_install_script() {
    # Экранируем переменные которые должны быть подставлены
    cat << SCRIPT_EOF
#!/bin/bash
# Полная установка: Docker + TelegramHelper + Xray + Hysteria2

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

# Определяем нужен ли sudo
if [ "\$(id -u)" -ne 0 ]; then
    SUDO="sudo"
    echo -e "\${YELLOW}⚠️ Запуск от обычного пользователя, используем sudo\${NC}"
else
    SUDO=""
fi

# Подсчёт шагов
TOTAL_STEPS=9
if [ "\$HY2_ENABLE" = "true" ]; then
    TOTAL_STEPS=11
fi
STEP=0
next_step() { STEP=\$((STEP+1)); echo -e "\${YELLOW}[\${STEP}/\${TOTAL_STEPS}] \$1\${NC}"; }

echo -e "\${GREEN}=== Полная установка VLESS-Reality + Hysteria2 + TelegramHelper ===\${NC}"

# 1. Обновление системы
next_step "Обновление системы..."
\$SUDO apt-get update -qq
\$SUDO apt-get upgrade -y -qq

# 2. Установка базовых пакетов + security tools
next_step "Установка пакетов..."
\$SUDO apt-get install -y -qq curl jq openssl ca-certificates qrencode git python3 python3-pip ufw fail2ban unattended-upgrades

# 3. Установка Docker
next_step "Установка Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | \$SUDO sh
    \$SUDO systemctl enable docker
    \$SUDO systemctl start docker
    if [ "\$(id -u)" -ne 0 ]; then
        \$SUDO usermod -aG docker \$USER
        echo -e "\${YELLOW}⚠️ Пользователь добавлен в группу docker. Требуется перелогин.\${NC}"
    fi
else
    echo "Docker уже установлен"
fi

# 4. Установка Xray
next_step "Установка Xray-core..."
if ! command -v xray &> /dev/null; then
    \$SUDO bash -c "\$(curl -sL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
else
    echo "Xray уже установлен"
fi

# 5. Генерация ключей VLESS
next_step "Генерация ключей VLESS..."
UUID=\$(xray uuid)
X25519_OUTPUT=\$(/usr/local/bin/xray x25519 2>/dev/null)
PRIVATE_KEY=\$(echo "\$X25519_OUTPUT" | grep -i "private" | awk -F': ' '{print \$2}' | tr -d ' ')
PUBLIC_KEY=\$(echo "\$X25519_OUTPUT" | grep -i "public" | awk -F': ' '{print \$2}' | tr -d ' ')
SHORT_ID=\$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 8)
SERVER_IP=\$(curl -s https://api.ipify.org || curl -s https://ifconfig.me/ip)

if [ -z "\$PRIVATE_KEY" ] || [ -z "\$PUBLIC_KEY" ]; then
    echo -e "\${YELLOW}⚠️ Повторная генерация ключей...\${NC}"
    X25519_OUTPUT=\$(/usr/local/bin/xray x25519)
    PRIVATE_KEY=\$(echo "\$X25519_OUTPUT" | head -1 | awk -F': ' '{print \$2}' | tr -d ' ')
    PUBLIC_KEY=\$(echo "\$X25519_OUTPUT" | tail -1 | awk -F': ' '{print \$2}' | tr -d ' ')
fi

echo "UUID: \$UUID"
echo "Private Key: [hidden; stored on server only]"
echo "Public Key: \${PUBLIC_KEY:0:10}..."

# Генерация ключей шифрования
API_KEY=\$(python3 -c "import secrets; print(secrets.token_hex(32))")
HMAC_KEY=\$(openssl rand -hex 32)
ENC_KEY=\$(python3 -c "import secrets; print(secrets.token_hex(32))")

# yahoo.com по умолчанию: www.microsoft.com у ряда мобильных операторов
# блокируется DPI. Сменить потом можно из бота: /vless_set_sni
SNI="yahoo.com"
FINGERPRINT="chrome"
PORT=\$VLESS_PORT

# 6. Конфигурация Xray + TelegramHelper
next_step "Конфигурация Xray и TelegramHelper..."
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

# Подготовка TelegramHelper
PROJECT_DIR="/opt/TelegramHelper"
\$SUDO mkdir -p \$PROJECT_DIR
if [ "\$(id -u)" -ne 0 ]; then
    \$SUDO chown -R \$USER:\$USER \$PROJECT_DIR
fi

# Создаём .env для бота
cat > \$PROJECT_DIR/.env << EOF
BOT_TOKEN=\$BOT_TOKEN
ADMIN_USER_IDS=\$ADMIN_ID
API_SECRET_KEY=\$API_KEY
HMAC_SECRET=\$HMAC_KEY
ENCRYPTION_KEY=\$ENC_KEY
API_URL=http://\$SERVER_IP:8000/ai_query
EOF

# Добавляем AI ключи если указаны
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

# Создаём vless_config.json
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

# Создаём app_keys.json (ВАЖНО: создать ДО docker compose!)
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
    next_step "Установка Hysteria2..."
    if ! command -v hysteria &> /dev/null; then
        bash <(curl -fsSL https://get.hy2.sh/)
    else
        echo "Hysteria2 уже установлен"
    fi

    next_step "Конфигурация и запуск Hysteria2..."

    # Генерация пароля Hysteria2
    if [ -z "\$HY2_PASSWORD" ]; then
        HY2_PASSWORD=\$(openssl rand -base64 16 | tr -d '=+/' | head -c 22)
    fi

    # TLS сертификат
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

    # Конфигурация Hysteria2
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

    # Systemd сервис
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
        echo -e "\${GREEN}✅ Hysteria2 запущен на UDP порту \$HY2_PORT\${NC}"
    else
        echo -e "\${YELLOW}⚠️ Hysteria2 не запустился, проверьте: journalctl -u hysteria-server -n 20\${NC}"
    fi

    HY2_LINK="hy2://\${HY2_PASSWORD}@\${SERVER_IP}:\${HY2_PORT}/?insecure=1#Hysteria2"

    # Создаём hysteria2_config.json для TelegramHelper
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

# Nginx + SSL (опционально)
if [ "\$NGINX_ENABLE" = "true" ]; then
    next_step "Установка Nginx + SSL..."
    \$SUDO apt-get install -y -qq nginx certbot
    \$SUDO systemctl enable nginx
    \$SUDO systemctl start nginx

    WEBROOT="/var/www/certbot"
    \$SUDO mkdir -p "\$WEBROOT"

    if [ -z "\$NGINX_HTTPS_PORT" ]; then
        if [ "\$PORT" = "443" ]; then
            NGINX_HTTPS_PORT="8443"
            echo -e "\${YELLOW}⚠️ VLESS использует 443, Nginx будет слушать 8443\${NC}"
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
next_step "Настройка безопасности..."

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

# Автообновления
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

echo -e "\${GREEN}✅ UFW, Fail2ban, автообновления настроены\${NC}"

# Финализация
next_step "Финализация..."

VLESS_LINK="vless://\${UUID}@\${SERVER_IP}:\${PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=\${SNI}&fp=\${FINGERPRINT}&pbk=\${PUBLIC_KEY}&sid=\${SHORT_ID}&type=tcp#VPS-Reality"

# Клиентский конфиг (текст)
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
🔐 API Keys (для TelegramHelper):

API_SECRET_KEY: \$API_KEY
ENCRYPTION_KEY: \$ENC_KEY
HMAC_SECRET:    \$HMAC_KEY

🩺 Dockhand (Diagnostics):
SSH Tunnel: ssh -L 8501:localhost:8501 -p \$SSH_PORT_FOR_UFW \$SSH_USER@\$SERVER_IP
URL:        http://localhost:8501

═══════════════════════════════════════════════════════════════
EOF

# Клиентский конфиг (JSON)
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
    echo -e "\${GREEN}[Headscale] Установка Headscale...\${NC}"

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
    echo -e "\${GREEN}[Headscale] ✅ Headscale запущен\${NC}"

    # Configure Nginx SNI routing if Nginx is enabled
    if [ "\$NGINX_ENABLE" = "true" ]; then
        echo -e "\${GREEN}[Headscale] Настройка Nginx SNI routing...\${NC}"

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
        echo -e "\${GREEN}[Headscale] ✅ Nginx SNI routing настроен\${NC}"
    fi

    # Save Headscale info
    cat > \$HOME_DIR/headscale_info.txt << HS_INFO_EOF
=== Headscale Info ===
URL: https://\$HEADSCALE_DOMAIN
Container: headscale

Generate Pre-Auth key:
  docker exec headscale headscale users list
  docker exec headscale headscale preauthkeys create --user 1 --reusable --expiration 24h
  # --user = числовой ID из users list, не username

Connect client:
  tailscale up --login-server https://\$HEADSCALE_DOMAIN --authkey <KEY>
HS_INFO_EOF
    echo -e "\${GREEN}[Headscale] Информация сохранена в \$HOME_DIR/headscale_info.txt\${NC}"
fi

echo ""
echo -e "\${GREEN}═══════════════════════════════════════════════════════════════\${NC}"
echo -e "\${GREEN}   ✅ Полная установка завершена!\${NC}"
echo -e "\${GREEN}═══════════════════════════════════════════════════════════════\${NC}"
echo ""
echo "📄 Конфигурация: \$HOME_DIR/vless_client_config.txt"
echo "📁 Проект: \$PROJECT_DIR"
echo ""
echo "Следующие шаги:"
echo "1. Скопируйте проект TelegramHelper в \$PROJECT_DIR"
echo "2. Запустите: cd \$PROJECT_DIR && docker compose up -d --build"
echo ""
cat \$HOME_DIR/vless_client_config.txt
SCRIPT_EOF
}

download_config() {
    echo ""
    echo -e "${BLUE}📥 Скачивание конфигурации с сервера...${NC}"

    # Создаём директорию
    mkdir -p "$OUTPUT_DIR"

    # Определяем домашнюю директорию на сервере
    REMOTE_HOME=$(ssh_cmd 'echo $HOME')

    # Скачиваем текстовый конфиг
    scp_cmd "$SSH_USER@$SSH_HOST:${REMOTE_HOME}/vless_client_config.txt" "$OUTPUT_DIR/vless_config_${SSH_HOST}.txt"

    # Скачиваем JSON конфиг
    scp_cmd "$SSH_USER@$SSH_HOST:${REMOTE_HOME}/vless_client_config.json" "$OUTPUT_DIR/vless_config_${SSH_HOST}.json"

    echo -e "${GREEN}✅ Конфигурация сохранена в:${NC}"
    echo "   📄 $OUTPUT_DIR/vless_config_${SSH_HOST}.txt"
    echo "   📄 $OUTPUT_DIR/vless_config_${SSH_HOST}.json"
    echo ""

    # Предлагаем зашифровать
    echo -e "${YELLOW}💡 Для безопасной передачи рекомендуется зашифровать конфиг:${NC}"
    echo "   python3 scripts/secure_config_transfer.py encrypt $OUTPUT_DIR/vless_config_${SSH_HOST}.json"
}

print_final_summary() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}   🎉 Всё готово!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${CYAN}Установленные протоколы:${NC}"
    echo "  🛡️  VLESS-Reality  — TCP порт $VLESS_PORT"
    if [[ "$HY2_ENABLE" == "true" ]]; then
        echo "  ⚡ Hysteria2      — UDP порт $HY2_PORT"
    fi
    if [[ "$WITH_MAINTENANCE" == "true" ]]; then
        echo "  🧹 Авто-чистка    — systemd-таймер (вс 04:00 UTC)"
    fi
    echo ""
    echo "Теперь вы можете:"
    echo "1. Импортировать ссылки в приложение (Hiddify, v2rayNG, NekoRay)"
    echo "2. Использовать конфигурацию из файла"
    echo ""

    local SSH_CMD="ssh"
    if [[ "$SSH_PORT" != "22" ]]; then
        SSH_CMD="ssh -p $SSH_PORT"
    fi
    SSH_CMD="$SSH_CMD $SSH_USER@$SSH_HOST"

    if [[ "$INSTALL_MODE" == "full" ]]; then
        echo "Для запуска бота на сервере:"
        echo "  $SSH_CMD"
        echo "  cd /opt/TelegramHelper && docker compose up -d --build"
        echo ""
        echo "Доступные команды бота:"
        echo "  /vless_status — статус VLESS-Reality"
        if [[ "$HY2_ENABLE" == "true" ]]; then
            echo "  /hy2_status   — статус Hysteria2"
        fi
    fi
}

install_maintenance() {
    if [[ "$WITH_MAINTENANCE" != "true" ]]; then
        echo -e "${YELLOW}⚠️  Авто-чистка диска отключена (--no-maintenance)${NC}"
        return 0
    fi

    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local local_script="$script_dir/vps_maintenance.sh"

    if [[ ! -f "$local_script" ]]; then
        echo -e "${YELLOW}⚠️  $local_script не найден — пропускаю установку авто-чистки${NC}"
        return 0
    fi

    echo ""
    echo -e "${BLUE}🧹 Установка авто-чистки диска (vps_maintenance.sh)...${NC}"

    if ! scp_cmd "$local_script" "$SSH_USER@$SSH_HOST:/tmp/vps_maintenance.sh" &> /dev/null; then
        echo -e "${YELLOW}⚠️  Не удалось скопировать vps_maintenance.sh — пропускаю${NC}"
        return 0
    fi

    # Скрипт требует root; если SSH_USER не root — пробуем через sudo
    ssh_cmd "chmod +x /tmp/vps_maintenance.sh && \
        if [ \$(id -u) -eq 0 ]; then bash /tmp/vps_maintenance.sh --install; \
        else sudo bash /tmp/vps_maintenance.sh --install; fi" \
        || echo -e "${YELLOW}⚠️  Установка авто-чистки завершилась с ошибкой (не критично, можно поставить руками: ./scripts/vps_maintenance.sh --install)${NC}"
}

cleanup() {
    echo -e "${BLUE}🧹 Очистка временных файлов на сервере...${NC}"
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

# Обработка Ctrl+C
trap cleanup EXIT

main "$@"

