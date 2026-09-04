#!/usr/bin/env bash
set -euo pipefail

DOMAIN=""
PORT="443"
USERNAME=""
PASSWORD=""
SERVICE_NAME="caddy-naive"
CADDY_BIN="/usr/local/bin/caddy-naive"
CADDY_DIR="/etc/caddy-naive"
EMAIL=""

usage() {
  cat <<EOF
Usage: sudo bash scripts/install_naiveproxy.sh --domain example.com [options]

Options:
  --domain DOMAIN        Public domain for TLS and client access (required)
  --port PORT            HTTPS listen port (default: 443)
  --username USER        Proxy username (default: generated)
  --password PASS        Proxy password (default: generated)
  --email EMAIL          ACME email (default: admin@DOMAIN)
  --service-name NAME    systemd service name (default: caddy-naive)
EOF
}

random_string() {
  tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="$2"; shift 2 ;;
    --port)
      PORT="$2"; shift 2 ;;
    --username)
      USERNAME="$2"; shift 2 ;;
    --password)
      PASSWORD="$2"; shift 2 ;;
    --email)
      EMAIL="$2"; shift 2 ;;
    --service-name)
      SERVICE_NAME="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root" >&2
  exit 1
fi

if [[ -z "$DOMAIN" ]]; then
  echo "--domain is required" >&2
  exit 1
fi

if [[ -z "$USERNAME" ]]; then
  USERNAME="naive-$(random_string 6 | tr 'A-Z' 'a-z')"
fi

if [[ -z "$PASSWORD" ]]; then
  PASSWORD="$(random_string 24)"
fi

if [[ -z "$EMAIL" ]]; then
  EMAIL="admin@${DOMAIN}"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl git tar ufw ca-certificates golang-go

export PATH="$PATH:/root/go/bin"
if ! command -v xcaddy >/dev/null 2>&1; then
  go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
fi

if [[ ! -x "$CADDY_BIN" ]]; then
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT
  pushd "$tmpdir" >/dev/null
  xcaddy build \
    --output "$CADDY_BIN" \
    --with github.com/caddyserver/forwardproxy=github.com/klzgrad/forwardproxy@naive
  popd >/dev/null
fi

mkdir -p "$CADDY_DIR" /var/lib/${SERVICE_NAME}

cat >"${CADDY_DIR}/Caddyfile" <<EOF
{
    email ${EMAIL}
    order forward_proxy before file_server
    auto_https disable_redirects
}

:${PORT}, ${DOMAIN} {
    log {
        output stdout
        level INFO
    }
    forward_proxy {
        basic_auth ${USERNAME} ${PASSWORD}
        hide_ip
        hide_via
        probe_resistance
    }
}
EOF

cat >"/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=NaiveProxy via Caddy forwardproxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${CADDY_BIN} run --config ${CADDY_DIR}/Caddyfile --adapter caddyfile
ExecReload=${CADDY_BIN} reload --config ${CADDY_DIR}/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576
WorkingDirectory=/var/lib/${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp || true
  ufw allow ${PORT}/tcp || true
fi

cat <<EOF
NaiveProxy installed successfully.

Domain: ${DOMAIN}
Port: ${PORT}
Username: ${USERNAME}
Password: ${PASSWORD}
Service: ${SERVICE_NAME}
URI: naive+https://${USERNAME}:${PASSWORD}@${DOMAIN}:${PORT}#TelegramHelper-NaiveProxy
Caddyfile: ${CADDY_DIR}/Caddyfile

Check service:
  systemctl status ${SERVICE_NAME} | cat
  journalctl -u ${SERVICE_NAME} -n 100 | cat
EOF
