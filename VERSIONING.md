# Versioning and Metadata

This document describes the versioning process for `TelegramHelper`. The
project follows [Semantic Versioning](https://semver.org/) and uses automated
scripts to manage version numbers.

---

## Quick Start

**macOS / Linux:**
```bash
python3 scripts/bump_version.py              # Check current versions
python3 scripts/bump_version.py --sync       # Synchronise all files
python3 scripts/bump_version.py --bump patch # Bump 1.2.3 → 1.2.4
```

**Windows (PowerShell):**
```powershell
python scripts/bump_version.py              # Check current versions
python scripts/bump_version.py --sync       # Synchronise all files
python scripts/bump_version.py --bump patch # Bump 1.2.3 → 1.2.4
python scripts/bump_version.py --dockhand --bump patch # Bump DOCKHAND_UI_VERSION
```

**Example output:**
```
==================================================
📦 TelegramHelper — Version Summary
==================================================

🏷️  pyproject.toml (source of truth)
   Version:      3.4.5
   Release date: 24.12.2025

📁 Other files:
   ✅ OK api.py (3.4.5) — FastAPI version

--------------------------------------------------
✅ All version references are in sync!
```

---

## 1. Source of Truth

All version and metadata information lives in `pyproject.toml`. This is the
only file that needs to be changed (or that the script changes).

Key fields in `pyproject.toml`:

- `[project].version` — current version (e.g. `1.2.3`)
- `[tool.telegramhelper.metadata].release_date` — release date (`DD.MM.YYYY`)
- `[tool.telegramhelper.metadata].last_updated` — date of last change
  (`YYYY-MM-DD`)
- `[tool.telegramhelper.metadata].developer` — developer name or team

---

## 2. Version Scheme

The project uses **MAJOR.MINOR.PATCH** (e.g. `1.5.2`):

- **MAJOR**: Breaking changes that are not backward compatible.
- **MINOR**: New features that are backward compatible.
- **PATCH**: Bug fixes without new functionality.

---

## 3. The `bump_version.py` Tool

`scripts/bump_version.py` manages versions automatically, updating the version
and dates in `pyproject.toml`.

To bump only the Dockhand UI version:

```bash
python3 scripts/bump_version.py --dockhand --bump patch
```

In this mode only `DOCKHAND_UI_VERSION` in `dockhand/app.py` is changed.

### Main Commands

#### Bug Fix (Patch)

```bash
# macOS / Linux
python3 scripts/bump_version.py --bump patch
# Result: 1.2.3 -> 1.2.4 (release_date updated to today)
```

```powershell
# Windows
python scripts/bump_version.py --bump patch
```

#### New Feature (Minor)

```bash
# macOS / Linux
python3 scripts/bump_version.py --bump minor
# Result: 1.2.3 -> 1.3.0
```

#### Breaking Change (Major)

```bash
# macOS / Linux
python3 scripts/bump_version.py --bump major
# Result: 1.2.3 -> 2.0.0
```

---

## 4. Advanced Usage

### Set a Specific Version

```bash
python3 scripts/bump_version.py --version 2.0.0
```

### Date Management

By default the script updates both `release_date` and `last_updated` to today.

**Bump version without updating release_date:**

```bash
python3 scripts/bump_version.py --bump patch --no-release-date
```

**Set a specific release date:**

```bash
python3 scripts/bump_version.py --version 1.5.0 --release-date 31.12.2025
```

### Change Developer Name

```bash
python3 scripts/bump_version.py --developer "Your Name"
```

---

## 5. Release Checklist

1. Ensure all tests pass.
2. Run the version bump script:
   ```bash
   python3 scripts/bump_version.py --bump minor
   ```
3. Review the changes in `pyproject.toml`.
4. Commit the change:
   ```bash
   git add pyproject.toml
   git commit -m "Bump version to 1.3.0"
   ```
5. Create a tag (optional):
   ```bash
   git tag v1.3.0
   ```

---

## 6. Deploying to the Server

After bumping the version, sync the code to the server and restart the bot.

### macOS / Linux — rsync (recommended)

```bash
rsync -av --delete \
  --exclude 'venv/' --exclude '__pycache__/' \
  --exclude '.git/' --exclude 'bot.log' \
  --exclude '.env' --exclude 'app_keys.json' \
  --exclude 'users.json' --exclude 'vless_config.json' \
  -e 'ssh -p YOUR_SSH_PORT' \
  /path/to/TelegramHelper/ \
  root@YOUR_VPS_IP:/opt/TelegramHelper/

ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP \
  "cd /opt/TelegramHelper && docker compose up -d --build telegram-helper"
```

### macOS / Linux — Git

```bash
git add pyproject.toml
git commit -m "Bump version to 1.3.0"
git push

ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP \
  "cd /opt/TelegramHelper && git pull && docker compose up -d --build telegram-helper"
```

### Windows — Git (PowerShell)

```powershell
git add pyproject.toml
git commit -m "Bump version to 1.3.0"
git push

ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP `
  "cd /opt/TelegramHelper && git pull && docker compose up -d --build telegram-helper"
```

---

## 7. Checking the Version on the Server

```bash
python3 scripts/show_version.py
```

Example output:

```
============================================================
📦 TelegramHelper Version Info
============================================================

🏷️  Version: 3.1.1
📁 Path: /opt/TelegramHelper
🐳 Docker: No (host system)

------------------------------------------------------------
📊 Git information:
------------------------------------------------------------
🌿 Branch: main
🔖 Commit: 85be5c7
📅 Date: 2025-01-15 12:30:00
💬 Message: feat: add show_version.py script...
✅ Working directory clean

------------------------------------------------------------
🔐 Allowed apps (ALLOWED_APPS):
------------------------------------------------------------
  • my-client-v1
  • apiai-v3

------------------------------------------------------------
🐍 Python information:
------------------------------------------------------------
  Version: 3.12.x
  Path: /usr/bin/python3
```

For the version displayed in the bot (`/ver`) to match `pyproject.toml` after a
bump, you must both sync the files **and rebuild** the container
(`docker compose up -d --build`), not just restart it.

The server path for **new** installations is `/opt/TelegramHelper`.

---

## 8. Full Deploy + Version Verify in One Command

### macOS / Linux

```bash
rsync -av --delete \
  --exclude 'venv/' --exclude '__pycache__/' \
  --exclude '.git/' --exclude 'bot.log' \
  --exclude '.env' --exclude 'app_keys.json' \
  --exclude 'users.json' --exclude 'vless_config.json' \
  -e 'ssh -p YOUR_SSH_PORT' \
  /path/to/TelegramHelper/ \
  root@YOUR_VPS_IP:/opt/TelegramHelper/ \
  && ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP \
    "cd /opt/TelegramHelper && docker compose up -d --build telegram-helper \
     && sleep 2 && python3 scripts/show_version.py"
```

Shell alias (add to `~/.zshrc` or `~/.bashrc`):

```bash
alias deploy-th='rsync -av --delete \
  --exclude "venv/" --exclude "__pycache__/" --exclude ".git/" \
  --exclude "bot.log" --exclude ".env" --exclude "app_keys.json" \
  --exclude "users.json" --exclude "vless_config.json" \
  -e "ssh -p YOUR_SSH_PORT" \
  /path/to/TelegramHelper/ root@YOUR_VPS_IP:/opt/TelegramHelper/ \
  && ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP \
    "cd /opt/TelegramHelper && docker compose up -d --build telegram-helper \
     && sleep 2 && python3 scripts/show_version.py"'
```

After adding the alias run `source ~/.zshrc`, then use `deploy-th`.

### Only Check Version (Without Sync)

```bash
ssh -p YOUR_SSH_PORT root@YOUR_VPS_IP \
  "cd /opt/TelegramHelper && python3 scripts/show_version.py"
```
