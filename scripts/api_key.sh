#!/bin/bash
# ============================================================
# API Key Manager для TelegramSimple
# Управление API ключом для интеграции с ApiAi
# ============================================================

set -e

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Определяем директорию проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"

# Функция генерации ключа
generate_key() {
    python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
    openssl rand -hex 32 2>/dev/null || \
    head -c 32 /dev/urandom | xxd -p
}

# Функция получения текущего ключа
get_current_key() {
    if [[ -f "$ENV_FILE" ]]; then
        grep "^API_SECRET_KEY=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d "'"
    fi
}

# Функция установки ключа
set_key() {
    local key="$1"
    
    if [[ ! -f "$ENV_FILE" ]]; then
        echo -e "${YELLOW}⚠️  Файл .env не найден, создаю из example.env...${NC}"
        if [[ -f "$PROJECT_DIR/example.env" ]]; then
            cp "$PROJECT_DIR/example.env" "$ENV_FILE"
        else
            touch "$ENV_FILE"
        fi
    fi
    
    # Проверяем есть ли уже ключ
    if grep -q "^API_SECRET_KEY=" "$ENV_FILE" 2>/dev/null; then
        # macOS/Linux совместимость для sed
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^API_SECRET_KEY=.*|API_SECRET_KEY=$key|" "$ENV_FILE"
        else
            sed -i "s|^API_SECRET_KEY=.*|API_SECRET_KEY=$key|" "$ENV_FILE"
        fi
    else
        echo "API_SECRET_KEY=$key" >> "$ENV_FILE"
    fi
}

# Функция установки URL
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

# Функция показа информации
show_info() {
    local key=$(get_current_key)
    local url=$(grep "^API_URL=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2)
    
    echo ""
    echo -e "${BLUE}🔐 API для ApiAi${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    if [[ -n "$key" ]]; then
        echo -e "${GREEN}📍 URL:${NC} ${url:-не установлен}"
        echo -e "${GREEN}🔑 Key:${NC} $key"
        echo ""
        echo -e "${YELLOW}📋 Скопируйте эти данные в ApiAi:${NC}"
        echo "   Настройки → API ключи → Telegram Bot API"
    else
        echo -e "${RED}❌ API ключ не установлен${NC}"
        echo "   Запустите: $0 generate"
    fi
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

# Главное меню
show_help() {
    echo ""
    echo -e "${BLUE}🔧 API Key Manager${NC}"
    echo ""
    echo "Использование: $0 <команда>"
    echo ""
    echo "Команды:"
    echo "  generate    Сгенерировать новый API ключ"
    echo "  show        Показать текущий ключ"
    echo "  set <key>   Установить конкретный ключ"
    echo "  reset       Сбросить и сгенерировать новый ключ"
    echo "  init        Инициализация (генерация если нет ключа)"
    echo ""
    echo "Примеры:"
    echo "  $0 generate     # Новый ключ"
    echo "  $0 show         # Показать"
    echo "  $0 init         # Первоначальная настройка"
    echo ""
}

# Основная логика
case "${1:-}" in
    generate|new)
        echo -e "${BLUE}🔄 Генерация нового API ключа...${NC}"
        NEW_KEY=$(generate_key)
        set_key "$NEW_KEY"
        # Detect Public IP
        PUBLIC_IP=$(curl -s ifconfig.me || echo "localhost")
        set_url "http://$PUBLIC_IP:8000/ai_query"
        echo -e "${GREEN}✅ Новый ключ сгенерирован!${NC}"
        show_info
        echo -e "${YELLOW}⚠️  Не забудьте перезапустить контейнеры:${NC}"
        echo "   docker compose down && docker compose up -d"
        ;;
        
    show|info)
        show_info
        ;;
        
    set)
        if [[ -z "${2:-}" ]]; then
            echo -e "${RED}❌ Укажите ключ: $0 set <key>${NC}"
            exit 1
        fi
        set_key "$2"
        echo -e "${GREEN}✅ Ключ установлен!${NC}"
        show_info
        ;;
        
    reset)
        echo -e "${YELLOW}⚠️  Сброс API ключа...${NC}"
        NEW_KEY=$(generate_key)
        set_key "$NEW_KEY"
        echo -e "${GREEN}✅ Ключ сброшен и сгенерирован новый!${NC}"
        show_info
        echo -e "${YELLOW}⚠️  Не забудьте:${NC}"
        echo "   1. Перезапустить контейнеры: docker compose down && docker compose up -d"
        echo "   2. Обновить ключ в ApiAi"
        ;;
        
    init)
        CURRENT_KEY=$(get_current_key)
        if [[ -z "$CURRENT_KEY" || "$CURRENT_KEY" == "your_very_long_random_secret_key_here_64_chars_minimum" ]]; then
            echo -e "${BLUE}🔄 Инициализация API ключа...${NC}"
            NEW_KEY=$(generate_key)
            set_key "$NEW_KEY"
            # Detect Public IP
            PUBLIC_IP=$(curl -s ifconfig.me || echo "localhost")
            set_url "http://$PUBLIC_IP:8000/ai_query"
            echo -e "${GREEN}✅ API ключ сгенерирован!${NC}"
            show_info
        else
            echo -e "${GREEN}✅ API ключ уже настроен${NC}"
            show_info
        fi
        ;;
        
    *)
        show_help
        ;;
esac

