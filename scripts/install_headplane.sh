#!/usr/bin/env bash
#
# install_headplane.sh — install and start Headplane (Web UI for Headscale).
# Matches HEADSCALE_GUIDE.md → "Web UI via Headplane".
#
# What it does:
#   1. Checks that the headscale container is running.
#   2. Auto-detects the host path to headscale config.yaml via
#      `docker inspect headscale` (mounts → /etc/headscale).
#      Supports both scenarios:
#        • our compose.headscale.yaml (./headscale/config/config.yaml)
#        • legacy/standalone install (/etc/headscale/config.yaml)
#      Writes the found path into .env as HEADSCALE_CONFIG_FILE — compose
#      picks it up on `up`.
#   3. Creates ./headplane/data (persistent state, owner 0:0).
#   4. Copies headplane/config.example.yaml → headplane/config.yaml,
#      fills in cookie_secret (openssl rand) and public_url (from
#      headscale_config.json, if present).
#   5. Issues a headscale API key via `docker exec headscale headscale
#      apikeys create` and puts it into config.yaml.
#   6. Brings up headplane via `docker compose -f compose.headplane.yaml up -d`.
#      Headplane uses network_mode: host, so the compose file is standalone —
#      it does not depend on how headscale is running (our compose or standalone).
#   7. Prints an SSH tunnel for access from a PC.
#
# Idempotent: a re-run without --force reuses the existing
# config.yaml. With --force it regenerates the config and issues a new API key.
#
set -euo pipefail

FORCE="0"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KEY_EXPIRATION="90d"

usage() {
  cat <<EOF
Usage: bash scripts/install_headplane.sh [options]

Options:
  --force              Regenerate config.yaml and the headscale API key.
                       By default existing ones are reused if present.
  --expiration DUR     Lifetime of the headscale API key (default: 90d).
                       Headscale format: 24h / 7d / 90d / 365d.
  -h | --help          Show help.

After install, access from a client PC via SSH tunnel:
  ssh -p <SSH_PORT> -L 3000:127.0.0.1:3000 root@<VPS_IP>
  Browser → http://127.0.0.1:3000/admin
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)        FORCE="1"; shift ;;
    --expiration)   KEY_EXPIRATION="$2"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    *)
      echo "❌ Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

cd "$APP_DIR"

# --- 1. Checks ---------------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ docker not found in PATH" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "headscale"; then
  echo "❌ Container headscale is not running." >&2
  echo "   Start headscale any way (our compose.headscale.yaml" >&2
  echo "   or standalone), then re-run install_headplane.sh." >&2
  exit 1
fi

if [[ ! -f compose.headplane.yaml ]]; then
  echo "❌ compose.headplane.yaml not found in $APP_DIR" >&2
  exit 1
fi

if [[ ! -f headplane/config.example.yaml ]]; then
  echo "❌ headplane/config.example.yaml not found" >&2
  exit 1
fi

# --- 2. Auto-detect host path to headscale config.yaml ----------------------

# Get the Source mount whose Destination is /etc/headscale.
HS_MOUNT_SOURCE="$(docker inspect headscale --format \
  '{{range .Mounts}}{{if eq .Destination "/etc/headscale"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"

if [[ -z "$HS_MOUNT_SOURCE" ]]; then
  # Maybe headscale is mounted file-by-file (config.yaml as a single file).
  HS_MOUNT_SOURCE="$(docker inspect headscale --format \
    '{{range .Mounts}}{{if eq .Destination "/etc/headscale/config.yaml"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
fi

if [[ -z "$HS_MOUNT_SOURCE" ]]; then
  echo "❌ Failed to resolve the headscale config path via docker inspect." >&2
  echo "   Check mounts: docker inspect headscale --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'" >&2
  exit 1
fi

# If source is a directory, append config.yaml; if it is already a file — use as-is.
if [[ -d "$HS_MOUNT_SOURCE" ]]; then
  HS_CONFIG_FILE="$HS_MOUNT_SOURCE/config.yaml"
else
  HS_CONFIG_FILE="$HS_MOUNT_SOURCE"
fi

if [[ ! -f "$HS_CONFIG_FILE" ]]; then
  echo "❌ Headscale config not found on the host: $HS_CONFIG_FILE" >&2
  echo "   Headscale container is running, but the config is elsewhere. Check mounts." >&2
  exit 1
fi

echo "✅ Headscale config: $HS_CONFIG_FILE"

# Write into .env so docker compose picks it up on up.
ENV_FILE=".env"
if [[ -f "$ENV_FILE" ]]; then
  # Drop the old value, append the new one.
  grep -v '^HEADSCALE_CONFIG_FILE=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
  mv "$ENV_FILE.tmp" "$ENV_FILE"
fi
echo "HEADSCALE_CONFIG_FILE=$HS_CONFIG_FILE" >> "$ENV_FILE"
echo "✅ HEADSCALE_CONFIG_FILE written to .env"

# Export for the docker compose call below.
export HEADSCALE_CONFIG_FILE="$HS_CONFIG_FILE"

# --- 3. Persistent-state directory ------------------------------------------

mkdir -p headplane/data
# The headplane image runs as root, so owner 0:0.
chown -R 0:0 headplane/data

# --- 4. Generate / reuse config.yaml ----------------------------------------

CONFIG_FILE="headplane/config.yaml"

if [[ -f "$CONFIG_FILE" && "$FORCE" != "1" ]]; then
  echo "ℹ️  $CONFIG_FILE already exists — reusing."
  echo "   To regenerate run: bash scripts/install_headplane.sh --force"
  REGENERATE_CONFIG="0"
else
  REGENERATE_CONFIG="1"
fi

if [[ "$REGENERATE_CONFIG" == "1" ]]; then
  cp headplane/config.example.yaml "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"

  # cookie_secret — exactly 32 characters (hex 16 bytes).
  COOKIE_SECRET="$(openssl rand -hex 16)"
  sed -i "s|REPLACE_WITH_32_CHAR_RANDOM_STRING|${COOKIE_SECRET}|" "$CONFIG_FILE"

  # public_url — from headscale_config.json, if present.
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
      echo "✅ public_url set: ${HS_URL}"
    fi
  fi

  echo "✅ headplane/config.yaml created (cookie_secret: 32 hex chars)"
fi

# --- 5. Headscale API key ---------------------------------------------------

CURRENT_KEY="$(grep -E '^[[:space:]]*api_key:' "$CONFIG_FILE" | head -n1 | sed -E 's/.*api_key:[[:space:]]*"([^"]*)".*/\1/' || true)"

if [[ -z "$CURRENT_KEY" || "$CURRENT_KEY" == "REPLACE_WITH_HEADSCALE_API_KEY" || "$FORCE" == "1" ]]; then
  echo "🔑 Issuing a headscale API key (expiration: ${KEY_EXPIRATION})..."
  RAW_OUT="$(docker exec headscale headscale apikeys create --expiration "$KEY_EXPIRATION" 2>&1)"
  NEW_KEY="$(echo "$RAW_OUT" | awk 'NF' | tail -n1 | tr -d '[:space:]')"
  if [[ -z "$NEW_KEY" || "${#NEW_KEY}" -lt 20 ]]; then
    echo "❌ Failed to parse the API key from headscale output:" >&2
    echo "$RAW_OUT" >&2
    exit 1
  fi
  sed -i "s|REPLACE_WITH_HEADSCALE_API_KEY|${NEW_KEY}|" "$CONFIG_FILE"
  sed -i -E "s|(^[[:space:]]*api_key:[[:space:]]*\")[^\"]*(\".*)|\1${NEW_KEY}\2|" "$CONFIG_FILE"
  echo "✅ New headscale API key saved in config.yaml"
else
  echo "ℹ️  Headscale API key is already set, reusing."
fi

# --- 6. Bring up headplane --------------------------------------------------

echo "🚀 Starting headplane..."
docker compose -f compose.headplane.yaml up -d

sleep 3
echo
echo "=== Container status ==="
docker ps --filter "name=headplane" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'

# --- 7. SSH-tunnel hint -----------------------------------------------------

SSH_PORT="${SSH_PORT:-22}"
if [[ -f .env ]]; then
  ENV_SSH_PORT="$(grep -E '^SSH_PORT=' .env | tail -n1 | cut -d= -f2 | tr -d '"' || true)"
  [[ -n "$ENV_SSH_PORT" ]] && SSH_PORT="$ENV_SSH_PORT"
fi

VPS_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "$VPS_IP" ]] && VPS_IP="<VPS_IP>"

# Pull the API key from config.yaml for a convenient first login.
API_KEY_HINT="$(grep -E '^[[:space:]]*api_key:' "$CONFIG_FILE" | head -n1 | sed -E 's/.*api_key:[[:space:]]*"([^"]*)".*/\1/' || true)"

cat <<EOF

╭─ Headplane is ready ───────────────────────────────────────────╮
│                                                                │
│  1. SSH tunnel from a client PC:                               │
│                                                                │
│       ssh -p ${SSH_PORT} -L 3000:127.0.0.1:3000 root@${VPS_IP}
│                                                                │
│  2. In the browser:                                            │
│                                                                │
│       http://127.0.0.1:3000/admin                              │
│                                                                │
│  3. Paste the headscale API key on the login form. The one in  │
│     config.yaml (90d) works, but for a one-off login it is     │
│     better to issue a short-lived key:                         │
│                                                                │
│       docker exec headscale headscale apikeys create --expiration 24h
│                                                                │
│  Logs:  docker logs -f headplane                               │
│  Stop:  docker compose -f compose.headplane.yaml stop headplane│
│                                                                │
╰────────────────────────────────────────────────────────────────╯

EOF

if [[ -n "$API_KEY_HINT" && "$API_KEY_HINT" != "REPLACE_WITH_HEADSCALE_API_KEY" ]]; then
  echo "💡 Current api_key from headplane/config.yaml (90d):"
  echo "   $API_KEY_HINT"
  echo
fi

echo "Details: HEADSCALE_GUIDE.md → \"Web UI via Headplane\"."
