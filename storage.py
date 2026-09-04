"""
Persistent storage for per-user preferences and special user list.
"""

import errno
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List

# Thread safety for concurrent handler access
_storage_lock = threading.Lock()

# Resolve storage path from env or default to project root file
_USER_STORE_PATH = os.getenv("USER_STORE_PATH", os.path.join(os.getcwd(), "users.json"))

# Data shape:
# {
#   "special_user_ids": [123, 456],
#   "users": {
#       "123": {"city": "City Name", "greeting": "Custom greeting"}
#   },
#   "settings": {"echo_enabled": false}
# }

def _ensure_defaults(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    data.setdefault("special_user_ids", [])
    data.setdefault("admin_user_ids", [])  # динамически назначенные админы (env-админы защищены отдельно)
    data.setdefault("users", {})
    settings = data.setdefault("settings", {})
    settings.setdefault("echo_enabled", False)
    return data

def _load_data() -> Dict[str, Any]:
    with _storage_lock:
        if not os.path.exists(_USER_STORE_PATH):
            return _ensure_defaults({})
        try:
            with open(_USER_STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return _ensure_defaults(data)
        except Exception:
            # Corrupted file or read error -> fallback to defaults
            return _ensure_defaults({})

def _atomic_write(data: Dict[str, Any]) -> None:
    with _storage_lock:
        directory = os.path.dirname(_USER_STORE_PATH) or "."
        os.makedirs(directory, exist_ok=True)
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp_path = tempfile.mkstemp(prefix="users_", suffix=".json", dir=directory)
        renamed = False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(serialized)
            try:
                os.replace(tmp_path, _USER_STORE_PATH)
                renamed = True
            except OSError as e:
                # Docker single-file bind mount pins the inode; os.replace
                # fails with EBUSY. Fall back to in-place truncate+write.
                if e.errno not in (errno.EBUSY, errno.EXDEV):
                    raise
                with open(_USER_STORE_PATH, "w", encoding="utf-8") as f:
                    f.write(serialized)
        finally:
            if not renamed:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

# --- Public API ---

def get_user_city(user_id: int) -> Optional[str]:
    data = _load_data()
    user = data["users"].get(str(user_id))
    if not user:
        return None
    city = user.get("city")
    return city if city else None

def set_user_city(user_id: int, city: str) -> None:
    data = _load_data()
    user = data["users"].setdefault(str(user_id), {})
    user["city"] = city
    _atomic_write(data)

def get_user_greeting(user_id: int) -> Optional[str]:
    data = _load_data()
    user = data["users"].get(str(user_id))
    if not user:
        return None
    greeting = user.get("greeting")
    return greeting if greeting else None

def set_user_greeting(user_id: int, greeting: str) -> None:
    data = _load_data()
    user = data["users"].setdefault(str(user_id), {})
    user["greeting"] = greeting
    _atomic_write(data)

def is_special_user(user_id: int) -> bool:
    data = _load_data()
    try:
        return int(user_id) in data.get("special_user_ids", [])
    except Exception:
        return False

def add_special_user(user_id: int) -> None:
    data = _load_data()
    special = data.setdefault("special_user_ids", [])
    if int(user_id) not in special:
        special.append(int(user_id))
        _atomic_write(data)

def remove_special_user(user_id: int) -> None:
    data = _load_data()
    special = data.setdefault("special_user_ids", [])
    if int(user_id) in special:
        special.remove(int(user_id))
        _atomic_write(data)


# --- Динамические администраторы (назначаются админом во время работы). ---
# Первичные админы (из .env ADMIN_USER_IDS) здесь НЕ хранятся и защищены отдельно.

def is_dynamic_admin(user_id: int) -> bool:
    data = _load_data()
    try:
        return int(user_id) in data.get("admin_user_ids", [])
    except Exception:
        return False


def get_dynamic_admins() -> list:
    data = _load_data()
    return [int(x) for x in data.get("admin_user_ids", [])]


def add_dynamic_admin(user_id: int) -> None:
    data = _load_data()
    admins = data.setdefault("admin_user_ids", [])
    if int(user_id) not in admins:
        admins.append(int(user_id))
        _atomic_write(data)


def remove_dynamic_admin(user_id: int) -> None:
    data = _load_data()
    admins = data.setdefault("admin_user_ids", [])
    if int(user_id) in admins:
        admins.remove(int(user_id))
        _atomic_write(data)

def track_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> None:
    """Record that a user interacted with the bot. Stores identity + last_seen."""
    data = _load_data()
    user = data["users"].setdefault(str(user_id), {})
    if username is not None:
        user["username"] = username
    if first_name is not None:
        user["first_name"] = first_name
    if last_name is not None:
        user["last_name"] = last_name
    user["last_seen"] = datetime.now(timezone.utc).isoformat()
    user.setdefault("first_seen", user["last_seen"])
    _atomic_write(data)


def get_user_email(user_id: int) -> Optional[str]:
    """Email связан с пользователем для отправки профилей через
    `/email_profile`. Возвращает None если не задан."""
    data = _load_data()
    user = data["users"].get(str(user_id)) or {}
    e = user.get("email")
    return e if e else None


def set_user_email(user_id: int, email: str) -> None:
    data = _load_data()
    user = data["users"].setdefault(str(user_id), {})
    user["email"] = (email or "").strip().lower()
    _atomic_write(data)


def remove_user_email(user_id: int) -> None:
    data = _load_data()
    user = data["users"].get(str(user_id))
    if not user:
        return
    if "email" in user:
        del user["email"]
        _atomic_write(data)


# --- UI prefs (тема оформления панелей бота; spec 2026-06-11 §7) ---

_UI_THEMES_ALLOWED = ("classic", "minimal", "neon")


def get_ui_prefs(user_id: int) -> Dict[str, Any]:
    """Per-user настройки оформления панелей бота.

    Возвращает всегда валидный dict: {"theme": str, "compact": bool}.
    Битые значения в users.json молча заменяются default'ами.
    """
    data = _load_data()
    user = data["users"].get(str(user_id)) or {}
    theme = user.get("ui_theme")
    if theme not in _UI_THEMES_ALLOWED:
        theme = "classic"
    return {"theme": theme, "compact": bool(user.get("ui_compact", False))}


def set_ui_pref(user_id: int, key: str, value: Any) -> None:
    """key: 'theme' (значение из _UI_THEMES_ALLOWED) или 'compact' (bool)."""
    if key == "theme":
        if value not in _UI_THEMES_ALLOWED:
            raise ValueError(f"unknown theme: {value!r}")
        field, val = "ui_theme", str(value)
    elif key == "compact":
        field, val = "ui_compact", bool(value)
    else:
        raise ValueError(f"unknown ui pref: {key!r}")
    data = _load_data()
    user = data["users"].setdefault(str(user_id), {})
    user[field] = val
    _atomic_write(data)


def get_my_profile_view_count(user_id: int) -> int:
    """Сколько раз пользователь успешно вызывал /my_profile с
    bot-managed профилями. Используется для лимита 2 просмотров."""
    data = _load_data()
    user = data["users"].get(str(user_id)) or {}
    try:
        return int(user.get("my_profile_views", 0))
    except (TypeError, ValueError):
        return 0


def inc_my_profile_view_count(user_id: int) -> int:
    """Атомарно: +1 к счётчику и вернуть новое значение."""
    data = _load_data()
    user = data["users"].setdefault(str(user_id), {})
    try:
        cur = int(user.get("my_profile_views", 0))
    except (TypeError, ValueError):
        cur = 0
    cur += 1
    user["my_profile_views"] = cur
    _atomic_write(data)
    return cur


def reset_my_profile_view_count(user_id: int) -> None:
    """Сбросить счётчик (после /provision или auto-cleanup)."""
    data = _load_data()
    user = data["users"].get(str(user_id))
    if not user:
        return
    if "my_profile_views" in user:
        user["my_profile_views"] = 0
        _atomic_write(data)


def add_my_profile_message(user_id: int, chat_id: int, message_id: int) -> None:
    """Запомнить, что бот отправил пользователю сообщение, которое нужно
    будет либо авто-удалить через 15 мин, либо снести на 3-м просмотре."""
    data = _load_data()
    user = data["users"].setdefault(str(user_id), {})
    msgs = user.setdefault("my_profile_messages", [])
    if not isinstance(msgs, list):
        msgs = []
    msgs.append({
        "chat_id": int(chat_id),
        "message_id": int(message_id),
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    user["my_profile_messages"] = msgs
    _atomic_write(data)


def get_my_profile_messages(user_id: int) -> List[Dict[str, Any]]:
    """Список (chat_id, message_id) ранее отправленных сообщений с URL/QR."""
    data = _load_data()
    user = data["users"].get(str(user_id)) or {}
    msgs = user.get("my_profile_messages") or []
    if not isinstance(msgs, list):
        return []
    return [m for m in msgs if isinstance(m, dict)]


def clear_my_profile_messages(user_id: int) -> None:
    data = _load_data()
    user = data["users"].get(str(user_id))
    if not user:
        return
    if "my_profile_messages" in user:
        user["my_profile_messages"] = []
        _atomic_write(data)


def remove_my_profile_message(user_id: int, chat_id: int, message_id: int) -> None:
    """Снять одну запись после успешного auto-delete."""
    data = _load_data()
    user = data["users"].get(str(user_id))
    if not user:
        return
    msgs = user.get("my_profile_messages") or []
    if not isinstance(msgs, list):
        return
    new_msgs = [
        m for m in msgs
        if not (isinstance(m, dict)
                and m.get("chat_id") == int(chat_id)
                and m.get("message_id") == int(message_id))
    ]
    if len(new_msgs) != len(msgs):
        user["my_profile_messages"] = new_msgs
        _atomic_write(data)


def get_all_my_profile_messages() -> List[Dict[str, Any]]:
    """Все отслеживаемые URL/QR сообщения по ВСЕМ пользователям.

    Возвращает список dict с ключами user_id, chat_id, message_id. Нужно, чтобы
    одним проходом снести устаревшие ссылки/QR из всех чатов (админ + все
    пользователи) — например, после смены порта/SNI/ключей VLESS, когда старые
    ссылки становятся невалидными и не должны больше нигде показываться."""
    data = _load_data()
    out: List[Dict[str, Any]] = []
    for uid_str, user in (data.get("users") or {}).items():
        if not isinstance(user, dict):
            continue
        msgs = user.get("my_profile_messages") or []
        if not isinstance(msgs, list):
            continue
        try:
            uid = int(uid_str)
        except (TypeError, ValueError):
            continue
        for m in msgs:
            if not isinstance(m, dict):
                continue
            out.append({
                "user_id": uid,
                "chat_id": m.get("chat_id"),
                "message_id": m.get("message_id"),
            })
    return out


def clear_all_my_profile_messages() -> None:
    """Очистить трекинг URL/QR сообщений у всех пользователей за один проход."""
    data = _load_data()
    changed = False
    for user in (data.get("users") or {}).values():
        if isinstance(user, dict) and user.get("my_profile_messages"):
            user["my_profile_messages"] = []
            changed = True
    if changed:
        _atomic_write(data)


def list_users() -> Tuple[List[int], Dict[int, Dict[str, Any]]]:
    data = _load_data()
    special = [int(x) for x in data.get("special_user_ids", [])]
    users: Dict[int, Dict[str, Any]] = {}
    for k, v in data.get("users", {}).items():
        try:
            users[int(k)] = v
        except ValueError:
            continue
    return special, users

# --- Global settings ---

def get_echo_enabled() -> bool:
    data = _load_data()
    return bool(data.get("settings", {}).get("echo_enabled", False))

def set_echo_enabled(enabled: bool) -> None:
    data = _load_data()
    settings = data.setdefault("settings", {})
    settings["echo_enabled"] = bool(enabled)
    _atomic_write(data)
