# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## Project Overview

**TelegramHelper** — a Python Telegram bot + REST API for managing AI queries
(Anthropic/OpenAI), API keys, AES-256-GCM encryption, VLESS-Reality, Hysteria2,
and MTProto proxy VPN protocols. Code identifiers, comments, and user-facing
text are in English. It is a cleaner, publicly shareable evolution of the
private ancestor project `TelegramOnly`.

## Commands

### Run Locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp example.env .env   # fill in BOT_TOKEN, API keys, etc.
python3 main.py           # Bot + API
python3 main.py --api-only  # Only REST API on :8000
python3 main.py --bot-only  # Only Telegram bot
```

### Docker (Production)

```bash
docker compose up -d --build
docker compose down
docker logs telegram-helper-lite --tail 50
```

### Tests

Tests are manual integration scripts (not pytest). They require a running API
server on `localhost:8000`:

```bash
python3 tests/test_api.py
python3 tests/test_obfuscation.py
python3 tests/test_compatibility.py
```

### Version Management

```bash
python3 scripts/bump_version.py --bump minor   # patch/minor/major
python3 scripts/show_version.py
```

## Architecture

### Dual-Service Entry Point

`main.py` starts both services in a single process using asyncio:

1. **Telegram Bot** — long-polling via `python-telegram-bot`
2. **REST API** — FastAPI + uvicorn on port 8000

The bot runs in non-blocking mode, then uvicorn's `server.serve()` takes the
event loop.

### Core Module Responsibilities

| Module | Role |
|---|---|
| `main.py` | Entry point, loads `.env`, starts bot + API |
| `bot.py` | `TelegramBotLite` class — builds `Application`, registers all command handlers |
| `telegram_bot_menu.py` | `setMyCommands` — base menu for all users and up to 100 commands per `ADMIN_USER_IDS` entry |
| `handlers.py` | `BotHandlersLite` — all Telegram command handler implementations (admin checks via `config.is_admin()`) |
| `api.py` | FastAPI app with endpoints: `/ai_query`, `/ai_query/secure`, `/echo`, `/admin_command`, etc. |
| `config.py` | `Config` class — reads all settings from env vars |
| `security.py` | Multi-layer request verification: API key, app_id allowlist, HMAC signature, timestamp, nonce, rate limiting |
| `encryption.py` | `SecureMessenger` — AES-256-GCM encrypt/decrypt, packet format: `[Nonce 12B][Ciphertext+Tag]` |
| `app_keys.py` | Per-app API keys and encryption keys, stored in `app_keys.json` |
| `storage.py` | User preferences (city, greeting, special users) in `users.json`, thread-safe with atomic writes |
| `utils.py` | AI provider wrappers (`get_anthropic_completion`, `get_openai_completion`), prompt templates, Markdown escaping |
| `vless_manager.py` | VLESS-Reality config management, key generation, Xray config export, Nginx SNI routing functions |
| `hysteria2_manager.py` | Hysteria2 QUIC/UDP proxy config, client management, cert generation |
| `mtproto_manager.py` | MTProto proxy (official Telegram C implementation), fake-TLS secrets, systemd unit generation |
| `headscale_manager.py` | Headscale (self-hosted Tailscale) management via Docker exec: users, pre-auth keys, nodes |
| `admin_cli.py` | `AdminCLI` — executes bot commands without Telegram context (for desktop client integration) |

### Request Flow (API)

Plain request:
```
Client -> /ai_query (X-API-KEY, X-APP-ID headers)
       -> full_security_check
       -> process_ai_request
       -> AI provider
       -> JSON response
```

Encrypted request:
```
Client -> /ai_query {data: base64} or /ai_query/secure
       -> SecureMessenger.decrypt
       -> credentials verified from payload
       -> process_ai_request
       -> encrypt response
       -> {data: base64}
```

The `/ai_query` endpoint auto-detects mode: if the `data` field is present it
routes to encrypted processing; if `prompt` is present it uses plain mode.

### Persistent Data Files (JSON, Mounted as Docker Volumes)

- `users.json` — user preferences and special user list
- `app_keys.json` — per-app API and encryption keys
- `vless_config.json` — VLESS-Reality configuration (includes nginx_fallback settings)
- `hysteria2_config.json` — Hysteria2 configuration
- `mtproto_config.json` — MTProto proxy configuration
- `headscale_config.json` — Headscale configuration (container_name, server_url, default_user)

### Docker Compose Variants

- `compose.yaml` — main setup (`telegram-helper-lite` container)
- `compose.headscale.yaml` — Headscale overlay:
  `docker compose -f compose.yaml -f compose.headscale.yaml up -d`

### Dockhand

`dockhand/` is a separate Streamlit app (its own Dockerfile) for Docker
diagnostics, exposed on port 8501 (localhost only).

## Key Patterns

- **Admin guard**: Bot command handlers check `self.config.is_admin(user_id)` before
  executing admin-only commands. Admin IDs come from the `ADMIN_USER_IDS` env var.
- **Graceful degradation**: AI providers, security module, and encryption module are
  imported with `try/except`. The API falls back to basic auth if `security.py`
  fails to import, and falls back between providers (anthropic ↔ openai).
- **Thread safety**: `storage.py`, `app_keys.py`, `vless_manager.py`,
  `hysteria2_manager.py`, `mtproto_manager.py`, and `headscale_manager.py` all use
  `threading.Lock` for concurrent access.
- **Atomic writes**: `storage.py` uses `tempfile.mkstemp` + `os.replace` to prevent
  data corruption.
- **Version**: Defined in `pyproject.toml` (`project.version`) and duplicated in
  `api.py`. Keep both in sync when bumping.

## VPS Deployment Notes

**Before touching anything related to the production deployment, read
[DOCKER.md](DOCKER.md).** It documents:

- A persistent PMTU black hole at the VPS upstream and the `tcp_mtu_probing=2`
  workaround applied both at the host level and inside the bot container via
  `compose.yaml` `sysctls:`.
- Hard-learned anti-patterns: do **not** run `docker compose down` to restart
  the bot (use `up -d --force-recreate <service>` instead), do **not** lower the
  Docker bridge MTU below 1500 (breaks outbound TCP), do **not** chain rapid
  recreates under upstream stress (locks SSH via swap thrashing on a low-RAM VPS).
- Recovery steps when SSH itself becomes unreachable.

The compose file declares `sysctls` for `telegram-helper` because Docker
containers do not fully inherit host sysctls — both layers are required.

## Safety Rules

### Security & Secrets

- If you detect a security vulnerability, immediately add a `WARNING` comment and
  propose a safer alternative.
- **Never implement insecure patterns**, even if explicitly asked.
- **Never commit secrets**. Treat files with API keys, tokens, or passwords as
  **read-only** — do not modify, move, or expose them.
- Never print secrets to logs, outputs, or error messages.
- **Never disable or bypass security checks**, linters, or security tooling without
  explicit approval.

### Change Discipline

- Before making changes, **read the relevant files first** and understand the
  existing implementation.
- Follow the **existing architecture, style, and patterns**. Do not introduce new
  frameworks or libraries without explicit request.
- Prefer **small, incremental changes** over large rewrites. Avoid modifying
  unrelated files.
- Do not add new dependencies unless absolutely necessary; prefer libraries already
  in the project.
- Do not perform large refactors without explicit request. If a major change seems
  necessary, propose a plan first and wait for confirmation.
- Never delete or overwrite files without creating a backup or receiving explicit
  confirmation.

### Tests & API Stability

- Before refactoring, check if tests exist. If present: run before changes, run
  again after each modification.
- Do not change or remove tests unless explicitly requested.
- **Never silently change public APIs** or introduce breaking changes without
  warning. If a breaking change is needed, explain the impact and suggest a
  migration path.

### Documentation

- When introducing non-trivial logic, add short comments explaining the reasoning.
- Update relevant documentation if behaviour or interfaces change.
