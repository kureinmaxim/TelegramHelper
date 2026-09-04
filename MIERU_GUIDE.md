# MIERU_GUIDE.md — Mieru in TelegramHelper

Practical reference for Mieru in TelegramHelper: what this transport is,
how to install it on a VPS, which bot commands are available, what settings
matter for DPI research, and how to deliver Mieru profiles to users.

## 0. Key Points

Mieru is a censorship-circumvention proxy from the `enfein/mieru` project.
It consists of two components:

| Component | Where it runs | Role |
| --- | --- | --- |
| `mita` | VPS / Linux | Server component; listens on TCP/UDP ports |
| `mieru` | Client device | Local SOCKS5/HTTP proxy and tunnel to `mita` |

```text
Application / browser / Clash Meta
  -> local SOCKS5 of the mieru client, e.g. 127.0.0.1:10810
  -> Mieru TCP or UDP transport
  -> mita server on the VPS
  -> Internet
```

What DPI sees:

- not TLS/HTTPS camouflage like VLESS-Reality or NaiveProxy;
- encrypted Mieru segments with random padding;
- TCP or UDP stream on the selected port/port range;
- with a traffic pattern configured — a more controlled fragment shape.

Key difference: Mieru **does not require a domain or a TLS certificate**. This is convenient if you have no domain or HTTPS masking is unstable. However, it also means Mieru does not disguise itself as an ordinary website as naturally as NaiveProxy does.

## 1. When to Choose Mieru

Use Mieru if:

- you need a fallback TCP/UDP transport without a domain;
- you want to test multiple ports or a port range;
- VLESS/Hysteria2/NaiveProxy are unstable on a particular network;
- you need a per-user username/password rather than a shared `basic_auth`;
- DPI experiments with TCP/UDP, MTU, multiplexing, handshake, and padding are important.

Do not choose Mieru as your first protocol if:

- VLESS-Reality on `443/tcp` is already stable;
- you need the most "plain HTTPS" appearance — NaiveProxy is the better choice;
- the network completely blocks unknown TCP/UDP streams by IP/port;
- the client application does not support Mieru or `mieru://`/`mierus://`.

## 2. Port Compatibility

| Protocol | Port | Conflict |
| --- | ---: | --- |
| VLESS-Reality | `443/tcp` | conflicts with Mieru TCP on the same port |
| Hysteria2 | `443/udp` | conflicts with Mieru UDP on the same port |
| NaiveProxy | `443/tcp` | conflicts with Mieru TCP on `443` |
| Mieru TCP | any `1025..65535/tcp` | choose a dedicated port or range |
| Mieru UDP | any `1025..65535/udp` | choose a dedicated port or range |

For initial testing, avoid `443` and use a separate port such as `29999/tcp`. If you need a UDP profile, add a separate `29999/udp` or a different port to avoid confusion with Hysteria2.

## 3. Files and Services

| Item | Path |
| --- | --- |
| Bot-side JSON | `/opt/TelegramHelper/mieru_config.json` |
| Manager code | `mieru_manager.py` |
| Installer | `scripts/install_mieru.sh` |
| Real server config for `mita apply config` | `/etc/mieru/server_config.json` |
| Server binary | `mita` |
| Client binary | `mieru` |
| systemd service | `mita.service` |
| Bot container | `telegram-helper-lite` |

Official management model:

```bash
mita apply config server_config.json
mita describe config
mita start
mita stop
mita reload
mita status
```

For the client:

```bash
mieru apply config client_config.json
mieru describe config
mieru start
mieru stop
mieru test
mieru export config
mieru export config simple
```

After changing server ports or MTU, a `mita stop` + `mita start` is typically required.
If only `users` or `loggingLevel` change, `mita reload` is sufficient.

## 4. Installation on a VPS

### 4.1 Via the Telegram Bot

After updating the VPS and starting the current container:

```text
/mieru_install
/mieru_set_server YOUR_VPS_IP
/mieru_set_port 29999 tcp
/mieru_add_client phone
/mieru_apply
/mieru_start
/mieru_status
/mieru_export phone
```

`/mieru_install` runs `scripts/install_mieru.sh` on the host via `host_utils.host_run`, installs the `mita` server component, and enables NTP.

### 4.2 Via the Fresh VPS Script

`scripts/deploy_fresh_vps.sh` includes a dedicated option:

```text
[5] Mieru (mita) — dedicated TCP/UDP port, default 29999/tcp
```

This option does not occupy `443/tcp` and does not conflict with VLESS/NaiveProxy as long as you do not manually assign Mieru the same port. The script installs `mita`, opens the selected port in UFW, and creates a basic `/opt/TelegramHelper/mieru_config.json`.

### 4.3 Manual Installation via the Official Installer

Official quick path:

```bash
curl -fSsLO https://raw.githubusercontent.com/enfein/mieru/refs/heads/main/tools/setup.py
chmod +x setup.py
sudo python3 setup.py
```

Manual path for Debian/Ubuntu amd64:

```bash
curl -LSO https://github.com/enfein/mieru/releases/download/v3.32.0/mita_3.32.0_amd64.deb
sudo dpkg -i mita_3.32.0_amd64.deb
sudo usermod -a -G mita "$USER"
```

After adding the user to the `mita` group, re-login via SSH.

Verify:

```bash
systemctl status mita --no-pager
mita status
```

Immediately after installation the status may be `IDLE`: this means the server has not received a working config yet and is not listening on any port.

## 5. Server Config

Minimal server config:

```json
{
  "portBindings": [
    {
      "port": 29999,
      "protocol": "TCP"
    }
  ],
  "users": [
    {
      "name": "Mieru_ID82_09",
      "password": "STRONG_RANDOM_PASSWORD"
    }
  ],
  "loggingLevel": "INFO",
  "mtu": 1400
}
```

Apply:

```bash
mita apply config server_config.json
mita describe config
mita start
mita status
```

Firewall:

```bash
ufw allow 29999/tcp
ufw status | grep 29999
```

For UDP:

```json
{
  "portBindings": [
    {
      "port": 29999,
      "protocol": "UDP"
    }
  ],
  "mtu": 1400
}
```

Open the firewall:

```bash
ufw allow 29999/udp
```

## 6. Client Config

Minimal client config:

```json
{
  "profiles": [
    {
      "profileName": "TelegramHelper-Mieru",
      "user": {
        "name": "Mieru_ID82_09",
        "password": "STRONG_RANDOM_PASSWORD"
      },
      "servers": [
        {
          "ipAddress": "YOUR_VPS_IP",
          "domainName": "",
          "portBindings": [
            {
              "port": 29999,
              "protocol": "TCP"
            }
          ]
        }
      ],
      "mtu": 1400,
      "multiplexing": {
        "level": "MULTIPLEXING_LOW"
      },
      "handshakeMode": "HANDSHAKE_STANDARD"
    }
  ],
  "activeProfile": "TelegramHelper-Mieru",
  "rpcPort": 8964,
  "socks5Port": 10810,
  "loggingLevel": "INFO",
  "socks5ListenLAN": false
}
```

Apply on the client:

```bash
mieru apply config client_config.json
mieru describe config
mieru start
mieru test https://httpbin.org/ip
```

Smoke test via SOCKS5:

```bash
curl -sS --socks5-hostname 127.0.0.1:10810 https://httpbin.org/ip
```

## 7. Bot Commands

Available admin commands:

| Command | Purpose |
| --- | --- |
| `/mieru_status` | Show JSON, `mita status`, ports, and client count |
| `/mieru_config` | Show current config without secrets |
| `/mieru_set_server <ip_or_domain>` | Set the public VPS address |
| `/mieru_set_port PORT PROTOCOL` | Set port and protocol |
| `/mieru_set_mtu VALUE` | Set MTU |
| `/mieru_set_multiplexing LEVEL` | Multiplexing level |
| `/mieru_set_handshake MODE` | Handshake mode |
| `/mieru_set_socks5_port PORT` | Client local SOCKS5 port |
| `/mieru_gen_password` | Generate a password for the default client |
| `/mieru_add_client <name>` | Add a client |
| `/mieru_list_clients` | List clients |
| `/mieru_del_client <name>` | Delete a client |
| `/mieru_apply [reload]` | Write config and restart/reload `mita` |
| `/mieru_start`, `/mieru_stop`, `/mieru_restart` | Manage `mita` |
| `/mieru_logs [N]` | Last N systemd log lines |
| `/mieru_export [name]` | URI, client JSON, Clash/mihomo YAML, Clash Meta profile |
| `/mieru_set_dpi <param> <value>` | Fine-grained parameters for DPI research |

Mieru is also included in the unified user delivery flow:

```text
/provision <telegram_id>
/profiles <telegram_id>
/my_profile
/email_profile <telegram_id>
```

Bot-managed client name format:

```text
Mieru_ID<first2>_<last2>
```

After creating/deleting/rotating a Mieru client via the user card, apply users to the server:

```text
/mieru_apply reload
```

For port/MTU/protocol changes, a full apply/restart is required:

```text
/mieru_apply
```

## 8. Fine-Tuning for DPI Research

Command:

```text
/mieru_set_dpi <param> <value>
```

Parameters:

| Parameter | Values | What it changes | Requires `/mieru_apply` |
| --- | --- | --- | --- |
| `protocol` | `tcp`, `udp` | Mieru transport between client and VPS | Yes |
| `port` | `1025..65535` | Public `mita` port | Yes |
| `port_range` | `20000-20010` | Port range for randomization | Yes |
| `mtu` | `1280..1500` | UDP payload/fragmentation size | Yes |
| `multiplexing` | `off`, `low`, `middle`, `high` | How many streams to combine | Re-export client |
| `handshake` | `standard`, `no_wait` | Standard handshake or 0-RTT | Re-export client |
| `socks5_port` | `1025..65535` | Client local SOCKS5 port | Re-export client |
| `logging` | `debug`, `info`, `warn`, `error` | Log level | Yes |

### 8.1 Parameter Glossary

`TCP` — the faster, recommended Mieru transport for most networks. Easier to diagnose and more likely to pass where UDP is filtered.

`UDP` — an alternative transport. Can be useful on specific networks but requires careful MTU tuning and is more often broken by firewalls/NAT.

`portBindings` — the list of ports or port ranges that `mita` listens on. The client can randomly pick one of the specified ports for each new connection.

`MTU` — the maximum transport payload size for UDP. Accepted range is `1280..1500`, but the recommended practical range for initial tests is `1280..1400`. Lower MTU reduces fragmentation risk but increases overhead.

`multiplexing` — combining multiple logical connections inside a single Mieru transport. `HIGH` can improve efficiency but changes the traffic profile; `LOW` or `OFF` is simpler for a baseline.

`handshakeMode` — the connection establishment mode. `HANDSHAKE_STANDARD` is more conservative. `HANDSHAKE_NO_WAIT` enables 0-RTT and can speed up connection start, but test it separately.

`trafficPattern` — advanced traffic-shape configuration: fragment/padding/nonce patterns. A powerful knob for DPI research, best added after the basic integration is working and with a separate export/explanation.

`time sync` / NTP — critical for Mieru: the encryption key depends on username/password and the system clock. If the client and server clocks differ by more than a few minutes, the connection may not work.

## 9. Diagnostics

Via the bot:

```text
/mieru_status
/mieru_config
/mieru_logs 80
/diag
```

On the VPS:

```bash
systemctl status mita --no-pager
journalctl -u mita -n 100 --no-pager
mita status
mita describe config
ss -lntup | grep -E ':(29999)\b'
timedatectl status
```

On the client:

```bash
mieru describe config
mieru start
mieru test https://httpbin.org/ip
curl -sS --socks5-hostname 127.0.0.1:10810 https://httpbin.org/ip
```

Check time sync:

```bash
timedatectl status
```

If time is not synchronized, enable NTP:

```bash
timedatectl set-ntp true
```

## 10. Common Errors

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `mita status` shows `IDLE` | Config not applied or service not started | `mita apply config`, then `mita start` |
| Client cannot connect | Firewall port not open | `ufw allow <port>/tcp` or `/udp` |
| Auth fails | Username/password mismatch | Re-issue config/URI |
| UDP connection unstable | MTU/fragmentation/NAT | Lower MTU, try TCP |
| Config change has no effect | Service not restarted | `mita stop && mita start` or `mita reload` for users |
| Everything configured but not working | Clock desync | Check NTP on both VPS and client |
| Conflict with VLESS/Hysteria2/NaiveProxy | Same port/protocol occupied | Choose a separate port |

## 11. Quick Reference

Via the bot:

```text
/mieru_install
/mieru_set_server YOUR_VPS_IP
/mieru_set_port 29999 tcp
/mieru_add_client phone
/mieru_apply
/mieru_start
/mieru_export phone
```

```bash
# VPS
mita apply config /etc/mieru/server_config.json
mita start
mita status
journalctl -u mita -n 80 --no-pager
```

```bash
# Client
mieru apply config client_config.json
mieru start
mieru test https://httpbin.org/ip
curl -sS --socks5-hostname 127.0.0.1:10810 https://httpbin.org/ip
```

Core rule: Mieru requires synchronized clocks, matching username/password, identical server/client port bindings, and an open firewall for the selected TCP/UDP port.

## 12. Python Dependencies

No new Python packages are needed for the current Mieru integration:

- `mieru_manager.py` uses the standard library (`json`, `os`, `secrets`, `urllib.parse`, `threading`, `datetime`);
- host-service management goes through the existing `host_utils.host_run`;
- QR generation uses the already-included `qrcode[pil]`.

`requirements.txt` does not need to be changed.
