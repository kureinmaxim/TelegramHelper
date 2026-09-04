# -*- coding: utf-8 -*-
"""
xui_manager.py — клиент 3x-ui (Sanaei) REST API + хранение кредов админа.

Назначение
----------
Бот опционально умеет **управлять отдельной панелью 3x-ui** на этом или
соседнем VPS:
  * проверять связь;
  * перечислять inbound'ы (для выбора, в какой добавлять клиентов);
  * создавать / удалять клиентов в выбранном inbound по email-имени.

Это совершенно НЕ заменяет ботового Xray (`/usr/local/etc/xray` под
`xray.service`). Это «вторая ветка» — для случая, когда админ хочет
выдавать клиентам профили из 3x-ui (и наоборот, удалять их через бота),
не теряя удобства Telegram-интерфейса.

Безопасность
-----------
* Файл конфига `xui_config.json` хранит пароль админа панели только в
  зашифрованном виде (AES-256-GCM, существующий `SecureMessenger`).
* Ключ шифрования берётся из `ENCRYPTION_KEY` (или, если он пуст, из
  `API_SECRET_KEY`) — тех же переменных, которые уже используются для
  шифрования других секретов в этом проекте (см. `encryption.py`,
  `app_keys.py`, `api.py`).
* Если ключ не задан в окружении — бот не сохраняет пароль и явно
  предупреждает админа в чате.

Совместимость с 3x-ui MHSanaei (v2.4+):
  * `POST {base}/login`                            — форма (username/password)
  * `POST {base}/panel/api/inbounds/list`          — список inbound'ов
  * `POST {base}/panel/api/inbounds/get/<id>`      — один inbound (с клиентами)
  * `POST {base}/panel/api/inbounds/addClient`     — добавить клиентов
  * `POST {base}/panel/api/inbounds/<id>/delClient/<client_uuid>` — удалить
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from encryption import EncryptionError, SecureMessenger

logger = logging.getLogger(__name__)


# === Пути и helper'ы ===

CONFIG_PATH = os.getenv("XUI_CONFIG_PATH", "xui_config.json")
_DIR_FALLBACK_CONFIG_NAME = "config.json"

# HTTPS-панели 3x-ui часто живут с self-signed сертификатом, поэтому по
# умолчанию проверку TLS отключаем. Реальное значение хранится в конфиге
# и задаётся админом в /xui_setup.
_DEFAULT_TIMEOUT = 8.0


def _encryption_key_from_env() -> Optional[str]:
    """Ключ для шифрования пароля. Сначала ENCRYPTION_KEY, потом API_SECRET_KEY."""
    for env_name in ("ENCRYPTION_KEY", "API_SECRET_KEY"):
        v = os.getenv(env_name)
        if v:
            return v
    return None


def encryption_available() -> bool:
    """True если из окружения можно собрать ключ для AES-GCM."""
    return bool(_encryption_key_from_env())


def _encrypt_password(plain: str) -> str:
    """Зашифровать пароль и вернуть base64-строку для хранения в JSON."""
    key = _encryption_key_from_env()
    if not key:
        raise EncryptionError(
            "ENCRYPTION_KEY/API_SECRET_KEY не заданы — пароль 3x-ui не сохранён"
        )
    msg = SecureMessenger(key)
    return base64.b64encode(msg.encrypt(plain)).decode("ascii")


def _decrypt_password(payload_b64: str) -> str:
    """Расшифровать base64-строку обратно в пароль."""
    key = _encryption_key_from_env()
    if not key:
        raise EncryptionError(
            "ENCRYPTION_KEY/API_SECRET_KEY не заданы — пароль 3x-ui не расшифровать"
        )
    msg = SecureMessenger(key)
    raw = base64.b64decode(payload_b64.encode("ascii"))
    return msg.decrypt(raw).decode("utf-8")


# === Storage ===

def _effective_config_path() -> str:
    """Return a writable config file path even if Docker created a directory.

    Old compose deployments could create `xui_config.json/` as a directory when
    the host-side bind-mount file was missing. Keep that deployment recoverable
    by storing the real JSON inside the directory.
    """
    if os.path.isdir(CONFIG_PATH):
        return os.path.join(CONFIG_PATH, _DIR_FALLBACK_CONFIG_NAME)
    return CONFIG_PATH

def _empty_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "base_url": "",
        "username": "",
        "password_enc_b64": "",
        "verify_tls": False,
        "default_inbound_id": 0,
        # bot-managed inbound (клон из default_inbound_id, ставится при первом
        # /provision'е). Используется только провизионинг-flow'ом, не
        # подменяет default_inbound_id для прежней `/user`-логики.
        "bot_inbound_id": 0,
        "bot_inbound_remark": "VLESS",
        "bot_inbound_port": 0,
        # Одноразовый флаг «inbound только что создан» — handler читает
        # его в `consume_just_created_flag` и сбрасывает.
        "bot_inbound_just_created": False,
        "configured_at": "",
    }


def load_config() -> Dict[str, Any]:
    """Загрузить xui_config.json. Если файла нет/битый — вернуть пустой шаблон."""
    path = _effective_config_path()
    if not os.path.exists(path):
        return _empty_config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("xui_manager: cannot read %s: %s", path, exc)
        return _empty_config()
    out = _empty_config()
    out.update({k: data.get(k, out[k]) for k in out.keys()})
    return out


def save_config(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Записать xui_config.json напрямую.

    Atomic rename (.tmp → real) не используется намеренно: Docker
    bind-mount отдельного файла делает его mount-point'ом, и
    `os.replace(tmp, real)` падает с `EBUSY: Device or resource busy`
    (нельзя rename поверх mount). Прямой `open("w")` переписывает
    содержимое того же inode и работает. Конфиг маленький, окно
    частичной записи минимальное; такой же подход используется в
    `naiveproxy_manager._save_config` и других *_manager.py.
    """
    path = _effective_config_path()
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True, "ok"
    except OSError as exc:
        logger.error("xui_manager: save_config failed: %s", exc)
        return False, str(exc)


def is_configured() -> bool:
    """True, если есть base_url + username + зашифрованный пароль."""
    cfg = load_config()
    return all((cfg.get("base_url"), cfg.get("username"), cfg.get("password_enc_b64")))


def is_enabled() -> bool:
    """True, если configured() и пользователь не выключал интеграцию."""
    cfg = load_config()
    return bool(cfg.get("enabled")) and is_configured()


def status_summary() -> Dict[str, Any]:
    """Безопасный (без пароля) снимок состояния — для /xui_status и /diag."""
    cfg = load_config()
    return {
        "enabled": bool(cfg.get("enabled")),
        "configured": is_configured(),
        "base_url": cfg.get("base_url", ""),
        "username_masked": _mask_username(cfg.get("username", "")),
        "verify_tls": bool(cfg.get("verify_tls", False)),
        "default_inbound_id": int(cfg.get("default_inbound_id") or 0),
        "configured_at": cfg.get("configured_at", ""),
    }


def _mask_username(name: str) -> str:
    if not name:
        return ""
    if len(name) <= 2:
        return name[0] + "·"
    return name[0] + "·" * (len(name) - 2) + name[-1]


# === Валидация ввода ===

_BASE_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)


def normalize_base_url(raw: str) -> Tuple[bool, str, str]:
    """
    Привести URL к каноническому виду без хвостового слеша.

    На входе ожидаем что-то вроде:
        `https://195.238.122.137:35421/mxmurl/`
    На выходе:
        `https://195.238.122.137:35421/mxmurl`

    Возвращает (ok, normalized_url, message).
    """
    if not raw:
        return False, "", "URL пуст"
    raw = raw.strip()
    if not _BASE_URL_RE.match(raw):
        return False, "", "URL должен быть вида https://host:port/web_base_path"
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return False, "", "URL должен содержать схему и хост"
    path = parsed.path.rstrip("/")
    out = f"{parsed.scheme}://{parsed.netloc}{path}"
    return True, out, "ok"


# === Клиент 3x-ui ===

@dataclass
class XUIClient:
    """
    Тонкий обёртка над requests.Session.

    Жизненный цикл: создать → `login()` → серия методов → объект больше не
    нужен. Cookie панели хранится только в session, никуда не пишется.
    """
    base_url: str
    username: str
    password: str
    verify_tls: bool = False
    timeout: float = _DEFAULT_TIMEOUT
    _session: requests.Session = field(default_factory=requests.Session, init=False, repr=False)
    _logged_in: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._session.verify = self.verify_tls
        # При self-signed cert urllib3 пишет шумный warning — приглушим его,
        # потому что это сознательный выбор админа в /xui_setup.
        if not self.verify_tls:
            try:
                from urllib3.exceptions import InsecureRequestWarning  # type: ignore
                requests.packages.urllib3.disable_warnings(  # type: ignore[attr-defined]
                    InsecureRequestWarning
                )
            except Exception:
                pass

    # -- low-level HTTP --

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        data=None,
        json_body=None,
    ) -> Dict[str, Any]:
        # В 3x-ui read-эндпоинты (`/panel/api/inbounds/list`, `.../get/<id>`)
        # принимают только GET и отвечают 404 на POST. Запись (login,
        # addClient, delClient) — POST.
        try:
            r = self._session.request(
                method,
                self._url(path),
                data=data,
                json=json_body,
                timeout=self.timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            logger.warning("xui_manager: HTTP error %s %s: %s", method, path, exc)
            return {"success": False, "msg": f"network: {exc}"}
        # 3x-ui всегда отвечает JSON; защищаемся от не-JSON.
        try:
            payload = r.json() if r.content else {}
        except ValueError:
            payload = {"success": False, "msg": f"non-json response, status {r.status_code}"}
        if not isinstance(payload, dict):
            payload = {"success": False, "msg": "unexpected response type"}
        if not payload.get("success") and r.status_code >= 400 and "msg" not in payload:
            payload["msg"] = f"HTTP {r.status_code}"
        return payload

    def _post(self, path: str, *, data=None, json_body=None) -> Dict[str, Any]:
        return self._request("POST", path, data=data, json_body=json_body)

    def _get(self, path: str) -> Dict[str, Any]:
        return self._request("GET", path)

    # -- API --

    def _login_once(self) -> Tuple[bool, str]:
        # 3x-ui принимает форму application/x-www-form-urlencoded.
        payload = self._post(
            "/login",
            data={"username": self.username, "password": self.password},
        )
        if payload.get("success"):
            self._logged_in = True
            return True, "ok"
        return False, str(payload.get("msg") or "login failed")

    def login(self) -> Tuple[bool, str]:
        """Логин в панель. При недоступном mesh-URL — авто-fallback на loopback.

        Если ``base_url`` указывает на Tailscale/Headscale CGNAT (100.64/10) и
        login падает по сети (Tailscale stopped / hairpin), пробуем тот же
        порт/path на ``127.0.0.1`` и при успехе переписываем ``xui_config.json``.
        Это покрывает типичный кейс локальной 3x-ui на том же VPS.
        """
        ok, msg = self._login_once()
        if ok:
            return True, msg
        if not _is_transient_network_error(msg):
            return False, msg
        alt = to_loopback_base_url(self.base_url)
        if not alt or alt == self.base_url:
            return False, msg

        old = self.base_url
        logger.warning(
            "xui_manager: mesh panel unreachable (%s); trying loopback %s",
            old,
            alt,
        )
        self.base_url = alt
        self._logged_in = False
        self._session = requests.Session()
        self.__post_init__()
        ok2, msg2 = self._login_once()
        if ok2:
            _persist_base_url_fallback(old, alt)
            return True, f"ok (auto-fallback {old} → {alt})"

        # Оба пути не сработали — вернём исходный URL для понятной ошибки.
        self.base_url = old
        self._logged_in = False
        self._session = requests.Session()
        self.__post_init__()
        return False, (
            f"{msg}\nАвто-fallback на {alt} тоже не удался: {msg2}"
        )

    def list_inbounds(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        if not self._logged_in:
            ok, msg = self.login()
            if not ok:
                return False, msg, []
        payload = self._get("/panel/api/inbounds/list")
        if not payload.get("success"):
            return False, str(payload.get("msg") or "list failed"), []
        obj = payload.get("obj") or []
        return True, "ok", obj if isinstance(obj, list) else []

    def get_inbound(self, inbound_id: int) -> Tuple[bool, str, Dict[str, Any]]:
        if not self._logged_in:
            ok, msg = self.login()
            if not ok:
                return False, msg, {}
        payload = self._get(f"/panel/api/inbounds/get/{int(inbound_id)}")
        if not payload.get("success"):
            return False, str(payload.get("msg") or "get failed"), {}
        obj = payload.get("obj") or {}
        return True, "ok", obj if isinstance(obj, dict) else {}

    def list_clients(self, inbound_id: int) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Вернуть распарсенный список clients из inbound.settings."""
        ok, msg, inbound = self.get_inbound(inbound_id)
        if not ok:
            return False, msg, []
        try:
            settings_raw = inbound.get("settings") or "{}"
            settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
            clients = settings.get("clients") or []
            return True, "ok", clients if isinstance(clients, list) else []
        except (TypeError, ValueError) as exc:
            return False, f"settings parse: {exc}", []

    def find_client(self, inbound_id: int, email: str) -> Tuple[bool, Dict[str, Any]]:
        """Найти клиента по email; возвращает (found, client_dict)."""
        ok, _msg, clients = self.list_clients(inbound_id)
        if not ok:
            return False, {}
        for c in clients:
            if str(c.get("email", "")).strip() == email.strip():
                return True, c
        return False, {}

    def add_vless_reality_client(
        self,
        inbound_id: int,
        email: str,
        client_uuid: Optional[str] = None,
        flow: str = "xtls-rprx-vision",
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Создать VLESS-клиента в указанном inbound.

        Если клиент с таким `email` уже есть — вернёт `(False, "exists", existing)`.
        """
        if not self._logged_in:
            ok, msg = self.login()
            if not ok:
                return False, msg, {}
        # Проверим существование, чтобы не плодить дубликаты при повторном клике.
        exists, current = self.find_client(inbound_id, email)
        if exists:
            return False, "exists", current
        client = _new_vless_reality_client(email, client_uuid=client_uuid, flow=flow)
        body = {
            "id": int(inbound_id),
            "settings": json.dumps({"clients": [client]}, ensure_ascii=False),
        }
        payload = self._post("/panel/api/inbounds/addClient", json_body=body)
        if payload.get("success"):
            return True, "created", client
        return False, str(payload.get("msg") or "addClient failed"), {}

    def del_client(self, inbound_id: int, client_uuid: str) -> Tuple[bool, str]:
        """Удалить клиента по UUID. 3x-ui требует именно UUID, не email."""
        if not self._logged_in:
            ok, msg = self.login()
            if not ok:
                return False, msg
        payload = self._post(
            f"/panel/api/inbounds/{int(inbound_id)}/delClient/{client_uuid}"
        )
        if payload.get("success"):
            return True, "deleted"
        return False, str(payload.get("msg") or "delClient failed")

    def add_inbound(self, payload: Dict[str, Any]) -> Tuple[bool, str, int]:
        """`POST /panel/api/inbounds/add` — создать новый inbound.

        3x-ui принимает form-encoded (как UI), где `settings`/
        `streamSettings`/`sniffing`/`allocate` — JSON-strings.
        Возвращает (ok, msg, new_id).
        """
        if not self._logged_in:
            ok, msg = self.login()
            if not ok:
                return False, msg, 0
        resp = self._post("/panel/api/inbounds/add", data=payload)
        if not resp.get("success"):
            return False, str(resp.get("msg") or "add failed"), 0
        obj = resp.get("obj") or {}
        new_id = 0
        if isinstance(obj, dict):
            try:
                new_id = int(obj.get("id") or 0)
            except (TypeError, ValueError):
                new_id = 0
        return True, "created", new_id


# === Helpers для VLESS-Reality client object ===

def _random_sub_id() -> str:
    """16 hex-символов как в 3x-ui по умолчанию."""
    return secrets.token_hex(8)


def _new_vless_reality_client(
    email: str,
    client_uuid: Optional[str] = None,
    flow: str = "xtls-rprx-vision",
) -> Dict[str, Any]:
    return {
        "id": client_uuid or str(uuid.uuid4()),
        "flow": flow,
        "email": email,
        "limitIp": 0,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "tgId": "",
        "subId": _random_sub_id(),
        "reset": 0,
    }


# === Высокоуровневые функции для бота ===

def panel_host(base_url: str) -> str:
    """Извлечь host (без порта/пути) из base_url панели — на случай, когда
    у inbound пустой `listen`."""
    try:
        return urlsplit(base_url).hostname or ""
    except Exception:
        return ""


def url_host_is_mesh_ip(url: str) -> bool:
    """True если хост URL — IP из CGNAT-диапазона
    (Tailscale/Headscale: 100.64.0.0/10) или Tailscale-ULA
    (fd7a:115c:a1e0::/48). Используется для подсказок про
    `network_mode: host` в `/xui_setup`."""
    import ipaddress
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    try:
        if ip.version == 4:
            return ip in ipaddress.ip_network("100.64.0.0/10")
        return ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")
    except (ValueError, TypeError):
        return False


def to_loopback_base_url(base_url: str) -> Optional[str]:
    """Переписать mesh-URL панели на loopback того же порта/path.

    Локальная 3x-ui на том же VPS часто задана как ``https://100.64.x.x:8081/...``,
    но когда Tailscale/Headscale клиент остановлен, mesh-IP недоступен, а
    ``127.0.0.1`` продолжает отвечать. Возвращает ``None``, если хост не mesh.
    """
    if not base_url or not url_host_is_mesh_ip(base_url):
        return None
    import ipaddress

    parts = urlsplit(base_url)
    host = parts.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.version == 4:
        loop_host = "127.0.0.1"
    else:
        loop_host = "[::1]"
    port = parts.port
    netloc = f"{loop_host}:{port}" if port else loop_host
    path = (parts.path or "").rstrip("/")
    return f"{parts.scheme}://{netloc}{path}"


def _is_transient_network_error(msg: str) -> bool:
    """Сетевые ошибки login, при которых имеет смысл loopback-fallback."""
    m = (msg or "").lower()
    needles = (
        "network:",
        "connecttimeout",
        "timed out",
        "timeout",
        "connection refused",
        "failed to establish",
        "name or service not known",
        "nodename nor servname",
        "temporary failure",
        "network is unreachable",
        "no route to host",
    )
    return any(n in m for n in needles)


def _persist_base_url_fallback(old_url: str, new_url: str) -> None:
    """Сохранить loopback URL в xui_config.json, если там ещё старый mesh."""
    try:
        cfg = load_config()
        if cfg.get("base_url") != old_url:
            return
        cfg["base_url"] = new_url
        ok, msg = save_config(cfg)
        if ok:
            logger.info(
                "xui_manager: persisted panel URL fallback %s → %s",
                old_url,
                new_url,
            )
        else:
            logger.warning(
                "xui_manager: could not persist URL fallback: %s", msg
            )
    except Exception as exc:
        logger.warning("xui_manager: persist URL fallback failed: %s", exc)


def build_vless_reality_link(
    inbound: Dict[str, Any],
    client: Dict[str, Any],
    *,
    fallback_host: str = "",
) -> Tuple[bool, str, str]:
    """
    Построить `vless://...` URI из объекта inbound и client панели 3x-ui.

    Возвращает (ok, message, link). Поддерживается только VLESS-Reality
    (`security=reality`), потому что только её бот сейчас умеет использовать
    с своей стороны.
    """
    try:
        protocol = (inbound.get("protocol") or "").lower()
        if protocol != "vless":
            return False, f"inbound protocol != vless ({protocol})", ""

        port = int(inbound.get("port") or 0)
        if not port:
            return False, "у inbound нет порта", ""

        listen = (inbound.get("listen") or "").strip()
        host = listen or fallback_host
        if host in ("", "0.0.0.0", "::"):
            host = fallback_host
        if not host:
            return False, "не удалось определить host для ссылки", ""

        client_uuid = str(client.get("id") or "").strip()
        if not client_uuid:
            return False, "у клиента нет id", ""
        flow = (client.get("flow") or "").strip() or "xtls-rprx-vision"

        # streamSettings приходит как JSON-строка
        ss_raw = inbound.get("streamSettings") or "{}"
        if isinstance(ss_raw, str):
            ss = json.loads(ss_raw)
        else:
            ss = ss_raw or {}

        security = (ss.get("security") or "").lower()
        network = (ss.get("network") or "tcp").lower()

        if security != "reality":
            # Бот сейчас работает только с Reality. Для других вариантов
            # вернём ошибку — пусть оператор копирует ссылку из самой панели.
            return False, f"inbound security={security or 'none'} (нужен reality)", ""

        rs = ss.get("realitySettings") or {}
        rs_settings = rs.get("settings") or {}

        pbk = (rs_settings.get("publicKey") or "").strip()
        fp = (rs_settings.get("fingerprint") or "chrome").strip()
        spx = (rs_settings.get("spiderX") or "/").strip() or "/"

        server_names = rs.get("serverNames") or []
        sni = (server_names[0] if server_names else "").strip()

        short_ids = rs.get("shortIds") or []
        sid = (short_ids[0] if short_ids else "").strip()

        if not pbk:
            return False, "у inbound нет realitySettings.publicKey", ""
        if not sni:
            return False, "у inbound пустой serverNames", ""

        from urllib.parse import quote

        params = [
            ("type", network),
            ("security", "reality"),
            ("pbk", pbk),
            ("fp", fp),
            ("sni", sni),
        ]
        if sid:
            params.append(("sid", sid))
        if spx:
            params.append(("spx", spx))
        if flow:
            params.append(("flow", flow))
        # Параметры строим вручную, потому что в spx часто `/` — её не
        # надо percent-кодировать в путь, но в query безопасней закодировать.
        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)

        remark = client.get("email") or inbound.get("remark") or "vless-reality"
        link = f"vless://{client_uuid}@{host}:{port}?{query}#{quote(str(remark))}"
        return True, "ok", link
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("xui_manager: build_vless_reality_link failed: %s", exc)
        return False, f"ошибка сборки ссылки: {exc}", ""


def _clone_inbound_payload(
    source: Dict[str, Any], new_remark: str, new_port: int
) -> Dict[str, Any]:
    """Подготовить payload для `add_inbound` клонированием существующего.

    Сохраняем streamSettings (Reality keys), sniffing, allocate, protocol,
    меняем только: remark, port, settings.clients=[], обнуляем счётчики.
    """
    payload: Dict[str, Any] = {}
    for k, v in source.items():
        if k in ("id", "up", "down", "clientStats"):
            continue
        payload[k] = v
    payload["remark"] = new_remark
    payload["port"] = int(new_port)
    payload["enable"] = True
    # `settings` приходит JSON-строкой; чистим список clients
    settings_raw = source.get("settings") or "{}"
    try:
        if isinstance(settings_raw, str):
            settings_obj = json.loads(settings_raw)
        else:
            settings_obj = settings_raw or {}
        if not isinstance(settings_obj, dict):
            settings_obj = {}
    except (TypeError, ValueError):
        settings_obj = {}
    settings_obj["clients"] = []
    payload["settings"] = json.dumps(settings_obj, ensure_ascii=False)
    return payload


def provision_named_client(
    client_name: str, telegram_id: int = 0
) -> Tuple[bool, str, str]:
    """Создаёт canonical-клиента в `default_inbound_id` (manual inbound,
    обычно на :443). Reality-стелс полный — не плодит отдельный
    bot-managed inbound на нестандартном порту.

    Изоляция от ручных клиентов админа — по имени: бот трогает
    только тех клиентов, чей email совпадает с canonical-паттерном
    `<Prefix>_ID<first2>_<last2>`. Манипуляции `IPhone13`/`MacBook_Air`/
    и прочих ручных клиентов не происходит.

    Идемпотентно: если клиент с таким именем уже есть — отдаёт
    существующий + строит для него URI.

    Backward-compat: при наличии legacy `bot_inbound_id` (от старого
    клон-flow) попытка снести там одноимённого клиента — чтобы не
    плодились дубли при миграции.

    Возвращает (ok, message, vless_uri).
    """
    cfg = load_config()
    if not is_configured():
        return False, "3x-ui интеграция не настроена (см. /xui_setup)", ""

    default_id = int(cfg.get("default_inbound_id") or 0)
    if not default_id:
        return False, (
            "default_inbound_id не задан в /xui_setup — "
            "не знаю, в какой inbound писать"
        ), ""

    client = make_client_for_config(cfg)
    if client is None:
        return False, (
            "не удалось восстановить XUIClient "
            "(пароль не расшифровывается? проверьте ENCRYPTION_KEY)"
        ), ""

    ok, msg = client.login()
    if not ok:
        return False, f"login: {msg}", ""

    ok2, _msg2, inbound_obj = client.get_inbound(default_id)
    if not ok2 or not inbound_obj:
        return False, (
            f"default inbound #{default_id} недоступен в панели"
        ), ""

    ok, msg, client_obj = client.add_vless_reality_client(
        default_id, email=client_name
    )
    existed = (not ok) and (msg == "exists")
    if existed:
        ok = True

    # 3x-ui применяет проверку email-уникальности **глобально по
    # панели**. После Variant A (клон-inbound) и старых тестов в
    # каком-то постороннем inbound мог остаться клиент с тем же
    # canonical-именем — `add_vless_reality_client` тогда вернёт
    # "Duplicate email". Сделаем активный sweep: пройдём по всем
    # inbound'ам в панели, кроме default'а, удалим все находки с этим
    # именем, и повторим add. Это и автоматическая миграция от Variant A,
    # и self-heal от любых других дубликатов.
    if (not ok) and "duplicate email" in str(msg).lower():
        cleared_anywhere = False
        try:
            ok_l, _msg_l, all_inbounds = client.list_inbounds()
            if ok_l:
                for ib in all_inbounds or []:
                    try:
                        ib_id = int(ib.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
                    if not ib_id or ib_id == default_id:
                        continue
                    try:
                        f, old = client.find_client(ib_id, client_name)
                        if not f:
                            continue
                        uid_old = str(old.get("id") or "").strip()
                        if not uid_old:
                            continue
                        ok_d, msg_d = client.del_client(ib_id, uid_old)
                        if ok_d:
                            cleared_anywhere = True
                            logger.info(
                                "xui_manager: dup-email sweep removed %s "
                                "from inbound %s",
                                client_name, ib_id,
                            )
                        else:
                            logger.warning(
                                "xui_manager: dup-email sweep del_client "
                                "failed in #%s: %s",
                                ib_id, msg_d,
                            )
                    except Exception as exc_inner:
                        logger.warning(
                            "xui_manager: dup-email probe inbound %s: %s",
                            ib_id, exc_inner,
                        )
        except Exception as exc:
            logger.warning("xui_manager: dup-email sweep failed: %s", exc)

        if cleared_anywhere:
            # Retry add — теперь email свободен.
            ok, msg, client_obj = client.add_vless_reality_client(
                default_id, email=client_name
            )
            if (not ok) and msg == "exists":
                ok = True
                existed = True

    if not ok:
        return False, f"addClient: {msg}", ""

    # Перечитаем inbound, чтобы Reality-поля были на актуальном.
    ok2, _msg2, fresh = client.get_inbound(default_id)
    if ok2 and fresh:
        inbound_obj = fresh

    fallback_host = panel_host(cfg.get("base_url", ""))
    ok2, msg2, uri = build_vless_reality_link(
        inbound_obj, client_obj, fallback_host=fallback_host
    )
    if not ok2:
        return True, f"client {'exists' if existed else 'created'} (URI: {msg2})", ""
    return True, "exists" if existed else "created", uri


def get_bot_inbound_port() -> Optional[int]:
    """Cached порт bot-managed VLESS inbound'а — для firewall-подсказок
    в handler'ах. Без обращения к панели."""
    cfg = load_config()
    try:
        p = int(cfg.get("bot_inbound_port") or 0)
    except (TypeError, ValueError):
        return None
    return p if p else None


def consume_just_created_flag() -> bool:
    """One-time flag «bot inbound только что создан». Возвращает True ровно
    один раз после создания, потом всегда False — чтобы admin получил
    подсказку про firewall именно в момент первого provision'а."""
    cfg = load_config()
    flag = bool(cfg.get("bot_inbound_just_created"))
    if flag:
        cfg["bot_inbound_just_created"] = False
        save_config(cfg)
    return flag


def find_named_client_uri(client_name: str) -> Tuple[bool, str, str]:
    """Read-only: найти клиента по email в default (и legacy bot) inbound.

    Раньше искали только default inbound — из-за этого `/profiles` писал
    «нет», хотя рабочий клиент жил в `bot_inbound_id` или под legacy-именем
    (его передают отдельным вызовом). Никаких изменений в панели не делает.

    Возвращает (exists, message, uri).
    """
    return find_named_client_uri_any([client_name])[:3]


def find_named_client_uri_any(
    client_names: List[str],
) -> Tuple[bool, str, str, str]:
    """Как ``find_named_client_uri``, но перебор имён и inbound'ов.

    Возвращает (exists, message, uri, matched_name).
    """
    names = [str(n).strip() for n in (client_names or []) if str(n).strip()]
    # уникальные, порядок сохраняем
    seen: set[str] = set()
    ordered: List[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    if not ordered:
        return False, "empty name list", "", ""

    cfg = load_config()
    if not is_configured():
        return False, "not configured", "", ""
    default_id = int(cfg.get("default_inbound_id") or 0)
    bot_id = int(cfg.get("bot_inbound_id") or 0)
    inbound_ids: List[int] = []
    if default_id:
        inbound_ids.append(default_id)
    if bot_id and bot_id != default_id:
        inbound_ids.append(bot_id)
    if not inbound_ids:
        return False, "no default inbound", "", ""

    xclient = make_client_for_config(cfg)
    if xclient is None:
        return False, "no XUIClient", "", ""
    ok, msg = xclient.login()
    if not ok:
        return False, f"login: {msg}", "", ""

    fallback_host = panel_host(cfg.get("base_url", ""))
    for iid in inbound_ids:
        ok2, _msg2, inbound = xclient.get_inbound(iid)
        if not ok2 or not inbound:
            continue
        for name in ordered:
            found, client_obj = xclient.find_client(iid, name)
            if not found:
                continue
            ok3, msg3, uri = build_vless_reality_link(
                inbound, client_obj, fallback_host=fallback_host
            )
            return True, ("ok" if ok3 else msg3), (uri if ok3 else ""), name
    return False, "not present", "", ""


def remove_named_client(client_name: str) -> Tuple[bool, str]:
    """Удалить canonical-клиента по имени из default inbound (где живут
    и manual-клиенты админа). Бот трогает только клиента с точно
    указанным именем — manual клиенты не задеваются.

    Идемпотентно: если клиента нет — `(True, "not present")`.
    Plus migration: если есть legacy `bot_inbound_id` от старой схемы и
    клиент с таким именем там — снести и его, чтобы не было дубликатов.
    """
    cfg = load_config()
    if not is_configured():
        return False, "not configured"
    default_id = int(cfg.get("default_inbound_id") or 0)
    if not default_id:
        return True, "no default inbound"
    xclient = make_client_for_config(cfg)
    if xclient is None:
        return False, "no XUIClient"
    ok, msg = xclient.login()
    if not ok:
        return False, f"login: {msg}"

    removed_anywhere = False

    # 1. Default inbound — основной источник истины.
    found, client_obj = xclient.find_client(default_id, client_name)
    if found:
        uuid_val = str(client_obj.get("id") or "").strip()
        if uuid_val:
            ok_d, msg_d = xclient.del_client(default_id, uuid_val)
            if ok_d:
                removed_anywhere = True
            else:
                return False, msg_d

    # 2. Legacy bot_inbound (старая схема с клон-inbound на port+1).
    legacy_bot_id = int(cfg.get("bot_inbound_id") or 0)
    if legacy_bot_id and legacy_bot_id != default_id:
        try:
            l_found, l_obj = xclient.find_client(legacy_bot_id, client_name)
            if l_found:
                l_uuid = str(l_obj.get("id") or "").strip()
                if l_uuid:
                    xclient.del_client(legacy_bot_id, l_uuid)
                    removed_anywhere = True
        except Exception as exc:
            logger.warning(
                "xui_manager: legacy del_client probe failed: %s", exc
            )

    return (True, "deleted") if removed_anywhere else (True, "not present")


def make_client_for_config(cfg: Dict[str, Any]) -> Optional[XUIClient]:
    """
    Восстановить XUIClient из сохранённого конфига. Возвращает None, если
    конфиг неполный или пароль не удалось расшифровать.
    """
    if not cfg or not all((cfg.get("base_url"), cfg.get("username"), cfg.get("password_enc_b64"))):
        return None
    try:
        password = _decrypt_password(cfg["password_enc_b64"])
    except (EncryptionError, ValueError) as exc:
        logger.warning("xui_manager: cannot decrypt password: %s", exc)
        return None
    return XUIClient(
        base_url=cfg["base_url"],
        username=cfg["username"],
        password=password,
        verify_tls=bool(cfg.get("verify_tls", False)),
    )


def save_credentials(
    base_url: str,
    username: str,
    password: str,
    *,
    verify_tls: bool = False,
    default_inbound_id: int = 0,
) -> Tuple[bool, str]:
    """
    Сохранить креденшелы 3x-ui (зашифровав пароль). НЕ выполняет логин —
    это делает caller. Возвращает (ok, message).
    """
    if not encryption_available():
        return False, (
            "В .env не задан ENCRYPTION_KEY (или API_SECRET_KEY). "
            "Без него пароль панели не получится зашифровать."
        )
    try:
        enc = _encrypt_password(password)
    except EncryptionError as exc:
        return False, f"шифрование пароля: {exc}"
    cfg = load_config()
    cfg.update(
        {
            "enabled": True,
            "base_url": base_url,
            "username": username,
            "password_enc_b64": enc,
            "verify_tls": bool(verify_tls),
            "default_inbound_id": int(default_inbound_id or 0),
            "configured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    return save_config(cfg)


def set_default_inbound(inbound_id: int) -> Tuple[bool, str]:
    cfg = load_config()
    if not is_configured():
        return False, "интеграция 3x-ui не настроена (см. /xui_setup)"
    cfg["default_inbound_id"] = int(inbound_id)
    return save_config(cfg)


def set_enabled(flag: bool) -> Tuple[bool, str]:
    cfg = load_config()
    if not is_configured() and flag:
        return False, "сначала настройте /xui_setup"
    cfg["enabled"] = bool(flag)
    return save_config(cfg)


def clear_credentials() -> Tuple[bool, str]:
    """Полностью обнулить xui_config.json (без удаления файла)."""
    return save_config(_empty_config())


def make_client_or_error() -> Tuple[Optional[XUIClient], str]:
    """Удобный shortcut для команд: либо клиент, либо человеко-читаемая ошибка."""
    cfg = load_config()
    if not is_configured():
        return None, "интеграция 3x-ui не настроена. Запустите /xui_setup."
    if not cfg.get("enabled"):
        return None, "интеграция 3x-ui выключена. Включите её через /xui_enable."
    client = make_client_for_config(cfg)
    if client is None:
        return None, "не удалось расшифровать креды 3x-ui — проверьте ENCRYPTION_KEY."
    return client, ""
