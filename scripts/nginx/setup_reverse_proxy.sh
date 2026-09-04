#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

DOMAIN=""
UPSTREAM_HOST="127.0.0.1"
UPSTREAM_PORT="8000"
EMAIL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="$2"
      shift 2
      ;;
    --upstream-host)
      UPSTREAM_HOST="$2"
      shift 2
      ;;
    --upstream-port)
      UPSTREAM_PORT="$2"
      shift 2
      ;;
    --email)
      EMAIL="$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

if [[ -z "${DOMAIN}" ]]; then
  echo "Usage: sudo bash $0 --domain api.example.com [--upstream-host 127.0.0.1] [--upstream-port 8000] [--email you@example.com]"
  exit 1
fi

CONF_PATH="/etc/nginx/sites-available/telegramsimple.conf"
WEBROOT="/var/www/certbot"

mkdir -p "${WEBROOT}"

cat > "${CONF_PATH}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root ${WEBROOT};
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    client_max_body_size 20m;

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_pass http://${UPSTREAM_HOST}:${UPSTREAM_PORT};
    }
}
EOF

ln -sf "${CONF_PATH}" /etc/nginx/sites-enabled/telegramsimple.conf

nginx -t
systemctl reload nginx

if [[ -n "${EMAIL}" ]]; then
  certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}"
  systemctl reload nginx
  echo "✅ SSL issued for ${DOMAIN}"
else
  echo "⚠️ No --email provided. Run certbot manually:"
  echo "certbot --nginx -d ${DOMAIN} --non-interactive --agree-tos -m you@example.com"
fi
