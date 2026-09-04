# EMAIL.md — send profiles by SMTP

`/email_profile` can send VPN URIs to a user when SMTP is configured.
Gmail OAuth JSON files are optional and must never be committed.

## SMTP (recommended for the public project)

Set these in `.env`:

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASS=your_app_password
SMTP_FROM=you@example.com
SMTP_USE_TLS=starttls
SMTP_BLOCKED_TLDS=
```

`starttls` is the default on port 587. Use `tls` for SMTPS on 465.
Leave `SMTP_HOST` empty to keep email disabled; the bot then replies that
SMTP is not configured.

Use an app password, not the mailbox login password, when the provider
requires 2FA.

## Commands

```text
/setemail 123456789 you@example.com
/email_profile 123456789
```

The bot deletes previous QR / URI messages from the chat when it can
track them. Treat the email as a secret: it contains working access URIs.

## Gmail API (optional)

If you prefer Gmail API instead of SMTP, place OAuth desktop-app JSON
**outside git** on the server, mount it into the container, and set:

```env
GMAIL_OAUTH_CREDENTIALS=/app/gmail_oauth_client.json
GMAIL_TOKEN_PATH=/app/gmail_token.json
GMAIL_FROM=you@example.com
```

Never commit `gmail_oauth_client.json` or `gmail_token.json`.
Prefer SMTP unless you already operate Google Cloud OAuth for this app.
