# DOCKER.md — Docker Compose Reference

This document covers the Docker Compose setup for `TelegramHelper`: services,
volumes, overrides, diagnostics, and common workflows.

---

## 1. Services Overview

The main stack (`compose.yaml`) includes:

- `telegram-helper` — Telegram bot + FastAPI on `:8000`;
- `docker-socket-proxy` — restricted read-only Docker Engine API proxy;
- `dockhand` — Streamlit diagnostics panel on `127.0.0.1:8501`.

Launch:

```bash
docker compose build --no-cache telegram-helper dockhand
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
```

### `compose.host.yaml`

Override for cases where `telegram-helper` needs to see host mesh interfaces,
for example a Tailscale/Headscale IP of the 3x-ui panel.

```bash
docker compose -f compose.yaml -f compose.host.yaml build --no-cache telegram-helper dockhand
docker compose -f compose.yaml -f compose.host.yaml up -d --force-recreate docker-socket-proxy telegram-helper dockhand
```

What changes:

- `telegram-helper` gets `network_mode: host`;
- `ports:` for it are ignored by Docker Compose — this is expected;
- `dockhand` reaches the API via `telegram-helper:host-gateway`.

### `compose.headscale.yaml`

Overlay for Headscale:

```bash
mkdir -p headscale/config headscale/data
docker compose -f compose.yaml -f compose.headscale.yaml up -d headscale
docker exec headscale headscale users create main_user
```

---

## 2. `telegram-helper`

### Dockerfile

The current Dockerfile:

- base image: `python:3.12-slim`;
- installs `rclone`;
- installs `requirements.txt`;
- copies the project into `/app`;
- creates `appuser`;
- default command: `python main.py`.

Note: the Dockerfile has `USER appuser`, but `compose.yaml` overrides it with
`user: "0:0"`, `pid: host`, `privileged: true`. This is required for host
operations, `nsenter`, and systemd/journalctl scenarios, and for writing to
host-mounted paths. It increases the blast radius — do not expose the API or
Dockhand beyond what is necessary.

### Volumes

| Host | Container | Purpose |
| --- | --- | --- |
| `.env` | `/app/.env` | Tokens and runtime env |
| `bot.log` | `/app/bot.log` | Bot log file |
| `users.json` | `/app/users.json` | Users |
| `app_keys.json` | `/app/app_keys.json` | API/encryption keys |
| `vless_config.json` | `/app/vless_config.json` | VLESS config |
| `naiveproxy_config.json` | `/app/naiveproxy_config.json` | NaiveProxy |
| `hysteria2_config.json` | `/app/hysteria2_config.json` | Hysteria2 |
| `tuic_config.json` | `/app/tuic_config.json` | TUIC |
| `anytls_config.json` | `/app/anytls_config.json` | AnyTLS |
| `xhttp_config.json` | `/app/xhttp_config.json` | XHTTP |
| `mtproto_config.json` | `/app/mtproto_config.json` | MTProto |
| `headscale_config.json` | `/app/headscale_config.json` | Headscale |
| `xui_config.json` | `/app/xui_config.json` | 3x-ui URL/login/encrypted password |
| `rclone/` | `/rclone:ro` | Offsite backup config |
| `/usr/local/etc/xray` | `/usr/local/etc/xray` | Host Xray config |
| `/etc/caddy-naive` | `/etc/caddy-naive` | NaiveProxy host config |
| `/etc/hysteria` | `/etc/hysteria` | Hysteria host config/certs |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Legacy/admin Docker access |

`xui_config.json` contains the encrypted 3x-ui password. The encryption key is
`ENCRYPTION_KEY` from `.env`.

### Healthcheck

`telegram-helper` is checked via `/health`:

```bash
docker inspect --format='{{json .State.Health}}' telegram-helper-lite
curl -s http://127.0.0.1:8000/health
```

---

## 3. `docker-socket-proxy`

Dockhand no longer mounts the Docker socket directly. It communicates through
`tecnativa/docker-socket-proxy`.

Minimum required permissions:

```text
CONTAINERS=1
POST=1
```

The service lives in the internal `socket_proxy` network and is not published
externally.

Verify:

```bash
docker compose ps docker-socket-proxy
docker compose logs --tail=50 docker-socket-proxy
```

---

## 4. `dockhand`

Dockhand is a local Streamlit panel. The port is only published on loopback:

```text
127.0.0.1:8501 -> 8501
```

Open from your local machine:

```bash
ssh -L 8501:localhost:8501 -p YOUR_SSH_PORT root@YOUR_VPS_IP
# browser: http://localhost:8501
```

Security profile:

- non-root user inside Dockerfile;
- `read_only: true`;
- `tmpfs` for `/tmp` and Streamlit home;
- `cap_drop: [ALL]`;
- `security_opt: no-new-privileges:true`;
- Docker API access only via `docker-socket-proxy`.

Update Dockhand only:

```bash
docker compose build --no-cache dockhand
docker compose up -d --force-recreate docker-socket-proxy dockhand
docker compose logs --tail=80 dockhand docker-socket-proxy
```

---

## 5. Headscale

Headscale is added via an overlay file:

```bash
docker compose -f compose.yaml -f compose.headscale.yaml up -d headscale
docker exec headscale headscale users create main_user
docker exec headscale headscale nodes list
```

Volumes:

| Host | Container |
| --- | --- |
| `headscale/config` | `/etc/headscale` |
| `headscale/data` | `/var/lib/headscale` |

---

## 6. Runtime Files Before First Launch

Before `docker compose up`, bind-mount targets must be files, not directories:

```bash
cd /opt/TelegramHelper
for f in \
  vless_config.json hysteria2_config.json tuic_config.json anytls_config.json \
  xhttp_config.json mtproto_config.json headscale_config.json naiveproxy_config.json \
  xui_config.json app_keys.json users.json; do
  [ -d "$f" ] && rmdir "$f"
  [ -f "$f" ] || echo '{}' > "$f"
done
[ -d bot.log ] && rmdir bot.log
[ -f bot.log ] || : > bot.log
```

For Gmail API:

```bash
test -f gmail_oauth_client.json && echo OK
test -f gmail_token.json && echo OK
```

If a file does not exist before the first launch Docker may create a directory
with that name, causing an `Is a directory` error.

---

## 7. Common Commands

### Status

```bash
docker compose ps
docker compose logs --tail=80 telegram-helper
docker compose logs --tail=80 dockhand
curl -s http://127.0.0.1:8000/health
```

### After Changing `.env`

```bash
docker compose up -d --force-recreate telegram-helper
docker compose logs --tail=80 telegram-helper
```

### After Changing Code

```bash
docker compose build --no-cache telegram-helper
docker compose up -d --force-recreate telegram-helper
docker compose logs --tail=80 telegram-helper
```

### Full Rebuild of the Main Stack

```bash
docker compose build --no-cache telegram-helper dockhand
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
docker compose ps
```

---

## 8. Container Diagnostics

### Versions Inside `telegram-helper-lite`

```bash
docker exec telegram-helper-lite python --version
docker exec telegram-helper-lite pip show python-telegram-bot
docker exec telegram-helper-lite grep -m1 '^version' /app/pyproject.toml
```

### Inspect

```bash
docker inspect --format='{{.Config.Image}}' telegram-helper-lite
docker inspect --format='{{.Created}}' telegram-helper-lite
docker inspect --format='{{.HostConfig.RestartPolicy.Name}}' telegram-helper-lite
docker inspect --format='{{json .State.Health}}' telegram-helper-lite
```

### Logs

```bash
docker logs --tail=100 telegram-helper-lite
docker logs -f telegram-helper-lite
docker logs telegram-helper-lite 2>&1 | grep -E "ERROR|Exception|Traceback"
docker logs telegram-helper-lite 2>&1 | grep "Gmail API sent profile email"
```

### Shell Inside the Container

```bash
docker exec -it telegram-helper-lite /bin/bash
docker exec -it telegram-helper-lite /bin/sh
```

### API from the Container and Host

```bash
docker exec telegram-helper-lite python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read())
PY

curl -s http://127.0.0.1:8000/health
```

### Images

```bash
docker images | grep -E "telegram-helper|dockhand"
docker history telegram-helper-lite:latest
docker build --no-cache -t telegram-helper-lite:latest .
```

### Temporary Container for Testing Python Dependencies

```bash
docker run --rm -it -v "$(pwd)":/app -w /app python:3.12-slim /bin/bash
python3 -m pip install -r requirements.txt
python3 -c "import telegram; print(telegram.__version__)"
```

---

## 9. Updating After `git pull`

```bash
cd /opt/TelegramHelper
git pull origin main
docker compose build --no-cache telegram-helper dockhand
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
docker compose logs --tail=80 telegram-helper
```

With host overlay:

```bash
git pull origin main
docker compose -f compose.yaml -f compose.host.yaml build --no-cache telegram-helper dockhand
docker compose -f compose.yaml -f compose.host.yaml up -d --force-recreate docker-socket-proxy telegram-helper dockhand
```

---

## 10. `.dockerignore`

`.dockerignore` must keep secrets, local environments, and build artefacts out
of the image:

```text
venv/
.venv/
.env
.env.*
.git/
__pycache__/
*.pyc
*.log
```

If you add new secret files, update both `.dockerignore` and `.gitignore`.

---

## 11. Docker Cleanup

Check first:

```bash
df -h
docker system df
```

Safe cleanup of build cache and images:

```bash
docker builder prune -af
docker image prune -af
```

Be careful with volumes:

```bash
docker volume prune -f
```

Do not delete without a backup:

```text
.env
app_keys.json
users.json
*_config.json
xui_config.json
gmail_oauth_client.json
gmail_token.json
rclone/rclone.conf
```

See also: disk cleanup via `scripts/cleanup_server.sh`.

---

## 12. Troubleshooting

| Problem | Action |
| --- | --- |
| `Is a directory: /app/<file>.json` | Remove the directory on the host, create the file, rebuild `--no-cache` |
| `.env` changed but bot sees old values | `docker compose up -d --force-recreate telegram-helper` |
| Old code in the container | `docker compose build --no-cache telegram-helper` |
| Dockhand not opening | Check SSH tunnel and `docker compose logs dockhand` |
| Dockhand cannot see Docker | Check `docker-socket-proxy` |
| 3x-ui mesh IP unreachable | Use `compose.host.yaml` |
| Disk full | `docker system df`, then prune build cache/images |

Check a bind-mount type:

```bash
docker exec telegram-helper-lite test -f /app/xui_config.json && echo OK
```

---

## 13. Related Documents

- `DEPLOY.md` — initial setup, `.env`, VPS operations.
- `POST_DEPLOY.md` — post-deploy checklist.
- the `scripts/` directory — all scripts.
- `DOCKHAND_GUIDE.md` — Dockhand installation and usage.
- disk cleanup via `scripts/cleanup_server.sh` — disk cleanup guide.
