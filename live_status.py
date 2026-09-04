# -*- coding: utf-8 -*-
"""
Лайв-проверки статуса транспорт-протоколов и Docker-контейнеров.

Этот модуль собирает реальный статус сервисов (xray, hysteria-server,
caddy-naive, mtproto-proxy, sing-box, dockhand-контейнер) на хосте
TelegramHelper — независимо от флагов `enabled` в JSON-конфигах транспорт-
менеджеров. Используется в `/start` и админских командах диагностики,
чтобы было видно реальное состояние, а не только запись в JSON.

Архитектура:

* Бот живёт в Docker-контейнере `telegram-helper-lite`. В `compose.yaml`
  у него `pid: host` и `privileged: true`, поэтому из контейнера видны
  все хост-процессы по `/proc`.
* Сетевой namespace у контейнера свой (нет `network_mode: host`), но мы
  читаем `/proc/1/net/{tcp,tcp6,udp,udp6}` — это netns хост-init'а,
  то есть реальный сетевой стек VPS. С `privileged: true` это работает.
* Контейнер `dockhand` опрашиваем через смонтированный `/var/run/docker.sock`
  без сторонних зависимостей.
* Никаких внешних бинарников (`ss`, `pgrep`, `docker`) не требуется.

Все функции рассчитаны на использование из event-loop'а телеграм-бота —
блокирующий I/O минимален (read /proc файлов, короткий unix-socket запрос).
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


# === Кеш ===
# Лёгкий TTL-кеш — чтобы /start не дёргал /proc и docker.sock на каждый клик.
_CACHE: Dict[str, Tuple[float, object]] = {}
_DEFAULT_TTL = float(os.getenv("TELEGRAMHELPER_LIVE_TTL", "3.0"))


def _cached(key: str, ttl: float, fn):
    """Простой in-process TTL-кеш."""
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry is not None:
        ts, value = entry
        if now - ts < ttl:
            return value
    value = fn()
    _CACHE[key] = (now, value)
    return value


# === Низкоуровневые проверки ===

def _proc_path(*parts: str) -> str:
    """Путь внутри /proc, переопределяемый через PROC_PATH (для тестов)."""
    base = os.getenv("PROC_PATH", "/proc")
    return os.path.join(base, *parts)


# /proc/net/tcp использует поле st = состояние сокета.
# 0A (= 10 dec) — TCP_LISTEN. Для UDP всё, что в /proc/net/udp с локальным
# адресом, считаем «слушает» — у UDP нет состояния LISTEN.
_TCP_LISTEN_STATE = "0A"


def _parse_listen_ports(net_file: str, is_tcp: bool) -> Set[int]:
    """
    Распарсить /proc/net/{tcp,tcp6,udp,udp6} и вернуть множество слушающих портов.

    Формат строки (man 5 proc):
        sl  local_address rem_address   st tx_queue:rx_queue ...
    local_address — это `IP:PORT` в HEX, port — последние 4 hex-символа.
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
                # local_addr вида "0100007F:1F40" → port = 0x1F40 = 8000
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
    Собрать слушающие порты в host-netns.

    Сначала пробуем netns хост-init'а через /proc/1/net/* (контейнер
    с pid=host видит хост-PID 1). Если по каким-то причинам файлы
    недоступны — fallback на /proc/net/* (это netns текущего контейнера,
    бесполезен для VPS, но даёт хоть какой-то ответ при локальной разработке).
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
    """Кешированная обёртка над _host_listen_ports()."""
    return _cached("listen_ports", ttl, _host_listen_ports)


def is_port_listening(port: int, proto: str = "tcp", ttl: float = _DEFAULT_TTL) -> bool:
    """Слушает ли указанный порт хоть какой-то процесс на хосте?"""
    if not isinstance(port, int) or port <= 0:
        return False
    proto = proto.lower()
    if proto not in {"tcp", "udp"}:
        return False
    return port in host_listen_ports(ttl).get(proto, set())


# === Поиск процессов ===

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
    """Прочитать полный cmdline процесса (NUL → пробел)."""
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
    Вернуть путь к исполняемому файлу процесса через `/proc/<pid>/exe`.

    Полезно, чтобы отличить, например, `/usr/local/bin/caddy-naive` от
    обычного `/usr/bin/caddy`, если cmdline это не однозначно определяет.
    Возвращает пустую строку при любой ошибке.
    """
    try:
        return os.readlink(_proc_path(str(pid), "exe"))
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _file_exists_and_readable(path: str) -> bool:
    """Без побочных эффектов: проверить, что файл существует и доступен на чтение."""
    try:
        return os.path.isfile(path) and os.access(path, os.R_OK)
    except OSError:
        return False


def _file_contains(path: str, needle: str, max_bytes: int = 65536) -> bool:
    """
    Дешёвая проверка: содержит ли первые `max_bytes` файла подстроку.

    Используется для подтверждения, что Caddyfile — именно NaiveProxy
    (содержит `forward_proxy`), а не обычный Caddy-вебсервер.
    """
    try:
        with open(path, "rb") as f:
            blob = f.read(max_bytes)
        return needle.encode("utf-8") in blob
    except (FileNotFoundError, PermissionError, OSError):
        return False


def _iter_processes() -> Iterable[ProcInfo]:
    """Перебрать все процессы в host-PID-namespace (контейнер с pid=host)."""
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
            # ядерные/zombie-процессы — пропускаем
            continue
        comm = _read_first_line(_proc_path(entry, "comm"))
        try:
            yield ProcInfo(pid=int(entry), cmdline=cmdline, comm=comm)
        except ValueError:
            continue


def _scan_processes() -> List[ProcInfo]:
    return list(_iter_processes())


def all_processes(ttl: float = _DEFAULT_TTL) -> List[ProcInfo]:
    """Кешированная выгрузка всех процессов хоста."""
    return _cached("processes", ttl, _scan_processes)


def find_processes(*needles: str, ttl: float = _DEFAULT_TTL) -> List[ProcInfo]:
    """
    Найти процессы, чей cmdline или comm содержат любой из needle (case-insensitive).

    Используем подстрочный поиск, чтобы покрыть варианты бинарника
    (например, `xray` vs `xray-linux-amd64`, `hysteria` vs `hysteria-amd64`).
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


# === Docker через unix-socket ===

_DOCKER_SOCKET_CANDIDATES = (
    "/var/run/docker.sock",
    "/run/docker.sock",
)


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection поверх AF_UNIX. Без сторонних зависимостей."""

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
    Сделать GET-запрос к Docker Engine API.

    Возвращает распарсенный JSON или None при любой ошибке (отсутствует
    сокет, нет прав, контейнер с таким именем не найден). Никогда не
    бросает исключения наверх — диагностика не должна валить /start.
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
    Спросить у Docker состояние контейнера по имени.

    Возвращает словарь с ключами:
        available  — bool, удалось ли вообще достучаться до Docker API
        found      — bool, существует ли контейнер с таким именем
        running    — bool, запущен ли он сейчас
        state      — короткое строковое состояние (running/exited/...)
        health     — статус healthcheck (healthy/starting/unhealthy/none)
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
    """Кешированный container_status."""
    return _cached(f"container:{name}", ttl, lambda: _container_status_uncached(name))


# === Snapshot протоколов ===

@dataclass
class ProtocolStatus:
    """
    Унифицированное представление статуса транспорт-протокола.

    `flag_enabled`  — что записано в JSON-конфиге менеджера (`enabled`).
    `process_alive` — есть ли реально живой хост-процесс под этот протокол.
    `port_listening`— слушает ли сервер ожидаемый порт.
    `live`          — финальная агрегированная оценка.
    `implemented`   — есть ли в боте серверная автоматизация для протокола.
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
        Реально работает ли протокол прямо сейчас.

        Логика устроена так, чтобы избегать ложно-положительных, когда
        несколько транспортов конкурируют за один порт (VLESS-Reality
        через xray и NaiveProxy через caddy-naive оба смотрят на
        :443/tcp). Поэтому:

        * слушающий порт сам по себе НЕ доказывает, что работает именно
          этот протокол — он может принадлежать соседу;
        * единственный надёжный сигнал — наличие СВОЕГО хост-процесса
          (xray для VLESS, caddy-naive для NaiveProxy, hysteria для Hy2,
          mtproto-proxy/mtg для MTProto).

        Решающее правило:
            process_alive=True  → 🟢 работает (порт уже не важен)
            process_alive=False → 🔴 выключен (даже если порт чем-то занят)
            process_alive=None  → мы не умеем искать процесс этого протокола.
                                  В этом случае откатываемся на флаг JSON
                                  и не доверяем порту.
        Для не-реализованных в боте протоколов (TUIC/AnyTLS/XHTTP)
        отдельно зашит результат False, см. ниже.
        """
        if not self.implemented:
            return False
        if self.process_alive is True:
            return True
        if self.process_alive is False:
            return False
        # process_alive is None — диагностика недоступна (нет /proc, нет прав).
        # Порт сознательно НЕ используем — слишком ненадёжно при конкуренции.
        if self.flag_enabled is False:
            return False
        return None

    def short_indicator(self) -> str:
        live = self.live
        if live is True:
            return "🟢"
        if live is False:
            return "🔴"
        return "⚪"  # неопределённо (нет данных)

    def short_label(self) -> str:
        if not self.implemented:
            return "не реализовано в боте"
        live = self.live
        if live is True:
            return "работает"
        if live is False:
            return "выключен"
        return "состояние неизвестно"


def _safe(fn, default):
    """Вызвать fn() и проглотить любую ошибку — диагностика не должна падать."""
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

    # Серверный конфиг xray смонтирован в контейнер как /usr/local/etc/xray
    # (см. compose.yaml). Это позволяет диагностике подсказать оператору,
    # установлен ли xray на хосте, даже если процесса сейчас нет.
    notes: List[str] = []
    xray_config = os.getenv("XRAY_SERVER_CONFIG", "/usr/local/etc/xray/config.json")
    display = "VLESS-Reality (бот → host Xray)"
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
                return "3x-ui подключена к боту, но default_inbound_id не задан — выполните /xui_setup"
            xclient = xui_manager.make_client_for_config(cfg)
            if xclient is None:
                return "3x-ui подключена к боту, но клиент панели не восстановился — проверьте /xui_status"
            ok_login, login_msg = xclient.login()
            if not ok_login:
                return f"3x-ui подключена к боту, но login не прошёл: {login_msg}"
            ok_inbound, msg, inbound = xclient.get_inbound(inbound_id)
            if not ok_inbound or not inbound:
                return f"3x-ui inbound #{inbound_id} недоступен: {msg}"
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
            display = "VLESS-Reality (один 3x-ui inbound)"
            flag_enabled = True
            configured = True
            port = inbound_port
            return (
                f"один VLESS endpoint: inbound #{inbound_id} «{remark}» на {inbound_port}/TCP; "
                f"клиентов всего: {len(clients)}, bot-managed: {bot_count}, ручных: {manual_count}; "
                "детально: /vless_list_clients"
            )
        except Exception as exc:
            logger.debug("live_status: xui VLESS client count failed: %s", exc)
            return None

    xui_note = _xui_client_note()
    if xui_note:
        notes.append(xui_note)
    else:
        # Явно отделяем от «веб-панелей»: это legacy inbound на host-Xray,
        # которым управляет бот через vless_config.json.
        count = _legacy_client_count()
        notes.append(
            f"один legacy VLESS endpoint на {port}/TCP; "
            f"клиентов в vless_config.json: {count}; "
            f"конфиг Xray: {xray_config}; детально: /vless_list_clients"
        )
        if xui_procs:
            notes.append(
                "на хосте также найдена 3x-ui панель; это панель управления, "
                "а не второй VLESS endpoint в /diag. Для работы через панель настройте /xui_setup"
            )
    if not procs and not _file_exists_and_readable(xray_config):
        notes.append(f"серверный конфиг {xray_config} не найден — xray на VPS не установлен?")

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

    # Каталог /etc/hysteria смонтирован в контейнер; проверим серверный конфиг.
    notes: List[str] = []
    hy2_server_config = os.getenv("HYSTERIA_SERVER_CONFIG", "/etc/hysteria/config.yaml")
    if not procs:
        if _file_exists_and_readable(hy2_server_config):
            notes.append(f"конфиг {hy2_server_config} есть, но процесс hysteria не запущен")
        else:
            notes.append(f"серверный конфиг {hy2_server_config} не найден — hysteria на VPS не установлен?")

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
    NaiveProxy реализован как Caddy + forwardproxy@naive (см.
    `Clash Meta/NAIVEPROXY_GUIDE.md`). Бинарь — отдельный
    `/usr/local/bin/caddy-naive`, юнит — `caddy-naive.service`,
    конфиг — `/etc/caddy-naive/Caddyfile`. Каталог `/etc/caddy-naive`
    смонтирован в контейнер бота через `compose.yaml`, поэтому мы
    можем читать Caddyfile прямо из бота и убедиться, что это
    именно NaiveProxy, а не штатный Caddy-вебсервер.
    """
    import naiveproxy_manager

    raw = _safe(naiveproxy_manager.get_status, {})
    service_name = (raw.get("service_name") or "caddy-naive").strip()
    # Сознательно НЕ ищем общий "caddy": штатный Caddy-вебсервер
    # ≠ NaiveProxy-сервер. Бинарь NaiveProxy всегда называется специфично.
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
        # Подтверждаем, что найденный процесс — это именно caddy-naive,
        # а не общий caddy, который случайно совпал по подстроке.
        for p in procs:
            exe = _read_proc_exe(p.pid)
            if exe and "caddy-naive" not in os.path.basename(exe).lower():
                notes.append(
                    f"⚠️ найден процесс с PID {p.pid} ({exe or p.comm}), "
                    f"но это не похоже на caddy-naive — проверьте вручную"
                )
        if raw.get("systemd_active") is True:
            notes.append(f"systemd: {service_name} active")
    else:
        # Процесса нет: дадим оператору понятную причину.
        if not binary_exists and not caddyfile_exists:
            notes.append(
                f"NaiveProxy сервер не установлен на этом VPS "
                f"(нет ни {naive_binary}, ни {caddyfile_path}); "
                f"см. Clash Meta/NAIVEPROXY_GUIDE.md §3.3"
            )
        elif not caddyfile_exists:
            notes.append(
                f"бинарь {naive_binary} есть, но конфиг {caddyfile_path} "
                f"отсутствует — Caddyfile не сгенерирован"
            )
        elif not has_forward_proxy:
            notes.append(
                f"{caddyfile_path} существует, но не содержит директиву "
                f"forward_proxy — это похоже на обычный Caddy, не NaiveProxy"
            )
        elif not binary_exists:
            notes.append(
                f"Caddyfile с forward_proxy есть, но бинарь {naive_binary} "
                f"не найден — соберите caddy-naive (xcaddy)"
            )
        else:
            notes.append(
                f"всё на месте, но caddy-naive не запущен; попробуйте: "
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
    """TUIC: серверная автоматизация в боте не реализована (см. /help роадмап).

    Сознательно НЕ привязываем «sing-box на хосте» к TUIC: этот же бинарь
    может обслуживать AnyTLS, ad-hoc VLESS и т.д. Без чтения конфигурации
    sing-box мы не можем сказать, какой именно протокол он отдаёт.
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
        notes=["серверная автоматизация в боте пока не реализована"],
    )


def _anytls_status() -> ProtocolStatus:
    """AnyTLS: см. комментарий в `_tuic_status` — sing-box не привязываем."""
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
        notes=["серверная автоматизация в боте пока не реализована"],
    )


def _xhttp_status() -> ProtocolStatus:
    """XHTTP: серверная сторона разделяется с xray-процессом VLESS-Reality,
    поэтому отдельного процесса нет, а порт совпадает с VLESS — судить
    о XHTTP по слушающему `:443/tcp` нельзя (это будет тот же xray)."""
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
        notes=["серверная автоматизация в боте пока не реализована"],
    )


def _mieru_status() -> ProtocolStatus:
    """
    Mieru (mita): отдельный TCP/UDP транспорт без TLS-маскировки. Бинарь —
    `mita`, юнит — `mita.service` (см. MIERU_GUIDE.md §3-4).

    В отличие от VLESS/XHTTP, Mieru слушает СВОЙ порт (по умолчанию 29999/tcp),
    поэтому здесь и порт, и процесс реально проверяются на хосте.
    Для Mieru критично синхронное системное время (ключ зависит от него) —
    подсказку про NTP добавляем как note, чтобы оператор не забыл.
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
            f"порт {port}/{transport} конфликтует с VLESS/Hysteria2/NaiveProxy — "
            "выберите отдельный порт через /mieru_set_port"
        )
    if bool(raw.get("configured")) and not procs:
        notes.append("config есть, но mita не запущен — попробуйте /mieru_start")
    if procs:
        notes.append("Mieru требует синхронное время на VPS — проверьте `timedatectl status`")

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
    # MTProto-прокси Telegram'а: поддерживаем варианты бинарника
    # mtproto-proxy / mtg / py-tg-proxy. По умолчанию слушает 993 (TCP).
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
    3x-ui (Sanaei) — внешняя web-панель управления Xray. На VPS может
    стоять нативно (`x-ui.service` + бинарь `/usr/local/x-ui/x-ui`) или
    в Docker-контейнере (типичные имена: `3x-ui`, `x-ui`, `3xui`).

    ВАЖНО: 3x-ui управляет СВОИМ Xray (`/usr/local/x-ui/bin/xray-*`) и
    собственной БД клиентов в `/etc/x-ui/x-ui.db`. Бот же пишет в
    свой Xray (`/usr/local/etc/xray`, `xray.service`) — это два
    независимых стека. Поэтому здесь нам важно лишь сообщить оператору,
    что такая панель присутствует на VPS, чтобы он не путал «свой»
    Xray с «панельным» и понимал, что клиенты, созданные ботом, в
    панели не появятся (и наоборот).

    Без креденшелов панели мы НЕ ходим в её API — сейчас только
    бесплатные сигналы: host-процесс, бинарь, systemd-юнит, Docker.
    """
    notes: List[str] = []

    # 1) Нативный процесс / бинарь / systemd unit
    binary_path = "/usr/local/x-ui/x-ui"
    systemd_unit = "/etc/systemd/system/x-ui.service"
    db_path = "/etc/x-ui/x-ui.db"
    binary_exists = _file_exists_and_readable(binary_path)
    unit_exists = _file_exists_and_readable(systemd_unit)
    db_exists = _file_exists_and_readable(db_path)

    procs = find_processes("x-ui")
    # Отсекаем шумные совпадения (например, `xui_panel_status`) —
    # принимаем только процессы, у которых cmdline / comm / exe реально
    # указывают на 3x-ui.
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

    # 2) Docker-контейнер. Перебираем типичные имена; первое найденное
    # «running» считается победителем; если ни один не running, но что-то
    # найдено — записываем как found-but-stopped.
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

    # Сначала — однозначный «тип развёртывания», чтобы не путать с VLESS-транспортом бота и Dockhand.
    if native_procs and running_any:
        notes.append(
            "развёртывание 3x-ui: одновременно процесс на хосте и Docker — "
            f"нетипично; проверьте инстанс (Docker: контейнер `{container_name_used}`)"
        )
    elif native_procs:
        notes.append(
            "развёртывание 3x-ui: нативно на хосте (процесс x-ui), не Docker-контейнер панели"
        )
    elif running_any:
        notes.append(
            f"развёртывание 3x-ui: Docker (контейнер `{container_name_used}`, running)"
        )
    elif found_any:
        notes.append(
            f"развёртывание 3x-ui: Docker (контейнер `{container_name_used}`, не running)"
        )
    elif binary_exists or unit_exists:
        notes.append(
            "развёртывание 3x-ui: признаки нативной установки (бинарь/unit), процесс не запущен"
        )

    if native_procs:
        notes.append("процесс панели на хосте активен")
    elif binary_exists and not native_procs:
        notes.append(f"бинарь {binary_path} найден, но процесс не запущен")
    if unit_exists and not native_procs and not running_any:
        notes.append(f"systemd unit `{systemd_unit}` присутствует, юнит не активен")
    if not (native_procs or binary_exists or unit_exists or found_any):
        # Ничего не нашли — просто помечаем как «отсутствует» без шума в /diag.
        notes.append("3x-ui на этом VPS не обнаружен")
    if db_exists and (native_procs or running_any):
        notes.append(
            "клиенты панели хранятся в /etc/x-ui/x-ui.db; 3x-ui — это "
            "панель управления тем же Xray/VLESS, не отдельный второй VPN"
        )

    proc_names = [p.comm or (p.cmdline.split() or ["x-ui"])[0] for p in native_procs]
    if running_any and not proc_names:
        proc_names = [f"docker:{container_name_used}"]

    return ProtocolStatus(
        key="xui_panel",
        display="3x-ui (сторонняя веб-панель)",
        icon="🛠",
        flag_enabled=None,
        configured=binary_exists or found_any or unit_exists,
        server="",
        port=None,  # порт у панели настраиваемый, читать его из x-ui.db без зависимостей не будем
        transport="tcp",
        process_alive=process_alive if (native_procs or found_any) else None,
        process_names=proc_names,
        port_listening=None,
        container_status=container_info if container_info.get("available") else None,
        # 3x-ui — внешний по отношению к боту инструмент. Поэтому
        # `implemented=True` означает только то, что мы умеем его
        # обнаружить, а не то, что бот им управляет.
        implemented=True,
        notes=notes,
    )


def _dockhand_status() -> ProtocolStatus:
    """
    Dockhand — Streamlit-панель в отдельном Docker-контейнере.

    Идентифицируем по имени контейнера (`dockhand` по умолчанию,
    переопределяется через DOCKHAND_CONTAINER_NAME).
    """
    container_name = os.getenv("DOCKHAND_CONTAINER_NAME", "dockhand")
    info = container_status(container_name)
    notes: List[str] = []
    available = info.get("available", False)
    found = info.get("found", False)
    running = info.get("running", False)
    health = info.get("health") or ""

    if not available:
        notes.append("docker.sock недоступен — статус контейнера не проверен")
    elif not found:
        notes.append(f"контейнер `{container_name}` не найден на этом хосте")
    elif not running:
        notes.append(f"контейнер найден, состояние: {info.get('state') or 'unknown'}")
    elif health and health not in {"healthy", ""}:
        notes.append(f"healthcheck: {health}")

    notes.insert(
        0,
        "Dockhand — Streamlit в Docker (диагностика бота); не Xray-транспорт и не 3x-ui",
    )

    # Порт 8501 у dockhand биндится только на 127.0.0.1 (см. compose.yaml),
    # поэтому к VPS снаружи он недоступен — используем чисто docker-запрос.
    return ProtocolStatus(
        key="dockhand",
        display="Dockhand панель (Docker)",
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
    """Вернуть статусы всех известных боту протоколов."""
    out: List[ProtocolStatus] = []
    for builder in _PROTOCOL_BUILDERS:
        try:
            out.append(builder())
        except Exception as exc:
            logger.warning("live_status: builder %s failed: %s", builder.__name__, exc)
    return out


def gather_full_snapshot() -> Dict:
    """Полный снимок состояния: протоколы + панели + сводка по портам."""
    protocols = gather_protocols()
    snapshot = {
        "protocols": protocols,
        "dockhand": _safe(_dockhand_status, ProtocolStatus(
            key="dockhand", display="Dockhand панель (Docker)", icon="🛠",
            notes=["dockhand: ошибка диагностики"],
        )),
        # Внешняя панель управления Xray (если установлена). Бот ею не
        # управляет — только детектирует и предупреждает оператора.
        "xui_panel": _safe(_xui_panel_status, ProtocolStatus(
            key="xui_panel", display="3x-ui (сторонняя веб-панель)", icon="🛠",
            notes=["xui_panel: ошибка диагностики"],
        )),
        "host_ports": host_listen_ports(),
    }
    return snapshot


def reset_cache() -> None:
    """Очистить кеш — для тестов и принудительного обновления."""
    _CACHE.clear()
