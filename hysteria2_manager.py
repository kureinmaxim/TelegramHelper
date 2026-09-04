# -*- coding: utf-8 -*-
"""
Модуль для управления Hysteria 2 конфигурацией.

Hysteria 2 — это высокоскоростной прокси-протокол на основе QUIC/UDP.
Отлично работает в сетях с потерями пакетов, поддерживает обфускацию.

Структура конфигурации:
{
    "enabled": false,
    "server": "IP или домен VPS",
    "port": 443,
    "password": "пароль аутентификации",
    "sni": "yahoo.com",
    "insecure": false,
    "up_mbps": 0,
    "down_mbps": 0,
    "obfs_type": "",
    "obfs_password": "",
    "tls_cert_path": "/etc/hysteria/server.crt",
    "tls_key_path": "/etc/hysteria/server.key",
    "masquerade_url": "https://yahoo.com",
    "quic": {
        "enabled": true,
        "disable_path_mtu_discovery": true,
        "init_stream_receive_window": 1048576,
        "max_stream_receive_window": 8388608,
        "init_conn_receive_window": 2097152,
        "max_conn_receive_window": 16777216,
        "max_idle_timeout": "30s",
        "keep_alive_period": "10s"
    },
    "clients": []
}
"""

import json
import logging
import os
import re
import secrets
import subprocess
import threading
import urllib.parse
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from host_utils import host_run as _host_run
from host_utils import host_write_text as _host_write_text
from profile_names import visible_profile_name

# Thread safety
_hy2_lock = threading.Lock()

# Путь к файлу конфигурации Hysteria2
_HY2_CONFIG_PATH = os.getenv(
    "HYSTERIA2_CONFIG_PATH", os.path.join(os.getcwd(), "hysteria2_config.json")
)

# Рекомендуемые порты для Hysteria2
# - 443: Стандартный HTTPS (UDP)
# - 8443: Альтернативный HTTPS
# - 4443: Часто используется для Hysteria
# - 10080: Alt port
RECOMMENDED_PORTS = [443, 8443, 4443, 10080]

SYSTEMD_SERVICE_PATH = "/etc/systemd/system/hysteria-server.service"

# Masquerade URLs / client SNI.
#
# Тот же кавеат, что и для VLESS AVAILABLE_SNI (см. vless_manager.py): это
# общеизвестные варианты, а НЕ гарантия обхода DPI. Мобильные
# операторы (особенно в РФ) могут фильтровать именно типовые для
# VPN-гайдов домены — в первую очередь www.microsoft.com. Поэтому
# дефолт и первый пункт списка — yahoo.com (как у VLESS).
# Если клиент не подключается при рабочих сети/порте/ключах —
# смените SNI + masquerade через /hy2_set_sni (кнопки) и перевыпустите cert.
DEFAULT_SNI = "yahoo.com"
AVAILABLE_SNI = [
    "yahoo.com",
    "www.cloudflare.com",
    "www.amazon.com",
    "www.microsoft.com",
    "www.apple.com",
    "www.google.com",
]
AVAILABLE_MASQUERADE = [f"https://{d}" for d in AVAILABLE_SNI]

# Safe QUIC defaults — фиксят handshake на Windows-клиентах (большие UDP-пакеты).
# Ключи соответствуют полям hysteria-server config (camelCase на выходе).
QUIC_SAFE_DEFAULTS = {
    "enabled": True,
    "disable_path_mtu_discovery": True,
    "init_stream_receive_window": 1048576,
    "max_stream_receive_window": 8388608,
    "init_conn_receive_window": 2097152,
    "max_conn_receive_window": 16777216,
    "max_idle_timeout": "30s",
    "keep_alive_period": "10s",
}

# Дефолтная конфигурация
DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",
    "port": 443,
    "password": "",
    "sni": DEFAULT_SNI,
    "insecure": False,
    "up_mbps": 0,
    "down_mbps": 0,
    "obfs_type": "",
    "obfs_password": "",
    "tls_cert_path": "/etc/hysteria/server.crt",
    "tls_key_path": "/etc/hysteria/server.key",
    "masquerade_url": f"https://{DEFAULT_SNI}",
    "quic": dict(QUIC_SAFE_DEFAULTS),
    "clients": [],
    "created_at": None,
    "updated_at": None,
}


def effective_sni(config: Optional[Dict] = None) -> str:
    """TLS SNI для клиентских URI/config: явный sni или DEFAULT_SNI (yahoo.com)."""
    if config is None:
        config = _load_config()
    sni = (config.get("sni") or "").strip()
    return sni or DEFAULT_SNI


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VERSION_RE = re.compile(
    r"(?i)\bv?(?:ersion[:\s]*)?(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.]+)?)\b"
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _parse_hysteria_version(raw: str) -> str:
    """Достать читаемый номер версии из вывода `hysteria version`.

    Новые сборки печатают ASCII-баннер; сырой stdout в Telegram выглядит
    как крякозябры, если обернуть его в backticks.
    """
    cleaned = _strip_ansi(raw)
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        # Пропускаем чисто декоративные строки баннера.
        if not re.search(r"[A-Za-z0-9]", line):
            continue
        m = _VERSION_RE.search(line)
        if m:
            return m.group(1)
    # fallback: первая «текстовая» строка без блоков
    for line in cleaned.splitlines():
        line = line.strip()
        if line and re.search(r"[A-Za-z0-9]", line) and len(line) < 80:
            return line
    return "unknown"


def _resolve_hysteria_bin() -> str:
    """Абсолютный путь к бинарнику на хосте (или пустая строка)."""
    which_result = _host_run(
        ["sh", "-c", "command -v hysteria"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    candidate = (
        which_result.stdout.strip() if which_result.returncode == 0 else ""
    )
    if candidate:
        probe = _host_run(
            ["test", "-x", candidate], capture_output=True, text=True, timeout=5
        )
        if probe.returncode == 0:
            return candidate
    for path in ("/usr/local/bin/hysteria", "/usr/bin/hysteria"):
        probe = _host_run(
            ["test", "-x", path], capture_output=True, text=True, timeout=5
        )
        if probe.returncode == 0:
            return path
    return ""


def _unit_exec_start_bin() -> str:
    """Путь из ExecStart текущего unit (если файл есть)."""
    show = _host_run(
        [
            "systemctl",
            "show",
            "-p",
            "ExecStart",
            "--value",
            "hysteria-server",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if show.returncode != 0:
        return ""
    # Формат: "{ path=/usr/local/bin/hysteria ; argv[]=... }"
    m = re.search(r"path=([^\s;]+)", show.stdout or "")
    return m.group(1) if m else ""


def _ensure_systemd_service() -> Tuple[bool, str]:
    """Ensure host systemd unit exists for Hysteria2.

    Some installs leave the `hysteria` binary present but no
    `hysteria-server.service`, so `/hy2_install` must verify both.
    Also rewrite the unit when ExecStart points to a missing binary
    (classic 203/EXEC restart loop).
    """
    try:
        hysteria_bin = _resolve_hysteria_bin()
        if not hysteria_bin:
            return False, "hysteria binary not found on host"

        unit_exists = (
            _host_run(
                ["test", "-f", SYSTEMD_SERVICE_PATH],
                capture_output=True,
                text=True,
                timeout=10,
            ).returncode
            == 0
        )
        current_bin = _unit_exec_start_bin() if unit_exists else ""
        current_ok = False
        if current_bin:
            current_ok = (
                _host_run(
                    ["test", "-x", current_bin],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).returncode
                == 0
            )
        if unit_exists and current_ok:
            return True, f"systemd unit ok ({current_bin})"

        unit = f"""[Unit]
Description=Hysteria2 Server
After=network.target
# Не крутить бесконечный restart-loop при 203/EXEC (пропал бинарник).
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
ExecStart={hysteria_bin} server -c /etc/hysteria/config.yaml
Restart=on-failure
RestartSec=5
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
"""
        result = _host_run(
            [
                "bash",
                "-c",
                "cat > /etc/systemd/system/hysteria-server.service",
            ],
            input=unit,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"failed to write systemd unit: {error[:300]}"

        reload_result = _host_run(
            ["systemctl", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if reload_result.returncode != 0:
            error = reload_result.stderr.strip() or reload_result.stdout.strip()
            return False, f"systemctl daemon-reload failed: {error[:300]}"

        enable_result = _host_run(
            ["systemctl", "enable", "hysteria-server"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if enable_result.returncode != 0:
            error = enable_result.stderr.strip() or enable_result.stdout.strip()
            return False, f"systemctl enable failed: {error[:300]}"

        # Сбросить счётчик restart-loop после починки unit/бинарника.
        _host_run(
            ["systemctl", "reset-failed", "hysteria-server"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        action = "repaired" if unit_exists else "created"
        return True, f"systemd unit {action} ({hysteria_bin})"
    except Exception as exc:
        return False, str(exc)


def _install_hysteria_binary() -> Tuple[bool, str]:
    """Скачать официальный бинарник через get.hy2.sh."""
    result = _host_run(
        ["bash", "-c", "curl -fsSL https://get.hy2.sh/ | bash"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()[:300]
        return False, f"official installer failed: {error or 'unknown error'}"
    hysteria_bin = _resolve_hysteria_bin()
    if not hysteria_bin:
        return False, (
            "installer finished, but executable still missing "
            "(/usr/local/bin/hysteria or PATH)"
        )
    return True, hysteria_bin


def ensure_hysteria_runtime(*, install_if_missing: bool = True) -> Tuple[bool, str]:
    """Гарантировать бинарник + валидный systemd unit перед start/apply.

    Закрывает кейс «unit есть, а /usr/local/bin/hysteria пропал» (203/EXEC):
    при необходимости ставит бинарник и переписывает ExecStart.
    """
    notes: List[str] = []
    hysteria_bin = _resolve_hysteria_bin()
    if not hysteria_bin:
        if not install_if_missing:
            return False, "hysteria binary not found on host"
        logger.warning("Hysteria2 binary missing — running official installer")
        ok, detail = _install_hysteria_binary()
        if not ok:
            return False, detail
        hysteria_bin = detail
        notes.append(f"binary installed: `{hysteria_bin}`")

    service_ok, service_msg = _ensure_systemd_service()
    if not service_ok:
        return False, service_msg
    notes.append(service_msg)

    ver = _host_run(
        [hysteria_bin, "version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    version = (
        _parse_hysteria_version(ver.stdout or ver.stderr or "")
        if ver.returncode == 0
        else "unknown"
    )
    notes.append(f"version `{version}`")
    return True, "; ".join(notes)


def _normalize_clients(config: Dict) -> None:
    """
    Нормализовать список клиентов Hysteria2.
    """
    clients = config.get("clients") or []
    if not isinstance(clients, list):
        clients = []

    # Если клиентов нет, но есть password — создаём дефолтного клиента.
    if not clients and config.get("password"):
        clients = [
            {
                "name": "default",
                "password": config.get("password"),
                "created_at": datetime.now().isoformat(),
            }
        ]

    # Убедимся, что дефолтный клиент синхронизирован с config["password"]
    if config.get("password"):
        for client in clients:
            if client.get("name") == "default":
                client["password"] = config.get("password")
                break
        else:
            clients.append(
                {
                    "name": "default",
                    "password": config.get("password"),
                    "created_at": datetime.now().isoformat(),
                }
            )

    config["clients"] = clients


def _load_config() -> Dict:
    """Загрузить конфигурацию Hysteria2 из файла"""
    with _hy2_lock:
        if not os.path.exists(_HY2_CONFIG_PATH):
            return dict(DEFAULT_CONFIG)

        try:
            with open(_HY2_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = dict(DEFAULT_CONFIG)
                config.update(data)
                # Migrate legacy configs: ensure quic block always has defaults.
                quic_cfg = dict(QUIC_SAFE_DEFAULTS)
                if isinstance(config.get("quic"), dict):
                    quic_cfg.update(config["quic"])
                config["quic"] = quic_cfg
                _normalize_clients(config)
                return config
        except Exception as e:
            logger.error(f"Error loading Hysteria2 config: {e}")
            return dict(DEFAULT_CONFIG)


def _save_config(config: Dict) -> bool:
    """Сохранить конфигурацию Hysteria2 в файл"""
    with _hy2_lock:
        try:
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]

            directory = os.path.dirname(_HY2_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)

            with open(_HY2_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            return True
        except Exception as e:
            logger.error(f"Error saving Hysteria2 config: {e}")
            return False


# === Public API ===


def is_enabled() -> bool:
    """Проверить, включён ли Hysteria2"""
    config = _load_config()
    return config.get("enabled", False)


def enable() -> Tuple[bool, str]:
    """
    Включить Hysteria2.

    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    config = _load_config()

    required = ["server", "password"]
    missing = [key for key in required if not config.get(key)]

    if missing:
        return False, f"Не настроены обязательные параметры: {', '.join(missing)}"

    config["enabled"] = True
    if _save_config(config):
        logger.info("Hysteria2 enabled")
        return True, "✅ Hysteria2 включён"

    return False, "❌ Ошибка при сохранении конфигурации"


def disable() -> Tuple[bool, str]:
    """
    Выключить Hysteria2.

    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    config = _load_config()
    config["enabled"] = False

    if _save_config(config):
        logger.info("Hysteria2 disabled")
        return True, "🔴 Hysteria2 выключен"

    return False, "❌ Ошибка при сохранении конфигурации"


def get_status() -> Dict:
    """
    Получить статус Hysteria2.

    Returns:
        Dict с информацией о статусе
    """
    config = _load_config()

    required = ["server", "password"]
    configured = all(config.get(key) for key in required)

    binary_path = _resolve_hysteria_bin()
    unit_bin = _unit_exec_start_bin()
    unit_bin_ok = False
    if unit_bin:
        unit_bin_ok = (
            _host_run(
                ["test", "-x", unit_bin],
                capture_output=True,
                text=True,
                timeout=5,
            ).returncode
            == 0
        )

    return {
        "enabled": config.get("enabled", False),
        "service_active": service_active(),
        "configured": configured,
        "server": config.get("server", ""),
        "port": config.get("port", 443),
        "sni": config.get("sni", ""),
        "insecure": config.get("insecure", False),
        "has_password": bool(config.get("password")),
        "has_obfs": bool(config.get("obfs_type")),
        "obfs_type": config.get("obfs_type", ""),
        "up_mbps": config.get("up_mbps", 0),
        "down_mbps": config.get("down_mbps", 0),
        "masquerade_url": config.get("masquerade_url", ""),
        "tls_cert_path": config.get("tls_cert_path", ""),
        "tls_key_path": config.get("tls_key_path", ""),
        "clients_count": len(config.get("clients", [])),
        "quic_safe": bool((config.get("quic") or {}).get("enabled", False)),
        "binary_path": binary_path,
        "unit_exec_path": unit_bin,
        "unit_exec_ok": unit_bin_ok,
        "updated_at": config.get("updated_at"),
    }


def get_config(include_secrets: bool = False) -> Dict:
    """
    Получить конфигурацию Hysteria2 (опционально с секретами).

    Args:
        include_secrets: включать ли пароли

    Returns:
        Dict с конфигурацией
    """
    config = _load_config()

    if not include_secrets:
        if config.get("password"):
            pw = config["password"]
            config["password"] = f"{pw[:4]}...{pw[-4:]}" if len(pw) > 8 else "***"
        if config.get("obfs_password"):
            opw = config["obfs_password"]
            config["obfs_password"] = f"{opw[:4]}..." if len(opw) > 4 else "***"
        # Mask client passwords
        for client in config.get("clients", []):
            if client.get("password"):
                cpw = client["password"]
                client["password"] = f"{cpw[:4]}..." if len(cpw) > 4 else "***"

    return config


# === Server IP ===


def get_server_public_ip() -> Optional[str]:
    """
    Получить публичный IP адрес сервера.
    """
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
                ip = response.read().decode("utf-8").strip()
                parts = ip.split(".")
                if len(parts) == 4 and all(
                    p.isdigit() and 0 <= int(p) <= 255 for p in parts
                ):
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
        if not ip.startswith(("10.", "172.", "192.168.", "127.")):
            return ip
    except Exception:
        pass

    return None


# === Setters ===


def set_server(server: Optional[str] = None) -> Tuple[bool, str]:
    """
    Установить адрес сервера Hysteria2.

    Args:
        server: IP или домен. Если None — автоопределение.
    """
    if not server or not server.strip():
        detected_ip = get_server_public_ip()
        if detected_ip:
            server = detected_ip
            auto_detected = True
        else:
            return (
                False,
                "❌ Не удалось автоматически определить IP сервера\n\nИспользуйте: /hy2_set_server <IP>",
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


def set_port(port: int) -> Tuple[bool, str]:
    """
    Установить порт сервера Hysteria2.
    """
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False, "❌ Порт должен быть числом от 1 до 65535"

    config = _load_config()
    config["port"] = port

    recommended = "⭐ рекомендуемый" if port in RECOMMENDED_PORTS else ""
    if _save_config(config):
        return (
            True,
            f"✅ Порт установлен: {port} {recommended}\n⚠️ Не забудьте открыть UDP порт: `ufw allow {port}/udp`",
        )
    return False, "❌ Ошибка при сохранении"


def set_password(password: str) -> Tuple[bool, str]:
    """
    Установить пароль аутентификации Hysteria2.
    """
    if not password or not password.strip():
        return False, "❌ Пароль не может быть пустым"

    password = password.strip()
    config = _load_config()
    config["password"] = password

    # Sync default client password
    _normalize_clients(config)

    if _save_config(config):
        return True, f"✅ Пароль установлен ({len(password)} символов)"
    return False, "❌ Ошибка при сохранении"


def set_sni(sni: str, *, sync_masquerade: bool = True) -> Tuple[bool, str]:
    """
    Установить TLS SNI клиента.

    При непустом SNI по умолчанию синхронизирует masquerade_url → https://<sni>,
    чтобы маскировка/сертификат/клиентский SNI оставались согласованными
    (как /vless_set_sni меняет Reality dest+serverNames разом).
    """
    config = _load_config()
    value = sni.strip() if sni else ""
    config["sni"] = value
    extra = ""
    if value and sync_masquerade:
        config["masquerade_url"] = f"https://{value}"
        extra = f"\n🎭 Masquerade: {config['masquerade_url']}"

    if _save_config(config):
        return (
            True,
            f"✅ SNI установлен: {value or f'(пусто → {DEFAULT_SNI} в экспорте)'}{extra}",
        )
    return False, "❌ Ошибка при сохранении"


def set_insecure(insecure: bool) -> Tuple[bool, str]:
    """
    Установить флаг insecure (пропуск проверки TLS).
    """
    config = _load_config()
    config["insecure"] = insecure

    if _save_config(config):
        status = "включён ⚠️" if insecure else "выключен ✅"
        return True, f"✅ Insecure mode: {status}"
    return False, "❌ Ошибка при сохранении"


def set_obfs(obfs_type: str, obfs_password: str = "") -> Tuple[bool, str]:
    """
    Установить обфускацию.

    Args:
        obfs_type: тип обфускации ("salamander" или "" для отключения)
        obfs_password: пароль обфускации
    """
    if obfs_type and obfs_type not in ("salamander", ""):
        return False, "❌ Поддерживается только тип обфускации: salamander"

    if obfs_type and not obfs_password:
        return False, "❌ Для обфускации необходим пароль"

    config = _load_config()
    config["obfs_type"] = obfs_type
    config["obfs_password"] = obfs_password

    if _save_config(config):
        if obfs_type:
            return True, f"✅ Обфускация включена: {obfs_type}"
        return True, "✅ Обфускация выключена"
    return False, "❌ Ошибка при сохранении"


def set_speed(up_mbps: int, down_mbps: int) -> Tuple[bool, str]:
    """
    Установить ограничения скорости (подсказка серверу).

    Args:
        up_mbps: скорость загрузки в Mbps (0 = авто)
        down_mbps: скорость скачивания в Mbps (0 = авто)
    """
    if up_mbps < 0 or down_mbps < 0:
        return False, "❌ Скорость не может быть отрицательной"

    config = _load_config()
    config["up_mbps"] = up_mbps
    config["down_mbps"] = down_mbps

    if _save_config(config):
        up_str = f"{up_mbps} Mbps" if up_mbps > 0 else "авто"
        down_str = f"{down_mbps} Mbps" if down_mbps > 0 else "авто"
        return True, f"✅ Скорость: ↑ {up_str} / ↓ {down_str}"
    return False, "❌ Ошибка при сохранении"


def set_quic_safe(enabled: bool) -> Tuple[bool, str]:
    """
    Включить/выключить блок `quic:` с safe-defaults в серверном конфиге.

    Блок нужен, когда клиенты на Windows не могут поднять QUIC handshake
    (ретрансмиты 1280/1280 большими пакетами). Включает
    `disablePathMTUDiscovery: true` и ограничивает receive-окна.

    После смены нужно вызвать `/hy2_apply` чтобы переписать
    `/etc/hysteria/config.yaml` и `/hy2_restart`.
    """
    config = _load_config()
    quic_cfg = dict(config.get("quic") or QUIC_SAFE_DEFAULTS)
    quic_cfg["enabled"] = bool(enabled)
    config["quic"] = quic_cfg

    if _save_config(config):
        status = "включены ✅" if enabled else "выключены"
        return True, (
            f"✅ Safe QUIC-defaults {status}\nℹ️ Примените: /hy2_apply → /hy2_restart"
        )
    return False, "❌ Ошибка при сохранении"


def set_quic_param(param: str, value: str) -> Tuple[bool, str]:
    """
    Установить один известный параметр блока `quic:` для экспериментов.

    Поддерживаем только whitelisted keys, чтобы случайная опечатка не
    породила невалидный server config.
    """
    aliases = {
        "disablePathMTUDiscovery": "disable_path_mtu_discovery",
        "initStreamReceiveWindow": "init_stream_receive_window",
        "maxStreamReceiveWindow": "max_stream_receive_window",
        "initConnReceiveWindow": "init_conn_receive_window",
        "maxConnReceiveWindow": "max_conn_receive_window",
        "maxIdleTimeout": "max_idle_timeout",
        "keepAlivePeriod": "keep_alive_period",
        "enabled": "enabled",
    }
    allowed_types = {
        "enabled": "bool",
        "disable_path_mtu_discovery": "bool",
        "init_stream_receive_window": "int",
        "max_stream_receive_window": "int",
        "init_conn_receive_window": "int",
        "max_conn_receive_window": "int",
        "max_idle_timeout": "duration",
        "keep_alive_period": "duration",
    }

    key = aliases.get(param, param)
    if key not in allowed_types:
        allowed = ", ".join(sorted(allowed_types))
        return False, f"❌ Неизвестный QUIC параметр: {param}\nДоступно: {allowed}"

    raw = str(value).strip()
    kind = allowed_types[key]
    if kind == "bool":
        lowered = raw.lower()
        if lowered not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            return False, "❌ Boolean значение: 1/0, true/false, yes/no, on/off"
        parsed = lowered in ("1", "true", "yes", "on")
    elif kind == "int":
        try:
            parsed = int(raw)
        except ValueError:
            return False, "❌ Значение должно быть целым числом"
        if parsed <= 0:
            return False, "❌ Окно QUIC должно быть больше нуля"
    else:
        if not raw:
            return False, "❌ Duration не может быть пустым"
        parsed = raw

    config = _load_config()
    quic_cfg = dict(QUIC_SAFE_DEFAULTS)
    if isinstance(config.get("quic"), dict):
        quic_cfg.update(config["quic"])
    quic_cfg[key] = parsed
    config["quic"] = quic_cfg

    if _save_config(config):
        return True, (
            f"✅ QUIC параметр обновлён: {key} = {parsed}\n"
            f"ℹ️ Примените: /hy2_apply → /hy2_restart"
        )
    return False, "❌ Ошибка при сохранении"


def set_masquerade(url: str) -> Tuple[bool, str]:
    """
    Установить URL маскировки (masquerade).
    """
    if not url or not url.strip():
        return False, "❌ URL не может быть пустым"

    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    config = _load_config()
    config["masquerade_url"] = url

    if _save_config(config):
        return True, f"✅ Masquerade URL: {url}"
    return False, "❌ Ошибка при сохранении"


# === Key/Cert Generation ===


def generate_password(length: int = 16) -> str:
    """Сгенерировать безопасный пароль."""
    return secrets.token_urlsafe(length)


def generate_self_signed_cert(
    cert_path: str = "/etc/hysteria/server.crt",
    key_path: str = "/etc/hysteria/server.key",
    domain: str = DEFAULT_SNI,
    days: int = 36500,
) -> Tuple[bool, str]:
    """
    Сгенерировать self-signed TLS сертификат для Hysteria2.

    Tries:
        1. openssl CLI
        2. Python cryptography library
    """
    # Ensure directory exists
    cert_dir = os.path.dirname(cert_path) or "."
    try:
        os.makedirs(cert_dir, exist_ok=True)
    except Exception as e:
        return False, f"❌ Не удалось создать директорию {cert_dir}: {e}"

    # Method 1: openssl CLI
    try:
        cmd = [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-keyout",
            key_path,
            "-out",
            cert_path,
            "-subj",
            f"/CN={domain}",
            "-days",
            str(days),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            # Save paths + auto-set SNI to match cert CN
            config = _load_config()
            config["tls_cert_path"] = cert_path
            config["tls_key_path"] = key_path
            # CN сертификата = клиентский SNI; всегда синхронизируем.
            config["sni"] = domain
            if not (config.get("masquerade_url") or "").strip():
                config["masquerade_url"] = f"https://{domain}"
            _save_config(config)
            logger.info(f"Generated TLS cert via openssl: {cert_path} (SNI={domain})")
            return (
                True,
                f"✅ Сертификат сгенерирован (openssl)\n📄 Cert: `{cert_path}`\n🔑 Key: `{key_path}`\n🌐 SNI: `{domain}`",
            )
    except FileNotFoundError:
        logger.debug("openssl not found, trying Python cryptography")
    except Exception as e:
        logger.debug(f"openssl failed: {e}")

    # Method 2: Python cryptography library
    try:
        import datetime as dt

        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1(), default_backend())

        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, domain),
            ]
        )

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
            f.write(
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )

        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        config = _load_config()
        config["tls_cert_path"] = cert_path
        config["tls_key_path"] = key_path
        config["sni"] = domain
        if not (config.get("masquerade_url") or "").strip():
            config["masquerade_url"] = f"https://{domain}"
        _save_config(config)
        logger.info(
            f"Generated TLS cert via Python cryptography: {cert_path} (SNI={domain})"
        )
        return (
            True,
            f"✅ Сертификат сгенерирован (Python)\n📄 Cert: `{cert_path}`\n🔑 Key: `{key_path}`\n🌐 SNI: `{domain}`",
        )

    except ImportError:
        return (
            False,
            "❌ Не удалось сгенерировать сертификат.\nУстановите openssl или: `pip install cryptography`",
        )
    except Exception as e:
        return False, f"❌ Ошибка генерации сертификата: {e}"


def generate_all() -> Tuple[bool, Dict, str]:
    """
    Сгенерировать всё: пароль + сертификат + автоопределить IP.

    Returns:
        Tuple[bool, Dict, str]: (успех, данные, сообщение)
    """
    results = {}
    messages = []

    # 1. Generate password
    password = generate_password()
    success, msg = set_password(password)
    results["password"] = password
    messages.append(msg)
    if not success:
        return False, results, "\n".join(messages)

    # 2. Auto-detect server IP
    success_srv, msg_srv = set_server(None)
    messages.append(msg_srv)
    if success_srv:
        config = _load_config()
        results["server"] = config.get("server", "")

    # 3. Generate TLS certificate
    success_cert, msg_cert = generate_self_signed_cert()
    results["cert_generated"] = success_cert
    messages.append(msg_cert)

    # 4. Self-signed cert → insecure=1 for client URIs
    if success_cert:
        set_insecure(True)
        messages.append("⚠️ insecure=1 (self-signed сертификат)")

    overall_success = success and success_cert
    return overall_success, results, "\n".join(messages)


# === Clients ===


def list_clients() -> List[Dict]:
    """
    Получить список клиентов Hysteria2.
    """
    config = _load_config()
    _normalize_clients(config)
    return config.get("clients", [])


def add_client(
    name: str, client_password: Optional[str] = None
) -> Tuple[bool, str, Dict]:
    """
    Добавить клиента Hysteria2.
    """
    if not name or not name.strip():
        return False, "❌ Имя клиента не может быть пустым", {}

    name = name.strip()
    config = _load_config()
    _normalize_clients(config)

    for client in config.get("clients", []):
        if client.get("name") == name:
            return False, f"❌ Клиент с именем {name} уже существует", {}

    if not client_password:
        client_password = generate_password()

    client = {
        "name": name,
        "password": client_password,
        "created_at": datetime.now().isoformat(),
    }

    config["clients"].append(client)
    if _save_config(config):
        return True, f"✅ Клиент добавлен: {name}", client
    return False, "❌ Ошибка при сохранении", {}


def remove_client(name_or_password: str) -> Tuple[bool, str]:
    """
    Удалить клиента по имени или паролю.
    """
    if not name_or_password or not name_or_password.strip():
        return False, "❌ Укажите имя клиента"

    name_or_password = name_or_password.strip()
    config = _load_config()
    _normalize_clients(config)

    if name_or_password == "default":
        return False, "❌ Нельзя удалить default клиента"

    clients = config.get("clients", [])
    new_clients = [
        c
        for c in clients
        if c.get("name") != name_or_password and c.get("password") != name_or_password
    ]

    if len(new_clients) == len(clients):
        return False, "❌ Клиент не найден"

    config["clients"] = new_clients
    if _save_config(config):
        return True, "✅ Клиент удалён"
    return False, "❌ Ошибка при сохранении"


# === Export Configurations ===


def _resolve_auth_identity(config: Dict, client_name: Optional[str]) -> Tuple[str, str]:
    """
    Подобрать (name, password) для клиентского auth.

    Сервер пишется с `type: userpass`, когда есть хоть один client
    (а `_normalize_clients` гарантирует `default`). Клиент обязан
    слать `name:password`. Пустой name возвращаем только для legacy
    случая, когда clients отсутствуют вовсе.
    """
    clients = config.get("clients") or []

    if client_name:
        client = next(
            (c for c in clients if c.get("name") == client_name),
            None,
        )
        if client and client.get("password"):
            return client_name, client["password"]

    # client_name не задан → берём default-клиента (он всегда существует
    # после _normalize_clients). Это именно тот идентификатор, который
    # hysteria-server ищет в userpass-мапе.
    if clients:
        default_client = next(
            (c for c in clients if c.get("name") == "default"),
            None,
        )
        if default_client and default_client.get("password"):
            return "default", default_client["password"]

    # Legacy: clients нет → сервер тоже не в userpass, шлём голый пароль.
    return "", config.get("password", "")


def generate_hy2_uri(
    client_name: Optional[str] = None,
    client_password: Optional[str] = None,
    comment: str = "Hysteria2",
) -> str:
    """
    Генерация URI hy2:// для клиента.

    Userpass format:  hy2://name:password@server:port/?insecure=1&sni=xxx#comment
    Single format:    hy2://password@server:port/?insecure=1&sni=xxx#comment
    """
    config = _load_config()

    server = config.get("server", "")
    port = config.get("port", 443)

    # Выбор идентификатора: явно заданный client_name/password, иначе default-клиент.
    if client_password:
        name_resolved = client_name or "default"
        password = client_password
    else:
        name_resolved, password = _resolve_auth_identity(config, client_name)

    if not server or not password:
        return ""

    params = {}

    sni = effective_sni(config)
    params["sni"] = sni

    if config.get("insecure"):
        params["insecure"] = "1"

    obfs_type = config.get("obfs_type", "")
    if obfs_type:
        params["obfs"] = obfs_type
        obfs_password = config.get("obfs_password", "")
        if obfs_password:
            params["obfs-password"] = obfs_password

    query_string = "&".join(
        [f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()]
    )
    comment_enc = urllib.parse.quote(comment)

    # Userpass: hy2://name:password@...  Legacy: hy2://password@...
    if name_resolved:
        auth_part = (
            f"{urllib.parse.quote(name_resolved)}:{urllib.parse.quote(password)}"
        )
    else:
        auth_part = urllib.parse.quote(password)

    uri = f"hy2://{auth_part}@{server}:{port}"
    if query_string:
        uri += f"/?{query_string}"
    uri += f"#{comment_enc}"
    return uri


def to_hysteria2_uri(uri: str) -> str:
    """
    Вернуть alias с полной схемой hysteria2:// для клиентов, которые
    не распознают короткую hy2://, не меняя auth/query/fragment.
    """
    if uri.startswith("hy2://"):
        return "hysteria2://" + uri[len("hy2://") :]
    return uri


def get_client(name_or_password: str) -> Optional[Dict]:
    """
    Найти клиента Hysteria2 по имени или паролю.
    """
    if not name_or_password or not name_or_password.strip():
        return None

    needle = name_or_password.strip()
    for client in list_clients():
        if client.get("name") == needle or client.get("password") == needle:
            return client
    return None


def generate_client_uri(name_or_password: str) -> Tuple[bool, str, str]:
    """
    Сгенерировать hy2:// URI для конкретного клиента.
    """
    client = get_client(name_or_password)
    if not client:
        return False, "❌ Клиент не найден", ""

    client_name = client.get("name") or "client"
    client_password = client.get("password") or ""
    if not client_password:
        return False, f"❌ У клиента {client_name} отсутствует пароль", ""

    uri = generate_hy2_uri(
        client_name,
        client_password,
        visible_profile_name(
            "Hysteria2", _load_config().get("server", ""), client_name
        ),
    )
    if not uri:
        return (
            False,
            "❌ Не удалось сгенерировать Hysteria2 URI. Проверьте настройки сервера и пароль",
            "",
        )

    return True, f"✅ URI для клиента {client_name} готов", uri


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
        logger.error(f"Failed to generate Hysteria2 QR image: {e}")
        return False, None, f"❌ Ошибка генерации QR: {e}"


def build_client_qr_payload(name_or_password: str) -> Tuple[bool, str, Dict]:
    """
    Подготовить данные клиента Hysteria2 для отправки QR-кода через Telegram.
    """
    client = get_client(name_or_password)
    if not client:
        return False, "❌ Клиент не найден", {}

    success, message, uri = generate_client_uri(name_or_password)
    if not success:
        return False, message, {}

    success, qr_buffer, qr_message = generate_qr_png_bytes(uri)
    if not success or qr_buffer is None:
        return False, qr_message, {}

    payload = {
        "name": client.get("name") or "client",
        "password": client.get("password") or "",
        "uri": uri,
        "qr_buffer": qr_buffer,
    }
    return True, "✅ QR-пакет для клиента Hysteria2 подготовлен", payload


def export_server_config() -> Dict:
    """
    Сгенерировать серверную конфигурацию Hysteria2 (для /etc/hysteria/config.yaml).

    Returns:
        Dict (YAML-like structure, сериализуется в YAML)
    """
    config = _load_config()

    clients = config.get("clients", [])

    # Use userpass auth when per-user clients exist, single password otherwise
    if clients:
        userpass_map = {}
        for c in clients:
            name = c.get("name", "")
            pwd = c.get("password", "")
            if name and pwd:
                userpass_map[name] = pwd
        auth_block = (
            {
                "type": "userpass",
                "userpass": userpass_map,
            }
            if userpass_map
            else {
                "type": "password",
                "password": config.get("password", ""),
            }
        )
    else:
        auth_block = {
            "type": "password",
            "password": config.get("password", ""),
        }

    server_config = {
        "listen": f":{config.get('port', 443)}",
        "tls": {
            "cert": config.get("tls_cert_path", "/etc/hysteria/server.crt"),
            "key": config.get("tls_key_path", "/etc/hysteria/server.key"),
        },
        "auth": auth_block,
    }

    # Bandwidth (optional)
    up_mbps = config.get("up_mbps", 0)
    down_mbps = config.get("down_mbps", 0)
    if up_mbps > 0 or down_mbps > 0:
        bandwidth = {}
        if up_mbps > 0:
            bandwidth["up"] = f"{up_mbps} mbps"
        if down_mbps > 0:
            bandwidth["down"] = f"{down_mbps} mbps"
        server_config["bandwidth"] = bandwidth

    # Obfuscation (optional)
    obfs_type = config.get("obfs_type", "")
    obfs_password = config.get("obfs_password", "")
    if obfs_type and obfs_password:
        server_config["obfs"] = {
            "type": obfs_type,
            obfs_type: {
                "password": obfs_password,
            },
        }

    # Masquerade
    masquerade_url = config.get("masquerade_url", "")
    if masquerade_url:
        server_config["masquerade"] = {
            "type": "proxy",
            "proxy": {
                "url": masquerade_url,
                "rewriteHost": True,
            },
        }

    # QUIC tuning — лечит QUIC handshake на Windows-клиентах.
    # См. sing-box/HYSTERIA2_TROUBLESHOOTING.md §4.4.
    quic_cfg = config.get("quic") or {}
    if quic_cfg.get("enabled"):
        server_config["quic"] = {
            "disablePathMTUDiscovery": bool(
                quic_cfg.get("disable_path_mtu_discovery", True)
            ),
            "initStreamReceiveWindow": int(
                quic_cfg.get("init_stream_receive_window", 1048576)
            ),
            "maxStreamReceiveWindow": int(
                quic_cfg.get("max_stream_receive_window", 8388608)
            ),
            "initConnReceiveWindow": int(
                quic_cfg.get("init_conn_receive_window", 2097152)
            ),
            "maxConnReceiveWindow": int(
                quic_cfg.get("max_conn_receive_window", 16777216)
            ),
            "maxIdleTimeout": str(quic_cfg.get("max_idle_timeout", "30s")),
            "keepAlivePeriod": str(quic_cfg.get("keep_alive_period", "10s")),
        }

    return server_config


def export_server_config_yaml() -> str:
    """
    Сгенерировать серверную конфигурацию в формате YAML.
    """
    config = export_server_config()

    # Simple YAML serialization (no pyyaml dependency)
    lines = []

    def _yaml_value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return f'"{v}"' if v else '""'

    def _dump_dict(d, indent=0):
        prefix = "  " * indent
        for key, val in d.items():
            if isinstance(val, dict):
                lines.append(f"{prefix}{key}:")
                _dump_dict(val, indent + 1)
            else:
                lines.append(f"{prefix}{key}: {_yaml_value(val)}")

    _dump_dict(config)
    return "\n".join(lines)


def export_client_config(client_name: Optional[str] = None) -> Dict:
    """
    Сгенерировать клиентскую конфигурацию Hysteria2 (native format).

    Server is always in `userpass` mode when clients exist, so auth is emitted
    as `name:password`. When client_name is None — falls back to `default`.
    """
    config = _load_config()

    name_resolved, password = _resolve_auth_identity(config, client_name)
    if name_resolved:
        auth_str = f"{name_resolved}:{password}"
    else:
        auth_str = password

    client_config = {
        "server": f"{config.get('server', '')}:{config.get('port', 443)}",
        "auth": auth_str,
        "tls": {},
        "socks5": {
            "listen": "127.0.0.1:1080",
        },
        "http": {
            "listen": "127.0.0.1:8080",
        },
    }

    sni = effective_sni(config)
    client_config["tls"]["sni"] = sni
    if config.get("insecure"):
        client_config["tls"]["insecure"] = True

    obfs_type = config.get("obfs_type", "")
    obfs_password = config.get("obfs_password", "")
    if obfs_type and obfs_password:
        client_config["obfs"] = {
            "type": obfs_type,
            obfs_type: {
                "password": obfs_password,
            },
        }

    up_mbps = config.get("up_mbps", 0)
    down_mbps = config.get("down_mbps", 0)
    if up_mbps > 0 or down_mbps > 0:
        bandwidth = {}
        if up_mbps > 0:
            bandwidth["up"] = f"{up_mbps} mbps"
        if down_mbps > 0:
            bandwidth["down"] = f"{down_mbps} mbps"
        client_config["bandwidth"] = bandwidth

    return client_config


def export_singbox_config(client_name: Optional[str] = None) -> Dict:
    """
    Сгенерировать конфигурацию sing-box (client) с Hysteria2 outbound.

    Server is always in `userpass` mode when clients exist, so `password`
    field is emitted as `name:password`. When client_name is None —
    falls back to `default`.
    """
    config = _load_config()

    name_resolved, password = _resolve_auth_identity(config, client_name)
    if name_resolved:
        password_str = f"{name_resolved}:{password}"
    else:
        password_str = password

    outbound = {
        "type": "hysteria2",
        "tag": "proxy",
        "server": config.get("server", ""),
        "server_port": config.get("port", 443),
        "password": password_str,
        "tls": {
            "enabled": True,
            "server_name": effective_sni(config),
            "insecure": config.get("insecure", False),
        },
    }

    up_mbps = config.get("up_mbps", 0)
    down_mbps = config.get("down_mbps", 0)
    if up_mbps > 0:
        outbound["up_mbps"] = up_mbps
    if down_mbps > 0:
        outbound["down_mbps"] = down_mbps

    obfs_type = config.get("obfs_type", "")
    obfs_password = config.get("obfs_password", "")
    if obfs_type and obfs_password:
        outbound["obfs"] = {
            "type": obfs_type,
            "password": obfs_password,
        }

    return {
        "log": {"level": "warn"},
        "inbounds": [
            {
                "type": "socks",
                "listen": "127.0.0.1",
                "listen_port": 1080,
            }
        ],
        "outbounds": [outbound],
    }


def export_clash_meta_config(client_name: Optional[str] = None) -> str:
    """
    Сгенерировать конфигурацию Clash Meta (YAML) с Hysteria2 proxy.
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 443)

    name_resolved, raw_password = _resolve_auth_identity(config, client_name)
    password = f"{name_resolved}:{raw_password}" if name_resolved else raw_password

    sni = effective_sni(config)
    insecure = config.get("insecure", False)
    obfs_type = config.get("obfs_type", "")
    obfs_password = config.get("obfs_password", "")

    lines = [
        "port: 7890",
        "socks-port: 7891",
        "mixed-port: 7892",
        "mode: rule",
        "log-level: info",
        "",
        "proxies:",
        "  - name: hysteria2",
        "    type: hysteria2",
        f"    server: {server}",
        f"    port: {port}",
        f"    password: {password}",
    ]

    if sni:
        lines.append(f"    sni: {sni}")
    if insecure:
        lines.append("    skip-cert-verify: true")
    if obfs_type:
        lines.append(f"    obfs: {obfs_type}")
        if obfs_password:
            lines.append(f"    obfs-password: {obfs_password}")

    lines.extend(
        [
            "",
            "proxy-groups:",
            "  - name: PROXY",
            "    type: select",
            "    proxies:",
            "      - hysteria2",
            "      - DIRECT",
            "",
            "rules:",
            "  - MATCH,PROXY",
        ]
    )

    return "\n".join(lines)


def export_subscription_list() -> List[str]:
    """
    Сформировать список URI для subscription.
    """
    links = []
    clients = list_clients()
    for client in clients:
        name = client.get("name") or "client"
        client_password = client.get("password") or ""
        link = generate_hy2_uri(
            name,
            client_password,
            visible_profile_name("Hysteria2", _load_config().get("server", ""), name),
        )
        if link:
            links.append(link)
    return links


def export_subscription_base64() -> str:
    """
    Сформировать subscription в base64.
    """
    import base64

    links = export_subscription_list()
    raw = "\n".join([x for x in links if x]).strip()
    if not raw:
        return ""
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


# === sing-box Profile Export (legacy apisb-profile) ===




# === Service Management (runs on VPS) ===


def _hysteria_service_group(default: str = "hysteria") -> str:
    """Группа, под которой бежит hysteria-server (для прав на config.yaml).

    Официальный systemd-юнит запускает сервис под `User=hysteria/Group=hysteria`,
    поэтому root-only config (0600) даёт FATAL «permission denied» при старте.
    Берём группу из юнита, на всякий случай — дефолт `hysteria`.
    """
    try:
        r = _host_run(
            ["systemctl", "show", "-p", "Group", "--value", "hysteria-server"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        grp = (r.stdout or "").strip()
        return grp or default
    except Exception:
        return default


def _grant_config_read(path: str) -> None:
    """Дать сервисному пользователю Hysteria2 читать config.yaml (chgrp + chmod 640).

    Лучшая попытка: если группы нет или мы не root — тихо пропускаем (сервис мог бы
    бежать под root, тогда 0640 root тоже читается). Без этого `/hy2_apply` пишет
    config 0600 root:root, и сервис под `hysteria` падает с «permission denied».
    """
    grp = _hysteria_service_group()
    try:
        _host_run(["chgrp", grp, path], capture_output=True, text=True, timeout=10)
        _host_run(["chmod", "640", path], capture_output=True, text=True, timeout=10)
    except Exception as e:
        logger.warning(f"Hysteria2: не удалось выставить права на {path}: {e}")


def apply_config() -> Tuple[bool, str]:
    """
    Применить текущую конфигурацию к серверу Hysteria2.
    Записывает /etc/hysteria/config.yaml и перезапускает сервис.
    """
    config_yaml = export_server_config_yaml()
    config_path = "/etc/hysteria/config.yaml"

    try:
        runtime_ok, runtime_msg = ensure_hysteria_runtime(install_if_missing=True)
        if not runtime_ok:
            return False, (
                "❌ Hysteria2 runtime не готов (бинарник/unit):\n"
                f"`{runtime_msg}`\n"
                "Попробуйте `/hy2_install`."
            )

        # 0640 + chgrp на группу сервиса: hysteria-server бежит под непривилегированным
        # пользователем и должен читать config.yaml (иначе FATAL «permission denied»).
        write_result = _host_write_text(config_path, config_yaml, mode="0640")
        if write_result.returncode != 0:
            error = write_result.stderr.strip() or write_result.stdout.strip()
            return False, f"❌ Не удалось записать host config Hysteria2:\n`{error}`"

        _grant_config_read(config_path)
        logger.info(f"Hysteria2 server config written to {config_path}")

        # Restart service
        result = _host_run(
            ["systemctl", "restart", "hysteria-server"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, (
                f"✅ Конфиг применён и сервис перезапущен\n"
                f"📄 `{config_path}`\n"
                f"🧩 {runtime_msg}"
            )
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"⚠️ Конфиг записан, но сервис не перезапустился:\n`{error}`"

    except PermissionError:
        return False, "❌ Нет прав на запись в /etc/hysteria/. Запустите с sudo."
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def service_control(action: str) -> Tuple[bool, str]:
    """
    Управление systemd сервисом Hysteria2.

    Args:
        action: start, stop, restart, status
    """
    if action not in ("start", "stop", "restart", "status"):
        return False, f"❌ Неизвестное действие: {action}"

    try:
        if action in ("start", "restart"):
            runtime_ok, runtime_msg = ensure_hysteria_runtime(install_if_missing=True)
            if not runtime_ok:
                return False, (
                    "❌ Не удалось подготовить Hysteria2 перед "
                    f"`{action}`:\n`{runtime_msg}`\n"
                    "Попробуйте `/hy2_install`."
                )

        result = _host_run(
            ["systemctl", action, "hysteria-server"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if action == "status":
            output = result.stdout.strip() or result.stderr.strip()
            is_active = "active (running)" in output
            status_emoji = "🟢" if is_active else "🔴"
            return True, f"{status_emoji} Hysteria2 сервис:\n```\n{output[:500]}\n```"

        if result.returncode == 0:
            action_labels = {
                "start": "запущен",
                "stop": "остановлен",
                "restart": "перезапущен",
            }
            extra = ""
            if action in ("start", "restart"):
                extra = f"\n🧩 {runtime_msg}"
            return True, (
                f"✅ Hysteria2 {action_labels.get(action, action)}{extra}"
            )
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"❌ Ошибка: {error[:300]}"

    except FileNotFoundError:
        return False, "❌ systemctl не найден. Hysteria2 установлен?"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def service_active() -> Optional[bool]:
    """
    Реальное состояние systemd-сервиса Hysteria2.

    Returns:
        True  — сервис запущен (systemctl is-active == "active"),
        False — установлен, но не запущен,
        None  — состояние не удалось определить (нет systemctl / ошибка SSH).
    """
    try:
        result = _host_run(
            ["systemctl", "is-active", "hysteria-server"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # is-active печатает "active" / "inactive" / "failed" / "activating"...
        return result.stdout.strip() == "active"
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"service_active() failed: {e}")
        return None


def get_logs(lines: int = 30) -> Tuple[bool, str]:
    """
    Получить логи Hysteria2 сервиса.
    """
    try:
        result = _host_run(
            ["journalctl", "-u", "hysteria-server", "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(пусто)"
        # Truncate for Telegram message limit
        if len(output) > 3500:
            output = output[-3500:]
        return True, output

    except FileNotFoundError:
        return False, "❌ journalctl не найден"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def install_hysteria2() -> Tuple[bool, str]:
    """
    Установить/починить Hysteria2 на хосте.

    Если бинарник уже есть — сверяет systemd ExecStart и при 203/EXEC
    переписывает unit. Если бинарника нет — качает официальный installer.
    """
    try:
        had_binary = bool(_resolve_hysteria_bin())
        ok, detail = ensure_hysteria_runtime(install_if_missing=True)
        if not ok:
            return False, f"❌ {detail}"

        hysteria_bin = _resolve_hysteria_bin() or "?"
        header = (
            "✅ Hysteria2 уже установлен / починен"
            if had_binary
            else "✅ Hysteria2 установлен"
        )
        return True, (
            f"{header}\n"
            f"📁 Binary: `{hysteria_bin}`\n"
            f"🧩 {detail}\n\n"
            "Дальше:\n"
            "1. `/hy2_gen_all` — пароль + сертификат + IP\n"
            "2. `/hy2_set_port 8443` — если 443/udp занят Caddy\n"
            "3. `/hy2_on` → `/hy2_apply` → `/hy2_start`"
        )
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def test_connection() -> Tuple[bool, str]:
    """
    Тест доступности Hysteria2 сервера (проверяет UDP порт).
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 443)

    if not server:
        return False, "❌ Сервер не настроен"

    import socket

    try:
        # UDP port check — send empty packet and see if we get ICMP unreachable
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)
        sock.sendto(b"\x00", (server, port))
        try:
            sock.recvfrom(1024)
        except socket.timeout:
            # Timeout is OK for UDP — means port is not explicitly rejected
            pass
        sock.close()

        return True, f"✅ UDP порт {server}:{port} доступен (не отклонён)"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"
