#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script supports Debian/Ubuntu (apt-get required)."
  exit 1
fi

apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx

systemctl enable nginx
systemctl start nginx

echo "✅ Nginx + Certbot installed."
