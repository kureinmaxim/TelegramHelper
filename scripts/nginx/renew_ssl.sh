#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

certbot renew --quiet
systemctl reload nginx

echo "✅ Certificates renewed (if needed)."
