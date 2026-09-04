#!/bin/bash
# ============================================================================
# 🧹 TelegramHelper — Полная очистка сервера
# ============================================================================
# Скрипт для удаления ВСЕГО, связанного с TelegramHelper, с VPS сервера.
# Использовать перед переустановкой с нуля.
#
# Использование:
#   ./cleanup_server.sh           # Интерактивный режим (с подтверждением)
#   ./cleanup_server.sh --force   # Без подтверждения (опасно!)
#   ./cleanup_server.sh --dry-run # Показать что будет удалено (без удаления)
#
# ============================================================================

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Конфигурация
APP_DIR="/opt/TelegramHelper"
CONTAINER_NAME="telegram-helper-lite"
IMAGE_NAME="telegram-helper-lite"
DOCKHAND_CONTAINER="dockhand"
DOCKHAND_IMAGE="dockhand"
BACKUP_DIR="/tmp/telegramhelper_backup_$(date +%Y%m%d_%H%M%S)"

# Флаги
FORCE_MODE=false
DRY_RUN=false

# Парсинг аргументов
for arg in "$@"; do
    case $arg in
        --force|-f)
            FORCE_MODE=true
            ;;
        --dry-run|-n)
            DRY_RUN=true
            ;;
        --help|-h)
            echo "🧹 TelegramHelper — Скрипт очистки сервера"
            echo ""
            echo "Использование:"
            echo "  ./cleanup_server.sh           # Интерактивный режим"
            echo "  ./cleanup_server.sh --force   # Без подтверждения"
            echo "  ./cleanup_server.sh --dry-run # Показать что будет удалено"
            echo ""
            echo "Флаги:"
            echo "  -f, --force    Удалить без подтверждения"
            echo "  -n, --dry-run  Только показать что будет удалено"
            echo "  -h, --help     Показать эту справку"
            exit 0
            ;;
    esac
done

# Функция для вывода
log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

log_action() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}[DRY-RUN] $1${NC}"
    else
        echo -e "${BLUE}▶️  $1${NC}"
    fi
}

# Функция выполнения команды (с учётом dry-run)
run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}   Команда: $*${NC}"
    else
        eval "$@"
    fi
}

# Определяем команды Docker
if [ "$EUID" -ne 0 ]; then
    DOCKER_CMD="sudo docker"
    COMPOSE_CMD="sudo docker compose"
else
    DOCKER_CMD="docker"
    COMPOSE_CMD="docker compose"
fi

echo ""
echo "=============================================="
echo "🧹 TelegramHelper — Очистка сервера"
echo "=============================================="
echo ""

if [ "$DRY_RUN" = true ]; then
    log_warning "РЕЖИМ DRY-RUN — ничего не будет удалено"
    echo ""
fi

# Показываем что будет удалено
echo "📋 Будет удалено:"
echo ""

# 1. Docker контейнер
CONTAINER_EXISTS=$($DOCKER_CMD ps -a --filter "name=$CONTAINER_NAME" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$CONTAINER_EXISTS" ]; then
    echo "   🐳 Контейнер: $CONTAINER_NAME"
else
    echo "   🐳 Контейнер: $CONTAINER_NAME (не найден)"
fi

# 1.1 Dockhand контейнер
DOCKHAND_EXISTS=$($DOCKER_CMD ps -a --filter "name=$DOCKHAND_CONTAINER" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$DOCKHAND_EXISTS" ]; then
    echo "   🐳 Контейнер: $DOCKHAND_CONTAINER"
else
    echo "   🐳 Контейнер: $DOCKHAND_CONTAINER (не найден)"
fi

# 2. Docker образ
IMAGE_EXISTS=$($DOCKER_CMD images "$IMAGE_NAME" --format "{{.Repository}}:{{.Tag}}" 2>/dev/null || true)
if [ -n "$IMAGE_EXISTS" ]; then
    echo "   📦 Образ: $IMAGE_EXISTS"
else
    echo "   📦 Образ: $IMAGE_NAME (не найден)"
fi

# 2.1 Dockhand образ
DOCKHAND_IMG_EXISTS=$($DOCKER_CMD images "$DOCKHAND_IMAGE" --format "{{.Repository}}:{{.Tag}}" 2>/dev/null || true)
if [ -n "$DOCKHAND_IMG_EXISTS" ]; then
    echo "   📦 Образ: $DOCKHAND_IMG_EXISTS"
else
    echo "   📦 Образ: $DOCKHAND_IMAGE (не найден)"
fi

# 3. Директория приложения
if [ -d "$APP_DIR" ]; then
    APP_SIZE=$(du -sh "$APP_DIR" 2>/dev/null | cut -f1 || echo "?")
    echo "   📁 Директория: $APP_DIR ($APP_SIZE)"
    
    # Показываем важные файлы
    if [ -f "$APP_DIR/.env" ]; then
        echo "      └── .env (конфигурация)"
    fi
    if [ -f "$APP_DIR/app_keys.json" ]; then
        echo "      └── app_keys.json (API ключи)"
    fi
    if [ -f "$APP_DIR/users.json" ]; then
        echo "      └── users.json (данные пользователей)"
    fi
    if [ -f "$APP_DIR/vless_config.json" ]; then
        echo "      └── vless_config.json (VLESS конфиг)"
    fi
else
    echo "   📁 Директория: (не найдена)"
fi

# 4. Docker volumes (если есть)
VOLUMES=$($DOCKER_CMD volume ls --filter "name=telegram" --format "{{.Name}}" 2>/dev/null || true)
if [ -n "$VOLUMES" ]; then
    echo "   💾 Volumes:"
    echo "$VOLUMES" | while read vol; do
        echo "      └── $vol"
    done
fi

echo ""

# Подтверждение (если не force режим)
if [ "$FORCE_MODE" = false ] && [ "$DRY_RUN" = false ]; then
    echo -e "${RED}⚠️  ВНИМАНИЕ: Это действие необратимо!${NC}"
    echo ""
    read -p "Создать бэкап .env и ключей перед удалением? (Y/n): " backup_response
    backup_response=${backup_response:-Y}
    
    if [[ "$backup_response" =~ ^[Yy]$ ]]; then
        DO_BACKUP=true
    else
        DO_BACKUP=false
    fi
    
    echo ""
    read -p "Вы уверены, что хотите удалить ВСЁ? (введите 'DELETE' для подтверждения): " confirm
    
    if [ "$confirm" != "DELETE" ]; then
        log_error "Отменено. Введите 'DELETE' для подтверждения."
        exit 1
    fi
else
    DO_BACKUP=true
fi

echo ""

# === СОЗДАНИЕ БЭКАПА ===
if [ "$DO_BACKUP" = true ] && [ -d "$APP_DIR" ]; then
    log_action "Создание бэкапа в $BACKUP_DIR"
    
    if [ "$DRY_RUN" = false ]; then
        mkdir -p "$BACKUP_DIR"
        
        # Копируем важные файлы
        for file in .env app_keys.json users.json vless_config.json; do
            if [ -f "$APP_DIR/$file" ]; then
                cp "$APP_DIR/$file" "$BACKUP_DIR/"
                log_success "Сохранён: $file"
            fi
        done
        
        echo ""
        log_success "Бэкап создан: $BACKUP_DIR"
        echo "   Для восстановления:"
        echo "   cp $BACKUP_DIR/.env $APP_DIR/"
        echo ""
    fi
fi

# === ОСТАНОВКА КОНТЕЙНЕРА ===
log_action "Остановка Docker контейнера..."

if [ -n "$CONTAINER_EXISTS" ]; then
    if [ -f "$APP_DIR/compose.yaml" ] || [ -f "$APP_DIR/docker-compose.yml" ]; then
        run_cmd "cd $APP_DIR && $COMPOSE_CMD down 2>/dev/null || true"
    else
        run_cmd "$DOCKER_CMD stop $CONTAINER_NAME 2>/dev/null || true"
        run_cmd "$DOCKER_CMD rm $CONTAINER_NAME 2>/dev/null || true"
    fi
    log_success "Контейнер $CONTAINER_NAME остановлен и удалён"
else
    log_info "Контейнер $CONTAINER_NAME не запущен"
fi

# === ОСТАНОВКА DOCKHAND ===
if [ -n "$DOCKHAND_EXISTS" ]; then
    run_cmd "$DOCKER_CMD stop $DOCKHAND_CONTAINER 2>/dev/null || true"
    run_cmd "$DOCKER_CMD rm $DOCKHAND_CONTAINER 2>/dev/null || true"
    log_success "Контейнер $DOCKHAND_CONTAINER остановлен и удалён"
fi

# === УДАЛЕНИЕ ОБРАЗА ===
log_action "Удаление Docker образа..."

if [ -n "$IMAGE_EXISTS" ]; then
    run_cmd "$DOCKER_CMD rmi $IMAGE_NAME:latest 2>/dev/null || true"
    run_cmd "$DOCKER_CMD rmi $IMAGE_NAME 2>/dev/null || true"
    log_success "Образ удалён"
else
    log_info "Образ $IMAGE_NAME не найден"
fi

if [ -n "$DOCKHAND_IMG_EXISTS" ]; then
    run_cmd "$DOCKER_CMD rmi $DOCKHAND_IMAGE:latest 2>/dev/null || true"
    run_cmd "$DOCKER_CMD rmi $DOCKHAND_IMAGE 2>/dev/null || true"
    log_success "Образ $DOCKHAND_IMAGE удалён"
fi

# === УДАЛЕНИЕ VOLUMES ===
if [ -n "$VOLUMES" ]; then
    log_action "Удаление Docker volumes..."
    echo "$VOLUMES" | while read vol; do
        run_cmd "$DOCKER_CMD volume rm $vol 2>/dev/null || true"
    done
    log_success "Volumes удалены"
fi

# === ОЧИСТКА DOCKER ===
log_action "Очистка неиспользуемых Docker ресурсов..."
run_cmd "$DOCKER_CMD system prune -f 2>/dev/null || true"
log_success "Docker очищен"

# === УДАЛЕНИЕ ДИРЕКТОРИИ ===
log_action "Удаление директории $APP_DIR..."

if [ -d "$APP_DIR" ]; then
    run_cmd "rm -rf $APP_DIR"
    log_success "Директория удалена"
else
    log_info "Директория не найдена"
fi

# === ИТОГ ===
echo ""
echo "=============================================="

if [ "$DRY_RUN" = true ]; then
    log_warning "DRY-RUN завершён. Ничего не было удалено."
    echo ""
    echo "Для реального удаления запустите без --dry-run"
else
    log_success "🧹 Очистка завершена!"
    echo ""
    echo "📋 Что было сделано:"
    echo "   • Docker контейнер остановлен и удалён"
    echo "   • Docker образ удалён"
    echo "   • Директория $APP_DIR удалена"
    echo "   • Неиспользуемые Docker ресурсы очищены"
    
    if [ "$DO_BACKUP" = true ]; then
        echo ""
        echo "📦 Бэкап сохранён в: $BACKUP_DIR"
    fi
    
    echo ""
    echo "🚀 Для переустановки:"
    echo "   1. Создайте директорию: mkdir -p /opt/TelegramHelper"
    echo "   2. ⚠️  ВАЖНО: Создайте файлы данных (rsync их НЕ скопирует):"
    echo "      cd /opt/TelegramHelper"
    echo "      echo '{\"app_keys\": {}, \"default\": {}}' > app_keys.json"
    echo "      echo '{}' > users.json"
    echo "      echo '{}' > vless_config.json"
    echo "      touch bot.log"
    if [ "$DO_BACKUP" = true ]; then
        echo "      cp $BACKUP_DIR/.env .env  # восстановить конфиг"
    fi
    echo "   3. Скопируйте код с локальной машины (rsync)"
    echo "   4. Установите права: chmod 666 app_keys.json users.json vless_config.json bot.log"
    echo "   5. Запустите: docker compose up -d --build"
fi

echo ""
echo "=============================================="
