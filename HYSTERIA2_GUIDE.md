# Hysteria2 — Practical Reference

> Hysteria2 in this project is a UDP/QUIC transport for VPN profiles. The primary user delivery path is the bot-managed flow: `/provision`, `/profiles`, `/my_profile`, `/email_profile`.

## What It Is

Hysteria2 runs over QUIC/UDP. It works well alongside VLESS-Reality:

| Protocol | Port | Purpose |
| --- | ---: | --- |
| VLESS-Reality | `443/tcp` | Stable TCP transport with TLS masking |
| Hysteria2 | `443/udp` | Fast UDP/QUIC transport for lossy networks |

TCP and UDP on the same port number do not conflict. VLESS-Reality and Hysteria2 can both use port `443` simultaneously, provided the VPS firewall has both `443/tcp` and `443/udp` open.

## Implementation

Key files:

| File | Role |
| --- | --- |
| `hysteria2_manager.py` | Stores settings; generates server/client configs, URIs, QR codes, and subscriptions |
| `handlers.py` | Telegram commands `/hy2_*`, `/provision`, `/profiles`, `/my_profile`, `/email_profile` |
| `hysteria2_config.json` | Bot-side config with server, port, client passwords, and QUIC settings |
| `/etc/hysteria/config.yaml` | Actual server config on the VPS |
| `/etc/hysteria/server.crt`, `/etc/hysteria/server.key` | Self-signed TLS certificate and key |

The server config is generated from `hysteria2_config.json`. If clients are present, the server uses `auth.type: userpass`, and the client password is exported as `name:password`. This is important: a simple `hy2://password@...` only works for the old single-password mode.

## Basic Setup

Run in the Telegram bot as an admin:

```bash
/hy2_install
/hy2_gen_all
/hy2_set_port 443
/hy2_set_masquerade https://yahoo.com
/hy2_set_quic_safe 1
/hy2_apply
/hy2_start
/hy2_status
```

Command reference:

| Command | Description |
| --- | --- |
| `/hy2_install` | Installs the Hysteria2 binary and verifies `hysteria-server.service` |
| `/hy2_gen_all` | Generates a password, self-signed certificate, SNI, and tries to detect the public IP |
| `/hy2_set_server <ip_or_domain>` | Manually sets the VPS address if auto-detection fails |
| `/hy2_set_port <port>` | Sets the UDP port, usually `443` |
| `/hy2_set_sni` / `/hy2_set_sni <domain>` | Button menu (default `yahoo.com`) or custom domain; syncs masquerade + regenerates the self-signed cert (CN=SNI) |
| `/hy2_set_insecure 1` | Required when using a self-signed certificate |
| `/hy2_set_quic_safe 1` | Enables safe QUIC defaults for Windows clients and MTU-problematic networks |
| `/hy2_set_quic <param> <value>` | Fine-tunes individual `quic` block parameters for experimentation |
| `/hy2_apply` | Writes `/etc/hysteria/config.yaml` and restarts the service |
| `/hy2_start`, `/hy2_stop`, `/hy2_restart` | Manage `hysteria-server.service` |
| `/hy2_logs [N]` | Show the last N systemd log lines |

After changing port, SNI, obfs, speed, masquerade, QUIC settings, or clients, always run:

```bash
/hy2_apply
```

`/hy2_apply` rewrites `/etc/hysteria/config.yaml` and restarts `hysteria-server.service`. A separate `/hy2_restart` is only needed if you edited host files manually or want to force-restart the service again.

## Delivering Profiles to Users

The recommended path is the unified user card flow, not the manual `/hy2_add_client` commands.

```bash
/special_add 123456789
/provision 123456789
/profiles 123456789
/email_profile 123456789
```

Flow:

1. `/special_add <telegram_id>` — grants the user permission to receive profiles.
2. `/provision <telegram_id>` — creates bot-managed profiles, including Hysteria2 if the protocol is configured.
3. `/profiles <telegram_id>` — shows the user card with QR/URI buttons.
4. `/email_profile <telegram_id>` — sends profiles by email and removes old Telegram QR/URL messages.

The Hysteria2 client name is built automatically from the Telegram ID. This ensures `/my_profile`, `/clean_user`, `/email_profile`, and profile audits work consistently across all protocols.

## Why Old and New Links Can Work Simultaneously

This is expected behavior for Hysteria2 in `userpass` mode.

When `hysteria2_config.json` has multiple clients, `/hy2_apply` generates a runtime config `/etc/hysteria/config.yaml` roughly like:

```yaml
auth:
  type: userpass
  userpass:
    legacy_user: "..."
    Hys_ID12_78: "..."
```

Each line in `userpass` is an independent valid login. Therefore:

- an old link continues to work as long as the old user (e.g. `legacy_user`) remains in `userpass`;
- a new link works once the bot-managed user (e.g. `Hys_ID12_78`) has been added;
- `/clean_user <id> YES` and the `/my_profile` auto-cleanup delete only bot-managed names (`Hys_ID12_78`, `Vless_ID12_78`, …) but leave manual clients (`legacy_user`, `phone`, `laptop`) untouched to avoid breaking existing devices.

To revoke an old link, delete that specific client and re-apply:

```bash
/hy2_del_client legacy_user
/hy2_apply
```

Or verify/clean manually via SSH in `hysteria2_config.json`, then run `/hy2_apply` again.

## Verifying That a New Client Is in the Runtime

After `/provision <telegram_id>`, Hysteria2 must appear in two places:

| Location | What it means |
| --- | --- |
| `/opt/TelegramHelper/hysteria2_config.json` | Bot-side source of truth |
| `/etc/hysteria/config.yaml` | Runtime config actually read by `hysteria-server.service` |

Check for user with Telegram ID `1290265278` (canonical name `Hys_ID12_78`):

```bash
cd /opt/TelegramHelper

echo "=== bot-side config ==="
grep -F "Hys_ID12_78" hysteria2_config.json || echo "NOT IN hysteria2_config.json"

echo "=== runtime config ==="
grep -F "Hys_ID12_78" /etc/hysteria/config.yaml || echo "NOT IN /etc/hysteria/config.yaml"

echo "=== service status ==="
systemctl status hysteria-server --no-pager
```

If the client is in `hysteria2_config.json` but absent from `/etc/hysteria/config.yaml`, the runtime has not been applied yet:

```bash
/hy2_apply
```

Starting with version `3.10.6`, `/provision <id>` automatically attempts to apply the Hysteria2 runtime config after creating or finding an existing bot-managed client. If apply failed, the reason should be visible directly in the `/provision` response.

Check the logs after connecting with a new link:

```bash
journalctl -u hysteria-server -n 80 --no-pager \
  | grep -E 'Hys_ID12_78|connected|auth|error'
```

A successful connection produces a log entry such as:

```text
client connected {"addr": "...", "id": "Hys_ID12_78", ...}
```

If the log only shows `id: "legacy_user"`, the client application is still using the old link/profile rather than the new bot-managed one.

## Legacy Client Commands

These commands are kept for older scenarios but should not be used in normal operation:

```bash
/hy2_add_client <name>
/hy2_qr <name_or_password>
/hy2_list_clients
/hy2_del_client <name>
```

The direct per-client flow is guarded by a legacy guard: the bot suggests switching to `/provision` and `/profiles`. This is intentional — it prevents "manual" clients that fall outside the shared cleanup, email delivery, and user-card system.

## Export

For general Telegram-only profile delivery:

```bash
/tgcapsule_export
```

Export buttons provide:

| Format | Use case |
| --- | --- |
| sing-box v2 Hysteria2 | Import into sing-box / Clash Meta |
| sing-box Hysteria2 | Direct JSON config for sing-box |
| Clash Meta Hysteria2 | YAML config for Clash Meta |

For a technical Hysteria2 export:

```bash
/hy2_export
```

This shows the default-client URI and provides buttons:

| Button | Result |
| --- | --- |
| `sing-box Profile` | Legacy profile JSON |
| `Client QR` | QR/URI for the selected Hysteria2 client |
| `Sing-box Config` | Full sing-box client config |
| `Clash Meta Config` | Clash Meta YAML |
| `Server Config (YAML)` | Current `/etc/hysteria/config.yaml` |
| `Subscription (base64)` | Base64 subscription with `hy2://` links |

## Obfuscation and Speed

Salamander obfuscation:

```bash
/hy2_set_obfs salamander STRONG_RANDOM_PASSWORD
/hy2_apply
/hy2_restart
```

Disable:

```bash
/hy2_set_obfs off
/hy2_apply
/hy2_restart
```

Speed hints:

```bash
/hy2_set_speed 100 500
/hy2_apply
/hy2_restart
```

`0` means auto / no explicit hint:

```bash
/hy2_set_speed 0 0
```

## Fine-Tuning for DPI Research

Hysteria2 is QUIC/UDP. In practice, DPI reacts not to a single parameter but to a combination: UDP port, TLS SNI, self-signed vs. valid cert, QUIC behavior, masquerade, and Salamander obfs. The following knobs are available in the bot:

| What to change | Command | What to investigate |
| --- | --- | --- |
| UDP port | `/hy2_set_port 443` | `443/udp` looks natural as HTTPS/QUIC; alternatives `8443`, `2087`, `2096`, `7844` may help with targeted filtering |
| SNI | `/hy2_set_sni` (buttons; default `yahoo.com`) | Whether SNI is consistent with masquerade and cert CN |
| Masquerade | `/hy2_set_masquerade https://yahoo.com` | What the server returns to a non-Hysteria client making a normal HTTPS request |
| Salamander obfs | `/hy2_set_obfs salamander <strong_random>` | Hides the Hysteria2 UDP payload, but requires client support and the same password in the URI |
| Bandwidth hints | `/hy2_set_speed 0 0` or `/hy2_set_speed 100 500` | Affects congestion/speed behavior; not a "disguise" itself but changes the traffic profile |
| Safe QUIC defaults | `/hy2_set_quic_safe 1` | Handshake stability on Windows / MTU-problematic networks |
| QUIC windows/timeouts | `/hy2_set_quic <param> <value>` | Careful A/B tests of keepalive, idle timeout, and receive windows |

Available `/hy2_set_quic` parameters:

```bash
/hy2_set_quic enabled 1
/hy2_set_quic disable_path_mtu_discovery 1
/hy2_set_quic init_stream_receive_window 1048576
/hy2_set_quic max_stream_receive_window 8388608
/hy2_set_quic init_conn_receive_window 2097152
/hy2_set_quic max_conn_receive_window 16777216
/hy2_set_quic max_idle_timeout 30s
/hy2_set_quic keep_alive_period 10s
```

### Fine-Tuning Glossary

`UDP port` — the external Hysteria2 port on the VPS. Port `443/udp` resembles normal HTTP/3/QUIC. TCP port `443` for VLESS-Reality and UDP port `443` for Hysteria2 can coexist, but the firewall must specifically allow UDP.

`QUIC` — the UDP-based transport that Hysteria2 runs on. It recovers faster from packet loss and network changes, but depends entirely on whether the ISP passes UDP. If UDP is completely blocked, QUIC settings cannot fix that.

`SNI` — the domain name in the TLS ClientHello. For masking it must look natural and be consistent with the `masquerade`/certificate. An unrelated or suspicious SNI can worsen the DPI profile.

`Masquerade` — the HTTP(S) response the server returns to a non-Hysteria client. This ensures the domain looks like a real website when probed by a browser or scanner.

`Salamander obfs` — obfuscation of the Hysteria2 UDP payload. It helps hide the characteristic Hysteria2 traffic pattern, but requires client support and an exact password match. A mismatch means the client will not connect.

`Bandwidth hints` (`up_mbps`, `down_mbps`) — approximate throughput hints for the client/server. This is not masking per se, but it can change congestion behavior and traffic profile. `0 0` means auto / no explicit cap.

`Safe QUIC defaults` — a set of more conservative QUIC settings for MTU-problematic networks and Windows clients. Enabled via `/hy2_set_quic_safe 1`; a good starting point.

`disable_path_mtu_discovery` — disables Path MTU Discovery. Useful when the network breaks ICMP/fragmentation and QUIC packets get "stuck" due to incorrect MTU. Downside: may reduce maximum transfer efficiency.

`init_stream_receive_window` and `max_stream_receive_window` — initial and maximum receive window for a single QUIC stream. A larger window can improve speed on long-latency routes, but very large values increase memory usage and may change the traffic fingerprint.

`init_conn_receive_window` and `max_conn_receive_window` — the same windows but for the entire QUIC connection. Usually raised together with stream windows when testing throughput at high latency.

`max_idle_timeout` — how long a connection can be idle before being closed. A larger value helps on unstable networks but may keep "dead" connections open.

`keep_alive_period` — how often the client/server sends keepalives to prevent the connection from being considered idle. A shorter period helps NAT/mobile networks but produces a more regular background traffic pattern.

After any such change:

```bash
/hy2_apply
/hy2_status
```

Recommended research order:

1. Record a baseline: `/hy2_status`, `/hy2_export`, `journalctl -u hysteria-server -n 80 --no-pager`, connectivity test from one client.
2. Change **one parameter at a time**.
3. After each change, update the client profile/URI if the parameter appears in the URI (`sni`, `insecure`, `obfs`, `obfs-password`, port).
4. Test from different networks: home ISP, mobile data, problematic Wi-Fi.
5. If UDP is completely blocked, Hysteria2 cannot help: use VLESS-Reality TCP as a fallback transport.

Practical profiles:

```bash
# Simplest stable baseline
/hy2_set_obfs off
/hy2_set_speed 0 0
/hy2_set_quic_safe 1

# A more "closed" variant for testing
/hy2_set_obfs salamander VERY_LONG_RANDOM_SECRET
/hy2_set_sni yahoo.com
/hy2_set_masquerade https://yahoo.com
/hy2_set_quic keep_alive_period 15s
/hy2_set_quic max_idle_timeout 45s
```

Do not change everything at once. For DPI research it is more useful to keep a short table: `network → profile → connection/speed/logs` rather than a "magic" parameter set.

## Firewall and Checks

The VPS must have the UDP port open:

```bash
ufw allow 443/udp
ufw status | grep 443
```

Useful SSH commands:

```bash
systemctl status hysteria-server --no-pager
journalctl -u hysteria-server -n 80 --no-pager
sed -n '1,220p' /etc/hysteria/config.yaml
ls -la /etc/hysteria/server.crt /etc/hysteria/server.key
ss -lunp | grep ':443'
```

In Docker mode, the bot manages systemd on the host via `nsenter`; therefore `/hy2_start`, `/hy2_apply`, and `/hy2_logs` require host PID/privileged settings in the compose file — see `DOCKER.md`.

## Common Problems

| Symptom | What to check |
| --- | --- |
| Client cannot connect | Is `443/udp` open, is `hysteria-server` running, is the server IP/domain correct |
| `TLS handshake error` | Is `/hy2_set_insecure 1` enabled for a self-signed certificate |
| `auth failed` | Client must use `name:password` if the server is in `userpass` mode |
| Windows client hangs on QUIC handshake | Enable `/hy2_set_quic_safe 1`, then `/hy2_apply` and `/hy2_restart` |
| ISP blocks UDP | Hysteria2 will not help; use VLESS-Reality over TCP |
| `/hy2_apply` writes config but service does not start | Check `/hy2_logs 80` and `systemctl status hysteria-server --no-pager` |
| Server is healthy, port is open, but client cannot connect over mobile data | Likely ISP DPI filtering by `sni`/`masquerade` (see VLESS_GUIDE.md §8 — same mechanism for Reality); try changing `/hy2_set_sni` and `/hy2_set_masquerade` to a domain less common in VPN guides (e.g. `yahoo.com`), then `/hy2_apply` |

## Related Documents

- `DOCKER.md` — Docker, host access, and secure Docker API
- the `scripts/` directory — commands and scripts
- `VLESS_GUIDE.md` — Reality/VLESS as the TCP counterpart to Hysteria2
