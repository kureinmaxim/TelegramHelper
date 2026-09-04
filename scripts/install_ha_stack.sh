#!/bin/bash
# ============================================================================
# HA-стек (заглушки) — установка на VPS
# ============================================================================
# Ставит self-contained стек для e2e-тестов клиента UDP_gRPC_COM_Lite:
#   - gRPC DeviceControlService с заглушками Mi-Home (mi_bulb/mi_th_sensor/mi_vibration)
#   - UDP-стаб-устройство (для raw-пути верхнеуровневого `send`)
#   - Reticulum-мост: /device_control (прото) + /udp_raw (raw) -> локальный gRPC/UDP
# Все сервисы слушают ТОЛЬКО 127.0.0.1. Доступ с твоей машины — через SSH-туннель.
#
# Дополнительно (идемпотентно, soft hang / VPS ≤2 GiB) — см. RETICULUM_GUIDE.md §10:
#   - swap 1G, если на хосте ещё нет swap;
#   - Restart=always у ha-reticulum-bridge;
#   - systemd timer ha-rns-watchdog (порт + heartbeat + gRPC probe);
#   - weekly cron: restart ha-reticulum-bridge (пн 04:15).
#
# Запуск НА VPS (из корня репозитория):
#   bash scripts/install_ha_stack.sh
#
# Опции:
#   --grpc-port N   gRPC порт заглушек (по умолчанию 50055)
#   --udp-port N    UDP-стаб порт          (по умолчанию 50056)
#   --rns-port N    RNS TCPServer порт     (по умолчанию 50061)
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
        *) echo -e "${RED}Неизвестная опция: $1${NC}"; exit 1;;
    esac
done

# sudo, если не root
if [[ "$(id -u)" -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi

# Пути
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
HA_DIR="$REPO_ROOT/ha_stack"
VENV="$HA_DIR/.venv"
PY="$VENV/bin/python"
STORAGE="$HA_DIR/.rnsdata"
RUN_USER="$(stat -c '%U' "$HA_DIR")"

# Префикс запуска под владельцем файлов: если мы уже он — без обёртки; иначе
# sudo -u (если sudo есть); на минимальном VPS без sudo — как есть.
if [[ "$(id -un)" == "$RUN_USER" ]]; then
    AS_USER=""
elif command -v sudo >/dev/null 2>&1; then
    AS_USER="sudo -u $RUN_USER"
else
    AS_USER=""
fi

if [[ ! -d "$HA_DIR" ]]; then
    echo -e "${RED}❌ Не найден $HA_DIR. Запусти из корня репозитория TelegramHelper.${NC}"; exit 1
fi

echo -e "${BLUE}📦 Установка HA-стека (заглушки) из ${HA_DIR}${NC}"
echo -e "    Порты (localhost): gRPC ${GRPC_PORT}, UDP ${UDP_PORT}, RNS ${RNS_PORT}; сервисный пользователь: ${RUN_USER}"

# 1) Python venv + зависимости
# Гарантируем python3 + venv-модуль + pip. На минимальном Debian/Ubuntu модуль
# venv (ensurepip) ставится отдельным пакетом python3-venv — без него venv не
# создаётся, даже если `python3 -m venv --help` работает.
if command -v apt-get >/dev/null 2>&1; then
    echo -e "${YELLOW}Проверяю python3-venv/pip...${NC}"
    $SUDO apt-get update -y >/dev/null 2>&1 || true
    $SUDO apt-get install -y python3 python3-venv python3-pip || true
fi
if [[ ! -x "$PY" ]] || ! "$PY" -m pip --version >/dev/null 2>&1; then
    echo -e "${BLUE}Создаю venv (с нуля)...${NC}"
    $AS_USER rm -rf "$VENV"
    $AS_USER python3 -m venv "$VENV"
fi
echo -e "${BLUE}Ставлю зависимости...${NC}"
$AS_USER "$PY" -m pip install --upgrade pip >/dev/null
$AS_USER "$PY" -m pip install -r "$HA_DIR/requirements.txt"
$AS_USER mkdir -p "$STORAGE"

# 2) systemd-юниты
echo -e "${BLUE}Создаю systemd-юниты...${NC}"

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

# 3) Автовосстановление: swap (если нет), watchdog :RNS_PORT, weekly cron моста
#    (RETICULUM_GUIDE.md §10). Идемпотентно — повторный install не дублирует.
echo -e "${BLUE}Настраиваю автовосстановление (swap / watchdog / weekly cron)...${NC}"

# 3a) Swap 1G — только если на хосте ещё нет активного swap и нет /swapfile
if ! swapon --show --noheadings 2>/dev/null | grep -q . && [[ ! -f /swapfile ]]; then
    if $SUDO fallocate -l 1G /swapfile 2>/dev/null || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none; then
        $SUDO chmod 600 /swapfile
        $SUDO mkswap /swapfile >/dev/null
        $SUDO swapon /swapfile
        if ! grep -qE '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab 2>/dev/null; then
            echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
        fi
        echo -e "      ${GREEN}● swap 1G создан${NC}"
    else
        echo -e "      ${YELLOW}● swap не создан (нет места / fallocate+dd failed) — см. RETICULUM_GUIDE.md §10.1${NC}"
    fi
else
    echo -e "      ● swap уже есть — пропускаю"
fi

# 3b) Watchdog: порт + heartbeat + локальный gRPC probe бэкенда
$SUDO tee /usr/local/bin/ha-rns-watchdog.sh > /dev/null << 'EOF'
#!/bin/bash
# Soft hang recovery for ha-reticulum-bridge (RETICULUM_GUIDE.md §10.2).
# 1) :50061 не слушает → restart bridge
# 2) heartbeat устарел (>90s) → restart bridge (процесс завис целиком)
# 3) gRPC backend (из ExecStart --grpc) не отвечает за 5s → restart adapter (если есть) + bridge
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

# 1) порт
if ! ss -ltnH | grep -q ":${RNS_PORT}"; then
  restart_bridge "port :${RNS_PORT} not listening"
fi

# 2) heartbeat (файл пишет run_bridge)
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
# GetDevices — быстрый liveness; SendCommand __ping__ если адаптер поддерживает
try:
    stub.GetDevices(pb.GetDeviceRequest(), timeout=4.0)
except grpc.RpcError as e:
    # UNIMPLEMENTED у stub — всё равно значит процесс отвечает
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

# Environment для heartbeat (юнит моста)
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

# 3c) Weekly cron: профилактический рестарт моста (пн 04:15)
CRON_BRIDGE='15 4 * * 1 systemctl restart ha-reticulum-bridge'
if $SUDO crontab -l 2>/dev/null | grep -qF 'systemctl restart ha-reticulum-bridge'; then
    echo -e "      ● cron ha-reticulum-bridge уже есть — пропускаю"
else
    ($SUDO crontab -l 2>/dev/null; echo "$CRON_BRIDGE") | $SUDO crontab -
    echo -e "      ${GREEN}● cron: ${CRON_BRIDGE}${NC}"
fi

# 4) Запуск
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now ha-stub-grpc.service ha-stub-udp.service ha-reticulum-bridge.service
$SUDO systemctl enable --now ha-rns-watchdog.timer

# 5) Достаём destination hash моста из журнала
echo -e "${BLUE}Жду подъёма моста и destination hash...${NC}"
BRIDGE_HASH=""
for _ in $(seq 1 15); do
    BRIDGE_HASH="$($SUDO journalctl -u ha-reticulum-bridge.service -n 40 --no-pager 2>/dev/null \
        | grep -oE 'destination = [0-9a-f]+' | tail -1 | awk '{print $3}')"
    [[ -n "$BRIDGE_HASH" ]] && break
    sleep 1
done

echo ""
echo -e "${GREEN}✅ HA-стек установлен и запущен.${NC}"
echo -e "    Сервисы (localhost): ${BLUE}ha-stub-grpc${NC} :${GRPC_PORT}, ${BLUE}ha-stub-udp${NC} :${UDP_PORT}, ${BLUE}ha-reticulum-bridge${NC} :${RNS_PORT}"
for s in ha-stub-grpc ha-stub-udp ha-reticulum-bridge; do
    if systemctl is-active --quiet "$s"; then
        echo -e "      ${GREEN}● $s active${NC}"
    else
        echo -e "      ${RED}● $s NOT active${NC} (см. journalctl -u $s)"
    fi
done
if systemctl is-active --quiet ha-rns-watchdog.timer 2>/dev/null || systemctl is-enabled --quiet ha-rns-watchdog.timer 2>/dev/null; then
    echo -e "      ${GREEN}● ha-rns-watchdog.timer enabled${NC}"
else
    echo -e "      ${YELLOW}● ha-rns-watchdog.timer не активен${NC}"
fi
echo ""
if [[ -n "$BRIDGE_HASH" ]]; then
    echo -e "${YELLOW}Bridge destination hash:${NC} ${BRIDGE_HASH}"
else
    echo -e "${YELLOW}Bridge hash не считался из журнала — возьми его так:${NC}"
    echo -e "    journalctl -u ha-reticulum-bridge.service | grep destination"
fi
echo ""
echo -e "${BLUE}Доступ с твоей машины (SSH-туннель):${NC}"
echo -e "    ssh -L ${GRPC_PORT}:127.0.0.1:${GRPC_PORT} -L ${RNS_PORT}:127.0.0.1:${RNS_PORT} <user>@<vps>"
echo -e "${BLUE}Тест (из UDP_gRPC_COM_Lite CLI):${NC}"
echo -e "    device send --device mi_bulb --block BU --cmd write --led on --protocol grpc      # TCP gRPC"
echo -e "    device send --device mi_th_sensor --block BU --cmd read --protocol reticulum --bridge-hash ${BRIDGE_HASH:-<hash>} --rns-config <client_rns>"
echo -e "    send --hex \"01 00\" --protocol reticulum --bridge-hash ${BRIDGE_HASH:-<hash>} --rns-config <client_rns>   # raw /udp_raw"
echo ""
echo -e "    Управление: ${BLUE}systemctl {status,restart} ha-stub-grpc ha-stub-udp ha-reticulum-bridge${NC}"
echo -e "    Автовосстановление: swap (если не было), ${BLUE}ha-rns-watchdog.timer${NC}, cron пн 04:15 → restart моста"
echo -e "    (I2P weekly cron i2pd — при ${BLUE}bash scripts/install_i2p_bridge.sh${NC}; детали RETICULUM_GUIDE.md §10)"
