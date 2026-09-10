# -*- coding: utf-8 -*-
"""
Module for managing Hysteria 2 configuration.

Hysteria 2 is a high-speed QUIC/UDP proxy protocol.
It works well on lossy networks and supports obfuscation.

Configuration structure:
{
    "enabled": false,
    "server": "VPS IP or domain",
    "port": 443,
    "password": "authentication password",
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

# Path to the Hysteria2 configuration file
_HY2_CONFIG_PATH = os.getenv(
    "HYSTERIA2_CONFIG_PATH", os.path.join(os.getcwd(), "hysteria2_config.json")
)

# Recommended ports for Hysteria2
# - 443: Standard HTTPS (UDP)
# - 8443: Alternative HTTPS
# - 4443: Often used for Hysteria
# - 10080: Alt port
RECOMMENDED_PORTS = [443, 8443, 4443, 10080]

SYSTEMD_SERVICE_PATH = "/etc/systemd/system/hysteria-server.service"

# Masquerade URLs / client SNI.
#
# Same caveat as VLESS AVAILABLE_SNI (see vless_manager.py): these are
# well-known options, NOT a guarantee of DPI bypass. Mobile operators
# (especially in RU) may filter domains typical of VPN guides — first of
# all www.microsoft.com. So the default and first list item is yahoo.com
# (same as VLESS). If the client cannot connect while network/port/keys
# are fine — change SNI + masquerade via /hy2_set_sni (buttons) and
# reissue the cert.
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

# Safe QUIC defaults — fix handshake on Windows clients (large UDP packets).
# Keys match hysteria-server config fields (camelCase on output).
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

# Default configuration
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
    """TLS SNI for client URI/config: explicit sni or DEFAULT_SNI (yahoo.com)."""
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
    """Extract a readable version number from `hysteria version` output.

    Newer builds print an ASCII banner; raw stdout looks like mojibake in
    Telegram if wrapped in backticks.
    """
    cleaned = _strip_ansi(raw)
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip purely decorative banner lines.
        if not re.search(r"[A-Za-z0-9]", line):
            continue
        m = _VERSION_RE.search(line)
        if m:
            return m.group(1)
    # fallback: first "text" line without block characters
    for line in cleaned.splitlines():
        line = line.strip()
        if line and re.search(r"[A-Za-z0-9]", line) and len(line) < 80:
            return line
    return "unknown"


def _resolve_hysteria_bin() -> str:
    """Absolute path to the host binary (or empty string)."""
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
    """Path from ExecStart of the current unit (if the file exists)."""
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
    # Format: "{ path=/usr/local/bin/hysteria ; argv[]=... }"
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
# Do not spin an endless restart loop on 203/EXEC (binary gone).
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

        # Reset the restart-loop counter after repairing the unit/binary.
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
    """Download the official binary via get.hy2.sh."""
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
    """Ensure the binary + a valid systemd unit before start/apply.

    Covers the case "unit exists but /usr/local/bin/hysteria is gone" (203/EXEC):
    install the binary if needed and rewrite ExecStart.
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
    Normalize the Hysteria2 client list.
    """
    clients = config.get("clients") or []
    if not isinstance(clients, list):
        clients = []

    # If there are no clients but there is a password — create a default client.
    if not clients and config.get("password"):
        clients = [
            {
                "name": "default",
                "password": config.get("password"),
                "created_at": datetime.now().isoformat(),
            }
        ]

    # Ensure the default client is synced with config["password"]
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
    """Load Hysteria2 configuration from file"""
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
    """Save Hysteria2 configuration to file"""
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
    """Check whether Hysteria2 is enabled"""
    config = _load_config()
    return config.get("enabled", False)


def enable() -> Tuple[bool, str]:
    """
    Enable Hysteria2.

    Returns:
        Tuple[bool, str]: (success, message)
    """
    config = _load_config()

    required = ["server", "password"]
    missing = [key for key in required if not config.get(key)]

    if missing:
        return False, f"Required parameters are not set: {', '.join(missing)}"

    config["enabled"] = True
    if _save_config(config):
        logger.info("Hysteria2 enabled")
        return True, "✅ Hysteria2 enabled"

    return False, "❌ Failed to save configuration"


def disable() -> Tuple[bool, str]:
    """
    Disable Hysteria2.

    Returns:
        Tuple[bool, str]: (success, message)
    """
    config = _load_config()
    config["enabled"] = False

    if _save_config(config):
        logger.info("Hysteria2 disabled")
        return True, "🔴 Hysteria2 disabled"

    return False, "❌ Failed to save configuration"


def get_status() -> Dict:
    """
    Get Hysteria2 status.

    Returns:
        Dict with status information
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
    Get Hysteria2 configuration (optionally with secrets).

    Args:
        include_secrets: whether to include passwords

    Returns:
        Dict with configuration
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
    Get the server public IP address.
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
    Set the Hysteria2 server address.

    Args:
        server: IP or domain. If None — auto-detect.
    """
    if not server or not server.strip():
        detected_ip = get_server_public_ip()
        if detected_ip:
            server = detected_ip
            auto_detected = True
        else:
            return (
                False,
                "❌ Failed to auto-detect server IP\n\nUse: /hy2_set_server <IP>",
            )
    else:
        auto_detected = False

    config = _load_config()
    config["server"] = server.strip()

    if _save_config(config):
        if auto_detected:
            return True, f"✅ Server set automatically: {server}"
        return True, f"✅ Server set: {server}"
    return False, "❌ Failed to save"


def set_port(port: int) -> Tuple[bool, str]:
    """
    Set the Hysteria2 server port.
    """
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False, "❌ Port must be a number from 1 to 65535"

    config = _load_config()
    config["port"] = port

    recommended = "⭐ recommended" if port in RECOMMENDED_PORTS else ""
    if _save_config(config):
        return (
            True,
            f"✅ Port set: {port} {recommended}\n⚠️ Remember to open the UDP port: `ufw allow {port}/udp`",
        )
    return False, "❌ Failed to save"


def set_password(password: str) -> Tuple[bool, str]:
    """
    Set the Hysteria2 authentication password.
    """
    if not password or not password.strip():
        return False, "❌ Password cannot be empty"

    password = password.strip()
    config = _load_config()
    config["password"] = password

    # Sync default client password
    _normalize_clients(config)

    if _save_config(config):
        return True, f"✅ Password set ({len(password)} characters)"
    return False, "❌ Failed to save"


def set_sni(sni: str, *, sync_masquerade: bool = True) -> Tuple[bool, str]:
    """
    Set the client TLS SNI.

    With a non-empty SNI, by default syncs masquerade_url → https://<sni>
    so camouflage/cert/client SNI stay consistent
    (same as /vless_set_sni changing Reality dest+serverNames together).
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
            f"✅ SNI set: {value or f'(empty → {DEFAULT_SNI} in export)'}{extra}",
        )
    return False, "❌ Failed to save"


def set_insecure(insecure: bool) -> Tuple[bool, str]:
    """
    Set the insecure flag (skip TLS verification).
    """
    config = _load_config()
    config["insecure"] = insecure

    if _save_config(config):
        status = "enabled ⚠️" if insecure else "disabled ✅"
        return True, f"✅ Insecure mode: {status}"
    return False, "❌ Failed to save"


def set_obfs(obfs_type: str, obfs_password: str = "") -> Tuple[bool, str]:
    """
    Set obfuscation.

    Args:
        obfs_type: obfuscation type ("salamander" or "" to disable)
        obfs_password: obfuscation password
    """
    if obfs_type and obfs_type not in ("salamander", ""):
        return False, "❌ Only obfuscation type supported: salamander"

    if obfs_type and not obfs_password:
        return False, "❌ Obfuscation requires a password"

    config = _load_config()
    config["obfs_type"] = obfs_type
    config["obfs_password"] = obfs_password

    if _save_config(config):
        if obfs_type:
            return True, f"✅ Obfuscation enabled: {obfs_type}"
        return True, "✅ Obfuscation disabled"
    return False, "❌ Failed to save"


def set_speed(up_mbps: int, down_mbps: int) -> Tuple[bool, str]:
    """
    Set speed limits (hint to the server).

    Args:
        up_mbps: upload speed in Mbps (0 = auto)
        down_mbps: download speed in Mbps (0 = auto)
    """
    if up_mbps < 0 or down_mbps < 0:
        return False, "❌ Speed cannot be negative"

    config = _load_config()
    config["up_mbps"] = up_mbps
    config["down_mbps"] = down_mbps

    if _save_config(config):
        up_str = f"{up_mbps} Mbps" if up_mbps > 0 else "auto"
        down_str = f"{down_mbps} Mbps" if down_mbps > 0 else "auto"
        return True, f"✅ Speed: ↑ {up_str} / ↓ {down_str}"
    return False, "❌ Failed to save"


def set_quic_safe(enabled: bool) -> Tuple[bool, str]:
    """
    Enable/disable the `quic:` block with safe defaults in the server config.

    Needed when Windows clients cannot complete the QUIC handshake
    (retransmits of 1280/1280 with large packets). Enables
    `disablePathMTUDiscovery: true` and caps receive windows.

    After changing, run `/hy2_apply` to rewrite
    `/etc/hysteria/config.yaml` and `/hy2_restart`.
    """
    config = _load_config()
    quic_cfg = dict(config.get("quic") or QUIC_SAFE_DEFAULTS)
    quic_cfg["enabled"] = bool(enabled)
    config["quic"] = quic_cfg

    if _save_config(config):
        status = "enabled ✅" if enabled else "disabled"
        return True, (
            f"✅ Safe QUIC-defaults {status}\nℹ️ Apply: /hy2_apply → /hy2_restart"
        )
    return False, "❌ Failed to save"


def set_quic_param(param: str, value: str) -> Tuple[bool, str]:
    """
    Set one known `quic:` block parameter for experiments.

    Only whitelisted keys are accepted so a typo cannot produce an invalid
    server config.
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
        return False, f"❌ Unknown QUIC parameter: {param}\nAvailable: {allowed}"

    raw = str(value).strip()
    kind = allowed_types[key]
    if kind == "bool":
        lowered = raw.lower()
        if lowered not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            return False, "❌ Boolean value: 1/0, true/false, yes/no, on/off"
        parsed = lowered in ("1", "true", "yes", "on")
    elif kind == "int":
        try:
            parsed = int(raw)
        except ValueError:
            return False, "❌ Value must be an integer"
        if parsed <= 0:
            return False, "❌ QUIC window must be greater than zero"
    else:
        if not raw:
            return False, "❌ Duration cannot be empty"
        parsed = raw

    config = _load_config()
    quic_cfg = dict(QUIC_SAFE_DEFAULTS)
    if isinstance(config.get("quic"), dict):
        quic_cfg.update(config["quic"])
    quic_cfg[key] = parsed
    config["quic"] = quic_cfg

    if _save_config(config):
        return True, (
            f"✅ QUIC parameter updated: {key} = {parsed}\n"
            f"ℹ️ Apply: /hy2_apply → /hy2_restart"
        )
    return False, "❌ Failed to save"


def set_masquerade(url: str) -> Tuple[bool, str]:
    """
    Set the masquerade URL.
    """
    if not url or not url.strip():
        return False, "❌ URL cannot be empty"

    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    config = _load_config()
    config["masquerade_url"] = url

    if _save_config(config):
        return True, f"✅ Masquerade URL: {url}"
    return False, "❌ Failed to save"


# === Key/Cert Generation ===


def generate_password(length: int = 16) -> str:
    """Generate a secure password."""
    return secrets.token_urlsafe(length)


def generate_self_signed_cert(
    cert_path: str = "/etc/hysteria/server.crt",
    key_path: str = "/etc/hysteria/server.key",
    domain: str = DEFAULT_SNI,
    days: int = 36500,
) -> Tuple[bool, str]:
    """
    Generate a self-signed TLS certificate for Hysteria2.

    Tries:
        1. openssl CLI
        2. Python cryptography library
    """
    # Ensure directory exists
    cert_dir = os.path.dirname(cert_path) or "."
    try:
        os.makedirs(cert_dir, exist_ok=True)
    except Exception as e:
        return False, f"❌ Failed to create directory {cert_dir}: {e}"

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
            # Certificate CN = client SNI; always keep them in sync.
            config["sni"] = domain
            if not (config.get("masquerade_url") or "").strip():
                config["masquerade_url"] = f"https://{domain}"
            _save_config(config)
            logger.info(f"Generated TLS cert via openssl: {cert_path} (SNI={domain})")
            return (
                True,
                f"✅ Certificate generated (openssl)\n📄 Cert: `{cert_path}`\n🔑 Key: `{key_path}`\n🌐 SNI: `{domain}`",
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
            f"✅ Certificate generated (Python)\n📄 Cert: `{cert_path}`\n🔑 Key: `{key_path}`\n🌐 SNI: `{domain}`",
        )

    except ImportError:
        return (
            False,
            "❌ Failed to generate certificate.\nInstall openssl or: `pip install cryptography`",
        )
    except Exception as e:
        return False, f"❌ Certificate generation error: {e}"


def generate_all() -> Tuple[bool, Dict, str]:
    """
    Generate everything: password + certificate + auto-detect IP.

    Returns:
        Tuple[bool, Dict, str]: (success, data, message)
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
        messages.append("⚠️ insecure=1 (self-signed certificate)")

    overall_success = success and success_cert
    return overall_success, results, "\n".join(messages)


# === Clients ===


def list_clients() -> List[Dict]:
    """
    Get the list of Hysteria2 clients.
    """
    config = _load_config()
    _normalize_clients(config)
    return config.get("clients", [])


def add_client(
    name: str, client_password: Optional[str] = None
) -> Tuple[bool, str, Dict]:
    """
    Add a Hysteria2 client.
    """
    if not name or not name.strip():
        return False, "❌ Client name cannot be empty", {}

    name = name.strip()
    config = _load_config()
    _normalize_clients(config)

    for client in config.get("clients", []):
        if client.get("name") == name:
            return False, f"❌ Client named {name} already exists", {}

    if not client_password:
        client_password = generate_password()

    client = {
        "name": name,
        "password": client_password,
        "created_at": datetime.now().isoformat(),
    }

    config["clients"].append(client)
    if _save_config(config):
        return True, f"✅ Client added: {name}", client
    return False, "❌ Failed to save", {}


def remove_client(name_or_password: str) -> Tuple[bool, str]:
    """
    Remove a client by name or password.
    """
    if not name_or_password or not name_or_password.strip():
        return False, "❌ Specify a client name"

    name_or_password = name_or_password.strip()
    config = _load_config()
    _normalize_clients(config)

    if name_or_password == "default":
        return False, "❌ Cannot delete the default client"

    clients = config.get("clients", [])
    new_clients = [
        c
        for c in clients
        if c.get("name") != name_or_password and c.get("password") != name_or_password
    ]

    if len(new_clients) == len(clients):
        return False, "❌ Client not found"

    config["clients"] = new_clients
    if _save_config(config):
        return True, "✅ Client removed"
    return False, "❌ Failed to save"


# === Export Configurations ===


def _resolve_auth_identity(config: Dict, client_name: Optional[str]) -> Tuple[str, str]:
    """
    Pick (name, password) for client auth.

    The server is written with `type: userpass` when there is at least one
    client (`_normalize_clients` guarantees `default`). The client must
    send `name:password`. An empty name is returned only for the legacy
    case when clients are missing entirely.
    """
    clients = config.get("clients") or []

    if client_name:
        client = next(
            (c for c in clients if c.get("name") == client_name),
            None,
        )
        if client and client.get("password"):
            return client_name, client["password"]

    # client_name not set → use the default client (always exists after
    # _normalize_clients). That is the identifier hysteria-server looks up
    # in the userpass map.
    if clients:
        default_client = next(
            (c for c in clients if c.get("name") == "default"),
            None,
        )
        if default_client and default_client.get("password"):
            return "default", default_client["password"]

    # Legacy: no clients → server is also not in userpass; send a bare password.
    return "", config.get("password", "")


def generate_hy2_uri(
    client_name: Optional[str] = None,
    client_password: Optional[str] = None,
    comment: str = "Hysteria2",
) -> str:
    """
    Generate a hy2:// URI for the client.

    Userpass format:  hy2://name:password@server:port/?insecure=1&sni=xxx#comment
    Single format:    hy2://password@server:port/?insecure=1&sni=xxx#comment
    """
    config = _load_config()

    server = config.get("server", "")
    port = config.get("port", 443)

    # Pick identity: explicit client_name/password, otherwise the default client.
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
    Return an alias with the full hysteria2:// scheme for clients that
    do not recognize short hy2://, without changing auth/query/fragment.
    """
    if uri.startswith("hy2://"):
        return "hysteria2://" + uri[len("hy2://") :]
    return uri


def get_client(name_or_password: str) -> Optional[Dict]:
    """
    Find a Hysteria2 client by name or password.
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
    Generate a hy2:// URI for a specific client.
    """
    client = get_client(name_or_password)
    if not client:
        return False, "❌ Client not found", ""

    client_name = client.get("name") or "client"
    client_password = client.get("password") or ""
    if not client_password:
        return False, f"❌ Client {client_name} has no password", ""

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
            "❌ Failed to generate Hysteria2 URI. Check server settings and password",
            "",
        )

    return True, f"✅ URI for client {client_name} is ready", uri


def generate_qr_png_bytes(content: str) -> Tuple[bool, Optional[BytesIO], str]:
    """
    Generate a QR-code PNG in memory.
    """
    if not content or not content.strip():
        return False, None, "❌ Nothing to encode in QR"

    try:
        import qrcode
    except ImportError:
        return (
            False,
            None,
            "❌ qrcode library is not installed. Update project dependencies",
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
        return True, buffer, "✅ QR code generated"
    except Exception as e:
        logger.error(f"Failed to generate Hysteria2 QR image: {e}")
        return False, None, f"❌ QR generation error: {e}"


def build_client_qr_payload(name_or_password: str) -> Tuple[bool, str, Dict]:
    """
    Prepare Hysteria2 client data for sending a QR code via Telegram.
    """
    client = get_client(name_or_password)
    if not client:
        return False, "❌ Client not found", {}

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
    return True, "✅ QR payload for Hysteria2 client is ready", payload


def export_server_config() -> Dict:
    """
    Generate Hysteria2 server configuration (for /etc/hysteria/config.yaml).

    Returns:
        Dict (YAML-like structure, serialized to YAML)
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

    # QUIC tuning — fixes QUIC handshake on Windows clients.
    # See sing-box/HYSTERIA2_TROUBLESHOOTING.md §4.4.
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
    Generate server configuration in YAML format.
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
    Generate Hysteria2 client configuration (native format).

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
    Generate a sing-box (client) configuration with a Hysteria2 outbound.

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
    Generate a Clash Meta configuration (YAML) with a Hysteria2 proxy.
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
    Build a list of URIs for subscription.
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
    Build a base64 subscription.
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
    """Group under which hysteria-server runs (for config.yaml permissions).

    The official systemd unit runs the service as `User=hysteria/Group=hysteria`,
    so a root-only config (0600) causes FATAL "permission denied" at start.
    Take the group from the unit, defaulting to `hysteria`.
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
    """Let the Hysteria2 service user read config.yaml (chgrp + chmod 640).

    Best-effort: if the group is missing or we are not root — skip silently
    (the service might run as root, then 0640 root is still readable). Without
    this, `/hy2_apply` writes config 0600 root:root and the service as
    `hysteria` dies with "permission denied".
    """
    grp = _hysteria_service_group()
    try:
        _host_run(["chgrp", grp, path], capture_output=True, text=True, timeout=10)
        _host_run(["chmod", "640", path], capture_output=True, text=True, timeout=10)
    except Exception as e:
        logger.warning(f"Hysteria2: failed to set permissions on {path}: {e}")


def apply_config() -> Tuple[bool, str]:
    """
    Apply the current configuration to the Hysteria2 server.
    Writes /etc/hysteria/config.yaml and restarts the service.
    """
    config_yaml = export_server_config_yaml()
    config_path = "/etc/hysteria/config.yaml"

    try:
        runtime_ok, runtime_msg = ensure_hysteria_runtime(install_if_missing=True)
        if not runtime_ok:
            return False, (
                "❌ Hysteria2 runtime is not ready (binary/unit):\n"
                f"`{runtime_msg}`\n"
                "Try `/hy2_install`."
            )

        # 0640 + chgrp to the service group: hysteria-server runs as an
        # unprivileged user and must read config.yaml (else FATAL "permission denied").
        write_result = _host_write_text(config_path, config_yaml, mode="0640")
        if write_result.returncode != 0:
            error = write_result.stderr.strip() or write_result.stdout.strip()
            return False, f"❌ Failed to write host Hysteria2 config:\n`{error}`"

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
                f"✅ Config applied and service restarted\n"
                f"📄 `{config_path}`\n"
                f"🧩 {runtime_msg}"
            )
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"⚠️ Config written, but the service did not restart:\n`{error}`"

    except PermissionError:
        return False, "❌ No write permission for /etc/hysteria/. Run with sudo."
    except Exception as e:
        return False, f"❌ Error: {e}"


def service_control(action: str) -> Tuple[bool, str]:
    """
    Control the Hysteria2 systemd service.

    Args:
        action: start, stop, restart, status
    """
    if action not in ("start", "stop", "restart", "status"):
        return False, f"❌ Unknown action: {action}"

    try:
        if action in ("start", "restart"):
            runtime_ok, runtime_msg = ensure_hysteria_runtime(install_if_missing=True)
            if not runtime_ok:
                return False, (
                    "❌ Failed to prepare Hysteria2 before "
                    f"`{action}`:\n`{runtime_msg}`\n"
                    "Try `/hy2_install`."
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
            return True, f"{status_emoji} Hysteria2 service:\n```\n{output[:500]}\n```"

        if result.returncode == 0:
            action_labels = {
                "start": "started",
                "stop": "stopped",
                "restart": "restarted",
            }
            extra = ""
            if action in ("start", "restart"):
                extra = f"\n🧩 {runtime_msg}"
            return True, (
                f"✅ Hysteria2 {action_labels.get(action, action)}{extra}"
            )
        else:
            error = result.stderr.strip() or result.stdout.strip()
            return False, f"❌ Error: {error[:300]}"

    except FileNotFoundError:
        return False, "❌ systemctl not found. Is Hysteria2 installed?"
    except Exception as e:
        return False, f"❌ Error: {e}"


def service_active() -> Optional[bool]:
    """
    Actual state of the Hysteria2 systemd service.

    Returns:
        True  — service is running (systemctl is-active == "active"),
        False — installed but not running,
        None  — state could not be determined (no systemctl / SSH error).
    """
    try:
        result = _host_run(
            ["systemctl", "is-active", "hysteria-server"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # is-active prints "active" / "inactive" / "failed" / "activating"...
        return result.stdout.strip() == "active"
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"service_active() failed: {e}")
        return None


def get_logs(lines: int = 30) -> Tuple[bool, str]:
    """
    Get Hysteria2 service logs.
    """
    try:
        result = _host_run(
            ["journalctl", "-u", "hysteria-server", "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(empty)"
        # Truncate for Telegram message limit
        if len(output) > 3500:
            output = output[-3500:]
        return True, output

    except FileNotFoundError:
        return False, "❌ journalctl not found"
    except Exception as e:
        return False, f"❌ Error: {e}"


def install_hysteria2() -> Tuple[bool, str]:
    """
    Install/repair Hysteria2 on the host.

    If the binary already exists — verify systemd ExecStart and rewrite the
    unit on 203/EXEC. If the binary is missing — download the official installer.
    """
    try:
        had_binary = bool(_resolve_hysteria_bin())
        ok, detail = ensure_hysteria_runtime(install_if_missing=True)
        if not ok:
            return False, f"❌ {detail}"

        hysteria_bin = _resolve_hysteria_bin() or "?"
        header = (
            "✅ Hysteria2 already installed / repaired"
            if had_binary
            else "✅ Hysteria2 installed"
        )
        return True, (
            f"{header}\n"
            f"📁 Binary: `{hysteria_bin}`\n"
            f"🧩 {detail}\n\n"
            "Next:\n"
            "1. `/hy2_gen_all` — password + certificate + IP\n"
            "2. `/hy2_set_port 8443` — if 443/udp is taken by Caddy\n"
            "3. `/hy2_on` → `/hy2_apply` → `/hy2_start`"
        )
    except Exception as e:
        return False, f"❌ Error: {e}"


def test_connection() -> Tuple[bool, str]:
    """
    Test Hysteria2 server reachability (checks the UDP port).
    """
    config = _load_config()
    server = config.get("server", "")
    port = config.get("port", 443)

    if not server:
        return False, "❌ Server is not configured"

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

        return True, f"✅ UDP port {server}:{port} is reachable (not rejected)"
    except Exception as e:
        return False, f"❌ Error: {e}"
