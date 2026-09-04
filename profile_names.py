# -*- coding: utf-8 -*-
"""Helpers for user-visible VPN profile names."""

import re


def server_marker(server: str) -> str:
    """Return a short stable server marker for imported profile names."""
    server = (server or "").strip()
    parts = server.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return "".join(parts)[-4:]

    marker = re.sub(r"[^A-Za-z0-9]+", "", server.split(".")[0])
    return marker[:6] or "srv"


def client_marker(value: str) -> str:
    """Return a short client marker while preserving canonical bot IDs."""
    value = (value or "").strip()
    match = re.search(r"ID\d+_\d+", value)
    if match:
        return match.group(0)

    marker = re.sub(r"[^A-Za-z0-9_]+", "", value)
    return marker[:12] or "client"


def visible_profile_name(protocol: str, server: str, client_name: str = "") -> str:
    """Build the name shown by clients: <Protocol>-<server>-<client>."""
    prefix = (protocol or "Profile").strip() or "Profile"
    marker = server_marker(server)
    client = (client_name or "").strip()
    if client:
        return f"{prefix}-{marker}-{client}"
    return f"{prefix}-{marker}"
