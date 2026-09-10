# RETICULUM_GUIDE.md — Reticulum (RNS) as Part of the HA Stack (Stubs) in TelegramHelper

Practical reference for the **Reticulum** stack on a VPS running `TelegramHelper`: what it is, how to install it, how it works over TCP and over **I2P** (path 2 — native i2pd tunnels, e2e ✅, §8). Reticulum here is the **HA stack (stubs)** for e2e testing of the client from `UDP_gRPC_COM_Lite`, not a VPN transport like VLESS/Hysteria2/NaiveProxy.

## Who starts it

The Telegram bot **does not** install or start this stack. `python main.py` and Docker Compose only run the bot + API. VLESS/Hysteria2 profile delivery does not need it.

The HA/Reticulum units are an **optional host sidecar**:

1. `bash scripts/vps_setup.sh` — prompt *HA server + Reticulum (Mi-Home stubs, for tests)?*
2. or `bash scripts/install_ha_stack.sh` on the VPS

After the units exist, the bot can **inspect and restart** them (`/reticulum_status`, `/reticulum_restart`, `/reticulum_hash`, `/reticulum_i2p`). If they are missing, those commands say the stack is not installed.

This is not Home Assistant. The stubs simulate Mi-Home devices. A real HA (typically on a NAS over Headscale) is a separate optional step: `scripts/install_ha_adapter.sh`.

## 0. The Most Important Thing

Reticulum in this project is an **RNS bridge** in front of a local gRPC stub server for Mi-Home devices. It is not an application traffic transport and not a browser proxy. It is a test harness that simulates an "HA server" with devices, accessible in two ways: directly via TCP gRPC and through Reticulum.

```text
Client (UDP_gRPC_COM_Lite)
  -> [path A] TCP gRPC direct: 127.0.0.1:50055 (via SSH tunnel)
  -> [path B] Reticulum:
       RNS Identity of client
       -> TCPClientInterface target 127.0.0.1:50061 (via SSH tunnel)
       -> ha-reticulum-bridge (RNS TCPServerInterface, destination hash)
       -> local gRPC 127.0.0.1:50055 (/device_control)
          or local UDP 127.0.0.1:50056 (/udp_raw)
       -> Mi-Home stubs (mi_bulb / mi_th_sensor / mi_vibration)
```

The transport is **determined only by the RNS config** (`ha_stack/rns/config`). The bridge code is transport-agnostic: switching TCP → I2P does not change a single line in `bridge/` and stubs — only the interface section in the config changes.

Important: all three services listen **only on `127.0.0.1`** — they are not exposed externally, no firewall changes needed. Access from your machine is via SSH tunnel.

## 1. What It Is and When to Use It

Use the HA stack with Reticulum when:

- you need to run **e2e tests** of the `UDP_gRPC_COM_Lite` client without a real Home Assistant;
- you need to verify both access paths to devices — direct TCP gRPC and through Reticulum (RNS bridge);
- you are preparing to move the transport to I2P and want to confirm the bridge code does not break on interface change.

Do not use it as:

- VPN/proxy for application traffic (for that use VLESS-Reality, Hysteria2, NaiveProxy, MTProto, Mieru, TUIC, etc.);
- a public service — ports are intentionally locked to `127.0.0.1`.

Three stub devices (deterministic):

| Device | Type | Response |
| --- | --- | --- |
| `mi_bulb` | LED bulb | WRITE on/off is remembered; READ → `power=on/off, brightness=80` |
| `mi_th_sensor` | T/H sensor | READ → `T=23.5C H=45%` |
| `mi_vibration` | vibration sensor | READ → `state=idle, last_event=none` |

## 2. Files and Services

| What | Path |
| --- | --- |
| HA stack bundle | `ha_stack/` |
| gRPC Mi-Home stubs | `ha_stack/stub_server.py` |
| UDP stub (for `/udp_raw`) | `ha_stack/udp_stub.py` |
| RNS bridge (logic) | `ha_stack/bridge/bridge.py` |
| Bridge entry point | `ha_stack/bridge/run_bridge.py` |
| gRPC backend of bridge | `ha_stack/bridge/grpc_backend.py` |
| Proto + stubs | `ha_stack/proto/` |
| RNS config (interface) | `ha_stack/rns/config` |
| Python dependencies | `ha_stack/requirements.txt` (`rns`, `grpcio`, `protobuf`) |
| Python venv | `ha_stack/.venv/` |
| RNS storage + identity | `ha_stack/.rnsdata/` (not committed to git) |
| Installer | `scripts/install_ha_stack.sh` |

systemd services (created by the installer):

| Service | Port (`127.0.0.1` only) | Role |
| --- | --- | --- |
| `ha-stub-grpc` | `50055/tcp` | gRPC `DeviceControlService` (Mi-Home stubs) |
| `ha-stub-udp` | `50056/udp` | UDP stub device (for raw path `/udp_raw`) |
| `ha-reticulum-bridge` | `50061/tcp` | RNS `TCPServerInterface` — Reticulum bridge |

> Files in `bridge/` and `proto/` are **vendored copies** from `rns-engine`. When the contract changes there, they need to be synchronised here too.

## 3. Installation from Scratch

Run **on the VPS, from the repository root** of `TelegramHelper`:

```bash
bash scripts/install_ha_stack.sh
```

The installer is idempotent and does:

1. Installs `python3` / `python3-venv` / `python3-pip` (if `apt-get` is available).
2. Creates a venv in `ha_stack/.venv` and installs `ha_stack/requirements.txt` (`rns>=1.3.0`, `grpcio>=1.60.0`, `protobuf>=4.25.0`).
3. Creates the `ha_stack/.rnsdata` directory (stores the stable bridge identity).
4. Writes three systemd units and starts them (`enable --now`).
5. Extracts the bridge **destination hash** from the journal and prints it.

Port options (defaults are `50055/50056/50061`):

```bash
bash scripts/install_ha_stack.sh --grpc-port 50055 --udp-port 50056 --rns-port 50061
```

The service user for units equals the owner of the `ha_stack/` directory (determined automatically).

## 4. RNS Config — TCP Phase (Current)

`ha_stack/rns/config` currently:

```ini
[reticulum]
  enable_transport = No
  share_instance = No
  panic_on_interface_error = No

[logging]
  loglevel = 3

[interfaces]
  [[TCP Server Interface]]
    type = TCPServerInterface
    interface_enabled = yes
    listen_ip = 127.0.0.1
    listen_port = 50061
```

The bridge starts as (see `ha-reticulum-bridge.service`):

```bash
python -m bridge.run_bridge \
  --grpc 127.0.0.1:50055 \
  --udp-target 127.0.0.1:50056 \
  --config ha_stack/rns \
  --storage ha_stack/.rnsdata
```

What the bridge registers (`bridge/bridge.py`):

- request handler **`/device_control`** — accepts `CommandRequest` (proto), forwards to local gRPC, returns `CommandResponse`;
- request handler **`/udp_raw`** — forwards raw bytes to UDP stub `127.0.0.1:50056` and returns one response (or `b""` on timeout);
- stream path over `RNS.Channel` (phase 2): client sends `SubscribeMessage`, bridge streams `DeviceEventMessage` (events from `mi_th_sensor`).

The bridge re-announces every **60 seconds** so that clients can find the path to the destination.

## 5. Verification on VPS

```bash
# Service statuses
systemctl is-active ha-stub-grpc ha-stub-udp ha-reticulum-bridge

# Bridge destination hash (stable across restarts)
journalctl -u ha-reticulum-bridge -n 20 --no-pager | grep destination

# Ports listen only on localhost
ss -ltnp | grep -E '5005[56]|50061'
```

Expected: three `active`, journal line `bridge up, destination = <hash>`, ports on `127.0.0.1`.

## 6. Client Verification

By default the bridge listens **only** on `127.0.0.1:50061` — connecting to `YOUR_VPS_IP:50061` publicly will time out. There are two working paths to the bridge.

### 6.1. Mode B — SSH Tunnel (Recommended in Production)

**Manual:**

```bash
# local 50062 → VPS 127.0.0.1:50061 (50062 to avoid conflicts with i2pd)
ssh -L 50062:127.0.0.1:50061 -L 50055:127.0.0.1:50055 user@YOUR_VPS_IP
```

In the RNS client config (SSH/tunnel → localhost):

```ini
[[TCP Client Interface]]
  type = TCPClientInterface
  interface_enabled = yes
  target_host = 127.0.0.1
  target_port = 50062
```

One-time SSH config (`~/.ssh/config`):

```
Host ha-tunnel
    HostName YOUR_VPS_IP
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    LocalForward 127.0.0.1:50062 127.0.0.1:50061
    ServerAliveInterval 30
    ServerAliveCountMax 3
    ExitOnForwardFailure yes
```

The key from `IdentityFile` must be in `authorized_keys` on **this** VPS. After changing VPS, update `HostName`.

The bridge destination hash comes from the bridge journal (`destination = …`).

### 6.2. Mode A — Direct TCP to Public IP

For testing or trusted networks: expose the bridge externally (temporarily).

```bash
# on VPS
sed -i 's/listen_ip = 127.0.0.1/listen_ip = 0.0.0.0/' /opt/TelegramHelper/ha_stack/rns/config
systemctl restart ha-reticulum-bridge
# ufw/iptables: allow tcp/50061
ss -tlnp | grep 50061   # 0.0.0.0:50061 or *:50061
```

In the client: direct TCP mode, host `YOUR_VPS_IP`, port `50061`, same hash.

Revert to localhost:

```bash
sed -i 's/listen_ip = 0.0.0.0/listen_ip = 127.0.0.1/' /opt/TelegramHelper/ha_stack/rns/config
systemctl restart ha-reticulum-bridge
# close 50061 in firewall
```

> In production prefer SSH / I2P / Reality / Tailscale — do not expose `:50061` publicly.

### 6.3. CLI (UDP_gRPC_COM_Lite)

```bash
# 1) TCP gRPC direct (via tunnel :50055):
device send --device mi_bulb --block BU --cmd write --led on --protocol grpc
#   -> mi_bulb: power=on, brightness=80

# 2) Via Reticulum:
device send --device mi_th_sensor --block BU --cmd read \
  --protocol reticulum --bridge-hash <HASH> --rns-config <client_rns_dir>
#   -> mi_th_sensor: T=23.5C H=45%

# 3) Raw UDP via Reticulum (/udp_raw):
send --hex "01 00" --protocol reticulum --bridge-hash <HASH> --rns-config <client_rns_dir>
#   -> mi_bulb raw ok   (first byte 0x01 = bulb)
```

For direct TCP: `target_host = YOUR_VPS_IP`, `target_port = 50061`.

`--block` is ignored by the stub (routing is by `--device`), but the CLI requires a valid block — any of `BU/BZ/BF/SHSKM` will work.

The stub understands special IDs: `__ping__` (Ping) and `__list__` (dropdown). Without them the UI shows `unknown device: __ping__` / empty list even with a live path.

### 6.4. Next Step — Real HA (ha-adapter)

The stubs (`:50055`) are for testing only. While a new VPS is **not in the mesh**, there is no path to the HA server on the LAN (e.g. `100.64.0.2` as a CGNAT mesh address) — first connect to Headscale as a client, then install the adapter.

**A. Add VPS to mesh (client role)** — issue a key on the coordinator (`headscale` container):

```bash
# on the COORDINATOR
docker exec headscale headscale users list
# --user accepts ONLY the numeric ID, not the name:
docker exec headscale headscale preauthkeys create --user 1 --reusable --expiration 24h

# on the NEW VPS
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --login-server https://headscale.example.com:8443 --authkey '<KEY>'
tailscale status
curl -sS -o /dev/null -w '%{http_code}\n' --connect-timeout 5 http://100.64.0.2:8123/
# expected: 200 or 401 — not timeout
```

See [`HEADSCALE_GUIDE.md`](HEADSCALE_GUIDE.md) for details (client role, `--user` = numeric ID).

**B. Once curl to HA from the VPS responds** — install the adapter with one command:

```bash
bash scripts/install_ha_adapter.sh \
  --ha-url http://100.64.0.2:8123 --ha-token '<LONG_LIVED>' \
  --switch-bridge --public-rns
```

Unit `ha-adapter-grpc` (`:50057`), bridge → `--grpc …:50057`. In UI: Ping → `HA API alive`, list — HA entities (`backend: real-ha`).

## 7. Destination Hash of the Bridge

`<HASH>` is the address of the bridge destination in the Reticulum network. The client specifies it via `--bridge-hash`.

- Printed by the installer at the end of installation.
- Retrieved manually:
  ```bash
  journalctl -u ha-reticulum-bridge | grep destination
  ```
- **Stable** across restarts and `git pull` — identity is stored in `ha_stack/.rnsdata/bridge_identity`. No need to update `--bridge-hash` on clients.
- If `bridge_identity` is deleted (or the venv is recreated from scratch after a major Python/protobuf change) — the hash **will change**, then distribute the new one to clients.

## 8. I2P Phase (e2e ✅ — Path 2: Native i2pd Tunnels)

The goal is to make the bridge available **as an I2P hidden service**, without a public port and without an SSH tunnel. The key idea: **bridge code, stubs, and even the RNS config do not change** — RNS stays on `TCPServerInterface 127.0.0.1:50061`. I2P is added **alongside** as a separate `i2pd` daemon.

> ⚠️ **Path 1 (RNS `type = I2PInterface` via SAM) was abandoned** — it does not start on this stack (endpoint hangs in "Bringing up I2P endpoint" and fails with `SAM API went offline` → leaseset not published; `RNS 1.3.5` / Python 3.11, i2pd healthy). Therefore the RNS config is **not switched** to I2P. Details — `RETICULUM_GUIDE.md` §8.

**Path 2 (working):** i2pd wraps the bridge's local TCP into an I2P destination via its own **server tunnel**; the client symmetrically — via a **client tunnel** exposes a local port that a standard `TCPClientInterface` RNS connects to. SAM is not needed.

```text
RNS-client ↔ TCP ↔ i2pd(client-tunnel) ↔ I2P ↔ i2pd(server-tunnel) ↔ TCP ↔ bridge
```

Plan on VPS:

1. Install i2pd (SAM **not** needed), set bandwidth class:
   ```bash
   apt update && apt install -y i2pd
   sed -i -E 's/^#?\s*bandwidth\s*=.*/bandwidth = P/' /etc/i2pd/i2pd.conf
   systemctl enable --now i2pd
   ```
2. Create a **server tunnel** for the bridge's local port `/etc/i2pd/tunnels.d/ha-bridge.conf`:
   ```ini
   [ha-bridge]
   type = server
   host = 127.0.0.1
   port = 50061
   keys = ha-bridge.dat
   inbound.length = 2
   outbound.length = 2
   inbound.quantity = 3
   outbound.quantity = 3
   ```
   `systemctl restart i2pd`. Get the **b32** (bridge address in I2P):
   ```bash
   curl -s "http://127.0.0.1:7070/?page=i2p_tunnels" | sed 's/<[^>]*>/ /g' | grep -iE 'ha-bridge|\.b32'
   ```
3. **Do not touch** `ha_stack/rns/config` — the bridge stays on `TCPServerInterface` (which is what the server tunnel wraps). No need to restart the bridge.

Plan on the client (symmetrically, SAM not needed):

1. i2pd + **client tunnel** to bridge's b32:
   ```ini
   [ha-bridge-client]
   type = client
   address = 127.0.0.1
   port = 50061
   destination = <BRIDGE_B32>
   destinationport = 50061
   keys = ha-bridge-client.dat
   ```
   A local listener `127.0.0.1:50061` will appear.
2. RNS client config — standard `TCPClientInterface` on `127.0.0.1:50061`. `--bridge-hash` is **the same** (destination is transport-independent) — only the delivery method changes.

Key things to keep in mind:

- I2P has a long cold-start (tunnels take 30–120 s to build) — first connection may give `no path to bridge`; warm up and retry, the bridge re-announces every 60 s.
- SSH tunnel for `50061` is no longer needed — access is via I2P; `50055/50056` remain local (the bridge calls them on the VPS itself).
- I2P layer installation is a separate installer [`scripts/install_i2p_bridge.sh`](scripts/install_i2p_bridge.sh). Full guide: [RETICULUM_GUIDE.md](RETICULUM_GUIDE.md).

> Status: **I2P e2e green** (stream phase 2 passed over I2P). i2pd + server tunnel are installed by `scripts/install_i2p_bridge.sh` (see `RETICULUM_GUIDE.md`).

## 9. Update After `git pull`

```bash
# Stub/bridge code changed (stub_server.py, udp_stub.py, bridge/...):
sudo systemctl restart ha-stub-grpc ha-stub-udp ha-reticulum-bridge

# ha_stack/requirements.txt changed — update dependencies and restart:
ha_stack/.venv/bin/python -m pip install -r ha_stack/requirements.txt
sudo systemctl restart ha-stub-grpc ha-stub-udp ha-reticulum-bridge

# Clean reinstall (units + venv from scratch) — installer is idempotent:
bash scripts/install_ha_stack.sh
```

Verify after restart:

```bash
systemctl is-active ha-stub-grpc ha-stub-udp ha-reticulum-bridge
journalctl -u ha-reticulum-bridge -n 20 --no-pager | grep destination
```

## 10. Auto-Recovery (Soft Hang)

If Python/`rns` hangs but the PID is alive (`systemctl` → `active`, port `:50061` is listening, client cannot reach it) — this is a **soft hang**. The fact that the unit is active alone does not fix it; you need to restart the process and apply the preventive measures below.

**On a fresh VPS this is set up automatically:**
- `bash scripts/install_ha_stack.sh` → swap (if not present), `Restart=always` for the bridge, timer `ha-rns-watchdog` (port + heartbeat + gRPC), env `HA_RNS_HANDLER_TIMEOUT` / `HA_RNS_HEARTBEAT`, cron Monday 04:15 → `restart ha-reticulum-bridge`;
- `bash scripts/install_i2p_bridge.sh` → cron Monday 04:30 → `restart i2pd`.

Both installers are idempotent (do not duplicate cron/swap). To upgrade an already-running VPS without full reinstall:

```bash
bash scripts/upgrade_ha_rns_watchdog.sh
```

### 10.1. Swap (Required on ≤2 GiB RAM with no Swap)

Without swap, processes stall on low memory without an OOM kill — amplifying soft hangs.

```bash
# as root; skip if swap already exists
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
free -h   # Swap: 1.0Gi
```

### 10.2. Watchdog (Port + Heartbeat + gRPC) — Every 5 Minutes

`ha-rns-watchdog.timer` runs `/usr/local/bin/ha-rns-watchdog.sh`:

1. **`:50061` not listening** → `systemctl restart ha-reticulum-bridge`
2. **heartbeat** `/tmp/ha-rns-bridge.heartbeat` older than 90 s (written by `run_bridge`) → restart bridge (entire process hung, announce/loop also dead)
3. **gRPC backend** from `ExecStart --grpc` (usually `:50057` adapter) not responding within ~5 s → `restart ha-adapter-grpc` (if present) + bridge

Also in bridge code: if the `device_control` handler does not return within **25 s** (`HA_RNS_HANDLER_TIMEOUT`) — process calls `os._exit(78)`, systemd `Restart=always` brings it back up. This covers the "path exists, Ping DEADLINE_EXCEEDED" case.

Prefer not to copy the script manually — from the repo root on the VPS (`/opt/TelegramHelper`):

```bash
cd /opt/TelegramHelper
git pull
bash scripts/upgrade_ha_rns_watchdog.sh
# or full stack: bash scripts/install_ha_stack.sh
systemctl list-timers | grep ha-rns
ls -l /tmp/ha-rns-bridge.heartbeat    # appears ~20 s after restart
tail -n 30 /var/log/ha-rns-watchdog.log
```

Watchdog log: `/var/log/ha-rns-watchdog.log`.

### 10.3. Weekly Cron (Bridge + i2pd)

Preventive restart once a week (Monday night):

```bash
crontab -l 2>/dev/null | grep -q 'ha-reticulum-bridge' || \
  (crontab -l 2>/dev/null; echo '15 4 * * 1 systemctl restart ha-reticulum-bridge') | crontab -
crontab -l 2>/dev/null | grep -q 'restart i2pd' || \
  (crontab -l 2>/dev/null; echo '30 4 * * 1 systemctl restart i2pd') | crontab -
crontab -l | grep -E 'reticulum|i2pd'
```

After restarting `i2pd` — cold-start of 30–120 s; clients should wait for warmup, the address does not change.

### 10.4. Soft Hang Diagnostics

```bash
# OOM?
dmesg -T | grep -iE 'oom|killed process' | tail -20

# Who restarted the bridge (crash vs manual stop)?
journalctl -u ha-reticulum-bridge --since "2 days ago" --no-pager | tail -80
systemctl show ha-reticulum-bridge -p ActiveState,NRestarts,ExecMainStartTimestamp,MainPID

# Port + memory
ss -ltnp | grep -E '50061|50055|50057'
free -h

# I2P (values often on the next line after the label — see RETICULUM_GUIDE.md §7)
systemctl is-active i2pd
```

In bot / SSH CLI: `/reticulum_status`, `/reticulum_health`, `/reticulum_restart`.

## 11. Common Errors

| Symptom | Cause / Solution |
| --- | --- |
| Client cannot find bridge | SSH tunnel not set up (TCP phase); check alias `ha-tunnel` → `50062`, key on VPS. On I2P — tunnels still building, wait. |
| `Connection refused` on `127.0.0.1:50062` | Alias `ha-tunnel`: wrong `HostName`/key; test `ssh -o BatchMode=yes ha-tunnel "echo ok"`. |
| Ping `unknown device: __ping__` | Old stub without special IDs — `git pull` + `systemctl restart ha-stub-grpc`. |
| Direct `YOUR_VPS_IP:50061` timeout | Bridge is on `127.0.0.1` — use SSH (§6.1) or temporarily `listen_ip = 0.0.0.0` (§6.2). |
| `bridge-hash` does not match | Hash changed (deleted `bridge_identity` / recreated venv). Get new one from `journalctl -u ha-reticulum-bridge | grep destination`. |
| `ha-reticulum-bridge` won't start | Depends on `ha-stub-grpc`/`ha-stub-udp` (`After=/Wants=`). Check their status and `journalctl -u ha-reticulum-bridge`. |
| Service `active`, client silent (soft hang) | Process alive, path dead. `systemctl restart ha-reticulum-bridge` or `/reticulum_restart`. Prevention — §10 (swap, weekly cron, watchdog port+heartbeat+gRPC, handler timeout). |
| venv broken after Python version change | Recreate: `bash scripts/install_ha_stack.sh` (venv is recreated). Note — recreating venv may change nothing, but deleting `.rnsdata` will change the hash. |
| `unknown device` in response | Wrong `--device`. Valid values: `mi_bulb`, `mi_th_sensor`, `mi_vibration`. |
| Port busy | Change ports via `--grpc-port/--udp-port/--rns-port` options. |

## 12. Quick Reference

```bash
# Install (on VPS, from repo root)
bash scripts/install_ha_stack.sh

# Status + hash
systemctl is-active ha-stub-grpc ha-stub-udp ha-reticulum-bridge
journalctl -u ha-reticulum-bridge | grep destination

# Tunnel from your machine (TCP phase)
ssh -L 50055:127.0.0.1:50055 -L 50061:127.0.0.1:50061 user@YOUR_VPS_IP

# Test paths (from UDP_gRPC_COM_Lite)
device send --device mi_bulb --block BU --cmd write --led on --protocol grpc
device send --device mi_th_sensor --block BU --cmd read --protocol reticulum \
  --bridge-hash <HASH> --rns-config <client_rns_dir>

# Restart after git pull
sudo systemctl restart ha-stub-grpc ha-stub-udp ha-reticulum-bridge

# Soft hang / prevention — §10 (swap, ha-rns-watchdog.timer, weekly cron)
systemctl list-timers | grep ha-rns
crontab -l | grep -E 'reticulum|i2pd'

# I2P (path 2, e2e ✅): install i2pd + server tunnel ha-bridge on 127.0.0.1:50061,
# do NOT touch the RNS config (stays TCPServerInterface). Details — §8 / RETICULUM_GUIDE.md
```

---

See also: [`DEPLOY.md`](DEPLOY.md) §6.7, [`POST_DEPLOY.md`](POST_DEPLOY.md) §12,
[RETICULUM_GUIDE.md](RETICULUM_GUIDE.md) §7, [`ha_stack/README.md`](ha_stack/README.md).
