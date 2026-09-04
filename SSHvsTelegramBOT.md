# SSH vs Telegram Bot — Two Control Panels for the Server

A guide to managing your VPS and everything running on it in two ways — via the **Telegram bot** (the main, full-featured control panel) and via **SSH** (backup panel + things the bot can't do: docker, host scripts, emergency scenarios). With step-by-step examples and a summary table at the end.

> **Bot deployment: Docker or systemd?** The SSH dashboard `./scripts/bot_cli.sh` works in **both** cases. For a native bot use **`BOT_NATIVE=1 ./scripts/bot_cli.sh`**.  
> Details: [§1.1](#11--bot_clish--docker-or-native-systemd).

Related documents: [DEPLOY.md](DEPLOY.md) (installation and operations), [POST_DEPLOY.md](POST_DEPLOY.md), the `scripts/` directory, [DOCKER.md](DOCKER.md), [HEADSCALE_GUIDE.md](HEADSCALE_GUIDE.md), [HEADSCALE_GUIDE.md](HEADSCALE_GUIDE.md), [SSHvsTelegramBOT.md](SSHvsTelegramBOT.md).

---

## 1. The Full Picture

```
┌──────────────────────── your PC / phone ────────────────────────┐
│                                                                 │
│  Telegram app                        terminal (ssh)             │
│        │                                   │                   │
└────────┼───────────────────────────────────┼───────────────────┘
         │ long-polling                      │ SSH (port YOUR_SSH_PORT)
         ▼                                   ▼
┌──────────────────────────── VPS ──────────────────────────────────┐
│                                                                   │
│  bot: container telegram-helper-lite     host                     │
│  OR native (systemd telegramhelper)        ├─ scripts/bot_cli.sh ─┼─► docker exec
│  ├─ bot.py  (~230 commands)               │   (container) OR venv │   OR host-venv
│  ├─ api.py  (REST :8000)                  ├─ scripts/exit_node.sh │
│  └─ cli_dashboard.py ◄────────────────────┤ docker compose / systemctl
│  managed services: VLESS-Reality, 3x-ui, Hysteria2, NaiveProxy,  │
│  TUIC, AnyTLS, XHTTP, Mieru, MTProto, Xray, Nginx SNI, Headscale │
│  (+exit node), rclone backups, API/encryption keys, users        │
└───────────────────────────────────────────────────────────────────┘
```

**Key difference.** The bot is the full control panel: all ~230 commands across all services. The SSH dashboard (`cli_dashboard.py` via `AdminCLI`) is a lightweight mirror: 25 most-needed commands (statuses, exit node, VLESS on/off, users, profile links and QR codes right in the terminal, backups, keys). Only via SSH can you access docker, rebuilds, container logs, host systemd services, and emergency scripts.

The CLI has no concept of "who is writing", so **SSH login is treated as admin**: commands without an explicit Telegram ID (e.g. `/links`) default to admin profiles (the first entry in `ADMIN_USER_IDS`).

**When to use which:**

| Situation | Panel |
| --- | --- |
| Everyday use: clients, profiles, statuses, backups | Telegram bot |
| Quickly grab profile links without Telegram | SSH → `./scripts/bot_cli.sh /links` (see §1.1) |
| Telegram unreachable, bot on VPS alive (Docker **or** systemd) | SSH → `./scripts/bot_cli.sh` (see §1.1) |
| Bot is native (systemd), but a dead Docker container exists on VPS | SSH → `BOT_NATIVE=1 ./scripts/bot_cli.sh` (see §1.1) |
| Bot container crashed (Docker deployment) | SSH → `docker compose up -d --force-recreate telegram-helper` |
| Code update / rebuild / logs / disk | SSH only (§3.4) |
| Fine-tuning proxy protocols (set_*, gen_*, QR, export) | Telegram bot only |

---

## 1.1. ⚡ `bot_cli.sh` — Docker **or** Native systemd

> **Key point.** The SSH dashboard (`./scripts/bot_cli.sh`) is **not** "Docker only". It works whether the bot runs **natively via systemd** (`telegramhelper`) or in a container (`telegram-helper-lite`). Commands are the same — only **from where** `cli_dashboard.py` runs changes.

### How to tell which deployment you have

```bash
cd /opt/TelegramHelper

# Option A — Docker
docker ps --format '{{.Names}}\t{{.Status}}' | grep telegram-helper

# Option B — native systemd (no Docker bot)
systemctl is-active telegramhelper 2>/dev/null || systemctl list-units --type=service | grep -i telegram
```

| What you see | Deployment | How to start the dashboard |
| --- | --- | --- |
| `telegram-helper-lite` in `docker ps` (Up) | **Docker** | `./scripts/bot_cli.sh` |
| No container, `telegramhelper` active | **Native systemd** | `./scripts/bot_cli.sh` (auto) or `BOT_NATIVE=1 ./scripts/bot_cli.sh` |
| Container exists but **not Up**, bot is **native** | Mixed / old Docker artifact | **`BOT_NATIVE=1 ./scripts/bot_cli.sh`** |
| Container exists but **not Up**, bot **should** be Docker | Docker, bot crashed | Bring up the container (§3.4), then `./scripts/bot_cli.sh` |

### Native Bot (systemd) — Main Commands

```bash
cd /opt/TelegramHelper

# Interactive menu (the "TelegramHelper — CLI Dashboard" you know)
BOT_NATIVE=1 ./scripts/bot_cli.sh

# Single command without menu
BOT_NATIVE=1 ./scripts/bot_cli.sh /info
BOT_NATIVE=1 ./scripts/bot_cli.sh /vless_status
BOT_NATIVE=1 ./scripts/bot_cli.sh /links
BOT_NATIVE=1 ./scripts/bot_cli.sh /reticulum_status
```

If the venv is not in the standard location — use the same Python as the systemd unit:

```bash
systemctl cat telegramhelper | grep -i ExecStart
BOT_NATIVE=1 BOT_PYTHON=/opt/TelegramHelper/venv/bin/python ./scripts/bot_cli.sh
```

Direct equivalent without the launcher:

```bash
cd /opt/TelegramHelper
source venv/bin/activate    # or .venv/bin/activate
python cli_dashboard.py     # menu
python cli_dashboard.py /info
```

### Docker Bot — Same Commands, No `BOT_NATIVE`

```bash
cd /opt/TelegramHelper
./scripts/bot_cli.sh              # docker exec → cli_dashboard.py inside container
./scripts/bot_cli.sh /vless_status
```

### Why "Container … not running" Appears for Native Bot

`scripts/bot_cli.sh` first checks: **does** the container `telegram-helper-lite` exist?  
If it **exists but is not running**, the script assumes Docker deployment and asks you to bring up the container — **even if** the real bot is running via systemd.

**Solution for native bot on such a VPS:**

```bash
BOT_NATIVE=1 ./scripts/bot_cli.sh
```

There is no need to start a dead container just for the dashboard: `AdminCLI` on the host calls the same managers directly and **does not require** the bot process to be alive.

### Launcher Variables (Cheatsheet)

| Variable | When |
| --- | --- |
| `BOT_NATIVE=1` | Force run on the **host** (systemd deployment or dead container) |
| `BOT_PYTHON=/path/to/python` | Explicit interpreter (matching the `ExecStart` in `telegramhelper`) |
| `BOT_CONTAINER=name` | Different container name (default `telegram-helper-lite`) |

### Bot Status and Logs (Native vs Docker)

```bash
# Native systemd
systemctl status telegramhelper --no-pager
journalctl -u telegramhelper -n 80 --no-pager

# Docker
docker compose ps telegram-helper
docker compose logs --tail=80 telegram-helper
```

---

## 2. Managing via the Telegram Bot

### 2.1. Getting Started

```
/start          ← welcome + role-aware panel with buttons
/help           ← interactive help by sections
/settings       ← theme (Classic/Minimal/Neon), compact mode
/info           ← your role + server summary
/ver            ← version, VPS address, VLESS summary
/diag           ← diagnostics (admin sees the full report)
```

The Telegram bottom menu shows priority commands (Telegram limit: 100 per scope); the rest are entered manually or via `/help` panels.

### 2.2. Typical Admin Day — Task Examples

**Status of all proxy services (one per protocol):**

```
/vless_status     /xui_status      /hy2_status      /naive_status
/tuic_status      /anytls_status   /xhttp_status    /mieru_status
/mt_status        /xray_status     /nginx_status    /headscale_status
```

**Give a user access (3x-ui, bot-managed):**

```
/xui_status                  ← integration configured?
/provision 123456789         ← create clients for TG-ID
/user 123456789              ← user card: profiles, QR, rotation
/email_profile 123456789     ← send profiles to linked email
```

**Legacy VLESS-Reality (host xray.service):**

```
/vless_status                ← state and keys
/vless_list_clients          ← who is connected
/vless_add_client name       ← add client
/vless_qr                    ← QR for import
/vless_export                ← client config
/vless_set_port 8443         ← change port (then sync on host, see §3.4)
```

**Exit to internet via VPS (exit node):**

```
/exit_node                   ← status + per-device guide
/exit_node_on                ← advertise + approve (admin only)
/exit_node_off               ← disable
```

**Backups (rclone, offsite):**

```
/backup_status → /backup_test → /backup_now → /backup_list
```

**Keys and users:**

```
/api               ← masked API key
/encryption_key    ← encryption keys per app
/gen_api_key app   ← generate key for an app
/list_users        ← bot users
/special_add 123   ← extended rights (special)
```

**Headscale (mesh network):**

```
/headscale             ← Tailscale IP of this host
/headscale_status      ← server: status, nodes, Headplane UI
/headscale_list_nodes  ← node list
/headscale_gen         ← Pre-Auth key for a new device
```

### 2.3. What the Bot CANNOT Do

Rebuilding/restarting containers, docker logs, disk operations, `git pull` — all SSH-only (§3.4–3.6). The bot can disable itself (`/disable_bot` exists in AdminCLI/API, but Telegram control is not exposed — otherwise you could saw off the branch you're sitting on).

---

## 3. Managing via SSH

### 3.1. Connect

SSH on this server listens on **non-standard port YOUR_SSH_PORT** (not 22).

```bash
ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP
cd /opt/TelegramHelper        # project working directory on the server
```

### 3.2. CLI Dashboard — "Bot in the Terminal"

> **Read [§1.1](#11--bot_clish--docker-or-native-systemd) first** — it covers: Docker vs systemd, `BOT_NATIVE=1`, and the typical "container not running" error for native bots.

Scenario: Telegram is unreachable (e.g. VPN is needed to reach Telegram). The dashboard runs the same code as the bot (`AdminCLI`) and **does not depend on the bot process being alive** — it calls managers directly.

**Quick reference:**

| Deployment | Command |
| --- | --- |
| Docker, container **Up** | `./scripts/bot_cli.sh` |
| **systemd** (`telegramhelper`) | `BOT_NATIVE=1 ./scripts/bot_cli.sh` |
| Container exists but **Stopped**, bot is native | `BOT_NATIVE=1 ./scripts/bot_cli.sh` |

**All launch methods:**

```bash
# Interactive menu (rich): select items by number
./scripts/bot_cli.sh

# One-shot: one command, then exit (clean text, pipe-friendly)
./scripts/bot_cli.sh /help                  # list all CLI commands
./scripts/bot_cli.sh /info                  # command without arguments
./scripts/bot_cli.sh /links 123456789       # command with arguments
./scripts/bot_cli.sh /vless_set_port 8443
./scripts/bot_cli.sh /reticulum_status      # Reticulum/HA stack + I2P status
./scripts/bot_cli.sh /reticulum_health      # i2pd: network / success / leasesets
./scripts/bot_cli.sh /reticulum_restart     # soft hang recovery (service active, client silent)
./scripts/bot_cli.sh /exit_node_on          # ⚠ commands in one-shot execute
                                            # IMMEDIATELY without confirmation

# Launcher environment variables
BOT_CONTAINER=other-name ./scripts/bot_cli.sh   # different container name
                                                # (default: telegram-helper-lite)
BOT_NATIVE=1 ./scripts/bot_cli.sh               # force run on host (venv)
BOT_PYTHON=/path/to/python ./scripts/bot_cli.sh # explicit interpreter for host
```

**Inside the interactive menu:**

| Input | Action |
| --- | --- |
| `1`…`24` (item number) | run command |
| `/command [args]` | run directly, bypassing numbers (e.g. `/links 123456789`) |
| `m` (or `h`) | redraw menu |
| `q` (or `0`, Ctrl+C, Ctrl+D) | exit |
| ⚠ next to an item | command will ask `y/n` confirmation before running |
| Enter on an argument prompt | for optional args (TG-ID in `/links`) — use default; for required args (port in `/vless_set_port`) — cancel |

**Full list of available commands (7 sections, 25 commands):**

```bash
# 🔧 System
./scripts/bot_cli.sh /info            # hostname, OS, Python, Docker
./scripts/bot_cli.sh /ver             # version + VLESS summary
./scripts/bot_cli.sh /dockhand        # ready SSH tunnel command for Dockhand
./scripts/bot_cli.sh /headscale       # Tailscale IP of this host

# 🤖 Bot
./scripts/bot_cli.sh /bot_status      # is the Telegram bot enabled?
./scripts/bot_cli.sh /enable_bot      # enable (uncomments BOT_TOKEN + restart)
./scripts/bot_cli.sh /disable_bot     # disable (API stays alive)

# 🌐 Exit node
./scripts/bot_cli.sh /exit_node       # status + per-device guide
./scripts/bot_cli.sh /exit_node_on    # advertise + forwarding + approve
./scripts/bot_cli.sh /exit_node_off   # disable

# 🛡️ VLESS-Reality
./scripts/bot_cli.sh /vless_status
./scripts/bot_cli.sh /vless_config        # keys masked
./scripts/bot_cli.sh /vless_link          # vless:// link for client
./scripts/bot_cli.sh /vless_on
./scripts/bot_cli.sh /vless_off
./scripts/bot_cli.sh /vless_set_port 8443 # remember: restart xray + firewall

# 👤 User profiles
./scripts/bot_cli.sh /list_users      # all bot users with Telegram IDs
./scripts/bot_cli.sh /links           # without ID = ADMIN profiles (SSH login
                                      # is treated as admin): VLESS, Hysteria2,
                                      # MTProto — same as /my_profile in bot, no QR
./scripts/bot_cli.sh /links 123456789 # same for a specific user;
                                      # get ID from /list_users
./scripts/bot_cli.sh /qr              # QR code DIRECTLY IN TERMINAL (unicode
                                      # half-blocks): without args — list of
                                      # available variants for admin
./scripts/bot_cli.sh /qr vless        # QR for current VLESS link
./scripts/bot_cli.sh /qr 123456789 hy2  # QR for user's Hysteria2

# 🗄️ Backups
./scripts/bot_cli.sh /backup_status
./scripts/bot_cli.sh /backup_list
./scripts/bot_cli.sh /backup_test
./scripts/bot_cli.sh /backup_now

# 🔑 Keys (masked)
./scripts/bot_cli.sh /api
./scripts/bot_cli.sh /encryption_key
```

One-shot output is clean text without frames, so commands work well in pipes:

```bash
./scripts/bot_cli.sh /vless_status | grep State
./scripts/bot_cli.sh /list_users | grep 🕒          # who logged in recently
./scripts/bot_cli.sh /links > my_links.txt          # save links to file
```

### 3.3. Exit Node Without Container (Break-Glass)

Scenario "chicken-and-egg": Telegram is accessible only via VPN, and VPN (exit node) is not yet up, and/or the bot container is down. The script runs directly on the host:

```bash
sudo ./scripts/exit_node.sh status    # advertise/approve/forwarding
sudo ./scripts/exit_node.sh on        # make VPS an exit node
sudo ./scripts/exit_node.sh off
```

After `on` — on the device (iPhone/PC in the Tailscale client) select this node as exit node; verify: `https://ifconfig.me` should show the VPS IP.

### 3.4. Updating and Restarting the Bot — Scenario A, Full Block

> The method depends on the bot deployment. To check: is there a bot container?  
> `docker ps -a --format '{{.Names}}' | grep telegram-helper` → yes = Docker, no = native systemd (`systemctl status telegramhelper`).  
> **Easiest: `bash scripts/vps_update.sh`** (POST_DEPLOY.md §0.1): auto-detects Docker/native and updates correctly. Manual alternatives below.

**Docker deployment** (canonical; see POST_DEPLOY.md §1.1 — also has the lightweight code-only variant):

```bash
export APP=/opt/TelegramHelper
cd "$APP"
git pull origin main
docker compose build --no-cache telegram-helper dockhand
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
docker compose ps
docker compose logs --tail=80 telegram-helper
```

**Native deployment** (bot as systemd service `telegramhelper`, no Docker; POST_DEPLOY.md §2a):

```bash
export APP=/opt/TelegramHelper
cd "$APP"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
git pull origin main
# If dependencies changed (requirements.txt) — rebuild venv (idempotent):
#   $SUDO bash scripts/install_telegramhelper_vps.sh
$SUDO systemctl restart telegramhelper
systemctl is-active telegramhelper
journalctl -u telegramhelper -n 80 --no-pager
```

> ⚠️ **Anti-patterns from DOCKER.md:**
> - NO `docker compose down` to restart — only `up -d --force-recreate <service>`;
> - NO lowering the Docker bridge MTU below 1500;
> - NO chaining multiple recreates under load (small RAM, swap thrashing locks SSH).
> - Host in Scenario B (mesh-only 3x-ui) deploys differently:
>   `docker compose -f compose.yaml -f compose.host.yaml ...` (see POST_DEPLOY.md §1.2).

### 3.5. Diagnostics and Logs

**Native bot (systemd):**

```bash
systemctl status telegramhelper --no-pager
journalctl -u telegramhelper -n 50 --no-pager
journalctl -u telegramhelper -f
python3 scripts/bot_status.py
```

**Docker bot:**

```bash
docker compose ps                                  # what's running
docker logs telegram-helper-lite --tail 50         # bot logs
docker logs telegram-helper-lite -f                # follow
```

**Common (host proxy services):**

```bash
journalctl -u xray -n 50                           # host-Xray logs (legacy VLESS)
systemctl status xray hysteria-server 2>/dev/null  # host systemd services
```

### 3.6. Other Host Scripts (details — scripts/)

```bash
sudo ./scripts/vless_sync.sh              # sync vless_config.json → Xray
python3 scripts/sync_xray_config.py       # same from Python (after /vless_set_port)
./scripts/show_version.py                 # project version
python3 scripts/show_keys.py              # app keys (local, masked)
sudo ./scripts/vps_maintenance.sh         # VPS maintenance
sudo ./scripts/cleanup_server.sh          # disk cleanup (see scripts/cleanup_server.sh)
./scripts/restore_bot.sh                  # restore bot from backup
./scripts/change_token.sh                 # change BOT_TOKEN
```

### 3.7. Dockhand — Docker Web Diagnostics via SSH Tunnel

```bash
# The hint with a ready command is given by /dockhand (both in bot and dashboard) —
# it substitutes the actual host and SSH port
ssh -L 8501:127.0.0.1:8501 -p YOUR_SSH_PORT root@YOUR_VPS_IP  # then open http://localhost:8501
```

---

## 4. Summary Table: Services × Management Methods

Legend: ✅ full · 🔶 partial (main operations) · ❌ none ·  
**TG** — Telegram bot · **CLI** — `scripts/bot_cli.sh` (dashboard) · **SSH** — host commands/scripts.

| Service / area | TG | CLI | SSH (host) | Key commands |
| --- | :-: | :-: | :-: | --- |
| Server status (`/info`, `/ver`, `/diag`) | ✅ | 🔶 | ✅ | TG: `/info /ver /diag` · CLI: `/info /ver` · SSH: `htop`, `df -h` |
| Telegram bot itself (enable/disable/status) | ❌ | ✅ | ✅ | CLI: `/bot_status /enable_bot /disable_bot` · SSH: §3.4, `bot_status.py` |
| Containers / rebuild / logs | ❌ | ❌ | ✅ | SSH: §3.4 (Scenario A full), `docker logs` |
| Exit node (internet via VPS) | ✅ | ✅ | ✅ | all: `/exit_node[_on/_off]` · SSH break-glass: `exit_node.sh` |
| Headscale (mesh: nodes, keys, users) | ✅ | 🔶 | 🔶 | TG: `/headscale_status /headscale_gen /headscale_list_nodes` · CLI: `/headscale` (IP) · SSH: `docker exec headscale ...` |
| VLESS-Reality (legacy, host Xray) | ✅ | 🔶 | 🔶 | TG: 20 `/vless_*` commands · CLI: status/config/link/on/off/set_port · SSH: `vless_sync.sh`, `systemctl restart xray` |
| 3x-ui (bot-managed clients) | ✅ | ❌ | ❌ | TG: `/xui_* /provision /user /profiles /clean_user` |
| Hysteria2 | ✅ | ❌ | 🔶 | TG: 28 `/hy2_*` commands · SSH: `install_hysteria2.sh`, systemctl |
| NaiveProxy | ✅ | ❌ | 🔶 | TG: `/naive_*` · SSH: `install_naiveproxy.sh` |
| TUIC | ✅ | ❌ | ❌ | TG: `/tuic_*` (status, clients, QR, export) |
| AnyTLS | ✅ | ❌ | ❌ | TG: `/anytls_*` |
| XHTTP | ✅ | ❌ | ❌ | TG: `/xhttp_*` |
| Mieru | ✅ | ❌ | 🔶 | TG: `/mieru_*` · SSH: `install_mieru.sh` |
| MTProto proxy | ✅ | ❌ | 🔶 | TG: `/mt_*` · SSH: `install_mtproto.sh`, `mtproto_sync_systemd.py` |
| Xray (engine) | ✅ | ❌ | ✅ | TG: `/xray_status /xray_restart /xray_logs` · SSH: systemctl, journalctl |
| Nginx SNI routing | ✅ | ❌ | 🔶 | TG: `/nginx_*` · SSH: `scripts/nginx/` |
| Backups (rclone, offsite) | ✅ | ✅ | 🔶 | TG/CLI: `/backup_status /backup_test /backup_now /backup_list` · SSH: RCLONE_VPS.md |
| API / encryption keys | ✅ | 🔶 | 🔶 | TG: `/api /encryption_key /gen_* /del_*` · CLI: `/api /encryption_key` (masked) · SSH: `show_keys.py` |
| Users and profiles | ✅ | 🔶 | ❌ | TG: `/list_users /user /special_* /setemail /email_profile /my_profile` · CLI: `/list_users`, `/links [uid]` (no uid = admin), `/qr [uid] <variant>` |
| AI queries (Anthropic/OpenAI) | ✅ | ❌ | ❌ | TG: regular messages, `/ai_provider /ch_model` |
| Dockhand (Docker web diagnostics) | 🔶 | 🔶 | ✅ | TG/CLI: `/dockhand` (tunnel hint) · SSH: tunnel :8501 |
| VPS maintenance (disk, cleanup) | ❌ | ❌ | ✅ | SSH: `vps_maintenance.sh`, `cleanup_server.sh` |

### Three Panels — Summary

| Panel | Commands | What it does | When it doesn't work |
| --- | --- | --- | --- |
| Telegram bot | ~230 | all service and client management | Telegram blocked/needs VPN; container down |
| `bot_cli.sh` (SSH: Docker **or** host venv) | 25 | statuses, exit node, VLESS, Reticulum, users, links/QR, backups, keys | no venv on host; Docker: container down **and** `BOT_NATIVE=1` not used |
| SSH host | unlimited | docker, git, systemd, disk, break-glass exit node | no network/SSH (see DOCKER.md recovery) |

---

## 5. Emergency Ladder (Best to Worst)

1. **Bot responds** → work in Telegram, SSH not needed.
2. **Bot silent, SSH works** → dashboard:
   - Docker (`telegram-helper-lite` Up): `./scripts/bot_cli.sh`
   - **systemd** (`telegramhelper`) or dead container with native bot: `BOT_NATIVE=1 ./scripts/bot_cli.sh` → `/bot_status`, `/exit_node`, …
3. **Docker bot, container down** → §3.4 (`up -d --force-recreate telegram-helper`);
   if Telegram needs VPN first — `sudo ./scripts/exit_node.sh on`.
4. **Native bot, systemd crashed** → `systemctl restart telegramhelper`,
   `journalctl -u telegramhelper -n 50`; dashboard still works: `BOT_NATIVE=1 ./scripts/bot_cli.sh`.
5. **SSH unreachable** → DOCKER.md, recovery section (provider console, swap thrashing, PMTU).
