# Headscale Guide

> Self-hosted Tailscale coordinator for your mesh network.

---

## TL;DR — Quick Start

**Headscale is already running** on the VPS. To add a **Web UI (Headplane)** and connect devices:

### 1. On the VPS

```bash
cd /opt/TelegramHelper
git pull origin main
bash scripts/install_headplane.sh
```

### 2. From Windows / any PC

```bash
ssh -p YOUR_SSH_PORT -L 3000:127.0.0.1:3000 root@YOUR_VPS_IP
```

Open in browser: **http://127.0.0.1:3000/admin**

Issue a short-lived login key:

```bash
docker exec headscale headscale apikeys create --expiration 24h
```

### 3. Verify

In Telegram: `/headscale_status` — the output should include  
`🟢 Headplane (Web UI): running` with the URL and SSH tunnel hint.

Full details in [Web UI via Headplane](#web-ui-via-headplane) below.

---

## What is Headscale

**Headscale** is an open-source implementation of the Tailscale control plane (coordinator). It lets you build a private mesh network between devices without the Tailscale cloud service.

### Why you need it

- Access **Home Assistant** from anywhere without port forwarding
- Mesh network between devices (laptop, phone, server, home)
- P2P WireGuard connections (Tailscale protocol)
- Works alongside a VPN (sing-box) via **Mesh Bypass**

### Architecture

```
┌─────────────────┐         ┌──────────────────────────────┐
│  Device 1       │         │  VPS                         │
│  (Tailscale)    │◄──WG──► │  Headscale coordinator :8080 │
└─────────────────┘         │  ├── Users                   │
                            │  ├── Pre-Auth Keys           │
┌─────────────────┐         │  └── Node registry           │
│  Device 2       │◄──WG──► │                              │
│  (Tailscale)    │         │  Accessible via Nginx SNI    │
└─────────────────┘         │  (headscale.example.com → :8080) │
        │                   └──────────────────────────────┘
        │ P2P (direct)
        ▼
┌─────────────────┐
│  Home (grey IP) │
│  ├── HA :8123   │
│  ├── Tailscale  │
│  └── IoT        │
└─────────────────┘
```

Devices connect to the coordinator to exchange keys, then establish direct WireGuard tunnels to each other.

---

## Why This Works Under DPI

The setup does **not** use raw WireGuard. The Tailscale stack has two independent planes:

**Control plane** (coordination, registration, key exchange):
- Transport: HTTPS/443 TCP to `headscale.example.com`
- What DPI sees: a normal TLS handshake to your domain. The WireGuard pattern is absent.

**Data plane** (P2P traffic between peers):
- Transport: (1) direct UDP WireGuard on a random port → (2) fallback to DERP relay (TLS WebSocket/443)
- What DPI sees: either UDP packets (if direct WG passes) or indistinguishable from HTTPS.

**Key points:**

1. **Control plane is invisible** — routed through Nginx SNI routing on your own domain on port 443, shared with Xray. No WireGuard pattern for DPI to find.
2. **DERP fallback is automatic** — if TSPU starts blocking WireGuard handshakes, Tailscale switches to a DERP relay (~5 seconds). All traffic continues as TLS-WebSocket on TCP/443.
3. **Random UDP port** — Tailscale does not listen on 51820 by default; the port is ephemeral (e.g. `14649`). Port-based blocking doesn't work.
4. **Your own coordinator** — `login.tailscale.com` may end up on block lists; your `headscale.example.com` is clean.

### Check your path

```bash
tailscale netcheck      # UDP: true/false, nearest DERP
tailscale status        # peer list
tailscale ping <ip>     # shows via <ip>:<port> (direct) or via DERP(xxx) (relay)
```

Example — direct P2P (WireGuard UDP passes):

```text
pong from your-vps (100.64.0.6) via YOUR_VPS_IP:41641 in 83ms
```

Example — fallback (UDP blocked, routed via TLS/443):

```text
pong from your-vps (100.64.0.6) via DERP(nue) in 68ms
```

---

## Via the `vps_setup.py` Wizard (Roles: Coordinator / Client)

`bash scripts/vps_setup.sh` asks for **the role of this VPS in the mesh** during the Headscale step (default: `client`):

- **coordinator** — this VPS becomes the Headscale server. The wizard creates `headscale/config` + `headscale/data`, downloads `config-example.yaml`, sets `server_url` (prompted) and `listen_addr 0.0.0.0:8080`, then starts **only** the `headscale` service (not the whole compose stack), then Headplane.
- **client** — this VPS is just a node: installs `tailscale` and joins an **existing** coordinator. The wizard asks for two values and runs `tailscale up --login-server <url> --authkey <key>`:
  - `--login-server` — public URL of the coordinator (its `server_url`, e.g. `https://headscale.example.com`);
  - **pre-auth key** — a one-time key issued on the coordinator.

### Getting `login-server` and a pre-auth key (on the coordinator server)

SSH into the **coordinator** and run:

```bash
# 1) server_url (= --login-server). Find the config path via the mount:
docker inspect headscale --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
grep -E '^server_url' /etc/headscale/config.yaml   # or ./headscale/config/config.yaml

# 2) pre-auth key. --user / -u takes ONLY a numeric ID (uint), NOT a username.
docker exec headscale headscale users list
# Example output:  ID | … | Username | …  → 1 | … | main_user | …
docker exec headscale headscale preauthkeys create --user 1 --reusable --expiration 24h
```

> ⚠️ Always substitute the numeric **ID** from the `users list` output. Shell interprets `<user>` as file redirection.

Verify the node connected (on the coordinator):

```bash
docker exec headscale headscale nodes list
```

> ⚠️ **External clients and public HTTPS.** The coordinator branch starts headscale on `127.0.0.1:8080`. For clients **from the internet** to connect, a public HTTPS reverse proxy/Nginx SNI is required (see [Nginx SNI Routing](#nginx-sni-routing)). A simple `up -d headscale` from the wizard is fine for a trusted network or behind a pre-configured proxy.

---

## Installation on VPS

> The manual steps below are equivalent to the **coordinator** wizard branch — useful for advanced setup or standalone Headscale installation.

### 1. Docker Compose

```bash
cd /opt/TelegramHelper
mkdir -p headscale/config headscale/data

curl -sL https://raw.githubusercontent.com/juanfont/headscale/main/config-example.yaml \
  -o headscale/config/config.yaml
```

### 2. Configure

Edit `headscale/config/config.yaml`:

```yaml
server_url: https://headscale.example.com
listen_addr: 0.0.0.0:8080
private_key_path: /var/lib/headscale/private.key
noise:
  private_key_path: /var/lib/headscale/noise_private.key
ip_prefixes:
  - 100.64.0.0/10
  - fd7a:115c:a1e0::/48
```

### 3. Start

```bash
docker compose -f compose.yaml -f compose.headscale.yaml up -d

docker exec headscale headscale users create main_user
```

### 4. Verify

```bash
docker ps | grep headscale
docker exec headscale headscale nodes list
```

---

## TLS Certificate: Checking Expiry and Renewal

**If the certificate expires — the entire mesh network stops connecting.** Clients show:  
`You are logged out. The last login error was: … x509: certificate has expired`.

### Check days remaining (from any computer, without SSH)

```bash
echo | openssl s_client -connect headscale.example.com:443 \
  -servername headscale.example.com 2>/dev/null | openssl x509 -noout -dates
```

`notAfter` is the expiry date.

### On the VPS: certbot status

```bash
certbot certificates            # all certificates and expiry dates
systemctl list-timers | grep certbot   # is the auto-renewal timer alive?
```

### Auto-renewal with hooks

Keep port 80 closed in UFW by default. Configure certbot to open it temporarily during Let's Encrypt challenges via hooks in `/etc/letsencrypt/renewal/your-domain.conf`:

```ini
pre_hook = ufw allow 80/tcp
post_hook = ufw delete allow 80/tcp; systemctl reload nginx
```

Test dry-run (hooks actually execute):

```bash
certbot renew --cert-name your-domain.com --dry-run
ufw status | grep -w 80 || echo "port 80 closed"
```

### Manual renewal

```bash
certbot renew --cert-name your-domain.com
echo | openssl s_client -connect localhost:443 2>/dev/null | openssl x509 -noout -dates
```

---

## Bot Management Commands

| Command | Description |
|---------|-------------|
| `/headscale_status` | Container status, URL, node count |
| `/headscale_enable` | Enable Headscale in bot config |
| `/headscale_disable` | Disable |
| `/headscale_set_url <url>` | Set coordinator URL |
| `/headscale_gen` | Generate a Pre-Auth key |
| `/headscale_list_nodes` | List connected devices |
| `/headscale_create_user <name>` | Create a user |
| `/exit_node` | Exit node status + per-device guide (admin + special) |
| `/exit_node_on` | Make this VPS an exit node (admin only) |
| `/exit_node_off` | Disable exit node (admin only) |
| `scripts/exit_node.sh on\|off\|status` | **Break-glass via SSH** — when Telegram is accessible only via VPN |

---

## Exit Node — Internet Traffic via the VPS

Any tailnet node (phone, laptop) can route **all its internet traffic** through a chosen exit node. The VPS running Headscale is a natural exit node — devices in roaming or on public Wi-Fi exit the internet "from" your VPS.

### ⚠️ Important limitation

Choosing "which exit node to use" is a **local setting in the Tailscale app on the device itself**. Neither Headscale nor the Tailscale API can remotely enable an exit node on someone else's phone. Therefore:

- **The bot handles the server side** — advertises the VPS as an exit node and approves routes;
- **The final tap is on the device** (once; Tailscale remembers the choice).

### One-time on the host: persistent IP forwarding

```bash
cat <<'EOF' | sudo tee /etc/sysctl.d/99-tailscale-exit.conf
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sudo sysctl -p /etc/sysctl.d/99-tailscale-exit.conf
```

Tailscale automatically inserts `ts-forward` (accept) and `ts-postrouting` (NAT/masquerade) chains above UFW. Verify: `sudo iptables -t nat -S ts-postrouting | grep -i masq`.

### Enable via bot (admin)

```text
/exit_node_on
```

The bot: advertises exit node (`tailscale set --advertise-exit-node` via nsenter), enables forwarding, finds the node in Headscale, and approves `0.0.0.0/0` + `::/0` routes.

> 💡 Auto-approve uses headscale **0.26+** syntax (`nodes approve-routes`). If your version is older, the bot will say so and you can approve manually (see below).

**Manual approve (older versions / fallback):**

- Via **Headplane**: SSH tunnel → node `100.64.x.x` → Routes → enable `0.0.0.0/0` and `::/0` toggles;
- Via CLI (headscale 0.23–0.25):
  ```bash
  docker exec headscale headscale nodes list | cat   # find <id>
  docker exec headscale headscale routes list         # find route ids
  docker exec headscale headscale routes enable -r <route-id>
  ```

### 🔑 Break-glass: enable via SSH when Telegram is unreachable

**Chicken-and-egg problem.** To reach Telegram you need an exit node — but the exit node is enabled by the Telegram bot. If Telegram opens only through the VPN, you can't reach the bot until the exit node is up.

**Solution** — manage the exit node directly via SSH, bypassing Telegram and even the bot container. Use [`scripts/exit_node.sh`](scripts/exit_node.sh):

```bash
ssh root@YOUR_VPS_IP -p YOUR_SSH_PORT
cd /opt/TelegramHelper
sudo ./scripts/exit_node.sh on        # advertise + forwarding + approve
sudo ./scripts/exit_node.sh status
# …select exit node on the device, wait for Telegram access…
sudo ./scripts/exit_node.sh off       # when done
```

**Without the script** (three steps manually):

```bash
# 1. Host advertises itself as exit node
sudo tailscale set --advertise-exit-node
# 2. Forwarding
sudo sysctl -w net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1
# 3. Approve in Headscale (0.26+)
docker exec headscale headscale nodes list | cat     # find <id>
docker exec headscale headscale nodes approve-routes -i <id> -r 0.0.0.0/0,::/0
```

### Using exit node (any user: admin / special)

```text
/exit_node
```

The bot shows whether the exit node is up and a step-by-step guide per device. The same **"🌐 Internet via VPS"** button appears on the `/start` and `/help` panels.

**On the device:**

- **iPhone/iPad:** Tailscale → menu (≡) → **Exit Node** → select VPS; optionally **Allow LAN access**.
- **Android:** Tailscale → ⋮ → **Use exit node** → select node.
- **macOS/Windows:** Tailscale icon in tray/menu bar → **Exit Node** → select node.
- **Linux:** `sudo tailscale set --exit-node=<name-or-IP> --exit-node-allow-lan-access`.

Verify from the device: open `https://ifconfig.me` — it should show the VPS public IP.

### Disable (admin)

```text
/exit_node_off
```

---

## Web UI via Headplane

Headscale has **no built-in web panel** — only a CLI and REST API. For convenient management we add **[Headplane](https://github.com/tale/headplane)** — a modern self-hosted UI: users, nodes, pre-auth keys, ACL, routes, DNS — all in a browser.

### Architecture: Option A — SSH Tunnel (default)

Headplane has full control over the mesh network. Any gap in the public UI equals an unauthorized node in your mesh. Therefore Headplane binds only to `127.0.0.1:3000` (see `compose.headplane.yaml`), not exposed externally. Access via SSH tunnel from the client PC.

```
Client PC                       VPS (network_mode: host)
┌───────────┐   ssh -L 3000     ┌──────────────────────┐
│ Browser   │ ◄───────────────► │ headplane :3000       │
│ :3000     │                   │   ↓ 127.0.0.1:8080    │
└───────────┘                   │ headscale (any container) │
                                └──────────────────────┘
```

Headplane runs in `network_mode: host` and reaches headscale via `127.0.0.1:8080`. This works with both deployment styles:

- `compose.headscale.yaml` (with `./headscale/config/` in the project);
- standalone installation with config in `/etc/headscale/` on the host.

The installer auto-detects the real config path via `docker inspect headscale` and writes it to `.env` as `HEADSCALE_CONFIG_FILE`.

Same pattern as Dockhand: zero external attack surface, access only for those with an SSH key on the VPS.

### 1. Installation

```bash
cd /opt/TelegramHelper
git pull origin main
bash scripts/install_headplane.sh
```

The script is idempotent. What it does:

1. Auto-detects the headscale config path via `docker inspect headscale` and writes it to `.env` as `HEADSCALE_CONFIG_FILE`.
2. Creates `headplane/data/` (persistent state).
3. Copies `headplane/config.example.yaml` → `headplane/config.yaml`.
4. Generates `cookie_secret` (32 hex chars).
5. Sets `public_url` from `headscale_config.json` (if present).
6. Issues a headscale API key via `docker exec headscale headscale apikeys create --expiration 90d` and writes it to `config.yaml`.
7. Starts the container via `docker compose -f compose.headplane.yaml up -d`.

Flags:

| Flag | Meaning |
|------|---------|
| `--force` | Regenerate `config.yaml` and issue a new API key |
| `--expiration <DUR>` | API key duration (headscale format: `24h`/`7d`/`90d`/`365d`, default `90d`) |

### 2. Access from a Client PC

```bash
ssh -p YOUR_SSH_PORT -L 3000:127.0.0.1:3000 root@YOUR_VPS_IP
```

Browser: **http://127.0.0.1:3000/admin**

On first login, Headplane asks for a **headscale API key**. For the UI login, issue a short-lived separate key:

```bash
docker exec headscale headscale apikeys create --expiration 24h
```

Paste it into the login form. After authorization the key is stored in a cookie.

### 3. What you see in Headplane

| Section | Manages |
|---------|---------|
| **Machines** | All mesh nodes: hostname, IP `100.64.x.x`, OS, last seen, expiry. Actions: rename, expire, delete, reassign to another user, attach tags |
| **Users** | Coordinator users (`main_user` by default). Create, delete, node count |
| **Pre-Auth Keys** | Pre-auth keys: reusable/ephemeral, expiry, owner. Create and revoke |
| **DNS** | MagicDNS, search domains, name servers, override records |
| **ACL** | HuJSON policy editor: which user/tag can talk to what. Changes auto-restart headscale (via `integration.docker`) |
| **Settings** | headscale API keys, server settings (from `headscale/config/config.yaml`, mounted read-only) |

### 4. Operations

```bash
# Logs
docker logs -f headplane

# Restart (e.g. after editing headplane/config.yaml)
docker compose -f compose.headplane.yaml up -d --force-recreate

# Stop
docker compose -f compose.headplane.yaml stop

# Remove
docker compose -f compose.headplane.yaml rm -sf
rm -rf headplane/data headplane/config.yaml
```

Verify from Telegram:

```text
/headscale_status
```

Output should include `🟢 Headplane (Web UI): running` with the URL and SSH tunnel hint.

### 5. Security

- `headplane/config.yaml` contains `cookie_secret` and headscale API key → gitignored. Back up together with `headscale/data/`.
- API keys can be listed and revoked via `docker exec headscale headscale apikeys list` / `expire <prefix>`.

### 6. Publish externally (NOT recommended by default)

If an SSH tunnel is inconvenient (e.g. need mobile access), you can put Headplane behind a reverse proxy:

- Use a subdomain like `panel.your-domain.com` via the same Nginx SNI routing as `headscale.example.com`.
- Set `cookie_secure: true` and `base_url: https://panel.your-domain.com` in `headplane/config.yaml`.
- Enable OIDC via Authentik/Keycloak — otherwise only an API key stands between the internet and full mesh control.

An SSH tunnel covers 99% of use cases.

---

## Headplane Installation — Step by Step

> A single end-to-end runbook for **both a fresh VPS** and an **already-running bot**.
>
> **Tested:** image `ghcr.io/tale/headplane:0.6.3`

### Two Different Keys — Don't Confuse Them

| Key | Where it lives | Purpose |
|-----|----------------|---------|
| **#1 — server-side** | `headplane/config.yaml` → `headscale.api_key` (90d) | headplane reads nodes / ACL / users. Set by `install_headplane.sh` automatically |
| **#2 — browser login** | not stored, entered into `/admin` form | UI login. Best issued as a **one-time 24h** key |

Leaking either key = full mesh access → immediately run `apikeys expire`.

### Scenario A — Bot Already Running

**Step 1. Update the code**

```bash
ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP
cd /opt/TelegramHelper
git pull origin main
```

> Rebuild the bot only if this update changed bot code (`.py`, `requirements.txt`, `Dockerfile`, `compose.yaml`). If the commits only touched `scripts/`, `compose.headplane.yaml` and `.md` files — no rebuild needed.

**Step 2. Install the panel**

```bash
bash scripts/install_headplane.sh
```

Expected output:

```
✅ Headscale config: /etc/headscale/config.yaml
✅ HEADSCALE_CONFIG_FILE written to .env
✅ headplane/config.yaml created (cookie_secret: 32 hex chars)
🔑 Issuing headscale API key (expiration: 90d)...
✅ New headscale API key saved to config.yaml
🚀 Starting headplane...
…
headplane   ghcr.io/tale/headplane:latest   Up … (healthy)
```

**Step 3. Verify the container is healthy**

```bash
docker ps --filter name=headplane --format '{{.Names}}\t{{.Status}}'  # Up … (healthy)
ss -tlnp | grep ':3000'                                               # LISTEN 127.0.0.1:3000
docker logs --tail=15 headplane                                       # "Running on 127.0.0.1:3000"
```

**Step 4. SSH tunnel from your PC** (keep the session open)

```bash
ssh -p YOUR_SSH_PORT -L 3000:127.0.0.1:3000 root@YOUR_VPS_IP
```

**Step 5. Browser login**

```bash
docker exec headscale headscale apikeys create --expiration 24h
```

Copy the output → browser `http://127.0.0.1:3000/admin` → paste into the form.

**Step 6. Verify from Telegram**

```text
/headscale_status
```

Expected:

```
🟢 Headplane (Web UI): running
🔗 http://127.0.0.1:3000/admin
🚪 SSH tunnel: ssh -L 3000:127.0.0.1:3000 root@YOUR_VPS_IP
```

### Scenario B — Fresh VPS

**Step 1. Base VPS + Docker + bot code** — follow `DEPLOY.md` fully.

**Step 2. Start headscale (coordinator)**

```bash
cd /opt/TelegramHelper
mkdir -p headscale/config headscale/data
curl -sL https://raw.githubusercontent.com/juanfont/headscale/main/config-example.yaml \
  -o headscale/config/config.yaml
# edit server_url / listen_addr (see Installation on VPS above)
docker compose -f compose.yaml -f compose.headscale.yaml up -d
docker exec headscale headscale users create main_user
docker ps | grep headscale     # should be Up
```

**Steps 3 and beyond** — same as Scenario A, starting with `bash scripts/install_headplane.sh`.

### Updating Headplane (after `git pull`)

```bash
cd /opt/TelegramHelper
git pull origin main
docker compose -f compose.headplane.yaml pull
docker compose -f compose.headplane.yaml up -d --force-recreate
docker logs --tail=30 headplane
```

### Rotating Secrets (if compromise is suspected)

```bash
bash scripts/install_headplane.sh --force
docker exec headscale headscale apikeys list
docker exec headscale headscale apikeys expire <old-key-prefix>
```

`--force` logs out active browser sessions — expected.

### Removing Headplane (headscale keeps running)

```bash
cd /opt/TelegramHelper
docker compose -f compose.headplane.yaml stop
docker compose -f compose.headplane.yaml rm -sf
rm -rf headplane/data headplane/config.yaml
sed -i '/^HEADSCALE_CONFIG_FILE=/d' .env
```

---

## Known Issues and Fixes

Three problems encountered during the first deployment. All are **already fixed** — kept here for reference.

### Issue 1 — Headscale config path

**Symptom:** old installer expected the config at `./headscale/config/config.yaml`, but on a production VPS headscale was installed standalone with the config at `/etc/headscale/config.yaml`.

**Fix:** `install_headplane.sh` auto-detects the path via `docker inspect headscale` (mount with `Destination == /etc/headscale`) and writes it to `.env` as `HEADSCALE_CONFIG_FILE`. Works for both `compose.headscale.yaml` and standalone. Verify manually:

```bash
docker inspect headscale --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

### Issue 2 — Bridge network can't reach standalone headscale

**Symptom:** in the normal compose bridge network, headplane couldn't resolve the name `headscale` when headscale was a separate compose project or standalone.

**Fix:** headplane uses `network_mode: host` (see `compose.headplane.yaml`). It shares the host's network namespace and reaches headscale via `127.0.0.1:8080` regardless of how headscale was installed.

### Issue 3 — Config schema 0.6.3 requires `pre_authkey` / `pod_name`

**Symptom:** container in restart loop with:

```
ERROR: Unable to load configuration: missing required fields or invalid values:
- integration.agent.pre_authkey must be a string (was missing)
- integration.kubernetes.pod_name must be a string (was missing)
```

**Fix:** `headplane/config.example.yaml` includes stubs (agent/k8s disabled, values unused):

```yaml
integration:
  agent:
    enabled: false
    pre_authkey: "unused-agent-disabled"
  kubernetes:
    enabled: false
    pod_name: "headscale"
```

To patch an **old** `config.yaml` manually (preserves existing `api_key`/`cookie_secret`):

```bash
cd /opt/TelegramHelper
sed -i '/^  agent:/,/^  docker:/ s/^    enabled: false/&\n    pre_authkey: "unused-agent-disabled"/' headplane/config.yaml
sed -i '/^  kubernetes:/,/^  proc:/ s/^    enabled: false/&\n    pod_name: "headscale"/' headplane/config.yaml
docker compose -f compose.headplane.yaml up -d --force-recreate
```

---

## Backup (what to save)

| File / folder | Why |
|---|---|
| `/etc/headscale/config.yaml` or `./headscale/config/config.yaml` | coordinator config |
| `/var/lib/headscale/` or `./headscale/data/` | headscale DB (SQLite): nodes, users, keys |
| `headplane/config.yaml` | `cookie_secret` + headscale API key (gitignored) |
| `headplane/data/` | Headplane internal DB (sessions, UI cache) |
| `.env` | `HEADSCALE_CONFIG_FILE` + other secrets |

---

## Connecting Devices

### 1. Generate a Pre-Auth Key

```bash
# Via bot (on coordinator)
/headscale_gen

# Via SSH — --user = numeric ID from users list, not the username
docker exec headscale headscale users list
docker exec headscale headscale preauthkeys create --user 1 --reusable --expiration 24h
```

### 2. Install Tailscale on the Device

**macOS:**
```bash
brew install tailscale
```

**Linux:**
```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

**Windows:** download from https://tailscale.com/download

### 3. Connect to Headscale

```bash
tailscale up --login-server https://headscale.example.com --authkey <PRE_AUTH_KEY>
```

### 4. Verify

```bash
tailscale status
# Should show other devices in the mesh network
```

### Connecting iPhone / Android

iOS/Android cannot use pre-auth keys directly — use the short registration ID and confirm on the server:

1. Install Tailscale from App Store / Google Play.
2. If already logged into regular Tailscale — **Log out**.
3. On the login screen: **Log in with other** (if hidden, tap the gear icon / three dots → **Use alternate coordination server**).
4. Enter the coordinator URL.
5. The app shows a key. Copy the **short 24-character ID** (letters+digits), not the long `nodekey:...`.
6. Immediately (key lives ~5 minutes) run on the VPS:

   ```bash
   docker exec headscale headscale nodes register \
     --user 1 \
     --key <24-char-id>
   ```

7. iOS does not provide a hostname — the node registers as `localhost`. Rename:

   ```bash
   docker exec headscale headscale nodes list | cat
   docker exec headscale headscale nodes rename --identifier <id> iphone-<name>
   ```

---

## Mesh Bypass in sing-box

When **sing-box VPN** (TUN mode) and **Tailscale** run simultaneously on a device, enable **Mesh Bypass** so Tailscale traffic bypasses the VPN:

- GUI: Settings → Mesh Bypass (checkbox)
- CLI: `config set mesh_bypass_enabled true`

Mesh Bypass adds direct routes for Tailscale/Headscale subnets:

| Subnet | Purpose |
|--------|---------|
| `100.64.0.0/10` | Tailscale/Headscale IPv4 |
| `fd7a:115c:a1e0::/48` | Tailscale/Headscale IPv6 |

---

## Nginx SNI Routing

Headscale listens on `127.0.0.1:8080`, but external clients connect via HTTPS (port 443). Xray (VLESS-Reality) already occupies 443 → use SNI routing:

```
Client → 443/TCP → Xray
                      ↓ non-VLESS (fallback)
                    Nginx :443 (ssl_preread)
                      ├── headscale.example.com → :8080
                      └── ha.your-domain.com → :8123
```

Configure via bot:
```text
/nginx_set_domain headscale.example.com ha.your-domain.com
/nginx_enable
/nginx_config    # Copy result to /etc/nginx/
```

Use `/nginx_set_domain`, `/nginx_enable`, and `/nginx_config` so Headscale and VLESS can share 443.

---

## Bot Configuration

Headscale config is stored in `headscale_config.json`:

```json
{
  "enabled": true,
  "container_name": "headscale",
  "server_url": "https://headscale.example.com",
  "default_user": "main_user",
  "key_expiration": "24h"
}
```

| Field | Description |
|-------|-------------|
| `enabled` | Whether Headscale is enabled in the bot |
| `container_name` | Docker container name |
| `server_url` | Public coordinator URL |
| `default_user` | Default user for Pre-Auth keys |
| `key_expiration` | Pre-Auth key validity duration |

---

## Docker Files

### compose.headscale.yaml

```yaml
services:
  headscale:
    container_name: headscale
    image: headscale/headscale:latest
    command: serve
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - ./headscale/config:/etc/headscale
      - ./headscale/data:/var/lib/headscale
```

Start with the main compose:
```bash
docker compose -f compose.yaml -f compose.headscale.yaml up -d
```

---

## TelegramHelper Bot + Mesh-Only 3x-ui Panel

If the 3x-ui panel is bound **only** to a mesh IP (e.g. `https://100.64.0.6:8081/panel`) and does not listen publicly, the bot in the default Docker bridge network **will not see the mesh** — `tailscale0` only exists in the host's network namespace. The `/xui_setup` command auto-detects this (CGNAT `100.64.0.0/10` and Tailscale-ULA `fd7a:115c:a1e0::/48`) and suggests the instructions below.

### Solution: Run the Bot in Host Mode via an Override File

The repository contains [`compose.host.yaml`](./compose.host.yaml) — applied on top of the main `compose.yaml`:

```bash
cd /opt/TelegramHelper
docker compose \
  -f compose.yaml \
  -f compose.host.yaml \
  up -d --force-recreate
```

What the override does:

- `telegram-helper` → `network_mode: host` (sees `tailscale0`, host mesh IPs, mesh DNS);
- `dockhand` stays on bridge, gets `extra_hosts: telegram-helper:host-gateway` to keep `DOCKHAND_API_URL=http://telegram-helper:8000` working;
- `ports: ["8000:8000"]` is automatically ignored under `network_mode: host` (with a warning — this is normal).

To revert to bridge — simply start without `-f compose.host.yaml`:

```bash
docker compose up -d --force-recreate
```

### Full Deployment Flow for Mesh-Only Setup

```bash
# 1. Tailscale on the host is already running
tailscale ip -4   # should return your mesh IP

# 2. Pull bot code
cd /opt/TelegramHelper
git pull origin main

# 3. Rebuild and start with host override
docker compose -f compose.yaml -f compose.host.yaml \
  build --no-cache telegram-helper
docker compose -f compose.yaml -f compose.host.yaml \
  up -d --force-recreate

# 4. Verify bot can see the mesh
docker exec telegram-helper-lite curl -sk \
  https://100.64.0.6:8081/panel/login \
  --connect-timeout 5 -o /dev/null -w "%{http_code}\n"
# Expect: 405 / 400 / 401 — anything except "(00) connection refused"

# 5. In Telegram: /xui_setup with mesh URL
```

If step 4 returns `(00)` or timeout — verify the panel is actually listening on the mesh IP (`ss -tlnp | grep 8081` on the host), and that `tailscale ping <panel-mesh-ip>` succeeds.

### When Host Mode is NOT Needed

- Panel is accessible via a public IP — bridge is sufficient, don't touch `compose.yaml`.
- Bot and panel on different VPS, panel also accessible via public IP — use the public URL in `/xui_setup`.

---

## Troubleshooting

| Problem | Solution |
|---------|---------|
| Container won't start | `docker logs headscale` — check config errors |
| `connection refused` on `tailscale up` | Verify Nginx SNI routing is configured and the domain resolves |
| Devices can't see each other | Check `tailscale status` on both, ensure they are in the same user |
| VPN breaks Tailscale | Enable Mesh Bypass in sing-box |
| Pre-Auth key expired | Generate a new one: `/headscale_gen` |
| iOS: `registration ID must be 24 characters long` | `nodekey:...` was copied instead of the short ID. Look for the 24-character code on the app screen |
| iOS: `node not found in registration cache` | More than 5 minutes elapsed — key expired. Reopen the login screen, get a fresh ID, immediately run `register` |
| iOS: no "Log in with other" button | Hidden under gear/⋯ on the login screen in newer versions |
| iOS: node registered as `localhost` | iOS doesn't send hostname. Rename via `headscale nodes rename --identifier <id> <name>` |
| Headplane: `Failed to read Headscale configuration` | headscale config not visible from the container. Check that `./headscale/config/config.yaml` exists and is mounted read-only |
| Headplane: `unauthorized` on login | headscale API key expired. Issue a new one: `docker exec headscale headscale apikeys create --expiration 24h`, paste into the form |
| Headplane: blank page / `connection refused` | Headplane only listens on `127.0.0.1:3000`. Need SSH tunnel: `ssh -L 3000:127.0.0.1:3000 root@YOUR_VPS_IP` |
| Headplane: restart loop, `pre_authkey … pod_name … must be a string` | Schema 0.6.x requires these fields even with `enabled:false`. See [Issue 3](#issue-3--config-schema-063-requires-pre_authkey--pod_name) |
| Headplane: ACL changes not picked up | Verify `integration.docker.enabled: true` in `headplane/config.yaml` and docker socket is mounted |

---

## Related Documents

- [SSHvsTelegramBOT.md](SSHvsTelegramBOT.md) — SSH tunneling (for Headplane / Dockhand access)
- `DEPLOY.md` — port 443 and Xray fallback + Nginx stream SNI
- the `scripts/` directory — installers and `scripts/exit_node.sh`
