#!/usr/bin/env bash
# Обновить только soft-hang watchdog на уже стоящем VPS (без полной
# переустановки stub'ов). Идемпотентно.
# Usage (от root, без sudo — на многих VPS sudo нет):
#   bash scripts/upgrade_ha_rns_watchdog.sh
# Если не root и есть sudo — скрипт сам добавит sudo.
set -euo pipefail
SUDO=""
[[ $(id -u) -eq 0 ]] || SUDO=sudo

$SUDO tee /usr/local/bin/ha-rns-watchdog.sh > /dev/null << 'EOF'
#!/bin/bash
# Soft hang recovery for ha-reticulum-bridge (RETICULUM_GUIDE.md §10.2).
set -euo pipefail
LOG=/var/log/ha-rns-watchdog.log
HB="${HA_RNS_HEARTBEAT:-/tmp/ha-rns-bridge.heartbeat}"
HB_MAX_AGE="${HA_RNS_HB_MAX_AGE:-90}"
RNS_PORT=50061

log() { echo "$(date -Is) $*" >>"$LOG" 2>/dev/null || true; }

restart_bridge() {
  log "restart ha-reticulum-bridge: $*"
  systemctl restart ha-reticulum-bridge
  exit 0
}

if ! ss -ltnH | grep -q ":${RNS_PORT}"; then
  restart_bridge "port :${RNS_PORT} not listening"
fi

if [[ -f "$HB" ]]; then
  now=$(date +%s)
  mtime=$(stat -c %Y "$HB" 2>/dev/null || echo 0)
  age=$((now - mtime))
  if (( age > HB_MAX_AGE )); then
    restart_bridge "heartbeat age ${age}s > ${HB_MAX_AGE}s ($HB)"
  fi
fi

EXEC=$(systemctl show ha-reticulum-bridge -p ExecStart --value 2>/dev/null || true)
GRPC=$(echo "$EXEC" | grep -oE -- '--grpc[= ][^ ]+' | head -1 | sed -E 's/--grpc[= ]//')
GRPC=${GRPC:-127.0.0.1:50057}
WD=$(systemctl show ha-reticulum-bridge -p WorkingDirectory --value 2>/dev/null || true)
PY=""
if [[ -n "$WD" && -x "$WD/.venv/bin/python" ]]; then
  PY="$WD/.venv/bin/python"
elif [[ -x /opt/TelegramHelper/ha_stack/.venv/bin/python ]]; then
  PY=/opt/TelegramHelper/ha_stack/.venv/bin/python
  WD=${WD:-/opt/TelegramHelper/ha_stack}
fi

if [[ -n "$PY" && -n "$WD" ]]; then
  if ! timeout 8 "$PY" - <<PYEOF
import sys
sys.path.insert(0, "${WD}")
import grpc
from proto import device_control_pb2 as pb
from proto import device_control_pb2_grpc as pbg
ch = grpc.insecure_channel("${GRPC}")
stub = pbg.DeviceControlServiceStub(ch)
try:
    stub.GetDevices(pb.GetDeviceRequest(), timeout=4.0)
except grpc.RpcError as e:
    if e.code() in (grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.UNAVAILABLE):
        raise
PYEOF
  then
    if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^ha-adapter-grpc.service'; then
      log "grpc ${GRPC} failed — restart ha-adapter-grpc + bridge"
      systemctl restart ha-adapter-grpc || true
      sleep 1
    fi
    restart_bridge "grpc probe failed on ${GRPC}"
  fi
fi
EOF
$SUDO chmod +x /usr/local/bin/ha-rns-watchdog.sh

$SUDO mkdir -p /etc/systemd/system/ha-reticulum-bridge.service.d
$SUDO tee /etc/systemd/system/ha-reticulum-bridge.service.d/watchdog.conf > /dev/null << 'EOF'
[Service]
Environment=HA_RNS_HEARTBEAT=/tmp/ha-rns-bridge.heartbeat
Environment=HA_RNS_HANDLER_TIMEOUT=25
EOF

$SUDO tee /etc/systemd/system/ha-rns-watchdog.service > /dev/null << 'EOF'
[Unit]
Description=HA RNS soft-hang watchdog (port + heartbeat + grpc)
[Service]
Type=oneshot
ExecStart=/usr/local/bin/ha-rns-watchdog.sh
EOF

$SUDO tee /etc/systemd/system/ha-rns-watchdog.timer > /dev/null << 'EOF'
[Unit]
Description=HA RNS watchdog every 5 min
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Persistent=true
[Install]
WantedBy=timers.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now ha-rns-watchdog.timer
$SUDO systemctl restart ha-reticulum-bridge
echo "OK: watchdog upgraded; bridge restarted"
echo "  timer: $(systemctl is-active ha-rns-watchdog.timer)"
echo "  hb:    ls -l /tmp/ha-rns-bridge.heartbeat (появится через ~20с)"
echo "  log:   /var/log/ha-rns-watchdog.log"
echo "NOTE: handler-timeout (os._exit 78) работает только после git pull кода"
echo "      bridge.py/run_bridge.py в /opt/TelegramHelper (WorkingDirectory юнита)"
