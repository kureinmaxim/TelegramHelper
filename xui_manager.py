# -*- coding: utf-8 -*-
"""
xui_manager.py — 3x-ui (Sanaei) REST API client + admin credential storage.

Purpose
-------
The bot can optionally **manage a separate 3x-ui panel** on this or
a neighboring VPS:
  * check connectivity;
  * list inbounds (to choose which one to add clients to);
  * create / delete clients in the selected inbound by email name.

This does NOT replace the bot-managed Xray (`/usr/local/etc/xray` under
`xray.service`). It is a "second branch" — for when an admin wants
to issue client profiles from 3x-ui (and delete them via the bot),
without losing the Telegram UI.

Security
--------
* The `xui_config.json` config file stores the panel admin password only in
  encrypted form (AES-256-GCM, existing `SecureMessenger`).
* The encryption key is taken from `ENCRYPTION_KEY` (or, if empty, from
  `API_SECRET_KEY`) — the same variables already used to
  encrypt other secrets in this project (see `encryption.py`,
  `app_keys.py`, `api.py`).
* If the key is not set in the environment — the bot does not save the password
  and warns the admin in chat.

Compatibility with 3x-ui MHSanaei (v2.4+):
  * `POST {base}/login`                            — form (username/password)
  * `POST {base}/panel/api/inbounds/list`          — list of inbounds
  * `POST {base}/panel/api/inbounds/get/<id>`      — one inbound (with clients)
  * `POST {base}/panel/api/inbounds/addClient`     — add clients
  * `POST {base}/panel/api/inbounds/<id>/delClient/<client_uuid>` — delete
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


# === Paths and helpers ===

CONFIG_PATH = os.getenv("XUI_CONFIG_PATH", "xui_config.json")
_DIR_FALLBACK_CONFIG_NAME = "config.json"

# HTTPS 3x-ui panels often run with a self-signed certificate, so TLS
# verification is off by default. The real value is stored in the config
# and set by the admin in /xui_setup.
_DEFAULT_TIMEOUT = 8.0


def _encryption_key_from_env() -> Optional[str]:
    """Key for encrypting the password. ENCRYPTION_KEY first, then API_SECRET_KEY."""
    for env_name in ("ENCRYPTION_KEY", "API_SECRET_KEY"):
        v = os.getenv(env_name)
        if v:
            return v
    return None


def encryption_available() -> bool:
    """True if an AES-GCM key can be built from the environment."""
    return bool(_encryption_key_from_env())


def _encrypt_password(plain: str) -> str:
    """Encrypt the password and return a base64 string for JSON storage."""
    key = _encryption_key_from_env()
    if not key:
        raise EncryptionError(
            "ENCRYPTION_KEY/API_SECRET_KEY are not set — 3x-ui password was not saved"
        )
    msg = SecureMessenger(key)
    return base64.b64encode(msg.encrypt(plain)).decode("ascii")


def _decrypt_password(payload_b64: str) -> str:
    """Decrypt a base64 string back into the password."""
    key = _encryption_key_from_env()
    if not key:
        raise EncryptionError(
            "ENCRYPTION_KEY/API_SECRET_KEY are not set — cannot decrypt the 3x-ui password"
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
        # bot-managed inbound (clone of default_inbound_id, set on first
        # /provision). Used only by the provisioning flow; does not
        # replace default_inbound_id for the older `/user` logic.
        "bot_inbound_id": 0,
        "bot_inbound_remark": "VLESS",
        "bot_inbound_port": 0,
        # One-shot flag "inbound just created" — the handler reads
        # it in `consume_just_created_flag` and clears it.
        "bot_inbound_just_created": False,
        "configured_at": "",
    }


def load_config() -> Dict[str, Any]:
    """Load xui_config.json. If the file is missing/corrupt — return an empty template."""
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
    """Write xui_config.json directly.

    Atomic rename (.tmp → real) is intentionally not used: a Docker
    bind-mount of a single file makes it a mount-point, and
    `os.replace(tmp, real)` fails with `EBUSY: Device or resource busy`
    (cannot rename over a mount). A direct `open("w")` rewrites
    the same inode and works. The config is small, so the partial-write
    window is tiny; the same approach is used in
    `naiveproxy_manager._save_config` and other *_manager.py files.
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
    """True if base_url + username + encrypted password are present."""
    cfg = load_config()
    return all((cfg.get("base_url"), cfg.get("username"), cfg.get("password_enc_b64")))


def is_enabled() -> bool:
    """True if configured() and the user has not turned the integration off."""
    cfg = load_config()
    return bool(cfg.get("enabled")) and is_configured()


def status_summary() -> Dict[str, Any]:
    """Safe (no password) state snapshot — for /xui_status and /diag."""
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


# === Input validation ===

_BASE_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)


def normalize_base_url(raw: str) -> Tuple[bool, str, str]:
    """
    Canonicalize the URL with no trailing slash.

    Input looks like:
        `https://195.238.122.137:35421/mxmurl/`
    Output:
        `https://195.238.122.137:35421/mxmurl`

    Returns (ok, normalized_url, message).
    """
    if not raw:
        return False, "", "URL is empty"
    raw = raw.strip()
    if not _BASE_URL_RE.match(raw):
        return False, "", "URL must look like https://host:port/web_base_path"
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return False, "", "URL must contain a scheme and host"
    path = parsed.path.rstrip("/")
    out = f"{parsed.scheme}://{parsed.netloc}{path}"
    return True, out, "ok"


# === 3x-ui client ===

@dataclass
class XUIClient:
    """
    Thin wrapper around requests.Session.

    Lifecycle: create → `login()` → a series of methods → the object is no longer
    needed. The panel cookie lives only in the session and is never written to disk.
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
        # With a self-signed cert urllib3 logs a noisy warning — mute it,
        # because this is a deliberate admin choice in /xui_setup.
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
        # In 3x-ui, read endpoints (`/panel/api/inbounds/list`, `.../get/<id>`)
        # accept only GET and return 404 on POST. Writes (login,
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
        # 3x-ui always replies with JSON; guard against non-JSON.
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
        # 3x-ui accepts application/x-www-form-urlencoded.
        payload = self._post(
            "/login",
            data={"username": self.username, "password": self.password},
        )
        if payload.get("success"):
            self._logged_in = True
            return True, "ok"
        return False, str(payload.get("msg") or "login failed")

    def login(self) -> Tuple[bool, str]:
        """Log in to the panel. If the mesh URL is unreachable — auto-fallback to loopback.

        If ``base_url`` points at Tailscale/Headscale CGNAT (100.64/10) and
        login fails on the network (Tailscale stopped / hairpin), try the same
        port/path on ``127.0.0.1`` and rewrite ``xui_config.json`` on success.
        This covers the typical local 3x-ui on the same VPS.
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

        # Both paths failed — restore the original URL for a clear error.
        self.base_url = old
        self._logged_in = False
        self._session = requests.Session()
        self.__post_init__()
        return False, (
            f"{msg}\nAuto-fallback to {alt} also failed: {msg2}"
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
        """Return the parsed clients list from inbound.settings."""
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
        """Find a client by email; returns (found, client_dict)."""
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
        Create a VLESS client in the given inbound.

        If a client with this `email` already exists — returns `(False, "exists", existing)`.
        """
        if not self._logged_in:
            ok, msg = self.login()
            if not ok:
                return False, msg, {}
        # Check existence so a double-click does not spawn duplicates.
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
        """Delete a client by UUID. 3x-ui requires UUID, not email."""
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
        """`POST /panel/api/inbounds/add` — create a new inbound.

        3x-ui accepts form-encoded (like the UI), where `settings`/
        `streamSettings`/`sniffing`/`allocate` are JSON-strings.
        Returns (ok, msg, new_id).
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


# === Helpers for the VLESS-Reality client object ===

def _random_sub_id() -> str:
    """16 hex characters, as 3x-ui uses by default."""
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


# === High-level helpers for the bot ===

def panel_host(base_url: str) -> str:
    """Extract the host (no port/path) from the panel base_url — for when
    the inbound has an empty `listen`."""
    try:
        return urlsplit(base_url).hostname or ""
    except Exception:
        return ""


def url_host_is_mesh_ip(url: str) -> bool:
    """True if the URL host is a CGNAT-range IP
    (Tailscale/Headscale: 100.64.0.0/10) or Tailscale ULA
    (fd7a:115c:a1e0::/48). Used for `network_mode: host`
    hints in `/xui_setup`."""
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
    """Rewrite a panel mesh URL to loopback on the same port/path.

    Local 3x-ui on the same VPS is often set as ``https://100.64.x.x:8081/...``,
    but when the Tailscale/Headscale client is stopped the mesh IP is unreachable
    while ``127.0.0.1`` still answers. Returns ``None`` if the host is not mesh.
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
    """Login network errors where a loopback fallback makes sense."""
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
    """Save the loopback URL in xui_config.json if the old mesh is still there."""
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
    Build a `vless://...` URI from a 3x-ui panel inbound and client object.

    Returns (ok, message, link). Only VLESS-Reality (`security=reality`) is
    supported, because that is the only mode the bot currently uses on its side.
    """
    try:
        protocol = (inbound.get("protocol") or "").lower()
        if protocol != "vless":
            return False, f"inbound protocol != vless ({protocol})", ""

        port = int(inbound.get("port") or 0)
        if not port:
            return False, "inbound has no port", ""

        listen = (inbound.get("listen") or "").strip()
        host = listen or fallback_host
        if host in ("", "0.0.0.0", "::"):
            host = fallback_host
        if not host:
            return False, "could not determine host for the link", ""

        client_uuid = str(client.get("id") or "").strip()
        if not client_uuid:
            return False, "client has no id", ""
        flow = (client.get("flow") or "").strip() or "xtls-rprx-vision"

        # streamSettings arrives as a JSON string
        ss_raw = inbound.get("streamSettings") or "{}"
        if isinstance(ss_raw, str):
            ss = json.loads(ss_raw)
        else:
            ss = ss_raw or {}

        security = (ss.get("security") or "").lower()
        network = (ss.get("network") or "tcp").lower()

        if security != "reality":
            # The bot currently only works with Reality. For other variants
            # return an error — the operator can copy the link from the panel itself.
            return False, f"inbound security={security or 'none'} (reality required)", ""

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
            return False, "inbound has no realitySettings.publicKey", ""
        if not sni:
            return False, "inbound has empty serverNames", ""

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
        # Build params by hand because spx often has `/` — it must not be
        # percent-encoded as a path, but encoding in the query is safer.
        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)

        remark = client.get("email") or inbound.get("remark") or "vless-reality"
        link = f"vless://{client_uuid}@{host}:{port}?{query}#{quote(str(remark))}"
        return True, "ok", link
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("xui_manager: build_vless_reality_link failed: %s", exc)
        return False, f"link build error: {exc}", ""


def _clone_inbound_payload(
    source: Dict[str, Any], new_remark: str, new_port: int
) -> Dict[str, Any]:
    """Prepare an `add_inbound` payload by cloning an existing inbound.

    Keep streamSettings (Reality keys), sniffing, allocate, protocol;
    change only: remark, port, settings.clients=[], and reset counters.
    """
    payload: Dict[str, Any] = {}
    for k, v in source.items():
        if k in ("id", "up", "down", "clientStats"):
            continue
        payload[k] = v
    payload["remark"] = new_remark
    payload["port"] = int(new_port)
    payload["enable"] = True
    # `settings` arrives as a JSON string; clear the clients list
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
    """Create a canonical client in `default_inbound_id` (manual inbound,
    usually on :443). Full Reality stealth — does not spawn a separate
    bot-managed inbound on a non-standard port.

    Isolation from the admin's manual clients is by name: the bot only
    touches clients whose email matches the canonical pattern
    `<Prefix>_ID<first2>_<last2>`. It does not manipulate `IPhone13` /
    `MacBook_Air` / other manual clients.

    Idempotent: if a client with this name already exists — return the
    existing one and build a URI for it.

    Backward-compat: if a legacy `bot_inbound_id` (from the old clone
    flow) is present, try to remove a same-named client there so
    duplicates are not left after migration.

    Returns (ok, message, vless_uri).
    """
    cfg = load_config()
    if not is_configured():
        return False, "3x-ui integration is not configured (see /xui_setup)", ""

    default_id = int(cfg.get("default_inbound_id") or 0)
    if not default_id:
        return False, (
            "default_inbound_id is not set in /xui_setup — "
            "I do not know which inbound to write to"
        ), ""

    client = make_client_for_config(cfg)
    if client is None:
        return False, (
            "failed to restore XUIClient "
            "(password not decrypting? check ENCRYPTION_KEY)"
        ), ""

    ok, msg = client.login()
    if not ok:
        return False, f"login: {msg}", ""

    ok2, _msg2, inbound_obj = client.get_inbound(default_id)
    if not ok2 or not inbound_obj:
        return False, (
            f"default inbound #{default_id} is unavailable in the panel"
        ), ""

    ok, msg, client_obj = client.add_vless_reality_client(
        default_id, email=client_name
    )
    existed = (not ok) and (msg == "exists")
    if existed:
        ok = True

    # 3x-ui applies email uniqueness **globally across the panel**.
    # After Variant A (clone inbound) and older tests, a client with the
    # same canonical name may still sit in some other inbound —
    # `add_vless_reality_client` then returns "Duplicate email". Do an
    # active sweep: walk every inbound except the default, delete all
    # matches with this name, and retry add. This is both automatic
    # migration from Variant A and self-heal from any other duplicates.
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
            # Retry add — the email is now free.
            ok, msg, client_obj = client.add_vless_reality_client(
                default_id, email=client_name
            )
            if (not ok) and msg == "exists":
                ok = True
                existed = True

    if not ok:
        return False, f"addClient: {msg}", ""

    # Re-read the inbound so Reality fields are up to date.
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
    """Cached port of the bot-managed VLESS inbound — for firewall hints
    in handlers. Does not talk to the panel."""
    cfg = load_config()
    try:
        p = int(cfg.get("bot_inbound_port") or 0)
    except (TypeError, ValueError):
        return None
    return p if p else None


def consume_just_created_flag() -> bool:
    """One-time flag "bot inbound just created". Returns True exactly
    once after creation, then always False — so the admin gets the
    firewall hint at the moment of the first provision."""
    cfg = load_config()
    flag = bool(cfg.get("bot_inbound_just_created"))
    if flag:
        cfg["bot_inbound_just_created"] = False
        save_config(cfg)
    return flag


def find_named_client_uri(client_name: str) -> Tuple[bool, str, str]:
    """Read-only: find a client by email in the default (and legacy bot) inbound.

    Previously only the default inbound was searched, so `/profiles` said
    "none" even though a working client lived in `bot_inbound_id` or under a
    legacy name (passed in a separate call). Makes no changes in the panel.

    Returns (exists, message, uri).
    """
    return find_named_client_uri_any([client_name])[:3]


def find_named_client_uri_any(
    client_names: List[str],
) -> Tuple[bool, str, str, str]:
    """Like ``find_named_client_uri``, but walks names and inbounds.

    Returns (exists, message, uri, matched_name).
    """
    names = [str(n).strip() for n in (client_names or []) if str(n).strip()]
    # unique, keep order
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
    """Remove the canonical client by name from the default inbound (where
    the admin's manual clients also live). The bot only touches the client
    with the exact given name — manual clients are left alone.

    Idempotent: if the client is missing — `(True, "not present")`.
    Plus migration: if a legacy `bot_inbound_id` from the old scheme exists
    and a same-named client is there — delete that too so duplicates are gone.
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

    # 1. Default inbound — the source of truth.
    found, client_obj = xclient.find_client(default_id, client_name)
    if found:
        uuid_val = str(client_obj.get("id") or "").strip()
        if uuid_val:
            ok_d, msg_d = xclient.del_client(default_id, uuid_val)
            if ok_d:
                removed_anywhere = True
            else:
                return False, msg_d

    # 2. Legacy bot_inbound (old scheme with a clone inbound on port+1).
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
    Restore an XUIClient from the saved config. Returns None if
    the config is incomplete or the password could not be decrypted.
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
    Save 3x-ui credentials (encrypting the password). Does NOT log in —
    the caller does that. Returns (ok, message).
    """
    if not encryption_available():
        return False, (
            "ENCRYPTION_KEY (or API_SECRET_KEY) is not set in .env. "
            "Without it the panel password cannot be encrypted."
        )
    try:
        enc = _encrypt_password(password)
    except EncryptionError as exc:
        return False, f"password encryption: {exc}"
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
        return False, "3x-ui integration is not configured (see /xui_setup)"
    cfg["default_inbound_id"] = int(inbound_id)
    return save_config(cfg)


def set_enabled(flag: bool) -> Tuple[bool, str]:
    cfg = load_config()
    if not is_configured() and flag:
        return False, "configure /xui_setup first"
    cfg["enabled"] = bool(flag)
    return save_config(cfg)


def clear_credentials() -> Tuple[bool, str]:
    """Fully reset xui_config.json (without deleting the file)."""
    return save_config(_empty_config())


def make_client_or_error() -> Tuple[Optional[XUIClient], str]:
    """Handy shortcut for commands: either a client, or a human-readable error."""
    cfg = load_config()
    if not is_configured():
        return None, "3x-ui integration is not configured. Run /xui_setup."
    if not cfg.get("enabled"):
        return None, "3x-ui integration is disabled. Enable it via /xui_enable."
    client = make_client_for_config(cfg)
    if client is None:
        return None, "failed to decrypt 3x-ui credentials — check ENCRYPTION_KEY."
    return client, ""
