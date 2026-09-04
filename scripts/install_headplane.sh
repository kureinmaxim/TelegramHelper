#!/usr/bin/env bash
#
# install_headplane.sh — установить и запустить Headplane (Web UI для Headscale).
# Соответствует HEADSCALE_GUIDE.md → «Web UI через Headplane».
#
# Что делает:
#   1. Проверяет, что контейнер headscale запущен.
#   2. Автодетектит путь к headscale config.yaml на хосте через
#      `docker inspect headscale` (mounts → /etc/headscale).
#      Поддерживает оба сценария:
#        • наш compose.headscale.yaml (./headscale/config/config.yaml)
#        • legacy/standalone установку (/etc/headscale/config.yaml)
#      Записывает найденный путь в .env как HEADSCALE_CONFIG_FILE — compose
#      подхватывает его при `up`.
#   3. Создаёт ./headplane/data (persistent state, owner 0:0).
#   4. Копирует headplane/config.example.yaml → headplane/config.yaml,
#      подставляет cookie_secret (openssl rand) и public_url (из
#      headscale_config.json, если есть).
#   5. Выпускает headscale API ключ через `docker exec headscale headscale
#      apikeys create` и подкладывает в config.yaml.
#   6. Поднимает headplane через `docker compose -f compose.headplane.yaml up -d`.
#      Headplane использует network_mode: host, поэтому compose автономный —
#      не зависит от того, как поднят headscale (наш compose или standalone).
#   7. Печатает SSH-туннель для доступа с ПК.
#
# Идемпотентен: повторный запуск без --force переиспользует существующий
# config.yaml. С --force перегенерирует config и выпускает новый API ключ.
#
set -euo pipefail

FORCE="0"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KEY_EXPIRATION="90d"

usage() {
  cat <<EOF
Usage: bash scripts/install_headplane.sh [options]

Options:
  --force              Перегенерировать config.yaml и headscale API ключ.
                       По умолчанию переиспользует существующие, если есть.
  --expiration DUR     Срок действия headscale API ключа (default: 90d).
                       Формат headscale: 24h / 7d / 90d / 365d.
  -h | --help          Показать справку.

После установки доступ с клиентского ПК — через SSH-туннель:
  ssh -p <SSH_PORT> -L 3000:127.0.0.1:3000 root@<VPS_IP>
  Браузер → http://127.0.0.1:3000/admin
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)        FORCE="1"; shift ;;
    --expiration)   KEY_EXPIRATION="$2"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    *)
      echo "❌ Неизвестный аргумент: $1" >&2
      usage
      exit 1
      ;;
  esac
done

cd "$APP_DIR"

# --- 1. Проверки -------------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ docker не найден в PATH" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "headscale"; then
  echo "❌ Контейнер headscale не запущен." >&2
  echo "   Запустите headscale любым способом (наш compose.headscale.yaml" >&2
  echo "   или standalone), затем повторите install_headplane.sh." >&2
  exit 1
fi

if [[ ! -f compose.headplane.yaml ]]; then
  echo "❌ compose.headplane.yaml не найден в $APP_DIR" >&2
  exit 1
fi

if [[ ! -f headplane/config.example.yaml ]]; then
  echo "❌ headplane/config.example.yaml не найден" >&2
  exit 1
fi

# --- 2. Автодетект пути к headscale config.yaml на хосте --------------------

# Достаём Source mount, у которого Destination = /etc/headscale.
HS_MOUNT_SOURCE="$(docker inspect headscale --format \
  '{{range .Mounts}}{{if eq .Destination "/etc/headscale"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"

if [[ -z "$HS_MOUNT_SOURCE" ]]; then
  # Возможно headscale смонтирован поштучно (config.yaml как отдельный файл).
  HS_MOUNT_SOURCE="$(docker inspect headscale --format \
    '{{range .Mounts}}{{if eq .Destination "/etc/headscale/config.yaml"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
fi

if [[ -z "$HS_MOUNT_SOURCE" ]]; then
  echo "❌ Не удалось определить путь к headscale config через docker inspect." >&2
  echo "   Проверьте mounts: docker inspect headscale --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'" >&2
  exit 1
fi

# Если source — директория, добавляем config.yaml; если уже файл — используем как есть.
if [[ -d "$HS_MOUNT_SOURCE" ]]; then
  HS_CONFIG_FILE="$HS_MOUNT_SOURCE/config.yaml"
else
  HS_CONFIG_FILE="$HS_MOUNT_SOURCE"
fi

if [[ ! -f "$HS_CONFIG_FILE" ]]; then
  echo "❌ Headscale config не найден на хосте: $HS_CONFIG_FILE" >&2
  echo "   Headscale контейнер запущен, но конфиг где-то ещё. Проверьте mounts." >&2
  exit 1
fi

echo "✅ Headscale config: $HS_CONFIG_FILE"

# Запись в .env, чтобы docker compose подхватил при up.
ENV_FILE=".env"
if [[ -f "$ENV_FILE" ]]; then
  # Удаляем старое значение, добавляем новое.
  grep -v '^HEADSCALE_CONFIG_FILE=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
  mv "$ENV_FILE.tmp" "$ENV_FILE"
fi
echo "HEADSCALE_CONFIG_FILE=$HS_CONFIG_FILE" >> "$ENV_FILE"
echo "✅ HEADSCALE_CONFIG_FILE записан в .env"

# Экспортируем для текущего вызова docker compose ниже.
export HEADSCALE_CONFIG_FILE="$HS_CONFIG_FILE"

# --- 3. Каталог для persistent state ----------------------------------------

mkdir -p headplane/data
# Образ headplane работает от root, поэтому owner 0:0.
chown -R 0:0 headplane/data

# --- 4. Генерация / переиспользование config.yaml ---------------------------

CONFIG_FILE="headplane/config.yaml"

if [[ -f "$CONFIG_FILE" && "$FORCE" != "1" ]]; then
  echo "ℹ️  $CONFIG_FILE уже существует — переиспользуем."
  echo "   Для перегенерации запустите: bash scripts/install_headplane.sh --force"
  REGENERATE_CONFIG="0"
else
  REGENERATE_CONFIG="1"
fi

if [[ "$REGENERATE_CONFIG" == "1" ]]; then
  cp headplane/config.example.yaml "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"

  # cookie_secret — ровно 32 символа (hex 16 байт).
  COOKIE_SECRET="$(openssl rand -hex 16)"
  sed -i "s|REPLACE_WITH_32_CHAR_RANDOM_STRING|${COOKIE_SECRET}|" "$CONFIG_FILE"

  # public_url — из headscale_config.json, если есть.
  if [[ -f headscale_config.json ]]; then
    HS_URL="$(python3 -c "
import json
try:
    with open('headscale_config.json') as f:
        d = json.load(f)
    print((d.get('server_url') or '').strip().rstrip('/'))
except Exception:
    print('')
" 2>/dev/null || true)"
    if [[ -n "$HS_URL" ]]; then
      ESCAPED="${HS_URL//\//\\/}"
      sed -i "s|https://headscale.your-domain.com|${ESCAPED}|" "$CONFIG_FILE"
      echo "✅ public_url установлен: ${HS_URL}"
    fi
  fi

  echo "✅ headplane/config.yaml создан (cookie_secret: 32 hex chars)"
fi

# --- 5. Headscale API ключ --------------------------------------------------

CURRENT_KEY="$(grep -E '^[[:space:]]*api_key:' "$CONFIG_FILE" | head -n1 | sed -E 's/.*api_key:[[:space:]]*"([^"]*)".*/\1/' || true)"

if [[ -z "$CURRENT_KEY" || "$CURRENT_KEY" == "REPLACE_WITH_HEADSCALE_API_KEY" || "$FORCE" == "1" ]]; then
  echo "🔑 Выпускаю headscale API ключ (expiration: ${KEY_EXPIRATION})..."
  RAW_OUT="$(docker exec headscale headscale apikeys create --expiration "$KEY_EXPIRATION" 2>&1)"
  NEW_KEY="$(echo "$RAW_OUT" | awk 'NF' | tail -n1 | tr -d '[:space:]')"
  if [[ -z "$NEW_KEY" || "${#NEW_KEY}" -lt 20 ]]; then
    echo "❌ Не удалось распарсить API ключ из вывода headscale:" >&2
    echo "$RAW_OUT" >&2
    exit 1
  fi
  sed -i "s|REPLACE_WITH_HEADSCALE_API_KEY|${NEW_KEY}|" "$CONFIG_FILE"
  sed -i -E "s|(^[[:space:]]*api_key:[[:space:]]*\")[^\"]*(\".*)|\1${NEW_KEY}\2|" "$CONFIG_FILE"
  echo "✅ Новый headscale API ключ сохранён в config.yaml"
else
  echo "ℹ️  Headscale API ключ уже задан, переиспользуем."
fi

# --- 6. Поднять headplane ---------------------------------------------------

echo "🚀 Запускаю headplane..."
docker compose -f compose.headplane.yaml up -d

sleep 3
echo
echo "=== Статус контейнера ==="
docker ps --filter "name=headplane" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'

# --- 7. Подсказка SSH-туннеля -----------------------------------------------

SSH_PORT="${SSH_PORT:-22}"
if [[ -f .env ]]; then
  ENV_SSH_PORT="$(grep -E '^SSH_PORT=' .env | tail -n1 | cut -d= -f2 | tr -d '"' || true)"
  [[ -n "$ENV_SSH_PORT" ]] && SSH_PORT="$ENV_SSH_PORT"
fi

VPS_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "$VPS_IP" ]] && VPS_IP="<VPS_IP>"

# Достаём API key из config.yaml для удобства первого логина.
API_KEY_HINT="$(grep -E '^[[:space:]]*api_key:' "$CONFIG_FILE" | head -n1 | sed -E 's/.*api_key:[[:space:]]*"([^"]*)".*/\1/' || true)"

cat <<EOF

╭─ Headplane готов ──────────────────────────────────────────────╮
│                                                                │
│  1. SSH-туннель с клиентского ПК:                              │
│                                                                │
│       ssh -p ${SSH_PORT} -L 3000:127.0.0.1:3000 root@${VPS_IP}
│                                                                │
│  2. В браузере:                                                │
│                                                                │
│       http://127.0.0.1:3000/admin                              │
│                                                                │
│  3. На форме логина вставить headscale API key. Тот, что в     │
│     config.yaml (90d) — подходит, но для разового логина       │
│     лучше выпустить короткоживущий:                            │
│                                                                │
│       docker exec headscale headscale apikeys create --expiration 24h
│                                                                │
│  Логи:  docker logs -f headplane                               │
│  Стоп:  docker compose -f compose.headplane.yaml stop headplane│
│                                                                │
╰────────────────────────────────────────────────────────────────╯

EOF

if [[ -n "$API_KEY_HINT" && "$API_KEY_HINT" != "REPLACE_WITH_HEADSCALE_API_KEY" ]]; then
  echo "💡 Текущий api_key из headplane/config.yaml (90d):"
  echo "   $API_KEY_HINT"
  echo
fi

echo "Подробнее: HEADSCALE_GUIDE.md → «Web UI через Headplane»."
