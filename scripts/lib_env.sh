# ============================================================================
# Shared .env helpers (source from install_*.sh):
#   # shellcheck source=lib_env.sh
#   source "$(dirname "$0")/lib_env.sh"
#
# Expects ENV_FILE=/path/to/.env
# ============================================================================

# Value of KEY from .env: no \r, no surrounding quotes or spaces.
env_get() {
  local key="$1" line val
  [[ -f "${ENV_FILE}" ]] || { echo ""; return 0; }
  line="$(grep -E "^${key}=" "${ENV_FILE}" | head -1 || true)"
  [[ -n "${line}" ]] || { echo ""; return 0; }
  val="${line#*=}"
  val="${val%$'\r'}"
  val="$(printf '%s' "${val}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  # strip one pair of quotes
  if [[ "${val}" =~ ^\"(.*)\"$ ]]; then
    val="${BASH_REMATCH[1]}"
  elif [[ "${val}" =~ ^\'(.*)\'$ ]]; then
    val="${BASH_REMATCH[1]}"
  fi
  printf '%s' "${val}"
}

# Write KEY=VALUE (creates the line if missing).
env_set() {
  local key="$1" value="$2" esc
  touch "${ENV_FILE}"
  esc="$(printf '%s' "${value}" | sed -e 's/[\/&]/\\&/g')"
  if grep -qE "^${key}=" "${ENV_FILE}"; then
    # GNU sed (Linux) vs BSD sed (macOS)
    if sed --version >/dev/null 2>&1; then
      sed -i "s/^${key}=.*/${key}=${esc}/" "${ENV_FILE}"
    else
      sed -i '' "s/^${key}=.*/${key}=${esc}/" "${ENV_FILE}"
    fi
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
  fi
}

# Placeholder / empty / typical example.env stubs
env_is_placeholder() {
  local key="$1" val
  val="$(env_get "${key}")"
  [[ -z "${val}" ]] && return 0
  case "${val}" in
    your_*|*_here*|YOUR_*|123456789) return 0 ;;
    sk-ant-api03-your-key-here|sk-proj-your-key-here) return 0 ;;
    your_very_long_random_secret_key_here_64_chars_minimum) return 0 ;;
    another_random_secret_for_hmac_signing_also_64_chars) return 0 ;;
    optional_separate_encryption_key_otherwise_api_secret_is_used) return 0 ;;
    http://YOUR_SERVER_IP:8000/ai_query) return 0 ;;
  esac
  # any YOUR_SERVER_IP inside the value
  [[ "${val}" == *YOUR_SERVER_IP* ]] && return 0
  return 1
}

env_ensure_file() {
  local example
  example="$(dirname "${ENV_FILE}")/example.env"
  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${example}" ]]; then
      cp "${example}" "${ENV_FILE}"
      echo "Created ${ENV_FILE} from example.env"
    else
      touch "${ENV_FILE}"
      echo "Created empty ${ENV_FILE}"
    fi
  fi
}

_rand_hex32() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  else
    openssl rand -hex 32
  fi
}

_detect_public_ip() {
  local ip=""
  ip="$(curl -4 -fsS --max-time 3 https://api.ipify.org 2>/dev/null || true)"
  [[ -n "${ip}" ]] || ip="$(curl -4 -fsS --max-time 3 https://ifconfig.me 2>/dev/null || true)"
  [[ -n "${ip}" ]] || ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf '%s' "${ip}"
}

# Interactive setup of required fields with hints.
# If a value is already real — do NOT re-ask (just report).
# Env vars BOT_TOKEN / ADMIN_USER_IDS take priority when .env is empty.
configure_essential_env() {
  local token ids secret hmac pub api_url current

  env_ensure_file

  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  Configure .env (required fields for the bot / API)"
  echo "  File: ${ENV_FILE}"
  echo "════════════════════════════════════════════════════════════"
  echo "  Hints:"
  echo "  • BOT_TOKEN — @BotFather → /newbot → token like 123456:AA..."
  echo "  • ADMIN_USER_IDS — your numeric Telegram id (@userinfobot"
  echo "    or send the bot /info after the first start)"
  echo "  • API_SECRET_KEY / HMAC_SECRET — long secrets for the API;"
  echo "    if empty, the script generates them"
  echo "  • Fields that are already filled are NOT re-asked"
  echo "════════════════════════════════════════════════════════════"

  # --- BOT_TOKEN ---
  if ! env_is_placeholder BOT_TOKEN; then
    current="$(env_get BOT_TOKEN)"
    echo "✅ BOT_TOKEN already set (${current:0:6}…${current: -4}) — skipping."
  else
    token="${BOT_TOKEN:-}"
    if [[ -z "${token}" && -t 0 ]]; then
      echo ""
      echo "BOT_TOKEN (from @BotFather). Enter — skip and set later in .env"
      read -r -p "BOT_TOKEN: " token || true
    fi
    if [[ -n "${token}" ]]; then
      if ! [[ "${token}" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
        echo "⚠ Token does not look like BotFather format (<digits>:<string>). Writing as-is."
      fi
      env_set BOT_TOKEN "${token}"
      echo "✅ BOT_TOKEN written."
    else
      echo "⚠ BOT_TOKEN is not set — the bot will not start until you fill it in ${ENV_FILE}"
    fi
  fi

  # --- ADMIN_USER_IDS ---
  if ! env_is_placeholder ADMIN_USER_IDS; then
    current="$(env_get ADMIN_USER_IDS)"
    echo "✅ ADMIN_USER_IDS already set (${current}) — skipping."
  else
    ids="${ADMIN_USER_IDS:-}"
    if [[ -z "${ids}" && -t 0 ]]; then
      echo ""
      echo "ADMIN_USER_IDS — your Telegram id (number)."
      echo "Find it: @userinfobot or @getidsbot. Several — comma-separated."
      echo "Enter — skip and set later in .env"
      read -r -p "ADMIN_USER_IDS: " ids || true
    fi
    if [[ -n "${ids}" ]]; then
      ids="$(printf '%s' "${ids}" | tr -d '[:space:]')"
      if ! [[ "${ids}" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        echo "⚠ Expected comma-separated numbers. Writing as-is."
      fi
      env_set ADMIN_USER_IDS "${ids}"
      echo "✅ ADMIN_USER_IDS written (${ids})."
    else
      echo "⚠ ADMIN_USER_IDS is not set — without it the bot will not treat you as admin."
    fi
  fi

  # --- API_SECRET_KEY ---
  if env_is_placeholder API_SECRET_KEY; then
    secret="$(_rand_hex32)"
    env_set API_SECRET_KEY "${secret}"
    echo "✅ API_SECRET_KEY generated and written."
  else
    echo "✅ API_SECRET_KEY already set — skipping."
  fi

  # --- HMAC_SECRET (next to API; in example.env it may sit lower) ---
  if env_is_placeholder HMAC_SECRET; then
    hmac="$(_rand_hex32)"
    env_set HMAC_SECRET "${hmac}"
    echo "✅ HMAC_SECRET generated and written."
  else
    echo "✅ HMAC_SECRET already set — skipping."
  fi

  # --- API_URL / public host ---
  if env_is_placeholder API_URL || env_is_placeholder TELEGRAMHELPER_PUBLIC_HOST; then
    pub="$(env_get TELEGRAMHELPER_PUBLIC_HOST)"
    if env_is_placeholder TELEGRAMHELPER_PUBLIC_HOST; then
      pub=""
    fi
    if [[ -z "${pub}" ]]; then
      pub="$(_detect_public_ip)"
    fi
    if [[ -t 0 ]]; then
      echo ""
      echo "Public IP/domain of this VPS (for API_URL and bot hints)."
      read -r -p "PUBLIC_HOST [${pub:-set later by hand}]: " current || true
      [[ -n "${current}" ]] && pub="${current}"
    fi
    if [[ -n "${pub}" ]]; then
      env_set TELEGRAMHELPER_PUBLIC_HOST "${pub}"
      api_url="$(env_get API_URL)"
      if env_is_placeholder API_URL || [[ -z "${api_url}" ]]; then
        env_set API_URL "http://${pub}:8000/ai_query"
        echo "✅ API_URL=http://${pub}:8000/ai_query"
      fi
      echo "✅ TELEGRAMHELPER_PUBLIC_HOST=${pub}"
    else
      echo "⚠ Public host is not set — fix API_URL / TELEGRAMHELPER_PUBLIC_HOST in .env"
    fi
  else
    echo "✅ API_URL / TELEGRAMHELPER_PUBLIC_HOST already set — skipping."
  fi

  echo ""
  echo "Critical fields in ${ENV_FILE}:"
  if env_is_placeholder BOT_TOKEN; then
    echo "  BOT_TOKEN         — ⚠ still a placeholder"
  else
    echo "  BOT_TOKEN         — ok"
  fi
  if env_is_placeholder ADMIN_USER_IDS; then
    echo "  ADMIN_USER_IDS    — ⚠ still a placeholder"
  else
    echo "  ADMIN_USER_IDS    — ok ($(env_get ADMIN_USER_IDS))"
  fi
  if env_is_placeholder API_SECRET_KEY; then
    echo "  API_SECRET_KEY    — ⚠"
  else
    echo "  API_SECRET_KEY    — ok"
  fi
  if env_is_placeholder HMAC_SECRET; then
    echo "  HMAC_SECRET       — ⚠"
  else
    echo "  HMAC_SECRET       — ok"
  fi
  echo ""
}
