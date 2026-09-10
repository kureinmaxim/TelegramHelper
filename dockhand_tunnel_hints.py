# -*- coding: utf-8 -*-
"""
SSH parameters for the /dockhand hint (tunnel to Dockhand on 127.0.0.1:8501 of the server).

Host priority: env vars → VLESS server → auto-detect public IP.
Port priority: DOCKHAND_SSH_PORT / TELEGRAMHELPER_SSH_PORT / SSH_PORT → 22.
SSH user: DOCKHAND_SSH_USER / TELEGRAMHELPER_SSH_USER → root.

In the -L string the remote target is 127.0.0.1 (not localhost); see build_ssh_tunnel_command.
"""

from __future__ import annotations

import logging
import os
from typing import List, NamedTuple

logger = logging.getLogger(__name__)


class DockhandSshParams(NamedTuple):
    host: str
    port: int
    user: str
    host_is_placeholder: bool
    notes: List[str]


def _first_nonempty_env(*keys: str) -> str:
    for key in keys:
        raw = os.getenv(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def _parse_ssh_port(raw: str, default: int = 22) -> int:
    try:
        p = int(raw.strip())
        if 1 <= p <= 65535:
            return p
    except (TypeError, ValueError):
        pass
    logger.warning("Invalid SSH port %r, using %s", raw, default)
    return default


def get_dockhand_ssh_params(*, resolve_public_ip: bool = True) -> DockhandSshParams:
    """Return host, port, and user for an ssh -L line (plus a 'fill in manually' flag).

    Args:
        resolve_public_ip: if False, skip network auto-detect of the public IP (faster for /start).
    """
    notes: List[str] = []

    port_raw = _first_nonempty_env(
        "DOCKHAND_SSH_PORT",
        "TELEGRAMHELPER_SSH_PORT",
        "SSH_PORT",
    )
    port = _parse_ssh_port(port_raw, 22) if port_raw else 22

    user = _first_nonempty_env("DOCKHAND_SSH_USER", "TELEGRAMHELPER_SSH_USER") or "root"

    host = _first_nonempty_env(
        "DOCKHAND_SSH_HOST",
        "TELEGRAMHELPER_SSH_HOST",
        "TELEGRAMHELPER_PUBLIC_HOST",
        "PUBLIC_HOST",
        "VPS_HOST",
    )
    if host:
        return DockhandSshParams(
            host=host, port=port, user=user, host_is_placeholder=False, notes=notes
        )

    try:
        import vless_manager

        vs = vless_manager.get_vless_status()
        server = (vs.get("server") or "").strip()
        if server:
            return DockhandSshParams(
                host=server, port=port, user=user, host_is_placeholder=False, notes=notes
            )
        if resolve_public_ip:
            detected = vless_manager.get_server_public_ip()
            if detected:
                return DockhandSshParams(
                    host=detected,
                    port=port,
                    user=user,
                    host_is_placeholder=False,
                    notes=notes,
                )
    except Exception as e:
        logger.debug("vless_manager hints for dockhand: %s", e)

    notes.append(
        "Could not determine the server IP/domain. Set it in .env next to compose, "
        "for example: DOCKHAND_SSH_HOST=your.ip or a domain; for a non-standard SSH port use DOCKHAND_SSH_PORT=2222"
    )
    return DockhandSshParams(
        host="YOUR_SERVER_IP",
        port=port,
        user=user,
        host_is_placeholder=True,
        notes=notes,
    )


def build_ssh_tunnel_command(
    params: DockhandSshParams, *, local_port: int = 8501, background: bool = False
) -> str:
    """One copy-paste line (no Markdown).

    On the remote side of the forward we use **127.0.0.1**, not ``localhost``: on macOS/Linux
    ``localhost`` may resolve to IPv6 (::1), and then the tunnel misses Streamlit on 127.0.0.1.

    Background mode: separate ``-f -N -L`` flags (compatible with OpenSSH on macOS; glued ``-fNL`` is parsed unpredictably by some shells).
    """
    inner = f"{local_port}:127.0.0.1:{local_port}"
    remote = f"{params.user}@{params.host}"
    port_opt = f"-p {params.port}"
    if background:
        return f"ssh -f -N -L {inner} {port_opt} {remote}"
    return f"ssh -L {inner} {port_opt} {remote}"
