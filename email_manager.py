"""
email_manager.py — отправка bot-managed VPN-профилей пользователю на email.

Конфигурация — через переменные окружения, читаются один раз на импорт.
Если не заданы ни SMTP-триплет, ни файл `GMAIL_OAUTH_CREDENTIALS`,
`is_configured()` вернёт False — handler сообщит об отсутствии настройки почты.

Зона `.ru` / `.su` (и любые из `SMTP_BLOCKED_TLDS`, через запятую)
**отвергается на валидации**: исходящий SMTP к .ru-почтам с RF-VPS
работает нестабильно (sanctions / TLS-фильтры), и пользоваться этим
каналом без сюрпризов нельзя. Используйте международный SMTP-провайдер
(Gmail с App Password, ProtonMail Bridge, Outlook, Yandex Mail с
паролем приложения и т.д.) и адресат на международном TLD.

Альтернатива SMTP — **Gmail API** (OAuth 2.0, тип «Desktop app»):

- В `.env`: `GMAIL_OAUTH_CREDENTIALS=/path/to/client_secret....json`
- Токен: по умолчанию `gmail_token.json` или `GMAIL_TOKEN_PATH`

Первый запуск откроет браузер. На сервере без GUI скопируйте `gmail_token.json`
с машины, где прошла авторизация. JSON с client_secret не коммитить.
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
    """`tls` (SMTPS на :465), `starttls` (default :587), `none`."""
    val = _env("SMTP_USE_TLS", "starttls").lower()
    return val if val in ("tls", "starttls", "none") else "starttls"


def _blocked_tlds() -> Tuple[str, ...]:
    """TLD'ы, на которые отправлять запрещено. Дефолт: `.ru, .su`."""
    raw = _env("SMTP_BLOCKED_TLDS", ".ru,.su")
    items = [
        s.strip().lower()
        for s in raw.split(",")
        if s.strip()
    ]
    # Убедимся, что каждое начинается с точки
    return tuple(s if s.startswith(".") else "." + s for s in items)


def _expanded_path(rel: str) -> str:
    """Путь из .env с ~ и переменными окружения."""
    return os.path.normpath(os.path.expandvars(os.path.expanduser(rel.strip())))


def _gmail_oauth_client_file() -> str:
    """JSON «OAuth client» (Desktop app) из Google Cloud Console."""
    return _env("GMAIL_OAUTH_CREDENTIALS")


def _gmail_token_file() -> str:
    """Файл с сохранённым refresh/access token."""
    return _env("GMAIL_TOKEN_PATH", "gmail_token.json") or "gmail_token.json"


def is_gmail_oauth_configured() -> bool:
    p = _gmail_oauth_client_file()
    return bool(p) and os.path.isfile(_expanded_path(p))


def _smtp_fully_configured() -> bool:
    return bool(_smtp_host() and _smtp_user() and _smtp_pass())


def is_configured() -> bool:
    """True, если настроен SMTP (полный триплет) или Gmail OAuth JSON."""
    return is_gmail_oauth_configured() or _smtp_fully_configured()


class SMTPConfig(NamedTuple):
    """Снимок настроек SMTP из окружения (для отправки и будущих провайдеров)."""

    host: str
    port: int
    use_tls: str
    mail_from_raw: str
    user_raw: str
    password_raw: str


def load_smtp_config() -> Optional[SMTPConfig]:
    """Возвращает конфиг, только если заданы host, user и pass."""
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
    """Если задан путь к OAuth JSON — файл должен быть и зависимости установлены."""
    raw = (_gmail_oauth_client_file() or "").strip()
    if not raw:
        return
    path = os.path.abspath(_expanded_path(raw))
    if not os.path.isfile(path):
        raise RuntimeError(
            f"GMAIL_OAUTH_CREDENTIALS: файл не найден ({path}). "
            "Укажите путь к JSON для типа Desktop app из Google Cloud Console."
        )
    try:
        from google.oauth2.credentials import Credentials  # noqa: F401

        __import__("google_auth_oauthlib.flow")  # InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "Включён Gmail API (GMAIL_OAUTH_CREDENTIALS), но нет зависимостей. "
            "Установите: pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        ) from exc
    tok = os.path.abspath(_expanded_path(_gmail_token_file()))
    logger.info(
        "Gmail API: клиентские секреты %s; сохранённый token.json → %s",
        path,
        tok,
    )


def validate_smtp_env() -> None:
    """Fail-fast при старте: Gmail OAuth или неполный SMTP в .env; подсказки по Gmail.

    Если ни Gmail JSON, ни SMTP не заданы — молча. При частичном SMTP — ошибка.
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
            "SMTP: укажите все SMTP_HOST, SMTP_USER и SMTP_PASS "
            "(или оставьте все пустыми — тогда /email_profile недоступен)."
        )

    if not all_set:
        return

    if port_raw:
        try:
            int(port_raw)
        except ValueError as exc:
            raise RuntimeError(
                f"SMTP_PORT должен быть числом, сейчас: {port_raw!r}"
            ) from exc

    hl = h.lower()
    if "gmail" in hl or "google" in hl:
        cleaned = _strip_smtp_auth_noise(p)
        printable = "".join(c for c in cleaned if 32 <= ord(c) < 127)
        if len(printable) < 16:
            logger.warning(
                "SMTP_PASS короче 16 печатаемых символов — для Gmail обычно "
                "«Пароль приложения» из 16 символов. Проверьте .env."
            )
        logger.info(
            "SMTP: задан Gmail-хост. Лимиты и политика Google для продакшена "
            "могут быть жёстче — при массовой рассылке смотрите SendGrid/Mailgun."
        )


# --- Validation ---

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def validate_email(email: str) -> Tuple[bool, str]:
    """`(ok, error_message)`. На False сообщает причину."""
    if not email:
        return False, "пустой адрес"
    addr = email.strip().lower()
    if "@" not in addr:
        return False, "нет @"
    if not _EMAIL_RE.match(addr):
        return False, "формат не похож на email"
    blocked = _blocked_tlds()
    for tld in blocked:
        if addr.endswith(tld):
            return False, (
                f"домен {tld} запрещён (см. SMTP_BLOCKED_TLDS); "
                f"используйте международный — gmail/proton/outlook/яндекс"
            )
    return True, ""


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _safe_attachment_segment(name: str) -> str:
    """Имя файла для вложения: без пробелов и символов, ломающих RFC / Gmail."""
    raw = (name or "client").strip() or "client"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    return (safe[:80] if safe else "client")


def _mailbox_only(header_value: str) -> str:
    """Только адрес e-mail: не-ASCII в display-name (Name <mail>) ломает сериализацию."""
    raw = (header_value or "").strip()
    if not raw:
        return raw
    _name, addr = parseaddr(raw)
    if addr and "@" in addr:
        return addr.strip()
    return raw


def _flatten_rfc822(msg: EmailMessage) -> bytes:
    """Как smtplib.send_message: shallow copy, без Bcc, CRLF, bytes."""
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
    """Убирает типичный мусор из копипаста в .env (BOM, zero-width, NBSP)."""
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
    """Оставляем символы в диапазоне печатаемого ASCII (как у App Password Google).

    Убирает невидимый мусор из копипаста; иначе smtplib AUTH PLAIN падает на .encode('ascii').
    """
    raw = _strip_smtp_auth_noise(value or "")
    cleaned = "".join(c for c in raw if 32 <= ord(c) < 127)
    if not cleaned:
        if raw:
            return "", (
                f"{label}: после очистки не осталось символов — проверьте .env "
                "(нужен обычный ASCII, без кавычек/пробелов вокруг пароля целиком)."
            )
        return "", f"{label}: пусто в .env"
    if cleaned != _strip_smtp_auth_noise(value or ""):
        logger.warning("%s: удалены непечатаемые/не-ASCII символы (SMTP AUTH)", label)
    return cleaned, None


def _smtp_auth_failure_message(
    exc: smtplib.SMTPAuthenticationError, host: str
) -> str:
    """Подсказка для Telegram: оператор чинит .env без логов."""
    code = getattr(exc, "smtp_code", None)
    detail = str(exc)
    hl = (host or "").lower()
    if code == 535 and ("gmail" in hl or "google" in hl):
        return (
            "❌ Gmail не принял логин/пароль (SMTP 535).\n\n"
            "Проверьте:\n"
            "1) Используется пароль приложения (не обычный пароль от почты).\n"
            "   Google → Безопасность → 2FA → Пароли приложений → Почта.\n\n"
            "2) SMTP_USER совпадает с тем же Gmail, для которого создан пароль.\n\n"
            "3) Пароль вставлен без пробелов и переносов строк.\n\n"
            "4) Пароль приложения не устарел (при сомнениях — создайте новый).\n\n"
            "5) Для Google Workspace SMTP может быть отключён админом.\n\n"
            f"Ответ сервера: {detail}"
        )
    return f"SMTP auth error ({code}): {detail}"


def _compose_profile_email_message(
    from_addr: str,
    to_email: str,
    user_id: int,
    profiles: Dict[str, Dict],
    qr_images: Optional[Dict[str, bytes]] = None,
) -> Tuple[Optional[EmailMessage], str]:
    """Сборка того же MIME, что отправляется по SMTP или через Gmail API."""
    from_ascii = (_mailbox_only(from_addr) or from_addr.strip()).strip()
    if not from_ascii or "@" not in from_ascii:
        return None, (
            "Укажите адрес отправителя как простой email (ASCII), "
            "без локализованного имени в угловых скобках."
        )
    qr_images = qr_images or {}

    msg = EmailMessage(policy=SMTP_POLICY)
    profile_count = len(profiles)
    msg["Subject"] = f"VPN access profiles for Telegram ID {user_id}"
    msg["From"] = from_ascii
    msg["To"] = normalize_email(to_email)

    profile_word = "профиль" if profile_count == 1 else "профилей"
    body_lines = [
        "Здравствуйте!",
        "",
        f"Для Telegram-ID {user_id} подготовлено {profile_count} VPN-{profile_word}.",
        "",
        "Что внутри письма:",
        "- ниже указаны URI/ссылки для ручного импорта;",
        "- QR-коды приложены отдельными PNG-файлами;",
        "- каждый QR соответствует одноимённому профилю.",
        "",
        "ВАЖНО: эти ссылки и QR дают доступ к VPN. Не пересылайте письмо "
        "посторонним и не публикуйте QR в открытых чатах.",
        "",
        "Профили:",
    ]
    for proto, p in profiles.items():
        name = p.get("client_name", "")
        uri = p.get("uri", "")
        title = f"{proto}"
        if name:
            title += f" / {name}"
        body_lines.append("")
        body_lines.append(f"--- {title} ---")
        body_lines.append(uri or "(URI не сгенерирован)")
        body_lines.append("")
    body_lines.extend(
        [
            "Как подключиться:",
            "1. Откройте VPN-клиент (Clash Meta / sing-box / v2rayN / "
            "Streisand / NekoBox / v2box или совместимый клиент).",
            "2. Импортируйте URI из текста письма или отсканируйте QR из вложения.",
            "3. Сохраните профиль и подключитесь.",
            "",
            "Если QR не сканируется, скопируйте URI вручную целиком, без пробелов "
            "и переносов.",
            "",
            "Это письмо — резервная копия. Сообщения с профилями в Telegram могут "
            "автоудаляться по TTL.",
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
    """Загрузить токен с диска и при необходимости refresh.

    Первичный OAuth через браузер **на сервере/Docker по умолчанию отключён** (нет GUI).
    Задайте ``GMAIL_OAUTH_ALLOW_LOCAL_SERVER=1`` только на машине с браузером при отладке.
    В проде: получите ``gmail_token.json`` на ПК и скопируйте на VPS.
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
            "Gmail API: нет действующего токена (gmail_token.json отсутствует, пустой или "
            "не удалось обновить). На сервере без браузера выполните вход на ПК с тем же "
            "OAuth JSON, затем скопируйте gmail_token.json на VPS в каталог с compose "
            "(см. POST_DEPLOY §11). Интерактивный OAuth на сервере выключён.\n"
            f"Диагностика токена: {'; '.join(token_diag)}"
        )
    if open_br and (Path("/.dockerenv").exists() or _env("DOCKER_CONTAINER")):
        return None, (
            "Gmail API: включён GMAIL_OAUTH_OPEN_BROWSER=1, но бот запущен в Docker, "
            "где нет браузера. Уберите GMAIL_OAUTH_ALLOW_LOCAL_SERVER/"
            "GMAIL_OAUTH_OPEN_BROWSER из .env на VPS и скопируйте готовый "
            "gmail_token.json, полученный на ПК (см. POST_DEPLOY §11.2)."
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
                "Gmail API: не найден браузер для OAuth. На VPS/Docker не запускайте "
                "интерактивный вход: получите gmail_token.json на ПК и скопируйте его "
                "на сервер в путь из GMAIL_TOKEN_PATH (см. POST_DEPLOY §11.2). "
                "Также удалите GMAIL_OAUTH_ALLOW_LOCAL_SERVER=1 и "
                "GMAIL_OAUTH_OPEN_BROWSER=1 из серверного .env."
            )
        return None, (
            f"OAuth не удался ({exc}). На VPS положите готовый gmail_token.json с ПК. "
            "Для ПК без автооткрытия браузера: GMAIL_OAUTH_OPEN_BROWSER=0 и откройте URL из логов."
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
            "Нет пакета google-api-python-client. Установите зависимости из requirements.txt."
        )

    client_path = os.path.abspath(_expanded_path(_gmail_oauth_client_file()))
    token_path = os.path.abspath(_expanded_path(_gmail_token_file()))
    qr_images = qr_images or {}

    try:
        creds, gerr = _ensure_gmail_credentials(client_path, token_path)
        if creds is None:
            return False, gerr or "Gmail API: нет учётных данных."
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        # Токен выдаётся только со scope gmail.send. users.getProfile требует
        # более широких scope и вернёт 403 insufficientPermissions, поэтому
        # отправителя явно задаём через GMAIL_FROM.
        from_addr = (_env("GMAIL_FROM")).strip().lower()
        if not from_addr or "@" not in from_addr:
            return False, (
                "Gmail API: задайте GMAIL_FROM в .env (например "
                "GMAIL_FROM=you@example.com). The gmail.send token does not allow "
                "автоматически читать профиль аккаунта."
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
        return False, f"сериализация письма для Gmail API: {exc}"
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
    """Отправить пользователю письмо с его VPN-профилями.

    Если задан ``GMAIL_OAUTH_CREDENTIALS``, используется Gmail API иначе SMTP.

    Args:
        to_email: адрес получателя (валидируется здесь)
        user_id: Telegram-ID получателя — попадёт в Subject и тело
        profiles: dict вида `{protocol: {"client_name": str, "uri": str}}`
        qr_images: опционально dict `{protocol: png_bytes}` — будут
                   приложены к письму как `qr-<protocol>-<client>.png`

    Возвращает (ok, message). `message` — `"ok"` или человеческое
    описание ошибки.
    """
    ok, err = validate_email(to_email)
    if not ok:
        return False, f"email invalid: {err}"
    if not profiles:
        return False, "пустой список профилей — нечего отправлять"

    if is_gmail_oauth_configured():
        return _send_profile_via_gmail_api(to_email, user_id, profiles, qr_images)

    cfg = load_smtp_config()
    if cfg is None:
        return False, (
            "Почта не настроена: задайте GMAIL_OAUTH_CREDENTIALS "
            "(JSON OAuth Desktop из Google Cloud) или SMTP_HOST/SMTP_USER/SMTP_PASS."
        )

    from_addr = _mailbox_only(cfg.mail_from_raw) or _mailbox_only(cfg.user_raw)

    msg, cerr = _compose_profile_email_message(
        from_addr, to_email, user_id, profiles, qr_images or {}
    )
    if msg is None:
        return False, cerr or (
            "Укажите SMTP_USER (и при необходимости SMTP_FROM) как e-mail, "
            "без локализованного имени в угловых скобках."
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
            # Должен быть 7-bit по проводам при base64-теле и ASCII-заголовках.
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
        return False, "Ошибка кодировки письма или SMTP AUTH (нужен печатаемый ASCII в .env)."
    except UnicodeDecodeError as exc:
        logger.error("send_profile_email wire not ascii: %s", exc)
        return False, f"сериализация письма: {exc}"
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("send_profile_email transport failed: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        logger.exception("send_profile_email unexpected failure")
        return False, f"{type(exc).__name__}: {exc}"


# --- QR helper ---


def render_qr_png(text: str) -> Optional[bytes]:
    """Сгенерировать PNG QR-кода для строки. Возвращает байты или None
    при ошибке. Использует уже существующую зависимость `qrcode[pil]`."""
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
