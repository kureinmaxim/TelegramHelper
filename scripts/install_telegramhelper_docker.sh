#!/usr/bin/env bash
# ============================================================================
# TelegramHelper — install the bot via Docker Compose (telegram-helper service).
#
# Docker equivalent of install_telegramhelper_vps.sh (systemd). The choice
# between them is an interactive question in scripts/vps_setup.sh, or run
# this file directly.
#
# What it does:
#   1) creates .env from example.env (if missing)
#   2) asks for BOT_TOKEN / ADMIN_USER_IDS (with hints); already filled
#      fields are NOT re-asked; API_SECRET_KEY / HMAC_SECRET are generated
#   3) makes bind-mount JSON configs into regular files (otherwise Docker
#      creates a directory for a missing file — see POST_DEPLOY.md §5)
#   4) docker compose build + up -d telegram-helper
#
# Run (from the repo root, on the VPS):
#   sudo bash scripts/install_telegramhelper_docker.sh
# ============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${APP_DIR}/.env"
cd "${APP_DIR}"

# shellcheck source=lib_env.sh
source "${APP_DIR}/scripts/lib_env.sh"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is intended for Linux VPS hosts."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found. Install it first:"
  echo "  curl -fsSL https://get.docker.com | sh"
  exit 1
fi

configure_essential_env

# --- Bind-mount sanity: JSON must be a file, not a directory (POST_DEPLOY.md §5) ---
echo ""
echo "Checking bind-mount files (JSON configs)..."
for f in \
  vless_config.json hysteria2_config.json tuic_config.json anytls_config.json \
  xhttp_config.json mtproto_config.json headscale_config.json naiveproxy_config.json \
  mieru_config.json xui_config.json app_keys.json users.json; do
  [[ -d "${f}" ]] && rmdir "${f}" 2>/dev/null || true
  [[ -f "${f}" ]] || echo '{}' > "${f}"
done
[[ -d bot.log ]] && rmdir bot.log 2>/dev/null || true
[[ -f bot.log ]] || : > bot.log
# SSH CLI dashboard history must also be a file.
[[ -d .cli_history ]] && rmdir .cli_history 2>/dev/null || true
[[ -f .cli_history ]] || : > .cli_history
chmod 600 .cli_history 2>/dev/null || true

if env_is_placeholder BOT_TOKEN; then
  echo ""
  echo "⚠ BOT_TOKEN is still a placeholder — the container will start, but the bot will not log in."
  echo "  Put the token in ${ENV_FILE}, then:"
  echo "  docker compose up -d --force-recreate telegram-helper"
fi

echo ""
echo "Building and starting container telegram-helper..."
# BuildKit builder DNS on some VPS hosts cannot resolve deb.debian.org even
# when host DNS works (POST_DEPLOY.md §10). Then build the image with host
# network — otherwise `up` silently starts an old/empty image.
if ! docker compose build telegram-helper; then
  echo "⚠ compose build failed — trying docker build --network=host (POST_DEPLOY.md §10)…"
  docker build --network=host -t telegram-helper-lite:latest .
fi
docker compose up -d telegram-helper

sleep 3
docker compose ps telegram-helper
docker compose logs --tail=40 telegram-helper

# --- Disk auto-cleanup (CLEANUP_SERVER.md) -----------------------------------
# Install the timer at setup time, not after the disk hits 100%:
# the build cache grows on every `compose build` (1.9 GB in two days on
# your-vps), and unbounded journald can eat gigabytes.
echo ""
if [[ "${EUID}" -eq 0 ]]; then
  if systemctl is-enabled telegramhelper-maintenance.timer >/dev/null 2>&1; then
    echo "🧹 Disk auto-cleanup: already enabled."
  else
    echo "🧹 Enabling disk auto-cleanup..."
    bash "${APP_DIR}/scripts/vps_maintenance.sh" --install \
      || echo "⚠ Failed — enable manually: sudo bash scripts/vps_maintenance.sh --install"
  fi
  echo "   When:        weekly, Sunday 04:00 UTC"
  echo "   Cleans:      docker build cache + unused images, journald >500M, apt cache"
  echo "   Does NOT:    running containers, volumes, .env, *_config.json,"
  echo "                dev data and Rust target/ in /root — those are warnings only"
  echo "   Check:       sudo bash scripts/vps_maintenance.sh --status"
  echo "   Diagnose:    sudo bash scripts/vps_maintenance.sh --report"
else
  echo "🧹 Disk auto-cleanup is NOT enabled (needs root). Enable with:"
  echo "   sudo bash scripts/vps_maintenance.sh --install"
fi
