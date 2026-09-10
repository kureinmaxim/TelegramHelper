#!/usr/bin/env bash
# ============================================================================
# install_ha_adapter.sh — real HA instead of the stub on the VPS.
#
# Installs systemd ha-adapter-grpc (:50057) → REST HA over the tailnet,
# optionally switches ha-reticulum-bridge to --grpc :50057
# and opens the bridge to the internet (listen_ip 0.0.0.0) for ApiHA without SSH.
#
# Requirements:
#   - HA stack already installed (bash scripts/install_ha_stack.sh)
#   - VPS on the mesh with the NAS (curl http://100.64.0.2:8123/ responds)
#   - Long-Lived Token from the HA profile
#
# Examples:
#   bash scripts/install_ha_adapter.sh \
#     --ha-url http://100.64.0.2:8123 --ha-token 'eyJ...' \
#     --switch-bridge --public-rns
#
#   HA_URL=... HA_TOKEN=... bash scripts/install_ha_adapter.sh   # will prompt for the rest
# ============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HA_DIR="${APP_DIR}/ha_stack"
PY="${HA_DIR}/.venv/bin/python"
ADAPTER_PY="${HA_DIR}/ha_adapter_server.py"
ENV_DIR="/etc/shskm"
ENV_FILE="${ENV_DIR}/ha-adapter.env"
UNIT="/etc/systemd/system/ha-adapter-grpc.service"
BRIDGE_UNIT="/etc/systemd/system/ha-reticulum-bridge.service"
LISTEN="127.0.0.1:50057"

HA_URL="${HA_URL:-}"
HA_TOKEN="${HA_TOKEN:-}"
SWITCH_BRIDGE=0
PUBLIC_RNS=0
DRY=0

SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

usage() {
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# Accept Latin Y/y and Cyrillic yes-letter (same layout as the old prompt).
_yes() {
  case "$1" in
    [Yy]|""|$'\u0434'|$'\u0414') return 0 ;;
    *) return 1 ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ha-url) HA_URL="$2"; shift 2 ;;
    --ha-token) HA_TOKEN="$2"; shift 2 ;;
    --switch-bridge) SWITCH_BRIDGE=1; shift ;;
    --public-rns) PUBLIC_RNS=1; shift ;;
    --listen) LISTEN="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1"; usage ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Linux VPS only."
  exit 1
fi

if [[ ! -f "${ADAPTER_PY}" ]]; then
  echo "Missing ${ADAPTER_PY} — git pull (the file is vendored from ApiRgRPC)."
  exit 1
fi

if [[ ! -x "${PY}" ]]; then
  echo "No venv ${PY}. First: bash scripts/install_ha_stack.sh"
  exit 1
fi

if [[ -z "${HA_URL}" && -t 0 ]]; then
  read -r -p "HA_URL [http://100.64.0.2:8123]: " HA_URL || true
fi
HA_URL="${HA_URL:-http://100.64.0.2:8123}"

if [[ -z "${HA_TOKEN}" && -t 0 ]]; then
  echo "Long-Lived Token: HA → profile → Long-lived access tokens."
  read -r -p "HA_TOKEN: " HA_TOKEN || true
fi
if [[ -z "${HA_TOKEN}" ]]; then
  echo "HA_TOKEN is required (env HA_TOKEN=... or --ha-token)."
  exit 1
fi

if [[ -t 0 && "${SWITCH_BRIDGE}" -eq 0 ]]; then
  read -r -p "Switch the bridge to adapter :50057? [Y/n] " ans || true
  _yes "${ans}" && SWITCH_BRIDGE=1
fi
if [[ -t 0 && "${PUBLIC_RNS}" -eq 0 ]]; then
  read -r -p "Open the bridge to the internet (listen_ip=0.0.0.0) for ApiHA without SSH? [y/N] " ans || true
  case "${ans}" in
    [Yy]|$'\u0434'|$'\u0414') PUBLIC_RNS=1 ;;
  esac
fi

echo "=== HA adapter ==="
echo "  URL:    ${HA_URL}"
echo "  listen: ${LISTEN}"
echo "  bridge: $([[ ${SWITCH_BRIDGE} -eq 1 ]] && echo '→ :50057' || echo 'leave as-is')"
echo "  public: $([[ ${PUBLIC_RNS} -eq 1 ]] && echo '0.0.0.0:50061' || echo 'unchanged')"

if [[ "${DRY}" -eq 1 ]]; then
  echo "[dry-run] exit"
  exit 0
fi

# Check HA reachability (do not fail the install — token/DNS may catch up)
code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 "${HA_URL%/}/" 2>/dev/null || echo 000)"
echo "  curl ${HA_URL} → HTTP ${code} (expected 200/401)"
if [[ "${code}" == "000" || "${code}" == "000000" ]]; then
  echo "⚠ HA is unreachable from the VPS. First: Tailscale client + curl to the NAS."
  echo "  Continuing unit install — fix the mesh and restart ha-adapter-grpc."
fi

$SUDO mkdir -p "${ENV_DIR}"
$SUDO tee "${ENV_FILE}" > /dev/null <<EOF
HA_URL=${HA_URL}
HA_TOKEN=${HA_TOKEN}
HA_AUTODISCOVER=1
EOF
$SUDO chmod 600 "${ENV_FILE}"
echo "✅ ${ENV_FILE} (600)"

RUN_USER="$(stat -c '%U' "${HA_DIR}" 2>/dev/null || echo root)"

$SUDO tee "${UNIT}" > /dev/null <<EOF
[Unit]
Description=HA gRPC adapter (real Home Assistant via tailnet)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${HA_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${PY} ${ADAPTER_PY} --listen ${LISTEN}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now ha-adapter-grpc.service
sleep 1
$SUDO systemctl is-active ha-adapter-grpc.service

if [[ "${SWITCH_BRIDGE}" -eq 1 ]]; then
  if [[ ! -f "${BRIDGE_UNIT}" ]]; then
    echo "⚠ Missing ${BRIDGE_UNIT} — not switching the bridge."
  else
    # Replace --grpc 127.0.0.1:NNNN with :50057
    if $SUDO grep -qE -- '--grpc[ =]' "${BRIDGE_UNIT}"; then
      $SUDO sed -i -E 's|--grpc[= ][^ ]+|--grpc 127.0.0.1:50057|' "${BRIDGE_UNIT}"
    else
      echo "⚠ Bridge unit has no --grpc — edit it by hand."
    fi
    # After= may only mention stub — add adapter
    if ! $SUDO grep -q 'ha-adapter-grpc' "${BRIDGE_UNIT}"; then
      $SUDO sed -i 's/^After=.*/& ha-adapter-grpc.service/' "${BRIDGE_UNIT}"
      $SUDO sed -i 's/^Wants=.*/& ha-adapter-grpc.service/' "${BRIDGE_UNIT}" || true
    fi
    $SUDO systemctl daemon-reload
    $SUDO systemctl restart ha-reticulum-bridge.service
    echo "✅ Bridge → --grpc 127.0.0.1:50057"
  fi
fi

if [[ "${PUBLIC_RNS}" -eq 1 ]]; then
  RNS_CFG="${HA_DIR}/rns/config"
  if [[ -f "${RNS_CFG}" ]]; then
    $SUDO sed -i 's/listen_ip = 127.0.0.1/listen_ip = 0.0.0.0/' "${RNS_CFG}"
    $SUDO systemctl restart ha-reticulum-bridge.service
    if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -qi active; then
      $SUDO ufw allow 50061/tcp comment 'RNS HA bridge' || true
    else
      $SUDO iptables -C INPUT -p tcp --dport 50061 -j ACCEPT 2>/dev/null \
        || $SUDO iptables -I INPUT -p tcp --dport 50061 -j ACCEPT || true
    fi
    echo "✅ listen_ip=0.0.0.0 + port 50061 (ApiHA without SSH)"
  fi
fi

echo ""
echo "Check:"
echo "  systemctl is-active ha-adapter-grpc ha-reticulum-bridge"
echo "  ss -tlnp | grep -E '50057|50061'"
echo "  journalctl -u ha-reticulum-bridge -n 20 --no-pager | grep destination"
echo ""
echo "ApiHA / ApiRgRPC: hash from journal, host=<VPS_IP> port=50061 (if --public-rns)"
echo "  or SSH ha-tunnel → 127.0.0.1:50062"
echo "Ping should reply: HA API alive"
