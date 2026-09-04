#!/usr/bin/env bash
#
# install_mieru.sh — установка серверной части Mieru (mita) на VPS.
# Installer for mita (Mieru).
#
# Поведение по умолчанию:
#   - определяет архитектуру через dpkg --print-architecture
#   - резолвит последний релиз через GitHub Releases API (или pin --version)
#   - ставит .deb через dpkg -i
#   - проверяет systemctl status mita
#   - включает NTP (timedatectl set-ntp true)
#   - НЕ открывает firewall автоматически — только если передан --port
#   - печатает следующие шаги для /mieru_set_* и /mieru_apply
#
set -euo pipefail

VERSION=""           # пусто => latest через GitHub API
PORT=""              # если задан, открыть в ufw
PROTOCOL="tcp"       # tcp|udp для firewall
SKIP_NTP="0"
REPO="enfein/mieru"

usage() {
  cat <<EOF
Usage: sudo bash scripts/install_mieru.sh [options]

Options:
  --version VER        Pinned mita version (например 3.32.0). По умолчанию — latest.
  --port PORT          Открыть указанный порт в ufw после установки.
  --protocol PROTO     tcp|udp для --port (default: tcp).
  --no-ntp             Не включать timedatectl set-ntp true.
  -h | --help          Показать справку.

После установки задайте параметры через бот:
  /mieru_set_server <ip>
  /mieru_set_port <port> [tcp|udp]
  /mieru_add_client <name>
  /mieru_apply
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)   VERSION="$2"; shift 2 ;;
    --port)      PORT="$2"; shift 2 ;;
    --protocol)  PROTOCOL="$2"; shift 2 ;;
    --no-ntp)    SKIP_NTP="1"; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "❌ Запустите как root (sudo bash scripts/install_mieru.sh)" >&2
  exit 1
fi

# --- 1. Определить архитектуру -----------------------------------------------
if ! command -v dpkg >/dev/null 2>&1; then
  echo "❌ dpkg не найден. install_mieru.sh поддерживает Debian/Ubuntu (.deb)." >&2
  exit 1
fi

ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  amd64|arm64) ;;
  *)
    echo "❌ Неподдерживаемая архитектура: $ARCH (нужна amd64 или arm64)" >&2
    exit 1
    ;;
esac

export DEBIAN_FRONTEND=noninteractive

# Базовые утилиты для скачивания и проверки.
apt-get update -y >/dev/null
apt-get install -y curl ca-certificates jq >/dev/null 2>&1 || \
  apt-get install -y curl ca-certificates >/dev/null

# --- 2. Резолвим версию ------------------------------------------------------
if [[ -z "$VERSION" ]]; then
  echo "→ Определяю последнюю версию mita через GitHub API…"
  if command -v jq >/dev/null 2>&1; then
    VERSION="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
      | jq -r '.tag_name' | sed 's/^v//')"
  else
    VERSION="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
      | grep -m1 '"tag_name"' | sed -E 's/.*"v?([^"]+)".*/\1/')"
  fi
  if [[ -z "$VERSION" ]]; then
    echo "❌ Не удалось определить версию mita из GitHub API." >&2
    echo "   Передайте вручную: --version 3.32.0" >&2
    exit 1
  fi
fi

DEB_NAME="mita_${VERSION}_${ARCH}.deb"
DEB_URL="https://github.com/${REPO}/releases/download/v${VERSION}/${DEB_NAME}"

echo "→ Версия: ${VERSION}, архитектура: ${ARCH}"
echo "→ URL: ${DEB_URL}"

# --- 3. Скачать и установить ------------------------------------------------
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

DEB_PATH="${TMPDIR}/${DEB_NAME}"
if ! curl -fSsLo "$DEB_PATH" "$DEB_URL"; then
  echo "❌ Не удалось скачать ${DEB_URL}" >&2
  exit 1
fi

if ! dpkg -i "$DEB_PATH"; then
  echo "→ dpkg вернул ошибку, пытаюсь apt-get install -f…"
  apt-get install -f -y
  dpkg -i "$DEB_PATH"
fi

# --- 4. Проверка systemd -----------------------------------------------------
systemctl daemon-reload || true
SYSTEMD_OK="0"
if systemctl status mita --no-pager >/dev/null 2>&1; then
  SYSTEMD_OK="1"
fi

# --- 5. NTP ------------------------------------------------------------------
if [[ "$SKIP_NTP" != "1" ]]; then
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-ntp true || true
  fi
fi

# --- 6. Firewall (только если задан --port) ---------------------------------
if [[ -n "$PORT" ]]; then
  PROTOCOL_LC="$(echo "$PROTOCOL" | tr '[:upper:]' '[:lower:]')"
  if [[ "$PROTOCOL_LC" != "tcp" && "$PROTOCOL_LC" != "udp" ]]; then
    echo "⚠️ --protocol должен быть tcp или udp; пропускаю firewall." >&2
  elif command -v ufw >/dev/null 2>&1; then
    ufw allow "${PORT}/${PROTOCOL_LC}" || true
    echo "→ ufw: открыт ${PORT}/${PROTOCOL_LC}"
  else
    echo "⚠️ ufw не найден — откройте ${PORT}/${PROTOCOL_LC} вручную."
  fi
fi

# --- 7. Final summary -------------------------------------------------------
cat <<EOF

✅ mita ${VERSION} (${ARCH}) установлен.
EOF

if [[ "$SYSTEMD_OK" == "1" ]]; then
  echo "→ systemctl status mita: OK (статус может быть IDLE до /mieru_apply)"
else
  echo "⚠️ systemctl status mita вернул ошибку — проверьте логи:"
  echo "   journalctl -u mita -n 100 --no-pager"
fi

# Подсказка про группу mita: позволяет работать с `mita` CLI без sudo.
if [[ -n "${SUDO_USER:-}" ]] && id "$SUDO_USER" >/dev/null 2>&1; then
  if ! id -nG "$SUDO_USER" | tr ' ' '\n' | grep -qx mita; then
    usermod -a -G mita "$SUDO_USER" || true
    echo "→ ${SUDO_USER} добавлен в группу mita — перелогиньтесь по SSH."
  fi
fi

cat <<EOF

Следующие шаги:
  /mieru_set_server <ip_or_domain>
  /mieru_set_port <port> [tcp|udp]      # например: 29999 tcp
  /mieru_add_client <name>
  /mieru_apply
  /mieru_start
  /mieru_status

Если порт задаётся вручную, не забудьте firewall:
  ufw allow <port>/tcp   # или /udp
EOF
