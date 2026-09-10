#!/usr/bin/env bash
# ============================================================================
# vps_setup.sh — bootstrap for the interactive installer (Python + rich).
# Installs rich/prompt_toolkit on a clean VPS and runs scripts/vps_setup.py.
#   bash scripts/vps_setup.sh            # normal mode
#   bash scripts/vps_setup.sh --dry-run  # show the plan without running it
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Installing python3..."
    $SUDO apt-get update -y && $SUDO apt-get install -y python3
fi

# rich + prompt_toolkit: Debian packages first, otherwise pip (PEP 668).
if ! python3 -c "import rich, prompt_toolkit" >/dev/null 2>&1; then
    echo "Installing rich / prompt_toolkit..."
    $SUDO apt-get update -y >/dev/null 2>&1 || true
    $SUDO apt-get install -y python3-rich python3-prompt-toolkit 2>/dev/null \
        || python3 -m pip install --break-system-packages rich prompt_toolkit
fi

# UTF-8 for stdin/stdout: on a minimal Debian with a C locale (or Cyrillic
# input) input() raises UnicodeDecodeError. PEP 540 + C.UTF-8 as fallback.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

exec python3 "$SCRIPT_DIR/vps_setup.py" "$@"
