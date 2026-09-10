#!/usr/bin/env bash
# ============================================================================
# rebuild_bot.sh — rebuild and restart ONLY the bot, in one command.
#
# Why a dedicated script: the manual sequence
#   docker compose build telegram-helper && docker compose up -d --force-recreate
# on this VPS fleet regularly fails silently. `compose build` dies on apt
# (BuildKit DNS cannot resolve deb.debian.org — POST_DEPLOY.md §10), and the
# following `up --force-recreate` cheerfully starts the OLD image: the bot is
# alive, the new code is not, `/version` shows the previous version. Caught on
# your-vps twice (2026-08-15 and 2026-08-18).
#
# This script closes that with three things:
#   1) fallback to `docker build --network=host` if compose build fails;
#   2) recreate ONLY the bot (never `docker compose down` — DOCKER.md §3);
#   3) compare the version inside the container with pyproject.toml — a miss
#      is visible immediately.
#
# Usage:
#   bash scripts/rebuild_bot.sh              # normal rebuild
#   bash scripts/rebuild_bot.sh --pull       # + git pull origin main
#   bash scripts/rebuild_bot.sh --no-cache   # rebuild layers from scratch
#   bash scripts/rebuild_bot.sh --prune      # + docker builder prune after build
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
    *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

cd "${APP_DIR}"

# ── 1. Code ─────────────────────────────────────────────────────────────────
if [[ ${DO_PULL} -eq 1 ]]; then
  echo "── git pull origin main ────────────────────────────"
  if ! git pull origin main; then
    echo "✗ git pull failed. If this is Authentication failed — the remote URL" >&2
    echo "  has an old token baked in; fix it like this (POST_DEPLOY.md §10):" >&2
    echo "    git remote set-url origin https://github.com/your-org/TelegramHelper.git" >&2
    echo "    git -c credential.helper= pull origin main" >&2
    exit 1
  fi
fi

# ── 2. Subnets ──────────────────────────────────────────────────────────────
# If the subnet declared in compose does not match the existing network,
# `up` will try to recreate the network and take the bot down (DEPLOY.md §8.9).
if [[ -x scripts/preflight_subnets.sh || -f scripts/preflight_subnets.sh ]]; then
  echo "── preflight_subnets ───────────────────────────────"
  bash scripts/preflight_subnets.sh \
    || echo "⚠ Subnets mismatch. Fix: bash scripts/preflight_subnets.sh --fix"
fi

# ── 3. Image build (with host-network fallback) ─────────────────────────────
echo "── docker compose build ${SERVICE} ─────────────────"
BUILD_OK=1
docker compose build ${NO_CACHE} "${SERVICE}" || BUILD_OK=0

if [[ ${BUILD_OK} -eq 0 ]]; then
  echo ""
  echo "⚠ compose build failed. Typical cause is BuildKit builder DNS"
  echo "  ('Temporary failure resolving deb.debian.org' / 'Unable to locate"
  echo "   package rclone') while host DNS still works."
  echo "  Trying workaround: docker build --network=host (POST_DEPLOY.md §10)…"
  echo ""
  if ! docker build --network=host ${NO_CACHE} -t "${IMAGE}" .; then
    echo ""
    echo "✗ Build failed even with host network. Container is NOT touched —" >&2
    echo "  the bot keeps running on the old image." >&2
    echo "  Check host DNS: getent hosts deb.debian.org" >&2
    exit 1
  fi
  echo "✓ Image built with host network."
fi

# ── 4. Recreate only the bot ────────────────────────────────────────────────
# Never `docker compose down`: it stops the whole project (dockhand, socket-proxy,
# networks). Use `up -d --force-recreate`, not `compose down`.
echo "── docker compose up -d --force-recreate ${SERVICE} ─"
docker compose up -d --force-recreate "${SERVICE}" || exit 1

# ── 5. Check that the version actually arrived ──────────────────────────────
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
echo "── Result ──────────────────────────────────────────"
docker compose ps "${SERVICE}"
echo ""
if [[ -z "${ACTUAL}" ]]; then
  echo "⚠ Could not read the version from the container (still starting?)."
  echo "  Check by hand: docker compose logs --tail=40 ${SERVICE}"
elif [[ "${ACTUAL}" == "${EXPECTED}" ]]; then
  echo "✓ Version in container: ${ACTUAL} — matches pyproject.toml."
  echo "  In Telegram: /version → ${EXPECTED}"
else
  echo "✗ VERSION MISMATCH: container has ${ACTUAL}, repo has ${EXPECTED}."
  echo "  The image was not rebuilt. Retry with the BuildKit workaround:"
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
  echo "💡 The build left layers in the build cache. Free them: docker builder prune -af"
  echo "   (telegramhelper-maintenance.timer does this weekly)"
fi
