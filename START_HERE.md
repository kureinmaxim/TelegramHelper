# START_HERE — TelegramHelper

VPS runtime: Telegram bot, VPN transports, Headscale client/coordinator,
Reticulum bridge, and (optionally) a path to Home Assistant on a NAS.

This tree is the **public, slightly trimmed** edition. The full private
operator tree is **TelegramOnly**. Both share the same core **v3.19.5**.

| | **TelegramHelper** (this repo) | **TelegramOnly** (private) |
| --- | --- | --- |
| Who it is for | GitHub, fork, clean VPS install | The operator's full copy |
| Core | bot + API + transports + 3x-ui + Headscale + HA stubs | the same core |
| Version | v3.19.5 | v3.19.5 |
| Docs | English, project-only | Russian, plus operator runbooks |
| Trimmed here | host inventory, ApiX / telegram_capsule, trading desk, NovaScale, personal notes | — |
| Extra here | `/ai`, `/tr`, `/prompt` | — |

Architecture overview: [ARCHITECTURE.md](ARCHITECTURE.md).

## First Steps on a Fresh Debian VPS

1. **SSH + GitHub key** on the VPS → `git clone` into `/opt/TelegramHelper`.
2. **Master setup:** `bash scripts/vps_setup.sh`
   - bot (Docker/systemd) + `.env` (token, admin IDs; API secrets are generated automatically);
   - transports (VLESS/Hy2/…) as needed;
   - **Headscale client**, if you need a path to a NAS (`100.64.x`);
   - **HA + Reticulum** (optional stubs, not started by the bot) → bridge at `:50061`;
   - **+ ha-adapter** (real HA) — once the mesh is up and `HA_TOKEN` is set.
3. Verify the bot is running: send `/ver` in Telegram.
4. Optional Home Assistant path: bridge hash + TCP
   (`YOUR_VPS_IP:50061` if published, or an SSH tunnel to `127.0.0.1:50062`).

Then issue the first profile: `/special_add` → `/provision` → `/profiles`,
or let the user fetch `/my_profile`. Details: [QR_CLIENT_ONBOARDING.md](QR_CLIENT_ONBOARDING.md).

Only one TLS service can own `443/tcp`. Pick the owner before install
(VLESS, NaiveProxy, or leave 443 free and run Mieru on `29999`).
Table: [DEPLOY.md](DEPLOY.md).

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
| This public landing page | [README.md](README.md) |
