# -*- coding: utf-8 -*-
"""
Модуль для управления TUIC конфигурацией.

TUIC v5 — высокопроизводительный прокси на основе QUIC.
Каждый клиент идентифицируется парой uuid + password.
Сервер (sing-box inbound) хранит массив users: [{uuid, password, name}].

Структура конфигурации:
{
    "enabled": false,
    "server": "IP или домен VPS",
    "port": 443,
    "sni": "",
    "insecure": false,
    "congestion_control": "bbr",
    "udp_relay_mode": "native",
    "alpn": ["h3"],
    "tls_cert_path": "/etc/tuic/server.crt",
    "tls_key_path": "/etc/tuic/server.key",
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
import uuid as _uuid_mod
from io import BytesIO
from typing import Dict, Optional, List, Tuple
from datetime import datetime

from profile_names import visible_profile_name

logger = logging.getLogger(__name__)

from host_utils import host_run as _host_run

_tuic_lock = threading.Lock()

_TUIC_CONFIG_PATH = os.getenv("TUIC_CONFIG_PATH",
                              os.path.join(os.getcwd(), "tuic_config.json"))

RECOMMENDED_PORTS = [443, 8443, 4443, 10443]

CONGESTION_CONTROLS = ["bbr", "cubic", "new_reno"]
UDP_RELAY_MODES = ["native", "quic"]
ALPN_OPTIONS = ["h3", "h3-29"]

DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",
    "port": 443,
    "sni": "",
    "insecure": False,
    "congestion_control": "bbr",
    "udp_relay_mode": "native",
    "alpn": ["h3"],
    "tls_cert_path": "/etc/tuic/server.crt",
    "tls_key_path": "/etc/tuic/server.key",
    "clients": [],
    "created_at": None,
    "updated_at": None,
}


def _generate_uuid() -> str:
    return str(_uuid_mod.uuid4())


def _generate_password(length: int = 16) -> str:
    return secrets.token_urlsafe(length)


def _load_config() -> Dict:
    """Загрузить конфигурацию TUIC из файла."""
    with _tuic_lock:
        if not os.path.exists(_TUIC_CONFIG_PATH):
            return dict(DEFAULT_CONFIG)

        try:
            with open(_TUIC_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = dict(DEFAULT_CONFIG)
                config.update(data)
                return config
        except Exception as e:
            logger.error(f"Error loading TUIC config: {e}")
            return dict(DEFAULT_CONFIG)


def _save_config(config: Dict) -> bool:
    """Сохранить конфигурацию TUIC в файл."""
    with _tuic_lock:
        try:
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]

            directory = os.path.dirname(_TUIC_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)

            with open(_TUIC_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            return True
        except Exception as e:
            logger.error(f"Error saving TUIC config: {e}")
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
        return False, "❌ Нет ни одного клиента. Сначала добавьте: /tuic_add <имя>"

    config["enabled"] = True
    if _save_config(config):
        return True, "✅ TUIC включён"
    return False, "❌ Ошибка при сохранении"


def disable() -> Tuple[bool, str]:
    config = _load_config()
    config["enabled"] = False
    if _save_config(config):
        return True, "🔴 TUIC выключен"
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
        "congestion_control": config.get("congestion_control", "bbr"),
        "udp_relay_mode": config.get("udp_relay_mode", "native"),
        "alpn": config.get("alpn", ["h3"]),
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
            if client.get("uuid"):
                u = client["uuid"]
                client["uuid"] = f"{u[:8]}...{u[-4:]}"
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
                    logger.info(f"Detected server IP: {ip} (via {service})")
                    return ip
        except Exception as e:
            logger.debug(f"Failed to get IP from {service}: {e}")
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
            return False, "❌ Не удалось определить IP. Укажите вручную: /tuic_set_server <IP>"
    else:
        auto_detected = False

    config = _load_config()
    config["server"] = server.strip()
    if _save_config(config):
        if auto_detected:
            return True, f"✅ Сервер установлен автоматически: {server}"
        return True, f"✅ Сервер установлен: {server}"
    return False, "❌ Ошибка при сохранении"


def set_port(port: int) -> Tuple[bool, str]:
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False, "❌ Порт должен быть числом от 1 до 65535"

    config = _load_config()
    config["port"] = port
    recommended = "⭐ рекомендуемый" if port in RECOMMENDED_PORTS else ""
    if _save_config(config):
        return True, f"✅ Порт установлен: {port} {recommended}\n⚠️ Не забудьте открыть UDP порт: `ufw allow {port}/udp`"
    return False, "❌ Ошибка при сохранении"


def set_sni(sni: str) -> Tuple[bool, str]:
    config = _load_config()
    config["sni"] = sni.strip() if sni else ""
    if _save_config(config):
        return True, f"✅ SNI установлен: {config['sni'] or '(пусто)'}"
    return False, "❌ Ошибка при сохранении"


def set_insecure(insecure: bool) -> Tuple[bool, str]:
    config = _load_config()
    config["insecure"] = insecure
    if _save_config(config):
        status = "включён ⚠️" if insecure else "выключен ✅"
        return True, f"✅ Insecure mode: {status}"
    return False, "❌ Ошибка при сохранении"


def set_congestion_control(cc: str) -> Tuple[bool, str]:
    cc = (cc or "").strip().lower()
    if cc not in CONGESTION_CONTROLS:
        return False, f"❌ Допустимые значения: {', '.join(CONGESTION_CONTROLS)}"

    config = _load_config()
    config["congestion_control"] = cc
    if _save_config(config):
        return True, f"✅ Congestion control: {cc}"
    return False, "❌ Ошибка при сохранении"


def set_udp_relay_mode(mode: str) -> Tuple[bool, str]:
    mode = (mode or "").strip().lower()
    if mode not in UDP_RELAY_MODES:
        return False, f"❌ Допустимые значения: {', '.join(UDP_RELAY_MODES)}"

    config = _load_config()
    config["udp_relay_mode"] = mode
    if _save_config(config):
        return True, f"✅ UDP relay mode: {mode}"
    return False, "❌ Ошибка при сохранении"


# === TLS ===

def generate_self_signed_cert(
    cert_path: str = "/etc/tuic/server.crt",
    key_path: str = "/etc/tuic/server.key",
    domain: str = "www.microsoft.com",
    days: int = 36500,
) -> Tuple[bool, str]:
    cert_dir = os.path.dirname(cert_path) or "."
    try:
        os.makedirs(cert_dir, exist_ok=True)
    except Exception as e:
        return False, f"❌ Не удалось создать директорию {cert_dir}: {e}"

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
            return True, f"✅ Сертификат сгенерирован (openssl)\n📄 Cert: `{cert_path}`\n🔑 Key: `{key_path}`"
    except FileNotFoundError:
        logger.debug("openssl not found, trying Python cryptography")
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
        return True, f"✅ Сертификат сгенерирован (Python)\n📄 Cert: `{cert_path}`\n🔑 Key: `{key_path}`"
    except ImportError:
        return False, "❌ Установите openssl или: `pip install cryptography`"
    except Exception as e:
        return False, f"❌ Ошибка генерации сертификата: {e}"


def generate_all() -> Tuple[bool, Dict, str]:
    results = {}
    messages = []

    # 1. Auto-detect server IP
    success_srv, msg_srv = set_server(None)
    messages.append(msg_srv)
    if success_srv:
        config = _load_config()
        results["server"] = config.get("server", "")

    # 2. Generate TLS cert
    success_cert, msg_cert = generate_self_signed_cert()
    results["cert_generated"] = success_cert
    messages.append(msg_cert)

    # 3. Create default client
    success_client, msg_client, client = add_client("default")
    results["default_client"] = client
    messages.append(msg_client)

    overall_success = success_srv and success_cert and success_client
    return overall_success, results, "\n".join(messages)


# === Clients ===

def list_clients() -> List[Dict]:
    config = _load_config()
    return config.get("clients", [])


def add_client(name: str, client_password: Optional[str] = None,
               client_uuid: Optional[str] = None) -> Tuple[bool, str, Dict]:
    if not name or not name.strip():
        return False, "❌ Имя клиента не может быть пустым", {}

    name = name.strip()
    config = _load_config()

    for client in config.get("clients", []):
        if client.get("name") == name:
            return False, f"❌ Клиент {name} уже существует", {}

    if not client_uuid:
        client_uuid = _generate_uuid()
    if not client_password:
        client_password = _generate_password()

    client = {
        "name": name,
        "uuid": client_uuid,
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

def generate_tuic_uri(
    client_uuid: str,
    client_password: str,
    comment: str = "TUIC",
) -> str:
    """
    Генерация URI tuic:// для клиента.

    Format: tuic://uuid:password@server:port?congestion_control=bbr&udp_relay_mode=native&alpn=h3&sni=xxx#comment
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 443)

    if not server or not client_uuid or not client_password:
        return ""

    params = {}
    cc = config.get("congestion_control", "bbr")
    if cc:
        params["congestion_control"] = cc

    udp_mode = config.get("udp_relay_mode", "native")
    if udp_mode:
        params["udp_relay_mode"] = udp_mode

    alpn = config.get("alpn", ["h3"])
    if alpn:
        params["alpn"] = ",".join(alpn)

    sni = config.get("sni", "")
    if sni:
        params["sni"] = sni

    if config.get("insecure"):
        params["allow_insecure"] = "1"

    query_string = "&".join(
        f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()
    )
    comment_enc = urllib.parse.quote(comment)

    uri = f"tuic://{client_uuid}:{urllib.parse.quote(client_password)}@{server}:{port}"
    if query_string:
        uri += f"?{query_string}"
    uri += f"#{comment_enc}"
    return uri


def generate_client_uri(name: str) -> Tuple[bool, str, str]:
    client = get_client(name)
    if not client:
        return False, "❌ Клиент не найден", ""

    client_uuid = client.get("uuid", "")
    client_password = client.get("password", "")
    if not client_uuid or not client_password:
        return False, f"❌ У клиента {name} неполные данные (uuid/password)", ""

    uri = generate_tuic_uri(
        client_uuid,
        client_password,
        visible_profile_name("TUIC", _load_config().get("server", ""), name),
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
        logger.error(f"Failed to generate TUIC QR image: {e}")
        return False, None, f"❌ Ошибка генерации QR: {e}"


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
        "uuid": client.get("uuid", ""),
        "password": client.get("password", ""),
        "uri": uri,
        "qr_buffer": qr_buffer,
    }
    return True, "✅ QR-пакет для клиента TUIC подготовлен", payload


# === Export Configurations ===

def export_server_config() -> Dict:
    """
    Сгенерировать серверную конфигурацию sing-box inbound для TUIC.

    Returns:
        Dict — sing-box inbound config
    """
    config = _load_config()
    clients = config.get("clients", [])

    users = []
    for c in clients:
        u = c.get("uuid", "")
        p = c.get("password", "")
        n = c.get("name", "")
        if u and p:
            users.append({"uuid": u, "password": p, "name": n})

    inbound = {
        "type": "tuic",
        "tag": "tuic-in",
        "listen": "::",
        "listen_port": config.get("port", 443),
        "users": users,
        "congestion_control": config.get("congestion_control", "bbr"),
        "tls": {
            "enabled": True,
            "certificate_path": config.get("tls_cert_path", "/etc/tuic/server.crt"),
            "key_path": config.get("tls_key_path", "/etc/tuic/server.key"),
        },
    }

    alpn = config.get("alpn", ["h3"])
    if alpn:
        inbound["tls"]["alpn"] = alpn

    return inbound


def export_server_singbox_config() -> Dict:
    """
    Полная серверная конфигурация sing-box с TUIC inbound.
    """
    inbound = export_server_config()
    return {
        "log": {"level": "info"},
        "inbounds": [inbound],
        "outbounds": [
            {"type": "direct", "tag": "direct"},
        ],
    }


def export_server_config_json() -> str:
    """Серверная конфигурация в JSON (для записи в файл)."""
    return json.dumps(export_server_singbox_config(), indent=2, ensure_ascii=False)


def export_singbox_config(client_name: Optional[str] = None) -> Dict:
    """
    Клиентская конфигурация sing-box с TUIC outbound.
    """
    config = _load_config()

    client_uuid = ""
    client_password = ""

    if client_name:
        client = next(
            (c for c in config.get("clients", []) if c.get("name") == client_name),
            None,
        )
        if client:
            client_uuid = client.get("uuid", "")
            client_password = client.get("password", "")

    if not client_uuid:
        # Fallback to first client
        clients = config.get("clients", [])
        if clients:
            client_uuid = clients[0].get("uuid", "")
            client_password = clients[0].get("password", "")

    outbound = {
        "type": "tuic",
        "tag": "proxy",
        "server": config.get("server", ""),
        "server_port": config.get("port", 443),
        "uuid": client_uuid,
        "password": client_password,
        "congestion_control": config.get("congestion_control", "bbr"),
        "udp_relay_mode": config.get("udp_relay_mode", "native"),
        "tls": {
            "enabled": True,
            "server_name": config.get("sni", "") or config.get("server", ""),
            "insecure": config.get("insecure", False),
        },
    }

    alpn = config.get("alpn", ["h3"])
    if alpn:
        outbound["tls"]["alpn"] = alpn

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
    Клиентская конфигурация Clash Meta (YAML) с TUIC proxy.
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 443)
    cc = config.get("congestion_control", "bbr")
    udp_mode = config.get("udp_relay_mode", "native")
    sni = config.get("sni", "")
    insecure = config.get("insecure", False)
    alpn = config.get("alpn", ["h3"])

    client_uuid = ""
    client_password = ""

    if client_name:
        client = next(
            (c for c in config.get("clients", []) if c.get("name") == client_name),
            None,
        )
        if client:
            client_uuid = client.get("uuid", "")
            client_password = client.get("password", "")

    if not client_uuid:
        clients = config.get("clients", [])
        if clients:
            client_uuid = clients[0].get("uuid", "")
            client_password = clients[0].get("password", "")

    lines = [
        "port: 7890",
        "socks-port: 7891",
        "mixed-port: 7892",
        "mode: rule",
        "log-level: info",
        "",
        "proxies:",
        "  - name: tuic",
        "    type: tuic",
        f"    server: {server}",
        f"    port: {port}",
        f"    uuid: {client_uuid}",
        f"    password: {client_password}",
        f"    congestion-controller: {cc}",
        f"    udp-relay-mode: {udp_mode}",
    ]

    if alpn:
        lines.append("    alpn:")
        for a in alpn:
            lines.append(f"      - {a}")

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
        "      - tuic",
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
        u = client.get("uuid", "")
        p = client.get("password", "")
        if u and p:
            link = generate_tuic_uri(
                u,
                p,
                visible_profile_name("TUIC", _load_config().get("server", ""), name),
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

def apply_config(config_path: str = "/etc/tuic/config.json") -> Tuple[bool, str]:
    """
    Применить текущую конфигурацию к серверу.
    Записывает sing-box JSON и перезапускает сервис.
    """
    config_json = export_server_config_json()

    try:
        config_dir = os.path.dirname(config_path) or "."
        os.makedirs(config_dir, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_json)
            f.flush()
            os.fsync(f.fileno())

        logger.info(f"TUIC server config written to {config_path}")

        result = _host_run(
            ["systemctl", "restart", "tuic-server"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, f"✅ Конфиг применён и сервис перезапущен\n📄 `{config_path}`"
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"⚠️ Конфиг записан, но сервис не перезапустился:\n`{error}`"

    except PermissionError:
        return False, f"❌ Нет прав на запись в {config_path}. Запустите с sudo."
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def service_control(action: str) -> Tuple[bool, str]:
    if action not in ("start", "stop", "restart", "status"):
        return False, f"❌ Неизвестное действие: {action}"

    try:
        result = _host_run(
            ["systemctl", action, "tuic-server"],
            capture_output=True, text=True, timeout=30,
        )

        if action == "status":
            output = result.stdout.strip() or result.stderr.strip()
            is_active = "active (running)" in output
            status_emoji = "🟢" if is_active else "🔴"
            return True, f"{status_emoji} TUIC сервис:\n```\n{output[:500]}\n```"

        if result.returncode == 0:
            action_labels = {"start": "запущен", "stop": "остановлен", "restart": "перезапущен"}
            return True, f"✅ TUIC {action_labels.get(action, action)}"
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
            ["journalctl", "-u", "tuic-server", "-n", str(lines), "--no-pager"],
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
