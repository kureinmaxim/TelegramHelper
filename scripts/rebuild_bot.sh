#!/usr/bin/env bash
# ============================================================================
# rebuild_bot.sh — пересобрать и перезапустить ТОЛЬКО бота, одной командой.
#
# Зачем отдельный скрипт: ручная последовательность
#   docker compose build telegram-helper && docker compose up -d --force-recreate
# на этом парке VPS регулярно даёт «тихий» провал. `compose build` падает на
# apt (DNS BuildKit не резолвит deb.debian.org — POST_DEPLOY.md §10), а
# следующий `up --force-recreate` спокойно поднимает СТАРЫЙ образ: бот жив,
# кода нового нет, `/version` показывает прошлую версию. Ловилось на your-vps
# дважды (2026-08-15 и 2026-08-18).
#
# Скрипт закрывает это тремя вещами:
#   1) фолбэк на `docker build --network=host`, если compose build упал;
#   2) recreate ТОЛЬКО бота (никаких `docker compose down` — DOCKER.md §3);
#   3) сверка версии в контейнере с pyproject.toml — провал виден сразу.
#
# Использование:
#   bash scripts/rebuild_bot.sh              # обычная пересборка
#   bash scripts/rebuild_bot.sh --pull       # + git pull origin main
#   bash scripts/rebuild_bot.sh --no-cache   # пересобрать слои с нуля
#   bash scripts/rebuild_bot.sh --prune      # + docker builder prune после сборки
# ============================================================================
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="telegram-helper"
CONTAINER="telegram-helper-lite"
IMAGE="telegram-helper-lite:latest"

DO_PULL=0
DO_PRUNE=0
NO_CACHE=""

for arg in "$@"; do
  case "${arg}" in
    --pull)     DO_PULL=1 ;;
    --prune)    DO_PRUNE=1 ;;
    --no-cache) NO_CACHE="--no-cache" ;;
    -h|--help)  sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "Неизвестный аргумент: ${arg}" >&2; exit 2 ;;
  esac
done

cd "${APP_DIR}"

# ── 1. Код ──────────────────────────────────────────────────────────────────
if [[ ${DO_PULL} -eq 1 ]]; then
  echo "── git pull origin main ────────────────────────────"
  if ! git pull origin main; then
    echo "✗ git pull не прошёл. Если это Authentication failed — в remote URL" >&2
    echo "  зашит старый токен, лечится так (POST_DEPLOY.md §10):" >&2
    echo "    git remote set-url origin https://github.com/your-org/TelegramHelper.git" >&2
    echo "    git -c credential.helper= pull origin main" >&2
    exit 1
  fi
fi

# ── 2. Подсети ──────────────────────────────────────────────────────────────
# Если объявленная в compose подсеть не совпадает с существующей сетью,
# `up` попытается пересоздать сеть и уронит бота (DEPLOY.md §8.9).
if [[ -x scripts/preflight_subnets.sh || -f scripts/preflight_subnets.sh ]]; then
  echo "── preflight_subnets ───────────────────────────────"
  bash scripts/preflight_subnets.sh \
    || echo "⚠ Подсети расходятся. Исправить: bash scripts/preflight_subnets.sh --fix"
fi

# ── 3. Сборка образа (с фолбэком на сеть хоста) ─────────────────────────────
echo "── docker compose build ${SERVICE} ─────────────────"
BUILD_OK=1
docker compose build ${NO_CACHE} "${SERVICE}" || BUILD_OK=0

if [[ ${BUILD_OK} -eq 0 ]]; then
  echo ""
  echo "⚠ compose build упал. Типовая причина — DNS сборщика BuildKit"
  echo "  ('Temporary failure resolving deb.debian.org' / 'Unable to locate"
  echo "   package rclone'), при рабочем DNS на самом хосте."
  echo "  Пробую обход: docker build --network=host (POST_DEPLOY.md §10)…"
  echo ""
  if ! docker build --network=host ${NO_CACHE} -t "${IMAGE}" .; then
    echo ""
    echo "✗ Сборка не прошла и с сетью хоста. Контейнер НЕ трогаю —" >&2
    echo "  бот продолжает работать на старом образе." >&2
    echo "  Проверь DNS хоста: getent hosts deb.debian.org" >&2
    exit 1
  fi
  echo "✓ Образ собран с сетью хоста."
fi

# ── 4. Пересоздание только бота ─────────────────────────────────────────────
# Никогда не `docker compose down`: тушит весь проект (dockhand, socket-proxy,
# networks). Use `up -d --force-recreate`, not `compose down`.
echo "── docker compose up -d --force-recreate ${SERVICE} ─"
docker compose up -d --force-recreate "${SERVICE}" || exit 1

# ── 5. Проверка, что версия реально доехала ─────────────────────────────────
EXPECTED="$(grep -m1 -E '^version[[:space:]]*=' pyproject.toml | cut -d'"' -f2)"
ACTUAL=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  ACTUAL="$(docker exec "${CONTAINER}" \
    grep -m1 -E '^version[[:space:]]*=' /app/pyproject.toml 2>/dev/null \
    | cut -d'"' -f2)"
  [[ -n "${ACTUAL}" ]] && break
  sleep 2
done

echo ""
echo "── Результат ───────────────────────────────────────"
docker compose ps "${SERVICE}"
echo ""
if [[ -z "${ACTUAL}" ]]; then
  echo "⚠ Не смог прочитать версию из контейнера (ещё стартует?)."
  echo "  Проверь вручную: docker compose logs --tail=40 ${SERVICE}"
elif [[ "${ACTUAL}" == "${EXPECTED}" ]]; then
  echo "✓ Версия в контейнере: ${ACTUAL} — совпадает с pyproject.toml."
  echo "  В Telegram: /version → ${EXPECTED}"
else
  echo "✗ РАСХОЖДЕНИЕ ВЕРСИЙ: в контейнере ${ACTUAL}, в репозитории ${EXPECTED}."
  echo "  Значит образ не пересобрался. Повтори с обходом BuildKit:"
  echo "    docker build --network=host -t ${IMAGE} ."
  echo "    docker compose up -d --force-recreate ${SERVICE}"
fi

echo ""
docker compose logs --tail=20 "${SERVICE}" 2>&1 \
  | grep -viE "GET /health|HTTP/1.1\" 200" || true

# ── 6. Build cache ──────────────────────────────────────────────────────────
if [[ ${DO_PRUNE} -eq 1 ]]; then
  echo ""
  echo "── docker builder prune -af ────────────────────────"
  docker builder prune -af || true
  df -h / | tail -1
else
  echo ""
  echo "💡 Сборка оставила слои в build cache. Освободить: docker builder prune -af"
  echo "   (еженедельно это делает telegramhelper-maintenance.timer)"
fi
