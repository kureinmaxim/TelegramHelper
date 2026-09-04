# START_HERE — TelegramHelper

VPS runtime: Telegram bot, VPN transports, Headscale client/coordinator,
Reticulum bridge, and (optionally) a path to Home Assistant on a NAS.

## First Steps on a Fresh Debian VPS

1. **SSH + GitHub key** on the VPS → `git clone` into `/opt/TelegramHelper`.
2. **Master setup:** `bash scripts/vps_setup.sh`
   - bot (Docker/systemd) + `.env` (token, admin IDs; API secrets are generated automatically);
   - transports (VLESS/Hy2/…) as needed;
   - **Headscale client**, if you need a path to a NAS (`100.64.x`);
   - **HA + Reticulum** (stubs) → bridge at `:50061`, hash in journal;
   - **+ ha-adapter** (real HA) — once the mesh is up and `HA_TOKEN` is set.
3. Verify the bot is running: send `/ver` in Telegram.
4. Optional Home Assistant path: bridge hash + TCP
   (`YOUR_VPS_IP:50061` if published, or an SSH tunnel to `127.0.0.1:50062`).

## Component Overview

| Component | Purpose |
|---|---|
| Telegram bot | VPN profiles, admin commands, `/ver` |
| VLESS / Hy2 / … | Transport protocols for clients |
| Headscale client | VPS can reach NAS at `100.64.0.2` |
| `ha-stub-*` | Test Ping/devices without HA |
| `ha-reticulum-bridge` | RNS `:50061`, hash for clients |
| `ha-adapter-grpc` | Real HA REST over Tailnet |

## Useful Commands

```bash
cd /opt/TelegramHelper
bash scripts/vps_setup.sh
bash scripts/install_ha_adapter.sh --ha-url http://100.64.0.2:8123 \
  --ha-token '…' --switch-bridge --public-rns
journalctl -u ha-reticulum-bridge -n 30 --no-pager | grep destination
```

## Further Reading

| Topic | File |
|---|---|
| Deploy / master setup | [DEPLOY.md](DEPLOY.md) |
| Reticulum, SSH vs direct TCP, adapter | [RETICULUM_GUIDE.md](RETICULUM_GUIDE.md) §6 |
| Headscale (`--user` = numeric ID) | [HEADSCALE_GUIDE.md](HEADSCALE_GUIDE.md) |
| Scripts reference | the `scripts/` directory |
