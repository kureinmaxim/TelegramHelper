#!/usr/bin/env bash
#
# install_mieru.sh — install the Mieru server side (mita) on a VPS.
# Installer for mita (Mieru).
#
# Default behaviour:
#   - detect architecture via dpkg --print-architecture
#   - resolve the latest release via GitHub Releases API (or pin --version)
#   - install the .deb via dpkg -i
#   - check systemctl status mita
#   - enable NTP (timedatectl set-ntp true)
#   - does NOT open the firewall automatically — only if --port is passed
#   - prints next steps for /mieru_set_* and /mieru_apply
#
set -euo pipefail

VERSION=""           # empty => latest via GitHub API
PORT=""              # if set, open in ufw
PROTOCOL="tcp"       # tcp|udp for firewall
SKIP_NTP="0"
REPO="enfein/mieru"

usage() {
  cat <<EOF
Usage: sudo bash scripts/install_mieru.sh [options]

Options:
  --version VER        Pinned mita version (e.g. 3.32.0). Default — latest.
  --port PORT          Open this port in ufw after install.
  --protocol PROTO     tcp|udp for --port (default: tcp).
  --no-ntp             Do not enable timedatectl set-ntp true.
  -h | --help          Show help.

After install set parameters via the bot:
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
  echo "❌ Run as root (sudo bash scripts/install_mieru.sh)" >&2
  exit 1
fi

# --- 1. Detect architecture -----------------------------------------------
if ! command -v dpkg >/dev/null 2>&1; then
  echo "❌ dpkg not found. install_mieru.sh supports Debian/Ubuntu (.deb)." >&2
  exit 1
fi

ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  amd64|arm64) ;;
  *)
    echo "❌ Unsupported architecture: $ARCH (need amd64 or arm64)" >&2
    exit 1
    ;;
esac

export DEBIAN_FRONTEND=noninteractive

# Base utilities for download and checks.
apt-get update -y >/dev/null
apt-get install -y curl ca-certificates jq >/dev/null 2>&1 || \
  apt-get install -y curl ca-certificates >/dev/null

# --- 2. Resolve version ------------------------------------------------------
if [[ -z "$VERSION" ]]; then
  echo "→ Resolving the latest mita version via GitHub API…"
  if command -v jq >/dev/null 2>&1; then
    VERSION="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
      | jq -r '.tag_name' | sed 's/^v//')"
  else
    VERSION="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
      | grep -m1 '"tag_name"' | sed -E 's/.*"v?([^"]+)".*/\1/')"
  fi
  if [[ -z "$VERSION" ]]; then
    echo "❌ Failed to resolve mita version from GitHub API." >&2
    echo "   Pass it by hand: --version 3.32.0" >&2
    exit 1
  fi
fi

DEB_NAME="mita_${VERSION}_${ARCH}.deb"
DEB_URL="https://github.com/${REPO}/releases/download/v${VERSION}/${DEB_NAME}"

echo "→ Version: ${VERSION}, architecture: ${ARCH}"
echo "→ URL: ${DEB_URL}"

# --- 3. Download and install ------------------------------------------------
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

DEB_PATH="${TMPDIR}/${DEB_NAME}"
if ! curl -fSsLo "$DEB_PATH" "$DEB_URL"; then
  echo "❌ Failed to download ${DEB_URL}" >&2
  exit 1
fi

if ! dpkg -i "$DEB_PATH"; then
  echo "→ dpkg returned an error, trying apt-get install -f…"
  apt-get install -f -y
  dpkg -i "$DEB_PATH"
fi

# --- 4. systemd check -----------------------------------------------------
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

# --- 6. Firewall (only if --port is set) ---------------------------------
if [[ -n "$PORT" ]]; then
  PROTOCOL_LC="$(echo "$PROTOCOL" | tr '[:upper:]' '[:lower:]')"
  if [[ "$PROTOCOL_LC" != "tcp" && "$PROTOCOL_LC" != "udp" ]]; then
    echo "⚠️ --protocol must be tcp or udp; skipping firewall." >&2
  elif command -v ufw >/dev/null 2>&1; then
    ufw allow "${PORT}/${PROTOCOL_LC}" || true
    echo "→ ufw: opened ${PORT}/${PROTOCOL_LC}"
  else
    echo "⚠️ ufw not found — open ${PORT}/${PROTOCOL_LC} by hand."
  fi
fi

# --- 7. Final summary -------------------------------------------------------
cat <<EOF

✅ mita ${VERSION} (${ARCH}) installed.
EOF

if [[ "$SYSTEMD_OK" == "1" ]]; then
  echo "→ systemctl status mita: OK (status may be IDLE until /mieru_apply)"
else
  echo "⚠️ systemctl status mita returned an error — check logs:"
  echo "   journalctl -u mita -n 100 --no-pager"
fi

# Hint about the mita group: lets you use the `mita` CLI without sudo.
if [[ -n "${SUDO_USER:-}" ]] && id "$SUDO_USER" >/dev/null 2>&1; then
  if ! id -nG "$SUDO_USER" | tr ' ' '\n' | grep -qx mita; then
    usermod -a -G mita "$SUDO_USER" || true
    echo "→ ${SUDO_USER} added to the mita group — re-login over SSH."
  fi
fi

cat <<EOF

Next steps:
  /mieru_set_server <ip_or_domain>
  /mieru_set_port <port> [tcp|udp]      # e.g. 29999 tcp
  /mieru_add_client <name>
  /mieru_apply
  /mieru_start
  /mieru_status

If you set the port by hand, do not forget the firewall:
  ufw allow <port>/tcp   # or /udp
EOF
