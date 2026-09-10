#!/bin/bash
# ============================================================================
# 🧹 TelegramHelper — full server cleanup
# ============================================================================
# Removes EVERYTHING related to TelegramHelper from the VPS.
# Use before a from-scratch reinstall.
#
# Usage:
#   ./cleanup_server.sh           # Interactive mode (with confirmation)
#   ./cleanup_server.sh --force   # No confirmation (dangerous!)
#   ./cleanup_server.sh --dry-run # Show what would be deleted (no delete)
#
# ============================================================================

set -e

# Output colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Config
APP_DIR="/opt/TelegramHelper"
CONTAINER_NAME="telegram-helper-lite"
IMAGE_NAME="telegram-helper-lite"
DOCKHAND_CONTAINER="dockhand"
DOCKHAND_IMAGE="dockhand"
BACKUP_DIR="/tmp/telegramhelper_backup_$(date +%Y%m%d_%H%M%S)"

# Flags
FORCE_MODE=false
DRY_RUN=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --force|-f)
            FORCE_MODE=true
            ;;
        --dry-run|-n)
            DRY_RUN=true
            ;;
        --help|-h)
            echo "🧹 TelegramHelper — server cleanup script"
            echo ""
            echo "Usage:"
            echo "  ./cleanup_server.sh           # Interactive mode"
            echo "  ./cleanup_server.sh --force   # No confirmation"
            echo "  ./cleanup_server.sh --dry-run # Show what would be deleted"
            echo ""
            echo "Flags:"
            echo "  -f, --force    Delete without confirmation"
            echo "  -n, --dry-run  Only show what would be deleted"
            echo "  -h, --help     Show this help"
            exit 0
            ;;
    esac
done

# Logging helpers
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

# Run a command (honours dry-run)
run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}   Command: $*${NC}"
    else
        eval "$@"
    fi
}

# Docker command prefixes
if [ "$EUID" -ne 0 ]; then
    DOCKER_CMD="sudo docker"
    COMPOSE_CMD="sudo docker compose"
else
    DOCKER_CMD="docker"
    COMPOSE_CMD="docker compose"
fi

echo ""
echo "=============================================="
echo "🧹 TelegramHelper — server cleanup"
echo "=============================================="
echo ""

if [ "$DRY_RUN" = true ]; then
    log_warning "DRY-RUN MODE — nothing will be deleted"
    echo ""
fi

# Show what will be deleted
echo "📋 Will be removed:"
echo ""

# 1. Docker container
CONTAINER_EXISTS=$($DOCKER_CMD ps -a --filter "name=$CONTAINER_NAME" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$CONTAINER_EXISTS" ]; then
    echo "   🐳 Container: $CONTAINER_NAME"
else
    echo "   🐳 Container: $CONTAINER_NAME (not found)"
fi

# 1.1 Dockhand container
DOCKHAND_EXISTS=$($DOCKER_CMD ps -a --filter "name=$DOCKHAND_CONTAINER" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$DOCKHAND_EXISTS" ]; then
    echo "   🐳 Container: $DOCKHAND_CONTAINER"
else
    echo "   🐳 Container: $DOCKHAND_CONTAINER (not found)"
fi

# 2. Docker image
IMAGE_EXISTS=$($DOCKER_CMD images "$IMAGE_NAME" --format "{{.Repository}}:{{.Tag}}" 2>/dev/null || true)
if [ -n "$IMAGE_EXISTS" ]; then
    echo "   📦 Image: $IMAGE_EXISTS"
else
    echo "   📦 Image: $IMAGE_NAME (not found)"
fi

# 2.1 Dockhand image
DOCKHAND_IMG_EXISTS=$($DOCKER_CMD images "$DOCKHAND_IMAGE" --format "{{.Repository}}:{{.Tag}}" 2>/dev/null || true)
if [ -n "$DOCKHAND_IMG_EXISTS" ]; then
    echo "   📦 Image: $DOCKHAND_IMG_EXISTS"
else
    echo "   📦 Image: $DOCKHAND_IMAGE (not found)"
fi

# 3. App directory
if [ -d "$APP_DIR" ]; then
    APP_SIZE=$(du -sh "$APP_DIR" 2>/dev/null | cut -f1 || echo "?")
    echo "   📁 Directory: $APP_DIR ($APP_SIZE)"

    # Show important files
    if [ -f "$APP_DIR/.env" ]; then
        echo "      └── .env (configuration)"
    fi
    if [ -f "$APP_DIR/app_keys.json" ]; then
        echo "      └── app_keys.json (API keys)"
    fi
    if [ -f "$APP_DIR/users.json" ]; then
        echo "      └── users.json (user data)"
    fi
    if [ -f "$APP_DIR/vless_config.json" ]; then
        echo "      └── vless_config.json (VLESS config)"
    fi
else
    echo "   📁 Directory: (not found)"
fi

# 4. Docker volumes (if any)
VOLUMES=$($DOCKER_CMD volume ls --filter "name=telegram" --format "{{.Name}}" 2>/dev/null || true)
if [ -n "$VOLUMES" ]; then
    echo "   💾 Volumes:"
    echo "$VOLUMES" | while read vol; do
        echo "      └── $vol"
    done
fi

echo ""

# Confirmation (unless force mode)
if [ "$FORCE_MODE" = false ] && [ "$DRY_RUN" = false ]; then
    echo -e "${RED}⚠️  WARNING: This action is irreversible!${NC}"
    echo ""
    read -p "Create a backup of .env and keys before deleting? (Y/n): " backup_response
    backup_response=${backup_response:-Y}

    if [[ "$backup_response" =~ ^[Yy]$ ]]; then
        DO_BACKUP=true
    else
        DO_BACKUP=false
    fi

    echo ""
    read -p "Are you sure you want to delete EVERYTHING? (type 'DELETE' to confirm): " confirm

    if [ "$confirm" != "DELETE" ]; then
        log_error "Cancelled. Type 'DELETE' to confirm."
        exit 1
    fi
else
    DO_BACKUP=true
fi

echo ""

# === BACKUP ===
if [ "$DO_BACKUP" = true ] && [ -d "$APP_DIR" ]; then
    log_action "Creating backup in $BACKUP_DIR"

    if [ "$DRY_RUN" = false ]; then
        mkdir -p "$BACKUP_DIR"

        # Copy important files
        for file in .env app_keys.json users.json vless_config.json; do
            if [ -f "$APP_DIR/$file" ]; then
                cp "$APP_DIR/$file" "$BACKUP_DIR/"
                log_success "Saved: $file"
            fi
        done

        echo ""
        log_success "Backup created: $BACKUP_DIR"
        echo "   To restore:"
        echo "   cp $BACKUP_DIR/.env $APP_DIR/"
        echo ""
    fi
fi

# === STOP CONTAINER ===
log_action "Stopping Docker container..."

if [ -n "$CONTAINER_EXISTS" ]; then
    if [ -f "$APP_DIR/compose.yaml" ] || [ -f "$APP_DIR/docker-compose.yml" ]; then
        run_cmd "cd $APP_DIR && $COMPOSE_CMD down 2>/dev/null || true"
    else
        run_cmd "$DOCKER_CMD stop $CONTAINER_NAME 2>/dev/null || true"
        run_cmd "$DOCKER_CMD rm $CONTAINER_NAME 2>/dev/null || true"
    fi
    log_success "Container $CONTAINER_NAME stopped and removed"
else
    log_info "Container $CONTAINER_NAME is not running"
fi

# === STOP DOCKHAND ===
if [ -n "$DOCKHAND_EXISTS" ]; then
    run_cmd "$DOCKER_CMD stop $DOCKHAND_CONTAINER 2>/dev/null || true"
    run_cmd "$DOCKER_CMD rm $DOCKHAND_CONTAINER 2>/dev/null || true"
    log_success "Container $DOCKHAND_CONTAINER stopped and removed"
fi

# === REMOVE IMAGE ===
log_action "Removing Docker image..."

if [ -n "$IMAGE_EXISTS" ]; then
    run_cmd "$DOCKER_CMD rmi $IMAGE_NAME:latest 2>/dev/null || true"
    run_cmd "$DOCKER_CMD rmi $IMAGE_NAME 2>/dev/null || true"
    log_success "Image removed"
else
    log_info "Image $IMAGE_NAME not found"
fi

if [ -n "$DOCKHAND_IMG_EXISTS" ]; then
    run_cmd "$DOCKER_CMD rmi $DOCKHAND_IMAGE:latest 2>/dev/null || true"
    run_cmd "$DOCKER_CMD rmi $DOCKHAND_IMAGE 2>/dev/null || true"
    log_success "Image $DOCKHAND_IMAGE removed"
fi

# === REMOVE VOLUMES ===
if [ -n "$VOLUMES" ]; then
    log_action "Removing Docker volumes..."
    echo "$VOLUMES" | while read vol; do
        run_cmd "$DOCKER_CMD volume rm $vol 2>/dev/null || true"
    done
    log_success "Volumes removed"
fi

# === DOCKER PRUNE ===
log_action "Pruning unused Docker resources..."
run_cmd "$DOCKER_CMD system prune -f 2>/dev/null || true"
log_success "Docker pruned"

# === REMOVE DIRECTORY ===
log_action "Removing directory $APP_DIR..."

if [ -d "$APP_DIR" ]; then
    run_cmd "rm -rf $APP_DIR"
    log_success "Directory removed"
else
    log_info "Directory not found"
fi

# === SUMMARY ===
echo ""
echo "=============================================="

if [ "$DRY_RUN" = true ]; then
    log_warning "DRY-RUN finished. Nothing was deleted."
    echo ""
    echo "For a real delete, run without --dry-run"
else
    log_success "🧹 Cleanup finished!"
    echo ""
    echo "📋 What was done:"
    echo "   • Docker container stopped and removed"
    echo "   • Docker image removed"
    echo "   • Directory $APP_DIR removed"
    echo "   • Unused Docker resources pruned"

    if [ "$DO_BACKUP" = true ]; then
        echo ""
        echo "📦 Backup saved in: $BACKUP_DIR"
    fi

    echo ""
    echo "🚀 To reinstall:"
    echo "   1. Create the directory: mkdir -p /opt/TelegramHelper"
    echo "   2. ⚠️  IMPORTANT: Create data files (rsync will NOT copy them):"
    echo "      cd /opt/TelegramHelper"
    echo "      echo '{\"app_keys\": {}, \"default\": {}}' > app_keys.json"
    echo "      echo '{}' > users.json"
    echo "      echo '{}' > vless_config.json"
    echo "      touch bot.log"
    if [ "$DO_BACKUP" = true ]; then
        echo "      cp $BACKUP_DIR/.env .env  # restore config"
    fi
    echo "   3. Copy the code from the local machine (rsync)"
    echo "   4. Set permissions: chmod 666 app_keys.json users.json vless_config.json bot.log"
    echo "   5. Start: docker compose up -d --build"
fi

echo ""
echo "=============================================="
