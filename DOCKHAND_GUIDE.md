# DOCKHAND_GUIDE.md — Installing and Using Dockhand

Dockhand is a lightweight Streamlit diagnostic panel for `TelegramHelper`. It runs alongside `telegram-helper-lite` in the shared `compose.yaml` and lets you quickly check container status, logs, `/health`, CPU/RAM metrics, and restart the bot when needed.

---

## 1. What Gets Started

The current `compose.yaml` includes two Dockhand-related services:

| Service | Container | Purpose |
| --- | --- | --- |
| `dockhand` | `dockhand` | Streamlit UI on `127.0.0.1:8501` |
| `docker-socket-proxy` | `docker-socket-proxy` | Filtered proxy to the Docker Engine API |

Dockhand does not mount `/var/run/docker.sock` directly. It communicates with Docker via:

```env
DOCKER_HOST=tcp://docker-socket-proxy:2375
```

`docker-socket-proxy` allows a minimal set of operations:

```env
CONTAINERS=1
POST=1
```

This is sufficient for list/inspect/logs/stats/restart of containers.

---

## 2. Security

- The UI port is published only as `127.0.0.1:8501:8501`.
- Dockhand is never exposed to the internet.
- Access is made through an SSH tunnel.
- Set `DOCKHAND_AUTH_PASSWORD` as an additional password on top of SSH.
- For read-only mode, set `DOCKHAND_READONLY=1`.

Even with `docker-socket-proxy`, the panel can read logs and restart containers, so public access must never be granted.

---

## 3. Requirements

For the full stack (`telegram-helper` + `dockhand`):

| Configuration | vCPU | RAM | Disk |
| --- | --- | --- | --- |
| Minimum | 1 | 1 GB + 1 GB swap | 10 GB |
| Comfortable | 1–2 | 2 GB | 20 GB |
| Ample | 2 | 4 GB | 40 GB |

Dockhand itself is lightweight: typically 80–150 MB RAM idle. The heaviest operation on a VPS is `docker compose build --no-cache`.

---

## 4. Quick Start

On the VPS:

```bash
cd /opt/TelegramHelper
docker compose build --no-cache dockhand
docker compose up -d --force-recreate docker-socket-proxy dockhand
docker compose ps
docker compose logs --tail=50 dockhand docker-socket-proxy
```

---

## 5. First Deploy from Scratch

If the VPS is clean, do not start Dockhand separately from the rest of the stack. Follow the general scenario:

```bash
cd /opt/TelegramHelper
git clone https://github.com/your-org/TelegramHelper.git .
cp -n example.env .env
chmod 600 .env
nano .env
docker compose build --no-cache telegram-helper dockhand
docker compose up -d --force-recreate docker-socket-proxy telegram-helper dockhand
```

See `DEPLOY.md` for the full deployment guide.

---

## 6. Opening the UI

Run this command on your **local computer**, not on the VPS:

```bash
ssh -L 8501:127.0.0.1:8501 -p YOUR_SSH_PORT root@YOUR_VPS_IP
```

Then open:

```text
http://localhost:8501
```

Why `127.0.0.1` rather than `localhost`: on some systems `localhost` resolves to IPv6 `::1`, while Streamlit listens on IPv4 loopback.

### Background Tunnel

```bash
ssh -f -N -L 8501:127.0.0.1:8501 -p YOUR_SSH_PORT root@YOUR_VPS_IP
pkill -f "ssh.*127.0.0.1:8501"
```

The `/dockhand` admin bot command can supply the host and port from `.env`. Key variables: `DOCKHAND_SSH_HOST`, `TELEGRAMHELPER_PUBLIC_HOST`, `DOCKHAND_SSH_PORT`, `DOCKHAND_SSH_USER`.

---

## 7. `.env` Variables

| Variable | Default | Effect |
| --- | --- | --- |
| `DOCKHAND_TARGETS` | `telegram-helper-lite` | CSV of containers in sidebar |
| `DOCKHAND_API_URL` | `http://telegram-helper:8000` | URL for `/health` check |
| `DOCKHAND_REFRESH_RATE` | `5` | Auto-refresh interval (seconds) |
| `DOCKHAND_READONLY` | `0` | `1` hides the restart button |
| `DOCKHAND_AUTH_PASSWORD` | empty | Password to enter the UI |
| `DOCKHAND_API_KEY` | empty | API key for backup/admin actions |
| `DOCKHAND_APP_ID` | `apiai-v3` | App ID for API calls |
| `DOCKHAND_SSH_HOST` | empty | Host for SSH tunnel hint |
| `DOCKHAND_SSH_PORT` | empty | SSH port for tunnel hint |
| `DOCKHAND_SSH_USER` | `root` | SSH user for tunnel hint |

Example:

```env
DOCKHAND_TARGETS=telegram-helper-lite
DOCKHAND_REFRESH_RATE=10
DOCKHAND_READONLY=1
DOCKHAND_AUTH_PASSWORD=strong-password
DOCKHAND_SSH_HOST=YOUR_VPS_IP
DOCKHAND_SSH_PORT=YOUR_SSH_PORT
DOCKHAND_SSH_USER=root
```

After changing `.env`:

```bash
docker compose up -d --force-recreate dockhand
```

---

## 8. What the UI Contains

Sidebar:

- Container selector (from `DOCKHAND_TARGETS`);
- VPS address and session open time;
- read-only / auth indicators.

System Status:

- Container status;
- Created time;
- CPU/RAM/network metrics;
- Restart button (unless `DOCKHAND_READONLY=1`);
- API health.

Live Logs:

- Tail 10–500 lines;
- Filter by substring;
- Download raw logs;
- Auto-refresh according to `DOCKHAND_REFRESH_RATE`.

---

## 9. Verification

On the VPS:

```bash
cd /opt/TelegramHelper
docker compose ps dockhand docker-socket-proxy telegram-helper
docker inspect dockhand --format '{{.State.Health.Status}}'
docker inspect docker-socket-proxy --format '{{.State.Status}}'
docker inspect telegram-helper-lite --format '{{.State.Health.Status}}'
ss -tlnp | grep :8501
```

Verify the proxy:

```bash
docker compose exec docker-socket-proxy wget -qO- http://localhost:2375/_ping
```

Verify from inside Dockhand:

```bash
docker compose exec dockhand python -c \
  "import os,urllib.request as u; print(u.urlopen(os.environ['DOCKER_HOST'].replace('tcp://','http://')+'/_ping',timeout=2).read())"
```

Expected response: `b'OK'`.

---

## 10. Updating

After changes in `dockhand/`:

```bash
cd /opt/TelegramHelper
docker compose build --no-cache dockhand
docker compose up -d --force-recreate docker-socket-proxy dockhand
docker compose logs --tail=50 dockhand docker-socket-proxy
```

Avoid using:

```bash
docker compose up -d --build
```

Without specifying a service this command may also rebuild `telegram-helper`.

---

## 11. Removal / Rollback

Remove only Dockhand and the proxy without touching the bot:

```bash
cd /opt/TelegramHelper
docker compose stop dockhand docker-socket-proxy
docker compose rm -f dockhand docker-socket-proxy
docker image ls | grep -E 'dockhand|docker-socket-proxy'
```

Full cleanup of the entire stack:

```bash
bash scripts/cleanup_server.sh
```

---

## 12. Troubleshooting

| Problem | What to Check |
| --- | --- |
| `Container Not Found` | `DOCKHAND_TARGETS` and `container_name` |
| `API Unreachable` | `docker compose logs telegram-helper`, `curl http://127.0.0.1:8000/health` |
| Docker daemon error | `docker compose ps docker-socket-proxy` |
| Restart returns `403` | `POST=1` in `docker-socket-proxy` environment |
| Browser `Connection refused` | SSH tunnel; `ss -tlnp` checking `:8501`; `docker compose ps dockhand` |
| Auth password rejected | `.env` and `docker compose up -d --force-recreate dockhand` |
| Dockhand `Created` but not `Up` | Wait for `telegram-helper` to be healthy, then recreate Dockhand |

If `telegram-helper` runs in `network_mode: host` via `compose.host.yaml`, Dockhand reaches it via `telegram-helper:host-gateway`.

---

## 13. Checklist

- [ ] Docker Engine and `docker compose` v2 installed.
- [ ] Repository in `/opt/TelegramHelper`.
- [ ] `docker-socket-proxy` is running.
- [ ] `dockhand` is running and healthy.
- [ ] `telegram-helper-lite` is healthy.
- [ ] `ss -tlnp | grep :8501` shows `127.0.0.1`, not `0.0.0.0`.
- [ ] SSH tunnel opens `http://localhost:8501`.
- [ ] Status, logs, and metrics are visible in the UI.
- [ ] `DOCKHAND_AUTH_PASSWORD` is set if a password is required.
- [ ] `DOCKHAND_READONLY=1` if the restart button is not needed.

---

## 14. Related Documents

- `DOCKER.md` — compose, Dockerfile, volumes and cleanup.
- `DEPLOY.md` — full stack installation.
- `POST_DEPLOY.md` — post-deploy commands.
- `DOCKHAND_GUIDE.md` — Dockhand internals and design decisions.
- `DOCKHAND_GUIDE.md` — detailed installation scenarios.
