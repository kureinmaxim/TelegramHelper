"""reticulum_manager.py — управление HA-стеком и Reticulum-мостом из бота.

HA-стек = три systemd-юнита на хосте:
  • ha-reticulum-bridge — мост Reticulum (TCP :50061), печатает bridge hash в лог;
  • ha-stub-grpc        — gRPC-заглушка Mi-Home (:50055);
  • ha-stub-udp         — UDP-заглушка (:50056).

Команды выполняются через host_utils.host_run (nsenter в Docker / прямой
subprocess на systemd-хосте). Потокобезопасно (threading.Lock), как остальные
менеджеры проекта.
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

# I2P (этап 3, путь 2): i2pd несёт I2P нативными туннелями, RNS ходит по TCP на
# localhost. Серверный туннель ha-bridge заворачивает мост (:50061) в I2P-destination.
I2PD_UNIT = "i2pd"
I2P_WEBCONSOLE = "http://127.0.0.1:7070/?page=i2p_tunnels"
I2P_CONSOLE = "http://127.0.0.1:7070/"  # главная: network status / tunnel success / routers

_lock = threading.Lock()


def _run(cmd, timeout: int = 15):
    """host_run с проглатыванием ошибок (None при недоступности)."""
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
    """Bridge destination hash из лога старта моста ('' если не найден)."""
    r = _run(["journalctl", "-u", BRIDGE_UNIT, "--no-pager", "-n", "200"], timeout=20)
    if not r:
        return ""
    found = ""
    for line in (r.stdout or "").splitlines():
        low = line.lower()
        if "destination" in low or "bridge hash" in low:
            m = re.search(r"[0-9a-f]{32}", line)
            if m:
                found = m.group(0)  # берём последнее (самый свежий старт)
    return found


def get_i2p_b32() -> str:
    """b32 серверного туннеля ha-bridge из веб-консоли i2pd ('' если не найден).

    Строка вида 'ha-bridge ⇒ <52symb>.b32.i2p:50061'. Транспортно-независим к
    bridge hash — это I2P-адрес назначения для клиентского туннеля (путь 2).
    """
    r = _run(["curl", "-s", "--max-time", "5", I2P_WEBCONSOLE], timeout=12)
    if not r or not (r.stdout or ""):
        return ""
    text = re.sub(r"<[^>]*>", " ", r.stdout)
    m = re.search(r"ha-bridge.*?([a-z2-7]{52}\.b32\.i2p)", text, re.S | re.I)
    return m.group(1) if m else ""


def get_i2p_status() -> Dict:
    """Статус I2P-пути (i2pd + серверный туннель ha-bridge)."""
    with _lock:
        installed = _unit_exists(I2PD_UNIT)
        active = _is_active(I2PD_UNIT) if installed else False
        return {
            "installed": installed,
            "active": active,
            "b32": get_i2p_b32() if active else "",
        }


def get_i2p_health() -> Dict:
    """Здоровье i2pd из web-консоли: network status, tunnel creation success rate,
    routers/floodfills, leasesets, transit, uptime. Пустые поля — если недоступно.

    Помогает понять «прогрелась ли сеть»: низкий success rate и LeaseSets=0 на
    свежем узле — норма первых минут; b32 моста публикуется после прогрева туннелей.
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
    """Полный статус HA-стека/Reticulum (TCP-мост + I2P-путь)."""
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
    """Перезапустить все три сервиса HA-стека."""
    with _lock:
        r = _run(["systemctl", "restart", *UNITS], timeout=30)
        if r is None:
            return False, "не удалось выполнить systemctl (нет прав/недоступен)"
        if r.returncode == 0:
            return True, "ha-stub-grpc, ha-stub-udp, ha-reticulum-bridge перезапущены"
        return False, (r.stderr or r.stdout or f"код возврата {r.returncode}").strip()[:300]
