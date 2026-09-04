#!/bin/bash
# ============================================================================
# I2P-слой для RNS-моста HA-стека (путь 2: нативные туннели i2pd)
# ============================================================================
# Делает RNS-мост (ha-reticulum-bridge, 127.0.0.1:50061) доступным как скрытый
# сервис I2P — БЕЗ публичного порта и без SAM. Ставит i2pd и server-туннель
# ha-bridge, который заворачивает локальный TCP моста в стабильный I2P-destination
# (b32). Конфиг RNS НЕ трогается (мост остаётся TCPServerInterface).
#
# Это «путь 2» из I2P_GUIDE.md. i2pd живёт ВНЕ ha_stack/ (системный демон +
# tunnels.d), поэтому скрипт отдельный от install_ha_stack.sh.
#
# Запуск НА VPS (HA-стек уже должен стоять — см. scripts/install_ha_stack.sh):
#   bash scripts/install_i2p_bridge.sh
#
# Дополнительно (идемпотентно): weekly cron restart i2pd (пн 04:30) —
#   профилактика длинного аптайма; b32 не меняется (ha-bridge.dat).
#   См. I2P_GUIDE.md §7.1 / RETICULUM_GUIDE.md §10.3.
#
# Опции:
#   --rns-port N   локальный TCP-порт моста, который заворачиваем (по умолч. 50061)
# ============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

RNS_PORT=50061
TUNNEL_NAME="ha-bridge"
TUNNELS_DIR="/etc/i2pd/tunnels.d"
I2PD_CONF="/etc/i2pd/i2pd.conf"
CONSOLE="http://127.0.0.1:7070"

while [[ $# -gt 0 ]]; do
    case $1 in
        --rns-port) RNS_PORT="$2"; shift 2;;
        -h|--help)  sed -n '2,21p' "$0"; exit 0;;
        *) echo -e "${RED}Неизвестная опция: $1${NC}"; exit 1;;
    esac
done

# sudo, если не root
if [[ "$(id -u)" -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi

echo -e "${BLUE}📦 I2P-слой для RNS-моста (путь 2: нативные туннели i2pd)${NC}"
echo -e "    Заворачиваем мост 127.0.0.1:${RNS_PORT} в I2P-destination туннелем '${TUNNEL_NAME}'."

# 0) Предупреждение, если мост не запущен (туннелю некуда будет ходить)
if command -v systemctl >/dev/null 2>&1 && ! systemctl is-active --quiet ha-reticulum-bridge; then
    echo -e "${YELLOW}⚠️  ha-reticulum-bridge не active. I2P-туннель поднимется, но без моста"
    echo -e "    коннект не пройдёт. Сначала: bash scripts/install_ha_stack.sh${NC}"
fi

# 1) Установка i2pd
if ! command -v i2pd >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        echo -e "${BLUE}Ставлю i2pd...${NC}"
        $SUDO apt-get update -y >/dev/null 2>&1 || true
        $SUDO apt-get install -y i2pd
    else
        echo -e "${RED}❌ i2pd не найден и apt-get недоступен. Поставь i2pd вручную.${NC}"; exit 1
    fi
else
    echo -e "i2pd уже установлен — пропускаю установку пакета."
fi

# 2) Класс полосы P — лучше встраивание/tunnel success на свежем узле.
#    SAM пути 2 НЕ нужен (намеренно не включаем).
if [[ -f "$I2PD_CONF" ]]; then
    if grep -qE '^\s*#?\s*bandwidth\s*=' "$I2PD_CONF"; then
        $SUDO sed -i -E 's/^\s*#?\s*bandwidth\s*=.*/bandwidth = P/' "$I2PD_CONF"
    else
        echo "bandwidth = P" | $SUDO tee -a "$I2PD_CONF" >/dev/null
    fi
fi

# 2.5) reseed hardening (P1: анти-DPI бутстрап). Главный способ заблокировать I2P —
#      это reseed (HTTPS к известным серверам): цензор режет их → узел не входит в
#      сеть. Ставим verify + несколько источников, РЕДАКТИРУЯ существующую секцию
#      [reseed] (второй [reseed] роняет парсер i2pd!). awk: заменяет verify/urls
#      внутри секции (закомм. или нет), при отсутствии — вставляет; секцию не дублит.
RESEED_URLS="https://reseed.i2p-projekt.de/,https://reseed.diva.exchange/,https://reseed.memcpy.io/,https://banana.incognet.io/,https://reseed.i2pgit.org/,https://reseed-pl.i2pd.xyz/"
if [[ -f "$I2PD_CONF" ]]; then
    echo -e "${BLUE}Закаляю reseed (verify + несколько источников)...${NC}"
    TMP_CONF="$(mktemp)"
    $SUDO awk -v URLS="$RESEED_URLS" '
      /^\[/ {
        if (in_reseed) { if(!v) print "verify = true"; if(!u) print "urls = " URLS }
        in_reseed = ($0 ~ /^\[reseed\]/); print; next
      }
      in_reseed && /^[[:space:]]*#?[[:space:]]*verify[[:space:]]*=/ { print "verify = true"; v=1; next }
      in_reseed && /^[[:space:]]*#?[[:space:]]*urls[[:space:]]*=/   { print "urls = " URLS;   u=1; next }
      { print }
      END { if (in_reseed) { if(!v) print "verify = true"; if(!u) print "urls = " URLS } }
    ' "$I2PD_CONF" > "$TMP_CONF" && $SUDO cp "$TMP_CONF" "$I2PD_CONF"
    rm -f "$TMP_CONF"
    if [[ "$(grep -c '^\[reseed\]' "$I2PD_CONF")" -ne 1 ]]; then
        echo -e "${YELLOW}⚠️  секций [reseed] != 1 — проверь ${I2PD_CONF} вручную${NC}"
    fi
fi

# 3) Server-туннель ha-bridge: локальный TCP моста -> I2P-destination (стабильный b32,
#    пока жив ha-bridge.dat в datadir i2pd). Идемпотентно перезаписываем конфиг.
echo -e "${BLUE}Пишу server-туннель ${TUNNELS_DIR}/${TUNNEL_NAME}.conf...${NC}"
$SUDO mkdir -p "$TUNNELS_DIR"
$SUDO tee "${TUNNELS_DIR}/${TUNNEL_NAME}.conf" > /dev/null << EOF
[${TUNNEL_NAME}]
type = server
host = 127.0.0.1
port = ${RNS_PORT}
keys = ${TUNNEL_NAME}.dat
inbound.length = 2
outbound.length = 2
inbound.quantity = 3
outbound.quantity = 3
EOF

# 4) Запуск/перезапуск i2pd
$SUDO systemctl enable i2pd >/dev/null 2>&1 || true
$SUDO systemctl restart i2pd

# 4.5) Weekly cron: профилактический рестарт i2pd (пн 04:30). Идемпотентно.
#      b32 не меняется, пока жив ha-bridge.dat. Cold-start после рестарта 30–120 с.
CRON_I2PD='30 4 * * 1 systemctl restart i2pd'
if $SUDO crontab -l 2>/dev/null | grep -qF 'systemctl restart i2pd'; then
    echo -e "      ● cron i2pd уже есть — пропускаю"
else
    ($SUDO crontab -l 2>/dev/null; echo "$CRON_I2PD") | $SUDO crontab -
    echo -e "      ${GREEN}● cron: ${CRON_I2PD}${NC}"
fi

# 5) Достаём b32 (адрес моста в I2P) из web-консоли. i2pd прогревается несколько
#    минут (reseed + туннели), поэтому ждём с запасом.
echo -e "${BLUE}Жду готовности i2pd и b32 destination (cold-start до ~2 мин)...${NC}"
B32=""
for _ in $(seq 1 40); do
    B32="$(curl -s "${CONSOLE}/?page=i2p_tunnels" 2>/dev/null \
        | sed 's/<[^>]*>/ /g' \
        | grep -ioE '[a-z2-7]{52}\.b32\.i2p' | head -1)"
    [[ -n "$B32" ]] && break
    sleep 3
done

echo ""
if systemctl is-active --quiet i2pd; then
    echo -e "${GREEN}✅ i2pd active, server-туннель ${TUNNEL_NAME} -> 127.0.0.1:${RNS_PORT}.${NC}"
else
    echo -e "${RED}● i2pd NOT active${NC} (см. journalctl -u i2pd)"
fi

if [[ -n "$B32" ]]; then
    echo -e "${YELLOW}I2P bridge b32 (адрес моста для клиентов):${NC}"
    echo -e "    ${B32}"
else
    echo -e "${YELLOW}b32 ещё не появился (туннель строится). Возьми его позже так:${NC}"
    echo -e "    curl -s \"${CONSOLE}/?page=i2p_tunnels\" | sed 's/<[^>]*>/ /g' | grep -iE '${TUNNEL_NAME}|\\.b32'"
fi
echo ""
echo -e "${BLUE}Раздай b32 клиентам (out-of-band, как и bridge-hash). На клиенте — i2pd"
echo -e "client-туннель на этот b32 -> локальный 127.0.0.1:${RNS_PORT}, RNS как обычно"
echo -e "ходит TCPClientInterface на 127.0.0.1:${RNS_PORT} (bridge-hash тот же).${NC}"
echo -e "    Автовосстановление: cron пн 04:30 → ${BLUE}systemctl restart i2pd${NC} (I2P_GUIDE.md §7.1)"
echo -e "${BLUE}Детали и клиентская сторона — I2P_GUIDE.md.${NC}"
