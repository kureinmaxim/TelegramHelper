# QR Client Onboarding

A concise reference for delivering VPN profiles to users via TelegramHelper.
The current path is **one unified flow**: bot-managed profiles via `/provision`,
viewing via `/profiles` or `/my_profile`, and email delivery via `/email_profile`.

Legacy commands such as `/vless_add_client`, `/vless_qr`, `/hy2_add_client`,
`/mt_qr` are no longer the primary onboarding path. In the 3x-ui scenario they
are guarded and redirect to the unified bot-managed flow.

---

## 1. Overview

- QR codes and URIs are **access credentials**. Do not share them in public chats.
- The authoritative source of profiles is **bot-managed clients** in the 3x-ui / default inbound.
- A single user flow can deliver profiles for multiple protocols if they are enabled: VLESS, Hysteria2, MTProto, TUIC, AnyTLS, XHTTP.
- The bot uses canonical client names such as `Vless_ID82_09`, `Hys_ID82_09`, etc. to distinguish its own clients from the admin's manual clients in the panel.
- Manual clients with arbitrary names in 3x-ui are not touched by the bot.

---

## 2. Roles

| Role | Permissions |
| --- | --- |
| Admin | Create, view, email, and delete profiles |
| Special user | Retrieve their own profile via `/my_profile` |
| Regular user | Cannot receive a VPN profile until the admin grants access |

Before delivering a profile, the user must be added to the special list:

```text
/special_add <telegram_user_id>
```

---

## 3. Main Scenario: Admin Delivers a Profile

### Step 1. Create or Update a Bot-Managed Profile

```text
/provision <telegram_user_id>
```

Example:

```text
/provision 123456789
```

What the bot does:

- creates a bot-managed client in the 3x-ui default inbound;
- uses a canonical client name;
- returns the URI and QR code;
- removes any stale duplicate bot-managed clients left from previous setups;
- resets the self-service `/my_profile` view counter.

### Step 2. Re-View an Existing Profile

```text
/profiles <telegram_user_id>
```

Example:

```text
/profiles 123456789
```

Read-only command: shows existing bot-managed profiles, URIs, and QR codes without creating new clients.

### Step 3. Send the Profile by Email

```text
/email_profile <telegram_user_id>
```

Example:

```text
/email_profile 123456789
```

The email is taken from the user's bound address. To bind an address:

```text
/setemail <telegram_user_id> <email>
```

Example:

```text
/setemail 123456789 user@example.com
```

You can also send to a one-off address without saving it:

```text
/email_profile 123456789 user@example.com
```

Email delivery is configured via Gmail API or SMTP. See `POST_DEPLOY.md` for details.

---

## 4. Self-Service: User Retrieves Their Own Profile

The special user messages the bot:

```text
/my_profile
```

The bot sends:

- a brief summary;
- a URI for each enabled protocol;
- QR images;
- messages with automatic deletion.

Security features:

- successful delivery messages auto-delete after a TTL;
- there is a limit on successful view attempts;
- when the limit is reached, the bot deletes the user's bot-managed clients and notifies admins.

To re-issue the profile:

```text
/provision <telegram_user_id>
```

---

## 5. What the Client Receives

Depending on the enabled protocols:

| Protocol | What the user receives |
| --- | --- |
| VLESS-Reality | `vless://...` URI and QR code |
| Hysteria2 | `hy2://...` URI and QR code |
| MTProto | `https://t.me/proxy?...` / `tg://proxy?...` and QR code |
| TUIC | URI/config for a compatible client and QR code, if enabled |
| AnyTLS | URI/config for a compatible client and QR code, if enabled |
| XHTTP | URI/config for a compatible client and QR code, if enabled |

The MTProto QR code is built on `https://t.me/proxy?...` so that phones open Telegram more reliably.

---

## 6. Deletion and Rotation

Delete bot-managed clients for a user:

```text
/clean_user <telegram_user_id> YES
```

Example:

```text
/clean_user 123456789 YES
```

Rotate secrets:

```text
/clean_user 123456789 YES
/provision 123456789
```

The canonical name remains the same; UUID/secrets are regenerated.

---

## 7. Disaster Recovery

If the VPS is completely lost (disk failure, provider block), restoring all users depends on having a recent backup.

### 7.1 Where User Profiles Are Stored

All protocol profile settings are stored in JSON files on the host:
- `vless_config.json` (if legacy VLESS is in use)
- `hysteria2_config.json`
- `mtproto_config.json`
- `tuic_config.json`
- `anytls_config.json`
- `xhttp_config.json`
- `mieru_config.json`
- `naiveproxy_config.json`

**Important about 3x-ui:** If you use 3x-ui as the source of truth (the recommended setup), the clients themselves are stored in the panel's internal SQLite database (`/etc/x-ui/x-ui.db`). However, the bot stores the encrypted panel password in `xui_config.json`.

Also critical for restoration:
- `.env` (contains tokens, encryption keys, passwords)
- `users.json` (list of special users and their email bindings)
- `app_keys.json` (individual API keys)

### 7.2 How Restoration Works

To be able to restore all clients on a new server, you **must** set up an offsite backup via Rclone (`/backup_status`, `/backup_now`).

Rclone collects all the files listed above (including `.env` and all `*_config.json`) into a single encrypted `.tar.gz` archive and uploads it to cloud storage (S3, iCloud, WebDAV).

**Recovery process on a new VPS:**
1. Deploy a new VPS using the repository scripts.
2. Install Rclone and connect it to your cloud storage (you will need the `crypt` layer password).
3. Download the latest `.tar.gz` archive.
4. Extract `.env` and `*.json` files to `/opt/TelegramHelper/`.
5. Run `docker compose up -d`.
6. If 3x-ui was in use, restore its `x-ui.db` database (if backed up separately), or recreate the inbound and run `/provision_all` to reissue all profiles.

Without a recent backup of `.env` and JSON files, restoring old profiles is **impossible**. You will need to generate new keys and re-deliver QR codes to all users.

If 3x-ui was in use, restore `x-ui.db` separately when you have a copy; otherwise recreate the inbound and run `/provision_all`.

---

## 8. What Not to Use for Onboarding

Do not use these as the primary path:

```text
/vless_add_client
/vless_qr
/vless_export
/hy2_add_client
/hy2_qr
/hy2_export
/mt_add_client
/mt_qr
/mt_export
```

These commands belong to legacy flows or individual protocol managers. For current client delivery, use:

```text
/provision
/profiles
/my_profile
/email_profile
/clean_user
```

---

## 9. Admin Checklist

1. Add the user to the special list:

   ```text
   /special_add 123456789
   ```

2. Create the profile:

   ```text
   /provision 123456789
   ```

3. Optionally bind an email:

   ```text
   /setemail 123456789 user@example.com
   ```

4. Send the profile by email:

   ```text
   /email_profile 123456789
   ```

5. The user can retrieve their own profile:

   ```text
   /my_profile
   ```

6. View or re-show the profile:

   ```text
   /profiles 123456789
   ```

7. Delete if needed:

   ```text
   /clean_user 123456789 YES
   ```

---

## 10. Further Reading

- `POST_DEPLOY.md` — VPS deployment, Docker, Gmail token, logs.
- `SECURITY.md` — TTL, view limits, secrets, rotation, and the ephemeral-link model.
- `DEPLOY.md` — rclone env (`RCLONE_REMOTE`, `RCLONE_CONFIG`) and `/provision`.
