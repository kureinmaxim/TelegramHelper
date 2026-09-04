"""Dockhand — minimal Streamlit diagnostics panel.

Configuration is env-driven so the same image can be reused for any stack:

    DOCKHAND_TARGETS          comma-separated container names (default: telegram-helper-lite)
    DOCKHAND_API_URL          base URL for the bot's /health endpoint
                              (default: http://telegram-helper:8000)
    DOCKHAND_REFRESH_RATE     auto-refresh interval, seconds (default: 5)
    DOCKHAND_READONLY         if "1"/"true" — hide the Restart button
    DOCKHAND_AUTH_PASSWORD    if set — gate the panel behind a password prompt
                              (constant-time compared with hmac.compare_digest)
    DOCKHAND_API_KEY          optional API key for /admin_command actions
    DOCKHAND_APP_ID           app id for /admin_command (default: apiai-v3)
    DOCKHAND_HIDE_HEALTH_DEFAULT  hide GET /health lines by default (true/false, default: true)
    DOCKHAND_LOG_DEFAULT_TAB      default log tab: all|errors|user_actions|health (default: all)

Docker access is delegated to docker-socket-proxy via DOCKER_HOST=tcp://...
(set in compose.yaml). Direct /var/run/docker.sock mount is no longer required.
"""

from __future__ import annotations

import hmac
import html
import os
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

import docker
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh


# ── Configuration (env-driven) ──────────────────────────────────────────────


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve_vps_display_host() -> str:
    """Адрес VPS для сайдбара: те же переменные, что в боте / SETUP_ENV, иначе короткий HTTP-запрос."""
    for key in (
        "DOCKHAND_SSH_HOST",
        "TELEGRAMHELPER_SSH_HOST",
        "TELEGRAMHELPER_PUBLIC_HOST",
        "PUBLIC_HOST",
        "VPS_HOST",
    ):
        v = os.getenv(key, "").strip()
        if v:
            return v
    try:
        import urllib.request

        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as resp:
            ip = (resp.read() or b"").decode("utf-8", errors="replace").strip()
            if ip:
                return f"{ip} (авто)"
    except Exception:
        pass
    return "не задан в .env (см. DOCKHAND_SSH_HOST / TELEGRAMHELPER_PUBLIC_HOST)"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, value)


TARGET_CONTAINERS: list[str] = _env_list(
    "DOCKHAND_TARGETS", ["telegram-helper-lite"]
)
API_URL: str = os.getenv("DOCKHAND_API_URL", "http://telegram-helper:8000").rstrip("/")
REFRESH_RATE: int = _env_int("DOCKHAND_REFRESH_RATE", 5, minimum=1)
READ_ONLY: bool = _env_bool("DOCKHAND_READONLY", False)
AUTH_PASSWORD: str = os.getenv("DOCKHAND_AUTH_PASSWORD", "")
DOCKHAND_API_KEY: str = os.getenv("DOCKHAND_API_KEY", "")
DOCKHAND_APP_ID: str = os.getenv("DOCKHAND_APP_ID", "apiai-v3")
HIDE_HEALTH_DEFAULT: bool = _env_bool("DOCKHAND_HIDE_HEALTH_DEFAULT", True)
LOG_DEFAULT_TAB: str = os.getenv("DOCKHAND_LOG_DEFAULT_TAB", "all").strip().lower()
if LOG_DEFAULT_TAB not in {"all", "errors", "user_actions", "health"}:
    LOG_DEFAULT_TAB = "all"
DOCKHAND_UI_VERSION: str = "v5"
st.set_page_config(
    page_title="Dockhand Diagnostics",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Compact UI styles ───────────────────────────────────────────────────────
# Уменьшаем дефолтные крупные элементы Streamlit (subheader, st.metric,
# alert-блоки) чтобы левая колонка визуально не "перевешивала" компактные
# логи справа. Это только косметика — структура и компоненты не меняются.
st.markdown(
    """
    <style>
    /* st.subheader -> h3: ближе к размеру строки лога (~12.5px) */
    section.main h3 {
        font-size: 0.88rem !important;
        font-weight: 600 !important;
        margin-top: 0.45rem !important;
        margin-bottom: 0.2rem !important;
        letter-spacing: 0.01em;
        line-height: 1.25 !important;
    }
    section.main h2 {
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        margin-top: 0.5rem !important;
        margin-bottom: 0.25rem !important;
        line-height: 1.3 !important;
    }
    /* st.title (h1) — заметно, но не «баннером» */
    section.main h1 {
        font-size: 1.18rem !important;
        font-weight: 600 !important;
        margin-top: 0 !important;
        margin-bottom: 0.15rem !important;
        line-height: 1.25 !important;
    }
    /* Первая строка под заголовком (Monitoring … | API) */
    section.main h1 + div [data-testid="stMarkdownContainer"] p {
        font-size: 0.8rem !important;
        line-height: 1.35 !important;
        opacity: 0.9;
        margin-top: 0 !important;
        margin-bottom: 0.35rem !important;
    }
    /* st.metric — крупные числа CPU/Memory режем до читаемого размера */
    [data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 6px;
        padding: 6px 10px;
    }
    [data-testid="stMetricValue"],
    [data-testid="stMetricValue"] > div {
        font-size: 1rem !important;
        font-weight: 600 !important;
        line-height: 1.2 !important;
    }
    [data-testid="stMetricLabel"],
    [data-testid="stMetricLabel"] p {
        font-size: 0.72rem !important;
        opacity: 0.75;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    /* st.success / st.info / st.error / st.warning — компактнее */
    [data-testid="stAlert"] {
        padding: 8px 10px !important;
    }
    [data-testid="stAlert"] p,
    [data-testid="stAlert"] div {
        font-size: 0.85rem !important;
        line-height: 1.35 !important;
    }
    /* st.caption — чуть меньше, ближе к подписи */
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p {
        font-size: 0.78rem !important;
        opacity: 0.75;
    }
    /* st.divider — компактнее по вертикали */
    [data-testid="stHorizontalBlock"] + hr,
    section.main hr {
        margin: 0.5rem 0 !important;
    }
    /* Вкладки логов (All / Errors / …) — меньше кнопки */
    section.main [data-testid="stTabs"] button {
        font-size: 0.78rem !important;
        padding: 0.35rem 0.55rem !important;
    }
    section.main [data-testid="stTabs"] [data-baseweb="tab-list"] {
        gap: 0.15rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Authentication gate (defense in depth on top of SSH tunnel) ─────────────


def _check_password() -> bool:
    """Optional password gate. Disabled if DOCKHAND_AUTH_PASSWORD is empty."""
    if not AUTH_PASSWORD:
        return True
    if st.session_state.get("dockhand_auth_ok"):
        return True

    st.title("🩺 Dockhand")
    st.caption("Защищённая зона. Введите пароль для входа.")
    pw = st.text_input("Пароль", type="password", key="dockhand_pw_input")
    if pw:
        # constant-time comparison; both operands must be bytes
        if hmac.compare_digest(pw.encode("utf-8"), AUTH_PASSWORD.encode("utf-8")):
            st.session_state["dockhand_auth_ok"] = True
            st.rerun()
        else:
            st.error("Неверный пароль")
    return False


if not _check_password():
    st.stop()


# ── Docker client (cached, talks to docker-socket-proxy by DOCKER_HOST) ─────


@st.cache_resource
def get_docker_client() -> Optional[docker.DockerClient]:
    try:
        return docker.from_env()
    except Exception as exc:
        st.error(f"Failed to connect to Docker daemon: {exc}")
        return None


client = get_docker_client()


# ── Helpers ─────────────────────────────────────────────────────────────────


def fetch_container(name: str):
    """Single round-trip to docker — return container object or None."""
    if client is None:
        return None, "Docker connection failed"
    try:
        return client.containers.get(name), None
    except docker.errors.NotFound:
        return None, "Not Found"
    except Exception as exc:
        return None, f"Error: {exc}"


def restart_container(container) -> Tuple[bool, str]:
    """Restart an already-fetched container — no extra round-trip."""
    if container is None:
        return False, "Container unavailable"
    try:
        container.restart()
        return True, "Restart initiated"
    except Exception as exc:
        return False, f"Restart failed: {exc}"


def format_created(raw: Optional[str]) -> str:
    """Pretty-print Docker's ISO-with-nanoseconds Created timestamp."""
    if not raw:
        return "—"
    # Docker ships nanoseconds + "Z"; trim to microseconds for fromisoformat.
    cleaned = raw
    if "." in cleaned:
        head, frac = cleaned.split(".", 1)
        # frac may end with "Z" or timezone suffix
        tz_suffix = ""
        if frac.endswith("Z"):
            frac = frac[:-1]
            tz_suffix = "+00:00"
        elif "+" in frac or "-" in frac[1:]:
            # find tz offset start
            for idx in range(1, len(frac)):
                if frac[idx] in "+-":
                    tz_suffix = frac[idx:]
                    frac = frac[:idx]
                    break
        cleaned = f"{head}.{frac[:6]}{tz_suffix}"
    elif cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        return raw


def get_api_health() -> Tuple[bool, object]:
    try:
        response = requests.get(f"{API_URL}/health", timeout=2)
        if response.status_code == 200:
            try:
                return True, response.json()
            except ValueError:
                return True, {"raw": response.text}
        return False, f"Status code: {response.status_code}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


def run_admin_command(command: str, timeout: int = 30) -> Tuple[bool, str]:
    """Call bot /admin_command without exposing API secrets in the UI."""
    if not DOCKHAND_API_KEY:
        return False, "DOCKHAND_API_KEY is not configured"
    try:
        response = requests.post(
            f"{API_URL}/admin_command",
            json={"command": command, "args": []},
            headers={"X-API-KEY": DOCKHAND_API_KEY, "X-APP-ID": DOCKHAND_APP_ID},
            timeout=timeout,
        )
    except Exception as exc:
        return False, f"Admin command failed: {exc}"

    if response.status_code != 200:
        return False, f"HTTP {response.status_code}: {response.text[:500]}"
    try:
        payload = response.json()
    except ValueError:
        return False, response.text[:1000]
    return bool(payload.get("success")), str(payload.get("response", payload))


def get_container_logs(container, lines: int) -> str:
    try:
        # errors='replace' — повреждённый байт в логах не должен валить страницу
        return container.logs(tail=lines).decode("utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading logs: {exc}"


BOT_TOKEN_IN_URL_RE = re.compile(r"/bot(\d+:[A-Za-z0-9_-]+)/")
BOT_TOKEN_RE = re.compile(r"\b(\d+:[A-Za-z0-9_-]{20,})\b")
HTTP_STATUS_RE = re.compile(r'HTTP/\d+\.\d+"?\s+(\d{3})\b')
INLINE_TS_RE = re.compile(r"(?<!\n)(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - )")
INLINE_UVICORN_RE = re.compile(r'(?<!\n)(INFO:\s+\d{1,3}(?:\.\d{1,3}){3}:\d+\s+-\s+"(?:GET|POST|PUT|PATCH|DELETE) )')
INLINE_LEVEL_RE = re.compile(r"(?<!\n)(INFO|WARNING|ERROR|DEBUG):\s")
LEVELS = ["ERROR", "WARNING", "INFO", "DEBUG"]


def sanitize_sensitive_text(text: str) -> str:
    """Mask secrets in text before rendering in UI."""
    sanitized = BOT_TOKEN_IN_URL_RE.sub("/bot***REDACTED***/", text)
    sanitized = BOT_TOKEN_RE.sub("***REDACTED***", sanitized)
    return sanitized


def normalize_log_text(text: str) -> str:
    """Repair common Docker log line gluing for better readability."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # Some runtimes emit consecutive entries in one chunk; split by clear starters.
    normalized = INLINE_TS_RE.sub(r"\n\1", normalized)
    normalized = INLINE_UVICORN_RE.sub(r"\n\1", normalized)
    normalized = INLINE_LEVEL_RE.sub(r"\n\1: ", normalized)
    # Avoid leading empty line if first token was prefixed.
    return normalized.lstrip("\n")


def detect_level(line: str) -> Optional[str]:
    upper = line.upper()
    for lvl in LEVELS:
        if f" {lvl} " in upper or upper.startswith(f"{lvl} "):
            return lvl
    return None


def classify_line(line: str) -> str:
    lower = line.lower()
    if "get /health" in lower:
        return "health"
    if "handlers - info - user" in lower or "requested" in lower:
        return "user_actions"
    if "error" in lower or "exception" in lower or "traceback" in lower:
        return "errors"
    # HTTP 4xx/5xx — только в реальном HTTP-контексте (uvicorn/httpx),
    # чтобы не ловить миллисекунды из timestamp типа `,584`.
    http_match = HTTP_STATUS_RE.search(line)
    if http_match:
        try:
            code = int(http_match.group(1))
        except (TypeError, ValueError):
            code = 0
        if 400 <= code <= 599:
            return "errors"
    return "all"


def filter_log_lines(
    logs: str,
    needle: str,
    selected_levels: list[str],
    hide_health: bool,
    mode: str,
) -> list[str]:
    """Filter log lines by text, level, health suppression and tab mode."""
    needle_lower = needle.lower().strip()
    filtered: list[str] = []

    for raw_line in normalize_log_text(logs).splitlines():
        line = sanitize_sensitive_text(raw_line)
        if not line.strip():
            continue

        kind = classify_line(line)
        if hide_health and kind == "health":
            continue

        if mode == "errors" and kind != "errors":
            continue
        if mode == "user_actions" and kind != "user_actions":
            continue
        if mode == "health" and kind != "health":
            continue

        if selected_levels:
            lvl = detect_level(line)
            if lvl is None or lvl not in selected_levels:
                continue

        if needle_lower and needle_lower not in line.lower():
            continue
        filtered.append(line)

    return filtered


def _line_color(line: str) -> str:
    upper = line.upper()
    if "ERROR" in upper or "EXCEPTION" in upper or "TRACEBACK" in upper:
        return "#ff6b6b"
    if "WARNING" in upper:
        return "#f2c46d"

    match = HTTP_STATUS_RE.search(line)
    if match:
        code = int(match.group(1))
        if 500 <= code <= 599:
            return "#ff6b6b"
        if 400 <= code <= 499:
            return "#f2c46d"
        if 200 <= code <= 299:
            return "#7bd88f"
    return "#d7d7d7"


def render_colored_logs(lines: list[str]) -> str:
    """Render log lines as a monospace, per-row HTML block.

    Каждая запись — отдельный блочный <div>, поэтому Streamlit markdown
    не схлопывает пробелы между строками, и логи читаются построчно.
    """
    container_style = (
        "font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;"
        " font-size: 12.5px;"
        " line-height: 1.55;"
        " background: rgba(0,0,0,0.28);"
        " border: 1px solid rgba(255,255,255,0.06);"
        " border-radius: 6px;"
        " padding: 8px 10px;"
        " max-height: 620px;"
        " overflow-y: auto;"
        " white-space: pre-wrap;"
        " word-break: break-word;"
    )

    if not lines:
        return (
            f'<div style="{container_style}">'
            '<span style="opacity:0.6">(no matching lines)</span>'
            "</div>"
        )

    row_base = (
        "padding: 2px 4px;"
        " border-bottom: 1px solid rgba(255,255,255,0.05);"
    )

    rendered_rows = []
    for line in lines:
        color = _line_color(line)
        rendered_rows.append(
            f'<div style="{row_base} color:{color};">{html.escape(line)}</div>'
        )

    return f'<div style="{container_style}">' + "".join(rendered_rows) + "</div>"


def get_container_stats(container) -> Optional[dict]:
    """Snapshot container stats. Returns CPU%, memory MiB and limit, network IO."""
    try:
        stats = container.stats(stream=False)
    except Exception:
        return None

    # CPU % — формула из docker CLI (стандартная)
    cpu_stats = stats.get("cpu_stats", {})
    pre_cpu = stats.get("precpu_stats", {})
    cpu_total = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
    pre_total = pre_cpu.get("cpu_usage", {}).get("total_usage", 0)
    system = cpu_stats.get("system_cpu_usage", 0)
    pre_system = pre_cpu.get("system_cpu_usage", 0)
    online_cpus = cpu_stats.get("online_cpus") or len(
        cpu_stats.get("cpu_usage", {}).get("percpu_usage", []) or [1]
    )

    cpu_delta = cpu_total - pre_total
    system_delta = system - pre_system
    cpu_percent = 0.0
    if cpu_delta > 0 and system_delta > 0:
        cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0

    mem = stats.get("memory_stats", {})
    mem_usage = mem.get("usage", 0)
    mem_limit = mem.get("limit", 0)
    # cgroup v2 убирает "cache" из usage → пытаемся вычесть, если есть
    cache = mem.get("stats", {}).get("inactive_file") or mem.get("stats", {}).get(
        "cache", 0
    )
    mem_actual = max(0, mem_usage - cache) if cache else mem_usage

    nets = stats.get("networks", {}) or {}
    rx = sum(n.get("rx_bytes", 0) for n in nets.values())
    tx = sum(n.get("tx_bytes", 0) for n in nets.values())

    return {
        "cpu_percent": round(cpu_percent, 2),
        "mem_usage_mib": round(mem_actual / 1024 / 1024, 1),
        "mem_limit_mib": round(mem_limit / 1024 / 1024, 1) if mem_limit else 0,
        "net_rx_mib": round(rx / 1024 / 1024, 2),
        "net_tx_mib": round(tx / 1024 / 1024, 2),
    }


# ── Auto-refresh ────────────────────────────────────────────────────────────

# Реализация через streamlit-autorefresh: компонент сам запускает rerun
# по таймеру внутри клиента, без гонок и без st.session_state-таймеров.
st_autorefresh(interval=REFRESH_RATE * 1000, key="dockhand_autorefresh")


# ── Sidebar — выбор контейнера ──────────────────────────────────────────────

if "dockhand_session_opened_at" not in st.session_state:
    st.session_state["dockhand_session_opened_at"] = datetime.now(timezone.utc)

st.sidebar.title("Dockhand")
st.sidebar.caption(f"UI {DOCKHAND_UI_VERSION}")

vps_host = _resolve_vps_display_host()
st.sidebar.markdown(f"**Адрес VPS:** `{vps_host}`")

opened = st.session_state["dockhand_session_opened_at"]
st.sidebar.caption(
    f"Время открытия сессии: {opened.strftime('%Y-%m-%d %H:%M:%S')} UTC"
)

if len(TARGET_CONTAINERS) > 1:
    selected = st.sidebar.selectbox(
        "Контейнер", TARGET_CONTAINERS, index=0, key="dockhand_target"
    )
else:
    selected = TARGET_CONTAINERS[0]
    st.sidebar.markdown(f"**Контейнер:** `{selected}`")

if READ_ONLY:
    st.sidebar.info("Режим только-чтение (DOCKHAND_READONLY=1)")

if AUTH_PASSWORD and st.sidebar.button("Выйти"):
    st.session_state.pop("dockhand_auth_ok", None)
    st.rerun()


# ── Main UI ─────────────────────────────────────────────────────────────────

st.title(f"🩺 Dockhand Diagnostics · UI {DOCKHAND_UI_VERSION}")
st.markdown(f"Monitoring **{selected}** | API: `{API_URL}`")

container, fetch_error = fetch_container(selected)
status_raw = container.status if container is not None else (fetch_error or "Unknown")
status_text = (status_raw or "Unknown").upper()
status_color = "green" if status_raw == "running" else "red"

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("System Status")
    st.markdown(f"**Container Status:** :{status_color}[{status_text}]")

    if container is not None:
        created = format_created(container.attrs.get("Created"))
        st.caption(f"Created: {created}")

        stats = get_container_stats(container)
        if stats:
            mem_label = (
                f"{stats['mem_usage_mib']:.1f} MiB"
                + (
                    f" / {stats['mem_limit_mib']:.0f} MiB"
                    if stats["mem_limit_mib"]
                    else ""
                )
            )
            mcol1, mcol2 = st.columns(2)
            mcol1.metric("CPU", f"{stats['cpu_percent']:.1f}%")
            mcol2.metric("Memory", mem_label)
            st.caption(
                f"Network: ↓ {stats['net_rx_mib']:.2f} MiB · ↑ {stats['net_tx_mib']:.2f} MiB"
            )

        if not READ_ONLY:
            if st.button("🔄 Restart Container", type="primary"):
                with st.spinner("Restarting..."):
                    ok, msg = restart_container(container)
                    if ok:
                        st.success(msg)
                        st.cache_resource.clear()
                        st.rerun()
                    else:
                        st.error(msg)

    st.divider()

    st.subheader("API Health")
    is_healthy, health_data = get_api_health()
    if is_healthy:
        st.success("API is Online")
        if isinstance(health_data, dict):
            st.json(health_data, expanded=False)
        else:
            st.write(health_data)
    else:
        st.error(f"API Unreachable: {health_data}")

    st.divider()
    st.subheader("Offsite Backups")
    st.caption("Rclone backup runs inside the bot container via /admin_command.")
    if not DOCKHAND_API_KEY:
        st.info("Set DOCKHAND_API_KEY to enable backup actions from Dockhand.")
    else:
        b1, b2 = st.columns(2)
        if b1.button("Backup status", use_container_width=True):
            ok, text = run_admin_command("/backup_status")
            (st.success if ok else st.error)(text)
        if b2.button("Test remote", use_container_width=True):
            ok, text = run_admin_command("/backup_test")
            (st.success if ok else st.error)(text)
        if st.button("List backups", use_container_width=True):
            ok, text = run_admin_command("/backup_list")
            (st.success if ok else st.error)(text)
        if READ_ONLY:
            st.caption("Run backup is hidden in read-only mode.")
        elif st.button("Run backup now", type="primary", use_container_width=True):
            with st.spinner("Creating encrypted offsite backup..."):
                ok, text = run_admin_command("/backup_now", timeout=330)
            (st.success if ok else st.error)(text)

with col2:
    current_time = datetime.now().strftime("%H:%M:%S")
    st.subheader(f"Live Logs (Updated: {current_time})")

    with st.expander("Как понимать логи", expanded=False):
        st.markdown(
            """
- `GET /health ... 200` — healthcheck, сервис жив.
- `.../getUpdates ... 200` — бот проверил новые сообщения.
- `handlers - INFO - User ...` — действие пользователя в боте.
- `.../sendMessage ... 200` — бот успешно отправил ответ.
- Коды: `2xx` — ок, `4xx` — проблема запроса/прав, `5xx` — сбой сервиса/сети.
"""
        )

    log_lines = st.slider("Log lines", 10, 1000, 30, key="dockhand_log_lines")
    log_filter = st.text_input(
        "Фильтр (подстрока)", value="", key="dockhand_log_filter"
    )
    selected_levels = st.multiselect(
        "Уровни логов",
        options=LEVELS,
        default=[],
        help="Пусто = показывать все уровни.",
        key="dockhand_log_levels",
    )
    hide_health = st.checkbox(
        "Скрывать healthcheck",
        value=HIDE_HEALTH_DEFAULT,
        key="dockhand_hide_health",
    )

    if container is not None:
        raw_logs = get_container_logs(container, log_lines)
        tab_items = [
            ("All", "all"),
            ("Errors", "errors"),
            ("User actions", "user_actions"),
            ("Health", "health"),
        ]
        # Streamlit не позволяет программно выбрать активную вкладку,
        # поэтому нужную вкладку делаем первой.
        tab_items.sort(key=lambda item: 0 if item[1] == LOG_DEFAULT_TAB else 1)
        tab_titles = [title for title, _ in tab_items]
        tabs = st.tabs(tab_titles)

        for tab, (_, mode) in zip(tabs, tab_items):
            with tab:
                lines = filter_log_lines(
                    logs=raw_logs,
                    needle=log_filter,
                    selected_levels=selected_levels,
                    hide_health=hide_health if mode != "health" else False,
                    mode=mode,
                )
                st.caption(f"Найдено строк: {len(lines)}")
                st.markdown(render_colored_logs(lines), unsafe_allow_html=True)

        st.download_button(
            "Скачать логи (raw)",
            data=sanitize_sensitive_text(raw_logs),
            file_name=f"{selected}-logs.txt",
            mime="text/plain",
            use_container_width=True,
        )
    else:
        st.warning(f"Container `{selected}` not available — cannot show logs.")
        if fetch_error:
            st.caption(fetch_error)


# ── Footer ──────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    f"Refresh: {REFRESH_RATE}s · "
    f"Targets: {', '.join(TARGET_CONTAINERS)} · "
    f"Mode: {'read-only' if READ_ONLY else 'full'} · "
    f"UI {DOCKHAND_UI_VERSION}"
)
