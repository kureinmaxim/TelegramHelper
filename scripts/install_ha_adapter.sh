#!/usr/bin/env bash
# ============================================================================
# install_ha_adapter.sh — реальный HA вместо stub на VPS.
#
# Ставит systemd ha-adapter-grpc (:50057) → REST HA по tailnet,
# опционально переключает ha-reticulum-bridge на --grpc :50057
# и открывает мост наружу (listen_ip 0.0.0.0) для ApiHA без SSH.
#
# Требования:
#   - уже установлен HA-стек (bash scripts/install_ha_stack.sh)
#   - VPS в mesh с NAS (curl http://100.64.0.2:8123/ отвечает)
#   - Long-Lived Token из профиля HA
#
# Примеры:
#   bash scripts/install_ha_adapter.sh \
#     --ha-url http://100.64.0.2:8123 --ha-token 'eyJ...' \
#     --switch-bridge --public-rns
#
#   HA_URL=... HA_TOKEN=... bash scripts/install_ha_adapter.sh   # интерактивно доспросит
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ha-url) HA_URL="$2"; shift 2 ;;
    --ha-token) HA_TOKEN="$2"; shift 2 ;;
    --switch-bridge) SWITCH_BRIDGE=1; shift ;;
    --public-rns) PUBLIC_RNS=1; shift ;;
    --listen) LISTEN="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage ;;
    *) echo "Неизвестный аргумент: $1"; usage ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Только Linux VPS."
  exit 1
fi

if [[ ! -f "${ADAPTER_PY}" ]]; then
  echo "Нет ${ADAPTER_PY} — сделай git pull (файл вендорится из ApiRgRPC)."
  exit 1
fi

if [[ ! -x "${PY}" ]]; then
  echo "Нет venv ${PY}. Сначала: bash scripts/install_ha_stack.sh"
  exit 1
fi

if [[ -z "${HA_URL}" && -t 0 ]]; then
  read -r -p "HA_URL [http://100.64.0.2:8123]: " HA_URL || true
fi
HA_URL="${HA_URL:-http://100.64.0.2:8123}"

if [[ -z "${HA_TOKEN}" && -t 0 ]]; then
  echo "Long-Lived Token: HA → профиль → «Долгосрочные токены доступа»."
  read -r -p "HA_TOKEN: " HA_TOKEN || true
fi
if [[ -z "${HA_TOKEN}" ]]; then
  echo "HA_TOKEN обязателен (env HA_TOKEN=... или --ha-token)."
  exit 1
fi

if [[ -t 0 && "${SWITCH_BRIDGE}" -eq 0 ]]; then
  read -r -p "Переключить мост на adapter :50057? [Y/n] " ans || true
  [[ -z "${ans}" || "${ans}" =~ ^[YyдД] ]] && SWITCH_BRIDGE=1
fi
if [[ -t 0 && "${PUBLIC_RNS}" -eq 0 ]]; then
  read -r -p "Открыть мост наружу (listen_ip=0.0.0.0) для ApiHA без SSH? [y/N] " ans || true
  [[ "${ans}" =~ ^[YyдД] ]] && PUBLIC_RNS=1
fi

echo "=== HA adapter ==="
echo "  URL:    ${HA_URL}"
echo "  listen: ${LISTEN}"
echo "  bridge: $([[ ${SWITCH_BRIDGE} -eq 1 ]] && echo '→ :50057' || echo 'не трогаю')"
echo "  public: $([[ ${PUBLIC_RNS} -eq 1 ]] && echo '0.0.0.0:50061' || echo 'как было')"

if [[ "${DRY}" -eq 1 ]]; then
  echo "[dry-run] выход"
  exit 0
fi

# Проверка доступности HA (не фейлим установку — токен/DNS могут догонять)
code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 "${HA_URL%/}/" 2>/dev/null || echo 000)"
echo "  curl ${HA_URL} → HTTP ${code} (ожидаемо 200/401)"
if [[ "${code}" == "000" || "${code}" == "000000" ]]; then
  echo "⚠ HA недоступен с VPS. Сначала: Tailscale client + curl к NAS."
  echo "  Продолжаю установку юнита — починишь mesh и restart ha-adapter-grpc."
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
    echo "⚠ Нет ${BRIDGE_UNIT} — мост не переключаю."
  else
    # Заменить --grpc 127.0.0.1:NNNN на :50057
    if $SUDO grep -qE -- '--grpc[ =]' "${BRIDGE_UNIT}"; then
      $SUDO sed -i -E 's|--grpc[= ][^ ]+|--grpc 127.0.0.1:50057|' "${BRIDGE_UNIT}"
    else
      echo "⚠ В юните моста нет --grpc — правь вручную."
    fi
    # After= может ссылаться только на stub — добавим adapter
    if ! $SUDO grep -q 'ha-adapter-grpc' "${BRIDGE_UNIT}"; then
      $SUDO sed -i 's/^After=.*/& ha-adapter-grpc.service/' "${BRIDGE_UNIT}"
      $SUDO sed -i 's/^Wants=.*/& ha-adapter-grpc.service/' "${BRIDGE_UNIT}" || true
    fi
    $SUDO systemctl daemon-reload
    $SUDO systemctl restart ha-reticulum-bridge.service
    echo "✅ Мост → --grpc 127.0.0.1:50057"
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
    echo "✅ listen_ip=0.0.0.0 + порт 50061 (ApiHA без SSH)"
  fi
fi

echo ""
echo "Проверка:"
echo "  systemctl is-active ha-adapter-grpc ha-reticulum-bridge"
echo "  ss -tlnp | grep -E '50057|50061'"
echo "  journalctl -u ha-reticulum-bridge -n 20 --no-pager | grep destination"
echo ""
echo "ApiHA / ApiRgRPC: hash из journal, host=<VPS_IP> port=50061 (если --public-rns)"
echo "  или SSH ha-tunnel → 127.0.0.1:50062"
echo "Ping должен ответить: HA API alive"
