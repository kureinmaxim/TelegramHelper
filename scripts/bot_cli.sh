#!/usr/bin/env bash
#
# bot_cli.sh — запустить CLI-дашборд бота по SSH (без Telegram).
#
# Универсально к способу развёртывания:
#   • Бот в Docker  → выполняем внутри контейнера (docker exec).
#   • Бот нативно   → запускаем cli_dashboard.py на хосте через venv проекта.
# Дашборд гоняет те же команды, что и бот (AdminCLI): статус, exit node,
# VLESS, бэкапы, reticulum и т. д. Дашборд не зависит от того, поднят ли сам
# процесс бота — он вызывает менеджеры напрямую.
#
# Использование (на сервере):
#   ./scripts/bot_cli.sh                     # интерактивное меню
#   ./scripts/bot_cli.sh /reticulum_status   # одна команда без меню
#   ./scripts/bot_cli.sh /vless_set_port 8443
#
# Переменные окружения (необязательные):
#   BOT_CONTAINER=telegram-helper-lite   # имя контейнера бота (для Docker)
#   BOT_NATIVE=1                         # форсировать нативный запуск на хосте
#   BOT_PYTHON=/path/to/python           # явный интерпретатор для нативного пути

set -uo pipefail

err()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
info() { printf '%s\n' "$*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BOT_CONTAINER="${BOT_CONTAINER:-telegram-helper-lite}"

choose_python() {
  if [ -n "${BOT_PYTHON:-}" ]; then echo "$BOT_PYTHON"; return 0; fi
  for cand in "$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/.venv/bin/python"; do
    [ -x "$cand" ] && { echo "$cand"; return 0; }
  done
  command -v python3 >/dev/null 2>&1 && { echo python3; return 0; }
  command -v python  >/dev/null 2>&1 && { echo python;  return 0; }
  return 1
}

run_native() {
  local py
  py="$(choose_python)" || { err "❌ Python не найден (нет $PROJECT_DIR/venv, .venv или python3)."; exit 1; }
  if [ ! -f "$PROJECT_DIR/cli_dashboard.py" ]; then
    err "❌ cli_dashboard.py не найден в $PROJECT_DIR"; exit 1
  fi
  cd "$PROJECT_DIR" || exit 1
  exec "$py" cli_dashboard.py "$@"
}

# --- Определяем развёртывание ---
container_exists=0
container_running=0
if command -v docker >/dev/null 2>&1; then
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$BOT_CONTAINER" && container_exists=1
  docker ps    --format '{{.Names}}' 2>/dev/null | grep -qx "$BOT_CONTAINER" && container_running=1
fi

# Принудительно нативный путь
if [ "${BOT_NATIVE:-0}" = 1 ]; then
  run_native "$@"
fi

# 1) Контейнер бота запущен → внутрь него
if [ "$container_running" = 1 ]; then
  TTY_FLAGS="-i"
  if [ -t 0 ] && [ -t 1 ]; then TTY_FLAGS="-it"; fi
  exec docker exec $TTY_FLAGS "$BOT_CONTAINER" python3 cli_dashboard.py "$@"
fi

# 2) Контейнер бота есть, но не запущен → это Docker-развёртывание; на хосте
#    может не быть зависимостей, поэтому ведём поднимать контейнер.
if [ "$container_exists" = 1 ]; then
  err "❌ Контейнер '$BOT_CONTAINER' есть, но не запущен."
  info ""
  info "Запущенные контейнеры:"
  docker ps --format '  {{.Names}}\t{{.Status}}' || true
  info ""
  info "Поднять бота:        docker compose up -d --force-recreate telegram-helper"
  info "Форсировать хост:    BOT_NATIVE=1 ./scripts/bot_cli.sh   (если на хосте есть venv проекта)"
  info "Exit node без бота:  sudo ./scripts/exit_node.sh status"
  exit 1
fi

# 3) Контейнера бота нет → нативное развёртывание, запускаем на хосте
run_native "$@"
