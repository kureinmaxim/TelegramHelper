# NGINX_SNI_ROUTING.md — Nginx, SNI Routing, and Port 443

This document covers a specific but important scenario: keeping **VLESS-Reality on TCP/443** while simultaneously serving plain HTTPS for Headscale / Home Assistant / API on separate domains from the same VPS.

In short: all TCP/443 traffic is received by **Xray**. VLESS clients are handled by Xray, while ordinary HTTPS goes to a **fallback** on a local Nginx `stream` router (`127.0.0.1:8443`). Nginx reads the SNI from the TLS ClientHello and proxies the stream to the appropriate backend.

---

## 1. When to Use This

Use SNI routing when all of the following are true:

- you have chosen **VLESS-Reality on 443/TCP**;
- HTTPS services such as Headscale or Home Assistant must also live on the same VPS;
- those services must be reachable under their own domains;
- you are willing to configure Xray fallback and Nginx `stream`.

Do not use this document for **NaiveProxy on 443**: Caddy holds port 443 in that case, not Xray. The Xray SNI fallback approach does not apply to NaiveProxy.

---

## 2. Important Note on 3x-ui and Legacy Host Xray

In the current recommended setup, **3x-ui is the source of truth** for VLESS-Reality. TelegramHelper manages bot-managed clients via `/xui_setup` but does not rewrite the Xray fallback inside the panel.

Therefore:

- if the VLESS inbound is managed by **3x-ui**, configure the fallback/SNI inside the 3x-ui panel or its Xray config;
- the commands `/nginx_status`, `/nginx_enable`, `/nginx_set_domain`, `/nginx_config` work with the bot's legacy `vless_config.json` and are useful for the **bot host Xray without 3x-ui** scenario;
- do not mix two Xray sources on 443: the 3x-ui Xray and the bot's `xray.service` must be separate scenarios, not simultaneous owners of the same port.

For details on scenarios A/B/C, see `VLESS_GUIDE.md`.

---

## 3. Traffic Flow

```text
Internet client
  |
  | TCP/443
  v
Xray / 3x-ui inbound (VLESS-Reality)
  |
  | VLESS-Reality client -> proxy/VPN
  |
  | plain HTTPS, not VLESS -> fallback 127.0.0.1:8443
  v
Nginx stream + ssl_preread
  |
  | SNI=headscale.example.com -> 127.0.0.1:8080
  | SNI=ha.example.com        -> 127.0.0.1:8123
  | default                   -> 127.0.0.1:8000
```

Terms:

| Term | Meaning |
| --- | --- |
| **SNI** | The domain the TLS client sends at the start of the connection |
| **ssl_preread** | Nginx stream mode that reads SNI without decrypting TLS |
| **fallback** | Where Xray sends ordinary HTTPS if it is not a VLESS client |
| **PROXY protocol** | A way to pass the real client IP to the backend |

---

## 4. DNS

All domains must point to the VPS public IP:

| Type | Name | Value |
| --- | --- | --- |
| A | `headscale.example.com` | VPS IP |
| A | `ha.example.com` | VPS IP |
| A | `api.example.com` | VPS IP |

Verify:

```bash
dig +short headscale.example.com
dig +short ha.example.com
dig +short api.example.com
```

Cloudflare notes:

- for VLESS-Reality, the masking domain (`sni`) should generally not be your Cloudflare-proxied domain;
- for plain HTTPS services through Cloudflare, use `Proxied` only if you understand that Cloudflare will connect to your VPS as an HTTPS client;
- VLESS-Reality does not work as a normal site proxy through Cloudflare.

---

## 5. Installing Nginx stream

On Debian/Ubuntu:

```bash
apt-get update
apt-get install -y nginx libnginx-mod-stream
nginx -t
systemctl enable --now nginx
```

Verify:

```bash
nginx -V 2>&1 | grep -o 'stream'
systemctl status nginx
```

---

## 6. Nginx stream Config

File:

```text
/etc/nginx/conf.d/stream_sni.conf
```

Example:

```nginx
stream {
    map $ssl_preread_server_name $backend {
        headscale.example.com  headscale_backend;
        ha.example.com         ha_backend;
        default                api_backend;
    }

    upstream headscale_backend { server 127.0.0.1:8080; }
    upstream ha_backend        { server 127.0.0.1:8123; }
    upstream api_backend       { server 127.0.0.1:8000; }

    server {
        listen 8443;
        listen [::]:8443;
        proxy_pass $backend;
        ssl_preread on;
        proxy_protocol on;
    }
}
```

Apply:

```bash
nginx -t
systemctl reload nginx
ss -ltnp | grep ':8443'
```

If the backend does not support PROXY protocol, remove `proxy_protocol on;` or configure the backend to accept it. PROXY protocol errors often look like "broken TLS" or an instant reset.

---

## 7. Xray Fallback

The Xray inbound on 443 must forward plain HTTPS to Nginx:

```json
{
  "inbounds": [
    {
      "port": 443,
      "protocol": "vless",
      "settings": {
        "fallbacks": [
          { "dest": "127.0.0.1:8443", "xver": 1 }
        ]
      }
    }
  ]
}
```

`xver: 1` means PROXY protocol. It must match the `proxy_protocol on;` setting in Nginx.

### If You Are Using 3x-ui

Configure the fallback in the 3x-ui panel's inbound settings. TelegramHelper `/nginx_*` commands do not modify the 3x-ui database.

Minimal verification after changing the panel:

```bash
systemctl status x-ui
journalctl -u x-ui -n 80 --no-pager
ss -ltnp | grep ':443'
```

### If You Are Using the Bot's Legacy Host Xray

You can use the Telegram commands:

```text
/nginx_set_domain headscale.example.com ha.example.com
/nginx_enable
/nginx_config
/nginx_status
```

What they do:

- save fields to `vless_config.json`;
- generate a sample Nginx stream config;
- enable the fallback flag in the bot's Xray config.

They are not the primary path in the 3x-ui Scenario C.

---

## 8. Reverse Proxy for the API Without SNI Routing

If you only need HTTPS for the bot API and VLESS/NaiveProxy does not occupy the same domain, a simple reverse proxy will work:

```text
Client -> Nginx HTTPS :443 -> 127.0.0.1:8000
```

Script in the repository:

```bash
sudo bash /opt/TelegramHelper/scripts/nginx/setup_reverse_proxy.sh \
  --domain api.example.com \
  --email you@example.com
```

Files:

| Path | Purpose |
| --- | --- |
| `/etc/nginx/sites-available/telegramhelper.conf` | Site config |
| `/etc/letsencrypt/live/<domain>/` | Let's Encrypt certificates |

If port 443 is already taken by Xray, a plain HTTPS reverse proxy on the same 443 cannot start directly. Use the fallback/SNI scheme above or a separate port/domain.

---

## 9. Firewall

Minimum rules:

```bash
ufw allow 22/tcp
ufw allow 443/tcp
ufw reload
ufw status verbose
```

If you use Cloudflare only for a plain HTTPS service, you can restrict 443 access to Cloudflare IP ranges. Do not apply this blindly to VLESS-Reality clients — they connect directly to the VPS IP/domain, and a Cloudflare-only allowlist will block them.

Cloudflare firewall scripts, if genuinely needed:

```bash
scripts/firewall/fetch_cloudflare_ips.sh
scripts/firewall/ufw_allow_cloudflare_443.sh
```

---

## 10. TLS Certificates for Backend Services

Options:

### Certbot standalone

```bash
certbot certonly --standalone -d headscale.example.com
certbot certonly --standalone -d ha.example.com
```

`--standalone` requires port 80 to be free.

### Certbot webroot

Use this if Nginx already serves the HTTP challenge.

### Headscale built-in TLS

In `headscale/config/config.yaml`:

```yaml
tls_cert_path: ""
tls_key_path: ""
tls_letsencrypt_hostname: "headscale.example.com"
tls_letsencrypt_listen: ":http"
```

---

## 11. Verification

Who is listening on 443:

```bash
ss -ltnp | grep ':443'
```

Nginx stream on 8443:

```bash
ss -ltnp | grep ':8443'
nginx -t
systemctl status nginx
```

Test SNI routing from outside:

```bash
openssl s_client -connect YOUR_VPS_IP:443 -servername headscale.example.com </dev/null
openssl s_client -connect YOUR_VPS_IP:443 -servername ha.example.com </dev/null
```

Logs:

```bash
journalctl -u x-ui -n 80 --no-pager      # 3x-ui scenario
journalctl -u xray -n 80 --no-pager      # legacy host Xray
tail -n 80 /var/log/nginx/error.log
```

---

## 12. Common Errors

| Symptom | Cause / Solution |
| --- | --- |
| `ssl_preread` unknown directive | `libnginx-mod-stream` not installed |
| 443 taken by Nginx, VLESS does not start | In the VLESS scenario, 443 must be held by Xray; Nginx stream listens on 8443 |
| Headscale/HA not accessible | Check DNS, SNI, backend port, and `nginx -t` |
| Backend complains about TLS/HTTP | `proxy_protocol on` may be enabled while the backend does not expect it |
| `/nginx_enable` has no effect on 3x-ui | Expected: the command writes the legacy `vless_config.json`, not the 3x-ui database |
| VLESS stopped connecting after Cloudflare UFW allowlist | You blocked direct client access; do not apply a Cloudflare-only allowlist to VLESS |

---

## 13. Related Documents

- `VLESS_GUIDE.md` — 3x-ui / legacy host Xray / bot-managed flow scenarios.
- `HEADSCALE_GUIDE.md` — Headscale and mesh-only 3x-ui.
- `POST_DEPLOY.md` — Docker rebuild/recreate after pull.
- the `scripts/` directory — commands and scripts.
