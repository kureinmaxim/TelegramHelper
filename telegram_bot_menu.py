# -*- coding: utf-8 -*-
"""
Telegram Bot Command Menu — регистрация команд через setMyCommands.

Лимит Telegram API: не более 100 команд на область (scope).
Область по умолчанию — базовые команды для всех.
Для каждого ADMIN_USER_IDS — расширенное меню (до 100 команд).

При добавлении новой команды в bot.py добавьте её имя в ALL_REGISTERED_COMMAND_NAMES.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Полный набор имён команд, синхронизируйте с регистрацией CommandHandler в bot.py.
ALL_REGISTERED_COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "ai",
        "ai_provider",
        "anytls_add",
        "anytls_apply",
        "anytls_config",
        "anytls_del",
        "anytls_export",
        "anytls_gen_all",
        "anytls_gen_cert",
        "anytls_list",
        "anytls_logs",
        "anytls_off",
        "anytls_on",
        "anytls_qr",
        "anytls_restart",
        "anytls_set_port",
        "anytls_set_server",
        "anytls_start",
        "anytls_status",
        "anytls_stop",
        "api",
        "backup_list",
        "backup_now",
        "backup_status",
        "backup_test",
        "ch_model",
        "clean_user",
        "clear",
        "email_profile",
        "del_api_key",
        "del_encryption_key",
        "diag",
        "dockhand",
        "encryption_key",
        "exit_node",
        "exit_node_off",
        "exit_node_on",
        "gen_api_key",
        "gen_chacha_key",
        "gen_encryption_key",
        "gen_pqc_key",
        "headscale",
        "headscale_create_user",
        "headscale_disable",
        "headscale_enable",
        "headscale_gen",
        "headscale_revoke",
        "headscale_list_nodes",
        "headscale_set_url",
        "headscale_status",
        "help",
        "en",
        "fr",
        "help_prompt",
        "hy2",
        "id",
        "kb_hide",
        "kb_off",
        "kb_on",
        "kb_translate",
        "hy2_add_client",
        "hy2_apply",
        "hy2_config",
        "hy2_del_client",
        "hy2_export",
        "hy2_gen_all",
        "hy2_gen_cert",
        "hy2_gen_password",
        "hy2_install",
        "hy2_list_clients",
        "hy2_logs",
        "hy2_off",
        "hy2_on",
        "hy2_qr",
        "hy2_restart",
        "hy2_set_insecure",
        "hy2_set_masquerade",
        "hy2_set_obfs",
        "hy2_set_password",
        "hy2_set_port",
        "hy2_set_quic",
        "hy2_set_quic_safe",
        "hy2_set_server",
        "hy2_set_sni",
        "hy2_set_speed",
        "hy2_start",
        "hy2_status",
        "hy2_stop",
        "info",
        "list_users",
        "mt_add_client",
        "mt_apply",
        "mt_config",
        "mt_del_client",
        "mt_export",
        "mt_fetch_config",
        "mt_gen_all",
        "mt_gen_secret",
        "mt_install",
        "mt_list_clients",
        "mt_logs",
        "mt_off",
        "mt_on",
        "mt_qr",
        "mt_restart",
        "mt_set_domain",
        "mt_set_mode",
        "mt_set_port",
        "mt_set_server",
        "mt_set_tag",
        "mt_set_workers",
        "mt_start",
        "mt_status",
        "mt_stop",
        "mieru_add_client",
        "mieru_apply",
        "mieru_config",
        "mieru_del_client",
        "mieru_export",
        "mieru_gen_password",
        "mieru_install",
        "mieru_list_clients",
        "mieru_logs",
        "mieru_restart",
        "mieru_set_dpi",
        "mieru_set_handshake",
        "mieru_set_mtu",
        "mieru_set_multiplexing",
        "mieru_set_port",
        "mieru_set_server",
        "mieru_set_socks5_port",
        "mieru_start",
        "mieru_status",
        "mieru_stop",
        "my_profile",
        "naive_apply",
        "naive_config",
        "naive_export",
        "naive_gen_creds",
        "naive_install",
        "naive_off",
        "naive_on",
        "naive_set_domain",
        "naive_set_dpi",
        "naive_set_password",
        "naive_set_port",
        "naive_set_user",
        "naive_status",
        "naive_uri",
        "nginx_config",
        "nginx_disable",
        "nginx_enable",
        "nginx_set_domain",
        "nginx_status",
        "profiles",
        "prompt",
        "prompt_show",
        "provision",
        "provision_all",
        "rclone",
        "ru",
        "setcity",
        "setemail",
        "setgreeting",
        "settings",
        "special_add",
        "special_remove",
        "start",
        "tr",
        "tr_ai",
        "tuic_add",
        "tuic_apply",
        "tuic_config",
        "tuic_del",
        "tuic_export",
        "tuic_gen_all",
        "tuic_gen_cert",
        "tuic_list",
        "tuic_logs",
        "tuic_off",
        "tuic_on",
        "tuic_qr",
        "tuic_restart",
        "tuic_set_cc",
        "tuic_set_port",
        "tuic_set_server",
        "tuic_start",
        "tuic_status",
        "tuic_stop",
        "user",
        "users_log",
        "ver",
        "vless_add_client",
        "vless_config",
        "vless_del_client",
        "vless_export",
        "vless_gen_keys",
        "vless_list_clients",
        "vless_off",
        "vless_on",
        "vless_qr",
        "vless_reset",
        "vless_set_fingerprint",
        "vless_set_key",
        "vless_set_port",
        "vless_set_server",
        "vless_set_shortid",
        "vless_set_sni",
        "vless_set_uuid",
        "vless_status",
        "vless_sync",
        "vless_test",
        "xhttp_add",
        "xhttp_apply",
        "xhttp_config",
        "xhttp_del",
        "xhttp_export",
        "xhttp_gen_all",
        "xhttp_gen_cert",
        "xhttp_list",
        "xhttp_logs",
        "xhttp_off",
        "xhttp_on",
        "xhttp_qr",
        "xhttp_restart",
        "xhttp_set_host",
        "xhttp_set_mode",
        "xhttp_set_path",
        "xhttp_set_port",
        "xhttp_set_server",
        "xhttp_start",
        "xhttp_status",
        "xhttp_stop",
        "xui_cancel",
        "xui_clear",
        "xui_disable",
        "xui_enable",
        "xui_list",
        "xui_set_inbound",
        "xui_setup",
        "xui_status",
        "xray_apply",
        "xray_config",
        "xray_install",
        "xray_logs",
        "xray_restart",
        "xray_start",
        "xray_status",
        "xray_stop",
        "reticulum_status",
        "reticulum_restart",
        "reticulum_hash",
        "reticulum_i2p",
        "admin_list",
        "admin_add",
        "admin_remove",
    }
)

MAX_MENU_COMMANDS = 100  # лимит Bot API на число команд в одном scope

# Команды, видимые всем пользователям (остальные по-прежнему отсекаются в handlers для не-админов).
PUBLIC_COMMAND_NAMES: Tuple[str, ...] = (
    "start",
    "help",
    "info",
    "id",
    "ai",
    "tr",
    "tr_ai",
    "prompt",
    "clear",
    "diag",
    "settings",
)

SPECIAL_MENU_COMMAND_NAMES: Tuple[str, ...] = (
    "start",
    "ver",
    "help",
    "my_profile",
    "info",
    "id",
    "ai",
    "tr",
    "diag",
    "settings",
    "exit_node",
    "dockhand",
)

# Компактное нижнее меню администратора.
#
# Telegram не поддерживает папки в setMyCommands, поэтому порядок = единственная
# «навигация». Группируем по смыслу сверху вниз (ежедневное → транспорты →
# mesh/HA → админка). Редкие команды остаются в bot.py и доступны вручную
# или через /help.
ADMIN_MENU_PRIORITY: Tuple[str, ...] = (
    # --- обзор (users_log сразу после help — частый вход, как кнопка
    # «📒 Журнал» в /list_users; Telegram не даёт дублировать одну команду) ---
    "start",
    "ver",
    "help",
    "ai",
    "tr",
    "users_log",
    "list_users",
    "diag",
    "info",
    "my_profile",
    # --- пользователи и выдача профилей ---
    "special_add",
    "special_remove",
    "provision",
    "profiles",
    "user",
    "setemail",
    "email_profile",
    # --- 3x-ui / VLESS ---
    "xui_status",
    "vless_status",
    "vless_list_clients",
    "vless_export",
    "vless_gen_keys",
    "vless_set_server",
    "vless_set_port",
    "vless_set_sni",
    "vless_set_fingerprint",
    "vless_set_shortid",
    "xray_apply",
    # --- Hysteria2 ---
    # /hy2 — хаб в автодополнении (клиенты часто не фильтруют hy2_* по префиксу /hy2)
    "hy2",
    "hy2_status",
    "hy2_install",
    "hy2_gen_all",
    "hy2_set_sni",
    "hy2_on",
    "hy2_apply",
    "hy2_start",
    # --- прочие транспорты ---
    "naive_status",
    "mt_status",
    "mieru_status",
    "tuic_status",
    "anytls_status",
    "xhttp_status",
    # --- mesh / exit / панели ---
    "headscale",
    "headscale_status",
    "headscale_list_nodes",
    "headscale_gen",
    "headscale_revoke",
    "exit_node",
    "exit_node_on",
    "exit_node_off",
    "dockhand",
    "reticulum_status",
    "reticulum_hash",
    "reticulum_i2p",
    "reticulum_restart",
    # --- админка / бэкап / ключи ---
    "admin_list",
    "admin_add",
    "admin_remove",
    "backup_status",
    "backup_now",
    "backup_list",
    "api",
    "encryption_key",
    "settings",
    "clear",
)

_DESCRIPTIONS_EXPLICIT: dict[str, str] = {
    "start": "Start the bot and show the welcome panel",
    "help": "Interactive help by section",
    "ai": "Ask the configured AI provider",
    "tr": "Translate text (choose direction)",
    "tr_ai": "AI translation with language detection",
    "ru": "Translate to Russian",
    "en": "Translate to English",
    "fr": "Translate to French",
    "id": "Show your Telegram ID",
    "prompt": "Build a structured prompt",
    "prompt_show": "Show a prompt template",
    "help_prompt": "How to use /prompt",
    "kb_on": "Show the RU/EN/FR translation keyboard",
    "kb_off": "Hide the translation keyboard",
    "info": "Информация о пользователе и сервере",
    "clear": "Очистить историю диалога",
    "ver": "Версия бота и адрес VPS",
    "diag": "Диагностика: кратко всем, полный отчёт — у админа",
    "dockhand": "Подсказка SSH-туннеля к панели Dockhand",
    "settings": "Настройки оформления бота (тема, компактный режим)",
    "rclone": "Краткая справка по offsite backup (rclone)",
    "api": "Показать (маскированный) API-ключ приложения",
    "reticulum_status": "Reticulum/HA-стек: статус сервисов, bridge hash и I2P",
    "reticulum_restart": "Перезапустить HA-стек (bridge + stub gRPC/UDP)",
    "reticulum_hash": "Показать bridge destination hash (для клиентов)",
    "reticulum_i2p": "I2P-путь (путь 2): статус i2pd и b32 моста",
    "admin_list": "Список администраторов (первичные защищены)",
    "admin_add": "Назначить пользователя админом: /admin_add <id>",
    "admin_remove": "Снять админа (кроме первичного): /admin_remove <id>",
    "backup_status": "Статус offsite-бэкапа (rclone)",
    "backup_test": "Проверить remote rclone",
    "backup_now": "Создать архив и отправить в облако",
    "backup_list": "Список последних архивов на remote",
    "encryption_key": "Ключи шифрования по приложениям",
    "gen_api_key": "Сгенерировать API-ключ",
    "del_api_key": "Удалить API-ключ",
    "gen_encryption_key": "Сгенерировать ключ шифрования",
    "del_encryption_key": "Удалить ключ шифрования",
    "gen_chacha_key": "Сгенерировать ChaCha20 ключ",
    "gen_pqc_key": "Сгенерировать постквантовый ключ",
    "list_users": "Список: admin / special / обычные (+ кнопки журнала)",
    "user": "Карточка пользователя по TG ID (профили, QR, ротация)",
    "users_log": "📒 Журнал: первое и последнее обращение пользователей",
    "my_profile": "Мои URL и QR профили (special/admin)",
    "setcity": "Задать город пользователю",
    "setgreeting": "Задать приветствие",
    "special_add": "Добавить в особый список",
    "special_remove": "Убрать из особого списка",
    "ai_provider": "Провайдер ИИ по умолчанию",
    "ch_model": "Выбор модели ИИ",
    "headscale": "Headscale / Tailscale на этом хосте",
    "exit_node": "Выход в интернет через VPS: статус и как включить на устройстве",
    "exit_node_on": "Сделать VPS-координатор exit node'ом (только админ)",
    "exit_node_off": "Выключить exit node на VPS (только админ)",
    "headscale_status": "Headscale: статус, ноды, Web UI Headplane",
    "headscale_list_nodes": "Headscale: список нод mesh",
    "headscale_gen": "Headscale: Pre-Auth ключ ([user] [срок], напр. 720h)",
    "headscale_revoke": "Headscale: отозвать Pre-Auth ключ (только админ)",
    "xui_setup": "Настроить интеграцию с панелью 3x-ui (URL/логин/пароль)",
    "xui_status": "Состояние интеграции 3x-ui",
    "xui_list": "Список inbound'ов в 3x-ui",
    "provision": "Создать клиентов в bot-managed inbound'ах для TG-ID",
    "provision_all": "Провизионинг: все admin + special пакетно",
    "profiles": "Профили admin/special по TG-ID (обычных нет — /special_add)",
    "clean_user": "Удалить bot-managed клиентов пользователя (нужно YES)",
    "vless_list_clients": "Список VLESS-клиентов (legacy или bot-managed)",
    "setemail": "Привязать email к TG-ID (для /email_profile)",
    "email_profile": "Отправить bot-managed профили на email пользователя",
    "xui_set_inbound": "Выбрать inbound по умолчанию для 3x-ui",
    "xui_enable": "Включить интеграцию 3x-ui",
    "xui_disable": "Выключить интеграцию 3x-ui (креды сохраняются)",
    "xui_clear": "Стереть креды 3x-ui (требует YES)",
    "xui_cancel": "Отменить пошаговую настройку 3x-ui",
    "naive_set_dpi": "NaiveProxy: тонкие параметры DPI-исследований",
    "mieru_set_dpi": "Mieru: тонкие параметры DPI-исследований (port/MTU/mux/handshake)",
    "mieru_status": "Mieru: статус mita, порты, число клиентов",
    "mieru_export": "Mieru: client config, mierus:// URI, Clash блок",
    "hy2": "Hysteria2: меню (статус, SNI, on, apply…)",
    "hy2_install": "Hysteria2: установить/починить бинарник и systemd unit",
    "hy2_gen_all": "Hysteria2: пароль + сертификат + публичный IP",
    "hy2_apply": "Hysteria2: применить config.yaml + перезапустить сервис",
    "hy2_start": "Hysteria2: запустить systemd-сервис",
    "hy2_status": "Hysteria2: статус профиля, сервиса и бинарника",
    "hy2_set_sni": "Hysteria2: сменить TLS SNI — без аргумента кнопки выбора",
    "hy2_on": "Hysteria2: включить в /provision и /my_profile",
    "vless_gen_keys": "VLESS: сгенерировать UUID/Reality-ключи (legacy host-Xray)",
    "vless_set_server": "VLESS: задать публичный IP/домен сервера",
    "vless_set_port": "VLESS: сменить TCP-порт (потом restart xray + firewall)",
    "vless_set_sni": "VLESS: сменить Reality SNI — /vless_set_sni без домена даёт кнопки выбора",
    "vless_set_fingerprint": "VLESS: сменить uTLS fingerprint (chrome/ios/…)",
    "vless_set_shortid": "VLESS: сменить Reality short_id (sid)",
    "xray_apply": "VLESS: записать конфиг host-Xray (потом /xray_restart или systemctl)",
}


_PREFIX_LABELS: Tuple[Tuple[str, str], ...] = (
    ("vless_", "VLESS"),
    ("hy2_", "Hysteria2"),
    ("naive_", "NaiveProxy"),
    ("tuic_", "TUIC"),
    ("anytls_", "AnyTLS"),
    ("xhttp_", "XHTTP"),
    ("mieru_", "Mieru"),
    ("mt_", "MTProto"),
    ("xray_", "Xray"),
    ("nginx_", "Nginx"),
    ("headscale_", "Headscale"),
    ("backup_", "Бэкап"),
)


def _infer_description(command: str) -> str:
    """Краткое описание для команд без явной строки в _DESCRIPTIONS_EXPLICIT."""
    for prefix, label in _PREFIX_LABELS:
        if command.startswith(prefix):
            tail = command[len(prefix) :].replace("_", " ").strip()
            return f"{label}: {tail}".strip() if tail else label
    return command.replace("_", " ")


def command_description(command: str) -> str:
    """Текст подсказки для меню Telegram (до 256 символов)."""
    if command in _DESCRIPTIONS_EXPLICIT:
        text = _DESCRIPTIONS_EXPLICIT[command]
    else:
        text = _infer_description(command)
    return text[:256]


def build_public_bot_commands() -> List["BotCommand"]:
    from telegram import BotCommand

    cmds: List[BotCommand] = []
    for name in PUBLIC_COMMAND_NAMES:
        if name not in ALL_REGISTERED_COMMAND_NAMES:
            logger.warning(
                "PUBLIC_COMMAND_NAMES ссылается на неизвестную команду %r — пропуск",
                name,
            )
            continue
        cmds.append(BotCommand(name, command_description(name)))
    return cmds


def build_admin_menu_command_names() -> Tuple[str, ...]:
    """Компактное admin-меню: только ежедневные entrypoints из ADMIN_MENU_PRIORITY."""
    ordered: List[str] = []
    seen: set[str] = set()

    for name in ADMIN_MENU_PRIORITY:
        if name not in ALL_REGISTERED_COMMAND_NAMES:
            logger.warning(
                "ADMIN_MENU_PRIORITY: команда %r отсутствует в ALL_REGISTERED_COMMAND_NAMES — проверьте bot.py",
                name,
            )
            continue
        if name not in seen:
            ordered.append(name)
            seen.add(name)

    total = len(ALL_REGISTERED_COMMAND_NAMES)
    result = tuple(ordered[:MAX_MENU_COMMANDS])
    hidden = max(0, total - len(result))
    if hidden:
        logger.info(
            "Меню администратора: компактный список %s команд. "
            "Ещё %s команд доступны через /help или ввод вручную.",
            len(result),
            hidden,
        )

    return result


def build_admin_bot_commands() -> List["BotCommand"]:
    from telegram import BotCommand

    return [
        BotCommand(name, command_description(name))
        for name in build_admin_menu_command_names()
    ]


def build_special_bot_commands() -> List["BotCommand"]:
    from telegram import BotCommand

    return [
        BotCommand(name, command_description(name))
        for name in SPECIAL_MENU_COMMAND_NAMES
        if name in ALL_REGISTERED_COMMAND_NAMES
    ]


async def setup_bot_commands(bot, config) -> None:
    """
    Вызывать после Application.initialize().
    Регистрирует команды по умолчанию и расширенное меню для каждого ADMIN_USER_IDS.
    """
    from telegram import BotCommandScopeChat, BotCommandScopeDefault
    from telegram.error import TelegramError

    public = build_public_bot_commands()
    special_cmds = build_special_bot_commands()
    admin_cmds = build_admin_bot_commands()

    try:
        await bot.set_my_commands(public, scope=BotCommandScopeDefault())
        logger.info(
            "Меню команд: зарегистрированы базовые команды (%s шт.)", len(public)
        )
    except TelegramError as exc:
        logger.warning("Не удалось установить меню команд по умолчанию: %s", exc)

    try:
        from telegram import MenuButtonCommands

        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Кнопка меню чата: MenuButtonCommands (default scope)")
    except TelegramError as exc:
        logger.warning("Не удалось установить кнопку меню чата: %s", exc)

    try:
        from storage import list_users as storage_list_users

        special_ids, _users = storage_list_users()
    except Exception as exc:
        logger.warning("Не удалось прочитать special_user_ids для меню команд: %s", exc)
        special_ids = []

    admin_ids = {
        int(x)
        for x in (getattr(config, "admin_user_ids", None) or [])
        if str(x).strip().lstrip("-").isdigit()
    }
    for raw_id in special_ids:
        try:
            chat_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if chat_id in admin_ids:
            continue
        try:
            await bot.set_my_commands(
                special_cmds, scope=BotCommandScopeChat(chat_id=chat_id)
            )
            logger.info(
                "Меню команд special зарегистрировано для chat_id=%s (%s команд)",
                chat_id,
                len(special_cmds),
            )
        except TelegramError as exc:
            logger.warning(
                "Не удалось установить меню команд для special %s: %s", chat_id, exc
            )

    if not getattr(config, "admin_user_ids", None):
        logger.info("ADMIN_USER_IDS пуст — расширенное меню администратора не задано")
        return

    for raw_id in config.admin_user_ids:
        try:
            chat_id = int(raw_id)
        except (TypeError, ValueError):
            logger.warning("Некорректный ADMIN_USER_IDS элемент %r — пропуск", raw_id)
            continue
        try:
            await bot.set_my_commands(
                admin_cmds, scope=BotCommandScopeChat(chat_id=chat_id)
            )
            logger.info(
                "Меню команд администратора зарегистрировано для chat_id=%s (%s команд)",
                chat_id,
                len(admin_cmds),
            )
        except TelegramError as exc:
            logger.warning(
                "Не удалось установить меню команд для админа %s: %s", chat_id, exc
            )


async def set_special_bot_menu(bot, chat_id: int) -> None:
    """Назначить special-меню одному пользователю сразу после /special_add."""
    from telegram import BotCommandScopeChat

    await bot.set_my_commands(
        build_special_bot_commands(),
        scope=BotCommandScopeChat(chat_id=int(chat_id)),
    )


async def clear_chat_bot_menu(bot, chat_id: int) -> None:
    """Сбросить персональное меню; пользователь увидит default commands."""
    from telegram import BotCommandScopeChat

    await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=int(chat_id)))
