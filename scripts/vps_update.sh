#!/usr/bin/env bash
# ============================================================================
# vps_update.sh — bootstrap для интерактивного апдейтера (Python + rich).
# Доставляет rich/prompt_toolkit на VPS и запускает scripts/vps_update.py.
#   bash scripts/vps_update.sh             # обычный режим
#   bash scripts/vps_update.sh --dry-run   # показать действия без выполнения
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Ставлю python3..."
    $SUDO apt-get update -y && $SUDO apt-get install -y python3
fi

# rich + prompt_toolkit: сперва системные пакеты Debian, иначе pip (PEP668).
if ! python3 -c "import rich, prompt_toolkit" >/dev/null 2>&1; then
    echo "Доставляю rich / prompt_toolkit..."
    $SUDO apt-get update -y >/dev/null 2>&1 || true
    $SUDO apt-get install -y python3-rich python3-prompt-toolkit 2>/dev/null \
        || python3 -m pip install --break-system-packages rich prompt_toolkit
fi

# UTF-8 для stdin/stdout: на минимальном Debian с C-локалью (или при вводе
# кириллицы) input() падает UnicodeDecodeError. PEP 540 + C.UTF-8 как fallback.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

exec python3 "$SCRIPT_DIR/vps_update.py" "$@"
