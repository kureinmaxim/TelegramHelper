#!/bin/bash
# ============================================================================
# HA stack (stubs) — install on a VPS
# ============================================================================
# Installs a self-contained stack for e2e tests of the UDP_gRPC_COM_Lite client:
#   - gRPC DeviceControlService with Mi-Home stubs (mi_bulb/mi_th_sensor/mi_vibration)
#   - UDP stub device (for the raw path of the top-level `send`)
#   - Reticulum bridge: /device_control (proto) + /udp_raw (raw) -> local gRPC/UDP
# All services listen on 127.0.0.1 ONLY. Access from your machine — via SSH tunnel.
#
# Extra (idempotent, soft hang / VPS ≤2 GiB) — see RETICULUM_GUIDE.md §10:
#   - 1G swap if the host has none yet;
#   - Restart=always on ha-reticulum-bridge;
#   - systemd timer ha-rns-watchdog (port + heartbeat + gRPC probe);
#   - weekly cron: restart ha-reticulum-bridge (Mon 04:15).
#
# Run ON the VPS (from the repo root):
#   bash scripts/install_ha_stack.sh
#
# Options:
#   --grpc-port N   stub gRPC port (default 50055)
#   --udp-port N    UDP stub port         (default 50056)
#   --rns-port N    RNS TCPServer port    (default 50061)
# ============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

GRPC_PORT=50055
UDP_PORT=50056
RNS_PORT=50061

while [[ $# -gt 0 ]]; do
    case $1 in
        --grpc-port) GRPC_PORT="$2"; shift 2;;
        --udp-port)  UDP_PORT="$2"; shift 2;;
        --rns-port)  RNS_PORT="$2"; shift 2;;
        -h|--help)
            sed -n '2,21p' "$0"; exit 0;;
        *) echo -e "${RED}Unknown option: $1${NC}"; exit 1;;
    esac
done

# sudo if not root
if [[ "$(id -u)" -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
HA_DIR="$REPO_ROOT/ha_stack"
VENV="$HA_DIR/.venv"
PY="$VENV/bin/python"
STORAGE="$HA_DIR/.rnsdata"
RUN_USER="$(stat -c '%U' "$HA_DIR")"

# Run-as prefix for the file owner: if we already are that user — no wrapper;
# otherwise sudo -u (if sudo exists); on a minimal VPS without sudo — as-is.
if [[ "$(id -un)" == "$RUN_USER" ]]; then
    AS_USER=""
elif command -v sudo >/dev/null 2>&1; then
    AS_USER="sudo -u $RUN_USER"
else
    AS_USER=""
fi

if [[ ! -d "$HA_DIR" ]]; then
    echo -e "${RED}❌ $HA_DIR not found. Run from the TelegramHelper repo root.${NC}"; exit 1
fi

echo -e "${BLUE}📦 Installing HA stack (stubs) from ${HA_DIR}${NC}"
echo -e "    Ports (localhost): gRPC ${GRPC_PORT}, UDP ${UDP_PORT}, RNS ${RNS_PORT}; service user: ${RUN_USER}"

# 1) Python venv + dependencies
# Ensure python3 + venv module + pip. On a minimal Debian/Ubuntu the venv
# module (ensurepip) is a separate python3-venv package — without it the venv
# is not created, even if `python3 -m venv --help` works.
if command -v apt-get >/dev/null 2>&1; then
    echo -e "${YELLOW}Checking python3-venv/pip...${NC}"
    $SUDO apt-get update -y >/dev/null 2>&1 || true
    $SUDO apt-get install -y python3 python3-venv python3-pip || true
fi
if [[ ! -x "$PY" ]] || ! "$PY" -m pip --version >/dev/null 2>&1; then
    echo -e "${BLUE}Creating venv (from scratch)...${NC}"
    $AS_USER rm -rf "$VENV"
    $AS_USER python3 -m venv "$VENV"
fi
echo -e "${BLUE}Installing dependencies...${NC}"
$AS_USER "$PY" -m pip install --upgrade pip >/dev/null
$AS_USER "$PY" -m pip install -r "$HA_DIR/requirements.txt"
$AS_USER mkdir -p "$STORAGE"

# 2) systemd units
echo -e "${BLUE}Creating systemd units...${NC}"

$SUDO tee /etc/systemd/system/ha-stub-grpc.service > /dev/null << EOF
[Unit]
Description=HA stub gRPC server (Mi-Home device stubs)
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${HA_DIR}
ExecStart=${PY} ${HA_DIR}/stub_server.py --listen 127.0.0.1:${GRPC_PORT}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

$SUDO tee /etc/systemd/system/ha-stub-udp.service > /dev/null << EOF
[Unit]
Description=HA UDP stub device (for raw /udp_raw path)
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${HA_DIR}
ExecStart=${PY} ${HA_DIR}/udp_stub.py --listen-ip 127.0.0.1 --listen-port ${UDP_PORT}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

$SUDO tee /etc/systemd/system/ha-reticulum-bridge.service > /dev/null << EOF
[Unit]
Description=Reticulum bridge (/device_control + /udp_raw) for HA stub
After=network.target ha-stub-grpc.service ha-stub-udp.service
Wants=ha-stub-grpc.service ha-stub-udp.service

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${HA_DIR}
ExecStart=${PY} -m bridge.run_bridge --grpc 127.0.0.1:${GRPC_PORT} --udp-target 127.0.0.1:${UDP_PORT} --config ${HA_DIR}/rns --storage ${STORAGE}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 3) Auto-recovery: swap (if missing), watchdog :RNS_PORT, weekly bridge cron
#    (RETICULUM_GUIDE.md §10). Idempotent — a re-install does not duplicate.
echo -e "${BLUE}Configuring auto-recovery (swap / watchdog / weekly cron)...${NC}"

# 3a) 1G swap — only if the host has no active swap and no /swapfile
if ! swapon --show --noheadings 2>/dev/null | grep -q . && [[ ! -f /swapfile ]]; then
    if $SUDO fallocate -l 1G /swapfile 2>/dev/null || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none; then
        $SUDO chmod 600 /swapfile
        $SUDO mkswap /swapfile >/dev/null
        $SUDO swapon /swapfile
        if ! grep -qE '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab 2>/dev/null; then
            echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
        fi
        echo -e "      ${GREEN}● swap 1G created${NC}"
    else
        echo -e "      ${YELLOW}● swap not created (no space / fallocate+dd failed) — see RETICULUM_GUIDE.md §10.1${NC}"
    fi
else
    echo -e "      ● swap already present — skipping"
fi

# 3b) Watchdog: port + heartbeat + local gRPC backend probe
$SUDO tee /usr/local/bin/ha-rns-watchdog.sh > /dev/null << 'EOF'
#!/bin/bash
# Soft hang recovery for ha-reticulum-bridge (RETICULUM_GUIDE.md §10.2).
# 1) :50061 is not listening → restart bridge
# 2) heartbeat is stale (>90s) → restart bridge (process hung entirely)
# 3) gRPC backend (from ExecStart --grpc) does not answer in 5s → restart adapter (if any) + bridge
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

# 1) port
if ! ss -ltnH | grep -q ":${RNS_PORT}"; then
  restart_bridge "port :${RNS_PORT} not listening"
fi

# 2) heartbeat (file is written by run_bridge)
if [[ -f "$HB" ]]; then
  now=$(date +%s)
  mtime=$(stat -c %Y "$HB" 2>/dev/null || echo 0)
  age=$((now - mtime))
  if (( age > HB_MAX_AGE )); then
    restart_bridge "heartbeat age ${age}s > ${HB_MAX_AGE}s ($HB)"
  fi
fi

# 3) gRPC backend probe
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
# GetDevices — fast liveness; SendCommand __ping__ if the adapter supports it
try:
    stub.GetDevices(pb.GetDeviceRequest(), timeout=4.0)
except grpc.RpcError as e:
    # UNIMPLEMENTED from the stub still means the process answers
    if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        raise
    if e.code() == grpc.StatusCode.UNAVAILABLE:
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

# Environment for heartbeat (bridge unit)
$SUDO mkdir -p /etc/systemd/system/ha-reticulum-bridge.service.d
$SUDO tee /etc/systemd/system/ha-reticulum-bridge.service.d/watchdog.conf > /dev/null << EOF
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

# 3c) Weekly cron: prophylactic bridge restart (Mon 04:15)
CRON_BRIDGE='15 4 * * 1 systemctl restart ha-reticulum-bridge'
if $SUDO crontab -l 2>/dev/null | grep -qF 'systemctl restart ha-reticulum-bridge'; then
    echo -e "      ● cron ha-reticulum-bridge already present — skipping"
else
    ($SUDO crontab -l 2>/dev/null; echo "$CRON_BRIDGE") | $SUDO crontab -
    echo -e "      ${GREEN}● cron: ${CRON_BRIDGE}${NC}"
fi

# 4) Start
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now ha-stub-grpc.service ha-stub-udp.service ha-reticulum-bridge.service
$SUDO systemctl enable --now ha-rns-watchdog.timer

# 5) Fetch the bridge destination hash from the journal
echo -e "${BLUE}Waiting for the bridge to come up and for the destination hash...${NC}"
BRIDGE_HASH=""
for _ in $(seq 1 15); do
    BRIDGE_HASH="$($SUDO journalctl -u ha-reticulum-bridge.service -n 40 --no-pager 2>/dev/null \
        | grep -oE 'destination = [0-9a-f]+' | tail -1 | awk '{print $3}')"
    [[ -n "$BRIDGE_HASH" ]] && break
    sleep 1
done

echo ""
echo -e "${GREEN}✅ HA stack installed and running.${NC}"
echo -e "    Services (localhost): ${BLUE}ha-stub-grpc${NC} :${GRPC_PORT}, ${BLUE}ha-stub-udp${NC} :${UDP_PORT}, ${BLUE}ha-reticulum-bridge${NC} :${RNS_PORT}"
for s in ha-stub-grpc ha-stub-udp ha-reticulum-bridge; do
    if systemctl is-active --quiet "$s"; then
        echo -e "      ${GREEN}● $s active${NC}"
    else
        echo -e "      ${RED}● $s NOT active${NC} (see journalctl -u $s)"
    fi
done
if systemctl is-active --quiet ha-rns-watchdog.timer 2>/dev/null || systemctl is-enabled --quiet ha-rns-watchdog.timer 2>/dev/null; then
    echo -e "      ${GREEN}● ha-rns-watchdog.timer enabled${NC}"
else
    echo -e "      ${YELLOW}● ha-rns-watchdog.timer is not active${NC}"
fi
echo ""
if [[ -n "$BRIDGE_HASH" ]]; then
    echo -e "${YELLOW}Bridge destination hash:${NC} ${BRIDGE_HASH}"
else
    echo -e "${YELLOW}Bridge hash was not read from the journal — fetch it with:${NC}"
    echo -e "    journalctl -u ha-reticulum-bridge.service | grep destination"
fi
echo ""
echo -e "${BLUE}Access from your machine (SSH tunnel):${NC}"
echo -e "    ssh -L ${GRPC_PORT}:127.0.0.1:${GRPC_PORT} -L ${RNS_PORT}:127.0.0.1:${RNS_PORT} <user>@<vps>"
echo -e "${BLUE}Test (from UDP_gRPC_COM_Lite CLI):${NC}"
echo -e "    device send --device mi_bulb --block BU --cmd write --led on --protocol grpc      # TCP gRPC"
echo -e "    device send --device mi_th_sensor --block BU --cmd read --protocol reticulum --bridge-hash ${BRIDGE_HASH:-<hash>} --rns-config <client_rns>"
echo -e "    send --hex \"01 00\" --protocol reticulum --bridge-hash ${BRIDGE_HASH:-<hash>} --rns-config <client_rns>   # raw /udp_raw"
echo ""
echo -e "    Control: ${BLUE}systemctl {status,restart} ha-stub-grpc ha-stub-udp ha-reticulum-bridge${NC}"
echo -e "    Auto-recovery: swap (if it was missing), ${BLUE}ha-rns-watchdog.timer${NC}, cron Mon 04:15 → restart the bridge"
echo -e "    (I2P weekly cron i2pd — with ${BLUE}bash scripts/install_i2p_bridge.sh${NC}; details RETICULUM_GUIDE.md §10)"
