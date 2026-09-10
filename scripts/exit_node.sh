#!/usr/bin/env bash
#
# exit_node.sh — break-glass Tailscale exit-node control DIRECTLY ON THE HOST.
#
# Why: the exit node (internet via the VPS coordinator) is normally enabled
# with the bot command /exit_node_on. There is a chicken-and-egg: Telegram
# itself may only be reachable through the VPN, so you cannot reach the bot
# until the exit node is up. This script is the SSH emergency path, bypassing
# Telegram and even the bot container: it talks to host `tailscale` and
# `docker exec headscale` directly (no nsenter, because we already run on the host).
#
# Matches HEADSCALE_GUIDE.md → "Exit node" and headscale_manager.py.
#
# Usage (on the server that runs the bot and the coordinator):
#   sudo ./scripts/exit_node.sh on        # make this VPS an exit node
#   sudo ./scripts/exit_node.sh off        # stop being an exit node
#   sudo ./scripts/exit_node.sh status     # show current state
#
# Environment (optional):
#   HS_CONTAINER=headscale   # Headscale Docker container name
#
# Requirements: run as root (sysctl/tailscale/docker). The tailscale client on
# the host must already be joined to your Headscale. JSON is parsed with jq or
# python3 (whichever is found); if neither exists, approve is done by hand
# (the script prints the commands).

set -uo pipefail

HS_CONTAINER="${HS_CONTAINER:-headscale}"
EXIT_ROUTES="0.0.0.0/0,::/0"
SYSCTL_PERSIST="/etc/sysctl.d/99-tailscale-exit.conf"

err()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
info() { printf '%s\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }

# --- find tailscale binary on host ---
TS=""
for cand in tailscale /usr/bin/tailscale /usr/sbin/tailscale; do
  if command -v "$cand" >/dev/null 2>&1; then TS="$cand"; break; fi
done
if [ -z "$TS" ]; then
  err "❌ tailscale not found on the host. Install the client and join Headscale (HEADSCALE_GUIDE.md)."
  exit 1
fi

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    err "❌ Root is required. Run via sudo."
    exit 1
  fi
}

# --- headscale nodes list -o json (via docker exec) ---
hs_nodes_json() {
  docker exec "$HS_CONTAINER" headscale nodes list -o json 2>/dev/null
}

# Extract the id of the node that advertised the exit route (0.0.0.0/0 in availableRoutes).
# Prints id to stdout or empty. Uses jq → python3 → (empty).
find_exit_node_id() {
  local json; json="$(hs_nodes_json)"
  [ -z "$json" ] && return 0

  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$json" | jq -r '
      (if type=="array" then . else (.nodes // []) end)
      | map(select(
          ((.availableRoutes // .available_routes // .subnetRoutes // []) | index("0.0.0.0/0")) != null
        ))
      | (.[0].id // .[0].ID // empty) | tostring
    ' 2>/dev/null
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 -c '
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
nodes = data if isinstance(data, list) else data.get("nodes", [])
for n in nodes:
    if not isinstance(n, dict):
        continue
    avail = (n.get("availableRoutes") or n.get("available_routes")
             or n.get("subnetRoutes") or [])
    if "0.0.0.0/0" in [str(x) for x in avail]:
        print(str(n.get("id") or n.get("ID") or ""))
        break
' 2>/dev/null
    return 0
  fi
  return 0
}

# Check whether the exit route is approved (0.0.0.0/0 in approvedRoutes).
is_exit_approved() {
  local json; json="$(hs_nodes_json)"
  [ -z "$json" ] && return 1

  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$json" | jq -e '
      (if type=="array" then . else (.nodes // []) end)
      | any(((.approvedRoutes // .approved_routes // .enabledRoutes // []) | index("0.0.0.0/0")) != null)
    ' >/dev/null 2>&1
    return $?
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 -c '
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
nodes = data if isinstance(data, list) else data.get("nodes", [])
for n in nodes:
    if not isinstance(n, dict):
        continue
    appr = (n.get("approvedRoutes") or n.get("approved_routes")
            or n.get("enabledRoutes") or [])
    if "0.0.0.0/0" in [str(x) for x in appr]:
        sys.exit(0)
sys.exit(1)
' >/dev/null 2>&1
    return $?
  fi
  return 1
}

approve_routes() {
  local node_id="$1"
  # headscale 0.26+: nodes approve-routes
  if docker exec "$HS_CONTAINER" headscale nodes approve-routes \
       -i "$node_id" -r "$EXIT_ROUTES" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

manual_approve_hint() {
  warn "Auto-approve failed. Enable the routes by hand in one of these ways:"
  info "  • Headplane: coordinator node → Routes → enable 0.0.0.0/0 and ::/0"
  info "  • CLI (0.26+): docker exec ${HS_CONTAINER} headscale nodes approve-routes -i <id> -r ${EXIT_ROUTES}"
  info "  • CLI (0.23–0.25):"
  info "      docker exec ${HS_CONTAINER} headscale nodes list | cat   # find <id>"
  info "      docker exec ${HS_CONTAINER} headscale routes list         # find route-ids"
  info "      docker exec ${HS_CONTAINER} headscale routes enable -r <route-id>"
}

cmd_status() {
  info "=== Exit node — status (host) ==="
  local f4 f6
  f4="$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo '?')"
  f6="$(sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo '?')"
  info "IP forwarding: IPv4=${f4}, IPv6=${f6}"

  if ! docker inspect -f '{{.State.Running}}' "$HS_CONTAINER" >/dev/null 2>&1; then
    warn "Container ${HS_CONTAINER} not found/not running — Headscale status unavailable."
    return 0
  fi

  local nid; nid="$(find_exit_node_id)"
  if [ -n "$nid" ]; then
    ok "advertise: ✅ a node advertised the exit route (id=${nid})"
  else
    warn "advertise: ❌ no node advertises 0.0.0.0/0"
  fi
  if is_exit_approved; then
    ok "approve:   ✅ exit route is approved in Headscale"
  else
    warn "approve:   ❌ exit route is not approved"
  fi
  if [ -n "$nid" ] && is_exit_approved; then
    ok "🟢 Exit node is ready — pick it on the device (Tailscale → Exit Node)."
  fi
}

cmd_on() {
  need_root
  info "→ Advertising this host as an exit node..."
  if ! "$TS" set --advertise-exit-node; then
    err "❌ tailscale set --advertise-exit-node failed."
    exit 1
  fi
  ok "✅ advertise enabled."

  info "→ Enabling IP forwarding (runtime + persistent)..."
  sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || warn "⚠️ failed to set net.ipv4.ip_forward"
  sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || warn "⚠️ failed to set net.ipv6.conf.all.forwarding"
  if [ ! -f "$SYSCTL_PERSIST" ]; then
    printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' > "$SYSCTL_PERSIST" \
      && ok "✅ forwarding persisted in ${SYSCTL_PERSIST}" \
      || warn "⚠️ failed to write ${SYSCTL_PERSIST} (forwarding is active only until reboot)"
  else
    ok "✅ ${SYSCTL_PERSIST} already exists."
  fi

  if ! docker inspect -f '{{.State.Running}}' "$HS_CONTAINER" >/dev/null 2>&1; then
    warn "⚠️ Container ${HS_CONTAINER} is unavailable — approve skipped."
    manual_approve_hint
    return 0
  fi

  info "→ Finding the node and approving the exit route in Headscale..."
  # Give headscale a second to "see" the fresh advertise.
  sleep 2
  local nid; nid="$(find_exit_node_id)"
  if [ -z "$nid" ]; then
    warn "⚠️ Headscale does not see the exit route yet. Retry in 5–10 s or approve by hand."
    manual_approve_hint
    return 0
  fi
  if approve_routes "$nid"; then
    ok "✅ Exit route approved (node id=${nid})."
    echo
    ok "🎉 Exit node is ready. On the device: Tailscale → Exit Node → pick this VPS."
    info "   Check from the device: https://ifconfig.me should show this VPS IP."
  else
    manual_approve_hint
  fi
}

cmd_off() {
  need_root
  info "→ Disabling advertise exit node on the host..."
  if "$TS" set --advertise-exit-node=false; then
    ok "✅ Exit node off: the host no longer advertises 0.0.0.0/0."
    info "   Clients that picked it will lose internet via this VPS."
  else
    err "❌ Failed to disable advertise."
    exit 1
  fi
}

case "${1:-}" in
  on)     cmd_on ;;
  off)    cmd_off ;;
  status) cmd_status ;;
  *)
    info "Usage: $0 {on|off|status}"
    info ""
    info "  on      — make this VPS an exit node (advertise + forwarding + approve)"
    info "  off     — stop being an exit node"
    info "  status  — show state"
    exit 1
    ;;
esac
