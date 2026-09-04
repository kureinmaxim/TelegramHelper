# POST_DEPLOY.md — What to Do on the VPS After Updating TelegramHelper

A quick operational cheatsheet for an already-installed server. The key idea: **`git pull` updates files on disk but does not update code inside the Docker image**. After a pull you almost always need `docker compose build` and container recreation.

Exception: if only Markdown documents (`*.md`), README, cheatsheets or comments changed, you don't need to rebuild containers. On a small VPS (`1 vCPU / 1 GB RAM`) a `--no-cache` build can take 10+ minutes. In that case:

```bash
cd /opt/TelegramHelper
git pull origin main
docker compose ps
```

Short rule:

- docs only (`*.md`) → `git pull`, no build;
- bot code (`*.py`, `requirements.txt`, `pyproject.toml`) → build `telegram-helper`;
- `dockhand/` → build `dockhand`;
- use `--no-cache` only when you have cache problems or dependency changes.

---

## 0. Standard Names and Path

| Item | Value |
| --- | --- |
| Project directory | `/opt/TelegramHelper` |
| Bot service | `telegram-helper` |
| Bot container | `telegram-helper-lite` |
| Dockhand UI | service `dockhand`, container `dockhand` |
| Docker API proxy | service `docker-socket-proxy` |

In all commands below:

```bash
export APP=/opt/TelegramHelper
cd "$APP"
```

### 0.1. Interactive Update (`vps_update.sh`)

A **Python + rich** wizard (`scripts/vps_update.py`; the `vps_update.sh` wrapper installs `rich` automatically) — the counterpart to `vps_setup.sh`. It detects what's installed and offers to update each component with verified actions (git pull → docker rebuild bot → restart services → re-sync VLESS → restart HA stack, etc.), finishing with a rich status report. For the bot, the wizard automatically selects the update method based on what's already running (docker service `telegram-helper` or systemd unit `telegramhelper`). From the repository root on the VPS:

```bash
bash scripts/vps_update.sh            # interactive prompts for detected components
bash scripts/vps_update.sh --dry-run  # show actions without executing
```

For edge cases (host-network mesh-only §1.2, Gmail tokens §7, etc.) — use the sections below directly.

---

## 1. Most Common Scenario: Pull Code and Rebuild Everything

### Quick: Update the Bot (99% of cases)

**Fast way (recommended):**

```bash
cd /opt/TelegramHelper
bash scripts/vps_update.sh
```

The wizard runs `git pull`, checks subnets, rebuilds the bot (with the BuildKit DNS fallback — see §10) and offers to clean up disk. Answer `yes` to the prompts.

**Manual, one command:**

```bash
cd /opt/TelegramHelper
bash scripts/rebuild_bot.sh --pull --prune
```

`rebuild_bot.sh` does the same as the expanded sequence below but handles three common pitfalls:

1. if `docker compose build` fails on `apt` (BuildKit DNS) — automatically rebuilds with `docker build --network=host`;
2. recreates **only the bot**, no `docker compose down`;
3. at the end verifies the version **inside the container** against `pyproject.toml` — if the image wasn't rebuilt you'll see it immediately.

Flags: `--pull` (git pull before build), `--prune` (clean build cache after), `--no-cache` (rebuild all layers from scratch).

**Fully manual — if you need to control every step:**

```bash
cd /opt/TelegramHelper

# 1. Pull code
git pull origin main

# 2. Check subnets
bash scripts/preflight_subnets.sh
# if it complains:  bash scripts/preflight_subnets.sh --fix

# 3. Rebuild and recreate ONLY the bot
docker compose build telegram-helper
docker compose up -d --force-recreate telegram-helper

# 4. Verify
docker compose ps telegram-helper
docker compose logs --tail=20 telegram-helper 2>&1 | grep -viE 'GET /health'
grep -E '^version' pyproject.toml                                   # expected version
docker exec telegram-helper-lite grep -E '^version' /app/pyproject.toml  # what's actually in the container
```

The last two lines should match. After that `/version` in Telegram will show the same version.

> ❌ **Do not `docker compose down`** — it shuts down the entire project (dockhand, socket-proxy, networks) and on a weak VPS can put the bot into a restart loop (see `DOCKER.md` §3).
>
> ⚠️ **If `docker compose build` failed but you ran `up --force-recreate` anyway** — the container starts on the **old image**. The bot is alive but the new code isn't there; `/version` shows the old version. That's why step 4 verifies the version.
>
> 🔑 **`git pull` fails with `Authentication failed`** — the remote URL has a stale token:
> ```bash
> git remote set-url origin https://github.com/your-org/TelegramHelper.git
> git -c credential.helper= pull origin main   # will prompt for login and new PAT
> ```

---

### 1.0. How to Choose a Scenario (A or B)

The scenario is determined by **one question**: does the bot need to access 3x-ui via a mesh-only address (`100.64.x.x`)? If not — Scenario A. If yes — B.

**Quick test on the VPS:**

```bash
cat /opt/TelegramHelper/xui_config.json 2>/dev/null | jq -r '.url // "—"'
```

| Test result | Scenario | Why |
| --- | --- | --- |
| `—`, `null`, `{}` or file missing | **A — regular bridge** | 3x-ui integration not configured |
| URL with public IP/domain (`https://1.2.3.4:...`, `https://panel.example.com:...`) | **A — regular bridge** | bot can reach the panel via regular bridge network |
| URL with mesh IP (`https://100.64.x.x:...`) | **B — host-network** | Docker bridge cannot see the host's `tailscale0` interface |

Additional hints (if `xui_config.json` is ambiguous):

- Stack **without 3x-ui** (legacy VLESS via host `xray.service`, config in `/usr/local/etc/xray/config.json`) → always **A**, even if Headscale runs on the VPS for other purposes.
- 3x-ui panel installed locally (`127.0.0.1:8081`) and the bot needs to reach it via loopback — usually **A** (loopback is reachable from bridge via the host's public IP) or **B** (if 3x-ui binds only to `tailscale0`). Check `ss -tlnp | grep 8081` on the VPS.

**If 3x-ui is accessible both via mesh and publicly** — prefer **A** (host-network is a workaround, not a design choice: it loses Docker network isolation, requires sysctls on the host, and dockhand needs an alias via host-gateway). But verify that the public path is **intentionally open** (e.g., via nginx with auth) — not accidentally exposed past UFW.

---

### 1.1. Scenario A: Regular Docker Bridge

Suitable when:

- stack **without 3x-ui** (legacy VLESS via host `xray.service`);
- **3x-ui via public IP/domain** — bot can reach the panel from the regular Docker bridge;
- any other mode where the container **does not need** access to mesh-only addresses `100.64.x.x`.

**Full (rebuild everything, safe by default):**

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker compose build --no-cache telegram-helper dockhand
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
docker compose ps
docker compose logs --tail=80 telegram-helper
```

**Lightweight (bot only; faster, saves disk):**

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker compose build telegram-helper
docker compose up -d --force-recreate telegram-helper
docker compose ps
docker compose logs --tail=80 telegram-helper
```

**Which block to use:**

| What changed in `git pull` | Block |
| --- | --- |
| only `*.py`, `pyproject.toml`, `api.py` (bot code) | **lightweight** |
| only `*.md` / docs | no build needed (see intro) |
| `requirements.txt` (dependencies) | **full** (`--no-cache` required) |
| `dockhand/` | **full** |
| `compose*.yaml` | **full** |
| unexplained build errors / "stuck" cache | **full** (`--no-cache`) |

**If `docker compose build` fails on `apt-get` / `rclone`:**

```text
Temporary failure resolving 'deb.debian.org'
E: Unable to locate package rclone
```

This is the **build daemon's** DNS (BuildKit), not the host's. Use the host network workaround (already baked into `scripts/rebuild_bot.sh`, `scripts/vps_update.sh` and `scripts/install_telegramhelper_docker.sh`):

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker build --network=host -t telegram-helper-lite:latest .
docker compose up -d --force-recreate telegram-helper
docker compose logs --tail=40 telegram-helper
```

See §10 "Temporary failure resolving deb.debian.org" for details.

### 1.2. Scenario B: Host-Network for Mesh-Only 3x-ui

Use only when the **3x-ui panel is accessible only via a Tailscale/Headscale mesh IP** (`https://100.64.x.x:...`). Apply the host-network override:

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
# In host-network mode Docker cannot apply sysctls inside the container,
# so set the PMTU workaround on the host before starting.
sysctl -w net.ipv4.tcp_mtu_probing=2
sysctl -w net.ipv4.tcp_base_mss=1024
docker compose -f compose.yaml -f compose.host.yaml build --no-cache telegram-helper dockhand
docker compose -f compose.yaml -f compose.host.yaml up -d --force-recreate docker-socket-proxy telegram-helper dockhand
docker compose -f compose.yaml -f compose.host.yaml ps
docker compose -f compose.yaml -f compose.host.yaml exec dockhand getent hosts telegram-helper
docker compose -f compose.yaml -f compose.host.yaml logs --tail=80 telegram-helper
```

After deploying to a server with a local 3x-ui panel, check integration:

```text
/xui_status
/xui_setup   # if 3x-ui integration is not configured
/xui_list
```

For legacy VLESS without 3x-ui after `/provision <telegram_id>`, the bot applies the config and restarts Xray on its own. Verification:

```bash
systemctl status xray --no-pager
xray run -test -config /usr/local/etc/xray/config.json
journalctl -u xray -f --no-pager
```

In mesh mode, `telegram-helper` runs in `network_mode: host` while `dockhand` stays in the Docker bridge network. `dockhand` must be started with `compose.host.yaml`: this override adds the alias `telegram-helper:host-gateway`. Check: `getent hosts telegram-helper` should return the host-gateway IP. If Dockhand shows `API Unreachable: Failed to resolve 'telegram-helper'`, recreate dockhand with the same override:

```bash
cd /opt/TelegramHelper
docker compose -f compose.yaml -f compose.host.yaml up -d --force-recreate dockhand
docker compose -f compose.yaml -f compose.host.yaml exec dockhand getent hosts telegram-helper
docker compose -f compose.yaml -f compose.host.yaml logs --tail=80 dockhand
```

If startup fails with `sysctl "net.ipv4.tcp_mtu_probing" not allowed in host network namespace`, run `git pull origin main` to get the version that removes this sysctl from `compose.host.yaml`, then retry the block above.

Version check inside the container:

```bash
docker exec telegram-helper-lite grep -m1 '^version' /app/pyproject.toml
```

### 1.3. Legacy VLESS: Xray Restart Needed After `/provision`

If the server runs in **legacy VLESS mode without 3x-ui**, the bot updates `/usr/local/etc/xray/config.json` after `/provision <id>` or `/clean_user <id> YES` but does not manage the host systemd directly. A new URI may look correct but won't connect until Xray is restarted.

After changing VLESS clients, run on the VPS:

```bash
cd /opt/TelegramHelper

echo "=== verify UUID in the real Xray config ==="
grep -F "<UUID_from_vless_link>" /usr/local/etc/xray/config.json || echo "UUID NOT IN XRAY CONFIG"

echo "=== test config ==="
xray -test -config /usr/local/etc/xray/config.json

echo "=== restart Xray ==="
systemctl restart xray
systemctl status xray --no-pager

echo "=== who listens on 443 ==="
ss -tlnp | grep ':443'
```

---

## 2. Update Only the Bot

Depends on the deployment method: **systemd** (`telegramhelper.service`) or **docker-compose** (`telegram-helper` from `compose.yaml`). The easiest path: `bash scripts/vps_update.sh` (§0.1) — it auto-detects the method.

### 2a. Bot via systemd (`telegramhelper`)

```bash
export APP=/opt/TelegramHelper
cd "$APP"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
git pull origin main
# If dependencies changed (requirements.txt), rebuild venv (idempotent):
#   $SUDO bash scripts/install_telegramhelper_vps.sh
$SUDO systemctl restart telegramhelper
systemctl is-active telegramhelper
journalctl -u telegramhelper -n 30 --no-pager
```

> ⚠️ If the log shows `Is a directory: '<...>.json'` after restart — JSON configs became directories (created by `docker compose` if the file was missing; see §5). Fix (as root — no `sudo`):
> ```bash
> cd /opt/TelegramHelper
> for f in users.json hysteria2_config.json mtproto_config.json \
>          naiveproxy_config.json tuic_config.json anytls_config.json xhttp_config.json; do
>   [ -d "$f" ] && rm -rf "$f" && echo "removed dir: $f"
> done
> systemctl restart telegramhelper
> ```
> The bot will recreate them as files.

### 2b. Bot via docker-compose (`telegram-helper`)

When `dockhand/`, `compose.yaml`, Dockhand's Dockerfile and proxy settings haven't changed — one command:

```bash
cd /opt/TelegramHelper
bash scripts/rebuild_bot.sh --pull
```

Expanded:

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker compose build --no-cache telegram-helper
docker compose up -d --force-recreate telegram-helper
docker compose logs --tail=80 telegram-helper
# verify version:
docker exec telegram-helper-lite grep -E '^version' /app/pyproject.toml
```

---

## 3. Update Only Dockhand

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker compose build --no-cache dockhand
docker compose up -d --force-recreate docker-socket-proxy dockhand
docker compose ps
```

If the browser shows old UI — do `Ctrl+F5` and verify the container was recreated from the new image.

---

## 4. If 3x-ui Is Accessible Only via Tailscale/Headscale

See §1.2 for the full flow. Quick reference:

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker compose -f compose.yaml -f compose.host.yaml build --no-cache telegram-helper dockhand
docker compose -f compose.yaml -f compose.host.yaml up -d --force-recreate docker-socket-proxy telegram-helper dockhand
docker compose -f compose.yaml -f compose.host.yaml exec dockhand getent hosts telegram-helper
```

Return to regular bridge:

```bash
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
```

---

## 4a. Headplane (Web UI for Headscale)

Headplane is a separate container for managing the mesh network via a browser. Access is via SSH tunnel only (port `127.0.0.1:3000`).

### First Installation

Headscale must already be running (`docker ps | grep headscale`). Then:

```bash
cd /opt/TelegramHelper
git pull origin main
bash scripts/install_headplane.sh
```

The script is idempotent; re-running without `--force` reuses the existing `headplane/config.yaml`.

Access from client PC (SSH tunnel):

```bash
ssh -p YOUR_SSH_PORT -L 3000:127.0.0.1:3000 root@YOUR_VPS_IP
# Browser: http://127.0.0.1:3000/admin
```

Issue a short-lived login key:

```bash
docker exec headscale headscale apikeys create --expiration 24h
```

Verify in Telegram:

```text
/headscale_status
```

Should show `🟢 Headplane (Web UI): running`.

### Updating Headplane (after `git pull`)

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker compose -f compose.headplane.yaml pull
docker compose -f compose.headplane.yaml up -d --force-recreate
docker logs --tail=50 headplane
```

### Re-issue Secrets (if suspected leak)

```bash
bash scripts/install_headplane.sh --force
docker exec headscale headscale apikeys list
docker exec headscale headscale apikeys expire <old-key-prefix>
```

`--force` regenerates `cookie_secret` (existing browser sessions will be logged out — expected) and issues a new headscale API key.

### Backup

`headplane/config.yaml` (cookie_secret + headscale API key) and `headplane/data/` (Headplane internal DB) — back up together with `headscale/data/`. Without them Headplane will prompt for a new API key on recovery and lose its internal UI state.

### Remove

```bash
cd /opt/TelegramHelper
docker compose -f compose.headplane.yaml stop
docker compose -f compose.headplane.yaml rm -sf
rm -rf headplane/data headplane/config.yaml
sed -i '/^HEADSCALE_CONFIG_FILE=/d' .env
```

Headscale continues running; mesh clients notice nothing.

---

## 5. Bind-Mount Sanity: JSON Must Be a File, Not a Directory

Docker creates a **directory** if a `volumes:` entry points to a host file that doesn't yet exist. This produces errors like:

```text
[Errno 21] Is a directory: '/app/tuic_config.json'
[Errno 21] Is a directory: '/app/anytls_config.json'
```

Fix (safe to run at any time, idempotent):

```bash
export APP=/opt/TelegramHelper
cd "$APP"
for f in \
  vless_config.json hysteria2_config.json tuic_config.json anytls_config.json \
  xhttp_config.json mtproto_config.json headscale_config.json naiveproxy_config.json \
  mieru_config.json xui_config.json app_keys.json users.json; do
  [ -d "$f" ] && rmdir "$f"
  [ -f "$f" ] || echo '{}' > "$f"
done
[ -d bot.log ] && rmdir bot.log
[ -f bot.log ] || : > bot.log
[ -d .cli_history ] && rmdir .cli_history
[ -f .cli_history ] || : > .cli_history
chmod 600 .cli_history 2>/dev/null || true
ls -la *_config.json app_keys.json users.json bot.log .cli_history 2>/dev/null
```

All `ls` lines should start with `-rw-`, not `drwx`.

After fixing directories, rebuild without cache:

```bash
docker compose build --no-cache telegram-helper
docker compose up -d --force-recreate telegram-helper
```

---

## 6. `.env`: How to Update

`example.env` is the template. **Do not replace the server's entire `.env` with the template** — only add new variables.

Check mail-related variables without exposing secret JSON:

```bash
grep -E '^(GMAIL_|SMTP_)' .env
```

After changing `.env`, recreate the container:

```bash
docker compose up -d --force-recreate telegram-helper
```

If code or dependencies also changed — run `build --no-cache` first.

---

## 7. Gmail API for `/email_profile`

Detailed step-by-step guide: `EMAIL.md`.

### 7.1. Files on VPS

```text
/opt/TelegramHelper/gmail_oauth_client.json
/opt/TelegramHelper/gmail_token.json
```

Verify inside the container:

```bash
docker compose exec -T telegram-helper \
  python3 - <<'PY'
import os
for p in ("/app/gmail_oauth_client.json", "/app/gmail_token.json"):
    print(p, "exists=", os.path.isfile(p), "size=", os.path.getsize(p) if os.path.exists(p) else "-")
PY
```

If files exist but `/email_profile` says "no valid token", check the token:

```bash
docker compose exec -T telegram-helper python3 - <<'PY'
import json
from pathlib import Path
token = json.loads(Path("/app/gmail_token.json").read_text() or "{}")
client = json.loads(Path("/app/gmail_oauth_client.json").read_text() or "{}")
installed = client.get("installed") or client.get("web") or {}
scopes = token.get("scopes") or token.get("scope") or []
if isinstance(scopes, str):
    scopes = scopes.split()
print("has_refresh_token=", bool(token.get("refresh_token")))
print("expiry=", token.get("expiry") or "-")
print("has_gmail_send_scope=", "https://www.googleapis.com/auth/gmail.send" in scopes)
print("client_id_matches=", token.get("client_id") == installed.get("client_id"))
PY
```

If `refresh_failed` / `invalid_grant` — re-run the OAuth flow on a local PC and copy the new `gmail_token.json` to the VPS.

### 7.2. Docker Volumes

In `compose.yaml` under `telegram-helper`:

```yaml
volumes:
  - ./gmail_oauth_client.json:/app/gmail_oauth_client.json:ro
  - ./gmail_token.json:/app/gmail_token.json
```

### 7.3. `.env` for Gmail API

```env
GMAIL_OAUTH_CREDENTIALS=/app/gmail_oauth_client.json
GMAIL_TOKEN_PATH=/app/gmail_token.json
GMAIL_FROM=you@example.com
```

Do **not** set on VPS:

```env
GMAIL_OAUTH_ALLOW_LOCAL_SERVER=1
GMAIL_OAUTH_OPEN_BROWSER=1
```

These flags are for local PC only and cause `could not locate runnable browser` on VPS.

---

## 8. SMTP as an Alternative to Gmail API

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=starttls
SMTP_USER=you@example.com
SMTP_PASS=app_password_16_chars
SMTP_FROM=you@example.com
```

For Gmail, `SMTP_PASS` is an **app password**, not the account password. Two-factor authentication must be enabled.

Common error: `SMTP 535 Username and Password not accepted` — almost always means a regular password was used instead of an App Password, or the App Password is expired/revoked.

---

## 9. Logs and Diagnostics

Short bot log:

```bash
docker compose logs --tail=120 telegram-helper
```

Bot + Dockhand log:

```bash
docker compose logs --tail=120 telegram-helper dockhand docker-socket-proxy
```

Error filter (with `rg`):

```bash
docker compose logs --tail=300 telegram-helper dockhand docker-socket-proxy \
  | rg -i "error|exception|traceback|warning|\b4[0-9]{2}\b|\b5[0-9]{2}\b"
```

Without `rg`:

```bash
docker compose logs --tail=300 telegram-helper \
  | grep -E "ERROR|Exception|Traceback|WARNING| 4[0-9]{2} | 5[0-9]{2} "
```

Follow in real time:

```bash
docker compose logs -f telegram-helper dockhand docker-socket-proxy
```

---

## 10. Common Errors

### `could not locate runnable browser`

Interactive OAuth is enabled on the VPS. Remove from `.env`:

```env
GMAIL_OAUTH_ALLOW_LOCAL_SERVER=1
GMAIL_OAUTH_OPEN_BROWSER=1
```

Obtain `gmail_token.json` on a PC with a browser and copy it to the VPS.

### `Gmail API has not been used ... or it is disabled`

Go to **APIs & Services → Library → Gmail API → Enable** in Google Cloud. Wait 1–5 minutes and retry `/email_profile`.

### `403 access_denied`

Account is not in Test users. Go to **Google Auth Platform → Audience → Test users → Add users**.

### `Temporary failure resolving 'deb.debian.org'` / `Unable to locate package rclone`

BuildKit DNS issue. Build with the host network:

```bash
cd /opt/TelegramHelper
docker build --network=host -t telegram-helper-lite:latest .
docker compose up -d --force-recreate telegram-helper
```

### `Is a directory: '/app/..._config.json'`

Docker created a directory instead of a file. See §5, then rebuild without cache.

### `Tailscale node: tailscale up fails (key already used)`

Pre-auth keys are **single-use** by default. Create a new one (use `--reusable` for repeated use):

```bash
# on the COORDINATOR (--user = numeric ID from users list, NOT username):
docker exec headscale headscale users list
docker exec headscale headscale preauthkeys create --user 1 --reusable --expiration 24h
# on the client:
tailscale up --login-server https://headscale.example.com --authkey <NEW_KEY>
```

---

## 11. Automatic Disk Cleanup (One-Time Setup per VPS)

On small VPS (20 GB disk, no swap) `/var/lib/docker/overlay2` accumulates unused layers after each `docker compose build --no-cache`. Over a few weeks this can consume 5–10 GB and fill the disk to 100% — after which the bot and `docker compose` stop working.

Install the weekly maintenance timer:

```bash
cd /opt/TelegramHelper
./scripts/vps_maintenance.sh --status      # current disk/docker/journald state
./scripts/vps_maintenance.sh --install     # installs systemd service + timer
systemctl list-timers telegramhelper-maintenance.timer
```

What it cleans:

- `docker builder prune -af` — build cache;
- `docker image prune -af` — dangling images from rebuilds;
- `journalctl --vacuum-size=500M` + `SystemMaxUse=500M` in `journald.conf`.

What it **does not touch**: running containers, Docker volumes, `.env`, `app_keys.json`, `users.json`, `*_config.json`, `bot.log`.

### Emergency Cleanup If Disk Is Already at 95%+

```bash
docker image prune -af          # usually frees the most space
docker builder prune -af
truncate -s 0 /var/log/btmp.1   # if failed-login logs have grown
df -h /
docker system df
```

Then install the timer (`--install`) to prevent recurrence. Full diagnostics and complete cleanup — see `scripts/cleanup_server.sh`.

---

## 12. HA Stack (Stubs): Update After `git pull`

The stack lives in [`ha_stack/`](ha_stack/) and is installed by `scripts/install_ha_stack.sh` (see `DEPLOY.md` §6.7). After `git pull` on the VPS:

```bash
# If stub/bridge code changed (stub_server.py, udp_stub.py, ha_stack/bridge/...):
sudo systemctl restart ha-stub-grpc ha-stub-udp ha-reticulum-bridge

# If ha_stack/requirements.txt changed — update dependencies and restart:
ha_stack/.venv/bin/python -m pip install -r ha_stack/requirements.txt
sudo systemctl restart ha-stub-grpc ha-stub-udp ha-reticulum-bridge

# Clean reinstall (units + venv from scratch) — the installer is idempotent:
bash scripts/install_ha_stack.sh
```

Verify:

```bash
systemctl is-active ha-stub-grpc ha-stub-udp ha-reticulum-bridge
journalctl -u ha-reticulum-bridge -n 20 --no-pager | grep destination
```

> **The bridge destination hash is stable** across restarts and updates — identity is stored in `ha_stack/.rnsdata/bridge_identity` (not committed to git). Clients don't need to update `--bridge-hash`. Delete this file or recreate the venv from scratch — and the hash changes; update it in clients then. Recreate the venv only for major Python/protobuf version changes.

**I2P layer (path 2, if `scripts/install_i2p_bridge.sh` was run):** i2pd and the `ha-bridge` server tunnel are independent of `git pull` (they live in `/etc/i2pd/`, not in the repo). Management:

```bash
systemctl is-active i2pd
sudo systemctl restart i2pd
# b32 of the bridge (address for clients) — stable as long as ha-bridge.dat in i2pd datadir exists:
curl -s "http://127.0.0.1:7070/?page=i2p_tunnels" | sed 's/<[^>]*>/ /g' | grep -iE 'ha-bridge|\.b32'
# Reinstallation is idempotent:
bash scripts/install_i2p_bridge.sh
```

**Auto-recovery (soft hang):** set up automatically on fresh installs — `install_ha_stack.sh` (swap, `ha-rns-watchdog` = port + heartbeat + gRPC, handler timeout in bridge, weekly cron) and `install_i2p_bridge.sh` (weekly cron for i2pd). On an existing VPS:

```bash
cd /opt/TelegramHelper
git pull
bash scripts/upgrade_ha_rns_watchdog.sh   # watchdog + env + bridge restart
```

Full guides: HA stack (TCP, destination hash) — [`RETICULUM_GUIDE.md`](RETICULUM_GUIDE.md); I2P layer — [RETICULUM_GUIDE.md](RETICULUM_GUIDE.md).

---

## 13. Related Documents

- `DEPLOY.md` — initial installation.
- `DOCKER.md` — operations guide, troubleshooting, PMTU black hole.
- `EMAIL.md` — SMTP / Gmail setup.
- `DOCKHAND_GUIDE.md` — Dockhand setup, SSH tunnel, UI and variables.
- `HEADSCALE_GUIDE.md` — Headscale + Headplane Web UI, mesh-only 3x-ui and `compose.host.yaml`.
- `RETICULUM_GUIDE.md` — HA stack (stubs): TCP gRPC + Reticulum (RNS bridge).
