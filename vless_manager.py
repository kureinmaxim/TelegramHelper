# -*- coding: utf-8 -*-
"""
Модуль для управления VLESS-Reality конфигурацией.

VLESS-Reality — это протокол маскировки трафика, который делает соединение
неотличимым от обычного HTTPS трафика к популярным сайтам.

Структура конфигурации:
{
    "enabled": false,
    "server": "IP или домен VPS",
    "port": 443,
    "uuid": "VLESS UUID",
    "public_key": "Reality публичный ключ (x25519)",
    "private_key": "Reality приватный ключ (только для сервера)",
    "short_id": "hex строка 1-16 символов",
    "sni": "yahoo.com",
    "fingerprint": "chrome",
    "flow": "xtls-rprx-vision",
    "fallback_servers": []
}
"""

import json
import logging
import os
import secrets
import subprocess
import threading
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional, Tuple

from host_utils import host_run as _host_run
from host_utils import host_write_text as _host_write_text
from profile_names import visible_profile_name

logger = logging.getLogger(__name__)

# Thread safety
_vless_lock = threading.Lock()

# Путь к файлу конфигурации VLESS
_VLESS_CONFIG_PATH = os.getenv(
    "VLESS_CONFIG_PATH", os.path.join(os.getcwd(), "vless_config.json")
)

# SNI варианты для маскировки (Reality "dest").
#
# ВАЖНО: это общеизвестные варианты, а НЕ гарантия обхода DPI. На
# практике мобильные операторы (особенно в РФ) всё активнее
# фингерпринтят Reality-трафик именно к «типовым для VPN-гайдов»
# доменам — в первую очередь www.microsoft.com. Известный кейс: у конкретного
# мобильного оператора SNI www.microsoft.com блокировался (хендшейк не доходил
# до Xray вообще, хотя сам IP:порт были доступны), а смена на yahoo.com
# на том же VPS/порту/ключах сразу решила проблему. Если клиент не
# подключается при рабочих сети/порте/ключах — первое, что стоит
# попробовать: сменить SNI на менее «типовой» для VPN-гайдов домен.
AVAILABLE_SNI = [
    "yahoo.com",
    "www.cloudflare.com",
    "www.amazon.com",
    "www.microsoft.com",
    "www.apple.com",
    "www.google.com",
    "www.netflix.com",
]

# TLS fingerprints
AVAILABLE_FINGERPRINTS = [
    "chrome",
    "firefox",
    "safari",
    "edge",
    "ios",
    "android",
    "random",
    "randomized",
]

# Рекомендуемые порты для VLESS-Reality (в порядке приоритета)
# - 443: Стандартный HTTPS (мониторится DPI)
# - 8443: Альтернативный HTTPS (⭐ рекомендуемый)
# - 2053: DNS-over-HTTPS (Cloudflare)
# - 2083: cPanel SSL
# - 2087: WHM SSL
# - 2096: cPanel Webmail
# - 8880: Alt HTTP
RECOMMENDED_PORTS = [443, 8443, 2053, 2083, 2087, 2096, 8880]

LEGACY_VLESS_REQUIRED_FIELDS = (
    "server",
    "port",
    "uuid",
    "public_key",
    "short_id",
    "sni",
    "fingerprint",
    "flow",
)

# Дефолтная конфигурация
DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",
    "port": 443,
    "uuid": "",
    "public_key": "",
    "private_key": "",
    "short_id": "",
    # yahoo.com вместо www.microsoft.com: microsoft — самый «типовой для
    # VPN-гайдов» SNI, и у ряда мобильных операторов он блокируется DPI ещё до
    # Xray (см. комментарий к AVAILABLE_SNI). Проверено: yahoo.com работает там,
    # где microsoft резался, при тех же VPS/порте/ключах.
    "sni": "yahoo.com",
    "fingerprint": "chrome",
    "flow": "xtls-rprx-vision",
    "fallback_servers": [],
    "clients": [],
    "created_at": None,
    "updated_at": None,
    # Nginx SNI routing (for Headscale / Home Assistant coexistence on port 443)
    "nginx_fallback_enabled": False,
    "nginx_fallback_port": 8443,
    "headscale_domain": "",
    "ha_domain": "",
}


def build_legacy_vless_contract(config: Dict) -> Dict:
    """
    Собрать минимальный legacy Reality/VLESS контракт для старых клиентских экспортов.

    Этот формат должен оставаться стабильным, чтобы `TelegramHelper` мог читать и
    переиспользовать legacy-артефакты `TelegramSimple` без немедленной миграции VPS.
    """
    return {
        "server": config.get("server", ""),
        "port": config.get("port", 443),
        "uuid": config.get("uuid", ""),
        "public_key": config.get("public_key", ""),
        "short_id": config.get("short_id", ""),
        "sni": config.get("sni", "www.microsoft.com"),
        "fingerprint": config.get("fingerprint", "chrome"),
        "flow": config.get("flow", "xtls-rprx-vision"),
    }


def validate_legacy_vless_contract(payload: Dict) -> Tuple[bool, List[str]]:
    """
    Проверить, что legacy Reality/VLESS артефакт содержит все обязательные поля.
    """
    missing = [
        field
        for field in LEGACY_VLESS_REQUIRED_FIELDS
        if payload.get(field) in (None, "")
    ]
    return len(missing) == 0, missing


def _normalize_clients(config: Dict) -> None:
    """
    Нормализовать список клиентов VLESS.
    """
    clients = config.get("clients") or []
    if not isinstance(clients, list):
        clients = []

    # Если клиентов нет, но есть uuid - создаём дефолтного клиента.
    if not clients and config.get("uuid"):
        clients = [
            {
                "name": "default",
                "uuid": config.get("uuid"),
                "created_at": datetime.now().isoformat(),
            }
        ]

    # Убедимся, что дефолтный клиент синхронизирован с config["uuid"]
    if config.get("uuid"):
        for client in clients:
            if client.get("name") == "default":
                client["uuid"] = config.get("uuid")
                break
        else:
            clients.append(
                {
                    "name": "default",
                    "uuid": config.get("uuid"),
                    "created_at": datetime.now().isoformat(),
                }
            )

    config["clients"] = clients


def _effective_uuid(config: Dict) -> str:
    """
    UUID для проверки полноты конфига: корневой или любого клиента в clients[].

    Раньше в /ver и «configured» учитывался только config[\"uuid\"], из‑за чего при
    нескольких клиентах без дублирования UUID в корне показывало «нет UUID».
    """
    u = (config.get("uuid") or "").strip()
    if u:
        return u
    for c in config.get("clients") or []:
        if not isinstance(c, dict):
            continue
        cu = (c.get("uuid") or "").strip()
        if cu:
            return cu
    return ""


def _load_config() -> Dict:
    """Загрузить конфигурацию VLESS из файла"""
    with _vless_lock:
        if not os.path.exists(_VLESS_CONFIG_PATH):
            return dict(DEFAULT_CONFIG)

        try:
            with open(_VLESS_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge with defaults for missing keys
                config = dict(DEFAULT_CONFIG)
                config.update(data)
                _normalize_clients(config)
                return config
        except Exception as e:
            logger.error(f"Error loading VLESS config: {e}")
            return dict(DEFAULT_CONFIG)


def _save_config(config: Dict) -> bool:
    """Сохранить конфигурацию VLESS в файл"""
    with _vless_lock:
        try:
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]

            directory = os.path.dirname(_VLESS_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)

            with open(_VLESS_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            return True
        except Exception as e:
            logger.error(f"Error saving VLESS config: {e}")
            return False


# === Public API ===


def is_vless_enabled() -> bool:
    """Проверить, включён ли VLESS-Reality"""
    config = _load_config()
    return config.get("enabled", False)


def enable_vless() -> Tuple[bool, str]:
    """
    Включить VLESS-Reality

    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    config = _load_config()
    # Подтягиваем UUID из clients[], если в корне пусто (исторический формат).
    eu = _effective_uuid(config)
    if eu and not (config.get("uuid") or "").strip():
        config["uuid"] = eu
        _normalize_clients(config)

    # Проверяем, что все необходимые параметры настроены
    required = ["server", "uuid", "public_key", "short_id"]
    missing = [key for key in required if not (config.get(key) or "").strip()]

    if missing:
        return False, f"Не настроены обязательные параметры: {', '.join(missing)}"

    config["enabled"] = True
    if _save_config(config):
        logger.info("VLESS-Reality enabled")
        return True, "✅ VLESS-Reality включён"

    return False, "❌ Ошибка при сохранении конфигурации"


def disable_vless() -> Tuple[bool, str]:
    """
    Выключить VLESS-Reality

    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    config = _load_config()
    config["enabled"] = False

    if _save_config(config):
        logger.info("VLESS-Reality disabled")
        return True, "🔴 VLESS-Reality выключен"

    return False, "❌ Ошибка при сохранении конфигурации"


def get_vless_status() -> Dict:
    """
    Получить статус VLESS-Reality

    Returns:
        Dict с информацией о статусе
    """
    config = _load_config()
    eff_uuid = _effective_uuid(config)

    # Проверяем конфигурацию (UUID может быть только у записей в clients[])
    configured = all(
        [
            bool((config.get("server") or "").strip()),
            bool(eff_uuid),
            bool((config.get("public_key") or "").strip()),
            bool((config.get("short_id") or "").strip()),
        ]
    )

    return {
        "enabled": config.get("enabled", False),
        "configured": configured,
        "server": config.get("server", ""),
        "port": config.get("port", 443),
        "sni": config.get("sni", "www.microsoft.com"),
        "fingerprint": config.get("fingerprint", "chrome"),
        "has_uuid": bool(eff_uuid),
        "has_public_key": bool(config.get("public_key")),
        "has_private_key": bool(config.get("private_key")),
        "has_short_id": bool(config.get("short_id")),
        "updated_at": config.get("updated_at"),
    }


def get_vless_version_card_fields() -> Dict:
    """
    Поля для блока VLESS в /ver: сервер в конфиге, подсказка адреса VPS из .env, список недостающих ключей.

    «Сервер» в VLESS — это публичный IP или домен, который клиенты используют для подключения;
    если поле пустое, Reality не знает, куда стучаться, даже если остальные поля заполнены.
    """
    status = get_vless_status()
    server = (status.get("server") or "").strip()
    public_hint = ""
    if not server:
        try:
            from dockhand_tunnel_hints import get_dockhand_ssh_params

            p = get_dockhand_ssh_params(resolve_public_ip=False)
            if not p.host_is_placeholder:
                public_hint = p.host.strip()
        except Exception as e:
            logger.debug("get_vless_version_card_fields: public hint: %s", e)

    missing_keys: List[str] = []
    if not status.get("configured", False):
        if not server:
            missing_keys.append("server")
        if not status.get("has_uuid"):
            missing_keys.append("uuid")
        if not status.get("has_public_key"):
            missing_keys.append("public_key")
        if not status.get("has_short_id"):
            missing_keys.append("short_id")

    return {
        "status": status,
        "server": server,
        "public_hint": public_hint,
        "missing_keys": missing_keys,
    }


def get_vless_config(include_secrets: bool = False) -> Dict:
    """
    Получить конфигурацию VLESS (опционально с секретами)

    Args:
        include_secrets: включать ли приватные ключи

    Returns:
        Dict с конфигурацией
    """
    config = _load_config()

    if not include_secrets:
        # Маскируем секретные данные
        if config.get("uuid"):
            uuid = config["uuid"]
            config["uuid"] = f"{uuid[:8]}...{uuid[-4:]}" if len(uuid) > 12 else "***"
        if config.get("public_key"):
            pk = config["public_key"]
            config["public_key"] = f"{pk[:8]}...{pk[-4:]}" if len(pk) > 12 else "***"
        if config.get("private_key"):
            config["private_key"] = "***hidden***"
        if config.get("short_id"):
            sid = config["short_id"]
            config["short_id"] = f"{sid[:4]}..." if len(sid) > 4 else "***"

    return config


_PUBLIC_IP_CACHE: Dict[str, object] = {"ip": None, "ts": 0.0}
_PUBLIC_IP_TTL = 3600.0  # публичный IP VPS стабилен — кешируем на час


def get_server_public_ip() -> Optional[str]:
    """
    Получить публичный IP адрес сервера.
    Использует несколько методов для надёжности. Результат кешируется на
    _PUBLIC_IP_TTL секунд, чтобы /start не дёргал сеть на каждый вызов.

    Returns:
        IP адрес или None если не удалось определить
    """
    import time
    import urllib.request

    now = time.time()
    cached_ip = _PUBLIC_IP_CACHE.get("ip")
    if (
        cached_ip
        and (now - float(_PUBLIC_IP_CACHE.get("ts", 0.0) or 0.0)) < _PUBLIC_IP_TTL
    ):
        return cached_ip  # type: ignore[return-value]

    # Список сервисов для определения IP
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
                ip = response.read().decode("utf-8").strip()
                # Базовая валидация IP
                parts = ip.split(".")
                if len(parts) == 4 and all(
                    p.isdigit() and 0 <= int(p) <= 255 for p in parts
                ):
                    logger.info(f"Detected server IP: {ip} (via {service})")
                    _PUBLIC_IP_CACHE.update(ip=ip, ts=now)
                    return ip
        except Exception as e:
            logger.debug(f"Failed to get IP from {service}: {e}")
            continue

    # Fallback: попробовать получить через socket
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        # Проверяем что это не локальный IP
        if not ip.startswith(("10.", "172.", "192.168.", "127.")):
            logger.info(f"Detected server IP via socket: {ip}")
            _PUBLIC_IP_CACHE.update(ip=ip, ts=now)
            return ip
    except Exception as e:
        logger.debug(f"Failed to get IP via socket: {e}")

    return None


def set_vless_server(server: Optional[str] = None) -> Tuple[bool, str]:
    """
    Установить адрес сервера VLESS.

    Args:
        server: IP или домен. Если None - автоопределение.

    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    # Автоопределение IP если не указан
    if not server or not server.strip():
        detected_ip = get_server_public_ip()
        if detected_ip:
            server = detected_ip
            auto_detected = True
        else:
            return (
                False,
                "❌ Не удалось автоматически определить IP сервера\n\nИспользуйте: /vless_set_server <IP>",
            )
    else:
        auto_detected = False

    config = _load_config()
    config["server"] = server.strip()

    if _save_config(config):
        if auto_detected:
            return True, f"✅ Сервер установлен автоматически: {server}"
        return True, f"✅ Сервер установлен: {server}"
    return False, "❌ Ошибка при сохранении"


def set_vless_port(port: int) -> Tuple[bool, str]:
    """
    Установить порт сервера VLESS.

    Рекомендуемые порты: 443, 8443, 2053, 2083, 2087, 2096, 8880
    """
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False, "❌ Порт должен быть числом от 1 до 65535"

    config = _load_config()
    old_port = config.get("port", 443)
    config["port"] = port

    if _save_config(config):
        msg = f"✅ Порт установлен: {port}"
        if old_port != port:
            msg += f"\n📝 Предыдущий порт: {old_port}"
        if port in RECOMMENDED_PORTS:
            msg += "\n⭐ Это рекомендуемый порт"
        else:
            msg += f"\n💡 Рекомендуемые порты: {', '.join(map(str, RECOMMENDED_PORTS[:4]))}..."
        # Next-steps (systemctl / ufw / /xray_restart / перевыдача URI)
        # показывает handlers._legacy_vless_apply_followup после apply.
        return True, msg
    return False, "❌ Ошибка при сохранении"


def get_recommended_ports() -> list:
    """Получить список рекомендуемых портов для VLESS-Reality."""
    return RECOMMENDED_PORTS.copy()


def set_vless_uuid(uuid: str) -> Tuple[bool, str]:
    """Установить UUID клиента VLESS"""
    if not uuid or not uuid.strip():
        return False, "❌ UUID не может быть пустым"

    # Базовая валидация UUID формата
    uuid = uuid.strip()
    if len(uuid) < 32:
        return False, "❌ UUID слишком короткий"

    config = _load_config()
    config["uuid"] = uuid
    _normalize_clients(config)
    for client in config.get("clients", []):
        if client.get("name") == "default":
            client["uuid"] = uuid
            break

    if _save_config(config):
        return True, f"✅ UUID установлен: {uuid[:8]}...{uuid[-4:]}"
    return False, "❌ Ошибка при сохранении"


def set_vless_public_key(public_key: str) -> Tuple[bool, str]:
    """Установить публичный ключ Reality"""
    if not public_key or not public_key.strip():
        return False, "❌ Публичный ключ не может быть пустым"

    config = _load_config()
    config["public_key"] = public_key.strip()

    if _save_config(config):
        return True, f"✅ Публичный ключ установлен"
    return False, "❌ Ошибка при сохранении"


def set_vless_private_key(private_key: str) -> Tuple[bool, str]:
    """Установить приватный ключ Reality (только для сервера)"""
    if not private_key or not private_key.strip():
        return False, "❌ Приватный ключ не может быть пустым"

    config = _load_config()
    config["private_key"] = private_key.strip()

    if _save_config(config):
        return True, f"✅ Приватный ключ установлен"
    return False, "❌ Ошибка при сохранении"


def set_vless_short_id(short_id: str) -> Tuple[bool, str]:
    """Установить Short ID для сессии"""
    if not short_id or not short_id.strip():
        return False, "❌ Short ID не может быть пустым"

    short_id = short_id.strip()

    # Валидация: должен быть hex строкой 1-16 символов
    if not all(c in "0123456789abcdefABCDEF" for c in short_id):
        return False, "❌ Short ID должен быть hex строкой (0-9, a-f)"

    if len(short_id) > 16:
        return False, "❌ Short ID не должен превышать 16 символов"

    config = _load_config()
    config["short_id"] = short_id.lower()

    if _save_config(config):
        return True, f"✅ Short ID установлен: {short_id[:4]}..."
    return False, "❌ Ошибка при сохранении"


def set_vless_sni(sni: str) -> Tuple[bool, str]:
    """Установить SNI для маскировки"""
    if not sni or not sni.strip():
        return False, "❌ SNI не может быть пустым"

    sni = sni.strip().lower()

    config = _load_config()
    config["sni"] = sni

    if _save_config(config):
        msg = f"✅ SNI установлен: {sni}"
        # Не отговариваем от кастомного SNI — наоборот, часто именно он решает
        # проблему (см. комментарий к AVAILABLE_SNI выше про www.microsoft.com).
        if sni not in AVAILABLE_SNI:
            msg += (
                "\n💡 Если клиент не подключается с этим SNI при рабочих сети/порте/ключах — "
                "попробуйте один из: " + ", ".join(AVAILABLE_SNI[:3])
            )
        return True, msg
    return False, "❌ Ошибка при сохранении"


def set_vless_fingerprint(fingerprint: str) -> Tuple[bool, str]:
    """Установить TLS fingerprint"""
    if not fingerprint or not fingerprint.strip():
        return False, "❌ Fingerprint не может быть пустым"

    fingerprint = fingerprint.strip().lower()

    if fingerprint not in AVAILABLE_FINGERPRINTS:
        return (
            False,
            f"❌ Неизвестный fingerprint. Доступные: {', '.join(AVAILABLE_FINGERPRINTS)}",
        )

    config = _load_config()
    config["fingerprint"] = fingerprint

    if _save_config(config):
        return True, f"✅ Fingerprint установлен: {fingerprint}"
    return False, "❌ Ошибка при сохранении"


def set_nginx_fallback(enabled: bool, port: int = 8443) -> Tuple[bool, str]:
    """Enable/disable Nginx SNI fallback in Xray config."""
    if port < 1 or port > 65535:
        return False, "❌ Порт должен быть от 1 до 65535"

    config = _load_config()
    config["nginx_fallback_enabled"] = enabled
    config["nginx_fallback_port"] = port

    if _save_config(config):
        state = "включён" if enabled else "выключен"
        return True, f"✅ Nginx fallback {state} (порт {port})"
    return False, "❌ Ошибка при сохранении"


def set_nginx_domains(headscale_domain: str, ha_domain: str = "") -> Tuple[bool, str]:
    """Set domains for Nginx SNI routing."""
    headscale_domain = headscale_domain.strip()
    ha_domain = ha_domain.strip()

    if not headscale_domain:
        return False, "❌ Домен Headscale не может быть пустым"

    config = _load_config()
    config["headscale_domain"] = headscale_domain
    config["ha_domain"] = ha_domain

    if _save_config(config):
        msg = f"✅ Headscale домен: {headscale_domain}"
        if ha_domain:
            msg += f"\n✅ Home Assistant домен: {ha_domain}"
        return True, msg
    return False, "❌ Ошибка при сохранении"


def get_nginx_sni_config() -> Tuple[bool, str]:
    """Generate Nginx stream SNI config for copy-paste to VPS."""
    config = _load_config()
    headscale_domain = config.get("headscale_domain", "")
    ha_domain = config.get("ha_domain", "")
    nginx_port = config.get("nginx_fallback_port", 8443)

    if not headscale_domain:
        return False, "❌ Домен Headscale не установлен. Используйте /nginx_set_domain"

    # Build map entries and upstreams
    map_entries = f"        {headscale_domain}  headscale_backend;"
    upstreams = "    upstream headscale_backend { server 127.0.0.1:8080; }"

    if ha_domain:
        map_entries += f"\n        {ha_domain}         ha_backend;"
        upstreams += "\n    upstream ha_backend        { server 127.0.0.1:8123; }"

    map_entries += "\n        default              api_backend;"
    upstreams += "\n    upstream api_backend       { server 127.0.0.1:8000; }"

    nginx_config = f"""# Nginx Stream SNI Routing
# File: /etc/nginx/conf.d/stream_sni.conf
# Requires: libnginx-mod-stream (apt install libnginx-mod-stream)

stream {{
    map $ssl_preread_server_name $backend {{
{map_entries}
    }}

{upstreams}

    server {{
        listen {nginx_port};
        listen [::]:{nginx_port};
        proxy_pass $backend;
        ssl_preread on;
        proxy_protocol on;
    }}
}}"""

    return True, nginx_config


def generate_uuid() -> str:
    """Генерация нового UUID для VLESS"""
    import uuid

    return str(uuid.uuid4())


def generate_short_id(length: int = 8) -> str:
    """Генерация нового Short ID (hex строка)"""
    if length < 1:
        length = 1
    if length > 16:
        length = 16
    return secrets.token_hex(length // 2 + length % 2)[:length]


def generate_reality_keys() -> Tuple[Optional[str], Optional[str], str]:
    """
    Генерация пары ключей x25519 для Reality

    Пытается использовать xray x25519 если доступен,
    иначе генерирует программно.

    Returns:
        Tuple[private_key, public_key, method]: ключи и метод генерации
    """
    # Пробуем использовать xray для генерации ключей
    try:
        result = subprocess.run(
            ["xray", "x25519"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            lines = output.split("\n")
            private_key = None
            public_key = None

            # Поддержка двух форматов вывода `xray x25519`:
            #   старый:  "Private key: ..."         / "Public key: ..."
            #   новый (26.x): "PrivateKey: ..."     / "Password (PublicKey): ..."
            for line in lines:
                low = line.strip().lower()
                if low.startswith("privatekey") or low.startswith("private key"):
                    private_key = line.split(":", 1)[1].strip()
                elif (
                    "publickey" in low
                    or low.startswith("public key")
                    or low.startswith("password")
                ):
                    public_key = line.split(":", 1)[1].strip()

            if private_key and public_key:
                logger.info("Generated Reality keys using xray x25519")
                return private_key, public_key, "xray"
    except FileNotFoundError:
        logger.info("xray not found, will generate keys programmatically")
    except subprocess.TimeoutExpired:
        logger.warning("xray x25519 timed out")
    except Exception as e:
        logger.warning(f"xray x25519 failed: {e}")

    # Fallback: генерация программно с использованием cryptography
    try:
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        private_key_obj = X25519PrivateKey.generate()
        public_key_obj = private_key_obj.public_key()

        # Получаем raw bytes и конвертируем в base64
        private_bytes = private_key_obj.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = public_key_obj.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )

        private_key = (
            base64.urlsafe_b64encode(private_bytes).decode("utf-8").rstrip("=")
        )
        public_key = base64.urlsafe_b64encode(public_bytes).decode("utf-8").rstrip("=")

        logger.info("Generated Reality keys using cryptography library")
        return private_key, public_key, "cryptography"
    except ImportError:
        logger.warning("cryptography library not available")
    except Exception as e:
        logger.error(f"Failed to generate keys with cryptography: {e}")

    # WARNING: нельзя подменять x25519-ключи случайными байтами.
    # Это приводит к невалидным Reality-конфигам и трудноуловимым ошибкам подключения.
    logger.error(
        "Unable to generate valid Reality keys: xray and cryptography are unavailable"
    )
    return None, None, "unavailable"


def generate_all_keys() -> Tuple[bool, Dict, str]:
    """
    Генерация всех ключей для VLESS-Reality

    Returns:
        Tuple[success, keys_dict, message]
    """
    try:
        uuid = generate_uuid()
        short_id = generate_short_id(8)
        private_key, public_key, method = generate_reality_keys()

        if not private_key or not public_key:
            return False, {}, "❌ Не удалось сгенерировать ключи Reality"

        keys = {
            "uuid": uuid,
            "short_id": short_id,
            "private_key": private_key,
            "public_key": public_key,
            "generation_method": method,
        }

        # Сохраняем в конфигурацию
        config = _load_config()
        config["uuid"] = uuid
        config["short_id"] = short_id
        config["private_key"] = private_key
        config["public_key"] = public_key
        _normalize_clients(config)
        # Синхронизируем default клиента с новым UUID
        for client in config.get("clients", []):
            if client.get("name") == "default":
                client["uuid"] = uuid
                break

        if _save_config(config):
            return True, keys, f"✅ Ключи сгенерированы (метод: {method})"

        return True, keys, "⚠️ Ключи сгенерированы, но не сохранены в конфиг"

    except Exception as e:
        logger.error(f"Error generating keys: {e}")
        return False, {}, f"❌ Ошибка генерации ключей: {e}"


def test_connection() -> Tuple[bool, str]:
    """
    Тестирование подключения к VLESS серверу

    Returns:
        Tuple[success, message]
    """
    config = _load_config()

    if not config.get("enabled"):
        return False, "⚠️ VLESS-Reality не включён"

    server = config.get("server")
    port = config.get("port", 443)

    if not server:
        return False, "❌ Сервер не настроен"

    # Простая проверка доступности порта
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((server, port))
        sock.close()

        if result == 0:
            return True, f"✅ Сервер {server}:{port} доступен"
        else:
            return False, f"❌ Сервер {server}:{port} недоступен (код: {result})"
    except socket.gaierror:
        return False, f"❌ Не удалось разрешить имя: {server}"
    except socket.timeout:
        return False, f"❌ Таймаут подключения к {server}:{port}"
    except Exception as e:
        return False, f"❌ Ошибка подключения: {e}"


def export_client_config() -> Dict:
    """
    Экспорт конфигурации для клиента (без приватного ключа)

    Returns:
        Dict с конфигурацией клиента
    """
    config = _load_config()

    return build_legacy_vless_contract(config)


def save_vless_config_files(output_dir: str = None) -> Tuple[bool, str, List[str]]:
    """
    Сохранить VLESS конфигурацию в файлы (JSON и TXT).

    Создаёт файлы аналогичные тем, что скачивает auto_setup_vps.sh:
    - vless_config_<IP>.json
    - vless_config_<IP>.txt

    Args:
        output_dir: Папка для сохранения (по умолчанию ./vless_configs)

    Returns:
        Tuple[success, message, list_of_created_files]
    """
    config = _load_config()

    # Проверяем что конфигурация заполнена
    server = config.get("server", "")
    if not server:
        return False, "❌ Сервер не настроен. Сначала используйте /vless_set_server", []

    port = config.get("port", 443)
    uuid = config.get("uuid", "")
    public_key = config.get("public_key", "")
    short_id = config.get("short_id", "")
    sni = config.get("sni", "www.microsoft.com")
    fingerprint = config.get("fingerprint", "chrome")

    if not uuid or not public_key:
        return (
            False,
            "❌ UUID или Public Key не настроены. Используйте /vless_gen_keys",
            [],
        )

    # Генерируем VLESS ссылку
    vless_link = generate_vless_link("VPS-Reality")

    # Определяем папку для сохранения
    if output_dir is None:
        # Ищем vless_configs относительно текущего файла или CWD
        base_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(base_dir, "vless_configs")

    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        return False, f"❌ Не удалось создать папку {output_dir}: {e}", []

    created_files = []

    # Имя файла на основе IP сервера
    safe_server = server.replace(":", "_").replace("/", "_")

    # 1. Сохраняем JSON конфиг
    json_path = os.path.join(output_dir, f"vless_config_{safe_server}.json")
    # WARNING: private_key must never be written into client-facing exports.
    json_config = {
        "server": server,
        "port": port,
        "uuid": uuid,
        "public_key": public_key,
        "short_id": short_id,
        "sni": sni,
        "fingerprint": fingerprint,
        "vless_link": vless_link,
    }

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_config, f, ensure_ascii=False, indent=2)
        created_files.append(json_path)
        logger.info(f"Saved VLESS JSON config to {json_path}")
    except Exception as e:
        return False, f"❌ Ошибка записи {json_path}: {e}", created_files

    # 2. Сохраняем текстовый конфиг
    txt_path = os.path.join(output_dir, f"vless_config_{safe_server}.txt")
    txt_content = f"""═══════════════════════════════════════════════════════════════
          🛡️  VLESS-Reality Configuration for Client
═══════════════════════════════════════════════════════════════

📍 Server:      {server}
🔌 Port:        {port}
🆔 UUID:        {uuid}
🔑 Public Key:  {public_key}
🏷️ Short ID:    {short_id}
🌐 SNI:         {sni}
🎭 Fingerprint: {fingerprint}

───────────────────────────────────────────────────────────────
🔗 VLESS Link (для Hiddify/Foxray/v2rayNG/NekoRay):

{vless_link}

═══════════════════════════════════════════════════════════════
"""

    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(txt_content)
        created_files.append(txt_path)
        logger.info(f"Saved VLESS TXT config to {txt_path}")
    except Exception as e:
        return False, f"❌ Ошибка записи {txt_path}: {e}", created_files

    return True, f"✅ Конфиги сохранены в {output_dir}", created_files


def _reconcile_reality_from_live_xray(
    xray_config_path: str = "/usr/local/etc/xray/config.json",
) -> None:
    """Подтянуть Reality-параметры из живого Xray-конфига перед генерацией ссылки.

    Зачем: ссылки строятся из ``vless_config.json``. Если админ правит
    ``/usr/local/etc/xray/config.json`` напрямую (типичный кейс — сменить SNI на
    менее «типовой» домен, обойдя бот), то бот молча отдаёт ссылку со СТАРЫМ
    ``sni``/``pbk`` — клиент по ней не подключается, хотя сервер здоров и рабочая
    прямая ссылка есть. Здесь мы сверяемся с живым конфигом и подтягиваем
    ``sni``/``short_id``/``port``/``uuid``/ключи, чтобы бот всегда выдавал то, что
    реально слушает Xray.

    No-op, если: файла нет; в нём нет vless+Reality inbound (panel-managed 3x-ui и
    т.п.); параметры уже совпадают. Тяжёлый вывод ``public_key`` из ``privateKey``
    (subprocess/cryptography внутри ``sync_from_xray_config``) запускается только
    при реальном расхождении — обычная генерация ссылки остаётся дешёвой.
    """
    try:
        if not os.path.exists(xray_config_path):
            return
        with open(xray_config_path, "r", encoding="utf-8") as f:
            xray_config = json.load(f)
    except Exception:
        return

    live_sni = live_sid = live_port = live_uuid = live_priv = None
    try:
        for inbound in xray_config.get("inbounds", []):
            if inbound.get("protocol") != "vless":
                continue
            reality = inbound.get("streamSettings", {}).get("realitySettings", {})
            if not reality:
                continue
            live_port = inbound.get("port")
            clients = inbound.get("settings", {}).get("clients", [])
            if clients:
                live_uuid = clients[0].get("id")
            names = reality.get("serverNames", [])
            sids = reality.get("shortIds", [])
            live_sni = names[0] if names else None
            live_sid = sids[0] if sids else None
            live_priv = reality.get("privateKey")
            break
    except Exception:
        return

    # Нет Reality-инбаунда — бот не управляет этим локальным Xray (panel-managed).
    if live_sni is None and live_priv is None:
        return

    config = _load_config()
    drift = (
        (live_sni is not None and config.get("sni") != live_sni)
        or (live_sid is not None and config.get("short_id") != live_sid)
        or (live_port is not None and config.get("port") != live_port)
        or (live_uuid is not None and config.get("uuid") != live_uuid)
        or (live_priv is not None and config.get("private_key") != live_priv)
    )
    if not drift:
        return

    ok, msg = sync_from_xray_config(xray_config_path)
    if ok:
        logger.info(
            "VLESS: авто-сверка с живым Xray перед генерацией ссылки (%s)", msg
        )
    else:
        logger.warning("VLESS: авто-сверка с живым Xray не удалась: %s", msg)


def generate_vless_link(comment: str = "sing-box-VLESS") -> str:
    """
    Генерация стандартной ссылки vless:// для импорта в клиенты (Hiddify, v2rayNG, etc)
    Format: vless://uuid@ip:port?security=reality&encryption=none&pbk=...&fp=...&type=tcp&flow=...&sni=...&sid=...#Name
    """
    _reconcile_reality_from_live_xray()
    config = _load_config()

    server = config.get("server", "")
    port = config.get("port", 443)
    uuid = config.get("uuid", "")
    public_key = config.get("public_key", "")
    short_id = config.get("short_id", "")
    sni = config.get("sni", "www.microsoft.com")
    fingerprint = config.get("fingerprint", "chrome")
    flow = config.get("flow", "xtls-rprx-vision")

    if not server or not uuid or not public_key:
        return ""

    # URL encode params if needed, but usually basic alpha-numeric
    import urllib.parse

    params = {
        "security": "reality",
        "encryption": "none",
        "pbk": public_key,
        "fp": fingerprint,
        "type": "tcp",
        "flow": flow,
        "sni": sni,
        "sid": short_id,
    }

    # Construct query string
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])

    # Construct comment (server name)
    comment_enc = urllib.parse.quote(comment)

    link = f"vless://{uuid}@{server}:{port}?{query_string}#{comment_enc}"
    return link


def generate_vless_link_for_uuid(client_uuid: str, comment: str) -> str:
    """
    Генерация ссылки vless:// для заданного UUID.
    """
    _reconcile_reality_from_live_xray()
    config = _load_config()

    server = config.get("server", "")
    port = config.get("port", 443)
    public_key = config.get("public_key", "")
    short_id = config.get("short_id", "")
    sni = config.get("sni", "www.microsoft.com")
    fingerprint = config.get("fingerprint", "chrome")
    flow = config.get("flow", "xtls-rprx-vision")

    if not server or not client_uuid or not public_key:
        return ""

    import urllib.parse

    params = {
        "security": "reality",
        "encryption": "none",
        "pbk": public_key,
        "fp": fingerprint,
        "type": "tcp",
        "flow": flow,
        "sni": sni,
        "sid": short_id,
    }

    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    comment_enc = urllib.parse.quote(comment)
    return f"vless://{client_uuid}@{server}:{port}?{query_string}#{comment_enc}"


def get_client(name_or_uuid: str) -> Optional[Dict]:
    """
    Найти клиента VLESS по имени или UUID.
    """
    if not name_or_uuid or not name_or_uuid.strip():
        return None

    needle = name_or_uuid.strip()
    for client in list_clients():
        if client.get("name") == needle or client.get("uuid") == needle:
            return client
    return None


def generate_client_link(name_or_uuid: str) -> Tuple[bool, str, str]:
    """
    Сгенерировать vless:// ссылку для конкретного клиента.
    """
    _reconcile_reality_from_live_xray()
    client = get_client(name_or_uuid)
    if not client:
        return False, "❌ Клиент не найден", ""

    client_name = client.get("name") or "client"
    client_uuid = client.get("uuid") or ""
    if not client_uuid:
        return False, f"❌ У клиента {client_name} отсутствует UUID", ""

    config = _load_config()
    if not (config.get("server") or "").strip():
        return (
            False,
            "❌ В конфиге VLESS не задан `server` (IP или домен).\n"
            "Выполните `/vless_set_server` с IP или доменом сервера, либо `/vless_sync` "
            "после настройки Xray.",
            "",
        )
    if not (config.get("public_key") or "").strip():
        return (
            False,
            "❌ Нет Reality `public_key` в конфиге бота.\n"
            "Сгенерируйте ключи: `/vless_gen_keys` или подтяните из Xray: `/vless_sync`.",
            "",
        )

    link = generate_vless_link_for_uuid(
        client_uuid,
        visible_profile_name("VLESS", config.get("server", ""), client_name),
    )
    if not link:
        return (
            False,
            "❌ Не удалось сгенерировать VLESS ссылку. Проверьте настройки сервера, UUID и Public Key",
            "",
        )

    return True, f"✅ Ссылка для клиента {client_name} готова", link


def generate_qr_png_bytes(content: str) -> Tuple[bool, Optional[BytesIO], str]:
    """
    Сгенерировать QR-код PNG в памяти.
    """
    if not content or not content.strip():
        return False, None, "❌ Нечего кодировать в QR"

    try:
        import qrcode
    except ImportError:
        return (
            False,
            None,
            "❌ Библиотека qrcode не установлена. Обновите зависимости проекта",
        )

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
        logger.error(f"Failed to generate QR image: {e}")
        return False, None, f"❌ Ошибка генерации QR: {e}"


def build_client_qr_payload(name_or_uuid: str) -> Tuple[bool, str, Dict]:
    """
    Подготовить данные клиента для отправки QR-кода через Telegram.
    """
    client = get_client(name_or_uuid)
    if not client:
        return False, "❌ Клиент не найден", {}

    success, message, link = generate_client_link(name_or_uuid)
    if not success:
        return False, message, {}

    success, qr_buffer, qr_message = generate_qr_png_bytes(link)
    if not success or qr_buffer is None:
        return False, qr_message, {}

    payload = {
        "name": client.get("name") or "client",
        "uuid": client.get("uuid") or "",
        "link": link,
        "qr_buffer": qr_buffer,
    }
    return True, "✅ QR-пакет для клиента подготовлен", payload


def list_clients() -> List[Dict]:
    """
    Получить список клиентов VLESS.
    """
    config = _load_config()
    _normalize_clients(config)
    return config.get("clients", [])


def add_client(name: str, client_uuid: Optional[str] = None) -> Tuple[bool, str, Dict]:
    """
    Добавить клиента VLESS.
    """
    if not name or not name.strip():
        return False, "❌ Имя клиента не может быть пустым", {}

    name = name.strip()
    config = _load_config()
    _normalize_clients(config)

    for client in config.get("clients", []):
        if client.get("name") == name:
            return False, f"❌ Клиент с именем {name} уже существует", {}

    if not client_uuid:
        client_uuid = generate_uuid()

    client = {
        "name": name,
        "uuid": client_uuid,
        "created_at": datetime.now().isoformat(),
    }

    config["clients"].append(client)
    if _save_config(config):
        return True, f"✅ Клиент добавлен: {name}", client
    return False, "❌ Ошибка при сохранении", {}


def remove_client(name_or_uuid: str) -> Tuple[bool, str]:
    """
    Удалить клиента по имени или UUID.
    """
    if not name_or_uuid or not name_or_uuid.strip():
        return False, "❌ Укажите имя или UUID клиента"

    name_or_uuid = name_or_uuid.strip()
    config = _load_config()
    _normalize_clients(config)

    if name_or_uuid == "default":
        return False, "❌ Нельзя удалить default клиента"

    clients = config.get("clients", [])
    new_clients = [
        c
        for c in clients
        if c.get("name") != name_or_uuid and c.get("uuid") != name_or_uuid
    ]

    if len(new_clients) == len(clients):
        return False, "❌ Клиент не найден"

    config["clients"] = new_clients
    if _save_config(config):
        return True, "✅ Клиент удалён"
    return False, "❌ Ошибка при сохранении"


def export_subscription_list() -> List[str]:
    """
    Сформировать список ссылок для subscription (raw list).
    """
    links = []
    clients = list_clients()
    for client in clients:
        name = client.get("name") or "client"
        client_uuid = client.get("uuid") or ""
        link = generate_vless_link_for_uuid(
            client_uuid,
            visible_profile_name("VLESS", config.get("server", ""), name),
        )
        if link:
            links.append(link)
    return links


def export_subscription_base64() -> str:
    """
    Сформировать subscription в base64 (как у большинства клиентов).
    """
    import base64

    links = export_subscription_list()
    raw = "\n".join([x for x in links if x]).strip()
    if not raw:
        return ""
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")




def export_singbox_config() -> Dict:
    """
    Сгенерировать минимальную конфигурацию sing-box (client).
    """
    config = _load_config()

    return {
        "log": {"level": "warn"},
        "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": 1080}],
        "outbounds": [
            {
                "type": "vless",
                "server": config.get("server", ""),
                "server_port": config.get("port", 443),
                "uuid": config.get("uuid", ""),
                "flow": config.get("flow", "xtls-rprx-vision"),
                "tls": {
                    "enabled": True,
                    "server_name": config.get("sni", "www.microsoft.com"),
                    "reality": {
                        "enabled": True,
                        "public_key": config.get("public_key", ""),
                        "short_id": config.get("short_id", ""),
                    },
                    "utls": {
                        "enabled": True,
                        "fingerprint": config.get("fingerprint", "chrome"),
                    },
                },
            }
        ],
    }


def export_clash_meta_config() -> str:
    """
    Сгенерировать минимальную конфигурацию Clash Meta (YAML).
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 443)
    uuid = config.get("uuid", "")
    sni = config.get("sni", "www.microsoft.com")
    fingerprint = config.get("fingerprint", "chrome")
    public_key = config.get("public_key", "")
    short_id = config.get("short_id", "")
    flow = config.get("flow", "xtls-rprx-vision")

    lines = [
        "port: 7890",
        "socks-port: 7891",
        "mixed-port: 7892",
        "mode: rule",
        "log-level: info",
        "",
        "proxies:",
        "  - name: TelegramSimple-VLESS",
        "    type: vless",
        f"    server: {server}",
        f"    port: {port}",
        f"    uuid: {uuid}",
        f"    flow: {flow}",
        "    network: tcp",
        "    tls: true",
        f"    servername: {sni}",
        f"    client-fingerprint: {fingerprint}",
        "    reality-opts:",
        f"      public-key: {public_key}",
        f"      short-id: {short_id}",
        "",
        "proxy-groups:",
        "  - name: Proxy",
        "    type: select",
        "    proxies:",
        "      - TelegramSimple-VLESS",
        "",
        "rules:",
        "  - MATCH,Proxy",
    ]

    return "\n".join(lines).strip()


def export_xray_config(is_server: bool = False) -> Dict:
    """
    Генерация конфигурации для Xray-core

    Args:
        is_server: True для серверной конфигурации, False для клиентской

    Returns:
        Dict с конфигурацией Xray
    """
    config = _load_config()

    if is_server:
        # Серверная конфигурация
        _normalize_clients(config)
        clients = config.get("clients", [])
        if not clients and config.get("uuid"):
            clients = [
                {
                    "id": config.get("uuid", ""),
                    "flow": config.get("flow", "xtls-rprx-vision"),
                    "email": "default",
                }
            ]
        else:
            clients = [
                {
                    "id": c.get("uuid", ""),
                    "flow": config.get("flow", "xtls-rprx-vision"),
                    "email": c.get("name", ""),
                }
                for c in clients
                if c.get("uuid")
            ]

        # Build fallbacks list dynamically
        fallbacks = []
        if config.get("nginx_fallback_enabled", False):
            nginx_port = config.get("nginx_fallback_port", 8443)
            # Nginx SNI router as primary fallback (xver=1 for PROXY protocol)
            fallbacks.append({"dest": f"127.0.0.1:{nginx_port}", "xver": 1})
        # Default fallback — TelegramSimple FastAPI
        fallbacks.append({"dest": "127.0.0.1:8000", "xver": 0})

        return {
            "log": {"loglevel": "warning"},
            # NB: НЕ блокировать ::/0 через blackhole. Reality на каждое
            # соединение дозванивается до dest (serverNames[0]:443) ради
            # настоящего TLS; если dest резолвится в IPv6, blackhole рубил
            # этот relay → Xray браковал ВСЕХ клиентов как "invalid connection".
            # Отсутствие IPv6-выхода корректно решает freedom/UseIPv4 ниже
            # (+ sniffing destOverride на inbound для литеральных IPv6 от клиента).
            "inbounds": [
                {
                    "port": config.get("port", 443),
                    "protocol": "vless",
                    "settings": {
                        "clients": clients,
                        "decryption": "none",
                        "fallbacks": fallbacks,
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "show": False,
                            "dest": f"{config.get('sni', 'www.microsoft.com')}:443",
                            "xver": 0,
                            "serverNames": [config.get("sni", "www.microsoft.com")],
                            "privateKey": config.get("private_key", ""),
                            "shortIds": [config.get("short_id", "")],
                        },
                    },
                    # Этот сервер без IPv6-выхода: routing ниже блэкхолит весь ::/0.
                    # Без destOverride клиент (Clash Meta, Karing, HAPP) шлёт литеральный
                    # IPv6 назначения (например claude.ai) → он матчит ::/0 → blackhole →
                    # клиент получает EOF на IPv6-сайтах. Со sniffing литеральный IP
                    # подменяется доменом из SNI, мимо ::/0, и freedom/UseIPv4 выходит
                    # по IPv4. routeOnly=False — чтобы подмена влияла и на дозвон, не
                    # только на маршрутизацию.
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls", "quic"],
                        "routeOnly": False,
                    },
                }
            ],
            "outbounds": [
                {
                    "protocol": "freedom",
                    "tag": "direct",
                    "settings": {"domainStrategy": "UseIPv4"},
                }
            ],
        }
    else:
        # Клиентская конфигурация
        return {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {"port": 1080, "protocol": "socks", "settings": {"udp": True}}
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": config.get("server", ""),
                                "port": config.get("port", 443),
                                "users": [
                                    {
                                        "id": config.get("uuid", ""),
                                        "flow": config.get("flow", "xtls-rprx-vision"),
                                        "encryption": "none",
                                    }
                                ],
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "serverName": config.get("sni", "www.microsoft.com"),
                            "fingerprint": config.get("fingerprint", "chrome"),
                            "publicKey": config.get("public_key", ""),
                            "shortId": config.get("short_id", ""),
                            "spiderX": "",
                        },
                    },
                }
            ],
        }


def sync_from_xray_config(
    xray_config_path: str = "/usr/local/etc/xray/config.json",
) -> Tuple[bool, str]:
    """
    Синхронизировать ключи из конфига xray в vless_config.json

    Читает privateKey из xray config и генерирует соответствующий public_key.
    Это нужно когда xray использует другие ключи чем vless_config.json.

    Args:
        xray_config_path: Путь к конфигу xray

    Returns:
        Tuple[success, message]
    """
    # Проверяем существует ли файл
    if not os.path.exists(xray_config_path):
        return False, f"❌ Файл не найден: {xray_config_path}"

    try:
        with open(xray_config_path, "r", encoding="utf-8") as f:
            xray_config = json.load(f)
    except Exception as e:
        return False, f"❌ Ошибка чтения xray config: {e}"

    # Ищем privateKey в структуре xray конфига
    private_key = None
    short_ids = []
    server_names = []
    uuid = None
    port = None

    try:
        # Пытаемся найти в inbounds -> streamSettings -> realitySettings
        inbounds = xray_config.get("inbounds", [])
        for inbound in inbounds:
            if inbound.get("protocol") == "vless":
                port = inbound.get("port", port)

                # Получаем UUID из clients
                settings = inbound.get("settings", {})
                clients = settings.get("clients", [])
                if clients:
                    uuid = clients[0].get("id")

                stream = inbound.get("streamSettings", {})
                reality = stream.get("realitySettings", {})
                if reality:
                    private_key = reality.get("privateKey")
                    short_ids = reality.get("shortIds", [])
                    server_names = reality.get("serverNames", [])
                    break
    except Exception as e:
        return False, f"❌ Ошибка парсинга xray config: {e}"

    if not private_key:
        return False, "❌ privateKey не найден в xray config"

    # Генерируем public_key из private_key
    public_key = None

    # Метод 1: Используем xray x25519
    try:
        result = subprocess.run(
            ["xray", "x25519", "-i", private_key],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            for line in output.split("\n"):
                # Public key отображается как "Public key:" - это Password
                if "Public key:" in line:
                    public_key = line.split(":", 1)[1].strip()
                    break
    except Exception as e:
        logger.warning(f"xray x25519 failed: {e}")

    # Метод 2: Fallback - используем Python cryptography (для Docker)
    if not public_key:
        try:
            import base64

            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.x25519 import (
                X25519PrivateKey,
            )

            # Decode private key from base64
            private_key_padded = private_key + "=" * (4 - len(private_key) % 4)
            private_bytes = base64.urlsafe_b64decode(private_key_padded)

            # Create private key object and derive public key
            private_key_obj = X25519PrivateKey.from_private_bytes(private_bytes)
            public_key_obj = private_key_obj.public_key()

            # Encode public key to base64
            public_bytes = public_key_obj.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            public_key = (
                base64.urlsafe_b64encode(public_bytes).decode("utf-8").rstrip("=")
            )

            logger.info(f"Generated public_key using Python cryptography")
        except ImportError:
            logger.warning("cryptography library not available")
        except Exception as e:
            logger.warning(f"Python cryptography failed: {e}")

    if not public_key:
        return (
            False,
            "❌ Не удалось получить public_key. Установите cryptography: pip install cryptography",
        )

    # Обновляем vless_config.json
    config = _load_config()

    updated_fields = []

    if config.get("private_key") != private_key:
        config["private_key"] = private_key
        updated_fields.append("private_key")

    if config.get("public_key") != public_key:
        config["public_key"] = public_key
        updated_fields.append("public_key")

    if uuid and config.get("uuid") != uuid:
        config["uuid"] = uuid
        updated_fields.append("uuid")

    if short_ids and config.get("short_id") != short_ids[0]:
        config["short_id"] = short_ids[0]
        updated_fields.append("short_id")

    if server_names and config.get("sni") != server_names[0]:
        config["sni"] = server_names[0]
        updated_fields.append("sni")

    if port and config.get("port") != port:
        config["port"] = port
        updated_fields.append("port")

    if not updated_fields:
        return True, "✅ Конфигурация уже синхронизирована"

    if _save_config(config):
        fields_str = ", ".join(updated_fields)
        logger.info(f"Synced from xray config: {fields_str}")
        return (
            True,
            f"✅ Синхронизировано из xray config:\n{fields_str}\n\n🔑 Public Key (для клиента):\n`{public_key}`",
        )

    return False, "❌ Ошибка сохранения конфигурации"


def reset_config() -> Tuple[bool, str]:
    """Сброс конфигурации VLESS к дефолтным значениям"""
    if _save_config(dict(DEFAULT_CONFIG)):
        return True, "✅ Конфигурация VLESS сброшена"
    return False, "❌ Ошибка при сбросе конфигурации"


# === Xray Management ===


def check_xray_installed() -> Tuple[bool, str, Dict]:
    """
    Проверить, установлен ли Xray на сервере.

    Returns:
        Tuple[installed, message, info_dict]
    """
    info = {
        "installed": False,
        "version": None,
        "running": False,
        "port_listening": False,
        "config_exists": False,
        "in_docker": False,
    }

    # Проверяем, работаем ли в Docker
    in_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER")
    info["in_docker"] = in_docker

    if in_docker:
        # Из Docker не можем проверить Xray на хосте
        # Попробуем проверить порт 443 через внешний IP (не localhost)
        port_open = False
        server_ip = None

        # Получаем внешний IP сервера
        try:
            server_ip = get_server_public_ip()
        except:
            pass

        if server_ip:
            try:
                import socket

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex((server_ip, 443))
                port_open = result == 0
                sock.close()
            except:
                pass

        info["port_listening"] = port_open

        port_status = "✅ открыт" if port_open else "⚠️ недоступен"
        # Escape dots in IP for Markdown V2
        escaped_ip = server_ip.replace(".", "\\.") if server_ip else None
        server_info = f" \\({escaped_ip}\\)" if escaped_ip else ""

        message = f"""📦 *Статус Xray* \\(из Docker\\)

🔌 Порт 443{server_info}: {port_status}

_Бот работает в Docker\\._

*Проверьте через SSH:*
`systemctl status xray`
`ss \\-tlnp \\| grep 443`"""

        return port_open, message, info

    # Проверяем наличие xray (если не в Docker)
    try:
        result = subprocess.run(
            ["which", "xray"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return False, "❌ Xray не установлен", info

        info["installed"] = True
    except Exception as e:
        return False, f"❌ Ошибка проверки: {e}", info

    # Получаем версию
    try:
        result = subprocess.run(
            ["xray", "version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Парсим версию из вывода
            lines = result.stdout.strip().split("\n")
            if lines:
                info["version"] = lines[0]
    except Exception:
        pass

    # Проверяем systemd статус
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "xray"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        info["running"] = result.stdout.strip() == "active"
    except Exception:
        pass

    # Проверяем порт 443
    try:
        result = subprocess.run(
            ["ss", "-tlnp"], capture_output=True, text=True, timeout=5
        )
        info["port_listening"] = ":443" in result.stdout
    except Exception:
        pass

    # Проверяем наличие конфига
    config_path = "/usr/local/etc/xray/config.json"
    info["config_exists"] = os.path.exists(config_path)

    # Формируем сообщение
    status_emoji = "🟢" if info["running"] else "🔴"
    port_emoji = "✅" if info["port_listening"] else "❌"
    config_emoji = "✅" if info["config_exists"] else "❌"

    message = f"""📦 *Статус Xray*

{status_emoji} Установлен: ✅
📌 Версия: `{info["version"] or "неизвестно"}`
⚡ Запущен: {"✅" if info["running"] else "❌"}
🔌 Порт 443: {port_emoji}
📄 Конфиг: {config_emoji}"""

    return True, message, info


def get_xray_config() -> Tuple[bool, str, dict]:
    """
    Получить текущую конфигурацию Xray с сервера.

    Returns:
        Tuple[success, message, config_dict]
    """
    config_path = "/usr/local/etc/xray/config.json"

    # Проверяем, работаем ли в Docker
    in_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER")

    if in_docker:
        # Из Docker показываем инструкции для SSH
        message = """📄 *Конфигурация Xray*

_Бот работает в Docker и не имеет доступа к файлам хоста\\._

**Проверьте конфигурацию через SSH:**
```
cat /usr/local/etc/xray/config\\.json
```

**Или откройте для редактирования:**
```
nano /usr/local/etc/xray/config\\.json
```

**После изменений перезапустите:**
```
xray \\-test \\-config /usr/local/etc/xray/config\\.json
systemctl restart xray
```"""
        return True, message, {"in_docker": True}

    # Если не в Docker - пробуем прочитать файл
    if not os.path.exists(config_path):
        return False, "❌ Конфигурация не найдена: " + config_path, {}

    try:
        with open(config_path, "r") as f:
            config = json.load(f)

        # Форматируем основные параметры
        inbounds = config.get("inbounds", [])

        info_lines = ["📄 *Текущая конфигурация Xray*\n"]

        for i, inbound in enumerate(inbounds):
            port = inbound.get("port", "N/A")
            protocol = inbound.get("protocol", "N/A")
            tag = inbound.get("tag", f"inbound-{i}")

            info_lines.append(f"**{tag}:** {protocol} на порту {port}")

            # Reality настройки
            stream = inbound.get("streamSettings", {})
            reality = stream.get("realitySettings", {})
            if reality:
                dest = reality.get("dest", "N/A")
                sni = (
                    reality.get("serverNames", ["N/A"])[0]
                    if reality.get("serverNames")
                    else "N/A"
                )
                info_lines.append(f"  • SNI: `{sni}`")
                info_lines.append(f"  • Dest: `{dest}`")

        message = "\n".join(info_lines)
        return True, message, config

    except Exception as e:
        return False, f"❌ Ошибка чтения конфигурации: {e}", {}


def install_xray() -> Tuple[bool, str]:
    """
    Установить Xray на сервер.

    Returns:
        Tuple[success, message]
    """
    try:
        # Проверяем, не установлен ли уже
        result = subprocess.run(["which", "xray"], capture_output=True, timeout=5)
        if result.returncode == 0:
            return True, "✅ Xray уже установлен"

        # Проверяем, работаем ли в Docker (curl может быть недоступен)
        in_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER")

        if in_docker:
            return (
                False,
                """❌ Установка из Docker контейнера невозможна

**Установите Xray вручную через SSH:**

```bash
ssh root@<IP_СЕРВЕРА>

bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

xray version
```

После установки используйте:
/xray\\_apply — применить конфигурацию
/xray\\_start — запустить""",
            )

        logger.info("Installing Xray...")

        # Скачиваем и запускаем установщик
        install_cmd = 'bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install'

        result = subprocess.run(
            install_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5 минут на установку
        )

        if result.returncode != 0:
            logger.error(f"Xray install failed: {result.stderr}")
            error_msg = result.stderr[:300] if result.stderr else "Неизвестная ошибка"
            return (
                False,
                f"""❌ Ошибка установки Xray

**Установите вручную через SSH:**
```bash
ssh root@<IP_СЕРВЕРА>
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
```

Ошибка: {error_msg}""",
            )

        logger.info("Xray installed successfully")
        return (
            True,
            "✅ Xray успешно установлен!\n\nТеперь выполните:\n/xray_apply — применить конфигурацию\n/xray_start — запустить",
        )

    except subprocess.TimeoutExpired:
        return False, "❌ Таймаут установки (5 минут)"
    except Exception as e:
        logger.error(f"Error installing Xray: {e}")
        return False, f"❌ Ошибка: {e}"


def _xray_service_group() -> Optional[str]:
    """Эффективная группа сервиса Xray (для прав на config.json).

    Официальный systemd-юнит запускает Xray под `User=nobody` БЕЗ явного `Group=`,
    поэтому берём primary-группу пользователя (на Debian/Ubuntu это `nogroup`).
    Возвращает None, если определить не удалось — тогда caller оставляет конфиг
    world-readable (0644), чтобы НЕ лишить Xray доступа к нему.
    """
    try:
        g = _host_run(
            ["systemctl", "show", "-p", "Group", "--value", "xray"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        grp = (g.stdout or "").strip()
        if grp:
            return grp
        u = _host_run(
            ["systemctl", "show", "-p", "User", "--value", "xray"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        user = (u.stdout or "").strip() or "nobody"
        r = _host_run(["id", "-gn", user], capture_output=True, text=True, timeout=10)
        grp = (r.stdout or "").strip()
        return grp or None
    except Exception:
        return None


def _restrict_xray_config(path: str) -> None:
    """Сузить world-read приватного Reality-ключа в config.json до группы сервиса.

    Fail-safe: тигнем до 0640 ТОЛЬКО если уверенно определили группу и `chgrp`
    прошёл. Иначе оставляем 0644 — лучше чуть шире права, чем сломать чтение
    конфига сервисом (Xray бежит под nobody без явного Group=).
    """
    grp = _xray_service_group()
    if not grp:
        return
    try:
        r = _host_run(["chgrp", grp, path], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            _host_run(
                ["chmod", "640", path], capture_output=True, text=True, timeout=10
            )
        else:
            logger.warning(
                f"VLESS: chgrp {grp} {path} не прошёл ({(r.stderr or '').strip()}); "
                f"оставляю 0644"
            )
    except Exception as e:
        logger.warning(f"VLESS: не удалось сузить права на {path}: {e}")


def apply_xray_config() -> Tuple[bool, str]:
    """
    Применить текущую VLESS конфигурацию к Xray серверу.

    Returns:
        Tuple[success, message]
    """
    config_path = "/usr/local/etc/xray/config.json"

    try:
        # Генерируем серверную конфигурацию
        xray_config = export_xray_config(is_server=True)

        # Проверяем что есть необходимые данные
        vless_config = _load_config()
        if not vless_config.get("uuid") or not vless_config.get("private_key"):
            return False, "❌ Сначала сгенерируйте ключи: /vless_gen_keys"

        config_json = json.dumps(xray_config, indent=2, ensure_ascii=False)
        # Пишем 0644 (гарантированно читаемо сервисом), затем fail-safe сужаем до
        # 0640+chgrp, если удаётся определить группу сервиса (приватный Reality-key
        # не должен быть world-readable). При неудаче остаётся 0644.
        write_result = _host_write_text(config_path, config_json, mode="0644")
        if write_result.returncode != 0:
            error = write_result.stderr.strip() or write_result.stdout.strip()
            return False, f"❌ Не удалось записать host config Xray:\n`{error}`"

        _restrict_xray_config(config_path)

        result = _host_run(
            ["xray", "-test", "-config", config_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False, f"❌ Ошибка в конфигурации:\n```\n{result.stderr[:500]}\n```"

        return True, f"✅ Конфигурация Xray записана и проверена\n\n📄 `{config_path}`"

    except OSError as e:
        # Директория недоступна (volume не подключён)
        logger.error(f"Cannot write xray config: {e}")
        return False, (
            "❌ Не удалось записать конфиг Xray\n\n"
            "Возможно, volume `/usr/local/etc/xray` не подключён.\n"
            "Примени конфиг вручную: `/vless_export` → Xray Server Config"
        )
    except Exception as e:
        logger.error(f"Error applying Xray config: {e}")
        return False, f"❌ Ошибка: {e}"


def start_xray() -> Tuple[bool, str]:
    """Запустить Xray сервис"""
    # Проверяем Docker
    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER"):
        return (
            False,
            "❌ Из Docker нет доступа к systemctl\n\nНа сервере: `systemctl start xray`",
        )

    try:
        # Включаем автозапуск
        subprocess.run(["systemctl", "enable", "xray"], capture_output=True, timeout=10)

        # Запускаем
        result = subprocess.run(
            ["systemctl", "start", "xray"], capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            return False, f"❌ Ошибка запуска:\n```\n{result.stderr}\n```"

        return True, "✅ Xray запущен!"

    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def stop_xray() -> Tuple[bool, str]:
    """Остановить Xray сервис"""
    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER"):
        return (
            False,
            "❌ Из Docker нет доступа к systemctl\n\nНа сервере: `systemctl stop xray`",
        )

    try:
        result = subprocess.run(
            ["systemctl", "stop", "xray"], capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            return False, f"❌ Ошибка остановки:\n```\n{result.stderr}\n```"

        return True, "🔴 Xray остановлен"

    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def restart_xray() -> Tuple[bool, str]:
    """Перезапустить Xray сервис"""
    try:
        result = _host_run(
            ["systemctl", "restart", "xray"], capture_output=True, text=True, timeout=15
        )

        if result.returncode != 0:
            return False, f"❌ Ошибка перезапуска:\n```\n{result.stderr}\n```"

        # Проверяем статус
        import time

        time.sleep(1)

        result = _host_run(
            ["systemctl", "is-active", "xray"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.stdout.strip() == "active":
            return True, "✅ Xray перезапущен и работает!"
        else:
            return (
                False,
                "⚠️ Xray перезапущен, но не активен. Проверьте логи: /xray_logs",
            )

    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def get_xray_logs(lines: int = 30) -> Tuple[bool, str]:
    """Получить последние логи Xray"""
    try:
        result = subprocess.run(
            ["journalctl", "-u", "xray", "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        logs = result.stdout.strip()
        if not logs:
            return True, "📋 Логи пусты"

        # Обрезаем если слишком длинные
        if len(logs) > 3500:
            logs = logs[-3500:]

        return True, f"📋 *Логи Xray \\(последние {lines}\\):*\n```\n{logs}\n```"

    except Exception as e:
        return False, f"❌ Ошибка: {e}"
