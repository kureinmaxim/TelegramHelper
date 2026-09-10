#!/usr/bin/env bash
# ============================================================================
# preflight_subnets.sh — compare compose.yaml subnets with existing
# Docker networks on this host.
#
# Why: compose.yaml pins the default and socket_proxy network subnets
# (COMPOSE_DEFAULT_SUBNET / COMPOSE_SOCKET_PROXY_SUBNET,
# see DEPLOY.md §8.9). On a host where the stack is already deployed with a
# DIFFERENT subnet (e.g. a headscale host with 172.20.0.0/16), `docker compose
# up` sees the mismatch and tries to recreate the network: the bot container
# stops, network delete fails because dockhand/headplane are still attached —
# and the bot is left with "is not connected to the network". This script
# catches the mismatch BEFORE `up`.
#
#   bash scripts/preflight_subnets.sh          # check; exit 1 on mismatch
#   bash scripts/preflight_subnets.sh --fix    # write existing subnets into .env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

FIX=0
[[ "${1:-}" == "--fix" ]] && FIX=1

# Defaults must match compose.yaml (${VAR:-...}).
DEF_DEFAULT_SUBNET="172.18.0.0/16"
DEF_PROXY_SUBNET="172.28.0.0/24"

if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found — nothing to check (first install?)."
    exit 0
fi

# Compose project name: COMPOSE_PROJECT_NAME or the directory name,
# normalised per compose rules (lowercase, [a-z0-9_-]).
proj="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')}"

# Last value of the variable from .env (same as compose).
env_get() {
    [[ -f .env ]] || return 0
    sed -n "s/^${1}=//p" .env | tail -1
}

declared_default="$(env_get COMPOSE_DEFAULT_SUBNET)"
declared_default="${declared_default:-$DEF_DEFAULT_SUBNET}"
declared_proxy="$(env_get COMPOSE_SOCKET_PROXY_SUBNET)"
declared_proxy="${declared_proxy:-$DEF_PROXY_SUBNET}"

mismatch=0
fix_vars=()   # "VAR=actual_subnet" for --fix

check_net() {
    local net="$1" declared="$2" var="$3"
    local actual
    actual="$(docker network inspect "$net" \
        -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || true)"
    if [[ -z "$actual" ]]; then
        echo "✓ ${net}: network does not exist yet — compose will create it with subnet ${declared}"
    elif [[ "$actual" == "$declared" ]]; then
        echo "✓ ${net}: ${actual} (matches declared)"
    else
        echo "✗ ${net}: exists with subnet ${actual}, but declared is ${declared}"
        fix_vars+=("${var}=${actual}")
        mismatch=1
    fi
}

check_net "${proj}_default"      "$declared_default" "COMPOSE_DEFAULT_SUBNET"
check_net "${proj}_socket_proxy" "$declared_proxy"   "COMPOSE_SOCKET_PROXY_SUBNET"

if [[ "$mismatch" -eq 0 ]]; then
    echo "Subnets look good, docker compose up is safe."
    exit 0
fi

if [[ "$FIX" -eq 1 ]]; then
    for kv in "${fix_vars[@]}"; do
        var="${kv%%=*}"
        val="${kv#*=}"
        if [[ -f .env ]] && grep -q "^${var}=" .env; then
            # Replace in place: duplicate keys in .env are a source of confusion.
            sed -i.bak "s|^${var}=.*|${var}=${val}|" .env && rm -f .env.bak
        else
            printf '%s=%s\n' "$var" "$val" >> .env
        fi
        echo "→ .env: ${var}=${val}"
    done
    echo "Done. docker compose up will no longer recreate the networks."
    exit 0
fi

echo ""
echo "docker compose up will now try to RECREATE the network and take the bot down."
echo "Fix automatically (writes existing subnets into .env):"
echo ""
echo "    bash scripts/preflight_subnets.sh --fix"
echo ""
exit 1
