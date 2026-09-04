# -*- coding: utf-8 -*-
"""
Параметры SSH для подсказки /dockhand (туннель к Dockhand на 127.0.0.1:8501 сервера).

Приоритет хоста: переменные окружения → server из VLESS → автоопределение публичного IP.
Приоритет порта: DOCKHAND_SSH_PORT / TELEGRAMHELPER_SSH_PORT / SSH_PORT → 22.
Пользователь SSH: DOCKHAND_SSH_USER / TELEGRAMHELPER_SSH_USER → root.

В строке -L удалённая цель — 127.0.0.1 (не localhost), см. build_ssh_tunnel_command.
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
    """Вернуть хост, порт и пользователя для строки ssh -L … (и флаг «подставьте вручную»).

    Args:
        resolve_public_ip: если False — не вызывать сетевое автоопределение публичного IP (быстрее для /start).
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
        "Не удалось определить IP/домен сервера. Задайте в .env рядом с compose, "
        "например: DOCKHAND_SSH_HOST=ваш.ip или домен, при нестандартном SSH — DOCKHAND_SSH_PORT=2222"
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
    """Одна строка для копирования (без Markdown).

    На удалённой стороне forward используем **127.0.0.1**, не ``localhost``: на macOS/Linux
    ``localhost`` может резолвиться в IPv6 (::1), тогда туннель не попадает в Streamlit на 127.0.0.1.

    Фоновый режим: отдельные флаги ``-f -N -L`` (совместимо с OpenSSH на macOS; склеенное ``-fNL`` у части оболочек парсится непредсказуемо).
    """
    inner = f"{local_port}:127.0.0.1:{local_port}"
    remote = f"{params.user}@{params.host}"
    port_opt = f"-p {params.port}"
    if background:
        return f"ssh -f -N -L {inner} {port_opt} {remote}"
    return f"ssh -L {inner} {port_opt} {remote}"
