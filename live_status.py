# -*- coding: utf-8 -*-
"""
Live status checks for transport protocols and Docker containers.

This module collects the real status of services (xray, hysteria-server,
caddy-naive, mtproto-proxy, sing-box, the dockhand container) on the
TelegramHelper host — independently of `enabled` flags in the transport
managers' JSON configs. Used by `/start` and admin diagnostic commands
so operators see the real state, not only what is written in JSON.

Architecture:

* The bot runs in the `telegram-helper-lite` Docker container. In `compose.yaml`
  it has `pid: host` and `privileged: true`, so host processes are visible
  from the container via `/proc`.
* The container has its own network namespace (no `network_mode: host`), but we
  read `/proc/1/net/{tcp,tcp6,udp,udp6}` — that is the host-init netns,
  i.e. the real VPS network stack. With `privileged: true` this works.
* The `dockhand` container is queried via the mounted `/var/run/docker.sock`
  with no third-party dependencies.
* No external binaries (`ss`, `pgrep`, `docker`) are required.

All functions are meant to be used from the Telegram bot event loop —
blocking I/O is minimal (read /proc files, a short unix-socket request).
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# === Cache ===
# Light TTL cache — so /start does not hit /proc and docker.sock on every tap.
_CACHE: Dict[str, Tuple[float, object]] = {}
_DEFAULT_TTL = float(os.getenv("TELEGRAMHELPER_LIVE_TTL", "3.0"))


def _cached(key: str, ttl: float, fn):
    """Simple in-process TTL cache."""
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry is not None:
        ts, value = entry
        if now - ts < ttl:
            return value
    value = fn()
    _CACHE[key] = (now, value)
    return value


# === Low-level checks ===

def _proc_path(*parts: str) -> str:
    """Path inside /proc, overridable via PROC_PATH (for tests)."""
    base = os.getenv("PROC_PATH", "/proc")
    return os.path.join(base, *parts)


# /proc/net/tcp uses field st = socket state.
# 0A (= 10 dec) — TCP_LISTEN. For UDP, anything in /proc/net/udp with a local
# address is treated as "listening" — UDP has no LISTEN state.
_TCP_LISTEN_STATE = "0A"


def _parse_listen_ports(net_file: str, is_tcp: bool) -> Set[int]:
    """
    Parse /proc/net/{tcp,tcp6,udp,udp6} and return the set of listening ports.

    Line format (man 5 proc):
        sl  local_address rem_address   st tx_queue:rx_queue ...
    local_address is `IP:PORT` in HEX; port is the last 4 hex characters.
    """
    ports: Set[int] = set()
    try:
        with open(net_file, "r", encoding="ascii", errors="ignore") as f:
            next(f, None)  # header
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                local_addr = parts[1]
                state = parts[3]
                if is_tcp and state != _TCP_LISTEN_STATE:
                    continue
                # local_addr like "0100007F:1F40" → port = 0x1F40 = 8000
                _, _, hex_port = local_addr.rpartition(":")
                if not hex_port:
                    continue
                try:
                    ports.add(int(hex_port, 16))
                except ValueError:
                    continue
    except FileNotFoundError:
        return ports
    except PermissionError as exc:
        logger.warning("live_status: cannot read %s: %s", net_file, exc)
        return ports
    except Exception as exc:
        logger.warning("live_status: error parsing %s: %s", net_file, exc)
    return ports


def _host_listen_ports() -> Dict[str, Set[int]]:
    """
    Collect listening ports in the host netns.

    First try the host-init netns via /proc/1/net/* (a pid=host container
    sees host PID 1). If those files are unavailable for any reason —
    fall back to /proc/net/* (the current container netns; useless on a VPS,
    but gives some answer during local development).
    """
    candidates: List[Tuple[str, str, str, str]] = [
        (
            _proc_path("1", "net", "tcp"),
            _proc_path("1", "net", "tcp6"),
            _proc_path("1", "net", "udp"),
            _proc_path("1", "net", "udp6"),
        ),
        (
            _proc_path("net", "tcp"),
            _proc_path("net", "tcp6"),
            _proc_path("net", "udp"),
            _proc_path("net", "udp6"),
        ),
    ]
    for tcp4, tcp6, udp4, udp6 in candidates:
        if not os.path.exists(tcp4):
            continue
        tcp_ports = _parse_listen_ports(tcp4, is_tcp=True) | _parse_listen_ports(tcp6, is_tcp=True)
        udp_ports = _parse_listen_ports(udp4, is_tcp=False) | _parse_listen_ports(udp6, is_tcp=False)
        return {"tcp": tcp_ports, "udp": udp_ports}
    return {"tcp": set(), "udp": set()}


def host_listen_ports(ttl: float = _DEFAULT_TTL) -> Dict[str, Set[int]]:
    """Cached wrapper around _host_listen_ports()."""
    return _cached("listen_ports", ttl, _host_listen_ports)


def is_port_listening(port: int, proto: str = "tcp", ttl: float = _DEFAULT_TTL) -> bool:
    """Is the given port listened on by any process on the host?"""
    if not isinstance(port, int) or port <= 0:
        return False
    proto = proto.lower()
    if proto not in {"tcp", "udp"}:
        return False
    return port in host_listen_ports(ttl).get(proto, set())


# === Process lookup ===

@dataclass(frozen=True)
class ProcInfo:
    pid: int
    cmdline: str
    comm: str


def _read_first_line(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.readline().strip()
    except (FileNotFoundError, PermissionError):
        return ""
    except Exception:
        return ""


def _read_cmdline(pid_str: str) -> str:
    """Read the full process cmdline (NUL → space)."""
    try:
        with open(_proc_path(pid_str, "cmdline"), "rb") as f:
            raw = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return ""
    if not raw:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _read_proc_exe(pid: int) -> str:
    """
    Return the process executable path via `/proc/<pid>/exe`.

    Useful to tell `/usr/local/bin/caddy-naive` from a regular
    `/usr/bin/caddy` when cmdline is ambiguous.
    Returns an empty string on any error.
    """
    try:
        return os.readlink(_proc_path(str(pid), "exe"))
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _file_exists_and_readable(path: str) -> bool:
    """Side-effect free: check that the file exists and is readable."""
    try:
        return os.path.isfile(path) and os.access(path, os.R_OK)
    except OSError:
        return False


def _file_contains(path: str, needle: str, max_bytes: int = 65536) -> bool:
    """
    Cheap check: whether the first `max_bytes` of the file contain a substring.

    Used to confirm the Caddyfile is NaiveProxy
    (contains `forward_proxy`), not a regular Caddy web server.
    """
    try:
        with open(path, "rb") as f:
            blob = f.read(max_bytes)
        return needle.encode("utf-8") in blob
    except (FileNotFoundError, PermissionError, OSError):
        return False


def _iter_processes() -> Iterable[ProcInfo]:
    """Iterate all processes in the host PID namespace (pid=host container)."""
    base = _proc_path()
    try:
        entries = os.listdir(base)
    except (FileNotFoundError, PermissionError):
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        cmdline = _read_cmdline(entry)
        if not cmdline:
            # kernel/zombie processes — skip
            continue
        comm = _read_first_line(_proc_path(entry, "comm"))
        try:
            yield ProcInfo(pid=int(entry), cmdline=cmdline, comm=comm)
        except ValueError:
            continue


def _scan_processes() -> List[ProcInfo]:
    return list(_iter_processes())


def all_processes(ttl: float = _DEFAULT_TTL) -> List[ProcInfo]:
    """Cached dump of all host processes."""
    return _cached("processes", ttl, _scan_processes)


def find_processes(*needles: str, ttl: float = _DEFAULT_TTL) -> List[ProcInfo]:
    """
    Find processes whose cmdline or comm contains any needle (case-insensitive).

    Substring search so binary name variants are covered
    (e.g. `xray` vs `xray-linux-amd64`, `hysteria` vs `hysteria-amd64`).
    """
    if not needles:
        return []
    lowered = [n.lower() for n in needles if n]
    matches: List[ProcInfo] = []
    for proc in all_processes(ttl):
        cmd = proc.cmdline.lower()
        com = proc.comm.lower()
        if any(n in cmd or n in com for n in lowered):
            matches.append(proc)
    return matches


def is_process_running(*needles: str, ttl: float = _DEFAULT_TTL) -> bool:
    return bool(find_processes(*needles, ttl=ttl))


# === Docker via unix-socket ===

_DOCKER_SOCKET_CANDIDATES = (
    "/var/run/docker.sock",
    "/run/docker.sock",
)


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection over AF_UNIX. No third-party dependencies."""

    def __init__(self, unix_path: str, timeout: float = 2.0):
        super().__init__("localhost", timeout=timeout)
        self._unix_path = unix_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._unix_path)
        self.sock = sock


def _docker_socket_path() -> Optional[str]:
    override = os.getenv("DOCKER_SOCKET")
    if override and os.path.exists(override):
        return override
    for path in _DOCKER_SOCKET_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _docker_request(path: str, timeout: float = 2.0) -> Optional[Dict]:
    """
    Issue a GET request to the Docker Engine API.

    Returns parsed JSON or None on any error (missing socket, no
    permissions, container with that name not found). Never raises
    to the caller — diagnostics must not take down /start.
    """
    sock_path = _docker_socket_path()
    if not sock_path:
        return None
    try:
        conn = _UnixHTTPConnection(sock_path, timeout=timeout)
        try:
            conn.request("GET", path, headers={"Host": "localhost"})
            resp = conn.getresponse()
            data = resp.read()
            if resp.status >= 400:
                return None
            if not data:
                return None
            return json.loads(data.decode("utf-8", errors="ignore"))
        finally:
            conn.close()
    except (FileNotFoundError, PermissionError, ConnectionError, socket.timeout, OSError) as exc:
        logger.debug("live_status: docker request %s failed: %s", path, exc)
        return None
    except json.JSONDecodeError as exc:
        logger.debug("live_status: docker response not JSON for %s: %s", path, exc)
        return None
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("live_status: unexpected docker error for %s: %s", path, exc)
        return None


def _container_status_uncached(name: str) -> Dict:
    """
    Ask Docker for container state by name.

    Returns a dict with keys:
        available  — bool, whether the Docker API was reachable at all
        found      — bool, whether a container with that name exists
        running    — bool, whether it is running now
        state      — short string state (running/exited/...)
        health     — healthcheck status (healthy/starting/unhealthy/none)
    """
    info = {
        "available": _docker_socket_path() is not None,
        "found": False,
        "running": False,
        "state": "",
        "health": "",
    }
    if not info["available"]:
        return info

    payload = _docker_request(f"/containers/{name}/json")
    if not payload:
        return info

    info["found"] = True
    state = (payload.get("State") or {}) if isinstance(payload, dict) else {}
    status = str(state.get("Status", "")).lower()
    info["state"] = status or ""
    info["running"] = bool(state.get("Running"))
    health = (state.get("Health") or {}) if isinstance(state, dict) else {}
    info["health"] = str(health.get("Status", "") or "").lower()
    return info


def container_status(name: str, ttl: float = _DEFAULT_TTL) -> Dict:
    """Cached container_status."""
    return _cached(f"container:{name}", ttl, lambda: _container_status_uncached(name))


# === Protocol snapshot ===

@dataclass
class ProtocolStatus:
    """
    Unified representation of a transport protocol's status.

    `flag_enabled`  — what is stored in the manager JSON config (`enabled`).
    `process_alive` — whether a live host process for this protocol exists.
    `port_listening`— whether the server is listening on the expected port.
    `live`          — final aggregated assessment.
    `implemented`   — whether the bot has server-side automation for the protocol.
    """

    key: str
    display: str
    icon: str = ""
    flag_enabled: Optional[bool] = None
    configured: Optional[bool] = None
    server: str = ""
    port: Optional[int] = None
    transport: str = "tcp"  # tcp | udp
    process_alive: Optional[bool] = None
    process_names: List[str] = field(default_factory=list)
    port_listening: Optional[bool] = None
    container_status: Optional[Dict] = None
    implemented: bool = True
    notes: List[str] = field(default_factory=list)

    @property
    def live(self) -> Optional[bool]:
        """
        Whether the protocol is actually running right now.

        The logic avoids false positives when several transports compete
        for one port (VLESS-Reality via xray and NaiveProxy via caddy-naive
        both look at :443/tcp). Therefore:

        * a listening port alone does NOT prove this protocol is the one
          running — it may belong to a neighbor;
        * the only reliable signal is the presence of THIS protocol's
          host process (xray for VLESS, caddy-naive for NaiveProxy,
          hysteria for Hy2, mtproto-proxy/mtg for MTProto).

        Decision rule:
            process_alive=True  → 🟢 running (port no longer matters)
            process_alive=False → 🔴 off (even if something occupies the port)
            process_alive=None  → we cannot look up this protocol's process.
                                  Fall back to the JSON flag and do not trust the port.
        For protocols not implemented in the bot (TUIC/AnyTLS/XHTTP)
        the result is hardcoded to False, see below.
        """
        if not self.implemented:
            return False
        if self.process_alive is True:
            return True
        if self.process_alive is False:
            return False
        # process_alive is None — diagnostics unavailable (no /proc, no permissions).
        # Port is deliberately unused — too unreliable under competition.
        if self.flag_enabled is False:
            return False
        return None

    def short_indicator(self) -> str:
        live = self.live
        if live is True:
            return "🟢"
        if live is False:
            return "🔴"
        return "⚪"  # unknown (no data)

    def short_label(self) -> str:
        if not self.implemented:
            return "not implemented in the bot"
        live = self.live
        if live is True:
            return "running"
        if live is False:
            return "off"
        return "state unknown"


def _safe(fn, default):
    """Call fn() and swallow any error — diagnostics must not crash."""
    try:
        return fn()
    except Exception as exc:
        logger.debug("live_status: helper failed: %s", exc)
        return default


def _vless_status() -> ProtocolStatus:
    import vless_manager

    raw = _safe(vless_manager.get_vless_status, {})
    procs = find_processes("xray")
    xui_procs = find_processes("x-ui")
    port = int(raw.get("port", 443) or 443)
    listening = is_port_listening(port, "tcp")

    # The xray server config is mounted into the container as /usr/local/etc/xray
    # (see compose.yaml). That lets diagnostics tell the operator whether
    # xray is installed on the host even if no process is running now.
    notes: List[str] = []
    xray_config = os.getenv("XRAY_SERVER_CONFIG", "/usr/local/etc/xray/config.json")
    display = "VLESS-Reality (bot → host Xray)"
    flag_enabled = bool(raw.get("enabled", False))
    configured = bool(raw.get("configured", False))

    def _legacy_client_count() -> int:
        clients = _safe(vless_manager.list_clients, [])
        return len([c for c in clients if isinstance(c, dict) and c.get("uuid")])

    def _xui_client_note() -> Optional[str]:
        nonlocal display, flag_enabled, configured, port
        try:
            import xui_manager

            if not xui_manager.is_enabled():
                return None
            cfg = xui_manager.load_config()
            inbound_id = int(cfg.get("default_inbound_id") or 0)
            if not inbound_id:
                return "3x-ui is connected to the bot, but default_inbound_id is not set — run /xui_setup"
            xclient = xui_manager.make_client_for_config(cfg)
            if xclient is None:
                return "3x-ui is connected to the bot, but the panel client did not restore — check /xui_status"
            ok_login, login_msg = xclient.login()
            if not ok_login:
                return f"3x-ui is connected to the bot, but login failed: {login_msg}"
            ok_inbound, msg, inbound = xclient.get_inbound(inbound_id)
            if not ok_inbound or not inbound:
                return f"3x-ui inbound #{inbound_id} is unavailable: {msg}"
            settings_raw = inbound.get("settings") or "{}"
            settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
            clients = settings.get("clients") or []
            if not isinstance(clients, list):
                clients = []
            bot_count = len([
                c for c in clients
                if isinstance(c, dict)
                and str(c.get("email", "")).startswith("Vless_ID")
            ])
            manual_count = max(0, len(clients) - bot_count)
            remark = str(inbound.get("remark") or "VLESS")
            inbound_port = int(inbound.get("port") or port)
            display = "VLESS-Reality (one 3x-ui inbound)"
            flag_enabled = True
            configured = True
            port = inbound_port
            return (
                f"one VLESS endpoint: inbound #{inbound_id} «{remark}» on {inbound_port}/TCP; "
                f"clients total: {len(clients)}, bot-managed: {bot_count}, manual: {manual_count}; "
                "details: /vless_list_clients"
            )
        except Exception as exc:
            logger.debug("live_status: xui VLESS client count failed: %s", exc)
            return None

    xui_note = _xui_client_note()
    if xui_note:
        notes.append(xui_note)
    else:
        # Explicitly separate from "web panels": this is a legacy inbound on host Xray,
        # managed by the bot via vless_config.json.
        count = _legacy_client_count()
        notes.append(
            f"one legacy VLESS endpoint on {port}/TCP; "
            f"clients in vless_config.json: {count}; "
            f"Xray config: {xray_config}; details: /vless_list_clients"
        )
        if xui_procs:
            notes.append(
                "a 3x-ui panel was also found on the host; that is a management panel, "
                "not a second VLESS endpoint in /diag. To use the panel, configure /xui_setup"
            )
    if not procs and not _file_exists_and_readable(xray_config):
        notes.append(f"server config {xray_config} not found — is xray installed on the VPS?")

    return ProtocolStatus(
        key="vless_reality",
        display=display,
        icon="🛡",
        flag_enabled=flag_enabled,
        configured=configured,
        server=str(raw.get("server", "") or ""),
        port=port,
        transport="tcp",
        process_alive=bool(procs),
        process_names=[p.comm or p.cmdline.split()[0] for p in procs],
        port_listening=listening,
        notes=notes,
    )


def _hy2_status() -> ProtocolStatus:
    import hysteria2_manager

    raw = _safe(hysteria2_manager.get_status, {})
    procs = find_processes("hysteria")
    port = int(raw.get("port", 443) or 443)
    listening = is_port_listening(port, "udp")

    # /etc/hysteria is mounted into the container; check the server config.
    notes: List[str] = []
    hy2_server_config = os.getenv("HYSTERIA_SERVER_CONFIG", "/etc/hysteria/config.yaml")
    if not procs:
        if _file_exists_and_readable(hy2_server_config):
            notes.append(f"config {hy2_server_config} exists, but the hysteria process is not running")
        else:
            notes.append(f"server config {hy2_server_config} not found — is hysteria installed on the VPS?")

    return ProtocolStatus(
        key="hysteria2",
        display="Hysteria2",
        icon="⚡",
        flag_enabled=bool(raw.get("enabled", False)),
        configured=bool(raw.get("configured", False)),
        server=str(raw.get("server", "") or ""),
        port=port,
        transport="udp",
        process_alive=bool(procs),
        process_names=[p.comm or p.cmdline.split()[0] for p in procs],
        port_listening=listening,
        notes=notes,
    )


def _naive_status() -> ProtocolStatus:
    """
    NaiveProxy is implemented as Caddy + forwardproxy@naive.
    Binary is a separate
    `/usr/local/bin/caddy-naive`, unit is `caddy-naive.service`,
    config is `/etc/caddy-naive/Caddyfile`. `/etc/caddy-naive`
    is mounted into the bot container via `compose.yaml`, so we
    can read the Caddyfile from the bot and confirm it is
    NaiveProxy, not a stock Caddy web server.
    """
    import naiveproxy_manager

    raw = _safe(naiveproxy_manager.get_status, {})
    service_name = (raw.get("service_name") or "caddy-naive").strip()
    # Deliberately do NOT search for generic "caddy": a stock Caddy web server
    # ≠ a NaiveProxy server. The NaiveProxy binary always has a specific name.
    needles = [service_name, "caddy-naive", "xcaddy-naive"]
    seen = set()
    needles = [n for n in needles if n and not (n in seen or seen.add(n))]
    procs = find_processes(*needles)

    port = int(raw.get("port", 443) or 443)
    listening = is_port_listening(port, "tcp")
    server = str(raw.get("domain") or raw.get("server") or "")

    caddyfile_path = os.getenv("NAIVE_CADDYFILE", "/etc/caddy-naive/Caddyfile")
    caddyfile_exists = _file_exists_and_readable(caddyfile_path)
    has_forward_proxy = (
        caddyfile_exists and _file_contains(caddyfile_path, "forward_proxy")
    )
    naive_binary = os.getenv("NAIVE_BINARY", "/usr/local/bin/caddy-naive")
    binary_exists = _file_exists_and_readable(naive_binary)

    notes: List[str] = []
    process_names = [p.comm or p.cmdline.split()[0] for p in procs]

    if procs:
        # Confirm the found process is actually caddy-naive,
        # not a generic caddy that happened to match the substring.
        for p in procs:
            exe = _read_proc_exe(p.pid)
            if exe and "caddy-naive" not in os.path.basename(exe).lower():
                notes.append(
                    f"⚠️ found process PID {p.pid} ({exe or p.comm}), "
                    f"but it does not look like caddy-naive — check manually"
                )
        if raw.get("systemd_active") is True:
            notes.append(f"systemd: {service_name} active")
    else:
        # No process: give the operator a clear reason.
        if not binary_exists and not caddyfile_exists:
            notes.append(
                f"NaiveProxy server is not installed on this VPS "
                f"(neither {naive_binary} nor {caddyfile_path}); "
                f"install via /naive_* or scripts/install_naiveproxy.sh"
            )
        elif not caddyfile_exists:
            notes.append(
                f"binary {naive_binary} exists, but config {caddyfile_path} "
                f"is missing — Caddyfile was not generated"
            )
        elif not has_forward_proxy:
            notes.append(
                f"{caddyfile_path} exists but has no "
                f"forward_proxy directive — looks like regular Caddy, not NaiveProxy"
            )
        elif not binary_exists:
            notes.append(
                f"Caddyfile with forward_proxy exists, but binary {naive_binary} "
                f"was not found — build caddy-naive (xcaddy)"
            )
        else:
            notes.append(
                f"everything is in place, but caddy-naive is not running; try: "
                f"systemctl status {service_name}"
            )

    return ProtocolStatus(
        key="naiveproxy",
        display="NaiveProxy",
        icon="🌐",
        flag_enabled=bool(raw.get("enabled", False)),
        configured=bool(raw.get("configured", False)),
        server=server,
        port=port,
        transport="tcp",
        process_alive=bool(procs),
        process_names=process_names,
        port_listening=listening,
        notes=notes,
    )


def _tuic_status() -> ProtocolStatus:
    """TUIC: server-side automation is not implemented in the bot (see /help roadmap).

    Deliberately do NOT bind "sing-box on the host" to TUIC: the same binary
    may serve AnyTLS, ad-hoc VLESS, etc. Without reading the sing-box
    config we cannot tell which protocol it is serving.
    """
    import tuic_manager

    raw = _safe(tuic_manager.get_status, {})
    port = int(raw.get("port", 443) or 443)
    return ProtocolStatus(
        key="tuic",
        display="TUIC",
        icon="🚀",
        flag_enabled=bool(raw.get("enabled", False)),
        configured=bool(raw.get("configured", False)),
        server=str(raw.get("server", "") or ""),
        port=port,
        transport="udp",
        process_alive=None,
        port_listening=None,
        implemented=False,
        notes=["server-side automation is not implemented in the bot yet"],
    )


def _anytls_status() -> ProtocolStatus:
    """AnyTLS: see the comment in `_tuic_status` — we do not bind sing-box."""
    import anytls_manager

    raw = _safe(anytls_manager.get_status, {})
    port = int(raw.get("port", 443) or 443)
    return ProtocolStatus(
        key="anytls",
        display="AnyTLS",
        icon="🔐",
        flag_enabled=bool(raw.get("enabled", False)),
        configured=bool(raw.get("configured", False)),
        server=str(raw.get("server", "") or ""),
        port=port,
        transport="tcp",
        process_alive=None,
        port_listening=None,
        implemented=False,
        notes=["server-side automation is not implemented in the bot yet"],
    )


def _xhttp_status() -> ProtocolStatus:
    """XHTTP: the server side is shared with the VLESS-Reality xray process,
    so there is no separate process and the port matches VLESS — you cannot
    infer XHTTP from a listening `:443/tcp` (that would be the same xray)."""
    import xhttp_manager

    raw = _safe(xhttp_manager.get_status, {})
    port = int(raw.get("port", 443) or 443)
    return ProtocolStatus(
        key="xhttp",
        display="XHTTP",
        icon="🛰",
        flag_enabled=bool(raw.get("enabled", False)),
        configured=bool(raw.get("configured", False)),
        server=str(raw.get("server", "") or ""),
        port=port,
        transport="tcp",
        process_alive=None,
        port_listening=None,
        implemented=False,
        notes=["server-side automation is not implemented in the bot yet"],
    )


def _mieru_status() -> ProtocolStatus:
    """
    Mieru (mita): a separate TCP/UDP transport without TLS camouflage. Binary
    is `mita`, unit is `mita.service`.

    Unlike VLESS/XHTTP, Mieru listens on ITS OWN port (default 29999/tcp),
    so both the port and the process are actually checked on the host.
    Mieru needs synchronized system time (the key depends on it) —
    an NTP hint is added as a note so the operator does not forget.
    """
    import mieru_manager

    raw = _safe(mieru_manager.get_status, {})
    bindings = raw.get("port_bindings") or []
    port = 0
    transport = "tcp"
    if bindings:
        first = bindings[0]
        transport = (first.get("protocol") or "TCP").lower()
        if "port" in first:
            port = int(first.get("port") or 0)
        elif "portRange" in first:
            port = int((first.get("portRange") or {}).get("from") or 0)

    procs = find_processes("mita")
    listening = is_port_listening(port, transport) if port else None

    notes: List[str] = []
    if port in (443,):
        notes.append(
            f"port {port}/{transport} conflicts with VLESS/Hysteria2/NaiveProxy — "
            "pick a dedicated port via /mieru_set_port"
        )
    if bool(raw.get("configured")) and not procs:
        notes.append("config exists, but mita is not running — try /mieru_start")
    if procs:
        notes.append("Mieru needs synchronized time on the VPS — check `timedatectl status`")

    return ProtocolStatus(
        key="mieru",
        display="Mieru (mita)",
        icon="🛰",
        flag_enabled=bool(raw.get("enabled", False)),
        configured=bool(raw.get("configured", False)),
        server=str(raw.get("server", "") or ""),
        port=port,
        transport=transport,
        process_alive=bool(procs),
        process_names=[p.comm or p.cmdline.split()[0] for p in procs],
        port_listening=listening,
        notes=notes,
    )


def _mtproto_status() -> ProtocolStatus:
    import mtproto_manager

    raw = _safe(mtproto_manager.get_status, {})
    # Telegram MTProto proxy: support binary name variants
    # mtproto-proxy / mtg / py-tg-proxy. Default listen port is 993 (TCP).
    procs = find_processes("mtproto-proxy", "mtg", "py-tg-proxy", "tg-proxy")
    port = int(raw.get("port", 993) or 993)
    listening = is_port_listening(port, "tcp")
    return ProtocolStatus(
        key="mtproto",
        display="MTProto (Telegram)",
        icon="📡",
        flag_enabled=bool(raw.get("enabled", False)),
        configured=bool(raw.get("configured", False)),
        server=str(raw.get("server", "") or ""),
        port=port,
        transport="tcp",
        process_alive=bool(procs),
        process_names=[p.comm or p.cmdline.split()[0] for p in procs],
        port_listening=listening,
    )


def _xui_panel_status() -> ProtocolStatus:
    """
    3x-ui (Sanaei) is an external Xray web panel. On the VPS it may run
    natively (`x-ui.service` + binary `/usr/local/x-ui/x-ui`) or
    in a Docker container (typical names: `3x-ui`, `x-ui`, `3xui`).

    IMPORTANT: 3x-ui manages ITS OWN Xray (`/usr/local/x-ui/bin/xray-*`) and
    its own client DB in `/etc/x-ui/x-ui.db`. The bot writes to
    its Xray (`/usr/local/etc/xray`, `xray.service`) — two
    independent stacks. Here we only tell the operator that such a
    panel is present on the VPS, so they do not confuse "their"
    Xray with the "panel" one and understand that clients created by
    the bot will not appear in the panel (and vice versa).

    Without panel credentials we do NOT call its API — only
    free signals: host process, binary, systemd unit, Docker.
    """
    notes: List[str] = []

    # 1) Native process / binary / systemd unit
    binary_path = "/usr/local/x-ui/x-ui"
    systemd_unit = "/etc/systemd/system/x-ui.service"
    db_path = "/etc/x-ui/x-ui.db"
    binary_exists = _file_exists_and_readable(binary_path)
    unit_exists = _file_exists_and_readable(systemd_unit)
    db_exists = _file_exists_and_readable(db_path)

    procs = find_processes("x-ui")
    # Drop noisy matches (e.g. `xui_panel_status`) —
    # accept only processes whose cmdline / comm / exe actually
    # point at 3x-ui.
    native_procs = []
    for p in procs:
        cmd = p.cmdline.lower()
        comm = (p.comm or "").lower()
        if comm == "x-ui" or "/x-ui/x-ui" in cmd or cmd.startswith("x-ui"):
            native_procs.append(p)
            continue
        try:
            exe = _read_proc_exe(p.pid).lower()
        except Exception:
            exe = ""
        if exe.endswith("/x-ui") or "/x-ui/" in exe:
            native_procs.append(p)

    # 2) Docker container. Try typical names; the first
    # "running" wins; if none is running but something was
    # found — record as found-but-stopped.
    container_candidates = ("3x-ui", "x-ui", "3xui")
    container_info: Dict = {"available": False}
    container_name_used = ""
    found_any = False
    running_any = False
    for cname in container_candidates:
        info = container_status(cname)
        if not info.get("available"):
            container_info = info
            continue
        if info.get("found"):
            found_any = True
            container_name_used = cname
            container_info = info
            if info.get("running"):
                running_any = True
                break

    process_alive = bool(native_procs) or running_any

    # First — a clear "deployment type" so it is not confused with the bot VLESS transport and Dockhand.
    if native_procs and running_any:
        notes.append(
            "3x-ui deployment: host process and Docker at the same time — "
            f"unusual; check the instance (Docker: container `{container_name_used}`)"
        )
    elif native_procs:
        notes.append(
            "3x-ui deployment: native on the host (x-ui process), not a panel Docker container"
        )
    elif running_any:
        notes.append(
            f"3x-ui deployment: Docker (container `{container_name_used}`, running)"
        )
    elif found_any:
        notes.append(
            f"3x-ui deployment: Docker (container `{container_name_used}`, not running)"
        )
    elif binary_exists or unit_exists:
        notes.append(
            "3x-ui deployment: signs of a native install (binary/unit), process not running"
        )

    if native_procs:
        notes.append("panel process is active on the host")
    elif binary_exists and not native_procs:
        notes.append(f"binary {binary_path} found, but the process is not running")
    if unit_exists and not native_procs and not running_any:
        notes.append(f"systemd unit `{systemd_unit}` is present, unit is not active")
    if not (native_procs or binary_exists or unit_exists or found_any):
        # Nothing found — mark as "absent" without noise in /diag.
        notes.append("3x-ui was not found on this VPS")
    if db_exists and (native_procs or running_any):
        notes.append(
            "panel clients are stored in /etc/x-ui/x-ui.db; 3x-ui is "
            "a management panel for the same Xray/VLESS, not a second VPN"
        )

    proc_names = [p.comm or (p.cmdline.split() or ["x-ui"])[0] for p in native_procs]
    if running_any and not proc_names:
        proc_names = [f"docker:{container_name_used}"]

    return ProtocolStatus(
        key="xui_panel",
        display="3x-ui (third-party web panel)",
        icon="🛠",
        flag_enabled=None,
        configured=binary_exists or found_any or unit_exists,
        server="",
        port=None,  # panel port is configurable; we will not read it from x-ui.db without extra deps
        transport="tcp",
        process_alive=process_alive if (native_procs or found_any) else None,
        process_names=proc_names,
        port_listening=None,
        container_status=container_info if container_info.get("available") else None,
        # 3x-ui is external to the bot. So
        # `implemented=True` only means we can detect it,
        # not that the bot manages it.
        implemented=True,
        notes=notes,
    )


def _dockhand_status() -> ProtocolStatus:
    """
    Dockhand is a Streamlit panel in a separate Docker container.

    Identified by container name (`dockhand` by default,
    overridable via DOCKHAND_CONTAINER_NAME).
    """
    container_name = os.getenv("DOCKHAND_CONTAINER_NAME", "dockhand")
    info = container_status(container_name)
    notes: List[str] = []
    available = info.get("available", False)
    found = info.get("found", False)
    running = info.get("running", False)
    health = info.get("health") or ""

    if not available:
        notes.append("docker.sock is unavailable — container status was not checked")
    elif not found:
        notes.append(f"container `{container_name}` was not found on this host")
    elif not running:
        notes.append(f"container found, state: {info.get('state') or 'unknown'}")
    elif health and health not in {"healthy", ""}:
        notes.append(f"healthcheck: {health}")

    notes.insert(
        0,
        "Dockhand — Streamlit in Docker (bot diagnostics); not an Xray transport and not 3x-ui",
    )

    # Dockhand port 8501 is bound only to 127.0.0.1 (see compose.yaml),
    # so it is not reachable from outside the VPS — use a Docker query only.
    return ProtocolStatus(
        key="dockhand",
        display="Dockhand panel (Docker)",
        icon="🛠",
        flag_enabled=None,
        configured=None,
        server="",
        port=8501,
        transport="tcp",
        process_alive=running if available and found else None,
        process_names=[],
        port_listening=is_port_listening(8501, "tcp"),
        container_status=info,
        notes=notes,
    )


_PROTOCOL_BUILDERS = (
    _vless_status,
    _hy2_status,
    _naive_status,
    _mtproto_status,
    _tuic_status,
    _anytls_status,
    _xhttp_status,
    _mieru_status,
)


def gather_protocols() -> List[ProtocolStatus]:
    """Return statuses of all protocols known to the bot."""
    out: List[ProtocolStatus] = []
    for builder in _PROTOCOL_BUILDERS:
        try:
            out.append(builder())
        except Exception as exc:
            logger.warning("live_status: builder %s failed: %s", builder.__name__, exc)
    return out


def gather_full_snapshot() -> Dict:
    """Full state snapshot: protocols + panels + port summary."""
    protocols = gather_protocols()
    snapshot = {
        "protocols": protocols,
        "dockhand": _safe(_dockhand_status, ProtocolStatus(
            key="dockhand", display="Dockhand panel (Docker)", icon="🛠",
            notes=["dockhand: diagnostics error"],
        )),
        # External Xray management panel (if installed). The bot does not
        # manage it — only detects it and warns the operator.
        "xui_panel": _safe(_xui_panel_status, ProtocolStatus(
            key="xui_panel", display="3x-ui (third-party web panel)", icon="🛠",
            notes=["xui_panel: diagnostics error"],
        )),
        "host_ports": host_listen_ports(),
    }
    return snapshot


def reset_cache() -> None:
    """Clear the cache — for tests and a forced refresh."""
    _CACHE.clear()
