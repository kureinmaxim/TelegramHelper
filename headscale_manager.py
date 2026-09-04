# -*- coding: utf-8 -*-
"""
Модуль для управления Headscale (self-hosted Tailscale control plane).

Взаимодействует с Docker-контейнером Headscale через docker exec
для создания пользователей, генерации Pre-Auth ключей и мониторинга нод.

Структура конфигурации (headscale_config.json):
{
    "enabled": false,
    "container_name": "headscale",
    "server_url": "https://headscale.example.com",
    "default_user": "1",
    "key_expiration": "24h"
}

default_user — числовой ID (CLI ``-u`` uint) или username; имя резолвится
через ``users list`` (Headscale больше не принимает строковое имя в -u).
"""

import json
import os
import re
import subprocess
import logging
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Thread safety
_hs_lock = threading.Lock()

# Путь к файлу конфигурации Headscale
_HS_CONFIG_PATH = os.getenv(
    "HEADSCALE_CONFIG_PATH",
    os.path.join(os.getcwd(), "headscale_config.json"),
)

DEFAULT_CONFIG = {
    "enabled": False,
    "container_name": "headscale",
    "server_url": "",
    "default_user": "1",
    "key_expiration": "24h",
    "created_at": None,
    "updated_at": None,
}


# === Argument parsing (общий для Telegram-бота и SSH CLI) ===

_DURATION_RE = re.compile(r"\d+[smhd]")


def parse_user_expiration(args: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Разобрать аргументы /headscale_gen в (user, expiration).

    Позиционно-независимо: токен вида ``720h``/``30m``/``7d`` — это срок жизни
    ключа, любой другой — имя пользователя. Используется и в Telegram-обработчике
    (handlers.headscale_gen), и в SSH CLI (admin_cli), чтобы логика не разъезжалась.
    """
    user: Optional[str] = None
    expiration: Optional[str] = None
    for arg in args:
        if _DURATION_RE.fullmatch(arg):
            expiration = arg
        else:
            user = arg
    return user, expiration


# === Internal helpers ===

def _load_config() -> Dict:
    """Загрузить конфигурацию Headscale из файла."""
    with _hs_lock:
        if not os.path.exists(_HS_CONFIG_PATH):
            return dict(DEFAULT_CONFIG)
        try:
            with open(_HS_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = dict(DEFAULT_CONFIG)
                config.update(data)
                return config
        except Exception as e:
            logger.error(f"Error loading Headscale config: {e}")
            return dict(DEFAULT_CONFIG)


def _save_config(config: Dict) -> bool:
    """Сохранить конфигурацию Headscale в файл."""
    with _hs_lock:
        try:
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]

            directory = os.path.dirname(_HS_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)

            with open(_HS_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            return True
        except Exception as e:
            logger.error(f"Error saving Headscale config: {e}")
            return False


def _docker_exec(config: Dict, *args: str, timeout: int = 15) -> Tuple[bool, str]:
    """Run a headscale command inside the Docker container.

    Docker CLI живёт на хосте, а бот работает в контейнере без docker.
    Поэтому идём через host_run (nsenter в namespace хоста, требует
    ``pid: host``) — тот же приём, что и get_host_tailscale_client_summary.
    """
    from host_utils import host_run

    container = config.get("container_name", "headscale")
    cmd = ["docker", "exec", container, "headscale"] + list(args)
    try:
        result = host_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip() or f"Exit code {result.returncode}"
    except FileNotFoundError:
        return False, "docker/nsenter не найден в PATH"
    except subprocess.TimeoutExpired:
        return False, f"Таймаут ({timeout}с) при выполнении команды"
    except Exception as e:
        return False, str(e)


# === Public API ===

def is_headscale_enabled() -> bool:
    """Проверить, включён ли Headscale."""
    return _load_config().get("enabled", False)


def get_config() -> Dict:
    """Получить текущую конфигурацию Headscale."""
    return _load_config()


def enable_headscale() -> Tuple[bool, str]:
    """Включить Headscale."""
    config = _load_config()
    config["enabled"] = True
    if _save_config(config):
        return True, "✅ Headscale включён"
    return False, "❌ Ошибка при сохранении"


def disable_headscale() -> Tuple[bool, str]:
    """Выключить Headscale."""
    config = _load_config()
    config["enabled"] = False
    if _save_config(config):
        return True, "✅ Headscale выключен"
    return False, "❌ Ошибка при сохранении"


def set_server_url(url: str) -> Tuple[bool, str]:
    """Установить URL координатора Headscale."""
    url = url.strip().rstrip("/")
    if not url:
        return False, "❌ URL не может быть пустым"

    config = _load_config()
    config["server_url"] = url
    if _save_config(config):
        return True, f"✅ URL установлен: {url}"
    return False, "❌ Ошибка при сохранении"


def set_container_name(name: str) -> Tuple[bool, str]:
    """Установить имя Docker-контейнера Headscale."""
    name = name.strip()
    if not name:
        return False, "❌ Имя контейнера не может быть пустым"

    config = _load_config()
    config["container_name"] = name
    if _save_config(config):
        return True, f"✅ Контейнер: {name}"
    return False, "❌ Ошибка при сохранении"


def create_user(username: str) -> Tuple[bool, str]:
    """Создать пользователя в Headscale."""
    username = username.strip()
    if not username:
        return False, "❌ Имя пользователя не может быть пустым"

    config = _load_config()
    ok, output = _docker_exec(config, "users", "create", username)
    if ok:
        return True, f"✅ Пользователь создан: {username}"
    # «already exists» — не ошибка
    if "already exists" in output.lower():
        return True, f"ℹ️ Пользователь уже существует: {username}"
    return False, f"❌ Ошибка: {output}"


def _resolve_user_id(config: Dict, user: Optional[str]) -> Tuple[bool, str, str]:
    """Привести user к числовому ID для CLI Headscale.

    Современный Headscale (`preauthkeys create -u`) принимает только uint ID,
    не username. Имя (например ``your-user``) резолвим через ``users list -o json``.
    Уже числовая строка («1») возвращается как есть.
    """
    raw = (user or config.get("default_user") or "").strip()
    if not raw:
        return False, "❌ Не задан user (default_user в headscale_config.json)", ""
    if raw.isdigit():
        return True, raw, raw

    ok, output = _docker_exec(config, "users", "list", "-o", "json")
    if not ok:
        return False, f"❌ Не удалось получить users list: {output}", ""
    try:
        users = json.loads(output)
    except json.JSONDecodeError:
        return False, f"❌ Невалидный JSON users list:\n{output[:200]}", ""
    if not isinstance(users, list):
        users = []

    needle = raw.lower()
    for u in users:
        if not isinstance(u, dict):
            continue
        uid = u.get("id")
        names = [
            str(u.get("name") or ""),
            str(u.get("username") or ""),
            str(u.get("display_name") or ""),
        ]
        if any(n.lower() == needle for n in names if n):
            if uid is None:
                continue
            return True, str(uid), f"{raw}→{uid}"

    known = ", ".join(
        f"{u.get('id')}:{u.get('username') or u.get('name') or '?'}"
        for u in users
        if isinstance(u, dict)
    ) or "—"
    return (
        False,
        f"❌ Пользователь «{raw}» не найден. Нужен ID из `users list` "
        f"(у нас часто: 1 = your-user). Известные: {known}",
        "",
    )


def create_preauth_key(
    user: Optional[str] = None,
    reusable: bool = True,
    expiration: Optional[str] = None,
) -> Tuple[bool, str, str]:
    """
    Создать Pre-Auth ключ для подключения клиента.

    Returns:
        (success, message, key)
    """
    config = _load_config()
    expiration = expiration or config.get("key_expiration", "24h")

    ok_uid, uid_or_err, uid_label = _resolve_user_id(config, user)
    if not ok_uid:
        return False, uid_or_err, ""

    args = ["preauthkeys", "create", "--user", uid_or_err, "--expiration", expiration]
    if reusable:
        args.append("--reusable")

    ok, output = _docker_exec(config, *args)
    if not ok:
        return False, f"❌ Ошибка: {output}", ""

    # Headscale выводит ключ в последней строке или в таблице
    key = _parse_preauth_key(output)
    if key:
        return (
            True,
            f"✅ Pre-Auth ключ создан (user: {uid_label}, expiration: {expiration})",
            key,
        )
    return True, f"✅ Ключ создан, но не удалось распарсить вывод:\n{output}", output


def _parse_preauth_key(output: str) -> str:
    """Извлечь Pre-Auth ключ из вывода headscale."""
    # Headscale >= 0.23 выводит ключ на отдельной строке
    lines = output.strip().split("\n")
    for line in reversed(lines):
        line = line.strip()
        # Ключи обычно длинные hex/base64 строки
        if len(line) > 20 and " " not in line:
            return line
    # Fallback: вернуть весь вывод
    return output.strip()


def list_preauth_keys(user: Optional[str] = None) -> Tuple[bool, str, List[Dict]]:
    """Список Pre-Auth ключей пользователя (для отзыва/аудита).

    Returns:
        (success, message, keys) — keys как список словарей из headscale JSON.
    """
    config = _load_config()
    ok_uid, uid_or_err, uid_label = _resolve_user_id(config, user)
    if not ok_uid:
        return False, uid_or_err, []
    ok, output = _docker_exec(
        config, "preauthkeys", "list", "--user", uid_or_err, "-o", "json"
    )
    if not ok:
        return False, f"❌ Ошибка: {output}", []
    try:
        keys = json.loads(output)
        if not isinstance(keys, list):
            keys = []
        return True, f"✅ Ключей у {uid_label}: {len(keys)}", keys
    except json.JSONDecodeError:
        return False, f"❌ Невалидный JSON:\n{output[:200]}", []


def revoke_preauth_key(key: str, user: Optional[str] = None) -> Tuple[bool, str]:
    """Отозвать (просрочить) Pre-Auth ключ.

    Узлы, уже подключённые по этому ключу, остаются в сети — отозвать сам
    узел можно через ``nodes delete`` / Headplane. Отзыв ключа лишь не даёт
    зарегистрировать по нему новые устройства.
    """
    key = key.strip()
    if not key:
        return False, "❌ Ключ не может быть пустым"

    config = _load_config()
    ok_uid, uid_or_err, uid_label = _resolve_user_id(config, user)
    if not ok_uid:
        return False, uid_or_err
    ok, output = _docker_exec(
        config, "preauthkeys", "expire", "--user", uid_or_err, key
    )
    if ok:
        return True, f"✅ Pre-Auth ключ отозван (user: {uid_label})"
    return False, f"❌ Ошибка: {output}"


def list_nodes() -> Tuple[bool, str, List[Dict]]:
    """Получить список подключённых нод."""
    config = _load_config()
    ok, output = _docker_exec(config, "nodes", "list", "-o", "json")
    if not ok:
        return False, f"❌ Ошибка: {output}", []

    try:
        nodes = json.loads(output)
        if not isinstance(nodes, list):
            nodes = []
        return True, f"✅ Найдено нод: {len(nodes)}", nodes
    except json.JSONDecodeError:
        return False, f"❌ Невалидный JSON:\n{output[:200]}", []


def list_users() -> Tuple[bool, str, List[str]]:
    """Получить список пользователей Headscale."""
    config = _load_config()
    ok, output = _docker_exec(config, "users", "list", "-o", "json")
    if not ok:
        return False, f"❌ Ошибка: {output}", []

    try:
        users = json.loads(output)
        if not isinstance(users, list):
            users = []
        names = [u.get("name", "?") for u in users if isinstance(u, dict)]
        return True, f"✅ Пользователей: {len(names)}", names
    except json.JSONDecodeError:
        return False, f"❌ Невалидный JSON:\n{output[:200]}", []


def _is_container_running(container_name: str) -> bool:
    """Проверить, запущен ли Docker-контейнер (docker на хосте, см. host_run)."""
    try:
        from host_utils import host_run

        result = host_run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False


def get_headplane_status() -> Dict:
    """
    Статус Headplane (Web UI) рядом с Headscale.

    Headplane — отдельный контейнер (см. compose.headplane.yaml).
    Запущен/нет определяется по наличию контейнера с именем ``headplane``.
    Доступ — только через SSH-туннель: ``ssh -L 3000:127.0.0.1:3000``.

    Конфиг хранится в ``headplane/config.yaml`` (gitignored), параметры
    в headscale_config.json не дублируются — single source of truth.
    """
    container = "headplane"
    return {
        "container_name": container,
        "container_running": _is_container_running(container),
        # Headplane всегда слушает loopback по compose.headplane.yaml.
        "tunnel_hint": "ssh -L 3000:127.0.0.1:3000 root@<VPS_IP>",
        "browser_url": "http://127.0.0.1:3000/admin",
    }


def get_status() -> Dict:
    """Получить статус Headscale (контейнер, ноды, URL) + статус Headplane."""
    config = _load_config()
    status = {
        "enabled": config.get("enabled", False),
        "server_url": config.get("server_url", ""),
        "container_name": config.get("container_name", "headscale"),
        "container_running": False,
        "node_count": 0,
        "user_count": 0,
        "headplane": get_headplane_status(),
    }

    status["container_running"] = _is_container_running(status["container_name"])

    # Get node count
    if status["container_running"]:
        ok, _, nodes = list_nodes()
        if ok:
            status["node_count"] = len(nodes)
        ok, _, users = list_users()
        if ok:
            status["user_count"] = len(users)

    return status


def get_host_tailscale_client_summary() -> Tuple[bool, str]:
    """
    IPv4/IPv6 адреса локального клиента Tailscale на **хосте** (не контейнер headscale).

    Используется для /headscale: на VPS часто ставят tailscale и подключают к Headscale.
    Внутри Docker с ``pid: host`` команды выполняются в неймспейсе хоста (см. host_utils.host_run).
    """
    try:
        from host_utils import host_run
    except ImportError:
        return False, "❌ Модуль host_utils недоступен."

    candidates = ("tailscale", "/usr/bin/tailscale", "/usr/sbin/tailscale")
    last_detail = ""

    try:
        for bin_path in candidates:
            r = host_run(
                [bin_path, "ip", "-4"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0:
                if not (r.stdout and r.stdout.strip()):
                    return (
                        False,
                        "❌ Tailscale на хосте отвечает, но tailscale ip -4 не вернул адрес.\n\n"
                        "Проверьте на сервере: tailscale status и при необходимости tailscale up.",
                    )
                v4_lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
                v6_lines: List[str] = []
                r6 = host_run(
                    [bin_path, "ip", "-6"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if r6.returncode == 0 and r6.stdout:
                    v6_lines = [
                        ln.strip()
                        for ln in r6.stdout.splitlines()
                        if ln.strip()
                    ]

                lines_msg = [
                    "🌐 Клиент Tailscale на этом сервере (хост):",
                    "",
                    "IPv4:",
                    "\n".join(v4_lines),
                ]
                if v6_lines:
                    lines_msg.extend(["", "IPv6:", "\n".join(v6_lines)])
                lines_msg.extend(
                    [
                        "",
                        "Если адресов нет — на сервере выполните tailscale up с вашим Headscale "
                        "(см. HEADSCALE_GUIDE.md).",
                    ]
                )
                return True, "\n".join(lines_msg)

            err = (r.stderr or r.stdout or "").strip()
            if err:
                last_detail = err
            # «executable not found» — пробуем следующий путь
            if r.returncode != 0 and (
                "not found" in err.lower() or "No such file" in err
            ):
                continue
            # Бинарь есть, но tailscale не поднят / не в сети
            if r.returncode != 0:
                hint = err or f"код выхода {r.returncode}"
                return (
                    False,
                    "❌ Команда tailscale на хосте есть, но адрес не получен.\n\n"
                    f"Детали: {hint}\n\n"
                    "Обычно нужно: tailscale up --login-server <URL> --authkey <ключ> "
                    "(см. HEADSCALE_GUIDE.md).",
                )

        # Ни один путь не сработал с полезным stdout
        if last_detail and ("not found" not in last_detail.lower()):
            return (
                False,
                "❌ На хосте не найден исполняемый файл Tailscale или клиент не в сети.\n\n"
                f"Последняя ошибка: {last_detail}",
            )
        return (
            False,
            "❌ Клиент Tailscale на этом сервере не установлен "
            "(в PATH нет tailscale) или недоступен из контейнера бота.\n\n"
            "Установите Tailscale на VPS и подключите к Headscale — см. HEADSCALE_GUIDE.md.",
        )
    except Exception as e:
        logger.error("get_host_tailscale_client_summary: %s", e)
        return False, f"❌ Ошибка при вызове tailscale: {e}"


# === Exit node (выход в интернет через VPS-координатор) ===
#
# Технология: узел-хост (сам клиент своего tailnet'а) объявляет себя exit node
# (маршрут 0.0.0.0/0 + ::/0), Headscale этот маршрут аппрувит, клиент (телефон)
# выбирает его в приложении Tailscale. Бот доводит серверную часть до состояния
# «доступно», но финальный выбор exit node делается на самом устройстве —
# протолкнуть его сервером невозможно (это локальная настройка клиента).

_EXIT_ROUTES = ("0.0.0.0/0", "::/0")
_TAILSCALE_BINS = ("tailscale", "/usr/bin/tailscale", "/usr/sbin/tailscale")


def _host_run_first(bins, args, timeout: int = 15):
    """Запустить первый доступный бинарь из ``bins`` на хосте через host_run.

    Возвращает (CompletedProcess | None, bin_path | None). None — если ни один
    путь не найден (FileNotFoundError / not found).
    """
    from host_utils import host_run

    last = None
    for bin_path in bins:
        try:
            r = host_run(
                [bin_path, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return None, None
        err = (r.stderr or r.stdout or "").lower()
        if r.returncode != 0 and ("not found" in err or "no such file" in err):
            last = r
            continue
        return r, bin_path
    return last, None


def _host_sysctl_get(key: str) -> Optional[str]:
    """Прочитать sysctl на хосте (например net.ipv4.ip_forward)."""
    r, _ = _host_run_first(("sysctl", "/sbin/sysctl", "/usr/sbin/sysctl"), ["-n", key], timeout=5)
    if r is not None and r.returncode == 0:
        return (r.stdout or "").strip()
    return None


def _host_sysctl_set(key: str, value: str) -> bool:
    """Выставить sysctl на хосте (runtime, не persistent)."""
    r, _ = _host_run_first(("sysctl", "/sbin/sysctl", "/usr/sbin/sysctl"), ["-w", f"{key}={value}"], timeout=5)
    return r is not None and r.returncode == 0


def _node_route_sets(node: Dict) -> Tuple[set, set]:
    """(available, approved) маршруты узла из nodes-list JSON.

    Headscale меняет имена полей между версиями — собираем по всем известным
    ключам, чтобы не привязываться к конкретной версии.
    """
    def pick(*keys) -> set:
        out: set = set()
        for k in keys:
            v = node.get(k)
            if isinstance(v, list):
                out.update(str(x) for x in v)
        return out

    available = pick("availableRoutes", "available_routes", "subnetRoutes", "subnet_routes")
    approved = pick("approvedRoutes", "approved_routes", "enabledRoutes", "enabled_routes")
    return available, approved


def _is_exit_routes(routes: set) -> bool:
    """В наборе есть оба exit-маршрута (или хотя бы IPv4 0.0.0.0/0)."""
    return "0.0.0.0/0" in routes


def _find_exit_candidate(nodes: List[Dict]) -> Optional[Dict]:
    """Узел, который объявил маршрут exit node (0.0.0.0/0 в available)."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        available, _ = _node_route_sets(node)
        if _is_exit_routes(available):
            return node
    return None


def _node_id(node: Dict) -> str:
    """ID узла для approve-routes (строкой)."""
    return str(node.get("id") or node.get("ID") or node.get("nodeId") or "").strip()


def _node_label(node: Dict) -> str:
    name = node.get("givenName") or node.get("name") or "?"
    ips = node.get("ipAddresses") or []
    ip = ips[0] if ips else "?"
    return f"{name} ({ip})"


def _approve_exit_routes(config: Dict, node_id: str) -> Tuple[bool, str]:
    """Аппрувнуть exit-маршруты узла в Headscale.

    Сначала пробуем синтаксис 0.26+ (``nodes approve-routes``); при неудаче
    возвращаем понятную ошибку с подсказкой про Headplane (старые версии
    используют ``routes enable``, которого может не быть).
    """
    routes_csv = ",".join(_EXIT_ROUTES)
    ok, output = _docker_exec(
        config, "nodes", "approve-routes", "-i", node_id, "-r", routes_csv, timeout=20
    )
    if ok:
        return True, output or "маршруты аппрувнуты"
    # Возможно старая версия headscale (нет approve-routes).
    return False, output


def get_exit_node_status() -> Dict:
    """Состояние exit node: forwarding на хосте + advertise/approve в Headscale."""
    config = _load_config()
    status: Dict = {
        "container_running": _is_container_running(config.get("container_name", "headscale")),
        "ip_forward_v4": _host_sysctl_get("net.ipv4.ip_forward"),
        "ip_forward_v6": _host_sysctl_get("net.ipv6.conf.all.forwarding"),
        "advertising": False,   # узел объявил себя exit node
        "approved": False,      # Headscale разрешил exit-маршрут
        "node_label": "",
        "error": "",
    }
    if not status["container_running"]:
        status["error"] = "контейнер headscale не запущен"
        return status

    ok, _msg, nodes = list_nodes()
    if not ok:
        status["error"] = "не удалось получить список нод"
        return status

    cand = _find_exit_candidate(nodes)
    if cand is not None:
        status["advertising"] = True
        status["node_label"] = _node_label(cand)
        _avail, approved = _node_route_sets(cand)
        status["approved"] = _is_exit_routes(approved)
    return status


def enable_exit_node() -> Tuple[bool, str]:
    """Сделать VPS-координатор exit node'ом: advertise на хосте + approve в HS.

    Возвращает (ok, человекочитаемый отчёт по шагам).
    """
    config = _load_config()
    steps: List[str] = []

    # 1. Хост объявляет себя exit node (неразрушающий set, без полного up).
    r, bin_path = _host_run_first(_TAILSCALE_BINS, ["set", "--advertise-exit-node"], timeout=20)
    if r is None and bin_path is None:
        return False, "❌ tailscale на хосте не найден. Установите клиент и подключите к Headscale (HEADSCALE_GUIDE.md §Exit node)."
    if r is not None and r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        return False, f"❌ tailscale set --advertise-exit-node не выполнен: {detail}"
    steps.append("✅ хост объявил себя exit node (advertise)")

    # 2. IP forwarding на хосте (runtime). Persistent — через HEADSCALE_GUIDE.md.
    for key, label in (
        ("net.ipv4.ip_forward", "IPv4"),
        ("net.ipv6.conf.all.forwarding", "IPv6"),
    ):
        cur = _host_sysctl_get(key)
        if cur == "1":
            steps.append(f"✅ forwarding {label} уже включён")
        elif _host_sysctl_set(key, "1"):
            steps.append(f"✅ forwarding {label} включён (runtime; persistent — см. гайд)")
        else:
            steps.append(f"⚠️ forwarding {label} не удалось включить — проверьте на хосте вручную")

    # 3. Approve exit-маршрута в Headscale (нужно дать headscale увидеть advertise).
    ok, _msg, nodes = list_nodes()
    cand = _find_exit_candidate(nodes) if ok else None
    if cand is None:
        steps.append(
            "⚠️ Headscale ещё не видит exit-маршрут от узла. Через 5–10 сек "
            "повторите /exit_node_on или аппрувните 0.0.0.0/0 и ::/0 в Headplane."
        )
        return True, "\n".join(steps)

    node_id = _node_id(cand)
    if not node_id:
        steps.append("⚠️ не удалось определить ID узла — аппрувните маршрут в Headplane.")
        return True, "\n".join(steps)

    appr_ok, appr_out = _approve_exit_routes(config, node_id)
    if appr_ok:
        steps.append(f"✅ Headscale аппрувнул exit-маршрут для {_node_label(cand)}")
        steps.append("")
        steps.append("🎉 Exit node готов. Дальше — выбрать его на устройстве (см. /exit_node).")
    else:
        steps.append(
            f"⚠️ авто-approve не прошёл ({appr_out}). Включите маршруты "
            f"0.0.0.0/0 и ::/0 для узла {_node_label(cand)} в Headplane."
        )
    return True, "\n".join(steps)


def disable_exit_node() -> Tuple[bool, str]:
    """Перестать быть exit node'ом (на хосте отключаем advertise).

    Маршрут в Headscale остаётся аппрувнутым, но без advertise клиенты не
    смогут его использовать. Это обратимо: повторный /exit_node_on вернёт всё.
    """
    r, bin_path = _host_run_first(_TAILSCALE_BINS, ["set", "--advertise-exit-node=false"], timeout=20)
    if r is None and bin_path is None:
        return False, "❌ tailscale на хосте не найден."
    if r is not None and r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        return False, f"❌ Не удалось отключить advertise: {detail}"
    return True, "✅ Exit node выключен: хост больше не объявляет 0.0.0.0/0.\nКлиенты, выбравшие его, потеряют выход через VPS."


def exit_node_client_instructions(node_label: str = "") -> str:
    """Пошаговый гайд для пользователя: как выбрать exit node на устройстве."""
    target = node_label or "узел вашего VPS-координатора"
    return (
        "🌐 Выход в интернет через VPS — как включить на устройстве\n\n"
        f"Exit node: {target}\n\n"
        "📱 iPhone / iPad:\n"
        "  1. Откройте приложение Tailscale\n"
        "  2. Меню (≡) → Exit Node\n"
        f"  3. Выберите {target}\n"
        "  4. (опц.) Allow LAN access — если нужен доступ к локальной сети\n\n"
        "🤖 Android: Tailscale → ⋮ → Use exit node → выберите узел\n\n"
        "💻 macOS / Windows: меню Tailscale в трее → Exit Node → выберите узел\n\n"
        "🐧 Linux: sudo tailscale set --exit-node=<имя-или-IP> --exit-node-allow-lan-access\n\n"
        "Проверка: откройте https://ifconfig.me — должен показать IP вашего VPS.\n"
        "Выбор запоминается: задаёте один раз, дальше Tailscale держит выход сам."
    )


def export_client_instructions(preauth_key: str) -> str:
    """Сгенерировать инструкции для подключения клиента к Headscale."""
    config = _load_config()
    server_url = config.get("server_url", "https://headscale.example.com")

    return f"""== Подключение к Headscale ==

URL координатора: {server_url}
Pre-Auth ключ: {preauth_key}

--- Linux / macOS ---
tailscale up --login-server {server_url} --authkey {preauth_key}

--- Windows ---
1. Shift + клик на иконку Tailscale в трее
2. Preferences → Log in to custom control panel
3. URL: {server_url}
4. Или через PowerShell:
   tailscale up --login-server {server_url} --authkey {preauth_key}
"""
