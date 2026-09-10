#!/bin/bash
# ============================================================================
# I2P layer for the HA-stack RNS bridge (path 2: native i2pd tunnels)
# ============================================================================
# Makes the RNS bridge (ha-reticulum-bridge, 127.0.0.1:50061) available as a
# hidden I2P service — WITHOUT a public port and without SAM. Installs i2pd
# and the ha-bridge server tunnel, which wraps the local bridge TCP into a
# stable I2P destination (b32). RNS config is NOT touched (the bridge stays
# TCPServerInterface).
#
# This is "path 2" from RETICULUM_GUIDE.md §8. i2pd lives OUTSIDE ha_stack/ (system
# daemon + tunnels.d), so this script is separate from install_ha_stack.sh.
#
# Run ON the VPS (HA stack must already be installed — see scripts/install_ha_stack.sh):
#   bash scripts/install_i2p_bridge.sh
#
# Extra (idempotent): weekly cron restart of i2pd (Mon 04:30) —
#   long-uptime hygiene; b32 does not change (ha-bridge.dat).
#   See RETICULUM_GUIDE.md §8 / §10.3.
#
# Options:
#   --rns-port N   local TCP port of the bridge we wrap (default 50061)
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
        *) echo -e "${RED}Unknown option: $1${NC}"; exit 1;;
    esac
done

# sudo if not root
if [[ "$(id -u)" -ne 0 ]]; then SUDO="sudo"; else SUDO=""; fi

echo -e "${BLUE}📦 I2P layer for the RNS bridge (path 2: native i2pd tunnels)${NC}"
echo -e "    Wrapping bridge 127.0.0.1:${RNS_PORT} into an I2P destination with tunnel '${TUNNEL_NAME}'."

# 0) Warn if the bridge is not running (the tunnel will have nowhere to go)
if command -v systemctl >/dev/null 2>&1 && ! systemctl is-active --quiet ha-reticulum-bridge; then
    echo -e "${YELLOW}⚠️  ha-reticulum-bridge is not active. The I2P tunnel will start, but without the bridge"
    echo -e "    connects will fail. First: bash scripts/install_ha_stack.sh${NC}"
fi

# 1) Install i2pd
if ! command -v i2pd >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        echo -e "${BLUE}Installing i2pd...${NC}"
        $SUDO apt-get update -y >/dev/null 2>&1 || true
        $SUDO apt-get install -y i2pd
    else
        echo -e "${RED}❌ i2pd not found and apt-get is unavailable. Install i2pd by hand.${NC}"; exit 1
    fi
else
    echo -e "i2pd already installed — skipping the package."
fi

# 2) Bandwidth class P — better embed/tunnel success on a fresh node.
#    Path-2 SAM is NOT needed (intentionally not enabled).
if [[ -f "$I2PD_CONF" ]]; then
    if grep -qE '^\s*#?\s*bandwidth\s*=' "$I2PD_CONF"; then
        $SUDO sed -i -E 's/^\s*#?\s*bandwidth\s*=.*/bandwidth = P/' "$I2PD_CONF"
    else
        echo "bandwidth = P" | $SUDO tee -a "$I2PD_CONF" >/dev/null
    fi
fi

# 2.5) reseed hardening (P1: anti-DPI bootstrap). The main way to block I2P is
#      reseed (HTTPS to known servers): the censor cuts them → the node never
#      joins. Set verify + several sources, EDITING the existing [reseed]
#      section (a second [reseed] crashes the i2pd parser!). awk: replace
#      verify/urls inside the section (commented or not); insert if missing;
#      never duplicate the section.
RESEED_URLS="https://reseed.i2p-projekt.de/,https://reseed.diva.exchange/,https://reseed.memcpy.io/,https://banana.incognet.io/,https://reseed.i2pgit.org/,https://reseed-pl.i2pd.xyz/"
if [[ -f "$I2PD_CONF" ]]; then
    echo -e "${BLUE}Hardening reseed (verify + several sources)...${NC}"
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
        echo -e "${YELLOW}⚠️  [reseed] section count != 1 — check ${I2PD_CONF} by hand${NC}"
    fi
fi

# 3) Server tunnel ha-bridge: local bridge TCP -> I2P destination (stable b32
#    while ha-bridge.dat lives in the i2pd datadir). Idempotently overwrite the config.
echo -e "${BLUE}Writing server tunnel ${TUNNELS_DIR}/${TUNNEL_NAME}.conf...${NC}"
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

# 4) Start/restart i2pd
$SUDO systemctl enable i2pd >/dev/null 2>&1 || true
$SUDO systemctl restart i2pd

# 4.5) Weekly cron: prophylactic i2pd restart (Mon 04:30). Idempotent.
#      b32 does not change while ha-bridge.dat lives. Cold-start after restart 30–120 s.
CRON_I2PD='30 4 * * 1 systemctl restart i2pd'
if $SUDO crontab -l 2>/dev/null | grep -qF 'systemctl restart i2pd'; then
    echo -e "      ● cron i2pd already present — skipping"
else
    ($SUDO crontab -l 2>/dev/null; echo "$CRON_I2PD") | $SUDO crontab -
    echo -e "      ${GREEN}● cron: ${CRON_I2PD}${NC}"
fi

# 5) Fetch b32 (bridge address in I2P) from the web console. i2pd warms up
#    for several minutes (reseed + tunnels), so wait with headroom.
echo -e "${BLUE}Waiting for i2pd and b32 destination (cold-start up to ~2 min)...${NC}"
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
    echo -e "${GREEN}✅ i2pd active, server tunnel ${TUNNEL_NAME} -> 127.0.0.1:${RNS_PORT}.${NC}"
else
    echo -e "${RED}● i2pd NOT active${NC} (see journalctl -u i2pd)"
fi

if [[ -n "$B32" ]]; then
    echo -e "${YELLOW}I2P bridge b32 (bridge address for clients):${NC}"
    echo -e "    ${B32}"
else
    echo -e "${YELLOW}b32 has not appeared yet (tunnel is building). Fetch it later with:${NC}"
    echo -e "    curl -s \"${CONSOLE}/?page=i2p_tunnels\" | sed 's/<[^>]*>/ /g' | grep -iE '${TUNNEL_NAME}|\\.b32'"
fi
echo ""
echo -e "${BLUE}Share the b32 with clients (out-of-band, like the bridge-hash). On the client — i2pd"
echo -e "client tunnel to this b32 -> local 127.0.0.1:${RNS_PORT}; RNS as usual"
echo -e "uses TCPClientInterface on 127.0.0.1:${RNS_PORT} (same bridge-hash).${NC}"
echo -e "    Auto-recovery: cron Mon 04:30 → ${BLUE}systemctl restart i2pd${NC} (RETICULUM_GUIDE.md §8)"
echo -e "${BLUE}Details and the client side — RETICULUM_GUIDE.md §8.${NC}"
