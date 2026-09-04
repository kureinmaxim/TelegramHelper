#!/usr/bin/env bash
# ============================================================================
# TelegramHelper — установка бота через Docker Compose (сервис telegram-helper).
#
# Docker-эквивалент install_telegramhelper_vps.sh (systemd). Выбор между ними —
# интерактивный вопрос в scripts/vps_setup.sh, либо запусти этот файл напрямую.
#
# Что делает:
#   1) создаёт .env из example.env (если ещё нет)
#   2) спрашивает BOT_TOKEN / ADMIN_USER_IDS (с подсказками); уже заполненные
#      поля НЕ переспрашивает; API_SECRET_KEY / HMAC_SECRET генерирует сам
#   3) приводит bind-mount JSON-конфиги к виду «обычный файл» (иначе Docker
#      создаст директорию для несуществующего файла — см. POST_DEPLOY.md §5)
#   4) docker compose build + up -d telegram-helper
#
# Запуск (из корня репозитория, на VPS):
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
  echo "Docker не найден. Установи его сначала:"
  echo "  curl -fsSL https://get.docker.com | sh"
  exit 1
fi

configure_essential_env

# --- Bind-mount sanity: JSON должен быть файлом, не директорией (POST_DEPLOY.md §5) ---
echo ""
echo "Проверяю bind-mount файлы (JSON-конфиги)..."
for f in \
  vless_config.json hysteria2_config.json tuic_config.json anytls_config.json \
  xhttp_config.json mtproto_config.json headscale_config.json naiveproxy_config.json \
  mieru_config.json xui_config.json app_keys.json users.json; do
  [[ -d "${f}" ]] && rmdir "${f}" 2>/dev/null || true
  [[ -f "${f}" ]] || echo '{}' > "${f}"
done
[[ -d bot.log ]] && rmdir bot.log 2>/dev/null || true
[[ -f bot.log ]] || : > bot.log
# История SSH CLI-дашборда — тоже должна быть файлом.
[[ -d .cli_history ]] && rmdir .cli_history 2>/dev/null || true
[[ -f .cli_history ]] || : > .cli_history
chmod 600 .cli_history 2>/dev/null || true

if env_is_placeholder BOT_TOKEN; then
  echo ""
  echo "⚠ BOT_TOKEN всё ещё плейсхолдер — контейнер поднимется, но бот не залогинится."
  echo "  Впиши токен в ${ENV_FILE}, затем:"
  echo "  docker compose up -d --force-recreate telegram-helper"
fi

echo ""
echo "Собираю и запускаю контейнер telegram-helper..."
# DNS сборщика BuildKit на части VPS не резолвит deb.debian.org, хотя у хоста
# DNS рабочий (POST_DEPLOY.md §10). Тогда собираем образ с сетью хоста —
# иначе `up` молча поднимет старый/пустой образ.
if ! docker compose build telegram-helper; then
  echo "⚠ compose build упал — пробую docker build --network=host (POST_DEPLOY.md §10)…"
  docker build --network=host -t telegram-helper-lite:latest .
fi
docker compose up -d telegram-helper

sleep 3
docker compose ps telegram-helper
docker compose logs --tail=40 telegram-helper

# --- Авто-чистка диска (CLEANUP_SERVER.md) -----------------------------------
# Ставим таймер сразу при установке, а не после того, как диск упрётся в 100%:
# build cache растёт при каждом `compose build` (на your-vps — 1.9 GB за двое
# суток), а journald без лимита съедает гигабайты.
echo ""
if [[ "${EUID}" -eq 0 ]]; then
  if systemctl is-enabled telegramhelper-maintenance.timer >/dev/null 2>&1; then
    echo "🧹 Авто-чистка диска: уже включена."
  else
    echo "🧹 Включаю авто-чистку диска..."
    bash "${APP_DIR}/scripts/vps_maintenance.sh" --install \
      || echo "⚠ Не удалось — включи вручную: sudo bash scripts/vps_maintenance.sh --install"
  fi
  echo "   Когда:       раз в неделю, воскресенье 04:00 UTC"
  echo "   Что чистит:  docker build cache + неиспользуемые образы, journald >500M, apt-кэш"
  echo "   НЕ трогает:  запущенные контейнеры, volumes, .env, *_config.json,"
  echo "                dev-данные и Rust target/ в /root — про них только предупреждает"
  echo "   Проверить:   sudo bash scripts/vps_maintenance.sh --status"
  echo "   Диагностика: sudo bash scripts/vps_maintenance.sh --report"
else
  echo "🧹 Авто-чистка диска НЕ включена (нужен root). Включить:"
  echo "   sudo bash scripts/vps_maintenance.sh --install"
fi
