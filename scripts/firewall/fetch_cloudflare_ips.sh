#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_FILE="${OUT_DIR}/cloudflare_ips.txt"

TMP_V4="$(mktemp)"
TMP_V6="$(mktemp)"

curl -fsSL https://www.cloudflare.com/ips-v4 -o "${TMP_V4}"
curl -fsSL https://www.cloudflare.com/ips-v6 -o "${TMP_V6}"

cat "${TMP_V4}" "${TMP_V6}" > "${OUT_FILE}"

rm -f "${TMP_V4}" "${TMP_V6}"

echo "✅ Cloudflare IP list saved to ${OUT_FILE}"
