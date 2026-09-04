#!/bin/bash
# =============================================================================
# vless_sync.sh - Скрипт для получения VLESS-Reality конфигурации для клиента
# 
# Использование:
#   ./vless_sync.sh              # Обычный вывод
#   ./vless_sync.sh --json       # JSON формат для автоматической обработки
#   ./vless_sync.sh --qr         # Показать QR-код (требует qrencode)
#
# Этот скрипт читает /usr/local/etc/xray/config.json и извлекает:
# - Private Key → Public Key (через xray x25519 -i)
# - UUID, Short ID, Port, SNI, Fingerprint
# - Генерирует VLESS://... ссылку для клиента
# =============================================================================

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Пути
XRAY_CONFIG="/usr/local/etc/xray/config.json"
XRAY_BIN="/usr/local/bin/xray"

# Флаги
OUTPUT_JSON=false
SHOW_QR=false

# Парсинг аргументов
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

# Проверяем наличие jq
if ! command -v jq &> /dev/null; then
    if [ "$OUTPUT_JSON" = false ]; then
        echo -e "${RED}Ошибка: jq не установлен${NC}"
        echo "Установите: apt install jq"
    fi
    exit 1
fi

# Проверяем наличие конфига
if [ ! -f "$XRAY_CONFIG" ]; then
    if [ "$OUTPUT_JSON" = false ]; then
        echo -e "${RED}Ошибка: Конфигурация Xray не найдена${NC}"
        echo "Ожидаемый путь: $XRAY_CONFIG"
    else
        echo '{"error": "Config not found", "path": "'"$XRAY_CONFIG"'"}'
    fi
    exit 1
fi

# Проверяем xray
if [ ! -x "$XRAY_BIN" ]; then
    # Пробуем найти в PATH
    XRAY_BIN=$(which xray 2>/dev/null || echo "")
    if [ -z "$XRAY_BIN" ]; then
        if [ "$OUTPUT_JSON" = false ]; then
            echo -e "${RED}Ошибка: xray не найден${NC}"
            echo "Установите Xray или укажите правильный путь"
        else
            echo '{"error": "xray binary not found"}'
        fi
        exit 1
    fi
fi

# Читаем конфиг
CONFIG=$(cat "$XRAY_CONFIG")

# Извлекаем данные из конфига
# Ищем в inbounds -> streamSettings -> realitySettings
PRIVATE_KEY=$(echo "$CONFIG" | jq -r '.inbounds[0].streamSettings.realitySettings.privateKey // empty')
SHORT_IDS=$(echo "$CONFIG" | jq -r '.inbounds[0].streamSettings.realitySettings.shortIds[0] // empty')
SERVER_NAMES=$(echo "$CONFIG" | jq -r '.inbounds[0].streamSettings.realitySettings.serverNames[0] // empty')

# UUID из inbounds -> settings -> clients
UUID=$(echo "$CONFIG" | jq -r '.inbounds[0].settings.clients[0].id // empty')

# Порт
PORT=$(echo "$CONFIG" | jq -r '.inbounds[0].port // 443')

# Fingerprint (если есть)
FINGERPRINT="chrome"

# Проверяем что нашли приватный ключ
if [ -z "$PRIVATE_KEY" ]; then
    if [ "$OUTPUT_JSON" = false ]; then
        echo -e "${RED}Ошибка: Private Key не найден в конфиге${NC}"
        echo "Проверьте структуру конфига Xray"
    else
        echo '{"error": "Private key not found in config"}'
    fi
    exit 1
fi

# Получаем публичный ключ из приватного
# Note: разные версии xray выводят по-разному:
# - старые: "Public key: xxx"
# - некоторые: "Password: xxx" 
# - другие: просто две строки - private key и public key
XRAY_OUTPUT=$("$XRAY_BIN" x25519 -i "$PRIVATE_KEY" 2>&1)

# Пробуем разные форматы парсинга
# Формат 1: "Public key: xxx"
PUBLIC_KEY=$(echo "$XRAY_OUTPUT" | grep -i "public" | head -1 | sed 's/.*: *//' | tr -d ' \n\r')

# Формат 2: если не нашли, ищем "Password: xxx"
if [ -z "$PUBLIC_KEY" ]; then
    PUBLIC_KEY=$(echo "$XRAY_OUTPUT" | grep -i "password" | head -1 | sed 's/.*: *//' | tr -d ' \n\r')
fi

# Формат 3: если вывод - просто две строки (private и public), берём вторую
if [ -z "$PUBLIC_KEY" ]; then
    # Берём вторую непустую строку
    PUBLIC_KEY=$(echo "$XRAY_OUTPUT" | grep -v "^$" | sed -n '2p' | tr -d ' \n\r')
fi

# Формат 4: если -i флаг выводит только public, берём первую строку
if [ -z "$PUBLIC_KEY" ]; then
    PUBLIC_KEY=$(echo "$XRAY_OUTPUT" | head -1 | tr -d ' \n\r')
fi

if [ -z "$PUBLIC_KEY" ]; then
    if [ "$OUTPUT_JSON" = false ]; then
        echo -e "${RED}Ошибка: Не удалось получить Public Key${NC}"
        echo "Команда: $XRAY_BIN x25519 -i \"$PRIVATE_KEY\""
        echo ""
        echo "Вывод xray:"
        echo "$XRAY_OUTPUT"
    else
        echo '{"error": "Failed to derive public key", "output": "'"$(echo "$XRAY_OUTPUT" | head -5 | sed 's/"/\\"/g' | tr -d '\n')"'"}'
    fi
    exit 1
fi

# Определяем IP сервера
SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || \
            curl -s --max-time 5 https://ifconfig.me 2>/dev/null || \
            hostname -I 2>/dev/null | awk '{print $1}' || \
            echo "YOUR_SERVER_IP")

# SNI (Server Name Indicator)
SNI="${SERVER_NAMES:-yahoo.com}"

# Short ID
SHORT_ID="${SHORT_IDS:-}"

# Генерируем VLESS ссылку
# Формат: vless://uuid@server:port?encryption=none&flow=xtls-rprx-vision&security=reality&sni=sni&fp=fingerprint&pbk=public_key&sid=short_id&type=tcp#name
VLESS_LINK="vless://${UUID}@${SERVER_IP}:${PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=${SNI}&fp=${FINGERPRINT}&pbk=${PUBLIC_KEY}&sid=${SHORT_ID}&type=tcp#ApiAi-VPS"

# Вывод
if [ "$OUTPUT_JSON" = true ]; then
    # JSON вывод для автоматической обработки
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
    # Красивый вывод для человека
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}           🛡️  VLESS-Reality Configuration for Client          ${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${CYAN}Скопируйте эти значения в ApiAi → Settings → Reality:${NC}"
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
    echo -e "${CYAN}🔗 VLESS Link (для Hiddify/Foxray/v2rayNG):${NC}"
    echo ""
    echo -e "${BLUE}${VLESS_LINK}${NC}"
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    
    # QR код если запрошено
    if [ "$SHOW_QR" = true ]; then
        if command -v qrencode &> /dev/null; then
            echo ""
            echo -e "${CYAN}📱 QR-код для мобильных клиентов:${NC}"
            echo ""
            qrencode -t ANSIUTF8 "$VLESS_LINK"
        else
            echo -e "${YELLOW}Для QR-кода установите: apt install qrencode${NC}"
        fi
    fi
fi
