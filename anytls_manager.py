# -*- coding: utf-8 -*-
"""
Модуль для управления AnyTLS конфигурацией.

AnyTLS — протокол, маскирующий VPN-трафик под обычные TLS-сессии.
Каждый клиент идентифицируется паролем (password).
Сервер (sing-box inbound) хранит массив users: [{password, name}].

Структура конфигурации:
{
    "enabled": false,
    "server": "IP или домен VPS",
    "port": 443,
    "sni": "",
    "insecure": false,
    "tls_cert_path": "/etc/anytls/server.crt",
    "tls_key_path": "/etc/anytls/server.key",
    "clients": []
}
"""

import json
import os
import secrets
import subprocess
import logging
import threading
import urllib.parse
from io import BytesIO
from typing import Dict, Optional, List, Tuple
from datetime import datetime

from profile_names import visible_profile_name

logger = logging.getLogger(__name__)

from host_utils import host_run as _host_run

_anytls_lock = threading.Lock()

_ANYTLS_CONFIG_PATH = os.getenv("ANYTLS_CONFIG_PATH",
                                os.path.join(os.getcwd(), "anytls_config.json"))

RECOMMENDED_PORTS = [443, 8443, 4443]

DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",
    "port": 443,
    "sni": "",
    "insecure": False,
    "tls_cert_path": "/etc/anytls/server.crt",
    "tls_key_path": "/etc/anytls/server.key",
    "clients": [],
    "created_at": None,
    "updated_at": None,
}


def _generate_password(length: int = 16) -> str:
    return secrets.token_urlsafe(length)


def _load_config() -> Dict:
    with _anytls_lock:
        if not os.path.exists(_ANYTLS_CONFIG_PATH):
            return dict(DEFAULT_CONFIG)
        try:
            with open(_ANYTLS_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = dict(DEFAULT_CONFIG)
                config.update(data)
                return config
        except Exception as e:
            logger.error(f"Error loading AnyTLS config: {e}")
            return dict(DEFAULT_CONFIG)


def _save_config(config: Dict) -> bool:
    with _anytls_lock:
        try:
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]

            directory = os.path.dirname(_ANYTLS_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)

            with open(_ANYTLS_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            return True
        except Exception as e:
            logger.error(f"Error saving AnyTLS config: {e}")
            return False


# === Public API ===

def is_enabled() -> bool:
    config = _load_config()
    return config.get("enabled", False)


def enable() -> Tuple[bool, str]:
    config = _load_config()
    if not config.get("server"):
        return False, "❌ Не настроен сервер"
    if not config.get("clients"):
        return False, "❌ Нет клиентов. Сначала: /anytls_add <имя>"

    config["enabled"] = True
    if _save_config(config):
        return True, "✅ AnyTLS включён"
    return False, "❌ Ошибка при сохранении"


def disable() -> Tuple[bool, str]:
    config = _load_config()
    config["enabled"] = False
    if _save_config(config):
        return True, "🔴 AnyTLS выключен"
    return False, "❌ Ошибка при сохранении"


def get_status() -> Dict:
    config = _load_config()
    return {
        "enabled": config.get("enabled", False),
        "configured": bool(config.get("server") and config.get("clients")),
        "server": config.get("server", ""),
        "port": config.get("port", 443),
        "sni": config.get("sni", ""),
        "insecure": config.get("insecure", False),
        "clients_count": len(config.get("clients", [])),
        "updated_at": config.get("updated_at"),
    }


def get_config(include_secrets: bool = False) -> Dict:
    config = _load_config()
    if not include_secrets:
        for client in config.get("clients", []):
            if client.get("password"):
                cpw = client["password"]
                client["password"] = f"{cpw[:4]}..." if len(cpw) > 4 else "***"
    return config


# === Server IP ===

def get_server_public_ip() -> Optional[str]:
    import urllib.request

    ip_services = [
        "https://api.ipify.org",
        "https://ipinfo.io/ip",
        "https://icanhazip.com",
        "https://ifconfig.me/ip",
        "https://checkip.amazonaws.com",
    ]
    for service in ip_services:
        try:
            with urllib.request.urlopen(service, timeout=5) as response:
                ip = response.read().decode('utf-8').strip()
                parts = ip.split('.')
                if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                    return ip
        except Exception:
            continue

    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith(('10.', '172.', '192.168.', '127.')):
            return ip
    except Exception:
        pass
    return None


# === Setters ===

def set_server(server: Optional[str] = None) -> Tuple[bool, str]:
    if not server or not server.strip():
        detected_ip = get_server_public_ip()
        if detected_ip:
            server = detected_ip
            auto_detected = True
        else:
            return False, "❌ Не удалось определить IP. Укажите: /anytls_set_server <IP>"
    else:
        auto_detected = False

    config = _load_config()
    config["server"] = server.strip()
    if _save_config(config):
        prefix = "автоматически: " if auto_detected else ""
        return True, f"✅ Сервер установлен {prefix}{server}"
    return False, "❌ Ошибка при сохранении"


def set_port(port: int) -> Tuple[bool, str]:
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False, "❌ Порт должен быть числом от 1 до 65535"

    config = _load_config()
    config["port"] = port
    recommended = "⭐" if port in RECOMMENDED_PORTS else ""
    if _save_config(config):
        return True, f"✅ Порт: {port} {recommended}\n⚠️ Откройте TCP порт: `ufw allow {port}/tcp`"
    return False, "❌ Ошибка при сохранении"


def set_sni(sni: str) -> Tuple[bool, str]:
    config = _load_config()
    config["sni"] = sni.strip() if sni else ""
    if _save_config(config):
        return True, f"✅ SNI: {config['sni'] or '(пусто)'}"
    return False, "❌ Ошибка при сохранении"


def set_insecure(insecure: bool) -> Tuple[bool, str]:
    config = _load_config()
    config["insecure"] = insecure
    if _save_config(config):
        status = "включён ⚠️" if insecure else "выключен ✅"
        return True, f"✅ Insecure mode: {status}"
    return False, "❌ Ошибка при сохранении"


# === TLS ===

def generate_self_signed_cert(
    cert_path: str = "/etc/anytls/server.crt",
    key_path: str = "/etc/anytls/server.key",
    domain: str = "www.microsoft.com",
    days: int = 36500,
) -> Tuple[bool, str]:
    cert_dir = os.path.dirname(cert_path) or "."
    try:
        os.makedirs(cert_dir, exist_ok=True)
    except Exception as e:
        return False, f"❌ Не удалось создать директорию: {e}"

    try:
        cmd = [
            "openssl", "req", "-x509", "-nodes",
            "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-keyout", key_path,
            "-out", cert_path,
            "-subj", f"/CN={domain}",
            "-days", str(days),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            config = _load_config()
            config["tls_cert_path"] = cert_path
            config["tls_key_path"] = key_path
            _save_config(config)
            return True, f"✅ Сертификат сгенерирован\n📄 `{cert_path}`\n🔑 `{key_path}`"
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"openssl failed: {e}")

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend
        import datetime as dt

        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, domain),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.utcnow())
            .not_valid_after(dt.datetime.utcnow() + dt.timedelta(days=days))
            .sign(key, hashes.SHA256(), default_backend())
        )
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ))
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        config = _load_config()
        config["tls_cert_path"] = cert_path
        config["tls_key_path"] = key_path
        _save_config(config)
        return True, f"✅ Сертификат сгенерирован (Python)\n📄 `{cert_path}`\n🔑 `{key_path}`"
    except ImportError:
        return False, "❌ Установите openssl или: `pip install cryptography`"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def generate_all() -> Tuple[bool, Dict, str]:
    results = {}
    messages = []

    success_srv, msg_srv = set_server(None)
    messages.append(msg_srv)
    if success_srv:
        config = _load_config()
        results["server"] = config.get("server", "")

    success_cert, msg_cert = generate_self_signed_cert()
    results["cert_generated"] = success_cert
    messages.append(msg_cert)

    success_client, msg_client, client = add_client("default")
    results["default_client"] = client
    messages.append(msg_client)

    overall = success_srv and success_cert and success_client
    return overall, results, "\n".join(messages)


# === Clients ===

def list_clients() -> List[Dict]:
    config = _load_config()
    return config.get("clients", [])


def add_client(name: str, client_password: Optional[str] = None) -> Tuple[bool, str, Dict]:
    if not name or not name.strip():
        return False, "❌ Имя клиента не может быть пустым", {}

    name = name.strip()
    config = _load_config()

    for client in config.get("clients", []):
        if client.get("name") == name:
            return False, f"❌ Клиент {name} уже существует", {}

    if not client_password:
        client_password = _generate_password()

    client = {
        "name": name,
        "password": client_password,
        "created_at": datetime.now().isoformat(),
    }

    config.setdefault("clients", []).append(client)
    if _save_config(config):
        return True, f"✅ Клиент добавлен: {name}", client
    return False, "❌ Ошибка при сохранении", {}


def remove_client(name: str) -> Tuple[bool, str]:
    if not name or not name.strip():
        return False, "❌ Укажите имя клиента"

    name = name.strip()
    config = _load_config()
    clients = config.get("clients", [])
    new_clients = [c for c in clients if c.get("name") != name]

    if len(new_clients) == len(clients):
        return False, "❌ Клиент не найден"

    config["clients"] = new_clients
    if _save_config(config):
        return True, f"✅ Клиент {name} удалён"
    return False, "❌ Ошибка при сохранении"


def get_client(name: str) -> Optional[Dict]:
    if not name or not name.strip():
        return None
    needle = name.strip()
    for client in list_clients():
        if client.get("name") == needle:
            return client
    return None


# === URI / QR ===

def generate_anytls_uri(
    client_password: str,
    comment: str = "AnyTLS",
) -> str:
    """
    URI формат: anytls://password@server:port?sni=xxx&insecure=1#comment
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 443)

    if not server or not client_password:
        return ""

    params = {}
    sni = config.get("sni", "")
    if sni:
        params["sni"] = sni
    if config.get("insecure"):
        params["insecure"] = "1"

    query_string = "&".join(
        f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()
    )
    comment_enc = urllib.parse.quote(comment)

    uri = f"anytls://{urllib.parse.quote(client_password)}@{server}:{port}"
    if query_string:
        uri += f"?{query_string}"
    uri += f"#{comment_enc}"
    return uri


def generate_client_uri(name: str) -> Tuple[bool, str, str]:
    client = get_client(name)
    if not client:
        return False, "❌ Клиент не найден", ""

    client_password = client.get("password", "")
    if not client_password:
        return False, f"❌ У клиента {name} нет пароля", ""

    uri = generate_anytls_uri(
        client_password,
        visible_profile_name("AnyTLS", _load_config().get("server", ""), name),
    )
    if not uri:
        return False, "❌ Не удалось сгенерировать URI. Проверьте настройки сервера", ""

    return True, f"✅ URI для клиента {name} готов", uri


def generate_qr_png_bytes(content: str) -> Tuple[bool, Optional[BytesIO], str]:
    if not content or not content.strip():
        return False, None, "❌ Нечего кодировать в QR"

    try:
        import qrcode
    except ImportError:
        return False, None, "❌ Библиотека qrcode не установлена"

    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(content.strip())
        qr.make(fit=True)

        image = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return True, buffer, "✅ QR-код сгенерирован"
    except Exception as e:
        logger.error(f"Failed to generate AnyTLS QR: {e}")
        return False, None, f"❌ Ошибка: {e}"


def build_client_qr_payload(name: str) -> Tuple[bool, str, Dict]:
    client = get_client(name)
    if not client:
        return False, "❌ Клиент не найден", {}

    success, message, uri = generate_client_uri(name)
    if not success:
        return False, message, {}

    success, qr_buffer, qr_message = generate_qr_png_bytes(uri)
    if not success or qr_buffer is None:
        return False, qr_message, {}

    payload = {
        "name": client.get("name", ""),
        "password": client.get("password", ""),
        "uri": uri,
        "qr_buffer": qr_buffer,
    }
    return True, "✅ QR-пакет для AnyTLS подготовлен", payload


# === Export Configurations ===

def export_server_config() -> Dict:
    """
    Серверная конфигурация sing-box inbound для AnyTLS.
    """
    config = _load_config()
    clients = config.get("clients", [])

    users = []
    for c in clients:
        p = c.get("password", "")
        n = c.get("name", "")
        if p:
            users.append({"password": p, "name": n})

    inbound = {
        "type": "anytls",
        "tag": "anytls-in",
        "listen": "::",
        "listen_port": config.get("port", 443),
        "users": users,
        "tls": {
            "enabled": True,
            "certificate_path": config.get("tls_cert_path", "/etc/anytls/server.crt"),
            "key_path": config.get("tls_key_path", "/etc/anytls/server.key"),
        },
    }
    return inbound


def export_server_singbox_config() -> Dict:
    inbound = export_server_config()
    return {
        "log": {"level": "info"},
        "inbounds": [inbound],
        "outbounds": [
            {"type": "direct", "tag": "direct"},
        ],
    }


def export_server_config_json() -> str:
    return json.dumps(export_server_singbox_config(), indent=2, ensure_ascii=False)


def export_singbox_config(client_name: Optional[str] = None) -> Dict:
    """
    Клиентская конфигурация sing-box с AnyTLS outbound.
    """
    config = _load_config()

    client_password = ""
    if client_name:
        client = next(
            (c for c in config.get("clients", []) if c.get("name") == client_name),
            None,
        )
        if client:
            client_password = client.get("password", "")

    if not client_password:
        clients = config.get("clients", [])
        if clients:
            client_password = clients[0].get("password", "")

    outbound = {
        "type": "anytls",
        "tag": "proxy",
        "server": config.get("server", ""),
        "server_port": config.get("port", 443),
        "password": client_password,
        "tls": {
            "enabled": True,
            "server_name": config.get("sni", "") or config.get("server", ""),
            "insecure": config.get("insecure", False),
        },
    }

    return {
        "log": {"level": "warn"},
        "inbounds": [{
            "type": "socks",
            "listen": "127.0.0.1",
            "listen_port": 1080,
        }],
        "outbounds": [outbound],
    }


def export_clash_meta_config(client_name: Optional[str] = None) -> str:
    """
    Клиентская конфигурация Clash Meta (YAML).
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 443)
    sni = config.get("sni", "")
    insecure = config.get("insecure", False)

    client_password = ""
    if client_name:
        client = next(
            (c for c in config.get("clients", []) if c.get("name") == client_name),
            None,
        )
        if client:
            client_password = client.get("password", "")

    if not client_password:
        clients = config.get("clients", [])
        if clients:
            client_password = clients[0].get("password", "")

    lines = [
        "port: 7890",
        "socks-port: 7891",
        "mixed-port: 7892",
        "mode: rule",
        "log-level: info",
        "",
        "proxies:",
        "  - name: anytls",
        "    type: anytls",
        f"    server: {server}",
        f"    port: {port}",
        f"    password: {client_password}",
    ]

    if sni:
        lines.append(f"    sni: {sni}")
    if insecure:
        lines.append("    skip-cert-verify: true")

    lines.extend([
        "",
        "proxy-groups:",
        "  - name: PROXY",
        "    type: select",
        "    proxies:",
        "      - anytls",
        "      - DIRECT",
        "",
        "rules:",
        "  - MATCH,PROXY",
    ])

    return "\n".join(lines)


def export_subscription_list() -> List[str]:
    links = []
    for client in list_clients():
        name = client.get("name", "client")
        p = client.get("password", "")
        if p:
            link = generate_anytls_uri(
                p,
                visible_profile_name("AnyTLS", _load_config().get("server", ""), name),
            )
            if link:
                links.append(link)
    return links


def export_subscription_base64() -> str:
    import base64
    links = export_subscription_list()
    raw = "\n".join([x for x in links if x]).strip()
    if not raw:
        return ""
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


# === Service Management ===

def apply_config(config_path: str = "/etc/anytls/config.json") -> Tuple[bool, str]:
    config_json = export_server_config_json()

    try:
        config_dir = os.path.dirname(config_path) or "."
        os.makedirs(config_dir, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_json)
            f.flush()
            os.fsync(f.fileno())

        result = _host_run(
            ["systemctl", "restart", "anytls-server"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, f"✅ Конфиг применён и сервис перезапущен\n📄 `{config_path}`"
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"⚠️ Конфиг записан, но сервис не перезапустился:\n`{error}`"
    except PermissionError:
        return False, f"❌ Нет прав на запись. Запустите с sudo."
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def service_control(action: str) -> Tuple[bool, str]:
    if action not in ("start", "stop", "restart", "status"):
        return False, f"❌ Неизвестное действие: {action}"

    try:
        result = _host_run(
            ["systemctl", action, "anytls-server"],
            capture_output=True, text=True, timeout=30,
        )
        if action == "status":
            output = result.stdout.strip() or result.stderr.strip()
            is_active = "active (running)" in output
            emoji = "🟢" if is_active else "🔴"
            return True, f"{emoji} AnyTLS сервис:\n```\n{output[:500]}\n```"

        if result.returncode == 0:
            labels = {"start": "запущен", "stop": "остановлен", "restart": "перезапущен"}
            return True, f"✅ AnyTLS {labels.get(action, action)}"
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"❌ Ошибка: {error[:300]}"
    except FileNotFoundError:
        return False, "❌ systemctl не найден"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def get_logs(lines: int = 30) -> Tuple[bool, str]:
    try:
        result = _host_run(
            ["journalctl", "-u", "anytls-server", "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, timeout=15,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(пусто)"
        if len(output) > 3500:
            output = output[-3500:]
        return True, output
    except FileNotFoundError:
        return False, "❌ journalctl не найден"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"
