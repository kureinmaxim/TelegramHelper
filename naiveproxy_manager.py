# -*- coding: utf-8 -*-
"""
NaiveProxy server manager backed by a local JSON config.

This module keeps TelegramHelper transport management consistent with existing
VLESS/Hysteria2 managers while targeting a Caddy + forwardproxy@naive server.
"""

import json
import logging
import os
import secrets
import string
import subprocess
import threading
from datetime import datetime
from typing import Dict, Tuple
from urllib.parse import urlparse

from profile_names import visible_profile_name

logger = logging.getLogger(__name__)

_naive_lock = threading.Lock()
_NAIVE_CONFIG_PATH = os.getenv(
    "NAIVEPROXY_CONFIG_PATH",
    os.path.join(os.getcwd(), "naiveproxy_config.json"),
)

DEFAULT_CONFIG = {
    "enabled": False,
    "domain": "",
    "server": "",
    "port": 443,
    "username": "",
    "password": "",
    "scheme": "https",
    "local_socks_port": 10808,
    "padding": True,
    "probe_resistance": True,
    "hide_ip": True,
    "hide_via": True,
    "camouflage_url": "",
    "caddyfile_path": "/etc/caddy-naive/Caddyfile",
    "service_name": "caddy-naive",
    "created_at": None,
    "updated_at": None,
}


def _load_config() -> Dict:
    with _naive_lock:
        if not os.path.exists(_NAIVE_CONFIG_PATH):
            return dict(DEFAULT_CONFIG)
        try:
            with open(_NAIVE_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            config = dict(DEFAULT_CONFIG)
            config.update(data)
            return config
        except Exception as exc:
            logger.error("Error loading NaiveProxy config: %s", exc)
            return dict(DEFAULT_CONFIG)


def _save_config(config: Dict) -> bool:
    with _naive_lock:
        try:
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]
            directory = os.path.dirname(_NAIVE_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)
            with open(_NAIVE_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            return True
        except Exception as exc:
            logger.error("Error saving NaiveProxy config: %s", exc)
            return False


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-2:]}"


def _random_string(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _parse_bool(value: str) -> bool:
    clean = str(value).strip().lower()
    if clean in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if clean in {"0", "false", "no", "off", "disable", "disabled"}:
        return False
    raise ValueError("value must be on/off, true/false, or 1/0")


def _run_systemctl(*args: str) -> Tuple[bool, str]:
    service_name = _load_config().get("service_name", "caddy-naive")
    cmd = ["systemctl", *args, service_name]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return False, "systemctl not found on this host"
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def is_enabled() -> bool:
    return bool(_load_config().get("enabled", False))


def enable() -> Tuple[bool, str]:
    config = _load_config()
    required = ["domain", "username", "password"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        return False, f"Required parameters are not set: {', '.join(missing)}"
    config["enabled"] = True
    if _save_config(config):
        return True, "✅ NaiveProxy enabled"
    return False, "❌ Failed to save configuration"


def disable() -> Tuple[bool, str]:
    config = _load_config()
    config["enabled"] = False
    if _save_config(config):
        return True, "🔴 NaiveProxy disabled"
    return False, "❌ Failed to save configuration"


def get_status() -> Dict:
    config = _load_config()
    systemd_ok, systemd_output = _run_systemctl("is-active")
    configured = all(config.get(key) for key in ("domain", "username", "password"))
    return {
        "enabled": config.get("enabled", False),
        "configured": configured,
        "domain": config.get("domain", ""),
        "server": config.get("server") or config.get("domain", ""),
        "port": config.get("port", 443),
        "username": config.get("username", ""),
        "scheme": config.get("scheme", "https"),
        "local_socks_port": config.get("local_socks_port", 10808),
        "padding": config.get("padding", True),
        "probe_resistance": config.get("probe_resistance", True),
        "hide_ip": config.get("hide_ip", True),
        "hide_via": config.get("hide_via", True),
        "camouflage_url": config.get("camouflage_url", ""),
        "service_name": config.get("service_name", "caddy-naive"),
        "systemd_active": systemd_ok,
        "systemd_output": systemd_output or ("active" if systemd_ok else "inactive"),
        "updated_at": config.get("updated_at"),
    }


def get_config(include_secrets: bool = False) -> Dict:
    config = _load_config()
    if not include_secrets:
        config["password"] = _mask_secret(config.get("password", ""))
    return config


def set_domain(domain: str) -> Tuple[bool, str]:
    config = _load_config()
    clean = domain.strip()
    config["domain"] = clean
    if not config.get("server"):
        config["server"] = clean
    if _save_config(config):
        return True, f"✅ NaiveProxy domain set: {clean}"
    return False, "❌ Failed to save domain"


def set_server(server: str) -> Tuple[bool, str]:
    config = _load_config()
    config["server"] = server.strip()
    if _save_config(config):
        return True, f"✅ NaiveProxy server set: {config['server']}"
    return False, "❌ Failed to save server"


def set_port(port: int) -> Tuple[bool, str]:
    if port <= 0 or port > 65535:
        return False, "❌ Port must be in the range 1-65535"
    config = _load_config()
    config["port"] = int(port)
    if _save_config(config):
        return True, f"✅ NaiveProxy port set: {port}"
    return False, "❌ Failed to save port"


def set_username(username: str) -> Tuple[bool, str]:
    config = _load_config()
    config["username"] = username.strip()
    if _save_config(config):
        return True, f"✅ NaiveProxy username set: {config['username']}"
    return False, "❌ Failed to save username"


def set_password(password: str) -> Tuple[bool, str]:
    config = _load_config()
    config["password"] = password.strip()
    if _save_config(config):
        return True, "✅ NaiveProxy password updated"
    return False, "❌ Failed to save password"


def set_dpi_param(param: str, value: str) -> Tuple[bool, str]:
    key = param.strip().lower().replace("-", "_")
    raw = value.strip()
    config = _load_config()

    if key in {"scheme", "protocol", "transport"}:
        scheme = raw.lower()
        if scheme not in {"https", "quic"}:
            return False, "❌ scheme must be https or quic"
        config["scheme"] = scheme
        message = f"✅ NaiveProxy scheme set: {scheme}"
    elif key == "padding":
        try:
            enabled = _parse_bool(raw)
        except ValueError as exc:
            return False, f"❌ {exc}"
        config["padding"] = enabled
        message = f"✅ padding set: {enabled}"
    elif key in {"local_socks_port", "socks_port", "local_port"}:
        try:
            port = int(raw)
        except ValueError:
            return False, "❌ local_socks_port must be a number"
        if port <= 0 or port > 65535:
            return False, "❌ local_socks_port must be in the range 1-65535"
        config["local_socks_port"] = port
        message = f"✅ local_socks_port set: {port}"
    elif key == "probe_resistance":
        try:
            enabled = _parse_bool(raw)
        except ValueError as exc:
            return False, f"❌ {exc}"
        config["probe_resistance"] = enabled
        message = (
            f"✅ probe_resistance set: {enabled}\n"
            "Apply on the server: /naive_apply"
        )
    elif key == "hide_ip":
        try:
            enabled = _parse_bool(raw)
        except ValueError as exc:
            return False, f"❌ {exc}"
        config["hide_ip"] = enabled
        message = f"✅ hide_ip set: {enabled}\nApply on the server: /naive_apply"
    elif key == "hide_via":
        try:
            enabled = _parse_bool(raw)
        except ValueError as exc:
            return False, f"❌ {exc}"
        config["hide_via"] = enabled
        message = f"✅ hide_via set: {enabled}\nApply on the server: /naive_apply"
    elif key in {"camouflage_url", "camouflage", "reverse_proxy"}:
        if raw.lower() in {"", "off", "none", "disable", "disabled"}:
            config["camouflage_url"] = ""
            message = "✅ camouflage_url disabled\nApply on the server: /naive_apply"
        else:
            parsed = urlparse(raw)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return False, "❌ camouflage_url must be an http(s) URL or off"
            config["camouflage_url"] = raw
            message = (
                f"✅ camouflage_url set: {raw}\n"
                "Apply on the server: /naive_apply"
            )
    else:
        allowed = (
            "scheme, padding, local_socks_port, probe_resistance, "
            "hide_ip, hide_via, camouflage_url"
        )
        return False, f"❌ Unknown parameter. Available: {allowed}"

    if _save_config(config):
        return True, message
    return False, "❌ Failed to save DPI parameter"


def generate_credentials() -> Tuple[bool, str, Dict]:
    config = _load_config()
    config["username"] = config.get("username") or f"naive-{_random_string(6).lower()}"
    config["password"] = _random_string(24)
    if _save_config(config):
        return True, "✅ NaiveProxy credentials generated", {
            "username": config["username"],
            "password": config["password"],
        }
    return False, "❌ Failed to generate credentials", {}


def build_caddyfile() -> str:
    config = _load_config()
    domain = config.get("domain") or config.get("server")
    port = config.get("port", 443)
    username = config.get("username")
    password = config.get("password")
    if not domain or not username or not password:
        raise ValueError("NaiveProxy config requires domain, username and password")
    email_domain = domain if "." in domain else "localhost"
    forward_proxy_options = []
    if config.get("hide_ip", True):
        forward_proxy_options.append("        hide_ip")
    if config.get("hide_via", True):
        forward_proxy_options.append("        hide_via")
    if config.get("probe_resistance", True):
        forward_proxy_options.append("        probe_resistance")
    forward_proxy_body = "\n".join(forward_proxy_options)
    if forward_proxy_body:
        forward_proxy_body = "\n" + forward_proxy_body
    camouflage_url = (config.get("camouflage_url") or "").strip()
    camouflage_block = ""
    if camouflage_url:
        camouflage_block = f"""

    reverse_proxy {camouflage_url} {{
        header_up Host {{upstream_hostport}}
        header_up X-Forwarded-Host {{host}}
    }}"""
    return f"""{{
    email admin@{email_domain}
    order forward_proxy before file_server
    auto_https disable_redirects
}}

:{port}, {domain} {{
    log {{
        output stdout
        level INFO
    }}
    forward_proxy {{
        basic_auth {username} {password}{forward_proxy_body}
    }}{camouflage_block}
}}
"""


def write_caddyfile(path: str = None) -> Tuple[bool, str]:
    config = _load_config()
    target = path or config.get("caddyfile_path") or "/etc/caddy-naive/Caddyfile"
    try:
        content = build_caddyfile()
        directory = os.path.dirname(target) or "."
        os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return True, f"✅ Caddyfile written: {target}"
    except Exception as exc:
        logger.error("Error writing Caddyfile: %s", exc)
        return False, f"❌ Failed to write Caddyfile: {exc}"


def apply_server_config() -> Tuple[bool, str]:
    ok, message = write_caddyfile()
    if not ok:
        return False, message
    reload_ok, reload_output = _run_systemctl("restart")
    if reload_ok:
        return True, "✅ Caddy/NaiveProxy configuration applied"
    return False, f"❌ Failed to restart service: {reload_output}"


def install_naiveproxy() -> Tuple[bool, str]:
    config = _load_config()
    domain = config.get("domain") or config.get("server")
    if not domain:
        return False, "❌ Set the domain first via /naive_set_domain"
    script_path = os.path.join(os.path.dirname(__file__), "scripts", "install_naiveproxy.sh")
    if not os.path.exists(script_path):
        return False, f"❌ Install script not found: {script_path}"

    cmd = [
        "bash",
        script_path,
        "--domain",
        domain,
        "--port",
        str(config.get("port", 443)),
    ]
    if config.get("username"):
        cmd.extend(["--username", config["username"]])
    if config.get("password"):
        cmd.extend(["--password", config["password"]])
    if config.get("service_name"):
        cmd.extend(["--service-name", config["service_name"]])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        logger.error("Error installing NaiveProxy: %s", exc)
        return False, f"❌ Failed to run install_naiveproxy.sh: {exc}"

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode == 0:
        return True, output or "✅ NaiveProxy installed"
    return False, output or "❌ NaiveProxy installation failed"


def build_client_uri() -> str:
    config = _load_config()
    domain = config.get("domain") or config.get("server")
    username = config.get("username")
    password = config.get("password")
    port = config.get("port", 443)
    scheme = config.get("scheme", "https")
    if not domain or not username or not password:
        raise ValueError("NaiveProxy config requires domain, username and password")
    fragment = visible_profile_name("NaiveProxy", domain)
    return f"naive+{scheme}://{username}:{password}@{domain}:{port}#{fragment}"


def export_client_config() -> Dict:
    config = _load_config()
    return {
        "listen": f"socks://127.0.0.1:{config.get('local_socks_port', 10808)}",
        "proxy": f"{config.get('scheme', 'https')}://{config.get('username', '')}:{config.get('password', '')}@{config.get('domain') or config.get('server', '')}:{config.get('port', 443)}",
        "padding": bool(config.get("padding", True)),
    }


def export_aping_profile() -> str:
    config = _load_config()
    profile = {
        "format": "aping-naive-profile",
        "version": 1,
        "profile": {
            "name": visible_profile_name(
                "NaiveProxy",
                config.get("domain") or config.get("server", ""),
            ),
            "icon": "🌐",
            "color": "#0ea5e9",
            "protocol_type": "naiveproxy",
        },
        "naiveproxy": {
            "enabled": True,
            "server": config.get("domain") or config.get("server", ""),
            "port": config.get("port", 443),
            "username": config.get("username", ""),
            "password": config.get("password", ""),
            "scheme": config.get("scheme", "https"),
            "local_socks_port": config.get("local_socks_port", 10808),
            "padding": bool(config.get("padding", True)),
        },
    }
    return json.dumps(profile, ensure_ascii=False, indent=2)
