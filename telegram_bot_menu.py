# -*- coding: utf-8 -*-
"""
Telegram Bot Command Menu — register commands via setMyCommands.

Telegram API limit: at most 100 commands per scope.
The default scope is the base menu for everyone.
Each ADMIN_USER_IDS entry gets an expanded menu (up to 100 commands).

When you add a command in bot.py, add its name to ALL_REGISTERED_COMMAND_NAMES.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Full set of command names; keep in sync with CommandHandler registration in bot.py.
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

MAX_MENU_COMMANDS = 100  # Bot API limit on commands in one scope

# Commands visible to every user (handlers still gate the rest for non-admins).
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

# Compact admin bottom menu.
#
# Telegram has no folders in setMyCommands, so order is the only
# navigation. Group by meaning top to bottom (daily → transports →
# mesh/HA → admin). Rare commands stay in bot.py and are available
# by typing them or via /help.
ADMIN_MENU_PRIORITY: Tuple[str, ...] = (
    # --- overview (users_log right after help — a frequent entry, like the
    # "📒 Log" button in /list_users; Telegram cannot duplicate one command) ---
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
    # --- users and profile delivery ---
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
    # /hy2 — autocomplete hub (clients often do not filter hy2_* by the /hy2 prefix)
    "hy2",
    "hy2_status",
    "hy2_install",
    "hy2_gen_all",
    "hy2_set_sni",
    "hy2_on",
    "hy2_apply",
    "hy2_start",
    # --- other transports ---
    "naive_status",
    "mt_status",
    "mieru_status",
    "tuic_status",
    "anytls_status",
    "xhttp_status",
    # --- mesh / exit / panels ---
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
    # --- admin / backup / keys ---
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
    "info": "User and server information",
    "clear": "Clear conversation history",
    "ver": "Bot version and VPS address",
    "diag": "Diagnostics: short for everyone, full report for admins",
    "dockhand": "SSH tunnel hint for the Dockhand panel",
    "settings": "Bot UI settings (theme, compact mode)",
    "rclone": "Short help for offsite backup (rclone)",
    "api": "Show the (masked) application API key",
    "reticulum_status": "Reticulum/HA stack: service status, bridge hash, and I2P",
    "reticulum_restart": "Restart the HA stack (bridge + stub gRPC/UDP)",
    "reticulum_hash": "Show the bridge destination hash (for clients)",
    "reticulum_i2p": "I2P path (path 2): i2pd status and bridge b32",
    "admin_list": "Administrator list (primary admins are protected)",
    "admin_add": "Make a user an admin: /admin_add <id>",
    "admin_remove": "Remove an admin (except primary): /admin_remove <id>",
    "backup_status": "Offsite backup status (rclone)",
    "backup_test": "Test the rclone remote",
    "backup_now": "Create an archive and upload it to the cloud",
    "backup_list": "List recent archives on the remote",
    "encryption_key": "Per-app encryption keys",
    "gen_api_key": "Generate an API key",
    "del_api_key": "Delete an API key",
    "gen_encryption_key": "Generate an encryption key",
    "del_encryption_key": "Delete an encryption key",
    "gen_chacha_key": "Generate a ChaCha20 key",
    "gen_pqc_key": "Generate a post-quantum key",
    "list_users": "List: admin / special / regular (+ log buttons)",
    "user": "User card by TG ID (profiles, QR, rotation)",
    "users_log": "📒 Log: users' first and last contact",
    "my_profile": "My profile URLs and QR codes (special/admin)",
    "setcity": "Set a user's city",
    "setgreeting": "Set a greeting",
    "special_add": "Add to the special list",
    "special_remove": "Remove from the special list",
    "ai_provider": "Default AI provider",
    "ch_model": "Choose an AI model",
    "headscale": "Headscale / Tailscale on this host",
    "exit_node": "Internet exit via this VPS: status and how to enable it on a device",
    "exit_node_on": "Make the VPS coordinator an exit node (admin only)",
    "exit_node_off": "Disable the exit node on the VPS (admin only)",
    "headscale_status": "Headscale: status, nodes, Headplane web UI",
    "headscale_list_nodes": "Headscale: mesh node list",
    "headscale_gen": "Headscale: pre-auth key ([user] [ttl], e.g. 720h)",
    "headscale_revoke": "Headscale: revoke a pre-auth key (admin only)",
    "xui_setup": "Configure 3x-ui panel integration (URL/login/password)",
    "xui_status": "3x-ui integration status",
    "xui_list": "List inbounds in 3x-ui",
    "provision": "Create clients in bot-managed inbounds for a TG ID",
    "provision_all": "Provision all admin + special users in batch",
    "profiles": "admin/special profiles by TG ID (regular users need /special_add)",
    "clean_user": "Delete a user's bot-managed clients (requires YES)",
    "vless_list_clients": "List VLESS clients (legacy or bot-managed)",
    "setemail": "Bind an email to a TG ID (for /email_profile)",
    "email_profile": "Send bot-managed profiles to the user's email",
    "xui_set_inbound": "Choose the default 3x-ui inbound",
    "xui_enable": "Enable 3x-ui integration",
    "xui_disable": "Disable 3x-ui integration (credentials are kept)",
    "xui_clear": "Erase 3x-ui credentials (requires YES)",
    "xui_cancel": "Cancel the 3x-ui setup wizard",
    "naive_set_dpi": "NaiveProxy: fine-grained DPI research settings",
    "mieru_set_dpi": "Mieru: fine-grained DPI research settings (port/MTU/mux/handshake)",
    "mieru_status": "Mieru: mita status, ports, client count",
    "mieru_export": "Mieru: client config, mierus:// URI, Clash block",
    "hy2": "Hysteria2: menu (status, SNI, on, apply…)",
    "hy2_install": "Hysteria2: install/repair the binary and systemd unit",
    "hy2_gen_all": "Hysteria2: password + certificate + public IP",
    "hy2_apply": "Hysteria2: apply config.yaml and restart the service",
    "hy2_start": "Hysteria2: start the systemd service",
    "hy2_status": "Hysteria2: profile, service, and binary status",
    "hy2_set_sni": "Hysteria2: change TLS SNI — no argument shows choice buttons",
    "hy2_on": "Hysteria2: include in /provision and /my_profile",
    "vless_gen_keys": "VLESS: generate UUID/Reality keys (legacy host Xray)",
    "vless_set_server": "VLESS: set the public server IP/domain",
    "vless_set_port": "VLESS: change the TCP port (then restart xray + firewall)",
    "vless_set_sni": "VLESS: change Reality SNI — /vless_set_sni with no domain shows buttons",
    "vless_set_fingerprint": "VLESS: change the uTLS fingerprint (chrome/ios/…)",
    "vless_set_shortid": "VLESS: change the Reality short_id (sid)",
    "xray_apply": "VLESS: write the host Xray config (then /xray_restart or systemctl)",
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
    ("backup_", "Backup"),
)


def _infer_description(command: str) -> str:
    """Short description for commands without an explicit _DESCRIPTIONS_EXPLICIT entry."""
    for prefix, label in _PREFIX_LABELS:
        if command.startswith(prefix):
            tail = command[len(prefix) :].replace("_", " ").strip()
            return f"{label}: {tail}".strip() if tail else label
    return command.replace("_", " ")


def command_description(command: str) -> str:
    """Hint text for the Telegram menu (up to 256 characters)."""
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
                "PUBLIC_COMMAND_NAMES refers to unknown command %r — skipping",
                name,
            )
            continue
        cmds.append(BotCommand(name, command_description(name)))
    return cmds


def build_admin_menu_command_names() -> Tuple[str, ...]:
    """Compact admin menu: daily entry points from ADMIN_MENU_PRIORITY only."""
    ordered: List[str] = []
    seen: set[str] = set()

    for name in ADMIN_MENU_PRIORITY:
        if name not in ALL_REGISTERED_COMMAND_NAMES:
            logger.warning(
                "ADMIN_MENU_PRIORITY: command %r is missing from ALL_REGISTERED_COMMAND_NAMES — check bot.py",
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
            "Admin menu: compact list of %s commands. "
            "Another %s commands are available via /help or by typing them.",
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
    Call after Application.initialize().
    Registers default commands and an expanded menu for each ADMIN_USER_IDS entry.
    """
    from telegram import BotCommandScopeChat, BotCommandScopeDefault
    from telegram.error import TelegramError

    public = build_public_bot_commands()
    special_cmds = build_special_bot_commands()
    admin_cmds = build_admin_bot_commands()

    try:
        await bot.set_my_commands(public, scope=BotCommandScopeDefault())
        logger.info(
            "Command menu: registered base commands (%s)", len(public)
        )
    except TelegramError as exc:
        logger.warning("Failed to set the default command menu: %s", exc)

    try:
        from telegram import MenuButtonCommands

        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Chat menu button: MenuButtonCommands (default scope)")
    except TelegramError as exc:
        logger.warning("Failed to set the chat menu button: %s", exc)

    try:
        from storage import list_users as storage_list_users

        special_ids, _users = storage_list_users()
    except Exception as exc:
        logger.warning("Failed to read special_user_ids for the command menu: %s", exc)
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
                "Special command menu registered for chat_id=%s (%s commands)",
                chat_id,
                len(special_cmds),
            )
        except TelegramError as exc:
            logger.warning(
                "Failed to set the command menu for special %s: %s", chat_id, exc
            )

    if not getattr(config, "admin_user_ids", None):
        logger.info("ADMIN_USER_IDS is empty — expanded admin menu not set")
        return

    for raw_id in config.admin_user_ids:
        try:
            chat_id = int(raw_id)
        except (TypeError, ValueError):
            logger.warning("Invalid ADMIN_USER_IDS entry %r — skipping", raw_id)
            continue
        try:
            await bot.set_my_commands(
                admin_cmds, scope=BotCommandScopeChat(chat_id=chat_id)
            )
            logger.info(
                "Admin command menu registered for chat_id=%s (%s commands)",
                chat_id,
                len(admin_cmds),
            )
        except TelegramError as exc:
            logger.warning(
                "Failed to set the command menu for admin %s: %s", chat_id, exc
            )


async def set_special_bot_menu(bot, chat_id: int) -> None:
    """Assign the special menu to one user right after /special_add."""
    from telegram import BotCommandScopeChat

    await bot.set_my_commands(
        build_special_bot_commands(),
        scope=BotCommandScopeChat(chat_id=int(chat_id)),
    )


async def clear_chat_bot_menu(bot, chat_id: int) -> None:
    """Clear the per-chat menu; the user will see the default commands."""
    from telegram import BotCommandScopeChat

    await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=int(chat_id)))
