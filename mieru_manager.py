# -*- coding: utf-8 -*-
"""
Mieru server manager backed by a local JSON config.

Mieru (project enfein/mieru) состоит из двух частей:
- ``mita`` — серверная часть на VPS, управляется через systemd и `mita` CLI.
- ``mieru`` — клиентский SOCKS5/HTTP proxy.

Этот менеджер хранит state в ``mieru_config.json`` рядом с другими
*_config.json файлами проекта и генерирует серверный/клиентский config,
``mierus://`` URI и Clash/mihomo блок. Управление сервисом идёт через
``systemctl`` (host_run, чтобы работало из Docker с pid: host).
"""

import json
import logging
import os
import secrets
import string
import subprocess
import threading
import urllib.parse
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from host_utils import host_run as _host_run
from profile_names import visible_profile_name

logger = logging.getLogger(__name__)

_mieru_lock = threading.Lock()
_MIERU_CONFIG_PATH = os.getenv(
    "MIERU_CONFIG_PATH",
    os.path.join(os.getcwd(), "mieru_config.json"),
)

# Серверный JSON, который скармливается `mita apply config`.
_MIERU_SERVER_CONFIG_PATH = os.getenv(
    "MIERU_SERVER_CONFIG_PATH",
    "/etc/mieru/server_config.json",
)

MULTIPLEXING_LEVELS = {
    "off": "MULTIPLEXING_OFF",
    "low": "MULTIPLEXING_LOW",
    "middle": "MULTIPLEXING_MIDDLE",
    "high": "MULTIPLEXING_HIGH",
}

HANDSHAKE_MODES = {
    "standard": "HANDSHAKE_STANDARD",
    "no_wait": "HANDSHAKE_NO_WAIT",
}

LOGGING_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR"}
PROTOCOLS = {"TCP", "UDP"}

DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",
    "port_bindings": [
        {"port": 29999, "protocol": "TCP"},
    ],
    "mtu": 1400,
    "multiplexing": "MULTIPLEXING_LOW",
    "handshake_mode": "HANDSHAKE_STANDARD",
    "socks5_port": 10810,
    "http_proxy_port": None,
    "rpc_port": 8964,
    "logging_level": "INFO",
    "service_name": "mita",
    "clients": [],
    "created_at": None,
    "updated_at": None,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_config() -> Dict:
    with _mieru_lock:
        if not os.path.exists(_MIERU_CONFIG_PATH):
            return dict(DEFAULT_CONFIG)
        try:
            with open(_MIERU_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            config = dict(DEFAULT_CONFIG)
            config.update(data)
            return config
        except Exception as exc:
            logger.error("Error loading Mieru config: %s", exc)
            return dict(DEFAULT_CONFIG)


def _save_config(config: Dict) -> bool:
    with _mieru_lock:
        try:
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]
            directory = os.path.dirname(_MIERU_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)
            with open(_MIERU_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            return True
        except Exception as exc:
            logger.error("Error saving Mieru config: %s", exc)
            return False


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-2:]}"


def generate_password(length: int = 24) -> str:
    """Сгенерировать случайный пароль клиента (URL-safe ASCII)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _canonical_client_name(owner_id) -> str:
    """``Mieru_ID<first2>_<last2>`` из telegram_id владельца."""
    digits = "".join(ch for ch in str(owner_id or "") if ch.isdigit())
    if len(digits) < 2:
        digits = (digits + "00")[:2]
    first = digits[:2]
    last = digits[-2:]
    return f"Mieru_ID{first}_{last}"


# ---------------------------------------------------------------------------
# State / config
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    return bool(_load_config().get("enabled", False))


def enable() -> Tuple[bool, str]:
    config = _load_config()
    if not config.get("server"):
        return False, "❌ Не задан server. Используйте /mieru_set_server <ip>"
    if not config.get("port_bindings"):
        return False, "❌ Нет port_bindings. Используйте /mieru_set_port <port> [tcp|udp]"
    if not config.get("clients"):
        return False, "❌ Нет клиентов. Используйте /mieru_add_client <name>"
    config["enabled"] = True
    if _save_config(config):
        return True, "✅ Mieru включён"
    return False, "❌ Ошибка при сохранении"


def disable() -> Tuple[bool, str]:
    config = _load_config()
    config["enabled"] = False
    if _save_config(config):
        return True, "🔴 Mieru выключен"
    return False, "❌ Ошибка при сохранении"


def get_config(include_secrets: bool = False) -> Dict:
    config = _load_config()
    if not include_secrets:
        masked = []
        for client in config.get("clients", []):
            entry = dict(client)
            entry["password"] = _mask_secret(entry.get("password", ""))
            masked.append(entry)
        config["clients"] = masked
    return config


def get_status() -> Dict:
    config = _load_config()
    systemd_ok, systemd_output = _service_action("is-active")
    mita_ok, mita_output = _mita_cli("status")
    return {
        "enabled": config.get("enabled", False),
        "configured": bool(config.get("server") and config.get("clients")),
        "server": config.get("server", ""),
        "port_bindings": config.get("port_bindings", []),
        "mtu": config.get("mtu", 1400),
        "multiplexing": config.get("multiplexing", "MULTIPLEXING_LOW"),
        "handshake_mode": config.get("handshake_mode", "HANDSHAKE_STANDARD"),
        "socks5_port": config.get("socks5_port", 10810),
        "http_proxy_port": config.get("http_proxy_port"),
        "logging_level": config.get("logging_level", "INFO"),
        "service_name": config.get("service_name", "mita"),
        "clients_count": len(config.get("clients", [])),
        "systemd_active": systemd_ok,
        "systemd_output": systemd_output or ("active" if systemd_ok else "inactive"),
        "mita_status_ok": mita_ok,
        "mita_status_output": mita_output,
        "updated_at": config.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Setters
# ---------------------------------------------------------------------------


def set_server(value: str) -> Tuple[bool, str]:
    clean = (value or "").strip()
    if not clean:
        return False, "❌ Укажите ip или домен"
    config = _load_config()
    config["server"] = clean
    if _save_config(config):
        return True, f"✅ server установлен: {clean}"
    return False, "❌ Ошибка при сохранении"


def set_port(port: int, protocol: str = "TCP") -> Tuple[bool, str]:
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return False, "❌ port должен быть числом"
    if port_int < 1 or port_int > 65535:
        return False, "❌ port должен быть в диапазоне 1..65535"
    proto = (protocol or "TCP").upper()
    if proto not in PROTOCOLS:
        return False, "❌ protocol должен быть TCP или UDP"
    config = _load_config()
    config["port_bindings"] = [{"port": port_int, "protocol": proto}]
    if _save_config(config):
        return (
            True,
            f"✅ port {port_int}/{proto.lower()} установлен.\n"
            f"⚠️ Откройте firewall: `ufw allow {port_int}/{proto.lower()}`",
        )
    return False, "❌ Ошибка при сохранении"


def set_port_range(port_range: str, protocol: str = "TCP") -> Tuple[bool, str]:
    raw = (port_range or "").strip()
    if "-" not in raw:
        return False, "❌ port_range формат: <from>-<to>, например 20000-20010"
    try:
        lo_str, hi_str = raw.split("-", 1)
        lo = int(lo_str)
        hi = int(hi_str)
    except ValueError:
        return False, "❌ port_range должен быть числами from-to"
    if lo < 1 or hi > 65535 or lo > hi:
        return False, "❌ port_range вне допустимого диапазона"
    proto = (protocol or "TCP").upper()
    if proto not in PROTOCOLS:
        return False, "❌ protocol должен быть TCP или UDP"
    config = _load_config()
    config["port_bindings"] = [
        {"portRange": {"from": lo, "to": hi}, "protocol": proto}
    ]
    if _save_config(config):
        return True, (
            f"✅ port_range {lo}-{hi}/{proto.lower()} установлен.\n"
            f"⚠️ Откройте firewall на каждый порт диапазона."
        )
    return False, "❌ Ошибка при сохранении"


def set_mtu(value: int) -> Tuple[bool, str]:
    try:
        mtu = int(value)
    except (TypeError, ValueError):
        return False, "❌ mtu должен быть числом"
    if mtu < 1280 or mtu > 1500:
        return False, "❌ mtu должен быть в диапазоне 1280..1500"
    config = _load_config()
    config["mtu"] = mtu
    if _save_config(config):
        return True, f"✅ mtu установлен: {mtu}"
    return False, "❌ Ошибка при сохранении"


def set_multiplexing(level: str) -> Tuple[bool, str]:
    key = (level or "").strip().lower()
    if key in MULTIPLEXING_LEVELS:
        resolved = MULTIPLEXING_LEVELS[key]
    elif key.upper() in MULTIPLEXING_LEVELS.values():
        resolved = key.upper()
    else:
        return False, "❌ multiplexing должен быть off|low|middle|high"
    config = _load_config()
    config["multiplexing"] = resolved
    if _save_config(config):
        return True, f"✅ multiplexing установлен: {resolved}"
    return False, "❌ Ошибка при сохранении"


def set_handshake_mode(mode: str) -> Tuple[bool, str]:
    key = (mode or "").strip().lower()
    if key in HANDSHAKE_MODES:
        resolved = HANDSHAKE_MODES[key]
    elif key.upper() in HANDSHAKE_MODES.values():
        resolved = key.upper()
    else:
        return False, "❌ handshake_mode должен быть standard|no_wait"
    config = _load_config()
    config["handshake_mode"] = resolved
    if _save_config(config):
        return True, f"✅ handshake_mode установлен: {resolved}"
    return False, "❌ Ошибка при сохранении"


def set_socks5_port(port: int) -> Tuple[bool, str]:
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return False, "❌ socks5_port должен быть числом"
    if port_int < 1 or port_int > 65535:
        return False, "❌ socks5_port должен быть в диапазоне 1..65535"
    config = _load_config()
    config["socks5_port"] = port_int
    if _save_config(config):
        return True, f"✅ socks5_port установлен: {port_int}"
    return False, "❌ Ошибка при сохранении"


def set_logging_level(level: str) -> Tuple[bool, str]:
    resolved = (level or "").strip().upper()
    if resolved not in LOGGING_LEVELS:
        return False, "❌ logging_level должен быть DEBUG|INFO|WARN|ERROR"
    config = _load_config()
    config["logging_level"] = resolved
    if _save_config(config):
        return True, f"✅ logging_level установлен: {resolved}"
    return False, "❌ Ошибка при сохранении"


def set_dpi_param(param: str, value: str) -> Tuple[bool, str]:
    """Единая ручка для DPI-исследований: см. plan_Mieru.md §10 / MIERU_GUIDE.md §8."""
    key = (param or "").strip().lower().replace("-", "_")
    raw = (value or "").strip()
    if key in {"protocol", "transport"}:
        config = _load_config()
        proto = raw.upper()
        if proto not in PROTOCOLS:
            return False, "❌ protocol должен быть tcp или udp"
        bindings = config.get("port_bindings") or [{"port": 29999, "protocol": "TCP"}]
        new_bindings = []
        for binding in bindings:
            entry = dict(binding)
            entry["protocol"] = proto
            new_bindings.append(entry)
        config["port_bindings"] = new_bindings
        if _save_config(config):
            return True, f"✅ protocol установлен: {proto}"
        return False, "❌ Ошибка при сохранении"
    if key == "port":
        try:
            port_int = int(raw)
        except ValueError:
            return False, "❌ port должен быть числом"
        config = _load_config()
        proto = "TCP"
        if config.get("port_bindings"):
            proto = config["port_bindings"][0].get("protocol", "TCP")
        return set_port(port_int, proto)
    if key == "port_range":
        config = _load_config()
        proto = "TCP"
        if config.get("port_bindings"):
            proto = config["port_bindings"][0].get("protocol", "TCP")
        return set_port_range(raw, proto)
    if key == "mtu":
        try:
            return set_mtu(int(raw))
        except ValueError:
            return False, "❌ mtu должен быть числом"
    if key == "multiplexing":
        return set_multiplexing(raw)
    if key in {"handshake", "handshake_mode"}:
        return set_handshake_mode(raw)
    if key in {"socks5_port", "socks_port"}:
        try:
            return set_socks5_port(int(raw))
        except ValueError:
            return False, "❌ socks5_port должен быть числом"
    if key in {"logging", "logging_level", "log_level"}:
        return set_logging_level(raw)
    return False, (
        "❌ Неизвестный параметр. Доступно: protocol, port, port_range, "
        "mtu, multiplexing, handshake, socks5_port, logging"
    )


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def list_clients() -> List[Dict]:
    return list(_load_config().get("clients", []))


def get_client(name: str) -> Optional[Dict]:
    needle = (name or "").strip()
    if not needle:
        return None
    for client in list_clients():
        if client.get("name") == needle:
            return client
    return None


def add_client(name: str, owner_id=None, password: Optional[str] = None) -> Tuple[bool, str, Dict]:
    clean = (name or "").strip()
    if not clean:
        return False, "❌ Имя клиента не может быть пустым", {}
    config = _load_config()
    for client in config.get("clients", []):
        if client.get("name") == clean:
            return False, f"❌ Клиент {clean} уже существует", {}
    secret = (password or "").strip() or generate_password()
    client = {
        "name": clean,
        "password": secret,
        "owner_id": owner_id,
        "created_at": datetime.now().isoformat(),
    }
    config.setdefault("clients", []).append(client)
    if _save_config(config):
        return True, f"✅ Клиент добавлен: {clean}", client
    return False, "❌ Ошибка при сохранении", {}


def delete_client(name: str) -> Tuple[bool, str]:
    clean = (name or "").strip()
    if not clean:
        return False, "❌ Укажите имя клиента"
    config = _load_config()
    clients = config.get("clients", [])
    new_clients = [c for c in clients if c.get("name") != clean]
    if len(new_clients) == len(clients):
        return False, f"❌ Клиент {clean} не найден"
    config["clients"] = new_clients
    if _save_config(config):
        return True, f"✅ Клиент {clean} удалён"
    return False, "❌ Ошибка при сохранении"


# provision_manager / user-card ожидают унифицированный API:
# remove_client(name) -> (ok, msg) и generate_client_uri(name) -> (ok, msg, uri).
def remove_client(name: str) -> Tuple[bool, str]:
    return delete_client(name)


def generate_client_uri(name: str) -> Tuple[bool, str, str]:
    try:
        uri = build_simple_uri(name)
    except ValueError as exc:
        return False, f"❌ {exc}", ""
    return True, "✅ URI готов", uri


# ---------------------------------------------------------------------------
# Build configs / URIs
# ---------------------------------------------------------------------------


def build_server_config() -> Dict:
    """Серверный JSON для ``mita apply config``."""
    config = _load_config()
    users = []
    for client in config.get("clients", []):
        password = client.get("password", "")
        name = client.get("name", "")
        if name and password:
            users.append({"name": name, "password": password})
    return {
        "portBindings": list(config.get("port_bindings", [])),
        "users": users,
        "loggingLevel": config.get("logging_level", "INFO"),
        "mtu": int(config.get("mtu", 1400)),
    }


def build_client_config(name: str) -> Dict:
    """Клиентский JSON для ``mieru apply config``."""
    config = _load_config()
    client = get_client(name)
    if not client:
        raise ValueError(f"Клиент {name} не найден")
    server = config.get("server", "")
    if not server:
        raise ValueError("server не задан")
    profile_name = visible_profile_name("Mieru", server, client["name"])
    profile = {
        "profileName": profile_name,
        "user": {
            "name": client["name"],
            "password": client["password"],
        },
        "servers": [
            {
                "ipAddress": server,
                "domainName": "",
                "portBindings": list(config.get("port_bindings", [])),
            }
        ],
        "mtu": int(config.get("mtu", 1400)),
        "multiplexing": {"level": config.get("multiplexing", "MULTIPLEXING_LOW")},
        "handshakeMode": config.get("handshake_mode", "HANDSHAKE_STANDARD"),
    }
    payload = {
        "profiles": [profile],
        "activeProfile": profile["profileName"],
        "rpcPort": int(config.get("rpc_port", 8964)),
        "socks5Port": int(config.get("socks5_port", 10810)),
        "loggingLevel": config.get("logging_level", "INFO"),
        "socks5ListenLAN": False,
    }
    http_port = config.get("http_proxy_port")
    if http_port:
        payload["httpProxyPort"] = int(http_port)
    return payload


def build_simple_uri(name: str) -> str:
    """``mierus://`` ссылка для шаринга. Format:
    ``mierus://<user>:<password>@<server>:<port>?protocol=tcp&mtu=...&mux=...&handshake=...#<name>``
    """
    config = _load_config()
    client = get_client(name)
    if not client:
        raise ValueError(f"Клиент {name} не найден")
    server = config.get("server", "")
    if not server:
        raise ValueError("server не задан")
    bindings = config.get("port_bindings") or []
    if not bindings:
        raise ValueError("port_bindings не заданы")
    first = bindings[0]
    if "port" in first:
        port_repr = str(first["port"])
    elif "portRange" in first:
        rng = first["portRange"]
        port_repr = f"{rng.get('from')}-{rng.get('to')}"
    else:
        raise ValueError("неподдерживаемый port_binding")
    proto = first.get("protocol", "TCP").lower()
    mux = config.get("multiplexing", "MULTIPLEXING_LOW").replace("MULTIPLEXING_", "").lower()
    handshake = config.get("handshake_mode", "HANDSHAKE_STANDARD").replace("HANDSHAKE_", "").lower()
    params = {
        "protocol": proto,
        "mtu": str(config.get("mtu", 1400)),
        "mux": mux,
        "handshake": handshake,
    }
    query = urllib.parse.urlencode(params)
    user_enc = urllib.parse.quote(client["name"], safe="")
    pass_enc = urllib.parse.quote(client["password"], safe="")
    fragment = urllib.parse.quote(
        visible_profile_name("Mieru", server, client["name"]),
        safe="",
    )
    return f"mierus://{user_enc}:{pass_enc}@{server}:{port_repr}?{query}#{fragment}"


def export_client_config(name: str) -> str:
    """JSON клиентского config как текст."""
    return json.dumps(build_client_config(name), ensure_ascii=False, indent=2)


def export_clash_block(name: str) -> str:
    """Минимальный mihomo/Clash YAML блок (proxy + simple group/rules)."""
    config = _load_config()
    client = get_client(name)
    if not client:
        raise ValueError(f"Клиент {name} не найден")
    server = config.get("server", "")
    if not server:
        raise ValueError("server не задан")
    bindings = config.get("port_bindings") or []
    if not bindings or "port" not in bindings[0]:
        raise ValueError("Clash блок требует фиксированный port (не port_range)")
    port = bindings[0]["port"]
    mux = config.get("multiplexing", "MULTIPLEXING_LOW").replace("MULTIPLEXING_", "").lower()
    proto = bindings[0].get("protocol", "TCP").lower()
    lines = [
        "proxies:",
        f"  - name: mieru-{client['name']}",
        "    type: mieru",
        f"    server: {server}",
        f"    port: {port}",
        f"    transport: {proto}",
        f"    username: {client['name']}",
        f"    password: {client['password']}",
        f"    multiplexing: {mux}",
        "",
        "proxy-groups:",
        "  - name: PROXY",
        "    type: select",
        "    proxies:",
        f"      - mieru-{client['name']}",
        "      - DIRECT",
        "",
        "rules:",
        "  - MATCH,PROXY",
    ]
    return "\n".join(lines)


def export_aping_profile(name: str) -> str:
    """Unified ``aping-profile`` v1 для Clash Meta desktop import.

    Соответствует ``ApiXExportProfile`` из Clash Meta ``shared-rs/src/app_config.rs``:
    desktop принимает только ``format == "apix-profile"`` или
    ``format == "aping-profile"``. Любая custom-форма (например, прежний
    ``aping-mieru-profile``) валидацией ``ApiXExportProfile::validate``
    отвергается.

    Структура секции ``mieru`` совпадает с ``ApiXMieruExport``: 10 полей,
    `port_bindings` сохраняет оба варианта (`port` и `portRange`).
    `server.host`/`server.port` — общие, `server.port` берётся из первого
    `port_binding` (с fallback на `port_range.from`).
    """
    config = _load_config()
    client = get_client(name)
    if not client:
        raise ValueError(f"Клиент {name} не найден")
    server = config.get("server", "")
    if not server:
        raise ValueError("server не задан")
    bindings = list(config.get("port_bindings") or [])
    if not bindings:
        raise ValueError("port_bindings не заданы")

    first = bindings[0]
    if "port" in first and first.get("port"):
        server_port = int(first["port"])
    elif "portRange" in first and first.get("portRange"):
        server_port = int(first["portRange"].get("from") or 29999)
    else:
        server_port = 29999

    profile_name = visible_profile_name("Mieru", server, client["name"])
    profile_id = f"mieru-{client['name']}-{int(datetime.now().timestamp())}"
    payload = {
        "format": "aping-profile",
        "version": 1,
        "source": {
            "app": "TelegramHelper",
            "exported_at": datetime.now().isoformat(),
        },
        "profile": {
            "id": profile_id,
            "name": profile_name,
            "icon": "🛰",
            "color": "#7c3aed",
            "protocol_type": "mieru",
            "is_default": False,
        },
        "server": {
            "host": server,
            "port": server_port,
        },
        "vpn_mode": "auto",
        # Прочие протокольные секции остаются null — они optional в
        # ApiXExportProfile (#[serde(default)]).
        "reality": None,
        "naiveproxy": None,
        "hysteria2": None,
        "tuic": None,
        "anytls": None,
        "xhttp": None,
        "mieru": {
            "enabled": True,
            "username": client["name"],
            "password": client["password"],
            "port_bindings": bindings,
            "mtu": int(config.get("mtu", 1400)),
            "multiplexing": config.get("multiplexing", "MULTIPLEXING_LOW"),
            "handshake_mode": config.get("handshake_mode", "HANDSHAKE_STANDARD"),
            "socks5_port": int(config.get("socks5_port", 10810)),
            "rpc_port": int(config.get("rpc_port", 8964)),
            "logging_level": config.get("logging_level", "INFO"),
        },
        "meta": {
            "subscription_name": profile_name,
            "server_label": profile_name,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Service control (mita)
# ---------------------------------------------------------------------------


def _service_action(action: str) -> Tuple[bool, str]:
    """systemctl <action> mita через host_run (Docker pid:host совместимо)."""
    service = _load_config().get("service_name", "mita")
    cmd = ["systemctl", action, service]
    try:
        result = _host_run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return False, "systemctl не найден на хосте"
    except Exception as exc:
        return False, f"Ошибка systemctl: {exc}"
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def _mita_cli(*args: str) -> Tuple[bool, str]:
    """Запуск ``mita ...`` через host_run."""
    cmd = ["mita", *args]
    try:
        result = _host_run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return False, "mita CLI не найден (установите через /mieru_install)"
    except Exception as exc:
        return False, f"Ошибка mita: {exc}"
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def _write_server_config_file() -> Tuple[bool, str, str]:
    """Сериализовать build_server_config() в файл на host. Возвращает (ok, msg, path)."""
    payload = build_server_config()
    if not payload.get("users"):
        return False, "❌ Нет clients — нечего применять", ""
    if not payload.get("portBindings"):
        return False, "❌ Нет port_bindings", ""
    target = _MIERU_SERVER_CONFIG_PATH
    try:
        directory = os.path.dirname(target) or "."
        os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
    except PermissionError:
        return False, f"❌ Нет прав на запись {target}. Запустите бот с доступом к /etc/mieru.", ""
    except Exception as exc:
        return False, f"❌ Ошибка записи {target}: {exc}", ""
    return True, f"✅ server_config записан: {target}", target


def apply_server_config(reload_only: bool = False) -> Tuple[bool, str]:
    """Записать server_config и применить через ``mita apply config`` + restart/reload.

    ``reload_only=True`` использует ``mita reload`` — допустимо только для
    изменений users/loggingLevel. Для port/MTU нужен полный restart.
    """
    ok, msg, path = _write_server_config_file()
    if not ok:
        return False, msg
    apply_ok, apply_out = _mita_cli("apply", "config", path)
    if not apply_ok:
        return False, f"❌ mita apply config: {apply_out}"
    if reload_only:
        reload_ok, reload_out = _mita_cli("reload")
        if reload_ok:
            return True, f"{msg}\n✅ mita reload OK"
        return False, f"{msg}\n❌ mita reload: {reload_out}"
    restart_ok, restart_out = _service_action("restart")
    if restart_ok:
        return True, f"{msg}\n✅ mita сервис перезапущен"
    return False, f"{msg}\n❌ Не удалось перезапустить mita: {restart_out}"


def install_mieru() -> Tuple[bool, str]:
    """Запуск scripts/install_mieru.sh."""
    script_path = os.path.join(os.path.dirname(__file__), "scripts", "install_mieru.sh")
    if not os.path.exists(script_path):
        return False, f"❌ Скрипт не найден: {script_path}"
    try:
        result = _host_run(
            ["bash", script_path],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:
        return False, f"❌ Ошибка запуска install_mieru.sh: {exc}"
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode == 0:
        return True, output or "✅ Mieru (mita) установлен"
    return False, output or "❌ Установка завершилась с ошибкой"


def start() -> Tuple[bool, str]:
    ok, output = _service_action("start")
    if ok:
        return True, "✅ mita запущен"
    return False, f"❌ {output or 'не удалось запустить mita'}"


def stop() -> Tuple[bool, str]:
    ok, output = _service_action("stop")
    if ok:
        return True, "🔴 mita остановлен"
    return False, f"❌ {output or 'не удалось остановить mita'}"


def restart() -> Tuple[bool, str]:
    ok, output = _service_action("restart")
    if ok:
        return True, "♻️ mita перезапущен"
    return False, f"❌ {output or 'не удалось перезапустить mita'}"


def logs(lines: int = 80) -> Tuple[bool, str]:
    service = _load_config().get("service_name", "mita")
    try:
        n = max(1, int(lines))
    except (TypeError, ValueError):
        n = 80
    try:
        result = _host_run(
            ["journalctl", "-u", service, "-n", str(n), "--no-pager"],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        return False, "❌ journalctl не найден на хосте"
    except Exception as exc:
        return False, f"❌ Ошибка journalctl: {exc}"
    output = (result.stdout or result.stderr or "").strip() or "(пусто)"
    if len(output) > 3500:
        output = output[-3500:]
    return result.returncode == 0, output
