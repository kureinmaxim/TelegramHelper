# -*- coding: utf-8 -*-
"""
Module for managing VLESS-Reality configuration.

VLESS-Reality is a traffic-camouflage protocol that makes the connection
indistinguishable from ordinary HTTPS traffic to popular websites.

Configuration structure:
{
    "enabled": false,
    "server": "VPS IP or domain",
    "port": 443,
    "uuid": "VLESS UUID",
    "public_key": "Reality public key (x25519)",
    "private_key": "Reality private key (server only)",
    "short_id": "hex string 1-16 characters",
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

# Path to the VLESS configuration file
_VLESS_CONFIG_PATH = os.getenv(
    "VLESS_CONFIG_PATH", os.path.join(os.getcwd(), "vless_config.json")
)

# SNI options for camouflage (Reality "dest").
#
# IMPORTANT: these are well-known options, NOT a guarantee of DPI bypass. In
# practice mobile operators (especially in RU) increasingly fingerprint
# Reality traffic toward "typical VPN-guide" domains — first of all
# www.microsoft.com. Known case: a specific mobile operator blocked SNI
# www.microsoft.com (the handshake never reached Xray at all, even though
# the IP:port were reachable), and switching to yahoo.com on the same
# VPS/port/keys immediately fixed it. If the client will not connect while
# network/port/keys work — the first thing to try is switching SNI to a
# less "typical VPN-guide" domain.
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

# Recommended ports for VLESS-Reality (priority order)
# - 443: Standard HTTPS (watched by DPI)
# - 8443: Alternative HTTPS (⭐ recommended)
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

# Default configuration
DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",
    "port": 443,
    "uuid": "",
    "public_key": "",
    "private_key": "",
    "short_id": "",
    # yahoo.com instead of www.microsoft.com: microsoft is the most "typical
    # VPN-guide" SNI, and some mobile operators DPI-block it before Xray
    # (see the comment on AVAILABLE_SNI). Verified: yahoo.com works where
    # microsoft was cut, with the same VPS/port/keys.
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
    Build a minimal legacy Reality/VLESS contract for older client exports.

    This format must stay stable so `TelegramHelper` can read and reuse
    `TelegramSimple` legacy artifacts without an immediate VPS migration.
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
    Check that a legacy Reality/VLESS artifact contains all required fields.
    """
    missing = [
        field
        for field in LEGACY_VLESS_REQUIRED_FIELDS
        if payload.get(field) in (None, "")
    ]
    return len(missing) == 0, missing


def _normalize_clients(config: Dict) -> None:
    """
    Normalize the VLESS client list.
    """
    clients = config.get("clients") or []
    if not isinstance(clients, list):
        clients = []

    # If there are no clients but uuid is set — create a default client.
    if not clients and config.get("uuid"):
        clients = [
            {
                "name": "default",
                "uuid": config.get("uuid"),
                "created_at": datetime.now().isoformat(),
            }
        ]

    # Make sure the default client is synced with config["uuid"]
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
    UUID used to check whether the config is complete: root or any client in clients[].

    Previously /ver and "configured" only looked at config["uuid"], so with
    several clients and no root UUID duplicate it showed "no UUID".
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
    """Load VLESS configuration from file"""
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
    """Save VLESS configuration to file"""
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
    """Check whether VLESS-Reality is enabled"""
    config = _load_config()
    return config.get("enabled", False)


def enable_vless() -> Tuple[bool, str]:
    """
    Enable VLESS-Reality

    Returns:
        Tuple[bool, str]: (success, message)
    """
    config = _load_config()
    # Pull UUID from clients[] if the root field is empty (historical format).
    eu = _effective_uuid(config)
    if eu and not (config.get("uuid") or "").strip():
        config["uuid"] = eu
        _normalize_clients(config)

    # Check that all required parameters are set
    required = ["server", "uuid", "public_key", "short_id"]
    missing = [key for key in required if not (config.get(key) or "").strip()]

    if missing:
        return False, f"Required parameters are not set: {', '.join(missing)}"

    config["enabled"] = True
    if _save_config(config):
        logger.info("VLESS-Reality enabled")
        return True, "✅ VLESS-Reality enabled"

    return False, "❌ Failed to save configuration"


def disable_vless() -> Tuple[bool, str]:
    """
    Disable VLESS-Reality

    Returns:
        Tuple[bool, str]: (success, message)
    """
    config = _load_config()
    config["enabled"] = False

    if _save_config(config):
        logger.info("VLESS-Reality disabled")
        return True, "🔴 VLESS-Reality disabled"

    return False, "❌ Failed to save configuration"


def get_vless_status() -> Dict:
    """
    Get VLESS-Reality status

    Returns:
        Dict with status information
    """
    config = _load_config()
    eff_uuid = _effective_uuid(config)

    # Check the configuration (UUID may exist only on clients[] entries)
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
    Fields for the VLESS block in /ver: server in the config, VPS address hint from .env, list of missing keys.

    "Server" in VLESS is the public IP or domain clients use to connect;
    if the field is empty, Reality does not know where to dial even if the other fields are filled.
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
    Get VLESS configuration (optionally with secrets)

    Args:
        include_secrets: whether to include private keys

    Returns:
        Dict with the configuration
    """
    config = _load_config()

    if not include_secrets:
        # Mask secret data
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
_PUBLIC_IP_TTL = 3600.0  # public VPS IP is stable — cache for an hour


def get_server_public_ip() -> Optional[str]:
    """
    Get the server's public IP address.
    Uses several methods for reliability. The result is cached for
    _PUBLIC_IP_TTL seconds so /start does not hit the network on every call.

    Returns:
        IP address or None if it could not be determined
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

    # List of services for IP detection
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
                # Basic IP validation
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

    # Fallback: try to obtain it via socket
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        # Check that this is not a local IP
        if not ip.startswith(("10.", "172.", "192.168.", "127.")):
            logger.info(f"Detected server IP via socket: {ip}")
            _PUBLIC_IP_CACHE.update(ip=ip, ts=now)
            return ip
    except Exception as e:
        logger.debug(f"Failed to get IP via socket: {e}")

    return None


def set_vless_server(server: Optional[str] = None) -> Tuple[bool, str]:
    """
    Set the VLESS server address.

    Args:
        server: IP or domain. If None — auto-detect.

    Returns:
        Tuple[bool, str]: (success, message)
    """
    # Auto-detect IP if not specified
    if not server or not server.strip():
        detected_ip = get_server_public_ip()
        if detected_ip:
            server = detected_ip
            auto_detected = True
        else:
            return (
                False,
                "❌ Could not auto-detect the server IP\n\nUse: /vless_set_server <IP>",
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


def set_vless_port(port: int) -> Tuple[bool, str]:
    """
    Set the VLESS server port.

    Recommended ports: 443, 8443, 2053, 2083, 2087, 2096, 8880
    """
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False, "❌ Port must be a number from 1 to 65535"

    config = _load_config()
    old_port = config.get("port", 443)
    config["port"] = port

    if _save_config(config):
        msg = f"✅ Port set: {port}"
        if old_port != port:
            msg += f"\n📝 Previous port: {old_port}"
        if port in RECOMMENDED_PORTS:
            msg += "\n⭐ This is a recommended port"
        else:
            msg += f"\n💡 Recommended ports: {', '.join(map(str, RECOMMENDED_PORTS[:4]))}..."
        # Next-steps (systemctl / ufw / /xray_restart / re-issue URI)
        # are shown by handlers._legacy_vless_apply_followup after apply.
        return True, msg
    return False, "❌ Failed to save"


def get_recommended_ports() -> list:
    """Get the list of recommended ports for VLESS-Reality."""
    return RECOMMENDED_PORTS.copy()


def set_vless_uuid(uuid: str) -> Tuple[bool, str]:
    """Set the VLESS client UUID"""
    if not uuid or not uuid.strip():
        return False, "❌ UUID cannot be empty"

    # Basic UUID format validation
    uuid = uuid.strip()
    if len(uuid) < 32:
        return False, "❌ UUID is too short"

    config = _load_config()
    config["uuid"] = uuid
    _normalize_clients(config)
    for client in config.get("clients", []):
        if client.get("name") == "default":
            client["uuid"] = uuid
            break

    if _save_config(config):
        return True, f"✅ UUID set: {uuid[:8]}...{uuid[-4:]}"
    return False, "❌ Failed to save"


def set_vless_public_key(public_key: str) -> Tuple[bool, str]:
    """Set the Reality public key"""
    if not public_key or not public_key.strip():
        return False, "❌ Public key cannot be empty"

    config = _load_config()
    config["public_key"] = public_key.strip()

    if _save_config(config):
        return True, f"✅ Public key set"
    return False, "❌ Failed to save"


def set_vless_private_key(private_key: str) -> Tuple[bool, str]:
    """Set the Reality private key (server only)"""
    if not private_key or not private_key.strip():
        return False, "❌ Private key cannot be empty"

    config = _load_config()
    config["private_key"] = private_key.strip()

    if _save_config(config):
        return True, f"✅ Private key set"
    return False, "❌ Failed to save"


def set_vless_short_id(short_id: str) -> Tuple[bool, str]:
    """Set the session Short ID"""
    if not short_id or not short_id.strip():
        return False, "❌ Short ID cannot be empty"

    short_id = short_id.strip()

    # Validation: must be a hex string of 1-16 characters
    if not all(c in "0123456789abcdefABCDEF" for c in short_id):
        return False, "❌ Short ID must be a hex string (0-9, a-f)"

    if len(short_id) > 16:
        return False, "❌ Short ID must not exceed 16 characters"

    config = _load_config()
    config["short_id"] = short_id.lower()

    if _save_config(config):
        return True, f"✅ Short ID set: {short_id[:4]}..."
    return False, "❌ Failed to save"


def set_vless_sni(sni: str) -> Tuple[bool, str]:
    """Set SNI for camouflage"""
    if not sni or not sni.strip():
        return False, "❌ SNI cannot be empty"

    sni = sni.strip().lower()

    config = _load_config()
    config["sni"] = sni

    if _save_config(config):
        msg = f"✅ SNI set: {sni}"
        # Do not discourage a custom SNI — often it is exactly what fixes
        # the problem (see the AVAILABLE_SNI comment above about www.microsoft.com).
        if sni not in AVAILABLE_SNI:
            msg += (
                "\n💡 If the client will not connect with this SNI while network/port/keys work — "
                "try one of: " + ", ".join(AVAILABLE_SNI[:3])
            )
        return True, msg
    return False, "❌ Failed to save"


def set_vless_fingerprint(fingerprint: str) -> Tuple[bool, str]:
    """Set the TLS fingerprint"""
    if not fingerprint or not fingerprint.strip():
        return False, "❌ Fingerprint cannot be empty"

    fingerprint = fingerprint.strip().lower()

    if fingerprint not in AVAILABLE_FINGERPRINTS:
        return (
            False,
            f"❌ Unknown fingerprint. Available: {', '.join(AVAILABLE_FINGERPRINTS)}",
        )

    config = _load_config()
    config["fingerprint"] = fingerprint

    if _save_config(config):
        return True, f"✅ Fingerprint set: {fingerprint}"
    return False, "❌ Failed to save"


def set_nginx_fallback(enabled: bool, port: int = 8443) -> Tuple[bool, str]:
    """Enable/disable Nginx SNI fallback in Xray config."""
    if port < 1 or port > 65535:
        return False, "❌ Port must be from 1 to 65535"

    config = _load_config()
    config["nginx_fallback_enabled"] = enabled
    config["nginx_fallback_port"] = port

    if _save_config(config):
        state = "enabled" if enabled else "disabled"
        return True, f"✅ Nginx fallback {state} (port {port})"
    return False, "❌ Failed to save"


def set_nginx_domains(headscale_domain: str, ha_domain: str = "") -> Tuple[bool, str]:
    """Set domains for Nginx SNI routing."""
    headscale_domain = headscale_domain.strip()
    ha_domain = ha_domain.strip()

    if not headscale_domain:
        return False, "❌ Headscale domain cannot be empty"

    config = _load_config()
    config["headscale_domain"] = headscale_domain
    config["ha_domain"] = ha_domain

    if _save_config(config):
        msg = f"✅ Headscale domain: {headscale_domain}"
        if ha_domain:
            msg += f"\n✅ Home Assistant domain: {ha_domain}"
        return True, msg
    return False, "❌ Failed to save"


def get_nginx_sni_config() -> Tuple[bool, str]:
    """Generate Nginx stream SNI config for copy-paste to VPS."""
    config = _load_config()
    headscale_domain = config.get("headscale_domain", "")
    ha_domain = config.get("ha_domain", "")
    nginx_port = config.get("nginx_fallback_port", 8443)

    if not headscale_domain:
        return False, "❌ Headscale domain is not set. Use /nginx_set_domain"

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
    """Generate a new UUID for VLESS"""
    import uuid

    return str(uuid.uuid4())


def generate_short_id(length: int = 8) -> str:
    """Generate a new Short ID (hex string)"""
    if length < 1:
        length = 1
    if length > 16:
        length = 16
    return secrets.token_hex(length // 2 + length % 2)[:length]


def generate_reality_keys() -> Tuple[Optional[str], Optional[str], str]:
    """
    Generate an x25519 key pair for Reality.

    Tries `xray x25519` if available, otherwise generates in Python.

    Returns:
        Tuple[private_key, public_key, method]: keys and generation method
    """
    # Try xray for key generation
    try:
        result = subprocess.run(
            ["xray", "x25519"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            lines = output.split("\n")
            private_key = None
            public_key = None

            # Support both `xray x25519` output formats:
            #   old:      "Private key: ..."         / "Public key: ..."
            #   new (26.x): "PrivateKey: ..."     / "Password (PublicKey): ..."
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

    # Fallback: generate with cryptography
    try:
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        private_key_obj = X25519PrivateKey.generate()
        public_key_obj = private_key_obj.public_key()

        # Raw bytes → URL-safe base64
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

    # WARNING: do not substitute x25519 keys with random bytes.
    # That yields invalid Reality configs and hard-to-debug connection failures.
    logger.error(
        "Unable to generate valid Reality keys: xray and cryptography are unavailable"
    )
    return None, None, "unavailable"


def generate_all_keys() -> Tuple[bool, Dict, str]:
    """
    Generate all keys for VLESS-Reality.

    Returns:
        Tuple[success, keys_dict, message]
    """
    try:
        uuid = generate_uuid()
        short_id = generate_short_id(8)
        private_key, public_key, method = generate_reality_keys()

        if not private_key or not public_key:
            return False, {}, "❌ Failed to generate Reality keys"

        keys = {
            "uuid": uuid,
            "short_id": short_id,
            "private_key": private_key,
            "public_key": public_key,
            "generation_method": method,
        }

        # Persist to configuration
        config = _load_config()
        config["uuid"] = uuid
        config["short_id"] = short_id
        config["private_key"] = private_key
        config["public_key"] = public_key
        _normalize_clients(config)
        # Keep the default client UUID in sync
        for client in config.get("clients", []):
            if client.get("name") == "default":
                client["uuid"] = uuid
                break

        if _save_config(config):
            return True, keys, f"✅ Keys generated (method: {method})"

        return True, keys, "⚠️ Keys generated but not saved to config"

    except Exception as e:
        logger.error(f"Error generating keys: {e}")
        return False, {}, f"❌ Key generation error: {e}"


def test_connection() -> Tuple[bool, str]:
    """
    Test connectivity to the VLESS server.

    Returns:
        Tuple[success, message]
    """
    config = _load_config()

    if not config.get("enabled"):
        return False, "⚠️ VLESS-Reality is not enabled"

    server = config.get("server")
    port = config.get("port", 443)

    if not server:
        return False, "❌ Server is not configured"

    # Simple port reachability check
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((server, port))
        sock.close()

        if result == 0:
            return True, f"✅ Server {server}:{port} is reachable"
        else:
            return False, f"❌ Server {server}:{port} is unreachable (code: {result})"
    except socket.gaierror:
        return False, f"❌ Could not resolve hostname: {server}"
    except socket.timeout:
        return False, f"❌ Connection timed out to {server}:{port}"
    except Exception as e:
        return False, f"❌ Connection error: {e}"


def export_client_config() -> Dict:
    """
    Export client configuration (without the private key).

    Returns:
        Dict with client configuration
    """
    config = _load_config()

    return build_legacy_vless_contract(config)


def save_vless_config_files(output_dir: str = None) -> Tuple[bool, str, List[str]]:
    """
    Save VLESS configuration to files (JSON and TXT).

    Creates files similar to those downloaded by auto_setup_vps.sh:
    - vless_config_<IP>.json
    - vless_config_<IP>.txt

    Args:
        output_dir: Output folder (default ./vless_configs)

    Returns:
        Tuple[success, message, list_of_created_files]
    """
    config = _load_config()

    # Ensure configuration is complete
    server = config.get("server", "")
    if not server:
        return False, "❌ Server is not configured. Use /vless_set_server first", []

    port = config.get("port", 443)
    uuid = config.get("uuid", "")
    public_key = config.get("public_key", "")
    short_id = config.get("short_id", "")
    sni = config.get("sni", "www.microsoft.com")
    fingerprint = config.get("fingerprint", "chrome")

    if not uuid or not public_key:
        return (
            False,
            "❌ UUID or Public Key is not set. Use /vless_gen_keys",
            [],
        )

    # Generate VLESS link
    vless_link = generate_vless_link("VPS-Reality")

    # Resolve output folder
    if output_dir is None:
        # Look for vless_configs relative to this file or CWD
        base_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(base_dir, "vless_configs")

    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        return False, f"❌ Failed to create folder {output_dir}: {e}", []

    created_files = []

    # Filename based on server IP
    safe_server = server.replace(":", "_").replace("/", "_")

    # 1. Save JSON config
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
        return False, f"❌ Write error {json_path}: {e}", created_files

    # 2. Save text config
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
🔗 VLESS Link (for Hiddify/Foxray/v2rayNG/NekoRay):

{vless_link}

═══════════════════════════════════════════════════════════════
"""

    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(txt_content)
        created_files.append(txt_path)
        logger.info(f"Saved VLESS TXT config to {txt_path}")
    except Exception as e:
        return False, f"❌ Write error {txt_path}: {e}", created_files

    return True, f"✅ Configs saved to {output_dir}", created_files


def _reconcile_reality_from_live_xray(
    xray_config_path: str = "/usr/local/etc/xray/config.json",
) -> None:
    """Pull Reality parameters from the live Xray config before generating a link.

    Why: links are built from ``vless_config.json``. If an admin edits
    ``/usr/local/etc/xray/config.json`` directly (typical case — change SNI to a
    less generic domain, bypassing the bot), the bot silently returns a link with
    the OLD ``sni``/``pbk`` — the client cannot connect even though the server
    is healthy and a working direct link exists. We compare against the live
    config and pull ``sni``/``short_id``/``port``/``uuid``/keys so the bot always
    emits what Xray is actually listening on.

    No-op if: the file is missing; it has no vless+Reality inbound (panel-managed
    3x-ui, etc.); parameters already match. The expensive derivation of
    ``public_key`` from ``privateKey`` (subprocess/cryptography inside
    ``sync_from_xray_config``) runs only on a real drift — ordinary link
    generation stays cheap.
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

    # No Reality inbound — the bot does not manage this local Xray (panel-managed).
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
            "VLESS: auto-reconcile with live Xray before link generation (%s)", msg
        )
    else:
        logger.warning("VLESS: auto-reconcile with live Xray failed: %s", msg)


def generate_vless_link(comment: str = "sing-box-VLESS") -> str:
    """
    Generate a standard vless:// link for import into clients (Hiddify, v2rayNG, etc)
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
    Generate a vless:// link for the given UUID.
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
    Find a VLESS client by name or UUID.
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
    Generate a vless:// link for a specific client.
    """
    _reconcile_reality_from_live_xray()
    client = get_client(name_or_uuid)
    if not client:
        return False, "❌ Client not found", ""

    client_name = client.get("name") or "client"
    client_uuid = client.get("uuid") or ""
    if not client_uuid:
        return False, f"❌ Client {client_name} has no UUID", ""

    config = _load_config()
    if not (config.get("server") or "").strip():
        return (
            False,
            "❌ VLESS config has no `server` (IP or domain).\n"
            "Run `/vless_set_server` with the server IP or domain, or `/vless_sync` "
            "after configuring Xray.",
            "",
        )
    if not (config.get("public_key") or "").strip():
        return (
            False,
            "❌ No Reality `public_key` in the bot config.\n"
            "Generate keys: `/vless_gen_keys` or pull from Xray: `/vless_sync`.",
            "",
        )

    link = generate_vless_link_for_uuid(
        client_uuid,
        visible_profile_name("VLESS", config.get("server", ""), client_name),
    )
    if not link:
        return (
            False,
            "❌ Failed to generate VLESS link. Check server, UUID, and Public Key settings",
            "",
        )

    return True, f"✅ Link for client {client_name} is ready", link


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
        logger.error(f"Failed to generate QR image: {e}")
        return False, None, f"❌ QR generation error: {e}"


def build_client_qr_payload(name_or_uuid: str) -> Tuple[bool, str, Dict]:
    """
    Prepare client data for sending a QR code via Telegram.
    """
    client = get_client(name_or_uuid)
    if not client:
        return False, "❌ Client not found", {}

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
    return True, "✅ QR payload for client is ready", payload


def list_clients() -> List[Dict]:
    """
    Get the list of VLESS clients.
    """
    config = _load_config()
    _normalize_clients(config)
    return config.get("clients", [])


def add_client(name: str, client_uuid: Optional[str] = None) -> Tuple[bool, str, Dict]:
    """
    Add a VLESS client.
    """
    if not name or not name.strip():
        return False, "❌ Client name cannot be empty", {}

    name = name.strip()
    config = _load_config()
    _normalize_clients(config)

    for client in config.get("clients", []):
        if client.get("name") == name:
            return False, f"❌ Client named {name} already exists", {}

    if not client_uuid:
        client_uuid = generate_uuid()

    client = {
        "name": name,
        "uuid": client_uuid,
        "created_at": datetime.now().isoformat(),
    }

    config["clients"].append(client)
    if _save_config(config):
        return True, f"✅ Client added: {name}", client
    return False, "❌ Failed to save", {}


def remove_client(name_or_uuid: str) -> Tuple[bool, str]:
    """
    Remove a client by name or UUID.
    """
    if not name_or_uuid or not name_or_uuid.strip():
        return False, "❌ Specify a client name or UUID"

    name_or_uuid = name_or_uuid.strip()
    config = _load_config()
    _normalize_clients(config)

    if name_or_uuid == "default":
        return False, "❌ Cannot delete the default client"

    clients = config.get("clients", [])
    new_clients = [
        c
        for c in clients
        if c.get("name") != name_or_uuid and c.get("uuid") != name_or_uuid
    ]

    if len(new_clients) == len(clients):
        return False, "❌ Client not found"

    config["clients"] = new_clients
    if _save_config(config):
        return True, "✅ Client removed"
    return False, "❌ Failed to save"


def export_subscription_list() -> List[str]:
    """
    Build a list of links for subscription (raw list).
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
    Build a base64 subscription (as most clients expect).
    """
    import base64

    links = export_subscription_list()
    raw = "\n".join([x for x in links if x]).strip()
    if not raw:
        return ""
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")




def export_singbox_config() -> Dict:
    """
    Generate a minimal sing-box client configuration.
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
    Generate a minimal Clash Meta configuration (YAML).
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
    Generate an Xray-core configuration.

    Args:
        is_server: True for server configuration, False for client

    Returns:
        Dict with Xray configuration
    """
    config = _load_config()

    if is_server:
        # Server configuration
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
            # NB: do NOT blackhole ::/0. Reality dials dest (serverNames[0]:443)
            # on every connection for real TLS; if dest resolves to IPv6, blackhole
            # killed that relay → Xray rejected ALL clients as "invalid connection".
            # Missing IPv6 egress is handled correctly by freedom/UseIPv4 below
            # (+ sniffing destOverride on inbound for literal IPv6 from the client).
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
                    # This server has no IPv6 egress: routing below blackholes all of ::/0.
                    # Without destOverride the client (Clash Meta, Karing, HAPP) sends a
                    # literal destination IPv6 (e.g. claude.ai) → it matches ::/0 →
                    # blackhole → the client gets EOF on IPv6 sites. With sniffing the
                    # literal IP is replaced by the SNI domain, misses ::/0, and
                    # freedom/UseIPv4 exits over IPv4. routeOnly=False so the rewrite
                    # also affects dialing, not only routing.
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
        # Client configuration
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
    Sync keys from the xray config into vless_config.json.

    Reads privateKey from the xray config and derives the matching public_key.
    Needed when xray uses different keys than vless_config.json.

    Args:
        xray_config_path: Path to the xray config

    Returns:
        Tuple[success, message]
    """
    # Check that the file exists
    if not os.path.exists(xray_config_path):
        return False, f"❌ File not found: {xray_config_path}"

    try:
        with open(xray_config_path, "r", encoding="utf-8") as f:
            xray_config = json.load(f)
    except Exception as e:
        return False, f"❌ Error reading xray config: {e}"

    # Look for privateKey in the xray config structure
    private_key = None
    short_ids = []
    server_names = []
    uuid = None
    port = None

    try:
        # Try inbounds -> streamSettings -> realitySettings
        inbounds = xray_config.get("inbounds", [])
        for inbound in inbounds:
            if inbound.get("protocol") == "vless":
                port = inbound.get("port", port)

                # Get UUID from clients
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
        return False, f"❌ Error parsing xray config: {e}"

    if not private_key:
        return False, "❌ privateKey not found in xray config"

    # Derive public_key from private_key
    public_key = None

    # Method 1: use xray x25519
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
                # Public key is shown as "Public key:" — this is Password
                if "Public key:" in line:
                    public_key = line.split(":", 1)[1].strip()
                    break
    except Exception as e:
        logger.warning(f"xray x25519 failed: {e}")

    # Method 2: fallback — Python cryptography (for Docker)
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
            "❌ Failed to obtain public_key. Install cryptography: pip install cryptography",
        )

    # Update vless_config.json
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
        return True, "✅ Configuration is already in sync"

    if _save_config(config):
        fields_str = ", ".join(updated_fields)
        logger.info(f"Synced from xray config: {fields_str}")
        return (
            True,
            f"✅ Synced from xray config:\n{fields_str}\n\n🔑 Public Key (for the client):\n`{public_key}`",
        )

    return False, "❌ Failed to save configuration"


def reset_config() -> Tuple[bool, str]:
    """Reset VLESS configuration to defaults"""
    if _save_config(dict(DEFAULT_CONFIG)):
        return True, "✅ VLESS configuration reset"
    return False, "❌ Failed to reset configuration"


# === Xray Management ===


def check_xray_installed() -> Tuple[bool, str, Dict]:
    """
    Check whether Xray is installed on the server.

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

    # Check if we are running in Docker
    in_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER")
    info["in_docker"] = in_docker

    if in_docker:
        # From Docker we cannot inspect Xray on the host
        # Try checking port 443 via the public IP (not localhost)
        port_open = False
        server_ip = None

        # Get the server public IP
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

        port_status = "✅ open" if port_open else "⚠️ unreachable"
        # Escape dots in IP for Markdown V2
        escaped_ip = server_ip.replace(".", "\\.") if server_ip else None
        server_info = f" \\({escaped_ip}\\)" if escaped_ip else ""

        message = f"""📦 *Xray status* \\(from Docker\\)

🔌 Port 443{server_info}: {port_status}

_The bot is running in Docker\\._

*Check via SSH:*
`systemctl status xray`
`ss \\-tlnp \\| grep 443`"""

        return port_open, message, info

    # Check that xray exists (when not in Docker)
    try:
        result = subprocess.run(
            ["which", "xray"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return False, "❌ Xray is not installed", info

        info["installed"] = True
    except Exception as e:
        return False, f"❌ Check error: {e}", info

    # Get version
    try:
        result = subprocess.run(
            ["xray", "version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Parse version from output
            lines = result.stdout.strip().split("\n")
            if lines:
                info["version"] = lines[0]
    except Exception:
        pass

    # Check systemd status
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

    # Check port 443
    try:
        result = subprocess.run(
            ["ss", "-tlnp"], capture_output=True, text=True, timeout=5
        )
        info["port_listening"] = ":443" in result.stdout
    except Exception:
        pass

    # Check that the config exists
    config_path = "/usr/local/etc/xray/config.json"
    info["config_exists"] = os.path.exists(config_path)

    # Build the message
    status_emoji = "🟢" if info["running"] else "🔴"
    port_emoji = "✅" if info["port_listening"] else "❌"
    config_emoji = "✅" if info["config_exists"] else "❌"

    message = f"""📦 *Xray status*

{status_emoji} Installed: ✅
📌 Version: `{info["version"] or "unknown"}`
⚡ Running: {"✅" if info["running"] else "❌"}
🔌 Port 443: {port_emoji}
📄 Config: {config_emoji}"""

    return True, message, info


def get_xray_config() -> Tuple[bool, str, dict]:
    """
    Get the current Xray configuration from the server.

    Returns:
        Tuple[success, message, config_dict]
    """
    config_path = "/usr/local/etc/xray/config.json"

    # Check if we are running in Docker
    in_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER")

    if in_docker:
        # From Docker show SSH instructions
        message = """📄 *Xray configuration*

_The bot is running in Docker and has no access to host files\\._

**Check the configuration via SSH:**
```
cat /usr/local/etc/xray/config\\.json
```

**Or open it for editing:**
```
nano /usr/local/etc/xray/config\\.json
```

**After changes restart:**
```
xray \\-test \\-config /usr/local/etc/xray/config\\.json
systemctl restart xray
```"""
        return True, message, {"in_docker": True}

    # Not in Docker — try reading the file
    if not os.path.exists(config_path):
        return False, "❌ Configuration not found: " + config_path, {}

    try:
        with open(config_path, "r") as f:
            config = json.load(f)

        # Format main parameters
        inbounds = config.get("inbounds", [])

        info_lines = ["📄 *Current Xray configuration*\n"]

        for i, inbound in enumerate(inbounds):
            port = inbound.get("port", "N/A")
            protocol = inbound.get("protocol", "N/A")
            tag = inbound.get("tag", f"inbound-{i}")

            info_lines.append(f"**{tag}:** {protocol} on port {port}")

            # Reality settings
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
        return False, f"❌ Error reading configuration: {e}", {}


def install_xray() -> Tuple[bool, str]:
    """
    Install Xray on the server.

    Returns:
        Tuple[success, message]
    """
    try:
        # Check whether it is already installed
        result = subprocess.run(["which", "xray"], capture_output=True, timeout=5)
        if result.returncode == 0:
            return True, "✅ Xray is already installed"

        # Check if we are in Docker (curl may be unavailable)
        in_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER")

        if in_docker:
            return (
                False,
                """❌ Cannot install from a Docker container

**Install Xray manually via SSH:**

```bash
ssh root@<SERVER_IP>

bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

xray version
```

After install use:
/xray\\_apply — apply configuration
/xray\\_start — start""",
            )

        logger.info("Installing Xray...")

        # Download and run the installer
        install_cmd = 'bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install'

        result = subprocess.run(
            install_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes for install
        )

        if result.returncode != 0:
            logger.error(f"Xray install failed: {result.stderr}")
            error_msg = result.stderr[:300] if result.stderr else "Unknown error"
            return (
                False,
                f"""❌ Xray install error

**Install manually via SSH:**
```bash
ssh root@<SERVER_IP>
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
```

Error: {error_msg}""",
            )

        logger.info("Xray installed successfully")
        return (
            True,
            "✅ Xray installed successfully!\n\nNow run:\n/xray_apply — apply configuration\n/xray_start — start",
        )

    except subprocess.TimeoutExpired:
        return False, "❌ Install timed out (5 minutes)"
    except Exception as e:
        logger.error(f"Error installing Xray: {e}")
        return False, f"❌ Error: {e}"


def _xray_service_group() -> Optional[str]:
    """Effective Xray service group (for config.json permissions).

    The official systemd unit runs Xray as `User=nobody` WITHOUT an explicit
    `Group=`, so we take the user's primary group (on Debian/Ubuntu that is
    `nogroup`). Returns None if it cannot be determined — then the caller
    leaves the config world-readable (0644) so Xray is not locked out.
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
    """Narrow world-read of the private Reality key in config.json to the service group.

    Fail-safe: tighten to 0640 ONLY if the group was identified and `chgrp`
    succeeded. Otherwise keep 0644 — slightly broader permissions beat breaking
    service reads (Xray runs as nobody without an explicit Group=).
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
                f"VLESS: chgrp {grp} {path} failed ({(r.stderr or '').strip()}); "
                f"leaving 0644"
            )
    except Exception as e:
        logger.warning(f"VLESS: failed to tighten permissions on {path}: {e}")


def apply_xray_config() -> Tuple[bool, str]:
    """
    Apply the current VLESS configuration to the Xray server.

    Returns:
        Tuple[success, message]
    """
    config_path = "/usr/local/etc/xray/config.json"

    try:
        # Generate server configuration
        xray_config = export_xray_config(is_server=True)

        # Ensure required fields exist
        vless_config = _load_config()
        if not vless_config.get("uuid") or not vless_config.get("private_key"):
            return False, "❌ Generate keys first: /vless_gen_keys"

        config_json = json.dumps(xray_config, indent=2, ensure_ascii=False)
        # Write 0644 (guaranteed readable by the service), then fail-safe tighten to
        # 0640+chgrp if the service group can be determined (the private Reality key
        # must not be world-readable). On failure it stays 0644.
        write_result = _host_write_text(config_path, config_json, mode="0644")
        if write_result.returncode != 0:
            error = write_result.stderr.strip() or write_result.stdout.strip()
            return False, f"❌ Failed to write host Xray config:\n`{error}`"

        _restrict_xray_config(config_path)

        result = _host_run(
            ["xray", "-test", "-config", config_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False, f"❌ Configuration error:\n```\n{result.stderr[:500]}\n```"

        return True, f"✅ Xray configuration written and verified\n\n📄 `{config_path}`"

    except OSError as e:
        # Directory unavailable (volume not mounted)
        logger.error(f"Cannot write xray config: {e}")
        return False, (
            "❌ Failed to write Xray config\n\n"
            "The `/usr/local/etc/xray` volume may not be mounted.\n"
            "Apply the config manually: `/vless_export` → Xray Server Config"
        )
    except Exception as e:
        logger.error(f"Error applying Xray config: {e}")
        return False, f"❌ Error: {e}"


def start_xray() -> Tuple[bool, str]:
    """Start the Xray service"""
    # Check Docker
    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER"):
        return (
            False,
            "❌ No systemctl access from Docker\n\nOn the server: `systemctl start xray`",
        )

    try:
        # Enable autostart
        subprocess.run(["systemctl", "enable", "xray"], capture_output=True, timeout=10)

        # Start
        result = subprocess.run(
            ["systemctl", "start", "xray"], capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            return False, f"❌ Start error:\n```\n{result.stderr}\n```"

        return True, "✅ Xray started!"

    except Exception as e:
        return False, f"❌ Error: {e}"


def stop_xray() -> Tuple[bool, str]:
    """Stop the Xray service"""
    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER"):
        return (
            False,
            "❌ No systemctl access from Docker\n\nOn the server: `systemctl stop xray`",
        )

    try:
        result = subprocess.run(
            ["systemctl", "stop", "xray"], capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            return False, f"❌ Stop error:\n```\n{result.stderr}\n```"

        return True, "🔴 Xray stopped"

    except Exception as e:
        return False, f"❌ Error: {e}"


def restart_xray() -> Tuple[bool, str]:
    """Restart the Xray service"""
    try:
        result = _host_run(
            ["systemctl", "restart", "xray"], capture_output=True, text=True, timeout=15
        )

        if result.returncode != 0:
            return False, f"❌ Restart error:\n```\n{result.stderr}\n```"

        # Check status
        import time

        time.sleep(1)

        result = _host_run(
            ["systemctl", "is-active", "xray"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.stdout.strip() == "active":
            return True, "✅ Xray restarted and is running!"
        else:
            return (
                False,
                "⚠️ Xray restarted but is not active. Check logs: /xray_logs",
            )

    except Exception as e:
        return False, f"❌ Error: {e}"


def get_xray_logs(lines: int = 30) -> Tuple[bool, str]:
    """Get the latest Xray logs"""
    try:
        result = subprocess.run(
            ["journalctl", "-u", "xray", "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        logs = result.stdout.strip()
        if not logs:
            return True, "📋 Logs are empty"

        # Truncate if too long
        if len(logs) > 3500:
            logs = logs[-3500:]

        return True, f"📋 *Xray logs \\(last {lines}\\):*\n```\n{logs}\n```"

    except Exception as e:
        return False, f"❌ Error: {e}"
