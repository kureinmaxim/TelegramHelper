#!/usr/bin/env bash
# ============================================================================
# preflight_subnets.sh — сверка подсетей compose.yaml с уже существующими
# сетями Docker на этом хосте.
#
# Зачем: compose.yaml закрепляет подсети сетей default и socket_proxy
# (переменные COMPOSE_DEFAULT_SUBNET / COMPOSE_SOCKET_PROXY_SUBNET,
# см. DEPLOY.md §8.9). На хосте, где стек уже развёрнут с ДРУГОЙ подсетью
# (например, headscale-хост с 172.20.0.0/16), `docker compose up` видит
# несовпадение и пытается пересоздать сеть: контейнер бота останавливается,
# удаление сети падает из-за активных dockhand/headplane — и бот остаётся
# лежать с ошибкой "is not connected to the network". Этот скрипт ловит
# расхождение ДО `up`.
#
#   bash scripts/preflight_subnets.sh          # проверка; exit 1 при расхождении
#   bash scripts/preflight_subnets.sh --fix    # прописать существующие подсети в .env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

FIX=0
[[ "${1:-}" == "--fix" ]] && FIX=1

# Дефолты обязаны совпадать с compose.yaml (${VAR:-...}).
DEF_DEFAULT_SUBNET="172.18.0.0/16"
DEF_PROXY_SUBNET="172.28.0.0/24"

if ! command -v docker >/dev/null 2>&1; then
    echo "docker не найден — проверять нечего (первая установка?)."
    exit 0
fi

# Имя compose-проекта: COMPOSE_PROJECT_NAME или имя каталога,
# нормализованное по правилам compose (lowercase, [a-z0-9_-]).
proj="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')}"

# Последнее значение переменной из .env (как это делает compose).
env_get() {
    [[ -f .env ]] || return 0
    sed -n "s/^${1}=//p" .env | tail -1
}

declared_default="$(env_get COMPOSE_DEFAULT_SUBNET)"
declared_default="${declared_default:-$DEF_DEFAULT_SUBNET}"
declared_proxy="$(env_get COMPOSE_SOCKET_PROXY_SUBNET)"
declared_proxy="${declared_proxy:-$DEF_PROXY_SUBNET}"

mismatch=0
fix_vars=()   # "VAR=actual_subnet" для --fix

check_net() {
    local net="$1" declared="$2" var="$3"
    local actual
    actual="$(docker network inspect "$net" \
        -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || true)"
    if [[ -z "$actual" ]]; then
        echo "✓ ${net}: сети ещё нет — compose создаст её с подсетью ${declared}"
    elif [[ "$actual" == "$declared" ]]; then
        echo "✓ ${net}: ${actual} (совпадает с объявленной)"
    else
        echo "✗ ${net}: существует с подсетью ${actual}, а объявлена ${declared}"
        fix_vars+=("${var}=${actual}")
        mismatch=1
    fi
}

check_net "${proj}_default"      "$declared_default" "COMPOSE_DEFAULT_SUBNET"
check_net "${proj}_socket_proxy" "$declared_proxy"   "COMPOSE_SOCKET_PROXY_SUBNET"

if [[ "$mismatch" -eq 0 ]]; then
    echo "Подсети в порядке, можно делать docker compose up."
    exit 0
fi

if [[ "$FIX" -eq 1 ]]; then
    for kv in "${fix_vars[@]}"; do
        var="${kv%%=*}"
        val="${kv#*=}"
        if [[ -f .env ]] && grep -q "^${var}=" .env; then
            # Заменяем на месте: дубликаты ключей в .env — источник путаницы.
            sed -i.bak "s|^${var}=.*|${var}=${val}|" .env && rm -f .env.bak
        else
            printf '%s=%s\n' "$var" "$val" >> .env
        fi
        echo "→ .env: ${var}=${val}"
    done
    echo "Готово. Теперь docker compose up не будет пересоздавать сети."
    exit 0
fi

echo ""
echo "docker compose up сейчас попытается ПЕРЕСОЗДАТЬ сеть и уронит бота."
echo "Исправить автоматически (пропишет существующие подсети в .env):"
echo ""
echo "    bash scripts/preflight_subnets.sh --fix"
echo ""
exit 1
