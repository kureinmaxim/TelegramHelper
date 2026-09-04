#!/usr/bin/env bash
set -euo pipefail

APPLY=false
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IP_FILE="${SCRIPT_DIR}/cloudflare_ips.txt"

if [[ ! -f "${IP_FILE}" ]]; then
  echo "Missing ${IP_FILE}. Run: bash ${SCRIPT_DIR}/fetch_cloudflare_ips.sh"
  exit 1
fi

if [[ "${APPLY}" == true && "${EUID}" -ne 0 ]]; then
  echo "Run as root to apply rules: sudo bash $0 --apply"
  exit 1
fi

if [[ "${APPLY}" == false ]]; then
  echo "Dry-run. Showing UFW commands:"
fi

while IFS= read -r ip; do
  [[ -z "${ip}" ]] && continue
  cmd=(ufw allow from "${ip}" to any port 443 proto tcp)
  if [[ "${APPLY}" == true ]]; then
    "${cmd[@]}"
  else
    echo "${cmd[*]}"
  fi
done < "${IP_FILE}"

if [[ "${APPLY}" == true ]]; then
  ufw reload
  echo "✅ UFW rules applied and reloaded."
fi
