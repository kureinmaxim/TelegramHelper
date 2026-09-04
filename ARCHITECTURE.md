# TelegramHelper Architecture

`TelegramHelper` is a server-side project for managing a Telegram bot, a REST
API, and VPN/proxy transports on a VPS. It is designed as a cleaner, publicly
shareable evolution of the private ancestor project `TelegramOnly`: the
predecessor can remain stable while TelegramHelper develops new profile
provisioning, diagnostics, and desktop-client integration scenarios.

The core idea is simple: the operator manages the server through a Telegram bot,
and users receive ready-to-use profiles, QR codes, or email copies for
connecting.

## Project Goals

- Run a Telegram bot, REST API, and transport managers on a single VPS.
- Manage server services: `xray`, `hysteria-server`, `caddy-naive`,
  `mtproto-proxy`, `mita` (Mieru), and optionally `nginx`, `headscale`,
  `dockhand`.
- Deliver user profiles through a unified bot-managed flow: `/special_add`,
  `/provision`, `/profiles`, `/my_profile`, `/email_profile`.
- Support export to desktop-client formats: `Clash Meta`, `sing-box`.
- Preserve compatibility with legacy JSON configs and commands while not
  building new scenarios on top of legacy flows.
- Stay practical for VPS deployments: JSON files, Docker Compose, systemd,
  SSH, and clear diagnostic commands.

## High-Level Diagram

```text
Operator / User
  -> Telegram Bot
  -> handlers.py
  -> transport managers / storage / email / security
  -> JSON configs + host systemd services
  -> xray / hysteria-server / caddy-naive / mtproto-proxy / mita

Desktop client
  -> profile import / QR / URI / email
  -> local sing-box / naive / Xray
  -> VPS transport service
```

The project has several layers:

1. **Control plane** — Telegram commands, inline cards, callback menus, and
   REST admin endpoints.
2. **Application API** — FastAPI endpoints for AI queries, encrypted requests,
   healthcheck, admin commands, and diagnostics.
3. **Security layer** — API keys, per-app keys, HMAC, nonce/timestamp,
   AES-256-GCM, rate limiting.
4. **Transport management** — managers for VLESS-Reality, Hysteria2, NaiveProxy,
   MTProto, Mieru (`mita`), and stubs for TUIC/AnyTLS/XHTTP.
5. **Export layer** — profile assembly for `Clash Meta`, `sing-box`, QR, and URI.
6. **Persistence layer** — JSON files, `.env`, host config paths, and Docker
   Compose bind mounts.

## Launch Modes

`main.py` starts the project in one of the following modes:

| Mode | What starts | When to use |
|---|---|---|
| default | Telegram Bot + FastAPI | normal production mode |
| `--api-only` | FastAPI only | headless API without Telegram polling |
| `--bot-only` | Telegram Bot only | when API is deployed separately or temporarily not needed |

`main.py` loads `.env`, configures logging, initialises config, and starts the
bot and/or `uvicorn`.

## Core Modules

| File | Role |
|---|---|
| `main.py` | Entry point, launch modes, logging, bot/API startup |
| `bot.py` | Registers Telegram command and conversation handlers |
| `handlers.py` | Main business logic for commands, callbacks, user cards, profile delivery |
| `api.py` | FastAPI application and HTTP endpoints |
| `config.py` | Reads settings from `.env` |
| `security.py` | API key checks, HMAC, nonce/timestamp, rate limiting |
| `encryption.py` | `SecureMessenger`, AES-256-GCM compatible format |
| `app_keys.py` | Per-app API and encryption keys stored in `app_keys.json` |
| `storage.py` | Users, roles, profiles, message tracking in `users.json` |
| `email_manager.py` | Profile delivery via SMTP or Gmail API |
| `live_status.py` | Live diagnostics for processes, ports, and Docker containers |
| `mieru_manager.py` | Mieru (`mita`) config, per-user clients, `mierus://` URI, apply/start/stop/logs |
| `provision_manager.py` | Canonical names `<Prefix>_ID<first2>_<last2>` and unified provision/profiles/clean_user flow for all enabled protocols |

## Telegram Bot

The Telegram bot is the primary operator interface. It is registered in
`bot.py` and commands are executed in `handlers.py`.

Main command groups:

| Group | Examples | Role |
|---|---|---|
| Basic | `/start`, `/help`, `/ver`, `/diag`, `/dockhand` | Server status and help |
| Users | `/list_users`, `/special_add`, `/special_remove`, `/user`, `/profiles`, `/my_profile` | Roles, user cards, and profile delivery |
| Security / API | `/api`, `/gen_api_key`, `/encryption_key`, `/gen_encryption_key` | Keys and API access |
| VLESS/Xray | `/vless_status`, `/vless_list_clients`, `/xray_apply`, `/vless_sync`, `/vless_export` | Reality transport, clients, and Xray service |
| Hysteria2 | `/hy2_status`, `/hy2_apply`, `/hy2_export`, `/hy2_set_quic_safe` | UDP/QUIC transport |
| NaiveProxy | `/naive_status`, `/naive_apply`, `/naive_export` | Caddy forwardproxy/naive transport |
| MTProto | `/mt_status`, `/mt_start`, `/mt_export` | Telegram-native proxy |
| Mieru | `/mieru_status`, `/mieru_apply`, `/mieru_export`, `/mieru_set_dpi` | TCP/UDP transport without TLS, per-user clients |
| 3x-ui | `/xui_setup`, `/xui_status`, `/xui_list` | Opt-in integration with an external 3x-ui panel |
| Export | `/tgcapsule_export`, `/email_profile` | Ready profiles and fallback email delivery |

### Unified User Delivery Flow

The recommended profile delivery sequence:

```bash
/special_add 123456789
/provision 123456789
/profiles 123456789
/email_profile 123456789
```

What happens:

1. `/special_add <id>` grants the user self-service profile access.
2. `/provision <id>` creates profiles in all available managers: VLESS,
   Hysteria2, MTProto, and other enabled protocols.
3. `/profiles <id>` shows the user card with QR/URI/rotate/delete buttons.
4. `/my_profile` lets the user retrieve their own profiles if they are `special`
   or admin.
5. `/email_profile <id>` sends profiles by email and deletes tracked QR/URL
   messages from the Telegram chat.

Legacy commands such as `/hy2_add_client <name>` and `/vless_add_client <name>`
remain for compatibility, but new scenarios should use `/provision` and
`/profiles`.

## REST API

`api.py` runs the FastAPI application. The API is used for integrations,
desktop clients, and external admin commands.

Main endpoint groups:

- healthcheck and service information;
- plain AI requests;
- encrypted AI requests;
- admin command execution;
- config/status endpoints;
- transport-specific helper endpoints;
- compatibility with legacy client integrations.

The API does not replace the Telegram bot. In production the bot remains the
primary operator UI, while the API serves as a programmatic interface.

## Security Layer

Security is distributed across several files:

| File | Role |
|---|---|
| `security.py` | API key, app allowlist, HMAC, nonce, timestamp, rate limiting |
| `encryption.py` | AES-256-GCM encrypt/decrypt via `SecureMessenger` |
| `app_keys.py` | Application key and encryption key storage |
| `xui_manager.py` | Stores the 3x-ui password in encrypted form only |
| `email_manager.py` | Does not log SMTP/Gmail secrets; provides safe error messages |

Key rules:

- secrets must not appear in logs or git;
- server private keys are not exported in client profiles;
- admin/password values are not displayed in full in Telegram;
- the 3x-ui password is deleted from the chat immediately after entry if the
  bot has the necessary permissions;
- the encrypted API uses a per-app encryption key when one is configured.

## Data Storage and Files

The project uses JSON instead of a separate database. This keeps things simple
for a small VPS and makes backup straightforward.

| File | Contents |
|---|---|
| `.env` | Runtime secrets and launch settings |
| `users.json` | Users, roles, preferences, tracked QR/URL messages |
| `app_keys.json` | Application API and encryption keys |
| `vless_config.json` | VLESS-Reality parameters and clients |
| `hysteria2_config.json` | Hysteria2 parameters, clients, QUIC safe defaults |
| `naiveproxy_config.json` | Domain, port, basic auth for Caddy/NaiveProxy |
| `mtproto_config.json` | MTProto parameters and clients |
| `mieru_config.json` | Mieru server (`mita`) parameters, port bindings, and clients |
| `xui_config.json` | Encrypted 3x-ui REST integration settings |
| `headscale_config.json` | Headscale settings (when used) |
| `tuic_config.json`, `anytls_config.json`, `xhttp_config.json` | JSON stubs for planned transports |

Host-side configs written by apply commands:

| Path | Written by |
|---|---|
| `/usr/local/etc/xray/config.json` | `vless_manager.py` / Xray apply |
| `/etc/hysteria/config.yaml` | `hysteria2_manager.py` / `/hy2_apply` |
| `/etc/caddy-naive/Caddyfile` | `naiveproxy_manager.py` / `/naive_apply` |
| `/etc/mieru/server_config.json` | `mieru_manager.py` / `/mieru_apply` (mita apply config) |
| `/etc/nginx/…` | Nginx/SNI helpers when needed |

## Transport Layer

### VLESS-Reality

Implemented in `vless_manager.py` and Xray.

Used for:

- stealth TCP transport;
- VLESS URI/QR generation;
- desktop client export;
- 3x-ui integration when the operator chooses an external panel-managed flow.

Important distinction:

- bot-managed VLESS writes to `vless_config.json` and
  `/usr/local/etc/xray/config.json`;
- 3x-ui-managed VLESS is stored in the panel's SQLite database and managed
  via the 3x-ui HTTP API;
- these two client lists are not merged automatically.

### Hysteria2

Implemented in `hysteria2_manager.py` and `hysteria-server.service`.

Used for:

- fast UDP/QUIC transport;
- fallback candidate for networks where TCP performs poorly;
- export to `sing-box`, `Clash Meta`, and Telegram-only profiles.

Implementation notes:

- when clients are present the server config uses `auth.type: userpass`;
- client export must pass `name:password`, not just the password;
- `/hy2_set_quic_safe 1` enables safe QUIC defaults for Windows/MTU issues;
- `/hy2_apply` writes `/etc/hysteria/config.yaml` and restarts the service;
- `/hy2_install` checks for both the `hysteria` binary and
  `hysteria-server.service`.

### NaiveProxy

Implemented in `naiveproxy_manager.py` and `caddy-naive.service`.

On the server this is not a separate naive daemon — it is Caddy with the
`forwardproxy@naive` plugin.

The bot handles:

- `Caddyfile` generation;
- domain and `basic_auth`;
- `systemctl restart caddy-naive`;
- export of `naive+https://user:pass@host:port`.

NaiveProxy currently uses a shared credential: changing the password affects
all users.

### MTProto

Implemented in `mtproto_manager.py` and the host-side `mtproto-proxy` or `mtg`
service.

Used as a Telegram-native fallback. Simpler for Telegram clients but not a
full VPN transport replacement.

### Mieru

Implemented in `mieru_manager.py` and the host-side `mita` service
(`enfein/mieru` project).

Used as:

- a backup TCP/UDP transport that requires no TLS certificate or domain;
- a research platform for DPI parameters (`port`, `port_range`, `mtu`,
  `multiplexing`, `handshake_mode`, `socks5_port`, `logging`);
- a per-user model: each client is a `name + password` pair, with names
  canonicalised to `Mieru_ID<first2>_<last2>` (see
  `provision_manager.PROTOCOL_PREFIX`).

The bot handles:

- generating `mieru_config.json` locally and `/etc/mieru/server_config.json`
  on the host;
- running `scripts/install_mieru.sh` (resolves the latest mita release via the
  GitHub API, `dpkg -i`, NTP, optional ufw);
- service management via `systemctl <action> mita`;
- soft-reload via `mita reload` for user/logging changes and full restart for
  port/MTU changes;
- export of `mierus://` URI, client JSON, Clash/mihomo block.

Mieru is critically dependent on time synchronisation between the VPS and the
client. `/diag` via `live_status._mieru_status` shows `timedatectl status` and
warns about port 443 conflicts with VLESS/Hysteria2/NaiveProxy.

### TUIC, AnyTLS, XHTTP

The repository includes JSON managers/stubs: `tuic_manager.py`,
`anytls_manager.py`, `xhttp_manager.py`.

Current status:

- configs and partial exports are present;
- full server-side apply flow is not yet complete;
- `/diag` shows them as planned/not implemented unless the bot can verify a live
  service.

### Headscale

Optional mesh infrastructure via `headscale_manager.py` and
`compose.headscale.yaml`.

Headscale is not a mandatory part of TelegramHelper. It is for operators who
need a self-hosted Tailscale-like mesh alongside transport management.

## Live Diagnostics

`live_status.py` allows the bot to show the real VPS state, not just an
`enabled` flag from JSON.

It checks:

- host ports via `/proc/1/net/{tcp,tcp6,udp,udp6}`;
- host processes via `/proc/<pid>/cmdline`, `/proc/<pid>/comm`,
  `/proc/<pid>/exe`;
- configs in `/usr/local/etc/xray`, `/etc/hysteria`, `/etc/caddy-naive`;
- Docker containers via Docker socket or socket proxy;
- presence of 3x-ui process/unit/container.

Why this matters: port `:443/tcp` may be used by different services (`xray`,
`caddy-naive`, `nginx`). The fact that a port is listening does not prove the
right protocol is running. A green status is only set when the corresponding
process or an explicit config/marker is found.

Main diagnostic commands:

```bash
/diag
/ver
/dockhand
```

`/diag` shows per-protocol status, port, process, unit/config hints, and
errors. If `/proc` or the Docker socket is unavailable, the module degrades to
`unknown` rather than breaking the bot loop.

## Docker Stack

The primary deployment uses Docker Compose.

| Container | Role |
|---|---|
| `telegram-helper-lite` | Telegram bot + FastAPI on `:8000` |
| `dockhand` | Streamlit diagnostics panel on `127.0.0.1:8501` |
| `docker-socket-proxy` | Restricted read-only access to the Docker Engine API |
| `headscale` | Optional, via `compose.headscale.yaml` overlay |

`telegram-helper-lite` typically runs with `pid: host` and `privileged: true`
because the bot must see host processes and manage systemd services. This is a
powerful mode, so Docker access for Dockhand is routed through
`docker-socket-proxy`, and the panel must only be accessible via an SSH tunnel.

## 3x-ui Integration

3x-ui is an external Xray panel stack. It is not TelegramHelper's internal
database.

The split:

| Stack | Where clients are stored | Who manages them |
|---|---|---|
| Bot-managed Xray | `vless_config.json` + `/usr/local/etc/xray/config.json` | `vless_manager.py` |
| 3x-ui Xray | `/etc/x-ui/x-ui.db` and bundled Xray | 3x-ui panel / API |

`xui_manager.py` provides opt-in REST integration:

- `/xui_setup` asks for URL, login, and password, plus the default inbound;
- the password is encrypted via `SecureMessenger`;
- `/xui_status`, `/xui_list`, `/xui_set_inbound`, and `/xui_clear` manage the
  binding;
- the `/user <id>` card can create/delete/show a client's QR code in 3x-ui via
  the panel's HTTP API.

The bot does not write directly to 3x-ui's SQLite database. All changes go
through the official panel API so the panel can regenerate the Xray config and
restart the relevant process.

## Export Layer

Export is implemented in transport managers and `telegram_capsule_export.py`.

Main formats:

| Format | For |
|---|---|
| `apix-profile v2` | Policy-aware profile for sing-box-compatible clients |
| `aping-naive-profile` | NaiveProxy profile for Clash Meta |
| `aping-mieru-profile` | Mieru profile for Clash Meta |
| `mierus://` | Shareable Mieru link with `protocol/mtu/mux/handshake` parameters |
| `sing-box` JSON | Direct import / manual sing-box verification |
| `Clash Meta` YAML | Import in Clash Meta (including Mieru block) |
| URI/QR | Quick connection via mobile or desktop client |
| Email bundle | Profile backup via `/email_profile` |

The Telegram-only routing model is stored as a policy: proxy Telegram domains
through selected transports, leave all other traffic direct. This lets the
client apply routing rules locally; the server does not need to know the
details of the local TUN/SOCKS5 setup.

## Clash Meta and sing-box

TelegramHelper is the server side. Desktop clients import profiles and run
local engines.

| Client | Current role |
|---|---|
| `Clash Meta` | Primary VPN client for VLESS-Reality and NaiveProxy |
| `sing-box` | Universal client for VLESS-Reality, Hysteria2, TUIC, AnyTLS, XHTTP |

Responsibility split:

| Area | Owner |
|---|---|
| Server configs and systemd services | TelegramHelper |
| Users, roles, QR/email/provisioning | TelegramHelper |
| Local TUN/SOCKS5/auto routing | Desktop client |
| Local `sing-box`, `naive`, `xray` process | Desktop client |
| Server admin via Telegram | TelegramHelper bot |

## Typical Flow: New User

```bash
/special_add 123456789
/provision 123456789
/profiles 123456789
```

The operator can then:

- open the QR/URI from the user card buttons;
- send profiles by email via `/email_profile 123456789`;
- delete profiles via `/clean_user 123456789`;
- rotate secrets via inline buttons where supported.

If a transport was changed manually, apply/restart the corresponding service:

| Transport | Apply command |
|---|---|
| VLESS/Xray | `/xray_apply` or the VLESS sync/apply flow |
| Hysteria2 | `/hy2_apply` |
| NaiveProxy | `/naive_apply` |
| MTProto | `/mt_apply` |

General rule: modifying a JSON client does not mean the host service has
accepted the new config. After apply, verify with `/diag` and logs.

## Typical Flow: Diagnosing a Connection Problem

1. Check `/diag` — does the bot see the expected process and port?
2. Check the firewall: `ufw status verbose` and security groups at the VPS
   provider.
3. Check logs: `journalctl -u <service> -n 100 --no-pager`.
4. Confirm the config was applied after the client was created.
5. Verify the profile: server, port, SNI, insecure flag, password/userpass.
6. Check the network: Wi-Fi vs mobile, especially for UDP/QUIC.
7. Check the desktop client: admin rights for TUN, local process, SOCKS5 port.

For Hysteria2 common causes are: `443/udp` is blocked, `insecure=1` is needed
for a self-signed cert, the client sends only a password without `name:` in
`userpass` mode, or Windows/MTU issues require `/hy2_set_quic_safe 1`.

## Email Profile Delivery

`email_manager.py` sends profiles via Gmail API or SMTP.

Architecturally this is not just a notification — it is a backup delivery
channel:

- Telegram QR codes/URIs may be deleted after TTL;
- email provides a persistent profile backup;
- Gmail API is preferred over SMTP when OAuth is configured;
- `/email_profile` cleans up tracked QR/URL messages in Telegram after a
  successful send.

Message tracking is stored in `users.json` so cleanup survives container
restarts. In-memory `asyncio` TTL is the fast path but not the only mechanism.

## Deployment Model

Primary production layout:

```text
/opt/TelegramHelper
  compose.yaml
  .env
  users.json
  app_keys.json
  *_config.json
  gmail_oauth_client.json
  gmail_token.json
```

Operations documentation:

- `DEPLOY.md` — installation, `.env`, VPS operations;
- `DOCKER.md` — Docker Compose, volumes, socket proxy;
- `POST_DEPLOY.md` — post-deploy checklist;
- `SECURITY.md` — keys, secrets, file permissions.

## What Is Implemented

- Telegram bot + FastAPI runtime.
- Admin/special user model.
- Bot-managed provisioning via `/provision`, `/profiles`, `/my_profile`.
- Email profile delivery via Gmail API/SMTP.
- VLESS-Reality management and Xray integration.
- Hysteria2 management, `userpass`, QR/URI/export, safe QUIC defaults.
- NaiveProxy via Caddy forwardproxy.
- MTProto management.
- Mieru (`mita`) management: per-user clients, `mierus://` URI, Clash block,
  DPI parameters; integrated into `/provision`, `/profiles`, `/my_profile`,
  `/email_profile`, `/diag`.
- 3x-ui REST integration without direct SQLite access.
- Live diagnostics via `/diag` and `live_status.py`.
- Dockhand diagnostics panel.
- JSON-backed stubs for TUIC/AnyTLS/XHTTP.
- Exports for `Clash Meta` and `sing-box`.
- Documentation and server cleanup/deploy guides.

## Design Trade-offs

The project deliberately chooses:

- JSON files over a separate database;
- explicit scripts and systemd over hidden magic;
- Telegram bot as the primary control plane;
- backward compatibility with existing flows rather than abrupt breaking changes;
- small manager modules rather than a monolithic orchestration framework.

Downsides of this approach:

- some code grew from the private ancestor project;
- some protocols are still planned/stubs;
- host-level Docker privileges require careful deployment;
- apply/restart discipline is important; otherwise JSON and the running service
  diverge.

Benefits:

- easy to debug on a VPS;
- simple to back up;
- the operator can see real files and systemd units;
- legacy deployments can be migrated incrementally.

## Roadmap

Near-term logical improvements:

- complete server-side apply flows for TUIC/AnyTLS/XHTTP;
- continue improving `/profiles` and `/user` cards;
- standardise export artifacts;
- strengthen JSON config validation before apply;
- expand health checks and `/diag` hints;
- reduce legacy coupling with the private ancestor project;
- keep documentation in sync with actual bot commands.

Long-term, TelegramHelper should remain a Telegram-first transport profile
management server: understandable for the operator, secure by default, and
compatible with current desktop clients.
