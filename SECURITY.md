# SECURITY.md — Security, Keys, and Secrets in TelegramHelper

This document consolidates security guidance covering what counts as a secret,
how API and encryption keys work, how bot-managed profiles are protected, and
how to respond to a credential leak.

---

## 1. Scope

`TelegramHelper` includes:

- Telegram bot as the control plane;
- FastAPI REST API;
- VPN/proxy profile management;
- 3x-ui integration;
- URI/QR export for clients;
- profile delivery by email via Gmail API or SMTP.

The project handles network credentials, tokens, and keys. Operational mistakes
(`.env` pasted into a chat, `gmail_token.json` deployed with wrong permissions,
a QR code left in a public channel) are treated as security issues on par with
code vulnerabilities.

---

## 2. What Is a Secret

Never commit or publish:

| File / value | Why it is a secret |
| --- | --- |
| `.env` | contains tokens, keys, passwords, and service addresses |
| `BOT_TOKEN` | full control over the Telegram bot |
| `ADMIN_USER_IDS` | list of Telegram IDs with administrator privileges |
| `API_SECRET_KEY` | fallback API key for the HTTP API |
| `ENCRYPTION_KEY` | AES-256-GCM key, also used to encrypt the 3x-ui password |
| `HMAC_SECRET` | request signing key when the security check is enabled |
| `app_keys.json` | per-app API and encryption keys |
| `xui_config.json` | contains the encrypted 3x-ui password (`password_enc_b64`) |
| `gmail_oauth_client.json` | Google OAuth client secret |
| `gmail_token.json` | Gmail API refresh/access token |
| `*_config.json` | may contain transport private keys, UUIDs, and passwords |
| Profile QR codes / URIs | live VPN access credentials |

Recommended file permissions on the VPS:

```bash
chmod 600 .env app_keys.json xui_config.json gmail_oauth_client.json gmail_token.json 2>/dev/null || true
```

---

## 3. API Key and Encryption Key

The project uses two distinct key types:

| Key | Purpose | Where used |
| --- | --- | --- |
| **API Key** | Request authorisation | HTTP header `X-API-KEY` |
| **Encryption Key** | AES-256-GCM body encryption/decryption | Encrypted API flows, some secret storage |

### 3.1. Fallback Keys from `.env`

```env
API_SECRET_KEY=...
ENCRYPTION_KEY=...
```

These are used as defaults when no per-`app_id` keys have been configured.
Convenient for testing, but for production it is better to issue individual keys.

### 3.2. Per-App Keys in `app_keys.json`

Example structure:

```json
{
  "app_keys": {
    "my-client-v1": {
      "api_key": "generated-api-key",
      "encryption_key": "generated-encryption-key"
    }
  }
}
```

Benefits:

- rotate one application's key without affecting others;
- revoke a single client's access;
- easier incident investigation.

### 3.3. Bot Commands

| Action | API Key | Encryption Key |
| --- | --- | --- |
| Show | `/api` | `/encryption_key` |
| Generate | `/gen_api_key` | `/gen_encryption_key` |
| Delete | `/del_api_key` | `/del_encryption_key` |

Additional key commands:

| Command | Purpose |
| --- | --- |
| `/gen_chacha_key` | Generate a ChaCha20 key |
| `/gen_pqc_key` | Generate a post-quantum key |

The API key and encryption key must match on both the server and the client
application. When creating a new `app_id` you normally need both:

```text
/gen_api_key
/gen_encryption_key
```

---

## 4. 3x-ui Password and `ENCRYPTION_KEY`

When an admin runs `/xui_setup`, the bot stores the 3x-ui panel password
not in plaintext but in `xui_config.json` as `password_enc_b64`.

The model:

- the admin is authenticated by Telegram ID (`ADMIN_USER_IDS`);
- the 3x-ui password is encrypted via `ENCRYPTION_KEY`;
- when the bot needs to call the panel it decrypts the password in process memory only;
- re-entering the password is not needed as long as `xui_config.json` and
  `ENCRYPTION_KEY` remain consistent.

If you rotate `ENCRYPTION_KEY`, the existing `password_enc_b64` can no longer
be decrypted. Fix:

```text
/xui_clear YES
/xui_setup
```

Relevant code: `xui_manager.py` (`_encrypt_password`, `_decrypt_password`,
`make_client_for_config`, `save_credentials`).

---

## 5. QR Codes, URIs, and Bot-Managed Profiles

QR codes and URIs (`vless://…`, `hy2://…`, `https://t.me/proxy?…`) are live
access credentials. Do not leave them in chats indefinitely.

The recommended onboarding flow:

```text
/provision <uid>
/profiles <uid>
/my_profile
/email_profile <uid>
/clean_user <uid> YES
```

Details: `QR_CLIENT_ONBOARDING.md`.

### 5.1. Self-Service `/my_profile`

A special user can retrieve their own profiles:

```text
/my_profile
```

Protections:

- messages containing URIs/QR codes are auto-deleted after a TTL;
- a view limit is enforced;
- when the limit is exhausted the bot deletes the user's bot-managed clients in
  3x-ui and notifies admins;
- the counter resets on a new `/provision <uid>`.

Storage: `users.json` (`my_profile_messages`, `my_profile_views`).

Code: `handlers.py` (`my_profile_command`, `_auto_cleanup_my_profile`,
`_track_and_schedule_delete`, `_notify_admins`).

### 5.2. Admin Flow: `/provision` and `/profiles`

Admin commands also display live URIs/QR codes. Their messages are therefore
also deleted after a TTL. Unlike `/my_profile`, the admin flow has no view
counter because an admin can re-issue profiles intentionally.

`/provision_all` shows only a summary and no URIs/QR codes, so it does not by
itself constitute a client credential leak.

---

## 6. Gmail API and SMTP

`/email_profile` supports two delivery modes:

1. **Gmail API** via `GMAIL_OAUTH_CREDENTIALS` and `gmail_token.json`;
2. **SMTP** via `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`.

Gmail API takes priority when the OAuth client JSON is configured.

Gmail API secrets:

- `gmail_oauth_client.json`;
- `gmail_token.json`.

`gmail_token.json` contains a refresh token. If this file leaks, revoke access
in Google Account / Google Cloud and generate a new token.

Details: `POST_DEPLOY.md`.

---

## 7. Responding to a Leak

| What leaked | What to do |
| --- | --- |
| `BOT_TOKEN` | Re-issue via BotFather, update `.env`, recreate the container |
| `.env` in full | Treat all keys inside as compromised; rotate everything |
| `API_SECRET_KEY` | Replace in `.env`, update clients, recreate the container |
| `ENCRYPTION_KEY` | Replace, then re-run `/xui_setup`; old ciphertext is no longer valid |
| `app_keys.json` | Re-issue individual keys for affected `app_id` values |
| `xui_config.json` | `/xui_clear YES`, change the panel password if needed, `/xui_setup` |
| `gmail_token.json` | Revoke OAuth access in Google, delete the token file, obtain a new one |
| User QR/URI | `/clean_user <uid> YES`, then `/provision <uid>` to rotate |

After any rotation:

```bash
cd /opt/TelegramHelper
docker compose up -d --force-recreate telegram-helper
docker compose logs --tail=80 telegram-helper
```

If code or dependencies changed, run `docker compose build --no-cache` first.

---

## 8. Secure Defaults

The project enforces the following rules:

- secrets are never printed in full to logs;
- client exports must not contain server admin secrets;
- key disclosure must be opt-in, not default;
- `.env`, JSON secrets, and tokens must not enter git;
- QR codes/URIs are auto-deleted where possible;
- admin access is tied to Telegram ID and requires protecting the Telegram account.

**Telegram admins must enable 2FA in Telegram.**

---

## 9. Reporting Vulnerabilities

Do not open a public GitHub issue if the problem involves:

- token, key, or password leakage;
- bypassing admin/special access controls;
- remote code execution;
- disclosure of QR codes/URIs, exports, or logs;
- insecure deployment defaults.

Report privately to the maintainer and include:

- a brief description;
- affected files or commands;
- reproduction steps;
- an impact assessment;
- a suggested fix, if available.

Security fixes are maintained for the current `main` branch. Older forks and
private customisations may require separate manual migration.

---

## 10. For Contributors

- Do not commit secrets.
- Do not add features that expose a full secret value by default.
- Mask sensitive values in logs and error messages.
- Before changing a security flow, verify Telegram admin/special guards.
- Update documentation alongside any behaviour change.
