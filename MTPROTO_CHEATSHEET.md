# MTProto Proxy — Cheatsheet

Technical description, deployment, and configuration for bypassing Telegram blocks.

## Important: Where MTProto Is Actually Installed

If TelegramHelper is running in Docker, this does **not** mean that MTProto is installed inside the container.

The `MTProto proxy` in this project is installed **on the Debian host itself**, because:

- it is set up as the system service `mtproto-proxy`
- it uses `systemd`
- it opens a port at the host level
- it configures the firewall and cron on the server itself

The architecture is:

```text
Docker container TelegramHelper -> manages the configuration
Debian host -> actually runs the MTProto proxy
```

If the `/mt_install` bot command reports that installation is impossible in Docker, that is expected. In that case, run the installation **via SSH directly on the server**.

## Terminology

- `secret`:
  The main password for a client connecting to MTProto. This is the value you paste into the Telegram client or pass in a `tg://proxy` link.

- `server secret`:
  The server-side portion of the secret. In `ee_split` mode this is a short 32-hex string without `ee`/`dd` prefixes; it is passed to `ExecStart` via `-S`.

- `client secret`:
  What the user actually receives. In `ee_split` mode this is an `ee...` string; in `dd_inline` mode it is a `dd...` string.

- `fake_tls_domain`:
  The masking domain, e.g. `google.com`. It makes traffic appear similar to ordinary TLS/HTTPS.

- `dd_inline`:
  Legacy mode. The same full `dd...` secret is used by both the client and the server.

- `ee_split`:
  The new recommended mode. The server is launched with `-S <32hex> -D <domain>`, and the client receives `ee<32hex><domain_hex>`.

- `systemd unit`:
  The Linux service file `/etc/systemd/system/mtproto-proxy.service` that defines how `mtproto-proxy` is started on the Debian host.

- `tag` / `proxy tag`:
  An optional hex identifier from `@MTProxybot`. It is not needed for the proxy to function; it is used to promote a proxy or channel through Telegram. Not required for personal use.

- `/mt_set_tag <hex_tag>`:
  Bot command that saves this promo-tag to the config. If you do not need a tag, leave it empty.

- `/mt_set_tag off`:
  Remove a previously saved tag.

## Operation Modes

TelegramHelper supports two MTProto modes:

- `ee_split` — recommended mode. The client receives a secret in `ee...` format, and the systemd unit on the host is started with `-S <32hex> -D <domain>`.
- `dd_inline` — legacy mode for older installations. The client and server share the same `dd...` secret.

Migration rules:

- Old configs without `secret_mode` are automatically treated as `dd_inline`
- New installations should use `ee_split` from the start
- After changing the mode, re-issue client links and QR codes

Basic migration flow:

```text
/mt_set_mode ee_split
/mt_set_domain google.com
/mt_gen_all
/mt_add_client phone
```

If the bot is running in Docker and `systemd` is only on the host, do not rely on `/mt_apply`: apply the unit via SSH on the host, and use the bot for configuration and client link delivery.

### Installing MTProto on a Debian Host

Connect to the server:

```bash
ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP
cd /opt/TelegramHelper
```

On a minimal Debian, install `cron` first because the script uses `crontab`:

```bash
apt-get update
apt-get install -y cron
systemctl enable --now cron
```

Then run the install script **on the host**:

```bash
bash scripts/install_mtproto.sh --mode ee_split --port 993 --domain google.com --workers 1
```

Verify after installation:

```bash
systemctl status mtproto-proxy --no-pager | cat
ss -tulpn | grep 993
crontab -l
```

Only then return to the Telegram bot and run:

```text
/mt_set_mode ee_split
/mt_set_server YOUR_VPS_IP
/mt_set_port 993
/mt_set_domain google.com
/mt_gen_all
/mt_on
/mt_add_client client1
```

The `/mt_add_client client1` command creates an individual client and normally sends the link and QR immediately.

If the bot is running in Docker, after `/mt_add_client` synchronize the host `systemd`:

```bash
ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP
cd /opt/TelegramHelper
python3 scripts/mtproto_sync_systemd.py
systemctl show -p ExecStart mtproto-proxy --no-pager | cat
```

This step is where all the required `-S` flags for new clients must appear in `ExecStart`.

---

## Typical Docker Workflow

When the bot runs in Docker, the usual picture is:

- `/mt_set_server` — works
- `/mt_set_port` — works
- `/mt_set_domain` — works
- `/mt_gen_all` — works
- `/mt_on` — works
- `/mt_add_client client1` — can create a client and return links/QR
- `/mt_install` — **does not install MTProto**, because installation must run on the host
- `/mt_apply` — **cannot manage `systemd`**, because the container cannot see the host systemd

Messages such as:

```text
Running in Docker — installation not possible
Running in Docker — systemctl not available
```

are **expected** in this scenario.

What this means in practice:

1. Install MTProto on the Debian host via SSH once:

```bash
ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP
cd /opt/TelegramHelper
apt-get update
apt-get install -y cron
systemctl enable --now cron
bash scripts/install_mtproto.sh --mode ee_split --port 993 --domain google.com --workers 1
```

2. Verify on the host:

```bash
systemctl status mtproto-proxy --no-pager | cat
ss -tulpn | grep 993
```

3. Then use the bot for configuration and clients:

```text
/mt_set_mode ee_split
/mt_set_server YOUR_VPS_IP
/mt_set_port 993
/mt_set_domain google.com
/mt_gen_all
/mt_on
/mt_add_client client1
/mt_qr client1
```

4. After each MTProto config change that affects the host unit, synchronize `systemd` on the Debian host:

```bash
ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP
cd /opt/TelegramHelper
python3 scripts/mtproto_sync_systemd.py
systemctl show -p ExecStart mtproto-proxy --no-pager | cat
systemctl status mtproto-proxy --no-pager | cat
```

Changes that require synchronization:

- `/mt_gen_all`
- `/mt_add_client <name>`
- `/mt_del_client <name>`
- `/mt_set_mode ...`
- `/mt_set_domain ...`
- `/mt_set_port ...`
- `/mt_set_workers ...`
- `/mt_set_tag ...`

If the bot is in Docker, a subsequent `/mt_apply` from the container is not needed — it cannot see the host `systemd`. The correct path here is `python3 scripts/mtproto_sync_systemd.py` on the Debian host.

---

## What Is MTProto Proxy

**MTProto proxy (MTProxy)** is a specialized Telegram proxy protocol for bypassing censorship, created in 2018 in response to blocking in Iran and Russia. It works **only with Telegram** — it does not route other traffic.

| Feature | SOCKS5 / HTTP | MTProto Proxy |
|---|---|---|
| Scope | Any application | Telegram only |
| Traffic obfuscation | No | Yes (fake-TLS, dd-padding) |
| Authentication | Username/password | Hex secret |
| Encryption | Application-dependent | MTProto 2.0 over the proxy |
| Channel promotion | Not possible | Via @MTProxybot |

---

## Secret Types

### Generation 0 — Plain secret (deprecated)
```
cafe1234cafe1234cafe1234cafe1234
```
32 hex characters. Packets appear as random data. DPI detects it by packet-size statistics.

### Generation 1 — dd-secret
```
ddcafe1234cafe1234cafe1234cafe1234
```
Prefix `dd` + 32 hex. The client uses randomized padding to hide the characteristic MTProto packet sizes.

### Generation 2 — Fake-TLS legacy (`dd_inline`)
```
dd<16_random_bytes_hex><domain_hex>
```
Example for `google.com`:
```
dd82af02e4a6fcc86afda8521af4033b28676f6f676c652e636f6d
```

Generate:
```bash
SECRET_RANDOM=$(head -c 16 /dev/urandom | xxd -ps -c 32)
DOMAIN_HEX=$(echo -n "google.com" | xxd -ps -c 256)
echo "dd${SECRET_RANDOM}${DOMAIN_HEX}"
```

### Modern TelegramHelper mode (`ee_split`)
```
ee<16_random_bytes_hex><domain_hex>
```

This is the client-side format for `ee_split` mode.

On the server side, the split form is used:

```text
-S <32_hex_random> -D <domain>
```

Use this mode for new servers and after `status=2/INVALIDARGUMENT` errors.

---

## How Fake-TLS Bypasses DPI

```
Telegram client                         DPI                              MTProxy server
       |                                   |                                   |
       |--- TLS ClientHello (SNI: google.com) --->                              |
       |                                   |                                   |
       |                          DPI sees: "standard HTTPS                     |
       |                          to google.com, let through"                  |
       |                                   |                                   |
       |<--------- TLS ServerHello + encrypted data ----|                       |
       |                                   |                                   |
       |========= MTProto inside TLS tunnel (with dd-padding) ================>|
```

| DPI method | Result | Reason |
|---|---|---|
| Protocol analysis | Not detected | Traffic = standard HTTPS |
| SNI inspection | Not detected | SNI shows a "clean" domain |
| Packet sizes | Not detected | dd-padding randomizes them |
| IP blocking | Partial | Risk of collateral damage |
| Active probing | May detect | Standard MTProxy does not return a valid website |

**Note (2026):** Advanced DPI systems have learned to detect MTProxy via active probing. Recommendations:
- Use a **private** server (do not publish the address)
- Use non-standard domains for fake-TLS
- Consider VLESS+Reality or Hysteria2 as more resilient alternatives

---

## VPS Deployment

### Quick Method (TelegramHelper Script)

```bash
bash scripts/install_mtproto.sh --mode ee_split --port 993 --domain google.com --workers 1
```

The script automatically:
1. Installs dependencies and builds MTProxy from source
2. Downloads `proxy-secret` and `proxy-multi.conf`
3. Generates a fake-TLS secret in the selected mode (`ee_split` or `dd_inline`)
4. Creates a systemd service
5. Opens the port in the firewall
6. Configures a cron job for daily config updates
7. Outputs ready-to-use `tg://proxy` and `https://t.me/proxy` links

### Manual Installation

#### 1. Dependencies

```bash
# Debian/Ubuntu
apt install -y git curl build-essential libssl-dev zlib1g-dev xxd

# CentOS/RHEL
yum groupinstall "Development Tools"
yum install openssl-devel zlib-devel
```

#### 2. Build

```bash
git clone https://github.com/TelegramMessenger/MTProxy.git /opt/MTProxy
cd /opt/MTProxy && make -j$(nproc)
cp objs/bin/mtproto-proxy /usr/local/bin/
chmod +x /usr/local/bin/mtproto-proxy
```

> For GCC 10+ build errors: add `-fcommon` to CFLAGS/LDFLAGS in the Makefile

#### 3. Telegram Configuration Files

```bash
mkdir -p /etc/mtproto-proxy
curl -s https://core.telegram.org/getProxySecret -o /etc/mtproto-proxy/proxy-secret
curl -s https://core.telegram.org/getProxyConfig -o /etc/mtproto-proxy/proxy-multi.conf
```

**Important**: `proxy-multi.conf` contains Telegram server IP addresses and must be updated daily.

#### 4. Generate a Secret for `ee_split`

```bash
SECRET_RANDOM=$(head -c 16 /dev/urandom | xxd -ps -c 32)
DOMAIN_HEX=$(echo -n "google.com" | xxd -ps -c 256)
CLIENT_SECRET="ee${SECRET_RANDOM}${DOMAIN_HEX}"
echo "Secret: ${CLIENT_SECRET}"
```

#### 5. Systemd Service

File `/etc/systemd/system/mtproto-proxy.service`:
```ini
[Unit]
Description=MTProto Proxy
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mtproto-proxy \
    -u nobody \
    -p 2398 \
    -H 993 \
    -S <32_HEX_SECRET> \
    -D google.com \
    --aes-pwd /etc/mtproto-proxy/proxy-secret \
    /etc/mtproto-proxy/proxy-multi.conf \
    -M 1 \
    --nat-info YOUR_VPS_IP:YOUR_VPS_IP
Restart=on-failure
RestartSec=5
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
```

Flags:
| Flag | Description |
|------|----------|
| `-u nobody` | Drop privileges to this user |
| `-p 2398` | Stats port (localhost; `curl localhost:2398/stats`) |
| `-H 993` | Public port for clients |
| `-S <secret>` | Server secret; in `ee_split` this is the plain `32hex`, without `ee`/`dd` |
| `-D <domain>` | Fake-TLS domain for `ee_split` |
| `-P <tag>` | Tag from @MTProxybot |
| `-M 1` | Worker count; typically safer to use `1` for TLS transport |
| `--aes-pwd` | Path to proxy-secret |
| `--nat-info` | For servers behind NAT (AWS, Oracle Cloud) |

Activate:
```bash
systemctl daemon-reload
systemctl enable mtproto-proxy
systemctl restart mtproto-proxy
```

#### 6. Firewall

```bash
# UFW (Debian/Ubuntu)
ufw allow 993/tcp comment "MTProto Proxy"

# iptables
iptables -I INPUT -p tcp --dport 993 -j ACCEPT

# Verify
ss -tulpn | grep 993
```

#### 7. Cron for proxy-secret Updates

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * curl -s https://core.telegram.org/getProxySecret -o /etc/mtproto-proxy/proxy-secret && curl -s https://core.telegram.org/getProxyConfig -o /etc/mtproto-proxy/proxy-multi.conf && systemctl restart mtproto-proxy") | crontab -
```

---

## Bot Management

### Quick Start
```
/mt_install          -- install MTProxy on the server
/mt_gen_all          -- generate secret + detect IP
/mt_apply            -- apply config and start (only if bot has access to host systemd)
/mt_export           -- get client links
```

### All Commands

**Status:** `/mt_status`, `/mt_config`
**Service control:** `/mt_on`, `/mt_off`
**Configuration:** `/mt_set_mode`, `/mt_set_server`, `/mt_set_port`, `/mt_set_domain`, `/mt_set_tag`, `/mt_set_workers`
**Generation:** `/mt_gen_secret`, `/mt_gen_all`
**Clients:** `/mt_add_client`, `/mt_qr`, `/mt_del_client`, `/mt_list_clients`
**Service:** `/mt_install`, `/mt_apply`, `/mt_start`, `/mt_stop`, `/mt_restart`, `/mt_logs`
**Export:** `/mt_export`, `/mt_fetch_config`

---

## Client Configuration

### Connection Links

```
tg://proxy?server=YOUR_VPS_IP&port=PORT&secret=SECRET
https://t.me/proxy?server=YOUR_VPS_IP&port=PORT&secret=SECRET
```

Users only need to tap the link and press **Connect**.

### Telegram Desktop (Windows / macOS / Linux)

**Settings** → **Advanced** → **Connection type** → **Use custom proxy** → **MTPROTO** → enter Server, Port, Secret → **Save**

### Telegram iOS

**Settings** → **Data and Storage** → **Proxy** → **Add Proxy** → **MTProto** → enter data → enable **Use Proxy**

### Telegram Android

**Settings** → **Data and Storage** → **Proxy Settings** → **Add Proxy** → **MTProto** → enter data → activate

---

## Promotion via @MTProxybot

1. Open **@MTProxybot** in Telegram
2. Send `/newproxy`
3. Enter the server IP and port
4. Receive a **proxy tag** (16-character hex string)
5. Add the tag at launch: `-P <proxy_tag>` (or `/mt_set_tag <tag>` via bot)
6. Press **Set promotion** → select a channel

**Result**: all proxy users will see your channel pinned at the top of their chat list with a "Sponsored" label.

---

## Common Problems

| Problem | Cause | Solution |
|---|---|---|
| "Connecting..." indefinitely | Port is closed | `ufw allow 993/tcp` |
| "Proxy unavailable" | NAT not configured | Add `--nat-info PRIVATE_IP:PUBLIC_IP` |
| Connection timeout | Server clock is off | `timedatectl set-ntp true` |
| Periodic drops | Stale proxy-multi.conf | Set up cron update |
| Slow connection | ISP throttling the IP | Switch port to 443, enable fake-TLS |
| Build error (GCC 10+) | `-fcommon` not set | Add `-fcommon` to CFLAGS in Makefile |
| iPhone does not add proxy / `mtproto-proxy` crashes with `status=2` | Build does not accept a long `dd...` secret in `-S` | Switch to `ee + -D` mode as described below |

### If iPhone Does Not Accept the Proxy or the Service Crashes with `INVALIDARGUMENT`

Typical symptoms:

- `systemctl status mtproto-proxy` shows `status=2/INVALIDARGUMENT`
- Telegram on iPhone does not add the proxy
- The current secret starts with `dd...` but the server service does not start

In this case, use the working server mode:

- server: `-S <32 hex>` and `-D google.com`
- client: `Secret` in the format `ee<32hex><domain_hex>`

### Ready-to-Use Server Block

Execute **on the Debian host via SSH**.
Copy and paste **the entire block at once**:

```bash
BASE_SECRET=$(head -c 16 /dev/urandom | xxd -ps -c 32)
DOMAIN="google.com"
DOMAIN_HEX=$(printf '%s' "$DOMAIN" | xxd -ps -c 256)
CLIENT_SECRET="ee${BASE_SECRET}${DOMAIN_HEX}"
SERVER_IP="YOUR_VPS_IP"
PORT="993"

cat >/etc/systemd/system/mtproto-proxy.service <<EOF
[Unit]
Description=MTProto Proxy
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mtproto-proxy -u nobody -p 2398 -H ${PORT} -S ${BASE_SECRET} -D ${DOMAIN} --aes-pwd /etc/mtproto-proxy/proxy-secret /etc/mtproto-proxy/proxy-multi.conf -M 1 --nat-info ${SERVER_IP}:${SERVER_IP}
Restart=on-failure
RestartSec=5
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
EOF

cat >/root/mtproto_client.txt <<EOF
Server: ${SERVER_IP}
Port: ${PORT}
Domain: ${DOMAIN}
Secret: ${CLIENT_SECRET}

tg://proxy?server=${SERVER_IP}&port=${PORT}&secret=${CLIENT_SECRET}
https://t.me/proxy?server=${SERVER_IP}&port=${PORT}&secret=${CLIENT_SECRET}
EOF

systemctl daemon-reload
systemctl restart mtproto-proxy
systemctl status mtproto-proxy --no-pager | cat
ss -tulpn | grep 993 | cat
cat /root/mtproto_client.txt | cat
```

### Finding the `Secret` for iPhone

After running the block above:

```bash
cat /root/mtproto_client.txt | cat
```

You will see a line:

```text
Secret: ee...
```

This value after `Secret:` is what to paste into Telegram on iPhone.

### What to Enter on iPhone

- `Server`: `YOUR_VPS_IP`
- `Port`: `993`
- `Secret`: the value from `/root/mtproto_client.txt` that starts with `ee...`

Or simply open this link on the iPhone:

```text
tg://proxy?server=YOUR_VPS_IP&port=993&secret=ee...
```

### Important

After this manual setup, do not use the following from the Docker bot:

- `/mt_install`
- `/mt_apply`
- `/mt_start`

Not because the mode is incompatible, but because the container cannot manage the host `systemd`. If the bot is running directly on the Debian host (not in Docker), `/mt_apply` with `ee_split` is fully compatible.

### Updating Telegram Configs

```bash
curl -s https://core.telegram.org/getProxySecret -o /etc/mtproto-proxy/proxy-secret
curl -s https://core.telegram.org/getProxyConfig -o /etc/mtproto-proxy/proxy-multi.conf
systemctl restart mtproto-proxy
```

Or via bot: `/mt_fetch_config`

---

## Security

### What the Proxy Operator Can See

| Data | Visible? |
|---|---|
| User IP address | **YES** |
| Connection time and duration | **YES** |
| Traffic volume | **YES** |
| Fact of using Telegram | **YES** |
| Message content | **NO** |
| Account (username, ID) | **NO** |
| Chat list, contacts | **NO** |
| Media files | **NO** |

### Telegram Encryption

- **Cloud chats**: client–server encryption (MTProto 2.0). The proxy sees only encrypted packets.
- **Secret chats**: end-to-end encryption. Neither Telegram servers nor the proxy can read the content.
- **Perfect Forward Secrecy**: compromising a key does not expose past messages.

MTProto proxy operates as a transport layer — it forwards already-encrypted packets and has no access to the keys.

### Recommendations

- Run a **private** server (do not publish the address in proxy lists)
- Use **secret chats** (E2E) for confidential conversations
- Treat MTProxy as a **first line of defense**; consider VLESS+Reality and Hysteria2 as more resilient alternatives

---

## Network Topology

```
Port        Protocol    Service                  Transport
────────────────────────────────────────────────────────────
443/TCP     VLESS       Xray (VLESS-Reality)    TCP
443/UDP     Hysteria2   Hysteria2 (QUIC)        UDP
993/TCP     MTProto     mtproto-proxy           TCP
8000/TCP    HTTP        FastAPI REST API         TCP
```

All three VPN protocols coexist without conflict:
- VLESS and MTProto on different TCP ports (443 vs 993)
- Hysteria2 on UDP (no overlap with TCP)

---

## Alternative MTProxy Implementations

| Implementation | Language | Fake-TLS | Active Probe Protection | Status |
|---|---|---|---|---|
| [TelegramMessenger/MTProxy](https://github.com/TelegramMessenger/MTProxy) | C | No (dd only) | No | Official, rarely updated |
| [GetPageSpeed/MTProxy](https://github.com/GetPageSpeed/MTProxy) | C | No | No | Fork with GCC 10+ fixes |
| [9seconds/mtg v2](https://github.com/9seconds/mtg) | Go | Yes (ee) | No | Actively maintained |
| [seriyps/mtproto_proxy](https://github.com/seriyps/mtproto_proxy) | Erlang | Yes | No | High-performance |
| [telemt](https://github.com/nicholasgasior/goproxy) | Rust | Yes | **Yes** | Active probing protection |

---

## Getting Connection Parameters

Three ways to get the Server / Port / Secret to share with users.

### Method 1 — Via the Telegram Bot (recommended)

```
/mt_export
```

The bot replies with an inline keyboard:

```
[ tg:// Link ]  [ HTTPS Link ]  [ sing-box Profile ]  [ Base64 ]
```

Press **tg:// Link** → get the ready link → forward it to the user.
The user only needs to tap and press **Connect**.

Alternatively, `/mt_gen_all` — a single command that detects the server IP, generates a secret, and displays both links. Do not use this on an already-running server unless you intend to rotate the secret.
If a QR code for a phone is needed, use `/mt_add_client <name>` or `/mt_qr <name>` — the bot shows a QR based on `https://t.me/proxy`, which a phone camera opens more reliably.

---

### Method 2 — Bash Script on the Server

Quick parameter view from the config:

```bash
python3 -c "
import json
with open('mtproto_config.json') as f:
    c = json.load(f)
server  = c.get('server', 'N/A')
port    = c.get('port', 993)
secret  = c.get('secret', 'N/A')
print(f'Server: {server}')
print(f'Port:   {port}/tcp')
print(f'Secret: {secret}')
print()
print(f'tg://proxy?server={server}&port={port}&secret={secret}')
print(f'https://t.me/proxy?server={server}&port={port}&secret={secret}')
"
```

If the config does not exist yet — run `/mt_gen_all` via the bot, or generate a secret manually in `ee_split` mode:

```bash
SECRET_RANDOM=$(head -c 16 /dev/urandom | xxd -ps -c 32)
DOMAIN_HEX=$(echo -n "google.com" | xxd -ps -c 256)
SECRET="ee${SECRET_RANDOM}${DOMAIN_HEX}"
SERVER_IP=$(curl -s https://api.ipify.org)
PORT=993

echo "Server: ${SERVER_IP}"
echo "Port:   ${PORT}"
echo "Secret: ${SECRET}"
echo
echo "tg://proxy?server=${SERVER_IP}&port=${PORT}&secret=${SECRET}"
echo "https://t.me/proxy?server=${SERVER_IP}&port=${PORT}&secret=${SECRET}"
```

---

### Method 3 — Script That Prints All Clients

```bash
python3 scripts/show_mtproto.py
```

File `scripts/show_mtproto.py`:

```python
#!/usr/bin/env python3
"""Print all MTProto connection parameters from mtproto_config.json."""
import json, sys, pathlib

config_path = pathlib.Path("mtproto_config.json")
if not config_path.exists():
    print("Error: mtproto_config.json not found. Run /mt_gen_all via the bot.")
    sys.exit(1)

c = json.loads(config_path.read_text())
server = c.get("server", "")
port   = c.get("port", 993)

print("=" * 55)
print("  MTProto Proxy — Connection Parameters")
print("=" * 55)
print(f"  Server:  {server}")
print(f"  Port:    {port}/tcp")
print(f"  Domain:  {c.get('fake_tls_domain', '')} (fake-TLS)")
print(f"  Workers: {c.get('workers', 2)}")
print()

# Main secret
main_secret = c.get("secret", "")
if main_secret:
    print("  Main secret:")
    print(f"    Secret: {main_secret}")
    print(f"    tg://proxy?server={server}&port={port}&secret={main_secret}")
    print(f"    https://t.me/proxy?server={server}&port={port}&secret={main_secret}")
    print()

# Additional clients
clients = c.get("clients", [])
if clients:
    print(f"  Clients ({len(clients)}):")
    for cl in clients:
        name   = cl.get("name", "—")
        secret = cl.get("secret", "")
        print(f"    [{name}]")
        print(f"    tg://proxy?server={server}&port={port}&secret={secret}")
        print()

print("=" * 55)
```

---

### Method 4 — QR Code for Phone Connection

Install `qrencode`:

```bash
apt install -y qrencode   # Debian/Ubuntu
```

Generate a QR code directly in the terminal:

```bash
python3 -c "
import json
c = json.load(open('mtproto_config.json'))
link = f'tg://proxy?server={c[\"server\"]}&port={c[\"port\"]}&secret={c[\"secret\"]}'
print(link)
" | qrencode -t ansiutf8
```

Scan with the phone → Telegram opens with a prompt to connect.

Save as PNG for distribution:

```bash
python3 -c "
import json
c = json.load(open('mtproto_config.json'))
print(f'tg://proxy?server={c[\"server\"]}&port={c[\"port\"]}&secret={c[\"secret\"]}')
" | qrencode -o ~/mtproto_qr.png -s 6
echo "QR saved: ~/mtproto_qr.png"
```

---

### Summary: Which Method to Use

| Scenario | Method |
|---|---|
| Share with one user | `/mt_export` → tg:// Link → forward |
| View parameters on the server | `python3 scripts/show_mtproto.py` |
| Connect a phone manually | QR code in terminal (`qrencode -t ansiutf8`) |
| Integration with other software | Read `mtproto_config.json` directly |
| Bulk distribution to clients | `/mt_export` → Base64 → paste into mailing |
