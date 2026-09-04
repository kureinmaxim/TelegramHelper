# -*- coding: utf-8 -*-
"""
Модуль для управления MTProto proxy конфигурацией.

MTProto Proxy — официальный прокси-протокол Telegram.
Использует C-реализацию https://github.com/TelegramMessenger/MTProxy
Поддерживает fake-TLS режим для обхода DPI.

Структура конфигурации:
{
    "enabled": false,
    "server": "IP VPS",
    "port": 993,
    "secret_mode": "ee_split",
    "secret": "ee<32_hex_random><hex_domain>",
    "fake_tls_domain": "google.com",
    "tag": "",
    "workers": 2,
    "clients": [],
    "created_at": null,
    "updated_at": null
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

# Thread safety
_mt_lock = threading.Lock()

# Путь к файлу конфигурации MTProto
_MT_CONFIG_PATH = os.getenv("MTPROTO_CONFIG_PATH",
                             os.path.join(os.getcwd(), "mtproto_config.json"))

# Рекомендуемые порты для MTProto proxy
# - 993: IMAPS — выглядит как шифрованная почта
# - 465: SMTPS — ещё один почтовый порт
# - 8443: Альтернативный HTTPS
RECOMMENDED_PORTS = [993, 465, 8443]

# Домены для fake-TLS маскировки
AVAILABLE_FAKE_TLS_DOMAINS = [
    "google.com",
    "www.google.com",
    "cloudflare.com",
    "microsoft.com",
    "apple.com",
    "amazon.com",
]

SECRET_MODE_DD_INLINE = "dd_inline"
SECRET_MODE_EE_SPLIT = "ee_split"
AVAILABLE_SECRET_MODES = (SECRET_MODE_DD_INLINE, SECRET_MODE_EE_SPLIT)
DEFAULT_SECRET_MODE = SECRET_MODE_EE_SPLIT
DEFAULT_FAKE_TLS_DOMAIN = "google.com"

# Системные пути
MTPROXY_BINARY_PATH = "/usr/local/bin/mtproto-proxy"
MTPROXY_CONFIG_DIR = "/etc/mtproto-proxy"
PROXY_SECRET_PATH = "/etc/mtproto-proxy/proxy-secret"
PROXY_MULTI_CONF_PATH = "/etc/mtproto-proxy/proxy-multi.conf"
SYSTEMD_SERVICE_NAME = "mtproto-proxy"
SYSTEMD_UNIT_PATH = f"/etc/systemd/system/{SYSTEMD_SERVICE_NAME}.service"
# Внутренний порт для статистики (только localhost)
STATS_PORT = 2398

# Дефолтная конфигурация
DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",
    "port": 993,
    "secret_mode": DEFAULT_SECRET_MODE,
    "secret": "",
    "fake_tls_domain": DEFAULT_FAKE_TLS_DOMAIN,
    "tag": "",
    "workers": 2,
    "clients": [],
    "created_at": None,
    "updated_at": None,
}


# === Secret Generation ===

def _normalize_secret_mode(secret_mode: Optional[str]) -> str:
    """Нормализовать имя режима секрета."""
    if not secret_mode:
        return DEFAULT_SECRET_MODE

    normalized = str(secret_mode).strip().lower()
    aliases = {
        "dd": SECRET_MODE_DD_INLINE,
        "legacy": SECRET_MODE_DD_INLINE,
        "inline": SECRET_MODE_DD_INLINE,
        "ee": SECRET_MODE_EE_SPLIT,
        "compat": SECRET_MODE_EE_SPLIT,
        "split": SECRET_MODE_EE_SPLIT,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in AVAILABLE_SECRET_MODES:
        return normalized
    return DEFAULT_SECRET_MODE


def _is_hex_string(value: str, exact_length: Optional[int] = None) -> bool:
    """Проверить, что строка является hex."""
    if not value:
        return False
    if exact_length is not None and len(value) != exact_length:
        return False
    try:
        bytes.fromhex(value)
        return True
    except ValueError:
        return False


def _encode_domain_hex(domain: str) -> str:
    """Кодировать ASCII домен в hex."""
    return domain.encode("ascii").hex()


def _decode_domain_hex(domain_hex: str) -> str:
    """Декодировать hex домена в ASCII."""
    if not domain_hex:
        return ""
    return bytes.fromhex(domain_hex).decode("ascii")


def _secret_mode_label(secret_mode: str) -> str:
    """Человекочитаемое имя режима."""
    mode = _normalize_secret_mode(secret_mode)
    if mode == SECRET_MODE_EE_SPLIT:
        return "ee + -D"
    return "dd inline"


def _docker_host_sync_hint() -> str:
    """Подсказка для Docker-сценария, когда systemd нужно обновлять на хосте."""
    if not _is_docker():
        return ""
    return (
        "\n\nℹ️ Бот запущен в Docker. После изменения MTProto-конфига "
        "синхронизируйте systemd на Debian-хосте:\n"
        "cd /opt/TelegramSimple && python3 scripts/mtproto_sync_systemd.py"
    )


def _build_client_secret(base_secret: str, domain: str, secret_mode: str) -> str:
    """Собрать клиентский MTProto secret для выбранного режима."""
    normalized_mode = _normalize_secret_mode(secret_mode)
    normalized_base = (base_secret or "").strip().lower()
    normalized_domain = (domain or DEFAULT_FAKE_TLS_DOMAIN).strip().lower()

    if not _is_hex_string(normalized_base, 32):
        return normalized_base

    if normalized_mode == SECRET_MODE_EE_SPLIT:
        return f"ee{normalized_base}{_encode_domain_hex(normalized_domain)}"
    return f"dd{normalized_base}{_encode_domain_hex(normalized_domain)}"


def _normalize_secret_for_mode(secret: str, secret_mode: str, domain: str) -> str:
    """Привести secret к клиентскому формату нужного режима."""
    if not secret:
        return ""

    normalized = secret.strip().lower()
    parsed = _parse_secret(normalized)
    normalized_mode = _normalize_secret_mode(secret_mode)
    target_domain = parsed.get("domain") or (domain or DEFAULT_FAKE_TLS_DOMAIN).strip().lower()

    if _is_hex_string(parsed.get("server_secret", ""), 32):
        if not parsed.get("prefix"):
            if normalized_mode == SECRET_MODE_DD_INLINE:
                return parsed["server_secret"]
        return _build_client_secret(parsed["server_secret"], target_domain, normalized_mode)

    return normalized


def _infer_secret_mode(config: Dict, file_exists: bool = False) -> str:
    """Определить режим секрета для legacy-конфигов."""
    explicit_mode = config.get("secret_mode")
    if explicit_mode:
        return _normalize_secret_mode(explicit_mode)

    candidate_secrets = []
    main_secret = str(config.get("secret") or "").strip().lower()
    if main_secret:
        candidate_secrets.append(main_secret)
    for client in config.get("clients") or []:
        secret = str((client or {}).get("secret") or "").strip().lower()
        if secret:
            candidate_secrets.append(secret)

    for secret in candidate_secrets:
        parsed = _parse_secret(secret)
        if parsed["mode"] in AVAILABLE_SECRET_MODES:
            return parsed["mode"]

    if file_exists:
        return SECRET_MODE_DD_INLINE
    return DEFAULT_SECRET_MODE


def _normalize_all_client_secrets(config: Dict) -> None:
    """Нормализовать все клиентские секреты под текущий режим."""
    clients = config.get("clients") or []
    normalized_clients = []
    target_mode = _normalize_secret_mode(config.get("secret_mode"))
    target_domain = (config.get("fake_tls_domain") or DEFAULT_FAKE_TLS_DOMAIN).strip().lower()

    for client in clients:
        if not isinstance(client, dict):
            continue
        name = str(client.get("name") or "").strip()
        if not name:
            continue

        secret = _normalize_secret_for_mode(str(client.get("secret") or ""), target_mode, target_domain)
        normalized_clients.append({
            "name": name,
            "secret": secret,
            "created_at": client.get("created_at") or datetime.now().isoformat(),
        })

    config["clients"] = normalized_clients


def _normalize_config(config: Dict, file_exists: bool = False) -> Dict:
    """Привести MTProto-конфиг к актуальной dual-mode структуре."""
    config["secret_mode"] = _infer_secret_mode(config, file_exists=file_exists)

    domain_hint = str(config.get("fake_tls_domain") or DEFAULT_FAKE_TLS_DOMAIN).strip().lower()
    for candidate in [str(config.get("secret") or "").strip().lower()] + [
        str((client or {}).get("secret") or "").strip().lower()
        for client in config.get("clients") or []
    ]:
        parsed = _parse_secret(candidate)
        if parsed.get("domain"):
            domain_hint = parsed["domain"]
            break
    config["fake_tls_domain"] = domain_hint or DEFAULT_FAKE_TLS_DOMAIN

    if config.get("secret"):
        config["secret"] = _normalize_secret_for_mode(
            str(config.get("secret") or ""),
            config["secret_mode"],
            config["fake_tls_domain"],
        )

    _normalize_all_client_secrets(config)
    _normalize_clients(config)
    return config

def generate_secret(domain: Optional[str] = None) -> str:
    """
    Сгенерировать MTProto proxy секрет для текущего режима.

    Форматы:
    - dd_inline: dd + 32_hex_random + hex(domain)
    - ee_split:  ee + 32_hex_random + hex(domain)

    Args:
        domain: Домен для fake-TLS. Если None — берётся из конфига.

    Returns:
        str: hex секрет
    """
    if not domain:
        config = _load_config()
        domain = config.get("fake_tls_domain", DEFAULT_FAKE_TLS_DOMAIN)
        secret_mode = config.get("secret_mode", DEFAULT_SECRET_MODE)
    else:
        config = _load_config()
        secret_mode = config.get("secret_mode", DEFAULT_SECRET_MODE)

    random_part = secrets.token_hex(16)  # 16 bytes = 32 hex chars
    return _build_client_secret(random_part, domain, secret_mode)


def _parse_secret(secret: str) -> Dict:
    """
    Разобрать MTProto proxy секрет.

    Returns:
        Dict: mode, is_fake_tls, prefix, server_secret, domain, raw
    """
    normalized = (secret or "").strip().lower()
    result = {
        "mode": "",
        "is_fake_tls": False,
        "prefix": "",
        "server_secret": "",
        "domain": "",
        "raw": normalized,
    }

    if not normalized:
        return result

    prefix = normalized[:2]
    if prefix in ("dd", "ee") and _is_hex_string(normalized[2:34], 32):
        result["prefix"] = prefix
        result["server_secret"] = normalized[2:34]
        result["mode"] = SECRET_MODE_DD_INLINE if prefix == "dd" else SECRET_MODE_EE_SPLIT
        result["is_fake_tls"] = len(normalized) > 34
        domain_hex = normalized[34:]
        if domain_hex:
            try:
                result["domain"] = _decode_domain_hex(domain_hex)
            except (ValueError, UnicodeDecodeError):
                result["domain"] = ""
        return result

    if _is_hex_string(normalized, 32):
        result["server_secret"] = normalized
        return result

    if _is_hex_string(normalized):
        result["server_secret"] = normalized

    return result


# === Config Load/Save ===

def _normalize_clients(config: Dict) -> None:
    """Нормализовать список клиентов MTProto."""
    clients = config.get("clients") or []
    if not isinstance(clients, list):
        clients = []

    # Если клиентов нет, но есть secret — создаём дефолтного клиента
    if not clients and config.get("secret"):
        clients = [{
            "name": "default",
            "secret": config.get("secret"),
            "created_at": datetime.now().isoformat()
        }]

    # Синхронизировать дефолтного клиента с config["secret"]
    if config.get("secret"):
        for client in clients:
            if client.get("name") == "default":
                client["secret"] = config.get("secret")
                break
        else:
            clients.append({
                "name": "default",
                "secret": config.get("secret"),
                "created_at": datetime.now().isoformat()
            })

    config["clients"] = clients


def _load_config() -> Dict:
    """Загрузить конфигурацию MTProto из файла."""
    with _mt_lock:
        if not os.path.exists(_MT_CONFIG_PATH):
            return _normalize_config(dict(DEFAULT_CONFIG), file_exists=False)

        try:
            with open(_MT_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = dict(DEFAULT_CONFIG)
                config.update(data)
                return _normalize_config(config, file_exists=True)
        except Exception as e:
            logger.error(f"Error loading MTProto config: {e}")
            return _normalize_config(dict(DEFAULT_CONFIG), file_exists=False)


def _save_config(config: Dict) -> bool:
    """Сохранить конфигурацию MTProto в файл."""
    with _mt_lock:
        try:
            config = _normalize_config(dict(config), file_exists=True)
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]

            directory = os.path.dirname(_MT_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)

            with open(_MT_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            return True
        except Exception as e:
            logger.error(f"Error saving MTProto config: {e}")
            return False


# === Public API ===

def is_enabled() -> bool:
    """Проверить, включён ли MTProto proxy."""
    config = _load_config()
    return config.get("enabled", False)


def enable() -> Tuple[bool, str]:
    """
    Включить MTProto proxy.

    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    config = _load_config()

    required = ["server", "secret"]
    missing = [key for key in required if not config.get(key)]

    if missing:
        return False, f"Не настроены обязательные параметры: {', '.join(missing)}"

    config["enabled"] = True
    if _save_config(config):
        logger.info("MTProto proxy enabled")
        return True, "✅ MTProto proxy включён"

    return False, "❌ Ошибка при сохранении конфигурации"


def disable() -> Tuple[bool, str]:
    """
    Выключить MTProto proxy.

    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    config = _load_config()
    config["enabled"] = False

    if _save_config(config):
        logger.info("MTProto proxy disabled")
        return True, "🔴 MTProto proxy выключен"

    return False, "❌ Ошибка при сохранении конфигурации"


def get_status() -> Dict:
    """
    Получить статус MTProto proxy.

    Returns:
        Dict с информацией о статусе
    """
    config = _load_config()

    required = ["server", "secret"]
    configured = all(config.get(key) for key in required)

    secret = config.get("secret", "")
    parsed = _parse_secret(secret) if secret else {}
    secret_mode = _normalize_secret_mode(config.get("secret_mode"))

    return {
        "enabled": config.get("enabled", False),
        "configured": configured,
        "server": config.get("server", ""),
        "port": config.get("port", 993),
        "secret_mode": secret_mode,
        "secret_mode_label": _secret_mode_label(secret_mode),
        "has_secret": bool(secret),
        "is_fake_tls": parsed.get("is_fake_tls", False),
        "fake_tls_domain": parsed.get("domain", "") or config.get("fake_tls_domain", ""),
        "tag": config.get("tag", ""),
        "workers": config.get("workers", 2),
        "clients_count": len(config.get("clients", [])),
        "updated_at": config.get("updated_at"),
    }


def get_config(include_secrets: bool = False) -> Dict:
    """
    Получить конфигурацию MTProto (опционально с секретами).

    Args:
        include_secrets: включать ли полные секреты

    Returns:
        Dict с конфигурацией
    """
    config = _load_config()

    if not include_secrets:
        if config.get("secret"):
            s = config["secret"]
            config["secret"] = f"{s[:6]}...{s[-6:]}" if len(s) > 12 else "***"
        for client in config.get("clients", []):
            if client.get("secret"):
                cs = client["secret"]
                client["secret"] = f"{cs[:6]}..." if len(cs) > 6 else "***"

    return config


def set_secret_mode(secret_mode: str) -> Tuple[bool, str]:
    """Переключить режим клиентских/серверных secret для MTProto."""
    requested_mode = str(secret_mode or "").strip().lower()
    if requested_mode not in {
        SECRET_MODE_DD_INLINE,
        SECRET_MODE_EE_SPLIT,
        "dd",
        "legacy",
        "inline",
        "ee",
        "compat",
        "split",
    }:
        return False, (
            "❌ Неизвестный режим. Используйте:\n"
            f"`{SECRET_MODE_DD_INLINE}` или `{SECRET_MODE_EE_SPLIT}`"
        )
    normalized_mode = _normalize_secret_mode(requested_mode)

    config = _load_config()
    old_mode = _normalize_secret_mode(config.get("secret_mode"))
    config["secret_mode"] = normalized_mode
    config = _normalize_config(config, file_exists=True)

    if _save_config(config):
        if old_mode == normalized_mode:
            return True, f"✅ Режим MTProto уже установлен: `{normalized_mode}`{_docker_host_sync_hint()}"

        return True, (
            f"✅ Режим MTProto переключён: `{old_mode}` → `{normalized_mode}`\n"
            f"Текущий формат клиентов: `{_secret_mode_label(normalized_mode)}`\n"
            "Если MTProto уже установлен на хосте, обновите его systemd unit."
            f"{_docker_host_sync_hint()}"
        )
    return False, "❌ Ошибка при сохранении"


# === Server IP ===

def get_server_public_ip() -> Optional[str]:
    """Получить публичный IP адрес сервера."""
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
    """
    Установить адрес сервера MTProto proxy.

    Args:
        server: IP или домен. Если None — автоопределение.
    """
    if not server or not server.strip():
        detected_ip = get_server_public_ip()
        if detected_ip:
            server = detected_ip
            auto_detected = True
        else:
            return False, "❌ Не удалось автоматически определить IP сервера\n\nИспользуйте: /mt_set_server <IP>"
    else:
        auto_detected = False

    config = _load_config()
    config["server"] = server.strip()

    if _save_config(config):
        if auto_detected:
            return True, f"✅ Сервер установлен автоматически: {server}{_docker_host_sync_hint()}"
        return True, f"✅ Сервер установлен: {server}{_docker_host_sync_hint()}"
    return False, "❌ Ошибка при сохранении"


def set_port(port: int) -> Tuple[bool, str]:
    """Установить порт сервера MTProto proxy."""
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False, "❌ Порт должен быть числом от 1 до 65535"

    config = _load_config()
    config["port"] = port

    recommended = "⭐ рекомендуемый" if port in RECOMMENDED_PORTS else ""
    if _save_config(config):
        return True, (
            f"✅ Порт установлен: {port} {recommended}\n"
            f"⚠️ Не забудьте открыть TCP порт: `ufw allow {port}/tcp`"
            f"{_docker_host_sync_hint()}"
        )
    return False, "❌ Ошибка при сохранении"


def set_secret(secret: Optional[str] = None, domain: Optional[str] = None) -> Tuple[bool, str]:
    """
    Установить или сгенерировать секрет MTProto proxy.

    Args:
        secret: Готовый секрет. Если None — генерируется автоматически.
        domain: Домен для fake-TLS (используется при генерации).
    """
    config = _load_config()
    fallback_domain = domain or config.get("fake_tls_domain", DEFAULT_FAKE_TLS_DOMAIN)

    if not secret:
        secret = generate_secret(fallback_domain)

    parsed = _parse_secret(secret)
    if parsed["mode"] in AVAILABLE_SECRET_MODES:
        config["secret_mode"] = parsed["mode"]

    config["secret"] = _normalize_secret_for_mode(
        secret,
        config.get("secret_mode", DEFAULT_SECRET_MODE),
        fallback_domain,
    )

    # Обновить fake_tls_domain из секрета
    parsed = _parse_secret(config["secret"])
    if parsed["is_fake_tls"] and parsed["domain"]:
        config["fake_tls_domain"] = parsed["domain"]
    elif fallback_domain:
        config["fake_tls_domain"] = fallback_domain

    _normalize_clients(config)

    if _save_config(config):
        parsed_info = _parse_secret(config["secret"])
        mode = "fake-TLS" if parsed_info["is_fake_tls"] else "простой"
        domain_info = f" ({parsed_info['domain']})" if parsed_info["domain"] else ""
        return True, (
            f"✅ Секрет установлен ({mode}{domain_info}, {len(config['secret'])} символов)\n"
            f"Режим: `{config.get('secret_mode', DEFAULT_SECRET_MODE)}`"
            f"{_docker_host_sync_hint()}"
        )
    return False, "❌ Ошибка при сохранении"


def set_fake_tls_domain(domain: str) -> Tuple[bool, str]:
    """
    Установить домен fake-TLS и перегенерировать секрет.

    Args:
        domain: ASCII домен для маскировки
    """
    if not domain or not domain.strip():
        return False, "❌ Домен не может быть пустым"

    domain = domain.strip().lower()

    # Проверка ASCII
    try:
        domain.encode("ascii")
    except UnicodeEncodeError:
        return False, "❌ Домен должен содержать только ASCII символы"

    config = _load_config()
    config["fake_tls_domain"] = domain

    if config.get("secret"):
        config["secret"] = _normalize_secret_for_mode(
            config["secret"],
            config.get("secret_mode", DEFAULT_SECRET_MODE),
            domain,
        )

    refreshed_clients = []
    for client in config.get("clients", []):
        refreshed = dict(client)
        if refreshed.get("secret"):
            refreshed["secret"] = _normalize_secret_for_mode(
                refreshed["secret"],
                config.get("secret_mode", DEFAULT_SECRET_MODE),
                domain,
            )
        refreshed_clients.append(refreshed)
    config["clients"] = refreshed_clients
    _normalize_clients(config)

    if _save_config(config):
        return True, (
            f"✅ Домен fake-TLS: {domain}\n"
            f"🔄 Секреты приведены к режиму `{config.get('secret_mode', DEFAULT_SECRET_MODE)}`"
            f"{_docker_host_sync_hint()}"
        )
    return False, "❌ Ошибка при сохранении"


def set_tag(tag: str) -> Tuple[bool, str]:
    """
    Установить статистический тег (hex строка).
    Тег используется для промоутирования прокси через @MTProxybot.
    """
    tag = tag.strip()

    # Валидация: должен быть hex строкой или пустым
    if tag:
        try:
            bytes.fromhex(tag)
        except ValueError:
            return False, "❌ Тег должен быть hex строкой (например: dcbe8f1493fa4cd973d)"

    config = _load_config()
    config["tag"] = tag

    if _save_config(config):
        if tag:
            return True, f"✅ Тег установлен: {tag}{_docker_host_sync_hint()}"
        return True, f"✅ Тег удалён{_docker_host_sync_hint()}"
    return False, "❌ Ошибка при сохранении"


def set_workers(workers: int) -> Tuple[bool, str]:
    """Установить количество воркеров (1-16)."""
    if not isinstance(workers, int) or workers < 1 or workers > 16:
        return False, "❌ Количество воркеров должно быть от 1 до 16"

    config = _load_config()
    config["workers"] = workers

    if _save_config(config):
        return True, f"✅ Воркеры: {workers}{_docker_host_sync_hint()}"
    return False, "❌ Ошибка при сохранении"


# === Clients ===

def list_clients() -> List[Dict]:
    """Получить список клиентов MTProto proxy."""
    config = _load_config()
    _normalize_clients(config)
    return config.get("clients", [])


def add_client(name: str, client_secret: Optional[str] = None) -> Tuple[bool, str, Dict]:
    """
    Добавить клиента MTProto proxy.

    Каждый клиент получает свой секрет (с тем же fake-TLS доменом).
    """
    if not name or not name.strip():
        return False, "❌ Имя клиента не может быть пустым", {}

    name = name.strip()
    config = _load_config()
    _normalize_clients(config)

    for client in config.get("clients", []):
        if client.get("name") == name:
            return False, f"❌ Клиент с именем {name} уже существует", {}

    if not client_secret:
        # Генерируем секрет с тем же доменом
        domain = config.get("fake_tls_domain", "google.com")
        client_secret = generate_secret(domain)
    else:
        client_secret = _normalize_secret_for_mode(
            client_secret,
            config.get("secret_mode", DEFAULT_SECRET_MODE),
            config.get("fake_tls_domain", DEFAULT_FAKE_TLS_DOMAIN),
        )

    client = {
        "name": name,
        "secret": client_secret,
        "created_at": datetime.now().isoformat()
    }

    config["clients"].append(client)
    if _save_config(config):
        return True, f"✅ Клиент добавлен: {name}{_docker_host_sync_hint()}", client
    return False, "❌ Ошибка при сохранении", {}


def remove_client(name_or_secret: str) -> Tuple[bool, str]:
    """Удалить клиента по имени или секрету."""
    if not name_or_secret or not name_or_secret.strip():
        return False, "❌ Укажите имя клиента"

    name_or_secret = name_or_secret.strip()
    config = _load_config()
    _normalize_clients(config)

    if name_or_secret == "default":
        return False, "❌ Нельзя удалить default клиента"

    clients = config.get("clients", [])
    new_clients = [
        c for c in clients
        if c.get("name") != name_or_secret and c.get("secret") != name_or_secret
    ]

    if len(new_clients) == len(clients):
        return False, "❌ Клиент не найден"

    config["clients"] = new_clients
    if _save_config(config):
        return True, f"✅ Клиент удалён{_docker_host_sync_hint()}"
    return False, "❌ Ошибка при сохранении"


# === Generate All ===

def generate_all() -> Tuple[bool, Dict, str]:
    """
    Сгенерировать всё: секрет + автоопределить IP.
    (TLS сертификат для MTProto не нужен.)

    Returns:
        Tuple[bool, Dict, str]: (успех, данные, сообщение)
    """
    results = {}
    messages = []

    # 1. Generate secret
    config = _load_config()
    domain = config.get("fake_tls_domain", "google.com")
    new_secret = generate_secret(domain)
    success, msg = set_secret(new_secret)
    results["secret"] = new_secret
    messages.append(msg)
    if not success:
        return False, results, "\n".join(messages)

    # 2. Auto-detect server IP
    success_srv, msg_srv = set_server(None)
    messages.append(msg_srv)
    if success_srv:
        config = _load_config()
        results["server"] = config.get("server", "")

    return success, results, "\n".join(messages)


# === URI Generation ===

def generate_tg_link(client_secret: Optional[str] = None) -> str:
    """
    Сгенерировать tg://proxy ссылку.

    Format: tg://proxy?server=IP&port=PORT&secret=SECRET

    Args:
        client_secret: Секрет клиента. Если None — основной секрет.

    Returns:
        str: tg://proxy?... ссылка
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 993)
    secret = client_secret or config.get("secret", "")

    if not server or not secret:
        return ""

    params = urllib.parse.urlencode({
        "server": server,
        "port": port,
        "secret": secret,
    })
    return f"tg://proxy?{params}"


def generate_https_link(client_secret: Optional[str] = None) -> str:
    """
    Сгенерировать HTTPS ссылку (t.me/proxy).

    Format: https://t.me/proxy?server=IP&port=PORT&secret=SECRET

    Args:
        client_secret: Секрет клиента. Если None — основной секрет.

    Returns:
        str: https://t.me/proxy?... ссылка
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 993)
    secret = client_secret or config.get("secret", "")

    if not server or not secret:
        return ""

    params = urllib.parse.urlencode({
        "server": server,
        "port": port,
        "secret": secret,
    })
    return f"https://t.me/proxy?{params}"


def get_client(name_or_secret: str) -> Optional[Dict]:
    """
    Найти клиента MTProto по имени или секрету.
    """
    if not name_or_secret or not name_or_secret.strip():
        return None

    needle = name_or_secret.strip()
    for client in list_clients():
        if client.get("name") == needle or client.get("secret") == needle:
            return client
    return None


def generate_client_links(name_or_secret: str) -> Tuple[bool, str, Dict]:
    """
    Сгенерировать tg:// и https:// ссылки для конкретного клиента.
    """
    config = _load_config()
    client = get_client(name_or_secret)
    if not client:
        return False, "❌ Клиент не найден", {}

    client_name = client.get("name") or "client"
    client_secret = client.get("secret") or ""
    if not client_secret:
        return False, f"❌ У клиента {client_name} отсутствует secret", {}

    tg_link = generate_tg_link(client_secret)
    https_link = generate_https_link(client_secret)
    if not tg_link or not https_link:
        return False, "❌ Не удалось сгенерировать MTProto ссылки. Проверьте сервер, порт и secret", {}

    return True, f"✅ Ссылки для клиента {client_name} готовы", {
        "name": client_name,
        "secret": client_secret,
        "secret_mode": config.get("secret_mode", DEFAULT_SECRET_MODE),
        "secret_mode_label": _secret_mode_label(config.get("secret_mode", DEFAULT_SECRET_MODE)),
        "tg_link": tg_link,
        "https_link": https_link,
    }


def generate_qr_png_bytes(content: str) -> Tuple[bool, Optional[BytesIO], str]:
    """
    Сгенерировать QR-код PNG в памяти.
    """
    if not content or not content.strip():
        return False, None, "❌ Нечего кодировать в QR"

    try:
        import qrcode
    except ImportError:
        return False, None, "❌ Библиотека qrcode не установлена. Обновите зависимости проекта"

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
        logger.error(f"Failed to generate MTProto QR image: {e}")
        return False, None, f"❌ Ошибка генерации QR: {e}"


def build_client_qr_payload(name_or_secret: str) -> Tuple[bool, str, Dict]:
    """
    Подготовить данные клиента MTProto для отправки QR-кода через Telegram.
    Для QR используем HTTPS ссылку, потому что камера телефона обычно открывает её надёжнее.
    """
    success, message, links = generate_client_links(name_or_secret)
    if not success:
        return False, message, {}

    success, qr_buffer, qr_message = generate_qr_png_bytes(links["https_link"])
    if not success or qr_buffer is None:
        return False, qr_message, {}

    payload = dict(links)
    payload["qr_buffer"] = qr_buffer
    return True, "✅ QR-пакет для клиента MTProto подготовлен", payload


# === Export ===

def export_subscription_list() -> List[str]:
    """Сформировать список tg://proxy URI для всех клиентов."""
    links = []
    clients = list_clients()
    for client in clients:
        name = client.get("name") or "client"
        client_secret = client.get("secret") or ""
        link = generate_tg_link(client_secret)
        if link:
            links.append(link)
    return links


def export_subscription_base64() -> str:
    """Сформировать subscription в base64."""
    import base64

    links = export_subscription_list()
    raw = "\n".join([x for x in links if x]).strip()
    if not raw:
        return ""
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")




# === Service Management (runs on VPS) ===

def _is_docker() -> bool:
    """Проверить, запущены ли мы внутри Docker."""
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def _collect_all_secrets() -> List[str]:
    """Собрать все уникальные серверные секреты для ExecStart."""
    config = _load_config()
    secret_mode = _normalize_secret_mode(config.get("secret_mode"))
    seen = set()
    result = []

    # Основной секрет
    main_secret = config.get("secret", "")
    if main_secret:
        parsed = _parse_secret(main_secret)
        server_secret = parsed.get("server_secret") if secret_mode == SECRET_MODE_EE_SPLIT else main_secret
        if server_secret and server_secret not in seen:
            seen.add(server_secret)
            result.append(server_secret)

    # Клиентские секреты
    for client in config.get("clients", []):
        cs = client.get("secret", "")
        if not cs:
            continue
        parsed = _parse_secret(cs)
        server_secret = parsed.get("server_secret") if secret_mode == SECRET_MODE_EE_SPLIT else cs
        if server_secret and server_secret not in seen:
            seen.add(server_secret)
            result.append(server_secret)

    return result


def _build_systemd_unit() -> str:
    """
    Построить содержимое systemd unit файла.

    mtproto-proxy использует CLI-флаги вместо конфиг-файла:
        -u nobody        — запуск от пользователя nobody
        -p 2398          — внутренний порт статистики
        -H PORT          — публичный порт для клиентов
        -S SECRET        — секрет (можно несколько -S)
        -D DOMAIN        — fake-TLS домен в ee_split режиме
        --aes-pwd FILE   — путь к proxy-secret от Telegram
        CONFIG_FILE      — путь к proxy-multi.conf от Telegram
        -M WORKERS       — количество воркеров
        --nat-info IP:IP — NAT info (для серверов за NAT)
    """
    config = _load_config()
    port = config.get("port", 993)
    workers = config.get("workers", 2)
    tag = config.get("tag", "")
    secret_mode = _normalize_secret_mode(config.get("secret_mode"))
    fake_tls_domain = (config.get("fake_tls_domain") or DEFAULT_FAKE_TLS_DOMAIN).strip().lower()

    all_secrets = _collect_all_secrets()
    if not all_secrets:
        raise ValueError("No secrets configured")

    secret_flags = " ".join(f"-S {s}" for s in all_secrets)

    # Базовая команда
    exec_start = (
        f"{MTPROXY_BINARY_PATH} "
        f"-u nobody "
        f"-p {STATS_PORT} "
        f"-H {port} "
        f"{secret_flags} "
    )

    if secret_mode == SECRET_MODE_EE_SPLIT:
        exec_start += f"-D {fake_tls_domain} "

    exec_start += (
        f"--aes-pwd {PROXY_SECRET_PATH} "
        f"{PROXY_MULTI_CONF_PATH} "
        f"-M {workers}"
    )

    # Добавить NAT info если есть публичный IP
    server_ip = config.get("server", "")
    if server_ip:
        exec_start += f" --nat-info {server_ip}:{server_ip}"

    # Добавить тег если есть
    if tag:
        exec_start += f" -T {tag}"

    unit = f"""[Unit]
Description=MTProto Proxy
After=network.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
"""
    return unit


def apply_config() -> Tuple[bool, str]:
    """
    Применить конфигурацию: записать systemd unit и перезапустить сервис.

    MTProto proxy не использует конфиг-файл — все параметры в ExecStart.
    """
    if _is_docker():
        return False, (
            "❌ Запущено в Docker — systemctl недоступен.\n"
            "Выполните на хосте:\n"
            "`cd /opt/TelegramSimple && python3 scripts/mtproto_sync_systemd.py`"
        )

    secret_mode = _normalize_secret_mode(_load_config().get("secret_mode"))
    try:
        unit_content = _build_systemd_unit()
    except ValueError as e:
        return False, f"❌ {e}"

    try:
        with open(SYSTEMD_UNIT_PATH, "w", encoding="utf-8") as f:
            f.write(unit_content)

        logger.info(f"MTProto systemd unit written to {SYSTEMD_UNIT_PATH}")

        # daemon-reload + restart
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=15,
        )

        result = subprocess.run(
            ["systemctl", "restart", SYSTEMD_SERVICE_NAME],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, (
                "✅ Конфиг применён и сервис перезапущен\n"
                f"📄 `{SYSTEMD_UNIT_PATH}`\n"
                f"Режим: `{secret_mode}` ({_secret_mode_label(secret_mode)})"
            )
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"⚠️ Unit записан, но сервис не перезапустился:\n`{error}`"

    except PermissionError:
        return False, "❌ Нет прав на запись. Запустите с sudo."
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def service_control(action: str) -> Tuple[bool, str]:
    """
    Управление systemd сервисом MTProto proxy.

    Args:
        action: start, stop, restart, status
    """
    if action not in ("start", "stop", "restart", "status"):
        return False, f"❌ Неизвестное действие: {action}"

    if _is_docker():
        return False, (
            "❌ Запущено в Docker — systemctl недоступен.\n"
            f"Выполните на хосте: `systemctl {action} {SYSTEMD_SERVICE_NAME}`"
        )

    try:
        result = subprocess.run(
            ["systemctl", action, SYSTEMD_SERVICE_NAME],
            capture_output=True, text=True, timeout=30,
        )

        if action == "status":
            output = result.stdout.strip() or result.stderr.strip()
            is_active = "active (running)" in output
            status_emoji = "🟢" if is_active else "🔴"
            return True, f"{status_emoji} MTProto proxy сервис:\n```\n{output[:500]}\n```"

        if result.returncode == 0:
            action_labels = {"start": "запущен", "stop": "остановлен", "restart": "перезапущен"}
            return True, f"✅ MTProto proxy {action_labels.get(action, action)}"
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"❌ Ошибка: {error[:300]}"

    except FileNotFoundError:
        return False, "❌ systemctl не найден. MTProto proxy установлен?"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def get_logs(lines: int = 30) -> Tuple[bool, str]:
    """Получить логи MTProto proxy сервиса."""
    if _is_docker():
        return False, "❌ Запущено в Docker — journalctl недоступен."

    try:
        result = subprocess.run(
            ["journalctl", "-u", SYSTEMD_SERVICE_NAME, "-n", str(lines), "--no-pager"],
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


def fetch_proxy_config() -> Tuple[bool, str]:
    """
    Обновить proxy-secret и proxy-multi.conf с серверов Telegram.
    Эти файлы нужны для работы MTProto proxy и должны обновляться ежедневно.
    """
    if _is_docker():
        return False, "❌ Запущено в Docker. Выполните на хосте."

    try:
        os.makedirs(MTPROXY_CONFIG_DIR, exist_ok=True)
    except Exception as e:
        return False, f"❌ Не удалось создать {MTPROXY_CONFIG_DIR}: {e}"

    messages = []
    success = True

    # Fetch proxy-secret
    try:
        result = subprocess.run(
            ["curl", "-s", "https://core.telegram.org/getProxySecret",
             "-o", PROXY_SECRET_PATH],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            messages.append(f"✅ proxy-secret обновлён: `{PROXY_SECRET_PATH}`")
        else:
            messages.append(f"❌ Ошибка загрузки proxy-secret: {result.stderr.strip()}")
            success = False
    except Exception as e:
        messages.append(f"❌ Ошибка: {e}")
        success = False

    # Fetch proxy-multi.conf
    try:
        result = subprocess.run(
            ["curl", "-s", "https://core.telegram.org/getProxyConfig",
             "-o", PROXY_MULTI_CONF_PATH],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            messages.append(f"✅ proxy-multi.conf обновлён: `{PROXY_MULTI_CONF_PATH}`")
        else:
            messages.append(f"❌ Ошибка загрузки proxy-multi.conf: {result.stderr.strip()}")
            success = False
    except Exception as e:
        messages.append(f"❌ Ошибка: {e}")
        success = False

    return success, "\n".join(messages)


def install_mtproto() -> Tuple[bool, str]:
    """
    Установить MTProto proxy на сервер (сборка из исходников).

    1. Установка зависимостей сборки
    2. Клонирование и компиляция MTProxy
    3. Загрузка proxy-secret и proxy-multi.conf
    4. Создание systemd сервиса
    """
    if _is_docker():
        return False, (
            "❌ Запущено в Docker — установка невозможна.\n"
            "Выполните на хосте:\n"
            f"`bash scripts/install_mtproto.sh --mode {DEFAULT_SECRET_MODE}`"
        )

    # Check if already installed
    try:
        check = subprocess.run(["test", "-f", MTPROXY_BINARY_PATH],
                               capture_output=True, text=True)
        if check.returncode == 0:
            return True, f"✅ MTProto proxy уже установлен: `{MTPROXY_BINARY_PATH}`"
    except Exception:
        pass

    messages = []

    # 1. Install build dependencies
    try:
        result = subprocess.run(
            ["apt-get", "install", "-y", "git", "curl", "cron", "build-essential",
             "libssl-dev", "zlib1g-dev", "xxd"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            messages.append("✅ Зависимости установлены")
        else:
            return False, f"❌ Ошибка установки зависимостей:\n`{result.stderr.strip()[:300]}`"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"

    # 2. Clone and build
    build_dir = "/opt/MTProxy"
    try:
        if os.path.exists(build_dir):
            subprocess.run(["rm", "-rf", build_dir], capture_output=True, timeout=10)

        result = subprocess.run(
            ["git", "clone", "https://github.com/TelegramMessenger/MTProxy.git", build_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return False, f"❌ Ошибка клонирования:\n`{result.stderr.strip()[:300]}`"

        messages.append("✅ Исходный код загружен")

        result = subprocess.run(
            ["make", "-j4", "-C", build_dir],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return False, f"❌ Ошибка компиляции:\n`{result.stderr.strip()[:300]}`"

        messages.append("✅ Компиляция завершена")

    except Exception as e:
        return False, f"❌ Ошибка сборки: {e}"

    # 3. Install binary
    try:
        src_binary = os.path.join(build_dir, "objs", "bin", "mtproto-proxy")
        subprocess.run(
            ["cp", src_binary, MTPROXY_BINARY_PATH],
            capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["chmod", "+x", MTPROXY_BINARY_PATH],
            capture_output=True, text=True, timeout=5,
        )
        messages.append(f"✅ Бинарник установлен: `{MTPROXY_BINARY_PATH}`")
    except Exception as e:
        return False, f"❌ Ошибка установки бинарника: {e}"

    # 4. Fetch Telegram config files
    success_fetch, msg_fetch = fetch_proxy_config()
    messages.append(msg_fetch)

    # 5. Setup cron for daily proxy config refresh
    try:
        subprocess.run(
            ["systemctl", "enable", "--now", "cron"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        messages.append("⚠️ Не удалось автоматически включить cron")

    try:
        cron_cmd = (
            f"(crontab -l 2>/dev/null | grep -v 'getProxySecret'; "
            f"echo '0 3 * * * curl -s https://core.telegram.org/getProxySecret "
            f"-o {PROXY_SECRET_PATH} && "
            f"curl -s https://core.telegram.org/getProxyConfig "
            f"-o {PROXY_MULTI_CONF_PATH} && "
            f"systemctl restart {SYSTEMD_SERVICE_NAME}') | crontab -"
        )
        subprocess.run(["bash", "-c", cron_cmd], capture_output=True, text=True, timeout=10)
        messages.append("✅ Cron: ежедневное обновление proxy-secret в 03:00")
    except Exception:
        messages.append("⚠️ Не удалось настроить cron (настройте вручную)")

    messages.append(
        "\n📡 Далее:\n"
        f"1. `/mt_set_mode {DEFAULT_SECRET_MODE}` — при необходимости зафиксировать режим\n"
        "2. `/mt_gen_all` — сгенерировать секрет и определить IP\n"
        "3. `/mt_apply` — применить конфиг и запустить\n"
        "4. `/mt_export` — получить ссылку для клиента"
    )

    return True, "\n".join(messages)


def test_connection() -> Tuple[bool, str]:
    """Тест доступности MTProto proxy (проверяет TCP порт)."""
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 993)

    if not server:
        return False, "❌ Сервер не настроен"

    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((server, port))
        sock.close()

        if result == 0:
            return True, f"✅ TCP порт {server}:{port} доступен"
        else:
            return False, f"❌ TCP порт {server}:{port} недоступен (код: {result})"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"
