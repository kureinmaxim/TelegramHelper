# Rclone on VPS — Offsite Backup (TelegramHelper)

Administrator guide for setting up **rclone** to back up project state (bot + API + configs) to external encrypted cloud storage.

---

## 1. Where Rclone Fits

Two layers protect your data:

* **Git repository (Blueprint):** Stores the server *map* — topology, open ports, file locations, task backlog. For security reasons it does **not** store secrets (tokens, UUIDs, passwords) or the user database.
* **Rclone (State & Secrets):** Takes a snapshot of *actual state* — `.env`, `users.json`, `app_keys.json`, `*_config.json`, and logs. The snapshot is packed into a `.tar.gz`, encrypted, and uploaded to cloud storage (S3, iCloud, WebDAV, etc.).

**Ideal Disaster Recovery flow:**
Spin up a new VPS → deploy structure from the repository → download the archive via `rclone` → unpack secrets → server fully restored.

---

## 2. How It Works Technically

The bot does **not mount** cloud storage via FUSE (that would require dangerous `CAP_SYS_ADMIN` privileges and often breaks on reboots). Instead, the bot locally assembles a `.tar.gz` and calls `rclone copyto`.

| Component | Role |
|-----------|------|
| **Bot container** | Contains the `rclone` binary. Collects `/app/*.json` and `.env` into an archive. |
| **Mount `./rclone:/rclone:ro`** | Passes the `rclone.conf` config file from the host into the container read-only. |
| **`.env` variables** | `RCLONE_CONFIG` (path to config) and `RCLONE_REMOTE` (cloud remote name). |
| **Telegram (admin)** | Commands `/backup_status`, `/backup_test`, `/backup_now`, `/backup_list`. |
| **Dockhand UI** | "Offsite Backups" block (works via `/admin_command`). |

---

## 3. Quick Start: Setup on the VPS

### Step 3.1. Install rclone on the host (once)

```bash
curl https://rclone.org/install.sh | sudo bash
rclone version
```

*(Alternative for Debian/Ubuntu: `sudo apt-get update && sudo apt-get install -y rclone`)*

### Step 3.2. Interactive setup (`rclone config`)

Run `rclone config`. You need to create **TWO** remote connections in this order:

1. **Backend (Base cloud):**
   * Press `n` (New remote).
   * Name: e.g. `mys3`, `icloud`, `b2vault`.
   * Type: select the number for your provider (S3, Google Drive, WebDAV, iCloud, etc.).
   * Complete the setup and save.
2. **Crypt (Encryption layer):**
   * Press `n` again.
   * Name: **`encrypted`** (recommended).
   * Type: select `Encrypt/Decrypt a remote` (usually number `16` or type `crypt`).
   * Remote to encrypt: specify the path to your base cloud (e.g. `mys3:telegram-backups`).
   * Password: press `g` (generate) or `y` (enter your own). **Save this password in a password manager! Without it, backups are unrecoverable.**

*Verify on host:*

```bash
rclone listremotes
rclone lsd encrypted:
```

### Step 3.3. Pass the config into Docker

By default `rclone` saves settings in `~/.config/rclone/rclone.conf`. Copy it into the project directory:

```bash
export APP=/opt/TelegramHelper
mkdir -p "$APP/rclone"
install -m 600 ~/.config/rclone/rclone.conf "$APP/rclone/rclone.conf"
```

Rebuild and restart the bot to pick up the new file (see `POST_DEPLOY.md`):

```bash
cd "$APP"
docker compose build --no-cache telegram-helper
docker compose up -d --force-recreate telegram-helper
```

---

## 4. `.env` Configuration

Add the following variables to your `.env` file:

```dotenv
# Name of the encrypted remote (created in step 3.2)
RCLONE_REMOTE=encrypted:

# Prefix for archive files (e.g. telegramhelper_2026-05-28.tar.gz)
RCLONE_BACKUP_PREFIX=telegramhelper

# Path to config inside the container (do not change)
RCLONE_CONFIG=/rclone/rclone.conf

# Limits
RCLONE_BACKUP_TIMEOUT=300
RCLONE_BACKUP_LOG_MAX_MB=20

# For Dockhand: key for calling backup from UI (do NOT commit!)
DOCKHAND_API_KEY=your_secret_key_here
DOCKHAND_APP_ID=apiai-v3
```

*Note:* Until `RCLONE_REMOTE` is set, the `/backup_*` commands will report that backup is not configured.

---

## 5. Management from Telegram (Admin Only)

Available commands directly in the bot chat:

* **`/backup_status`** — Checks whether the bot can find `rclone`, whether the remote is configured, and which files are ready to be backed up.
* **`/backup_test`** — Makes a test call `rclone lsd <remote>` (checks connectivity to cloud).
* **`/backup_now`** — **Creates an archive and uploads it to cloud storage.**
* **`/backup_list`** — Shows the most recently uploaded archives in cloud storage.

**What goes into the archive?**
`.env`, `users.json`, `app_keys.json`, all `*_config.json`, and `bot.log` (if it is under the size limit). A `manifest.json` with metadata is also included inside the archive.

---

## 6. Management via Dockhand

If `DOCKHAND_API_KEY` is set in `.env`, the Dockhand web interface will show an **Offsite Backups** block. From there you can check status, test the connection, and trigger a backup with a single button (the UI calls the internal `/admin_command` endpoint).

---

## 7. Automation (Cron)

If you want scheduled backups without manual Telegram commands, add a cron job on the host. It will invoke the script inside the container:

```bash
# Open crontab
crontab -e

# Add a line (backup every day at 03:15):
15 3 * * * docker exec telegram-helper-lite python3 -c "import rclone_manager as m; print(m.format_backup_result(m.create_backup()))" >> /var/log/telegramhelper-backup.log 2>&1
```

---

## 8. Rclone Command Reference (on host)

| Task | Command |
|------|---------|
| List connected remotes | `rclone listremotes` |
| Browse folders in cloud | `rclone lsd encrypted:` |
| List all files | `rclone ls encrypted:path` |
| Download a file from cloud | `rclone copyto encrypted:path/file.tar.gz /opt/TelegramHelper/restore.tar.gz` |
| Delete a file in cloud | `rclone delete encrypted:path/file.tar.gz` |

---

## 9. Troubleshooting

| Symptom | Solution |
|---------|---------|
| **`RCLONE_REMOTE not configured`** | Check that `.env` contains `RCLONE_REMOTE=encrypted:` and recreate the container (`docker compose up -d --force-recreate`). |
| **`rclone is not installed in the container`** | You forgot to rebuild the bot image. Run `docker compose build --no-cache telegram-helper`. |
| **Empty file list in `/backup_status`** | The bot cannot find JSON files. Ensure they exist on the host and are mounted in `compose.yaml` (see `POST_DEPLOY.md`). |
| **Dockhand: `/admin_command` error** | Verify that `DOCKHAND_API_KEY` in `.env` matches the key in `app_keys.json` for `DOCKHAND_APP_ID`. |

---

## 10. Disaster Recovery

If the VPS is completely lost (disk failure, provider block), you can restore all users and their profiles on a new server using a downloaded backup.

### 10.1. What gets restored?

The archive contains the *state* of the bot:

- `.env` (tokens, encryption keys, passwords)
- `users.json` (special user list and email bindings)
- `app_keys.json` (individual API keys)
- All `*_config.json` (profile settings for Hysteria2, MTProto, Mieru, TUIC, AnyTLS, XHTTP, and legacy VLESS).

### 10.2. Recovery process

1. **Prepare a new VPS:** Deploy the directory structure from the repository.
2. **Install Rclone:** Follow steps 3.1 and 3.2 of this guide to connect your cloud (you will need the `crypt` layer password).
3. **Download the backup:**
   ```bash
   rclone copyto encrypted:telegramhelper_latest.tar.gz /opt/TelegramHelper/restore.tar.gz
   ```
4. **Unpack secrets:**
   ```bash
   cd /opt/TelegramHelper
   tar -xzf restore.tar.gz
   ```
5. **Start the project:**
   ```bash
   docker compose up -d --force-recreate
   ```

### 10.3. Restoring 3x-ui Clients (Two Paths)

The bot backup (via Rclone) does **not include** the 3x-ui panel database (`x-ui.db`), as it is a third-party service. If the server is lost, you have two paths for restoring VLESS clients.

#### Path A: Full restoration (QR codes unchanged)

This path requires that you previously saved the 3x-ui database file.

1. **Where the database lives:** For native installations the file is at `/etc/x-ui/x-ui.db`.
2. **How to back it up:** Periodically download this file via SFTP/SCP, or add it to your cron script.
3. **How to restore on a new VPS:**
   * Install 3x-ui from scratch (see `DEPLOY.md`).
   * Stop the panel service: `systemctl stop x-ui`
   * Replace the fresh empty `/etc/x-ui/x-ui.db` with your saved backup.
   * Start the service: `systemctl start x-ui`

*Result:* All Inbounds, client UUIDs, and Reality keys are preserved. Old VPN profiles continue to work (if connected by domain — just update the DNS record; if by IP — users need to update the IP in their app).

#### Path B: Restore via bot (QR codes will change)

Use this if the `x-ui.db` is lost but you have the bot backup (restored via step 10.2).

1. **Install:** Set up 3x-ui from scratch on the new VPS.
2. **Create an Inbound:** Open the 3x-ui browser panel and manually create a new VLESS-Reality Inbound (port 443, xtls-rprx-vision, generate new keys).
3. **Link the bot:** In Telegram, send the bot `/xui_setup`. Provide the URL of the new panel, credentials, and select the newly created Inbound.
4. **Mass reprovisioning:** In Telegram, run `/provision_all`.
   * The bot reads your user list (special users) from the restored `users.json`.
   * For each user the bot automatically creates a new client in the 3x-ui panel with the correct canonical name.
5. **Distribute new access:** Since the Inbound is new (new Reality keys) and clients are recreated (new UUIDs), **old VPN profiles will no longer work**.
   * Send new credentials via `/email_profile <id>` (if emails are linked), or ask users to open the bot and press `/my_profile` — the bot will provide the new QR codes.

---

## 11. Security Checklist

1. 🔒 **`rclone.conf` lives only on the host.** It must have permissions `600` (`chmod 600 rclone.conf`). **Never commit it to git!**
2. 🛡 **Always use `crypt`.** Do not upload `.env` and secrets to S3/iCloud in plain text. The cloud provider should never see your keys.
3. 🔑 **Save the `crypt` password.** If the VPS is destroyed and you do not have the encryption layer password, you will not be able to unpack the downloaded backup.
4. 🚫 **Do not use `rclone mount` in Docker.** It breaks isolation and requires granting the container `privileged` or `CAP_SYS_ADMIN`. The `copyto` approach used here is much safer.
