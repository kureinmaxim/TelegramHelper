"""
email_manager.py — send bot-managed VPN profiles to a user by email.

Configuration comes from environment variables, read once at import.
If neither the SMTP triplet nor `GMAIL_OAUTH_CREDENTIALS` is set,
`is_configured()` returns False — the handler reports that mail is not set up.

The `.ru` / `.su` zone (and any TLD in `SMTP_BLOCKED_TLDS`, comma-separated)
**is rejected at validation**: outbound SMTP to .ru mailboxes from an RF VPS
is unreliable (sanctions / TLS filters), so this channel cannot be used
without surprises. Use an international SMTP provider
(Gmail with an App Password, ProtonMail Bridge, Outlook, Yandex Mail with
an app password, etc.) and a recipient on an international TLD.

SMTP alternative — **Gmail API** (OAuth 2.0, "Desktop app" type):

- In `.env`: `GMAIL_OAUTH_CREDENTIALS=/path/to/client_secret....json`
- Token: `gmail_token.json` by default, or `GMAIL_TOKEN_PATH`

The first run opens a browser. On a server without a GUI, copy `gmail_token.json`
from the machine where authorization completed. Do not commit the client_secret JSON.
"""

import base64
import copy
import io
import json
import logging
import os
import re
import smtplib
from email.generator import BytesGenerator
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import parseaddr
from pathlib import Path
from typing import Dict, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

# --- SMTP config (lazy-cached on first call) ---


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return (v if v is not None else default).strip()


def _smtp_host() -> str:
    return _env("SMTP_HOST")


def _smtp_port() -> int:
    raw = _env("SMTP_PORT", "587")
    try:
        return int(raw)
    except ValueError:
        return 587


def _smtp_user() -> str:
    return _env("SMTP_USER")


def _smtp_pass() -> str:
    return _env("SMTP_PASS")


def _smtp_from() -> str:
    return _env("SMTP_FROM") or _smtp_user()


def _smtp_use_tls() -> str:
    """`tls` (SMTPS on :465), `starttls` (default :587), `none`."""
    val = _env("SMTP_USE_TLS", "starttls").lower()
    return val if val in ("tls", "starttls", "none") else "starttls"


def _blocked_tlds() -> Tuple[str, ...]:
    """TLDs that must not be mailed. Default: `.ru, .su`."""
    raw = _env("SMTP_BLOCKED_TLDS", ".ru,.su")
    items = [
        s.strip().lower()
        for s in raw.split(",")
        if s.strip()
    ]
    # Ensure each entry starts with a dot
    return tuple(s if s.startswith(".") else "." + s for s in items)


def _expanded_path(rel: str) -> str:
    """Path from .env with ~ and environment variables expanded."""
    return os.path.normpath(os.path.expandvars(os.path.expanduser(rel.strip())))


def _gmail_oauth_client_file() -> str:
    """OAuth client JSON (Desktop app) from Google Cloud Console."""
    return _env("GMAIL_OAUTH_CREDENTIALS")


def _gmail_token_file() -> str:
    """File with the saved refresh/access token."""
    return _env("GMAIL_TOKEN_PATH", "gmail_token.json") or "gmail_token.json"


def is_gmail_oauth_configured() -> bool:
    p = _gmail_oauth_client_file()
    return bool(p) and os.path.isfile(_expanded_path(p))


def _smtp_fully_configured() -> bool:
    return bool(_smtp_host() and _smtp_user() and _smtp_pass())


def is_configured() -> bool:
    """True if SMTP (full triplet) or Gmail OAuth JSON is configured."""
    return is_gmail_oauth_configured() or _smtp_fully_configured()


class SMTPConfig(NamedTuple):
    """Snapshot of SMTP settings from the environment (for sending and future providers)."""

    host: str
    port: int
    use_tls: str
    mail_from_raw: str
    user_raw: str
    password_raw: str


def load_smtp_config() -> Optional[SMTPConfig]:
    """Return the config only if host, user, and pass are all set."""
    if not _smtp_fully_configured():
        return None
    return SMTPConfig(
        host=_smtp_host(),
        port=_smtp_port(),
        use_tls=_smtp_use_tls(),
        mail_from_raw=_smtp_from(),
        user_raw=_smtp_user(),
        password_raw=_smtp_pass(),
    )


def validate_gmail_oauth_env() -> None:
    """If an OAuth JSON path is set, the file must exist and dependencies must be installed."""
    raw = (_gmail_oauth_client_file() or "").strip()
    if not raw:
        return
    path = os.path.abspath(_expanded_path(raw))
    if not os.path.isfile(path):
        raise RuntimeError(
            f"GMAIL_OAUTH_CREDENTIALS: file not found ({path}). "
            "Set the path to a Desktop app JSON from Google Cloud Console."
        )
    try:
        from google.oauth2.credentials import Credentials  # noqa: F401

        __import__("google_auth_oauthlib.flow")  # InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "Gmail API is enabled (GMAIL_OAUTH_CREDENTIALS), but dependencies are missing. "
            "Install: pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        ) from exc
    tok = os.path.abspath(_expanded_path(_gmail_token_file()))
    logger.info(
        "Gmail API: client secrets %s; saved token.json → %s",
        path,
        tok,
    )


def validate_smtp_env() -> None:
    """Fail-fast at startup: Gmail OAuth or incomplete SMTP in .env; Gmail hints.

    If neither Gmail JSON nor SMTP is set — stay silent. Partial SMTP raises an error.
    """
    validate_gmail_oauth_env()

    h = (os.getenv("SMTP_HOST") or "").strip()
    u = (os.getenv("SMTP_USER") or "").strip()
    p = (os.getenv("SMTP_PASS") or "").strip()
    port_raw = (os.getenv("SMTP_PORT") or "").strip()

    any_set = bool(h or u or p)
    all_set = bool(h and u and p)

    if any_set and not all_set:
        raise RuntimeError(
            "SMTP: set all of SMTP_HOST, SMTP_USER, and SMTP_PASS "
            "(or leave them all empty — then /email_profile is unavailable)."
        )

    if not all_set:
        return

    if port_raw:
        try:
            int(port_raw)
        except ValueError as exc:
            raise RuntimeError(
                f"SMTP_PORT must be a number, currently: {port_raw!r}"
            ) from exc

    hl = h.lower()
    if "gmail" in hl or "google" in hl:
        cleaned = _strip_smtp_auth_noise(p)
        printable = "".join(c for c in cleaned if 32 <= ord(c) < 127)
        if len(printable) < 16:
            logger.warning(
                "SMTP_PASS is shorter than 16 printable characters — Gmail usually "
                "uses a 16-character App Password. Check .env."
            )
        logger.info(
            "SMTP: a Gmail host is set. Google production limits and policy "
            "can be stricter — for bulk mail look at SendGrid/Mailgun."
        )


# --- Validation ---

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def validate_email(email: str) -> Tuple[bool, str]:
    """`(ok, error_message)`. On False, reports the reason."""
    if not email:
        return False, "empty address"
    addr = email.strip().lower()
    if "@" not in addr:
        return False, "missing @"
    if not _EMAIL_RE.match(addr):
        return False, "does not look like an email"
    blocked = _blocked_tlds()
    for tld in blocked:
        if addr.endswith(tld):
            return False, (
                f"domain {tld} is blocked (see SMTP_BLOCKED_TLDS); "
                f"use an international one — gmail/proton/outlook/yandex"
            )
    return True, ""


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _safe_attachment_segment(name: str) -> str:
    """Attachment filename: no spaces or characters that break RFC / Gmail."""
    raw = (name or "client").strip() or "client"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    return (safe[:80] if safe else "client")


def _mailbox_only(header_value: str) -> str:
    """Email address only: non-ASCII in a display-name (Name <mail>) breaks serialization."""
    raw = (header_value or "").strip()
    if not raw:
        return raw
    _name, addr = parseaddr(raw)
    if addr and "@" in addr:
        return addr.strip()
    return raw


def _flatten_rfc822(msg: EmailMessage) -> bytes:
    """Like smtplib.send_message: shallow copy, no Bcc, CRLF, bytes."""
    msg_copy = copy.copy(msg)
    for hdr in ("Bcc", "Resent-Bcc"):
        if hdr in msg_copy:
            del msg_copy[hdr]
    buf = io.BytesIO()
    BytesGenerator(buf, policy=msg_copy.policy).flatten(
        msg_copy, linesep="\r\n"
    )
    return buf.getvalue()


def _strip_smtp_auth_noise(s: str) -> str:
    """Strip typical copy-paste junk from .env (BOM, zero-width, NBSP)."""
    if not s:
        return s
    return (
        s.replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\u00a0", "")
        .strip()
    )


def _smtp_auth_printable_ascii(value: str, label: str) -> Tuple[str, Optional[str]]:
    """Keep characters in the printable ASCII range (same as a Google App Password).

    Strips invisible copy-paste junk; otherwise smtplib AUTH PLAIN fails on .encode('ascii').
    """
    raw = _strip_smtp_auth_noise(value or "")
    cleaned = "".join(c for c in raw if 32 <= ord(c) < 127)
    if not cleaned:
        if raw:
            return "", (
                f"{label}: no characters left after cleanup — check .env "
                "(plain ASCII is required, no quotes/spaces around the whole password)."
            )
        return "", f"{label}: empty in .env"
    if cleaned != _strip_smtp_auth_noise(value or ""):
        logger.warning("%s: removed non-printable/non-ASCII characters (SMTP AUTH)", label)
    return cleaned, None


def _smtp_auth_failure_message(
    exc: smtplib.SMTPAuthenticationError, host: str
) -> str:
    """Hint for Telegram: the operator can fix .env without reading logs."""
    code = getattr(exc, "smtp_code", None)
    detail = str(exc)
    hl = (host or "").lower()
    if code == 535 and ("gmail" in hl or "google" in hl):
        return (
            "❌ Gmail rejected the login/password (SMTP 535).\n\n"
            "Check:\n"
            "1) You are using an App Password (not the regular mailbox password).\n"
            "   Google → Security → 2FA → App passwords → Mail.\n\n"
            "2) SMTP_USER matches the same Gmail the App Password was created for.\n\n"
            "3) The password was pasted without spaces or line breaks.\n\n"
            "4) The App Password has not expired (if in doubt — create a new one).\n\n"
            "5) For Google Workspace, SMTP may be disabled by an admin.\n\n"
            f"Server reply: {detail}"
        )
    return f"SMTP auth error ({code}): {detail}"


def _compose_profile_email_message(
    from_addr: str,
    to_email: str,
    user_id: int,
    profiles: Dict[str, Dict],
    qr_images: Optional[Dict[str, bytes]] = None,
) -> Tuple[Optional[EmailMessage], str]:
    """Build the same MIME that is sent over SMTP or via the Gmail API."""
    from_ascii = (_mailbox_only(from_addr) or from_addr.strip()).strip()
    if not from_ascii or "@" not in from_ascii:
        return None, (
            "Set the sender address as a plain email (ASCII), "
            "without a localized name in angle brackets."
        )
    qr_images = qr_images or {}

    msg = EmailMessage(policy=SMTP_POLICY)
    profile_count = len(profiles)
    msg["Subject"] = f"VPN access profiles for Telegram ID {user_id}"
    msg["From"] = from_ascii
    msg["To"] = normalize_email(to_email)

    profile_word = "profile" if profile_count == 1 else "profiles"
    body_lines = [
        "Hello!",
        "",
        f"{profile_count} VPN {profile_word} prepared for Telegram-ID {user_id}.",
        "",
        "What is in this email:",
        "- URI/links below for manual import;",
        "- QR codes attached as separate PNG files;",
        "- each QR matches the profile of the same name.",
        "",
        "IMPORTANT: these links and QRs grant VPN access. Do not forward this email "
        "to strangers and do not post QRs in public chats.",
        "",
        "Profiles:",
    ]
    for proto, p in profiles.items():
        name = p.get("client_name", "")
        uri = p.get("uri", "")
        title = f"{proto}"
        if name:
            title += f" / {name}"
        body_lines.append("")
        body_lines.append(f"--- {title} ---")
        body_lines.append(uri or "(URI was not generated)")
        body_lines.append("")
    body_lines.extend(
        [
            "How to connect:",
            "1. Open a VPN client (Clash Meta / sing-box / v2rayN / "
            "Streisand / NekoBox / v2box or a compatible client).",
            "2. Import the URI from the email text or scan the QR from the attachment.",
            "3. Save the profile and connect.",
            "",
            "If the QR will not scan, copy the URI by hand in full, with no spaces "
            "or line breaks.",
            "",
            "This email is a backup copy. Telegram messages with profiles may "
            "auto-delete by TTL.",
        ]
    )
    msg.set_content("\n".join(body_lines), charset="utf-8", cte="base64")

    for proto, png in qr_images.items():
        if not png:
            continue
        client_name = (profiles.get(proto) or {}).get("client_name") or "client"
        safe_proto = _safe_attachment_segment(str(proto))
        safe_client = _safe_attachment_segment(str(client_name))
        msg.add_attachment(
            png,
            maintype="image",
            subtype="png",
            filename=f"qr-{safe_proto}-{safe_client}.png",
        )
    return msg, ""


def _gmail_token_diagnostic(client_secrets_path: str, token_path: str) -> list[str]:
    """Return non-secret diagnostics for Gmail OAuth token troubleshooting."""
    details: list[str] = []
    token_file = Path(token_path)
    details.append(f"token_path={token_path}")
    if not token_file.exists():
        details.append("token_file=missing")
        return details
    details.append(f"token_size={token_file.stat().st_size}")
    if not token_file.is_file():
        details.append("token_file=not_regular_file")
        return details
    try:
        token_data = json.loads(token_file.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        details.append(f"token_json_error={type(exc).__name__}")
        return details

    details.append(f"has_refresh_token={bool(token_data.get('refresh_token'))}")
    details.append(f"expiry={token_data.get('expiry') or '-'}")
    scopes = token_data.get("scopes") or token_data.get("scope") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    details.append(
        "has_gmail_send_scope="
        f"{'https://www.googleapis.com/auth/gmail.send' in scopes}"
    )

    try:
        client_data = json.loads(Path(client_secrets_path).read_text(encoding="utf-8") or "{}")
        installed = client_data.get("installed") or client_data.get("web") or {}
        details.append(
            f"client_id_matches={token_data.get('client_id') == installed.get('client_id')}"
        )
    except Exception as exc:
        details.append(f"client_json_error={type(exc).__name__}")
    return details


def _ensure_gmail_credentials(client_secrets_path: str, token_path: str) -> Tuple[Optional[object], str]:
    """Load the token from disk and refresh if needed.

    Browser-based first-time OAuth **is disabled by default on server/Docker** (no GUI).
    Set ``GMAIL_OAUTH_ALLOW_LOCAL_SERVER=1`` only on a machine with a browser during debugging.
    In production: obtain ``gmail_token.json`` on a PC and copy it to the VPS.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/gmail.send"]
    token_file = Path(_expanded_path(token_path))
    secret_s = str(Path(_expanded_path(client_secrets_path)).resolve())
    token_s = str(token_file.resolve())
    token_diag = _gmail_token_diagnostic(secret_s, token_s)

    creds: Optional[Credentials] = None
    if token_file.is_file() and token_file.stat().st_size > 0:
        try:
            creds = Credentials.from_authorized_user_file(token_s, scopes)
        except Exception as exc:
            logger.warning("gmail token file unreadable: %s", exc)
            token_diag.append(f"token_parse_error={type(exc).__name__}: {exc}")

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json(), encoding="utf-8")
            return creds, ""
        except Exception as exc:
            logger.warning("gmail token refresh failed: %s", exc)
            token_diag.append(f"refresh_failed={type(exc).__name__}: {exc}")
            creds = None

    if creds and creds.valid:
        return creds, ""

    if creds and not creds.refresh_token:
        token_diag.append("refresh_token_missing")
    elif creds and creds.expired:
        token_diag.append("token_expired")
    elif creds:
        token_diag.append("token_not_valid")

    allow = _env("GMAIL_OAUTH_ALLOW_LOCAL_SERVER", "").lower() in ("1", "true", "yes")
    open_br = _env("GMAIL_OAUTH_OPEN_BROWSER", "").lower() in ("1", "true", "yes")
    if not allow:
        return None, (
            "Gmail API: no valid token (gmail_token.json is missing, empty, or "
            "could not be refreshed). On a server without a browser, sign in on a PC with the same "
            "OAuth JSON, then copy gmail_token.json to the VPS into the compose directory "
            "(see POST_DEPLOY §11). Interactive OAuth on the server is disabled.\n"
            f"Token diagnostics: {'; '.join(token_diag)}"
        )
    if open_br and (Path("/.dockerenv").exists() or _env("DOCKER_CONTAINER")):
        return None, (
            "Gmail API: GMAIL_OAUTH_OPEN_BROWSER=1 is set, but the bot is running in Docker, "
            "where there is no browser. Remove GMAIL_OAUTH_ALLOW_LOCAL_SERVER/"
            "GMAIL_OAUTH_OPEN_BROWSER from .env on the VPS and copy a ready "
            "gmail_token.json obtained on a PC (see POST_DEPLOY §11.2)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(secret_s, scopes)
    try:
        creds = flow.run_local_server(
            port=0,
            open_browser=open_br,
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
    except Exception as exc:
        logger.exception("gmail OAuth run_local_server failed")
        if "browser" in str(exc).lower():
            return None, (
                "Gmail API: no browser found for OAuth. On VPS/Docker do not run "
                "interactive sign-in: obtain gmail_token.json on a PC and copy it "
                "to the server at the path in GMAIL_TOKEN_PATH (see POST_DEPLOY §11.2). "
                "Also remove GMAIL_OAUTH_ALLOW_LOCAL_SERVER=1 and "
                "GMAIL_OAUTH_OPEN_BROWSER=1 from the server .env."
            )
        return None, (
            f"OAuth failed ({exc}). Put a ready gmail_token.json from a PC onto the VPS. "
            "On a PC without auto-opening a browser: GMAIL_OAUTH_OPEN_BROWSER=0 and open the URL from the logs."
        )

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds, ""


def _send_profile_via_gmail_api(
    to_email: str,
    user_id: int,
    profiles: Dict[str, Dict],
    qr_images: Optional[Dict[str, bytes]],
) -> Tuple[bool, str]:
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        return False, (
            "Package google-api-python-client is missing. Install dependencies from requirements.txt."
        )

    client_path = os.path.abspath(_expanded_path(_gmail_oauth_client_file()))
    token_path = os.path.abspath(_expanded_path(_gmail_token_file()))
    qr_images = qr_images or {}

    try:
        creds, gerr = _ensure_gmail_credentials(client_path, token_path)
        if creds is None:
            return False, gerr or "Gmail API: no credentials."
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        # The token is issued only with the gmail.send scope. users.getProfile needs
        # a broader scope and would return 403 insufficientPermissions, so
        # the sender is set explicitly via GMAIL_FROM.
        from_addr = (_env("GMAIL_FROM")).strip().lower()
        if not from_addr or "@" not in from_addr:
            return False, (
                "Gmail API: set GMAIL_FROM in .env (for example "
                "GMAIL_FROM=you@example.com). The gmail.send token does not allow "
                "reading the account profile automatically."
            )

        msg, cerr = _compose_profile_email_message(
            from_addr, to_email, user_id, profiles, qr_images
        )
        if msg is None:
            return False, cerr

        payload = _flatten_rfc822(msg)
        payload.decode("ascii")
        raw = base64.urlsafe_b64encode(payload).decode("ascii")
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        logger.info(
            "Gmail API sent profile email: to=%s from=%s message_id=%s thread_id=%s",
            normalize_email(to_email),
            from_addr,
            sent.get("id"),
            sent.get("threadId"),
        )
        return True, "ok"
    except UnicodeDecodeError as exc:
        logger.error("Gmail raw MIME not ascii: %s", exc)
        return False, f"email serialization for Gmail API: {exc}"
    except HttpError as exc:
        logger.error("Gmail API send failed: %s", exc)
        status = getattr(getattr(exc, "resp", None), "status", "")
        detail = getattr(exc, "error_details", None) or getattr(exc, "content", "") or str(exc)
        if isinstance(detail, bytes):
            try:
                detail = detail.decode("utf-8", errors="replace")
            except Exception:
                detail = repr(detail)
        return False, f"Gmail API: {status} {detail}"
    except OSError as exc:
        logger.error("Gmail credential flow failed: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        logger.exception("Gmail send unexpected failure")
        return False, f"{type(exc).__name__}: {exc}"


# --- Send ---


def send_profile_email(
    to_email: str,
    user_id: int,
    profiles: Dict[str, Dict],
    qr_images: Optional[Dict[str, bytes]] = None,
) -> Tuple[bool, str]:
    """Send the user an email with their VPN profiles.

    If ``GMAIL_OAUTH_CREDENTIALS`` is set, use the Gmail API; otherwise SMTP.

    Args:
        to_email: recipient address (validated here)
        user_id: recipient Telegram ID — goes into Subject and body
        profiles: dict of the form `{protocol: {"client_name": str, "uri": str}}`
        qr_images: optional dict `{protocol: png_bytes}` — attached
                   to the email as `qr-<protocol>-<client>.png`

    Returns (ok, message). `message` is `"ok"` or a human-readable
    error description.
    """
    ok, err = validate_email(to_email)
    if not ok:
        return False, f"email invalid: {err}"
    if not profiles:
        return False, "empty profile list — nothing to send"

    if is_gmail_oauth_configured():
        return _send_profile_via_gmail_api(to_email, user_id, profiles, qr_images)

    cfg = load_smtp_config()
    if cfg is None:
        return False, (
            "Mail is not configured: set GMAIL_OAUTH_CREDENTIALS "
            "(OAuth Desktop JSON from Google Cloud) or SMTP_HOST/SMTP_USER/SMTP_PASS."
        )

    from_addr = _mailbox_only(cfg.mail_from_raw) or _mailbox_only(cfg.user_raw)

    msg, cerr = _compose_profile_email_message(
        from_addr, to_email, user_id, profiles, qr_images or {}
    )
    if msg is None:
        return False, cerr or (
            "Set SMTP_USER (and SMTP_FROM if needed) as an e-mail, "
            "without a localized name in angle brackets."
        )

    host, port, use_tls = cfg.host, cfg.port, cfg.use_tls

    raw_user = _mailbox_only(cfg.user_raw) or cfg.user_raw.strip()
    user, uerr = _smtp_auth_printable_ascii(raw_user, "SMTP_USER")
    if uerr:
        return False, uerr
    password, perr = _smtp_auth_printable_ascii(cfg.password_raw, "SMTP_PASS")
    if perr:
        return False, perr
    if "@" not in user:
        return False, "SMTP_USER must be an email address (for example you@example.com)."

    try:
        def _send_bytes(smtp: smtplib.SMTP) -> None:
            payload = _flatten_rfc822(msg)
            # Must be 7-bit on the wire with a base64 body and ASCII headers.
            payload.decode("ascii")
            refused = smtp.sendmail(from_addr, [normalize_email(to_email)], payload)
            if refused:
                raise smtplib.SMTPException(f"RCPT refused: {refused}")

        if use_tls == "tls":
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                s.login(user, password)
                _send_bytes(s)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                if use_tls == "starttls":
                    s.starttls()
                    s.ehlo()
                s.login(user, password)
                _send_bytes(s)
        return True, "ok"
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("send_profile_email auth failed: %s", exc)
        return False, _smtp_auth_failure_message(exc, host)
    except UnicodeEncodeError as exc:
        logger.error("send_profile_email encoding failed: %s", exc)
        return False, "Email or SMTP AUTH encoding error (printable ASCII is required in .env)."
    except UnicodeDecodeError as exc:
        logger.error("send_profile_email wire not ascii: %s", exc)
        return False, f"email serialization: {exc}"
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("send_profile_email transport failed: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        logger.exception("send_profile_email unexpected failure")
        return False, f"{type(exc).__name__}: {exc}"


# --- QR helper ---


def render_qr_png(text: str) -> Optional[bytes]:
    """Generate a PNG QR code for a string. Returns bytes or None
    on error. Uses the existing `qrcode[pil]` dependency."""
    try:
        import qrcode  # type: ignore
        from io import BytesIO

        buf = BytesIO()
        img = qrcode.make(text)
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        logger.warning("render_qr_png failed: %s", exc)
        return None
