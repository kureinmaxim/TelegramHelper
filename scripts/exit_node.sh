#!/usr/bin/env bash
#
# exit_node.sh — break-glass управление Tailscale exit node ПРЯМО НА ХОСТЕ.
#
# Зачем: exit node (выход в интернет через VPS-координатор) обычно включается
# командой бота /exit_node_on. Но бывает курица-яйцо: сам Telegram доступен
# только через VPN, значит до бота не достучаться, пока exit node не поднят.
# Этот скрипт — аварийный путь по SSH, в обход Telegram и даже контейнера бота:
# он ходит в host-`tailscale` и `docker exec headscale` напрямую (без nsenter,
# т.к. уже выполняется на самом хосте).
#
# Соответствует HEADSCALE_GUIDE.md → «Exit node» и логике headscale_manager.py.
#
# Использование (на сервере, где крутятся бот и координатор):
#   sudo ./scripts/exit_node.sh on        # сделать VPS exit node'ом
#   sudo ./scripts/exit_node.sh off        # перестать быть exit node'ом
#   sudo ./scripts/exit_node.sh status     # показать текущее состояние
#
# Переменные окружения (необязательные):
#   HS_CONTAINER=headscale   # имя Docker-контейнера Headscale
#
# Требования: запускать от root (sysctl/tailscale/docker). tailscale-клиент на
# хосте должен быть подключён к вашему Headscale. JSON парсится через jq или
# python3 (что найдётся); если нет ни того, ни другого — approve делается
# вручную (скрипт подскажет команды).

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
  err "❌ tailscale не найден на хосте. Установите клиент и подключите к Headscale (HEADSCALE_GUIDE.md)."
  exit 1
fi

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    err "❌ Нужны права root. Запустите через sudo."
    exit 1
  fi
}

# --- headscale nodes list -o json (через docker exec) ---
hs_nodes_json() {
  docker exec "$HS_CONTAINER" headscale nodes list -o json 2>/dev/null
}

# Извлечь id узла, который объявил exit-маршрут (0.0.0.0/0 в availableRoutes).
# Печатает id в stdout либо пусто. Использует jq → python3 → (пусто).
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

# Проверить, аппрувнут ли exit-маршрут (0.0.0.0/0 в approvedRoutes).
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
  warn "Авто-approve не прошёл. Включите маршруты вручную одним из способов:"
  info "  • Headplane: узел координатора → Routes → включить 0.0.0.0/0 и ::/0"
  info "  • CLI (0.26+): docker exec ${HS_CONTAINER} headscale nodes approve-routes -i <id> -r ${EXIT_ROUTES}"
  info "  • CLI (0.23–0.25):"
  info "      docker exec ${HS_CONTAINER} headscale nodes list | cat   # найти <id>"
  info "      docker exec ${HS_CONTAINER} headscale routes list         # найти route-id'ы"
  info "      docker exec ${HS_CONTAINER} headscale routes enable -r <route-id>"
}

cmd_status() {
  info "=== Exit node — статус (хост) ==="
  local f4 f6
  f4="$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo '?')"
  f6="$(sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo '?')"
  info "IP forwarding: IPv4=${f4}, IPv6=${f6}"

  if ! docker inspect -f '{{.State.Running}}' "$HS_CONTAINER" >/dev/null 2>&1; then
    warn "Контейнер ${HS_CONTAINER} не найден/не запущен — статус Headscale недоступен."
    return 0
  fi

  local nid; nid="$(find_exit_node_id)"
  if [ -n "$nid" ]; then
    ok "advertise: ✅ узел объявил exit-маршрут (id=${nid})"
  else
    warn "advertise: ❌ ни один узел не объявляет 0.0.0.0/0"
  fi
  if is_exit_approved; then
    ok "approve:   ✅ exit-маршрут аппрувнут в Headscale"
  else
    warn "approve:   ❌ exit-маршрут не аппрувнут"
  fi
  if [ -n "$nid" ] && is_exit_approved; then
    ok "🟢 Exit node готов — выбирайте его на устройстве (Tailscale → Exit Node)."
  fi
}

cmd_on() {
  need_root
  info "→ Объявляю хост exit node'ом..."
  if ! "$TS" set --advertise-exit-node; then
    err "❌ tailscale set --advertise-exit-node не выполнен."
    exit 1
  fi
  ok "✅ advertise включён."

  info "→ Включаю IP forwarding (runtime + persistent)..."
  sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || warn "⚠️ не удалось выставить net.ipv4.ip_forward"
  sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || warn "⚠️ не удалось выставить net.ipv6.conf.all.forwarding"
  if [ ! -f "$SYSCTL_PERSIST" ]; then
    printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' > "$SYSCTL_PERSIST" \
      && ok "✅ forwarding закреплён в ${SYSCTL_PERSIST}" \
      || warn "⚠️ не удалось записать ${SYSCTL_PERSIST} (forwarding активен только до ребута)"
  else
    ok "✅ ${SYSCTL_PERSIST} уже существует."
  fi

  if ! docker inspect -f '{{.State.Running}}' "$HS_CONTAINER" >/dev/null 2>&1; then
    warn "⚠️ Контейнер ${HS_CONTAINER} недоступен — approve пропущен."
    manual_approve_hint
    return 0
  fi

  info "→ Ищу узел и аппрувлю exit-маршрут в Headscale..."
  # Дать headscale секунду «увидеть» свежий advertise.
  sleep 2
  local nid; nid="$(find_exit_node_id)"
  if [ -z "$nid" ]; then
    warn "⚠️ Headscale пока не видит exit-маршрут. Повторите через 5–10 сек или аппрувните вручную."
    manual_approve_hint
    return 0
  fi
  if approve_routes "$nid"; then
    ok "✅ Exit-маршрут аппрувнут (узел id=${nid})."
    echo
    ok "🎉 Exit node готов. На устройстве: Tailscale → Exit Node → выберите этот VPS."
    info "   Проверка с устройства: https://ifconfig.me должен показать IP этого VPS."
  else
    manual_approve_hint
  fi
}

cmd_off() {
  need_root
  info "→ Отключаю advertise exit node на хосте..."
  if "$TS" set --advertise-exit-node=false; then
    ok "✅ Exit node выключен: хост больше не объявляет 0.0.0.0/0."
    info "   Клиенты, выбравшие его, потеряют выход через VPS."
  else
    err "❌ Не удалось отключить advertise."
    exit 1
  fi
}

case "${1:-}" in
  on)     cmd_on ;;
  off)    cmd_off ;;
  status) cmd_status ;;
  *)
    info "Использование: $0 {on|off|status}"
    info ""
    info "  on      — сделать VPS exit node'ом (advertise + forwarding + approve)"
    info "  off     — перестать быть exit node'ом"
    info "  status  — показать состояние"
    exit 1
    ;;
esac
