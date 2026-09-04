# DEPLOY.md — Installation, .env, Updates and Operations for TelegramHelper

Single reference guide replacing the former `INSTALL_VPS.md` and `VPS_GUIDE.md`. Covers: flavor selection, fresh installation, Mieru as a standalone port, `.env`, Docker Compose, Gmail/SMTP, bot-managed onboarding, redeployment, ports, diagnostics and firewall baseline.

---

## 0. Two Installation Flavors

Choose one — mixing them is not supported.

| Flavor | Script | What you get | When to use |
| --- | --- | --- | --- |
| **Bot-only (systemd, Python venv)** | `scripts/install_telegramhelper_vps.sh` | `telegramhelper.service` + venv; no transports, no firewall changes, no Docker | Server already exists; transports installed separately (§6) |
| **Full-stack (Docker Compose, interactive)** | `scripts/deploy_fresh_vps.sh` | Bot + selected transport on 443 (VLESS-Reality **or** NaiveProxy) + Hysteria2 + MTProto + ufw, **IPv6 disabled system-wide** | Fresh VPS, everything in one pass |

Sections §3 describe the **bot-only** flavor. §4 covers **full-stack** via `deploy_fresh_vps.sh`. §6 covers individual transport installation (can be used on top of bot-only). §8+ cover `.env`, Docker, onboarding and operations — shared by both flavors.

### 0.1. Interactive Setup on VPS (`vps_setup.sh`)

An alternative to flags — **an interactive wizard running directly on the server**.

**Step 1. Deliver the code to the VPS** (private repo) — choose one:

Option A — `git clone` on the VPS (requires a GitHub fine-grained token, read-only):
```bash
apt update && apt install -y git
git clone https://github.com/your-org/TelegramHelper.git /opt/TelegramHelper
#   when prompted: login your-github-user, password = GitHub token (read-only, this repo)
cd /opt/TelegramHelper
```

Option B — `rsync` from your local machine (no token needed on the VPS; rsync available in Git Bash):
```bash
# on local machine, from the directory containing TelegramHelper/
rsync -az -e ssh \
  --exclude '.git' --exclude '*venv*' --exclude '__pycache__' \
  --exclude 'node_modules' --exclude '.env' --exclude 'dist' --exclude 'build-*' \
  TelegramHelper/ root@YOUR_VPS_IP:/opt/TelegramHelper/
# then on VPS:
cd /opt/TelegramHelper
```

**Step 2. Run the wizard** from the repository root:
```bash
bash scripts/vps_setup.sh            # interactive prompts for each component
bash scripts/vps_setup.sh --dry-run  # show actions without executing
```

The wizard runs on **Python + rich** (`scripts/vps_setup.py`; the `vps_setup.sh` wrapper installs `rich` automatically). **Docker and Hysteria2 are always installed** (the wizard states this explicitly). Optionally prompts for: **API — Telegram bot** (if selected, also asks **how to install**: `systemd` or `docker`, then **starts it**, asks for `BOT_TOKEN` and `ADMIN_USER_IDS`), VLESS, MTProto, NaiveProxy, **Headscale** (role `coordinator`/`client`; Headplane is installed for `coordinator`), HA server+Reticulum, **Dockhand** (Streamlit diagnostics, `:8501` localhost). Port `443/TCP` conflicts (VLESS↔NaiveProxy) are resolved **interactively** (without exiting). At the end — a rich report with the status of each service.

> **Docker or systemd for the bot — which to choose.** `docker` is simpler and behaves the same as in `compose.yaml` (service `telegram-helper`) — choose it if the bot is the only service on the VPS. `systemd` (`telegramhelper.service`, without Docker for the bot itself) is recommended for a **full-stack** VPS with other Docker services already running (Headscale, Dockhand, docker-socket-proxy, HA stack) — it avoids port conflicts on `127.0.0.1:8000` and reduces load on small VPS (1 GB RAM and less — see `DOCKER.md`). Both options are fully functional and updated the same way (POST_DEPLOY.md §2). Switching methods after installation requires manual steps (stop old, start new via the corresponding `scripts/install_telegramhelper_{vps,docker}.sh`).

> VLESS requires the project already deployed on the VPS (needs `vless_manager.py`).
> HA server + Reticulum — optional (TCP gRPC + Reticulum tests, see §6.7).

> **Why IPv6 is disabled in full-stack.** Dual-stack VPS routes egress over IPv6 when the destination has an AAAA record. Geolocation services (ipify, ipwho, ipinfo) see the server's IPv6 address rather than the IPv4 tunnel egress — and services like ChatGPT/banks/Spotify decide the client is connecting from the wrong country. `deploy_fresh_vps.sh` writes `/etc/sysctl.d/99-disable-ipv6.conf`. To re-enable IPv6: `rm /etc/sysctl.d/99-disable-ipv6.conf && sysctl --system && systemctl restart x-ui caddy-naive` (plus restart any other transports that need IPv6 binding). The bot-only flavor does not touch IPv6.

---

## 1. Current VLESS Architecture

For a new production VPS there are two VLESS backend modes and three operational management methods. Choose before initial installation to avoid having two different Xray instances sharing a single `443/tcp`.

```text
Option 1: legacy xray.service
           source of truth: /opt/TelegramHelper/vless_config.json
           management: bot + file + scripts/ssh/vless.py

Option 2: 3x-ui, management via terminal menu x-ui
           source of truth: x-ui.service / 3x-ui
           management: x-ui in terminal + bot after /xui_setup

Option 3: 3x-ui, management via browser panel
           source of truth: same x-ui.service / 3x-ui
           management: browser + bot after /xui_setup
```

Options 2 and 3 use the **same 3x-ui backend**. The difference is only in the operator interface.

User onboarding via the bot is identical for all VLESS options:

```text
/provision <telegram_user_id>
/profiles <telegram_user_id>
/my_profile
/email_profile <telegram_user_id>
/clean_user <telegram_user_id> YES
```

---

## 2. Paths and Names

| Item | Value |
| --- | --- |
| Project directory | `/opt/TelegramHelper` |
| Compose service (bot) | `telegram-helper` |
| Bot container | `telegram-helper-lite` |
| Dockhand service | `dockhand` |
| Docker API proxy | `docker-socket-proxy` |

In all commands below:

```bash
export APP=/opt/TelegramHelper
cd "$APP"
```

### 2.1. Port Map

| Port | Service | Notes |
| --- | --- | --- |
| `443/tcp` | Xray/3x-ui VLESS-Reality or Caddy/NaiveProxy | one owner per IP (see §5) |
| `443/udp` | Hysteria2 or Caddy QUIC | does not conflict with VLESS TCP |
| `8000/tcp` | TelegramHelper REST API | can be closed by reverse proxy/firewall as needed |
| `8501/tcp` | Dockhand | `127.0.0.1` only, access via SSH tunnel |
| `993/tcp` | MTProto | optional |
| `8443/tcp` | Nginx SNI / Headscale / HA | optional |
| `29999/tcp` | Mieru / mita | default; no domain required |
| `YOUR_SSH_PORT/tcp` | SSH | full-stack flavor moves SSH here (§4); keep open in UFW |
| `50055/tcp`, `50056/udp`, `50061/tcp` | HA stack (stubs): gRPC / UDP stub / Reticulum bridge | `127.0.0.1` only; access via SSH tunnel (§6.7) |

Verification:

```bash
ss -tulpn | grep -E ':443|:8000|:8443|:8501|:993|:29999|:YOUR_SSH_PORT|:50055|:50061'
ufw status numbered
```

---

## 3. Flavor A: Bot-Only Installation

### 3.1. Clone the Repository

```bash
git clone <your-repo-url> TelegramHelper
cd TelegramHelper
```

### 3.2. Bootstrap Script

```bash
sudo bash scripts/install_telegramhelper_vps.sh
```

What it does:

- installs base system packages for the Python runtime;
- creates `venv/`;
- installs `requirements.txt`;
- creates `.env` from `example.env` if it doesn't exist;
- **interactively prompts for `BOT_TOKEN`** (from @BotFather) if it's still a placeholder. Can be pre-set via environment variable (`BOT_TOKEN=... sudo -E bash scripts/install_telegramhelper_vps.sh`) or skipped and filled in later via `.env` / `scripts/change_token.sh <token>`;
- **interactively prompts for `ADMIN_USER_IDS`** (your Telegram ID; find it via @userinfobot; multiple IDs separated by commas). Without this the bot won't recognize you as admin. Can be pre-set (`ADMIN_USER_IDS=... sudo -E bash scripts/install_telegramhelper_vps.sh`) or added later to `.env`;
- writes `telegramhelper.service`;
- enables the systemd service.

### 3.3. Start the Service

```bash
sudo systemctl restart telegramhelper
sudo systemctl status telegramhelper | cat
```

Logs:

```bash
sudo journalctl -u telegramhelper -f | cat
```

Continue with §8 for `.env`, §6 for transports, §11 for onboarding.

---

## 4. Flavor B: Full-Stack via `deploy_fresh_vps.sh`

On a fresh Debian/Ubuntu VPS, after first login via the provider's default SSH port:

```bash
ssh root@YOUR_VPS_IP
apt-get update
apt-get install -y git curl ca-certificates
mkdir -p /opt/TelegramHelper
cd /opt/TelegramHelper
git clone https://github.com/your-org/TelegramHelper.git .
bash scripts/deploy_fresh_vps.sh
```

> The deployment scripts (`auto_setup_vps.sh`, `deploy_fresh_vps.sh`, `install_telegramhelper_vps.sh`) install the `qrencode` package — needed to render VLESS QR codes directly in the SSH console: `printf '%s' "$VLESS_LINK" | qrencode -t ANSIUTF8`. More convenient and reliable than copying a long link with `pbk` by hand.

The script is interactive. Before installing Xray/Caddy/mita it asks which transport stack you need:

```text
Which transport stack should be the primary?

  [1] VLESS-Reality legacy xray.service
      No 3x-ui: bot + vless_config.json + /usr/local/etc/xray/config.json.

  [2] VLESS-Reality via 3x-ui + terminal menu x-ui
      Installs 3x-ui; manage the service with the x-ui command.

  [3] VLESS-Reality via 3x-ui + browser panel
      Installs 3x-ui; edit inbounds/clients in the browser.

  [4] NaiveProxy / Caddy
      Requires a domain; Cloudflare proxy must be OFF.

  [5] Mieru / mita
      Separate TCP/UDP port, default 29999/tcp. No domain required.

Note: 1/2/3/4 own TCP/443. Mieru does NOT occupy 443 by default.

Choice [1/2/3/4/5] (Enter = 1):
```

What happens:

1. Swap 1 GB (for small VPS).
2. **Disable IPv6** system-wide (`/etc/sysctl.d/99-disable-ipv6.conf`).
3. `apt update && apt upgrade`.
4. Base packages + Docker.
5. Selected transport.
6. Hysteria2.
7. UFW + fail2ban.
8. Move SSH to `YOUR_SSH_PORT`.
9. Scaffold `/opt/TelegramHelper`.
10. Summary with credentials.

After the server phase completes — reconnect on the new SSH port `YOUR_SSH_PORT`:

```bash
ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP
```

Continue with §8 for `.env`, §9 for Docker Compose, §11 for onboarding.

---

## 5. Choosing the Owner of Port 443

On a single VPS, port `443/TCP` must be held by one primary TLS service.

| Scenario | 443/TCP | 443/UDP | Notes |
| --- | --- | --- | --- |
| **VLESS-Reality / 3x-ui** | Xray panel | optionally Hysteria2 | convenient with browser panel and manual management |
| **VLESS-Reality / legacy Xray** | `xray.service` | optionally Hysteria2 | no panel; bot manages clients directly |
| **NaiveProxy** | Caddy | Caddy QUIC | requires domain, Cloudflare proxy OFF |
| **Mieru / mita** | free | free | separate TCP/UDP port (default `29999`); do not use 443 |
| **Nginx HTTPS API** | Nginx | none | only if 443 is not taken by Xray/Caddy |

Mieru is a fallback transport: it uses its own port (default `29999/tcp` via `/mieru_set_port 29999 tcp`). It requires no domain and no TLS certificate, so it can safely coexist with any of the options above.

If you need VLESS on 443 and Headscale/Home Assistant on HTTPS simultaneously, use Xray fallback + Nginx stream SNI routing (`/nginx_*`).

---

## 6. Transports Individually (Helper Scripts)

Can be used on top of a bot-only installation, or to add a second transport to a full-stack setup.

- `scripts/setup_vless_server.sh` — VLESS-Reality via Xray on `:443/TCP`;
- `scripts/install_hysteria2.sh` — Hysteria2 on `:443/UDP`;
- `scripts/install_mtproto.sh` — MTProto Telegram relay;
- `scripts/install_naiveproxy.sh` — NaiveProxy via Caddy on `:443/TCP` (+ `:443/UDP` for HTTP/3);
- `scripts/install_mieru.sh` — Mieru/mita;
- `scripts/install_headplane.sh` — **Web UI for Headscale** (not a transport). Starts the `headplane` container via `compose.headplane.yaml` on `127.0.0.1:3000`, accessed via SSH tunnel. Requires Headscale already running (`docker ps | grep headscale`). See [`HEADSCALE_GUIDE.md`](HEADSCALE_GUIDE.md) → "Web UI via Headplane".
- `scripts/install_ha_adapter.sh` — **real HA** (`ha-adapter-grpc` `:50057`, bridge switching, optional public `:50061`); also prompted in `vps_setup.sh` after the HA stack step. See [START_HERE.md](START_HERE.md).
- `scripts/install_ha_stack.sh` — **HA stack (stubs)** (not a transport). gRPC `DeviceControlService` with Mi-Home devices + Reticulum bridge for e2e client tests via TCP gRPC and through Reticulum. Localhost only, accessed via SSH tunnel. Includes automatic soft-hang recovery (swap, `ha-rns-watchdog`: port + heartbeat + gRPC, handler timeout, weekly cron) — see [`RETICULUM_GUIDE.md`](RETICULUM_GUIDE.md) §10. Upgrade on a live VPS: `bash scripts/upgrade_ha_rns_watchdog.sh`. See §6.7 and [`ha_stack/README.md`](ha_stack/README.md).

> **Port 443 is exclusive.** VLESS-Reality (Xray) and NaiveProxy (Caddy) both own `:443/TCP` and cannot coexist on the same VPS. Hysteria2 (`:443/UDP`) is compatible with VLESS but **conflicts with NaiveProxy** (Caddy also binds `:443/UDP` for HTTP/3). Choose the owner of `:443` **before** installation.

### 6.1. Headscale (mesh) and Headplane

Headscale is started via a Compose overlay:

```bash
docker compose -f compose.yaml -f compose.headscale.yaml up -d headscale
```

> ⚠️ **Start only the `headscale` service** (not the entire stack). `docker compose ... up -d` without a service name also starts the bot container, which conflicts with the systemd bot over `127.0.0.1:8000`. If you installed the bot via systemd (`install_telegramhelper_vps.sh`), specify `... up -d headscale` for Headscale.
>
> **The `vps_setup.py` wizard handles this correctly:** the Headscale step asks the VPS role — **coordinator** (Headscale server here) or **client** (node: installs `tailscale` and connects to an existing coordinator). Coordinator: generates `headscale/config/config.yaml` (`server_url`/`listen_addr 0.0.0.0:8080`), starts **only** the `headscale` service (fixes the 8000 conflict) + Headplane. Client: asks for the coordinator URL + pre-auth key and runs `tailscale up --login-server <url> --authkey <key>`.

Common operations:

```bash
docker exec headscale headscale users list
docker exec headscale headscale nodes list
docker exec headscale headscale preauthkeys create --user 1 --reusable --expiration 24h
# --user = numeric ID (uint), not username
```

Web UI (Headplane) — SSH tunnel only, not exposed publicly:

```bash
bash scripts/install_headplane.sh
ssh -p YOUR_SSH_PORT -L 3000:127.0.0.1:3000 root@YOUR_VPS_IP
# Browser: http://127.0.0.1:3000/admin
# Login key: docker exec headscale headscale apikeys create --expiration 24h
```

Bot commands: `/headscale_status`, `/headscale_list_nodes`, `/headscale_gen`, `/headscale_create_user`, `/headscale_enable`, `/headscale_set_url`; exit node — `/exit_node`, `/exit_node_on`, `/exit_node_off`. Bot config — `headscale_config.json` (`enabled`, `server_url`, `default_user`, `container_name`).

See [`HEADSCALE_GUIDE.md`](HEADSCALE_GUIDE.md) (including exit node and break-glass `scripts/exit_node.sh`). If Headscale/HA share HTTPS with VLESS on 443, use `/nginx_set_domain`, `/nginx_enable`, and `/nginx_config`.

### 6.7. HA Stack (Stubs) — Access via TCP gRPC and Reticulum

A self-contained stack for **e2e testing** of the gRPC client: simulates an "HA server" with Mi-Home devices (`mi_bulb`, `mi_th_sensor`, `mi_vibration`) — **without a real Home Assistant** — and provides access via **two paths**: directly over **TCP gRPC** and through the **Reticulum** stack (RNS bridge). All code is in [`ha_stack/`](ha_stack/).

**Installation (on VPS, from the repository root):**

```bash
bash scripts/install_ha_stack.sh
# Starts three systemd services on 127.0.0.1 and prints the bridge destination hash:
#   ha-stub-grpc        :50055  — gRPC DeviceControlService (stubs)
#   ha-stub-udp         :50056  — UDP stub (for raw path)
#   ha-reticulum-bridge :50061  — RNS TCPServerInterface (bridge)
```

All services listen only on `127.0.0.1` — **not exposed externally**, no firewall changes needed.

**Access from your machine — SSH tunnel:**

```bash
ssh -L 50055:127.0.0.1:50055 -L 50061:127.0.0.1:50061 <user>@YOUR_VPS_IP
```

Management: `systemctl {status,restart} ha-stub-grpc ha-stub-udp ha-reticulum-bridge` (+ `i2pd`, if the I2P layer was installed).

**I2P access to the bridge (optional, path 2 — e2e ✅):** expose the bridge as an I2P hidden service **without a public port or SSH tunnel**. The RNS config and bridge code **do not change** (the bridge stays on `TCPServerInterface 127.0.0.1:50061`) — an `i2pd` daemon is installed alongside to wrap the local bridge port into an I2P destination via a **server tunnel**. SAM is not needed.

```bash
bash scripts/install_i2p_bridge.sh        # i2pd + reseed hardening + ha-bridge tunnel, prints b32
```

Full guides: HA stack (TCP, destination hash) — [`RETICULUM_GUIDE.md`](RETICULUM_GUIDE.md); I2P layer — [RETICULUM_GUIDE.md](RETICULUM_GUIDE.md).

---

## 7. NaiveProxy via Caddy

Recommended server model — `Caddy + forwardproxy@naive`.

### 7.1. DNS Prerequisite (Cloudflare)

The `A` record for the NaiveProxy domain **must** be **DNS only** (grey cloud) in Cloudflare, **not** Proxied (orange cloud). Cloudflare proxy does not forward `HTTP CONNECT` tunnels — Proxied silently breaks the proxy with a TLS `wrong version number` error downstream.

### 7.2. Installation

```bash
sudo bash scripts/install_naiveproxy.sh --domain your.domain.example
```

Optional flags:

- `--port 443`
- `--username naive-user`
- `--password strong-password`
- `--email you@example.com`

After installation:

```bash
sudo systemctl status caddy-naive | cat
sudo journalctl -u caddy-naive -n 100 | cat
```

### 7.3. Testing the Tunnel

`curl -x https://<domain> ...` **does not work** against this server and **will always appear broken** (`200 OK` + empty body + TLS `wrong version number`). This is by design: the `probe_resistance` directive in the generated Caddyfile rejects any CONNECT without NaiveProxy `Padding`/`Padding-Type-Request` headers. Without a real NaiveProxy client the server intentionally disguises itself as an ordinary web server.

Test with a real client:

1. Export a client profile: `/naive_export` in the bot or import into `Clash Meta`.
2. Start the client so it opens a local SOCKS5 proxy.
3. From the client machine:

```bash
curl -sS --socks5-hostname 127.0.0.1:10808 https://httpbin.org/ip
# → {"origin": "<public IP of VPS>"}
```

If `origin` equals the VPS public IP — the CONNECT tunnel works end-to-end.

### 7.4. Bot Commands for NaiveProxy

- `/naive_status`
- `/naive_config`
- `/naive_set_domain`
- `/naive_gen_creds`
- `/naive_install`
- `/naive_apply`
- `/naive_export`

---

## 8. `.env`: What to Fill In

`.env` is the runtime configuration. **Never commit it to git.**

### 8.1. Create the File

```bash
cd /opt/TelegramHelper
cp -n example.env .env
chmod 600 .env
```

Generate secrets:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
openssl rand -hex 32
```

Your Telegram admin ID — use `/info` in the bot or `getUpdates` after `/start`.

### 8.2. Telegram

| Variable | Description |
| --- | --- |
| `BOT_TOKEN` | token from `@BotFather` |
| `ADMIN_USER_IDS` | comma-separated Telegram IDs of admins |

If `BOT_TOKEN` is wrong, the bot won't start or Telegram will return `Unauthorized`.

### 8.3. API and Cryptography

| Variable | Purpose |
| --- | --- |
| `API_SECRET_KEY` | fallback API key |
| `HMAC_SECRET` | request signing |
| `ENCRYPTION_KEY` | AES-256-GCM; also encrypts the 3x-ui password in `xui_config.json` |
| `API_URL` | API URL shown to clients by the bot |

If you change `ENCRYPTION_KEY`, the old encrypted 3x-ui password becomes unreadable. You must re-run:

```text
/xui_clear YES
/xui_setup
```

### 8.4. Security Flags

Keep all enabled in production:

```env
ENABLE_SIGNATURE_CHECK=true
ENABLE_TIMESTAMP_CHECK=true
ENABLE_NONCE_CHECK=true
ENABLE_RATE_LIMITING=true
ENABLE_APP_WHITELIST=true
```

### 8.5. Dockhand

Typical variables:

```env
DOCKHAND_AUTH_PASSWORD=...
DOCKHAND_READONLY=1
DOCKHAND_TARGETS=telegram-helper-lite
DOCKHAND_REFRESH_RATE=10
DOCKHAND_HIDE_HEALTH_DEFAULT=true
DOCKHAND_LOG_DEFAULT_TAB=errors
```

For the SSH tunnel hint in `/dockhand`:

```env
DOCKHAND_SSH_HOST=YOUR_VPS_IP
DOCKHAND_SSH_PORT=YOUR_SSH_PORT
DOCKHAND_SSH_USER=root
```

### 8.6. Public VPS Address

So that `/start`, `/ver`, `/dockhand` show the correct address:

```env
TELEGRAMHELPER_PUBLIC_HOST=YOUR_VPS_IP
```

Address priority in code: `DOCKHAND_SSH_HOST`, `TELEGRAMHELPER_SSH_HOST`, `TELEGRAMHELPER_PUBLIC_HOST`, `PUBLIC_HOST`, `VPS_HOST`, then `server` from the VLESS config.

### 8.7. Rclone Backup

If offsite backup is used:

```env
RCLONE_REMOTE=encrypted:
RCLONE_BACKUP_PREFIX=telegramhelper
RCLONE_CONFIG=/rclone/rclone.conf
```

Bot commands: `/backup_status`, `/backup_test`, `/backup_now`, `/backup_list`.

### 8.8. Applying `.env` Changes

`docker compose restart` **does not re-read** `.env` — it reuses the environment snapshot from the last `up`. After editing `.env` (e.g., rotating `BOT_TOKEN`) the correct command is:

```bash
cd /opt/TelegramHelper
docker compose up -d --force-recreate telegram-helper
docker compose logs --tail=30 telegram-helper
```

Symptom of using `restart` after editing `.env`: the container starts with the **old** value and fails (`telegram.error.InvalidToken: Unauthorized` for the bot token, similar auth errors for other services).

If code or dependencies changed — run `docker compose build --no-cache` first.

### 8.9. Pin Docker Compose Subnets

TelegramHelper pins the default bridge subnet so `docker compose down` / `up` does not silently change the gateway address. Override with `COMPOSE_DEFAULT_SUBNET` and `COMPOSE_SOCKET_PROXY_SUBNET` in `.env` when the host already uses another pool:

```bash
bash scripts/preflight_subnets.sh
bash scripts/preflight_subnets.sh --fix
```

Run the preflight before the first `up` after a code update on an existing host.

---

## 9. Docker Compose and Runtime Files

Before the first start, ensure bind-mount files exist as **files**, not directories (Docker creates directories if the bind target is missing):

```bash
cd /opt/TelegramHelper
for f in \
  vless_config.json hysteria2_config.json tuic_config.json anytls_config.json \
  xhttp_config.json mtproto_config.json headscale_config.json naiveproxy_config.json \
  mieru_config.json xui_config.json app_keys.json users.json; do
  [ -d "$f" ] && rmdir "$f"
  [ -f "$f" ] || echo '{}' > "$f"
done
[ -d bot.log ] && rmdir bot.log
[ -f bot.log ] || : > bot.log
```

Start:

```bash
docker compose build --no-cache telegram-helper dockhand
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
docker compose ps
docker compose logs --tail=80 telegram-helper
```

If 3x-ui is accessible only via a Tailscale/Headscale mesh IP:

```bash
docker compose -f compose.yaml -f compose.host.yaml build --no-cache telegram-helper dockhand
docker compose -f compose.yaml -f compose.host.yaml up -d --force-recreate docker-socket-proxy telegram-helper dockhand
```

### 9.1. Small VPS: 1 vCPU / 1 GB RAM / 10 GB Disk

Don't rebuild unnecessarily. If only the bot changed, build and recreate only `telegram-helper`:

```bash
cd /opt/TelegramHelper
docker compose build telegram-helper
docker compose up -d --force-recreate telegram-helper
docker compose ps
docker compose logs --tail=100 telegram-helper
```

To free space, **do not delete volumes**:

```bash
docker image prune -f
docker builder prune -f
```

Do not use:

```bash
docker system prune --volumes
```

This can destroy important data (Headscale database, etc.).

### 9.2. Headscale Running, 3x-ui Only Accessible via Mesh

Typical setup:

```text
VPS: 1 vCPU / 1 GB RAM / 10 GB disk
Bot: telegram-helper-lite
Mesh: headscale + tailscale0 on host
3x-ui panel URL: https://100.64.0.2:PORT/WEB_BASE_PATH
```

If 3x-ui listens only on a mesh IP, the container on a regular Docker bridge network won't reach it. Run the bot with the host-network override:

```bash
cd /opt/TelegramHelper

docker compose -f compose.yaml -f compose.host.yaml \
  build --no-cache telegram-helper dockhand

docker compose -f compose.yaml -f compose.host.yaml \
  up -d --force-recreate docker-socket-proxy telegram-helper dockhand
```

Verify that the **bot container** can reach the panel:

```bash
docker exec telegram-helper-lite curl -sk \
  https://100.64.0.2:PORT/WEB_BASE_PATH/login \
  --connect-timeout 5 \
  -o /dev/null \
  -w "%{http_code}\n"
```

Expected: `200`, `400`, `401`, `404`, or `405` — any of these means TCP/TLS reached the panel. Bad: `000`, timeout, `connection refused`, `no route to host`.

After this in Telegram:

```text
/xui_clear YES      # if old URL/password/ENCRYPTION_KEY no longer apply
/xui_setup          # set the mesh URL of the panel
/xui_status
/xui_list
```

### 9.3. Quick Port Diagnostics

```bash
ss -tlnp | grep -E ':(443|8081|8000|29999)\b' || true
ss -ulnp | grep -E ':(443|8443|29999)\b' || true
```

For 3x-ui / VLESS:

```bash
systemctl status x-ui --no-pager || true
journalctl -u x-ui -n 80 --no-pager || true
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

For legacy VLESS without panel:

```bash
systemctl status xray --no-pager || true
journalctl -u xray -n 80 --no-pager || true
test -f /usr/local/etc/xray/config.json && echo "xray config exists"
```

For Hysteria2:

```bash
systemctl status hysteria-server --no-pager || true
journalctl -u hysteria-server -n 80 --no-pager || true
ufw status | grep -E '443|8443' || true
```

For Mieru:

```bash
systemctl status mita --no-pager || true
journalctl -u mita -n 80 --no-pager || true
mita status || true
ufw status | grep -E '29999|mieru|mita' || true
```

---

## 10. Gmail API and SMTP for `/email_profile`

Detailed walkthrough: `EMAIL.md`.

### 10.1. Gmail API

On VPS:

```env
GMAIL_OAUTH_CREDENTIALS=/app/gmail_oauth_client.json
GMAIL_TOKEN_PATH=/app/gmail_token.json
GMAIL_FROM=you@example.com
```

Files on host:

```text
/opt/TelegramHelper/gmail_oauth_client.json
/opt/TelegramHelper/gmail_token.json
```

In `compose.yaml`:

```yaml
volumes:
  - ./gmail_oauth_client.json:/app/gmail_oauth_client.json:ro
  - ./gmail_token.json:/app/gmail_token.json
```

Do **not** enable on VPS:

```env
GMAIL_OAUTH_ALLOW_LOCAL_SERVER=1
GMAIL_OAUTH_OPEN_BROWSER=1
```

These flags are only for a local machine with a browser.

### 10.2. SMTP Fallback

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=starttls
SMTP_USER=you@example.com
SMTP_PASS=app_password_16_chars
SMTP_FROM=you@example.com
SMTP_BLOCKED_TLDS=.ru,.su
```

For Gmail, `SMTP_PASS` is an app password, not the account password.

---

## 11. User Onboarding

Before issuing profiles, determine the VLESS mode:

- **3x-ui mode**: panel installed, bot connected via `/xui_setup`.
- **Legacy VLESS mode**: no panel, `xray.service` running, bot manages `vless_config.json`.

In 3x-ui mode, first configure integration:

```text
/xui_setup
/xui_status
```

In legacy mode, verify VLESS:

```text
/vless_status
/vless_sync
/vless_status
```

If `/vless_status` shows `Enabled` and `Configured: yes`, proceed with the common onboarding:

1. Add the user to special:

   ```text
   /special_add <telegram_user_id>
   ```

2. Create a profile:

   ```text
   /provision <telegram_user_id>
   ```

   In legacy VLESS mode the bot applies the config and restarts host Xray automatically after creating/removing a VLESS profile. If the client still doesn't appear in logs, restart manually:

   ```bash
   systemctl restart xray
   systemctl status xray --no-pager
   ss -tlnp | grep ':443'
   ```

3. View / re-issue URI and QR:

   ```text
   /profiles <telegram_user_id>
   ```

4. User retrieves their own profile:

   ```text
   /my_profile
   ```

5. Send via email:

   ```text
   /email_profile <telegram_user_id>
   ```

6. Remove / rotate:

   ```text
   /clean_user <telegram_user_id> YES
   /provision <telegram_user_id>
   ```

See `QR_CLIENT_ONBOARDING.md` for details.

### 11.1. sing-box Export

After configuring the server:

1. Open the bot.
2. Run `/tgcapsule_export`.
3. Select a TelegramHelper target.
4. Import the generated file into `sing-box`.

For NaiveProxy use `/naive_export` — produces a native client JSON for `naive` and a `Clash Meta` profile with `naiveproxy` settings.

---

## 12. Redeployment after `git pull`

Most common workflow:

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker compose build --no-cache telegram-helper dockhand
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
docker compose logs --tail=80 telegram-helper
```

If only the bot changed:

```bash
git pull origin main
docker compose build --no-cache telegram-helper
docker compose up -d --force-recreate telegram-helper
```

Detailed post-deploy cheatsheet: `POST_DEPLOY.md`.

### 12.1. Bot-Only (systemd, no Docker)

```bash
cd /opt/TelegramHelper
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart telegramhelper
sudo systemctl status telegramhelper | cat
```

### 12.2. Update Only Dockhand

```bash
docker compose build --no-cache dockhand
docker compose up -d --force-recreate docker-socket-proxy dockhand
docker compose ps dockhand docker-socket-proxy
docker compose logs --tail=80 dockhand
```

---

## 13. Verification

Diagnostic block (copy to VPS as one unit):

```bash
cd /opt/TelegramHelper

echo "=== Docker ==="
docker compose ps

echo "=== Bot logs ==="
docker compose logs --tail=60 telegram-helper

echo "=== Health ==="
curl -s http://127.0.0.1:8000/health || true

echo "=== Ports ==="
ss -tulpn | grep -E ':443|:8000|:8443|:8501|:993|:29999' || true

echo "=== Runtime files ==="
for f in .env app_keys.json users.json xui_config.json bot.log; do
  test -f "$f" && echo "OK $f" || echo "BAD $f"
done
```

Version inside the container:

```bash
docker exec telegram-helper-lite grep -m1 '^version' /app/pyproject.toml
```

Gmail API send verification:

```bash
docker compose logs --tail=150 telegram-helper | grep "Gmail API sent profile email"
```

QR dependency check:

```bash
docker exec telegram-helper-lite python -c "import qrcode; print('qrcode ok')"
```

In Telegram:

```text
/start
/info
/ver
```

Should show the correct public address and current version.

---

## 14. Common Errors

### `.env` became a directory

```bash
cd /opt/TelegramHelper
rm -rf .env
cp example.env .env
chmod 600 .env
```

Refill secrets or restore from backup.

### `Is a directory: '/app/..._config.json'`

Docker created a directory instead of a file. See §9 (force-create files) then:

```bash
docker compose build --no-cache telegram-helper
docker compose up -d --force-recreate telegram-helper
```

### `could not locate runnable browser`

Interactive Gmail OAuth is enabled on the VPS. Remove:

```env
GMAIL_OAUTH_ALLOW_LOCAL_SERVER=1
GMAIL_OAUTH_OPEN_BROWSER=1
```

Obtain `gmail_token.json` on a local PC and copy it to the VPS.

### `Gmail API ... disabled`

Gmail API is not enabled in Google Cloud:

```text
APIs & Services → Library → Gmail API → Enable
```

### `BOT_TOKEN` invalid

Check:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/getMe"
```

### `nsenter: Operation not permitted`

For host-namespace commands inside the container, the compose configuration needs:

```yaml
pid: host
privileged: true
user: "0:0"
```

### NaiveProxy: "container uses old Caddyfile"

Run `/naive_apply` in the bot — regeneration is idempotent and will apply the latest template.

---

## 15. Changing the Bot Token

1. In `@BotFather`: `/revoke` or `/token`.
2. Update on the VPS:

   ```bash
   cd /opt/TelegramHelper
   nano .env
   docker compose up -d --force-recreate telegram-helper
   docker compose logs --tail=80 telegram-helper
   ```

3. Verify in Telegram:

   ```text
   /start
   /info
   ```

---

## 16. Security

- `.env`, `gmail_oauth_client.json`, `gmail_token.json`, `app_keys.json`, `xui_config.json`, `users.json` — **never commit**.
- VPN profile QR codes / URIs provide VPN access. Treat them like passwords.
- On profile leak: `/clean_user <uid> YES` then `/provision <uid>`.
- On Gmail token leak: revoke OAuth access and obtain a new token.

UFW baseline (SSH port is `YOUR_SSH_PORT` after full-stack installation):

```bash
ufw status numbered
ufw allow YOUR_SSH_PORT/tcp
ufw allow 443/tcp
ufw allow 443/udp
```

Fail2ban:

```bash
fail2ban-client ping
fail2ban-client status sshd
```

See `SECURITY.md` for details.

---

## 17. Disk Layout: New vs. Legacy Servers

- **New deployments:** **`TelegramHelper`** layout, path **`/opt/TelegramHelper`** is consistent everywhere (`rsync` destination and `cd` before `docker compose`).
- **Existing legacy servers:** often use **`/opt/TelegramSimple`** (as in `scripts/deploy_to_server.sh`). Don't mix paths between copying and compose.

When migrating from `TelegramSimple` to `TelegramHelper`, move the directory tree once, then update all commands and scripts to use the new path.

---

## 18. Related Documents

- [`POST_DEPLOY.md`](POST_DEPLOY.md) — quick pull/build/recreate after changes.
- [`SSHvsTelegramBOT.md`](SSHvsTelegramBOT.md) — two control interfaces (Telegram bot vs SSH/CLI dashboard).
- the `scripts/` directory — all `scripts/` scripts in one list.
- [`DOCKER.md`](DOCKER.md) — operations guide, troubleshooting, PMTU black hole.
- [`EMAIL.md`](EMAIL.md) — SMTP / Gmail and token flow.
- [`QR_CLIENT_ONBOARDING.md`](QR_CLIENT_ONBOARDING.md) — unified URI/QR/email issuance path.
- [HEADSCALE_GUIDE.md](HEADSCALE_GUIDE.md) — Headscale / Headplane.
- [`DOCKER.md`](DOCKER.md) — Docker configuration details.
- [`DOCKHAND_GUIDE.md`](DOCKHAND_GUIDE.md) — Dockhand service.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — high-level architecture.
