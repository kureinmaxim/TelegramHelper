#!/usr/bin/env bash
#
# bot_cli.sh — run the bot CLI dashboard over SSH (no Telegram).
#
# Works with either deployment style:
#   • Bot in Docker  → run inside the container (docker exec).
#   • Bot native     → run cli_dashboard.py on the host via the project venv.
# The dashboard runs the same commands as the bot (AdminCLI): status, exit node,
# VLESS, backups, reticulum, etc. It does not depend on the bot process itself —
# it calls the managers directly.
#
# Usage (on the server):
#   ./scripts/bot_cli.sh                     # interactive menu
#   ./scripts/bot_cli.sh /reticulum_status   # one command, no menu
#   ./scripts/bot_cli.sh /vless_set_port 8443
#
# Environment (optional):
#   BOT_CONTAINER=telegram-helper-lite   # bot container name (Docker)
#   BOT_NATIVE=1                         # force native host run
#   BOT_PYTHON=/path/to/python           # explicit interpreter for the native path

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
  py="$(choose_python)" || { err "❌ Python not found (no $PROJECT_DIR/venv, .venv, or python3)."; exit 1; }
  if [ ! -f "$PROJECT_DIR/cli_dashboard.py" ]; then
    err "❌ cli_dashboard.py not found in $PROJECT_DIR"; exit 1
  fi
  cd "$PROJECT_DIR" || exit 1
  exec "$py" cli_dashboard.py "$@"
}

# --- Detect deployment ---
container_exists=0
container_running=0
if command -v docker >/dev/null 2>&1; then
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$BOT_CONTAINER" && container_exists=1
  docker ps    --format '{{.Names}}' 2>/dev/null | grep -qx "$BOT_CONTAINER" && container_running=1
fi

# Force native path
if [ "${BOT_NATIVE:-0}" = 1 ]; then
  run_native "$@"
fi

# 1) Bot container is running → go inside it
if [ "$container_running" = 1 ]; then
  TTY_FLAGS="-i"
  if [ -t 0 ] && [ -t 1 ]; then TTY_FLAGS="-it"; fi
  exec docker exec $TTY_FLAGS "$BOT_CONTAINER" python3 cli_dashboard.py "$@"
fi

# 2) Bot container exists but is not running → this is a Docker deployment;
#    the host may lack deps, so tell the operator to start the container.
if [ "$container_exists" = 1 ]; then
  err "❌ Container '$BOT_CONTAINER' exists but is not running."
  info ""
  info "Running containers:"
  docker ps --format '  {{.Names}}\t{{.Status}}' || true
  info ""
  info "Start the bot:       docker compose up -d --force-recreate telegram-helper"
  info "Force host path:     BOT_NATIVE=1 ./scripts/bot_cli.sh   (if the host has the project venv)"
  info "Exit node without bot: sudo ./scripts/exit_node.sh status"
  exit 1
fi

# 3) No bot container → native deployment, run on the host
run_native "$@"
