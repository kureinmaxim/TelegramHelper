"""reticulum_manager.py — manage the HA stack and Reticulum bridge from the bot.

HA stack = three systemd units on the host:
  • ha-reticulum-bridge — Reticulum bridge (TCP :50061), prints the bridge hash to the log;
  • ha-stub-grpc        — Mi-Home gRPC stub (:50055);
  • ha-stub-udp         — UDP stub (:50056).

Commands run via host_utils.host_run (nsenter in Docker / direct
subprocess on a systemd host). Thread-safe (threading.Lock), like the other
project managers.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Dict, Tuple

from host_utils import host_run

logger = logging.getLogger(__name__)

UNITS = ("ha-reticulum-bridge", "ha-stub-grpc", "ha-stub-udp")
BRIDGE_UNIT = "ha-reticulum-bridge"
BRIDGE_PORT = 50061

# I2P (stage 3, path 2): i2pd carries I2P with native tunnels; RNS talks TCP to
# localhost. The ha-bridge server tunnel wraps the bridge (:50061) in an I2P destination.
I2PD_UNIT = "i2pd"
I2P_WEBCONSOLE = "http://127.0.0.1:7070/?page=i2p_tunnels"
I2P_CONSOLE = "http://127.0.0.1:7070/"  # home: network status / tunnel success / routers

_lock = threading.Lock()


def _run(cmd, timeout: int = 15):
    """host_run that swallows errors (None when unavailable)."""
    try:
        return host_run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        logger.debug("reticulum host_run %s failed: %s", cmd, exc)
        return None


def _is_active(unit: str) -> bool:
    r = _run(["systemctl", "is-active", unit])
    return bool(r and (r.stdout or "").strip() == "active")


def _unit_exists(unit: str) -> bool:
    r = _run(["systemctl", "list-unit-files", "--no-pager", "--type=service"])
    if not r:
        return False
    return any(line.startswith(unit + ".service") for line in (r.stdout or "").splitlines())


def _listening() -> bool:
    r = _run(["ss", "-tlnH"])
    return bool(r and f":{BRIDGE_PORT}" in (r.stdout or ""))


def get_bridge_hash() -> str:
    """Bridge destination hash from the bridge start log ('' if not found)."""
    r = _run(["journalctl", "-u", BRIDGE_UNIT, "--no-pager", "-n", "200"], timeout=20)
    if not r:
        return ""
    found = ""
    for line in (r.stdout or "").splitlines():
        low = line.lower()
        if "destination" in low or "bridge hash" in low:
            m = re.search(r"[0-9a-f]{32}", line)
            if m:
                found = m.group(0)  # keep the last match (most recent start)
    return found


def get_i2p_b32() -> str:
    """b32 of the ha-bridge server tunnel from the i2pd web console ('' if not found).

    Line looks like 'ha-bridge ⇒ <52symb>.b32.i2p:50061'. Independent of the
    bridge hash — this is the I2P destination for the client tunnel (path 2).
    """
    r = _run(["curl", "-s", "--max-time", "5", I2P_WEBCONSOLE], timeout=12)
    if not r or not (r.stdout or ""):
        return ""
    text = re.sub(r"<[^>]*>", " ", r.stdout)
    m = re.search(r"ha-bridge.*?([a-z2-7]{52}\.b32\.i2p)", text, re.S | re.I)
    return m.group(1) if m else ""


def get_i2p_status() -> Dict:
    """I2P path status (i2pd + ha-bridge server tunnel)."""
    with _lock:
        installed = _unit_exists(I2PD_UNIT)
        active = _is_active(I2PD_UNIT) if installed else False
        return {
            "installed": installed,
            "active": active,
            "b32": get_i2p_b32() if active else "",
        }


def get_i2p_health() -> Dict:
    """i2pd health from the web console: network status, tunnel creation success rate,
    routers/floodfills, leasesets, transit, uptime. Empty fields if unavailable.

    Helps tell whether the network has warmed up: a low success rate and LeaseSets=0 on
    a fresh node are normal for the first minutes; the bridge b32 is published after tunnels warm up.
    """
    with _lock:
        installed = _unit_exists(I2PD_UNIT)
        active = _is_active(I2PD_UNIT) if installed else False
        out = {
            "installed": installed, "active": active, "network": "",
            "success_rate": "", "routers": "", "floodfills": "",
            "leasesets": "", "transit": "", "uptime": "",
        }
        if not active:
            return out
        r = _run(["curl", "-s", "--max-time", "5", I2P_CONSOLE], timeout=12)
        if not r or not (r.stdout or ""):
            return out
        text = re.sub(r"<[^>]*>", " ", r.stdout)
        text = text.replace("&nbsp;", " ")

        def grab(pattern: str) -> str:
            m = re.search(pattern, text, re.I)
            return m.group(1).strip() if m else ""

        out["network"] = grab(r"Network status:\s*([A-Za-z ]+?)\s{2,}")
        out["success_rate"] = grab(r"Tunnel creation success rate:\s*([0-9]+%)")
        out["routers"] = grab(r"Routers:\s*([0-9]+)")
        out["floodfills"] = grab(r"Floodfills:\s*([0-9]+)")
        out["leasesets"] = grab(r"LeaseSets:\s*([0-9]+)")
        out["transit"] = grab(r"Transit Tunnels:\s*([0-9]+)")
        out["uptime"] = grab(r"Uptime:\s*([0-9A-Za-z, ]+?)\s{2,}")
        return out


def get_status() -> Dict:
    """Full HA-stack/Reticulum status (TCP bridge + I2P path)."""
    with _lock:
        installed = _unit_exists(BRIDGE_UNIT)
        services = {u: _is_active(u) for u in UNITS}
        i2pd_installed = _unit_exists(I2PD_UNIT)
        i2pd_active = _is_active(I2PD_UNIT) if i2pd_installed else False
        return {
            "installed": installed,
            "services": services,
            "bridge_active": services.get(BRIDGE_UNIT, False),
            "listening": _listening(),
            "bridge_hash": get_bridge_hash() if installed else "",
            "all_active": installed and all(services.values()),
            "i2pd_installed": i2pd_installed,
            "i2pd_active": i2pd_active,
            "i2p_b32": get_i2p_b32() if i2pd_active else "",
        }


def restart() -> Tuple[bool, str]:
    """Restart all three HA-stack services."""
    with _lock:
        r = _run(["systemctl", "restart", *UNITS], timeout=30)
        if r is None:
            return False, "could not run systemctl (no permission / unavailable)"
        if r.returncode == 0:
            return True, "ha-stub-grpc, ha-stub-udp, ha-reticulum-bridge restarted"
        return False, (r.stderr or r.stdout or f"exit code {r.returncode}").strip()[:300]
