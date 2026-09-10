# -*- coding: utf-8 -*-
"""
Simplified bot command handlers with VLESS-Reality support.

This module contains a minimal command set:
- Basic: start, help, info, clear
- Admin: ver, dockhand, headscale, api, gen_api_key, del_api_key, encryption_key, gen_encryption_key,
             del_encryption_key, gen_chacha_key, gen_pqc_key
- User management: list_users, users_log, setcity, setgreeting, special_add, special_remove
- AI settings: ai_provider, ch_model
- VLESS-Reality: vless_status, vless_on, vless_off, vless_config, vless_set_*, vless_gen_keys, vless_test
"""

import asyncio
import base64
import html
import json
import logging
import os
import re
import secrets
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import anytls_manager
import email_manager
import headscale_manager
import hysteria2_manager
import live_status
import mieru_manager
import mtproto_manager
import naiveproxy_manager
import provision_manager
import rclone_manager
import tuic_manager
import vless_manager
import xhttp_manager
import xui_manager
from ai_translate_mixin import AITranslateMixin
from config import Config
from storage import (
    add_my_profile_message as storage_add_my_profile_message,
)
from storage import (
    add_special_user,
    get_user_city,
    get_user_greeting,
    remove_special_user,
    set_user_city,
    set_user_greeting,
)
from storage import (
    clear_all_my_profile_messages as storage_clear_all_my_profile_messages,
)
from storage import (
    clear_my_profile_messages as storage_clear_my_profile_messages,
)
from storage import (
    get_all_my_profile_messages as storage_get_all_my_profile_messages,
)
from storage import (
    get_my_profile_messages as storage_get_my_profile_messages,
)
from storage import (
    get_my_profile_view_count as storage_get_my_profile_views,
)
from storage import (
    get_ui_prefs as storage_get_ui_prefs,
)
from storage import (
    get_user_email as storage_get_user_email,
)
from storage import (
    inc_my_profile_view_count as storage_inc_my_profile_views,
)
from storage import (
    is_special_user as storage_is_special_user,
)
from storage import (
    list_users as storage_list_users,
)
from storage import (
    remove_my_profile_message as storage_remove_my_profile_message,
)
from storage import (
    remove_user_email as storage_remove_user_email,
)
from storage import (
    reset_my_profile_view_count as storage_reset_my_profile_views,
)
from storage import (
    set_ui_pref as storage_set_ui_pref,
)
from storage import (
    set_user_email as storage_set_user_email,
)
from storage import (
    track_user as storage_track_user,
)
from telegram_bot_menu import clear_chat_bot_menu, set_special_bot_menu
from utils import (
    escape_markdown,
    format_user_info,
    get_app_version,
    get_available_models,
    get_current_model,
    set_current_model,
)

logger = logging.getLogger(__name__)


class BotHandlersLite(AITranslateMixin):
    """Simplified bot handler class with VLESS-Reality support."""

    def __init__(self, config: Config = None):
        """Initialize handlers."""
        self.config = config
        # (chat_id, message_id) -> Task; one /user card — one timer, reset on refresh
        self._user_card_ttl_tasks: dict[tuple[int, int], asyncio.Task] = {}

    async def _reply_export_file(
        self, message, content: str, filename: str, caption: str
    ):
        """Send an export as a file to avoid Telegram limits/MarkdownV2 issues."""
        buffer = BytesIO(content.encode("utf-8"))
        buffer.name = filename
        await message.reply_document(document=buffer, caption=caption)

    def _is_admin(self, user_id: int) -> bool:
        """Return True if the user is an administrator."""
        if not self.config:
            return False
        return self.config.is_admin(user_id)

    def _is_privileged(self, user_id: int) -> bool:
        """Admin or a user on the special list."""
        return self._is_admin(user_id) or storage_is_special_user(user_id)

    async def _ensure_personal_command_menu(self, context, user_id: int) -> None:
        """Repair Telegram command menu for users restored from users.json."""
        try:
            if self._is_admin(user_id):
                return
            if storage_is_special_user(user_id):
                await set_special_bot_menu(context.bot, user_id)
        except Exception as exc:
            logger.warning(
                "personal command menu refresh failed for %s: %s", user_id, exc
            )

    @staticmethod
    def _track_user(user) -> None:
        if user is None:
            return
        try:
            storage_track_user(
                user.id,
                username=getattr(user, "username", None),
                first_name=getattr(user, "first_name", None),
                last_name=getattr(user, "last_name", None),
            )
        except Exception as e:
            logger.warning(f"track_user failed for {getattr(user, 'id', '?')}: {e}")

    def _secret_reveal_allowed(self) -> bool:
        """Whether full secret reveal is allowed over remote channels."""
        return os.getenv("TELEGRAMHELPER_ALLOW_SECRET_REVEAL", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _mask_secret(self, value: str) -> str:
        """Return a safe masked representation of a secret."""
        if not value:
            return "***"
        if len(value) <= 12:
            return "***"
        return f"{value[:8]}...{value[-4:]}"

    @staticmethod
    def _plain_from_markdown_v2(text: str) -> str:
        """Best-effort fallback text when Telegram rejects MarkdownV2."""
        return (
            str(text)
            .replace("\\", "")
            .replace("*", "")
            .replace("_", "")
            .replace("`", "")
        )

    async def _reply_md2_safe(self, message, text: str, **kwargs):
        """Reply with MarkdownV2, falling back to plain text on parse errors."""
        try:
            return await message.reply_text(
                text, parse_mode=ParseMode.MARKDOWN_V2, **kwargs
            )
        except Exception as exc:
            logger.warning(
                "MarkdownV2 reply failed, falling back to plain text: %s", exc
            )
            return await message.reply_text(
                self._plain_from_markdown_v2(text), **kwargs
            )

    # Panel appearance themes (spec §7). Bot API cannot control real chat
    # colors — themes only change accents in the bot's own messages.
    # Theme names are synced with storage._UI_THEMES_ALLOWED.
    _UI_THEMES: dict = {
        "classic": {
            "label": "Classic",
            "brand": "✨",
            "profile": "ℹ️",
            "help": "❓",
            "diag": "🔎",
            "clear": "🧹",
            "vpn": "📱",
            "settings": "⚙️",
            "back": "◀️",
            "ok": "✅",
        },
        "minimal": {
            "label": "Minimal",
            "brand": "",
            "profile": "",
            "help": "",
            "diag": "",
            "clear": "",
            "vpn": "",
            "settings": "",
            "back": "←",
            "ok": "OK",
        },
        "neon": {
            "label": "Neon",
            "brand": "🟣",
            "profile": "🟢",
            "help": "💡",
            "diag": "⚡",
            "clear": "🌀",
            "vpn": "🛸",
            "settings": "🎛",
            "back": "◀️",
            "ok": "✅",
        },
    }

    def _theme_icons(self, user_id: int) -> dict:
        """User theme accent dictionary (fallback to classic)."""
        prefs = storage_get_ui_prefs(user_id)
        return self._UI_THEMES.get(prefs["theme"], self._UI_THEMES["classic"])

    @staticmethod
    def _btn(icons: dict, key: str, text: str) -> str:
        """Button label with theme accent; minimal theme has no emoji."""
        icon = icons.get(key, "")
        return f"{icon} {text}".strip()

    async def _menu_panel(self, message, text: str, keyboard, *, edit: bool):
        """Show/update a menu panel: MarkdownV2 with plain-text fallback.

        edit=True — edit_text (in-panel navigation), otherwise reply_text.
        """
        send = message.edit_text if edit else message.reply_text
        try:
            return await send(
                text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard
            )
        except Exception as exc:
            logger.warning("menu panel MD2 failed (%s) — fallback plain", exc)
            try:
                return await send(
                    self._plain_from_markdown_v2(text), reply_markup=keyboard
                )
            except Exception as exc2:
                logger.error("menu panel fallback failed: %s", exc2)
                return None

    def _main_menu_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        """Main panel buttons by role (spec §2)."""
        icons = self._theme_icons(user_id)
        if self._is_admin(user_id):
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🗂 /help panel", callback_data="menu:admin_help"
                        ),
                        InlineKeyboardButton(
                            self._btn(icons, "diag", "Full diag"),
                            callback_data="menu:diag",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "👥 Users", callback_data="menu:admin_users"
                        ),
                        InlineKeyboardButton(
                            "🌐 Internet via VPS", callback_data="menu:exit_node"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            self._btn(icons, "settings", "Settings"),
                            callback_data="menu:settings",
                        ),
                        InlineKeyboardButton(
                            "📦 Backup",
                            callback_data="menu:backup",
                        ),
                    ],
                ]
            )
        rows = []
        if storage_is_special_user(user_id):
            rows.append(
                [
                    InlineKeyboardButton(
                        self._btn(icons, "vpn", "My VPN profiles"),
                        callback_data="menu:my_profile",
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        "🌐 Internet via VPS",
                        callback_data="menu:exit_node",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    self._btn(icons, "profile", "My profile"),
                    callback_data="menu:info",
                ),
                InlineKeyboardButton(
                    self._btn(icons, "help", "Help"), callback_data="menu:help"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    self._btn(icons, "diag", "Diagnostics"),
                    callback_data="menu:diag",
                ),
                InlineKeyboardButton(
                    self._btn(icons, "clear", "Clear"),
                    callback_data="menu:clear",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    self._btn(icons, "settings", "Settings"),
                    callback_data="menu:settings",
                )
            ]
        )
        return InlineKeyboardMarkup(rows)

    def _user_help_panel_text(self, user_id: int) -> str:
        """/help panel text (and menu:back) for regular and special users."""
        prefs = storage_get_ui_prefs(user_id)
        icons = self._UI_THEMES.get(prefs["theme"], self._UI_THEMES["classic"])
        version_info = get_app_version()
        ver = self._escape_md2(version_info.get("version", "N/A"))
        app_name = self._escape_md2(version_info.get("name", "TelegramHelper"))
        brand = f"{icons['brand']} " if icons["brand"] else ""
        lines = [f"{brand}*{app_name}* v{ver}", ""]
        if not prefs["compact"]:
            lines += [
                "Buttons below run commands for you\\.",
                "The same actions are available as slash\\-commands in the Telegram menu\\.",
                "",
            ]
        if storage_is_special_user(user_id):
            lines.append(
                "_VPN\\-profiles are issued by admin; messages with URL/QR auto\\-delete\\._"
            )
        return "\n".join(lines).strip()

    def _settings_panel(self, user_id: int):
        """(text, keyboard) for the /settings screen (spec §7)."""
        prefs = storage_get_ui_prefs(user_id)
        icons = self._UI_THEMES.get(prefs["theme"], self._UI_THEMES["classic"])
        theme_label = self._escape_md2(self._UI_THEMES[prefs["theme"]]["label"])
        compact_label = "on" if prefs["compact"] else "off"
        s = f"{icons['settings']} " if icons["settings"] else ""
        lines = [
            f"{s}*Interface settings*",
            "",
            f"Theme: *{theme_label}*",
            f"Compact mode: *{compact_label}*",
        ]
        if not prefs["compact"]:
            lines += [
                "",
                "_Theme changes bot panel styling\\. Chat colors "
                "are set in the Telegram app and are not "
                "available to the bot\\._",
            ]
        rows = []
        for name, theme in self._UI_THEMES.items():
            mark = " ✓" if name == prefs["theme"] else ""
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{theme['label']}{mark}",
                        callback_data=f"menu:set_theme:{name}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    (
                        "Disable compact mode"
                        if prefs["compact"]
                        else "Enable compact mode"
                    ),
                    callback_data="menu:toggle_compact",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    self._btn(icons, "back", "Back"), callback_data="menu:back"
                )
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def _reply_vless_qr(self, message, name_or_uuid: str):
        """Send a QR code and import link for a VLESS client."""
        success, response, payload = vless_manager.build_client_qr_payload(name_or_uuid)
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"vless-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR for VLESS client {payload['name']}",
        )
        await message.reply_text(
            "📲 VLESS QR for client {name}\n\n"
            "UUID: {uuid}\n\n"
            "Import link:\n{link}\n\n"
            "FoXray: Import/Scan QR -> point the camera at the code or import the link directly.".format(
                name=payload["name"],
                uuid=payload["uuid"],
                link=payload["link"],
            )
        )
        return True

    async def _show_vless_qr_selection(self, message):
        """Show an inline menu to pick a client for QR."""
        clients = vless_manager.list_clients()
        if not clients:
            await message.reply_text(
                "❌ Client list is empty. First use /vless_add_client"
            )
            return

        keyboard = []
        for client in clients:
            client_name = client.get("name") or "client"
            client_uuid = client.get("uuid") or ""
            if not client_uuid:
                continue
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"📷 {client_name}",
                        callback_data=f"vless_export_qr_uuid:{client_uuid}",
                    )
                ]
            )

        if not keyboard:
            await message.reply_text("❌ Clients have no UUID for QR generation")
            return

        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "Select a client to show QR:", reply_markup=reply_markup
        )

    async def _reply_hy2_qr(self, message, name_or_password: str):
        """Send a QR code and URI for a Hysteria2 client."""
        success, response, payload = hysteria2_manager.build_client_qr_payload(
            name_or_password
        )
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"hy2-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR for Hysteria2 client {payload['name']}",
        )
        await message.reply_text(
            "⚡ Hysteria2 QR for client {name}\n\n"
            "Password: {password}\n\n"
            "Import URI:\n{uri}\n\n"
            "A supported client can import the profile via QR or directly from the hy2:// link.".format(
                name=payload["name"],
                password=payload["password"],
                uri=payload["uri"],
            )
        )
        return True

    async def _show_hy2_qr_selection(self, message):
        """Show an inline menu to pick a Hysteria2 client for QR."""
        clients = hysteria2_manager.list_clients()
        if not clients:
            await message.reply_text(
                "❌ Client list is empty. First use /hy2_add_client"
            )
            return

        keyboard = []
        for client in clients:
            client_name = client.get("name") or "client"
            client_password = client.get("password") or ""
            if not client_password:
                continue
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"📷 {client_name}",
                        callback_data=f"hy2_export_qr_pw:{client_password}",
                    )
                ]
            )

        if not keyboard:
            await message.reply_text("❌ Clients have no password for QR generation")
            return

        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "Select a Hysteria2 client to show QR:", reply_markup=reply_markup
        )

    async def _reply_mt_qr(self, message, name_or_secret: str):
        """Send QR codes and links for an MTProto client."""
        success, response, payload = mtproto_manager.build_client_qr_payload(
            name_or_secret
        )
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"mtproto-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR for MTProto client {payload['name']}",
        )
        await message.reply_text(
            "📡 MTProto QR for client {name}\n\n"
            "Mode: {mode}\n\n"
            "Secret: {secret}\n\n"
            "HTTPS link:\n{https_link}\n\n"
            "tg:// link:\n{tg_link}\n\n"
            "QR uses the HTTPS link so the phone camera opens Telegram more reliably.".format(
                name=payload["name"],
                mode=payload.get("secret_mode_label", "unknown"),
                secret=payload["secret"],
                https_link=payload["https_link"],
                tg_link=payload["tg_link"],
            )
        )
        return True

    async def _show_mt_qr_selection(self, message):
        """Show an inline menu to pick an MTProto client for QR."""
        clients = mtproto_manager.list_clients()
        if not clients:
            await message.reply_text(
                "❌ Client list is empty. First use /mt_add_client"
            )
            return

        keyboard = []
        for client in clients:
            client_name = client.get("name") or "client"
            client_secret = client.get("secret") or ""
            if not client_secret:
                continue
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"📷 {client_name}",
                        callback_data=f"mt_export_qr_name:{client_name}",
                    )
                ]
            )

        if not keyboard:
            await message.reply_text("❌ Clients have no secret for QR generation")
            return

        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "Select an MTProto client to show QR:", reply_markup=reply_markup
        )

    async def _reply_tuic_qr(self, message, name: str):
        """Send a QR code and URI for a TUIC client."""
        success, response, payload = tuic_manager.build_client_qr_payload(name)
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"tuic-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR for TUIC client {payload['name']}",
        )
        await message.reply_text(
            "🔷 TUIC QR for client {name}\n\n"
            "UUID: {uuid}\n"
            "Password: {password}\n\n"
            "URI:\n{uri}".format(
                name=payload["name"],
                uuid=payload["uuid"],
                password=payload["password"],
                uri=payload["uri"],
            )
        )
        return True

    async def _reply_anytls_qr(self, message, name: str):
        """Send a QR code and URI for an AnyTLS client."""
        success, response, payload = anytls_manager.build_client_qr_payload(name)
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"anytls-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR for AnyTLS client {payload['name']}",
        )
        await message.reply_text(
            "🔶 AnyTLS QR for client {name}\n\n"
            "Password: {password}\n\n"
            "URI:\n{uri}".format(
                name=payload["name"],
                password=payload["password"],
                uri=payload["uri"],
            )
        )
        return True

    async def _reply_xhttp_qr(self, message, name: str):
        """Send a QR code and URI for an XHTTP client."""
        success, response, payload = xhttp_manager.build_client_qr_payload(name)
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"xhttp-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR for XHTTP client {payload['name']}",
        )
        await message.reply_text(
            "🌐 XHTTP QR for client {name}\n\nUUID: {uuid}\n\nURI:\n{uri}".format(
                name=payload["name"],
                uuid=payload["uuid"],
                uri=payload["uri"],
            )
        )
        return True

    async def _reply_mieru_qr(self, message, name: str):
        """Send URI and QR for a Mieru client (per-user; plan §7)."""
        try:
            uri = mieru_manager.build_simple_uri(name)
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}")
            return False

        await message.reply_text(
            "🛰 Mieru URI for client {name}\n\n{uri}".format(name=name, uri=uri)
        )
        try:
            import qrcode  # type: ignore
        except ImportError:
            return True
        try:
            buf = BytesIO()
            qrcode.make(uri).save(buf, format="PNG")
            buf.seek(0)
            buf.name = f"mieru-{name}.png"
            await message.reply_photo(
                photo=buf,
                caption=f"QR for Mieru client {name}",
            )
        except Exception as exc:
            logger.warning("mieru QR render failed: %s", exc)
        return True

    _HELP_MENU_KEYBOARD = [
        [
            InlineKeyboardButton("🚀 Quick start", callback_data="help_roadmap"),
            InlineKeyboardButton("👥 Users", callback_data="help_users"),
        ],
        [
            InlineKeyboardButton("🧩 Protocols", callback_data="help_protocols"),
            InlineKeyboardButton("🔎 Diagnostics", callback_data="help_diag"),
        ],
        [
            InlineKeyboardButton("🔧 System and keys", callback_data="help_admin"),
            InlineKeyboardButton("💾 Backups", callback_data="help_backup"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="menu:settings"),
        ],
    ]

    _HELP_PROTOCOL_KEYBOARD = [
        [
            InlineKeyboardButton("🛡️ VLESS", callback_data="help_vless"),
            InlineKeyboardButton("⚡ Hysteria2", callback_data="help_hy2"),
        ],
        [
            InlineKeyboardButton("🌐 NaiveProxy", callback_data="help_naive"),
            InlineKeyboardButton("🕵️ Mieru", callback_data="help_mieru"),
        ],
        [
            InlineKeyboardButton("🔷 TUIC", callback_data="help_tuic"),
            InlineKeyboardButton("🔶 AnyTLS", callback_data="help_anytls"),
        ],
        [
            InlineKeyboardButton("🌐 XHTTP", callback_data="help_xhttp"),
            InlineKeyboardButton("📡 MTProto", callback_data="help_mt"),
        ],
        [
            InlineKeyboardButton("🛠 3x-ui", callback_data="help_xui"),
        ],
    ]

    _HELP_PROTOCOL_CALLBACKS = frozenset(
        {
            "help_protocols",
            "help_vless",
            "help_hy2",
            "help_tuic",
            "help_anytls",
            "help_xhttp",
            "help_naive",
            "help_mt",
            "help_mieru",
            "help_xui",
        }
    )

    async def _help_show_menu(self, message, *, edit: bool = True):
        """Show the main /help menu with inline buttons.

        Args:
            message: Telegram message object.
            edit: if True — edit_text (for callbacks), otherwise reply_text.
        """
        version_info = get_app_version()
        ver = self._escape_md2(version_info.get("version", "N/A"))
        app_name = self._escape_md2(version_info.get("name", "TelegramHelper"))
        text = (
            f"✨ *{app_name}* v{ver}\n\n"
            "*Admin navigation panel*\n\n"
            "The Telegram slash\\-menu stays complete, but day\\-to\\-day work is easier through the sections below\\.\n\n"
            "*Most used:*\n"
            "• `/user <id>` — user card with profile buttons\n"
            "• `/profiles <id>` — issued profiles for the user\n"
            "• `/provision <id>` — create bot\\-managed profiles\n"
            "• `/diag` — transport and port status\n\n"
            "_Sections contain short playbooks, not a long command dump\\._"
        )
        reply_markup = InlineKeyboardMarkup(self._HELP_MENU_KEYBOARD)
        if edit:
            await message.edit_text(
                text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
            )
        else:
            await message.reply_text(
                text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
            )

    def _help_section_keyboard(self, section: str) -> InlineKeyboardMarkup:
        """Navigation inside admin help; regular user /help is unchanged."""
        if section == "help_protocols":
            rows = [*self._HELP_PROTOCOL_KEYBOARD]
            rows.append(
                [
                    InlineKeyboardButton(
                        "◀️ Back to main menu", callback_data="help_back"
                    )
                ]
            )
            return InlineKeyboardMarkup(rows)
        if section in self._HELP_PROTOCOL_CALLBACKS:
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🧩 To protocols", callback_data="help_protocols"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "◀️ Back to main menu", callback_data="help_back"
                        )
                    ],
                ]
            )
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "◀️ Back to main menu", callback_data="help_back"
                    )
                ]
            ]
        )

    # === BASIC COMMANDS ===

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start — launch the bot and greet the user."""
        try:
            user = update.effective_user
            logger.info(f"User {user.id} ({user.username}) started the bot")
            self._track_user(user)
            await self._ensure_personal_command_menu(context, user.id)

            try:
                from dockhand_tunnel_hints import get_dockhand_ssh_params

                p = await asyncio.to_thread(
                    get_dockhand_ssh_params, resolve_public_ip=True
                )
                if p.host_is_placeholder:
                    vps_line = (
                        "🌐 *VPS address:* auto\\-detect unavailable "
                        "\\(set `/vless\\_set\\_server` or `DOCKHAND\\_SSH\\_HOST` in `.env`\\)"
                    )
                else:
                    vps_line = "🌐 *VPS address:* `" + escape_markdown(p.host) + "`"
            except Exception as ex:
                logger.debug("start_command: VPS host hint failed: %s", ex)
                vps_line = "🌐 *VPS address:* temporarily unavailable"

            if not self._is_admin(user.id):
                # Regular and special users: greeting + VPS address + role buttons.
                # ReplyKeyboardRemove is not needed: the inline keyboard lives in
                # the message and does not conflict with reply keyboards.
                prefs = storage_get_ui_prefs(user.id)
                icons = self._theme_icons(user.id)
                brand = f"{icons['brand']} " if icons["brand"] else ""
                parts = [
                    f"Hi, {escape_markdown(user.first_name or 'User')}\\!",
                    "",
                    f"{brand}*TelegramHelper* — a bot for personal tasks and notifications\\.",
                    "",
                    vps_line,
                ]
                if not prefs["compact"]:
                    parts += [
                        "",
                        "_Buttons below are the main actions\\. Full command "
                        "list is in the Telegram menu\\._",
                    ]
                await self._menu_panel(
                    update.message,
                    "\n".join(parts),
                    self._main_menu_keyboard(user.id),
                    edit=False,
                )
                return

            protocol_lines = self._build_protocol_status_lines(short=True)
            protocols_block = "\n".join(protocol_lines) if protocol_lines else ""

            welcome_message = f"""Hi, {escape_markdown(user.first_name or "User")}\\!

✨ *TelegramHelper* — a bot for API, keys, and proxy protocols

{vps_line}

*Transports on this VPS:*
{protocols_block}

Open `/help` to pick a section and see short command examples\\.
Full diagnostics: `/diag`\\."""

            await self._menu_panel(
                update.message,
                welcome_message,
                self._main_menu_keyboard(user.id),
                edit=False,
            )

        except Exception as e:
            logger.error(f"Error in start_command: {e}")
            await update.message.reply_text(
                "Hi! Use /help to see available commands."
            )

    # === Transport diagnostics ===

    def _build_protocol_status_lines(self, short: bool = True) -> list:
        """
        Build MarkdownV2 lines with the status of each protocol.

        short=True   — compact output for /start (one line per protocol).
        short=False  — expanded output for /diag (port, signal sources
                       and notes, no secrets).
        """
        lines: list = []
        try:
            snapshot = live_status.gather_full_snapshot()
            statuses = snapshot["protocols"]
            dockhand = snapshot["dockhand"]
            xui_panel = snapshot.get("xui_panel")
        except Exception as exc:
            logger.warning("_build_protocol_status_lines: gather failed: %s", exc)
            return ["⚠️ Failed to collect transport status\\."]

        esc = self._escape_md2

        for st in statuses:
            indicator = st.short_indicator()
            label = st.short_label()
            name = esc(st.display)
            if not st.implemented and label == "off":
                # Unimplemented protocols with no live signals get an explicit
                # "not implemented" label so they are not confused with a configured "off".
                label = "not implemented in the bot"
            if short:
                lines.append(f"{st.icon} *{name}:* {indicator} {esc(label)}")
            else:
                port_text = ""
                if st.port:
                    port_text = f" (port {st.port}/{st.transport.upper()})"
                detail_bits = []
                if st.flag_enabled is True:
                    detail_bits.append("JSON flag: on")
                elif st.flag_enabled is False:
                    detail_bits.append("JSON flag: off")
                if st.process_alive is True:
                    procs = ", ".join(sorted(set(st.process_names))) or "yes"
                    detail_bits.append(f"process: {procs}")
                elif st.process_alive is False:
                    detail_bits.append("process: not found")
                if st.port_listening is True:
                    detail_bits.append("port listening")
                elif st.port_listening is False:
                    detail_bits.append("port not listening")
                if not st.implemented:
                    detail_bits.append("server automation is not implemented in the bot")
                if st.notes:
                    detail_bits.extend(st.notes)
                detail = "; ".join(esc(b) for b in detail_bits)
                line = f"{st.icon} *{name}:* {indicator} {esc(label)}{esc(port_text)}"
                if detail:
                    line += f"\n   ↳ {detail}"
                lines.append(line)

        # Dockhand is a separate block (a panel, not a transport).
        di = dockhand.short_indicator()
        di_label = dockhand.short_label()
        if dockhand.notes and dockhand.live is None:
            # If diagnostics are unavailable, use a soft status.
            di_label = dockhand.notes[0]
        dockhand_name = esc(dockhand.display)
        if short:
            lines.append(f"{dockhand.icon} *{dockhand_name}:* {di} {esc(di_label)}")
        else:
            container = dockhand.container_status or {}
            extra_bits = []
            if container.get("found"):
                extra_bits.append(f"state: {container.get('state') or 'unknown'}")
                if container.get("health"):
                    extra_bits.append(f"health: {container.get('health')}")
            elif container.get("available") is False:
                extra_bits.append("docker.sock is not mounted")
            elif container.get("available") and not container.get("found"):
                extra_bits.append("container not found")
            extra = "; ".join(esc(b) for b in extra_bits)
            line = f"{dockhand.icon} *{dockhand_name}:* {di} {esc(di_label)}"
            if extra:
                line += f"\n   ↳ {extra}"
            lines.append(line)

        # 3x-ui is an external Xray control panel. Show a line only when
        # something is actually found (live process/container, or at least
        # an installed binary). On a "clean" VPS there is no line — so
        # /start and /diag are not cluttered with a useless message.
        if xui_panel is not None:
            xui_found = (
                xui_panel.process_alive is True
                or xui_panel.configured is True
                or (xui_panel.container_status or {}).get("found") is True
            )
            if xui_found:
                xi = xui_panel.short_indicator()
                xname = esc(xui_panel.display)
                if short:
                    lines.append(
                        f"{xui_panel.icon} *{xname}:* {xi} "
                        + esc(xui_panel.short_label())
                    )
                else:
                    detail_bits: list[str] = []
                    if xui_panel.process_alive is True:
                        procs = ", ".join(sorted(set(xui_panel.process_names))) or "yes"
                        detail_bits.append(f"process: {procs}")
                    elif xui_panel.process_alive is False:
                        detail_bits.append("process: not running")
                    if xui_panel.notes:
                        detail_bits.extend(xui_panel.notes)
                    detail = "; ".join(esc(b) for b in detail_bits)
                    line = f"{xui_panel.icon} *{xname}:* {xi} " + esc(
                        xui_panel.short_label()
                    )
                    if detail:
                        line += f"\n   ↳ {detail}"
                    line += "\n   ↳ " + esc(
                        "3x-ui is an Xray/VLESS control panel, "
                        "not a second VLESS port; see clients and inbound in /vless_list_clients"
                    )
                    lines.append(line)

        return lines

    async def diag_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        `/diag` command.

        Admin: extended diagnostics (transports, Dockhand, 3x-ui,
        listening ports, notes).

        Regular and special users: the same compact summary as `/start`,
        without ports and without the operator legend.
        """
        try:
            user = update.effective_user
            msg = update.effective_message
            if msg is None:
                logger.warning("/diag: effective_message is None")
                return
            self._track_user(user)
            logger.info("User %s requested /diag", user.id if user else "?")

            if not self._is_admin(user.id):
                try:
                    lines = self._build_protocol_status_lines(short=True)
                except Exception as exc:
                    logger.error("diag_command (user): failed to build lines: %s", exc)
                    await msg.reply_text(
                        "❌ Failed to collect diagnostics. See bot logs.",
                    )
                    return
                message = (
                    "🔎 *Brief diagnostics*\n\n"
                    + "\n".join(lines)
                    + "\n\n_Full report \\(host ports, process details, notes\\) "
                    "is admin\\-only\\._"
                )
                if len(message) > 3800:
                    message = message[:3800] + "\n…\\(truncated\\)"
                try:
                    await msg.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception as md_exc:
                    logger.warning(
                        "diag_command (user) MD2 failed (%s) — fallback plain text",
                        md_exc,
                    )
                    plain = (
                        message.replace("\\", "")
                        .replace("*", "")
                        .replace("_", "")
                        .replace("`", "")
                    )
                    await msg.reply_text(plain)
                return

            esc = self._escape_md2
            try:
                lines = self._build_protocol_status_lines(short=False)
            except Exception as exc:
                logger.error("diag_command: failed to build lines: %s", exc)
                await msg.reply_text(
                    "❌ Failed to collect diagnostics. See bot logs.",
                )
                return

            # Listening-port summary — useful for spotting conflicts quickly.
            try:
                host_ports = live_status.host_listen_ports()
                tcp_sample = sorted(host_ports.get("tcp", set()))
                udp_sample = sorted(host_ports.get("udp", set()))
            except Exception:
                tcp_sample, udp_sample = [], []

            def _fmt_ports(ports):
                if not ports:
                    return "no data"
                shown = ports[:25]
                more = len(ports) - len(shown)
                base = ", ".join(str(p) for p in shown)
                return base + (f" (+{more})" if more > 0 else "")

            tcp_line = esc(_fmt_ports(tcp_sample))
            udp_line = esc(_fmt_ports(udp_sample))

            diag_legend = (
                "\n\n*Do not confuse three different components:*\n"
                "• *VLESS\\-Reality \\(bot → host Xray\\)* — the transport the bot manages "
                "\\(`xray` on the host, `/usr/local/etc/xray`\\); this is *not* a web panel\\.\n"
                "• *Dockhand \\(Docker\\)* — Streamlit for bot logs and diagnostics; "
                "not a VPN and not an Xray client panel\\.\n"
                "• *3x\\-ui* — a third\\-party Xray web panel; in its block the "
                "*3x\\-ui deploy* line shows *native on host* or *Docker*\\.\n"
            )

            footer = (
                "\n\n*Listening ports on the host:*\n"
                f"• TCP: {tcp_line}\n"
                f"• UDP: {udp_line}\n\n"
                "_🟢 — actually running; 🔴 — off/not started;_\n"
                "_⚪ — could not check \\(no permissions/no data\\)\\._"
            )

            message = (
                "🔎 *Transport diagnostics*\n\n"
                + "\n\n".join(lines)
                + diag_legend
                + footer
            )
            # Telegram limit ~4096 chars; truncate if needed.
            if len(message) > 3800:
                message = message[:3800] + "\n…\\(truncated\\)"

            try:
                await msg.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception as md_exc:
                # MarkdownV2 is strict: one unescaped character in
                # any live_status line fails the whole reply. Retry
                # as plain text so admin is not left without diagnostics.
                logger.warning(
                    "diag_command MD2 failed (%s) — fallback plain text",
                    md_exc,
                )
                # Strip MD2 escaping (\\X → X) for readability.
                plain = (
                    message.replace("\\", "")
                    .replace("*", "")
                    .replace("_", "")
                    .replace("`", "")
                )
                await msg.reply_text(plain)
        except Exception as e:
            logger.error(f"Error in diag_command: {e}")
            await update.message.reply_text("Failed to collect diagnostics.")

    # === Help: section texts (class-level) ===

    _HELP_SECTIONS = {
        "help_main": (
            "📚 *Main commands*\n\n"
            "• `/start` — greeting and status\n"
            "• `/help` — this menu\n"
            "• `/info` — user profile\n"
            "• `/diag` — brief transport status; full report is admin\\-only\n"
            "• `/clear` — clear the chat"
        ),
        "help_protocols": (
            "🧩 *Protocols*\n\n"
            "Pick a transport below\\. Each section has a short launch playbook, "
            "profile export, and diagnostic commands\\.\n\n"
            "*Working flow for a user:*\n"
            "• `/provision <id>` — create bot\\-managed profiles\n"
            "• `/profiles <id>` — view and issue links/QR\n"
            "• `/user <id>` — card with create/delete/rotate buttons\n\n"
            "*Quick check of all transports:* `/diag`"
        ),
        "help_roadmap": (
            "🚀 *Quick start / server from scratch*\n\n"
            "Short path for a clean VPS: bring up the primary profile first, then add fallback protocols\\.\n\n"
            "*1\\. VLESS\\-Reality*\n"
            "`/vless\\_sync` → `/vless\\_add\\_client phone` → `/vless\\_qr phone`\n\n"
            "*2\\. Hysteria2*\n"
            "`/hy2\\_install` → `/hy2\\_set\\_server IP` → `/hy2\\_gen\\_all` → `/hy2\\_apply` → `/hy2\\_on` → `/hy2\\_start` → `/hy2\\_add\\_client phone` → `/hy2\\_qr phone`\n\n"
            "*3\\. TUIC / AnyTLS / XHTTP*\n"
            "`/PROTO\\_set\\_server IP` → `/PROTO\\_gen\\_all` → `/PROTO\\_apply` → `/PROTO\\_start` → `/PROTO\\_qr phone`\n\n"
            "*4\\. NaiveProxy / MTProto*\n"
            "`/naive\\_install` → `/naive\\_gen\\_creds` → `/naive\\_uri`\n"
            "`/mt\\_install` → `/mt\\_gen\\_all` → `/mt\\_qr phone`\n\n"
            "*5\\. Mieru*\n"
            "`/mieru\\_install` → `/mieru\\_set\\_server IP` → `/mieru\\_set\\_port 29999 tcp` "
            "→ `/mieru\\_add\\_client phone` → `/mieru\\_apply` → `/mieru\\_export phone`\n\n"
            "Check: `/PROTO\\_status` and `/PROTO\\_logs`"
        ),
        "help_admin": (
            "🔧 *System and keys*\n\n"
            "*Useful daily:*\n"
            "• `/info` — profile and Telegram ID\n"
            "• `/ver` — version, VPS address, and full VLESS\\-Reality summary\n"
            "• `/dockhand` — Dockhand panel access\n"
            "• `/headscale` — server Tailscale IP\n"
            "• `/diag` — transport diagnostics\n\n"
            "*Keys:*\n"
            "• `/api` and `/encryption\\_key` — show a mask\n"
            "• `/gen\\_api\\_key` / `/del\\_api\\_key` — API keys\n"
            "• `/gen\\_encryption\\_key` / `/del\\_encryption\\_key` — encryption keys\n"
            "• `/gen\\_chacha\\_key` / `/gen\\_pqc\\_key` — extra keys\n\n"
            "*AI:*\n"
            "• `/ai\\_provider openai` — choose a provider\n"
            "• `/ch\\_model` — choose a model"
        ),
        "help_backup": (
            "💾 *Backups*\n\n"
            "Offsite backup runs via rclone and is useful before updates, "
            "a VPS move, or large config changes\\.\n\n"
            "*Commands:*\n"
            "• `/rclone` — what it is and how to enable backup\n"
            "• `/backup\\_status` — rclone status\n"
            "• `/backup\\_test` — test the remote\n"
            "• `/backup\\_now` — create a backup\n"
            "• `/backup\\_list` — recent archives\n\n"
            "Before manual edits to `*_config\\.json`, run `/backup\\_now`\\."
        ),
        "help_diag": (
            "🔎 *Diagnostics*\n\n"
            "*Main:*\n"
            "• `/diag` — transports, processes, and ports summary\n"
            "• `/ver` — version, VPS address, and VLESS summary\n"
            "• `/dockhand` — SSH tunnel to the logs panel\n\n"
            "*Per protocol:*\n"
            "• `/PROTO\\_status` — status of a specific transport\n"
            "• `/PROTO\\_logs 80` — recent logs where supported\n"
            "• `/vless\\_test` — VLESS port check\n\n"
            "If it is unclear who took port `443`, start with `/diag`, then the matching Protocols section\\."
        ),
        "help_users": (
            "👥 *Users*\n\n"
            "*Main working scenario:*\n"
            "• `/user 12345` — user card with profile buttons\n"
            "• `/profiles 12345` — show issued profiles\n"
            "• `/provision 12345` — create bot\\-managed clients\n"
            "• `/email\\_profile 12345` — send profiles by email\n\n"
            "*Lists and roles:*\n"
            "• `/list\\_users` — all known users\n"
            "• `/users\\_log` — first/last seen journal\n"
            "• `/special\\_add 12345` — add to special\n"
            "• `/special\\_remove 12345` — remove from special\n\n"
            "*User fields:*\n"
            "• `/setemail 12345 user@example\\.com` — email for profiles\n"
            "• `/setcity 12345 Moscow` — user city\n"
            "• `/setgreeting 12345 Hello` — personal greeting\n\n"
            "Tip: an admin can just send a numeric TG ID — the bot opens the card\\."
        ),
        "help_vless": (
            "🛡️ *VLESS\\-Reality*\n\n"
            "Primary profile: disguises traffic as ordinary HTTPS\\.\n\n"
            "*Quick start \\(legacy host\\-Xray\\):*\n"
            "`/vless\\_set\\_server IP` → `/vless\\_sync` → `/vless\\_on` → `/provision <id>`\n"
            "Direct path \\(no 3x\\-ui\\): `/vless\\_add\\_client phone` → `/vless\\_qr phone`\n\n"
            "⚠️ `/vless\\_on` is required: without it `/provision` will not enable VLESS\\. "
            "When 3x\\-ui is active the panel manages clients — `/vless\\_add\\_client` "
            "redirects to `/provision`\\.\n\n"
            "*Commands:*\n"
            "• `/vless\\_status` — status\n"
            "• `/vless\\_config` — config\n"
            "• `/vless\\_gen\\_keys` — Reality keys\n"
            "• `/vless\\_set\\_port 443` — port\n"
            "• `/vless\\_on` / `/vless\\_off` — enable or disable\n"
            "• `/vless\\_test` — port check\n"
            "• `/vless\\_export` — export\n\n"
            "*Clients:*\n"
            "`/vless\\_list\\_clients` — VLESS client list\n"
            "`/vless\\_add\\_client phone` → `/vless\\_qr phone`\n"
            "`/vless\\_del\\_client phone` — delete a client\n\n"
            "For a user by TG ID prefer the unified flow: "
            "`/provision <id>` → `/profiles <id>`\\.\n\n"
            "After changing SNI/fingerprint/short\\_id/Reality keys, re\\-issue URI/QR: "
            "`/profiles <id>`, `/my\\_profile`, or `/vless\\_export`\\.\n\n"
            "*Change SNI \\(camouflage\\):*\n"
            "`/vless\\_set\\_sni` with no domain — picker buttons \\(current marked ✅\\)\\. "
            "One tap changes SNI, writes the config, and offers an Xray restart\\. "
            "Mobile operators often block `www\\.microsoft\\.com` — then use `yahoo\\.com`\\.\n\n"
            "*If it does not connect:*\n"
            "`/xray\\_status`, `/vless\\_test`, then firewall: `ufw allow 443/tcp`"
        ),
        "help_hy2": (
            "⚡ *Hysteria2*\n\n"
            "Fast UDP/QUIC profile, good as a fallback channel\\.\n\n"
            "*Quick start \\(in order\\):*\n"
            "1\\. `/hy2\\_install`\n"
            "2\\. `/hy2\\_set\\_server IP`\n"
            "3\\. `/hy2\\_gen\\_all` — password \\+ certificate \\+ IP\n"
            "4\\. `/hy2\\_apply` — write config to the server\n"
            "5\\. `/hy2\\_on` — mark the profile active \\(for `/provision`\\)\n"
            "6\\. `/hy2\\_start` — start the service\n"
            "7\\. `/hy2\\_add\\_client phone` → `/hy2\\_qr phone` — issue QR\n\n"
            "Check: `/hy2\\_status` shows 🟢 profile \\+ 🟢 service and hints the next step\\.\n\n"
            "*Commands:*\n"
            "• `/hy2\\_status` / `/hy2\\_config` — state\n"
            "• `/hy2\\_on` / `/hy2\\_off` — activate / deactivate the profile\n"
            "• `/hy2\\_set\\_port 8443` — UDP port\n"
            "• `/hy2\\_set\\_obfs salamander pass` — obfuscation\n"
            "• `/hy2\\_set\\_speed 0 0` — auto speed\n"
            "• `/hy2\\_set\\_quic\\_safe 1` — Windows compatibility\n"
            "• `/hy2\\_logs` — diagnostics\n\n"
            "After changing SNI/obfs/QUIC/port: `/hy2\\_apply`, then re\\-issue URI/QR via "
            "`/profiles <id>`, `/my\\_profile`, or `/hy2\\_export`\\.\n\n"
            "Firewall: `ufw allow 8443/udp`"
        ),
        "help_tuic": (
            "🔷 *TUIC*\n\n"
            "Lightweight QUIC profile with a TLS certificate and client QR\\.\n\n"
            "*Quick start:*\n"
            "`/tuic\\_set\\_server IP` → `/tuic\\_gen\\_all` → `/tuic\\_apply` → `/tuic\\_start` → `/tuic\\_qr phone`\n\n"
            "*Commands:*\n"
            "• `/tuic\\_status` / `/tuic\\_config` — state\n"
            "• `/tuic\\_set\\_port 8444` — UDP port\n"
            "• `/tuic\\_set\\_cc bbr` — congestion control\n"
            "• `/tuic\\_add phone` / `/tuic\\_list` — clients\n"
            "• `/tuic\\_logs` / `/tuic\\_export` — logs and export\n\n"
            "Firewall: `ufw allow 8444/udp`"
        ),
        "help_anytls": (
            "🔶 *AnyTLS*\n\n"
            "TCP profile with TLS, simple for clients and diagnostics\\.\n\n"
            "*Quick start:*\n"
            "`/anytls\\_set\\_server IP` → `/anytls\\_gen\\_all` → `/anytls\\_apply` → `/anytls\\_start` → `/anytls\\_qr phone`\n\n"
            "*Commands:*\n"
            "• `/anytls\\_status` / `/anytls\\_config` — state\n"
            "• `/anytls\\_set\\_port 8445` — TCP port\n"
            "• `/anytls\\_gen\\_cert` — certificate\n"
            "• `/anytls\\_add phone` / `/anytls\\_list` — clients\n"
            "• `/anytls\\_logs` / `/anytls\\_export` — logs and export\n\n"
            "Firewall: `ufw allow 8445/tcp`"
        ),
        "help_xhttp": (
            "🌐 *XHTTP \\(VLESS\\+XHTTP\\)*\n\n"
            "VLESS over HTTP transport: useful on unusual networks\\.\n\n"
            "*Quick start:*\n"
            "`/xhttp\\_set\\_server IP` → `/xhttp\\_gen\\_all` → `/xhttp\\_apply` → `/xhttp\\_start` → `/xhttp\\_qr phone`\n\n"
            "*Commands:*\n"
            "• `/xhttp\\_status` / `/xhttp\\_config` — state\n"
            "• `/xhttp\\_set\\_path /tg` — path\n"
            "• `/xhttp\\_set\\_host example.com` — host\n"
            "• `/xhttp\\_set\\_mode auto` — mode\n"
            "• `/xhttp\\_logs` / `/xhttp\\_export` — logs and export\n\n"
            "If the path is taken: `/xhttp\\_set\\_path /new`"
        ),
        "help_naive": (
            "🌐 *NaiveProxy*\n\n"
            "HTTPS proxy via Caddy; needs a domain with correct DNS\\.\n\n"
            "*Quick start:*\n"
            "`/naive\\_install` → `/naive\\_set\\_domain example.com` → `/naive\\_gen\\_creds` → `/naive\\_apply` → `/naive\\_uri`\n\n"
            "*Commands:*\n"
            "• `/naive\\_status` / `/naive\\_config` — state\n"
            "• `/naive\\_set\\_port 443` — HTTPS port\n"
            "• `/naive\\_set\\_user user` — login\n"
            "• `/naive\\_set\\_password pass` — password\n"
            "• `/naive\\_set\\_dpi scheme https` / `padding on` / `probe\\_resistance on` — DPI parameters\n"
            "• `/naive\\_export` — export\n\n"
            "After client DPI parameters, re\\-issue the profile via `/naive\\_export`\\. "
            "After server parameters, run `/naive\\_apply` first, then `/naive\\_export`\\.\n\n"
            "Verify the domain DNS before starting\\."
        ),
        "help_mt": (
            "📡 *MTProto Proxy*\n\n"
            "Native Telegram proxy with a fake\\-TLS secret\\.\n\n"
            "*Quick start:*\n"
            "`/mt\\_install` → `/mt\\_set\\_server IP` → `/mt\\_gen\\_all` → `/mt\\_apply` → `/mt\\_start` → `/mt\\_qr phone`\n\n"
            "*Commands:*\n"
            "• `/mt\\_status` / `/mt\\_config` — state\n"
            "• `/mt\\_set\\_mode ee\\_split` — secret mode\n"
            "• `/mt\\_set\\_domain www.microsoft.com` — fake\\-TLS domain\n"
            "• `/mt\\_set\\_workers 4` — workers\n"
            "• `/mt\\_logs` / `/mt\\_export` — logs and export\n\n"
            "Firewall: `ufw allow 8443/tcp`"
        ),
        "help_mieru": (
            "🕵️ *Mieru*\n\n"
            "Fallback TCP/UDP transport with no domain or TLS certificate\\. "
            "Useful when VLESS/Hysteria2/NaiveProxy are unstable on the network, "
            "and for DPI experiments with port/MTU/multiplexing/handshake\\.\n\n"
            "*Quickstart \\(admin\\):*\n"
            "`/mieru\\_install`\n"
            "`/mieru\\_set\\_server IP`\n"
            "`/mieru\\_set\\_port 29999 tcp`\n"
            "`/mieru\\_add\\_client phone`\n"
            "`/mieru\\_apply`\n"
            "`/mieru\\_start`\n"
            "`/mieru\\_export phone`\n\n"
            "*Fine\\-tuning:* `/mieru\\_set\\_dpi <param> <value>` — "
            "protocol/port/port\\_range/mtu/multiplexing/handshake/socks5\\_port/logging\\.\n"
            "*Logs and state:* `/mieru\\_status`, `/mieru\\_logs \\[N\\]`\\.\n\n"
            "⚠️ After changing port/MTU/multiplexing/handshake, re\\-issue "
            "URI/QR/export via `/mieru\\_export <name>`\\.\n"
            "ℹ️ Details: `MIERU\\_GUIDE\\.md`\\."
        ),
        "help_xui": (
            "🛠 *3x\\-ui integration \\(optional\\)*\n\n"
            "A separate mode for a VPS where the `3x\\-ui` panel is actually "
            "installed and it is what manages Xray/VLESS\\-Reality\\.\n\n"
            "If `/vless\\_list\\_clients` says `legacy Xray`, VLESS on this "
            "server runs *without the panel* via `xray.service` and "
            "`/usr/local/etc/xray/config.json`\\. In that mode 3x\\-ui buttons "
            "do not manage current VLESS clients — use "
            "`/vless\\_status`, `/vless\\_sync`, `/vless\\_qr`, "
            "`/vless\\_export`\\.\n\n"
            "If the panel exists \\(locally or via Headscale/Tailscale mesh\\), "
            "the bot can call its REST API and create/delete clients "
            "in the selected inbound\\.\n\n"
            "*Setup:*\n"
            "• `/xui\\_setup` — step\\-by\\-step URL → login → password → "
            "inbound picker\\.\n"
            "  ⚠️ The password message is deleted IMMEDIATELY after input\\.\n"
            "  The password is stored encrypted only \\(AES\\-256\\-GCM "
            "with the key from `ENCRYPTION\\_KEY`\\)\\.\n"
            "• `/xui\\_status` — state, masked login, last\\-seen "
            "panel connectivity\\.\n"
            "• `/xui\\_list` — inbound list\\.\n"
            "• `/xui\\_set\\_inbound <id>` — set the default inbound\\.\n"
            "• `/xui\\_enable` / `/xui\\_disable` — enable/disable "
            "integration without deleting credentials\\.\n"
            "• `/xui\\_clear YES` — wipe credentials completely\\.\n"
            "• `/xui\\_cancel` — exit the `/xui\\_setup` wizard\\.\n\n"
            "*On the `/user <id>` card:*\n"
            "If integration is configured and the user is `admin`/`special`, "
            "a “VLESS via 3x\\-ui” block appears with buttons "
            "*➕ To 3x\\-ui*, *❌ From 3x\\-ui*, *📲 QR \\(3x\\-ui\\)*\\. The "
            "client name in the panel equals the bot profile name \\(`Vless82\\.\\.09`\\), "
            "so one TG ID is enough for CRUD\\.\n\n"
            "*How to tell which mode is active:*\n"
            "• `/xui\\_status` — whether REST integration is configured\\.\n"
            "• `/vless\\_list\\_clients` — shows either `3x\\-ui inbound` "
            "or `legacy Xray`\\.\n\n"
            "This does not overlap with legacy Xray in `/usr/local/etc/xray`: "
            "3x\\-ui and `xray.service` are different sources of truth\\."
        ),
    }

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help — main help menu with inline protocol buttons."""
        try:
            user = update.effective_user
            logger.info(f"User {user.id} requested help")
            self._track_user(user)

            if self._is_admin(user.id):
                await self._help_show_menu(update.message, edit=False)
            else:
                await self._menu_panel(
                    update.effective_message,
                    self._user_help_panel_text(user.id),
                    self._main_menu_keyboard(user.id),
                    edit=False,
                )

        except Exception as e:
            logger.error(f"Error in help_command: {e}")
            await update.message.reply_text("Failed to display help.")

    async def info_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /info — user information (available to everyone)."""
        try:
            user = update.effective_user
            chat = update.effective_chat
            logger.info(f"User {user.id} requested info")
            self._track_user(user)
            await self._ensure_personal_command_menu(context, user.id)

            info_message = format_user_info(user, chat)

            # Add role line
            if self._is_admin(user.id):
                role = "Admin"
            elif storage_is_special_user(user.id):
                role = "Special"
            else:
                role = "User"
            info_message += f"\n👮 *Type:* {role}"

            await self._reply_md2_safe(update.effective_message, info_message)

        except Exception as e:
            logger.error(f"Error in info_command: {e}")
            await update.effective_message.reply_text("Could not get information.")

    async def clear_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /clear — visual chat clearing."""
        try:
            user = update.effective_user
            message = update.effective_message
            if message is None:
                return

            if context.args:
                await message.reply_text(
                    "Telegram does not let the bot reliably delete arbitrary old "
                    "messages by count. Just use /clear."
                )
                return

            logger.info(f"User {user.id} requested visual chat clearing")

            clear_message = (
                "🧹 *Chat cleared* 🧹\n\n"
                "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
                "History above this mark is visually separated\\."
            )

            await message.reply_text(clear_message, parse_mode=ParseMode.MARKDOWN_V2)

        except Exception as e:
            logger.error(f"Error in clear_chat: {e}")
            if update.effective_message:
                await update.effective_message.reply_text("Could not clear the chat.")

    async def settings_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/settings command — personal bot panel appearance settings."""
        try:
            user = update.effective_user
            self._track_user(user)
            text, kb = self._settings_panel(user.id)
            await self._menu_panel(update.effective_message, text, kb, edit=False)
        except Exception as e:
            logger.error(f"Error in settings_command: {e}")
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Could not open settings."
                )

    # === ADMIN COMMANDS — SYSTEM AND INFO ===

    async def version_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/ver command — app version (everyone); VPS address (everyone); full VLESS — admin only."""
        msg = update.effective_message
        if msg is None:
            logger.warning("/ver: effective_message is None")
            return
        try:
            user = update.effective_user
            logger.info(f"User {user.id} requested version info")

            def he(x) -> str:
                """HTML-escape for Telegram HTML parse mode."""
                if x is None:
                    return ""
                return html.escape(str(x), quote=False)

            version_info = get_app_version()
            ver_disp = str(version_info.get("version", "N/A"))
            name_disp = he(version_info.get("name") or "TelegramHelper")
            desc_raw = version_info.get("description")
            desc_disp = he(
                str(desc_raw).strip()
                if (desc_raw is not None and str(desc_raw).strip())
                else "N/A"
            )

            # HTML: do not mix with MarkdownV2 (in MDV2 `=` and escaping inside `code` broke parsing).
            version_message = f"""📋 <b>Version information</b>

🔖 Version: <code>{he(ver_disp)}</code>
📦 Name: {name_disp}
📝 Description: {desc_disp}"""

            # VPS address — everyone (same as /start).
            try:
                from dockhand_tunnel_hints import get_dockhand_ssh_params

                p = await asyncio.to_thread(
                    get_dockhand_ssh_params, resolve_public_ip=True
                )
                if p.host_is_placeholder:
                    version_message += (
                        "\n\n🌐 <b>VPS address:</b> auto-detect unavailable "
                        "(set <code>/vless_set_server</code> or "
                        "<code>DOCKHAND_SSH_HOST</code> in <code>.env</code>)."
                    )
                else:
                    version_message += (
                        f"\n\n🌐 <b>VPS address:</b> <code>{he(p.host)}</code>"
                    )
            except Exception as ex:
                logger.debug("version_command: VPS host hint failed: %s", ex)
                version_message += "\n\n🌐 <b>VPS address:</b> temporarily unavailable"

            if not self._is_admin(user.id):
                version_message += (
                    "\n\n<i>The detailed VLESS-Reality block and server config readiness "
                    "are in the admin «/ver» reply.</i>"
                )
                await msg.reply_text(
                    version_message,
                    parse_mode=ParseMode.HTML,
                )
                return

            # Admin only: extended VLESS summary (previously admin/special).
            card = vless_manager.get_vless_version_card_fields()
            vless_status = card["status"]
            server_raw = (card.get("server") or "").strip()
            public_hint = (card.get("public_hint") or "").strip()
            missing_keys = card.get("missing_keys") or []

            if server_raw:
                server_block = (
                    f"Server (VLESS, client address): <code>{he(server_raw)}</code>"
                )
            else:
                server_block = (
                    "Server (VLESS): <b>not set</b> — config has no public IP or domain "
                    "for clients to connect via Reality.\n"
                    "Set it with: <code>/vless_set_server</code> or <code>/vless_sync</code>."
                )
                if public_hint:
                    server_block += (
                        f"\nThis VPS has a known address from <code>.env</code>/environment "
                        f"<code>{he(public_hint)}</code> "
                        f"(often the same IP — you can set it as the VLESS server).\n"
                        f"Example: <code>/vless_set_server {he(public_hint)}</code>"
                    )

            gaps_ru = {
                "server": "public server address",
                "uuid": "client UUID (config root or a clients entry)",
                "public_key": "Reality keys",
                "short_id": "short id",
            }
            cfg_line = ""
            if missing_keys:
                labels = [gaps_ru[k] for k in missing_keys if k in gaps_ru]
                if labels:
                    cfg_line = (
                        "\nReality config in <code>vless_config.json</code> is missing: "
                        + he(", ".join(labels))
                        + "."
                    )

            runtime_line = ""
            try:
                snapshot = live_status.gather_full_snapshot()
                vr = next(
                    (
                        s
                        for s in snapshot.get("protocols", [])
                        if s.key == "vless_reality"
                    ),
                    None,
                )
                if (
                    vr is not None
                    and vr.process_alive is True
                    and vr.port_listening is True
                ):
                    runtime_line = (
                        "\n<b>On the host:</b> the Xray process is listening on the port (see <code>/diag</code>).\n"
                        "<b>Below:</b> flags from <code>vless_config.json</code> "
                        "(the enabled field in JSON and completeness of fields).\n"
                    )
                elif vr is not None and vr.process_alive is True:
                    runtime_line = (
                        "\n<b>On the host:</b> Xray process found; see the port in <code>/diag</code>.\n"
                        "<b>Below:</b> <code>vless_config.json</code> "
                        "(do not confuse with the binary running on disk).\n"
                    )
            except Exception:
                runtime_line = ""

            vless_stat_line = (
                "🟢 On (JSON: enabled=true)"
                if vless_status["enabled"]
                else "🔴 Off (JSON: enabled=false; the Xray process on the VPS may still be running separately)"
            )
            version_message += f"""

🛡️ <b>VLESS-Reality</b>:{runtime_line}Status: {vless_stat_line}
Configured: {"✅ Yes" if vless_status["configured"] else "❌ No"}
{server_block}{cfg_line}"""

            await msg.reply_text(
                version_message,
                parse_mode=ParseMode.HTML,
            )

        except Exception as e:
            logger.exception("Error in version_command: %s", e)
            try:
                await msg.reply_text("Failed to get version information.")
            except Exception:
                pass

    async def dockhand_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/dockhand command — SSH tunnel hint to Dockhand (admin/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ This command is for admin or special users only."
                )
                return
            self._track_user(user)
            logger.info(f"Privileged user {user.id} requested /dockhand")

            from dockhand_tunnel_hints import (
                build_ssh_tunnel_command,
                get_dockhand_ssh_params,
            )

            params = get_dockhand_ssh_params()
            cmd = build_ssh_tunnel_command(params, background=False)
            cmd_bg = build_ssh_tunnel_command(params, background=True)

            await update.message.reply_text(
                "📋 Copy into a terminal on your PC (PowerShell, cmd, or Terminal):\n\n"
                f"{cmd}"
            )

            notes_tail = ""
            if params.notes:
                notes_tail = "\n\n⚠️ " + params.notes[0]
            placeholder_warn = ""
            if params.host_is_placeholder:
                placeholder_warn = (
                    "\n\n⚠️ The command above still has the YOUR_SERVER_IP placeholder — "
                    "set DOCKHAND_SSH_HOST in .env next to compose or configure /vless_set_server."
                )

            body = (
                "Dockhand is a diagnostics panel on the server; port 8501 listens on 127.0.0.1 on the VPS only "
                "(see DOCKHAND_GUIDE.md in the repo).\n\n"
                "Windows: built-in OpenSSH (Windows 10/11) — the same ssh in cmd or PowerShell.\n"
                "macOS / Linux: a regular Terminal — the same commands.\n\n"
                "After the tunnel is up, open this on the same computer in a browser:\n"
                "http://localhost:8501\n\n"
                "Background tunnel (no interactive session, convenient on macOS/Linux):\n"
                f"{cmd_bg}\n\n"
                "Stop the background process (macOS/Linux): "
                'pkill -f "ssh.*127.0.0.1:8501" or find the PID via ps aux | grep ssh. '
                "On Windows, background use is usually a separate window or WSL.\n\n"
                f"Substituted: {params.user}@{params.host}, SSH port {params.port}."
                f"{notes_tail}{placeholder_warn}"
            )
            await update.message.reply_text(
                body,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Error in dockhand_command: {e}")
            await update.message.reply_text(
                "Failed to build the Dockhand hint."
            )









    async def backup_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/backup_status command — rclone backup status (admin/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ This command is for admin or special users only."
                )
                return
            self._track_user(user)
            await update.message.reply_text(rclone_manager.format_status())
        except Exception as e:
            logger.error(f"Error in backup_status: {e}")
            await update.message.reply_text("Failed to check backup status.")

    async def rclone_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Short /rclone help with basic offsite backup setup steps."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ This command is for admin or special users only."
                )
                return

            self._track_user(user)
            await update.message.reply_text(
                "📦 Rclone backup (short)\n\n"
                "If offsite backup is not set up yet, start here:\n"
                "1) Prepare rclone config on the server.\n"
                "2) Add at least RCLONE_REMOTE and RCLONE_CONFIG to .env.\n"
                "3) Recreate the bot container: docker compose up -d --force-recreate telegram-helper.\n\n"
                "Check and run:\n"
                "• /backup_status — current status\n"
                "• /backup_test — test the remote\n"
                "• /backup_now — create a backup now\n"
                "• /backup_list — recent archives."
            )
        except Exception as e:
            logger.error(f"Error in rclone_command: {e}")
            await update.message.reply_text("Failed to show rclone help.")

    async def backup_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/backup_test command — check rclone remote access (admin/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ This command is for admin or special users only."
                )
                return
            self._track_user(user)
            result = rclone_manager.test_remote()
            await update.message.reply_text(
                rclone_manager.format_command_result("Rclone remote test", result)
            )
        except Exception as e:
            logger.error(f"Error in backup_test: {e}")
            await update.message.reply_text("Failed to test rclone remote.")

    async def backup_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/backup_now command — create an offsite backup of runtime files (admin/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ This command is for admin or special users only."
                )
                return
            self._track_user(user)
            await update.message.reply_text("⏳ Starting backup of runtime files...")
            result = rclone_manager.create_backup()
            await update.message.reply_text(rclone_manager.format_backup_result(result))
        except Exception as e:
            logger.error(f"Error in backup_now: {e}")
            await update.message.reply_text("Failed to create backup.")

    async def backup_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/backup_list command — show recent backup archives (admin/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ This command is for admin or special users only."
                )
                return
            self._track_user(user)
            result = rclone_manager.list_backups()
            await update.message.reply_text(
                rclone_manager.format_command_result("Rclone backups", result)
            )
        except Exception as e:
            logger.error(f"Error in backup_list: {e}")
            await update.message.reply_text("Failed to read backup list.")

    async def api_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/api command — show a masked API key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested API key info")

            try:
                from security import ALLOWED_APPS

                all_apps = ["default"] + list(ALLOWED_APPS.keys())

                keyboard = []
                for app_id in all_apps:
                    if app_id == "default":
                        label = "🔑 Default (from .env)"
                    else:
                        app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                        label = f"🔑 {app_name} ({app_id})"
                    keyboard.append(
                        [InlineKeyboardButton(label, callback_data=f"api_key:{app_id}")]
                    )

                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "🔐 Select a service to view the API key:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                # If the security module is missing, show the default key
                api_key = os.getenv("API_SECRET_KEY", "not configured")
                masked = self._mask_secret(api_key)
                await update.message.reply_text(
                    f"🔑 API key: `{masked}`", parse_mode=ParseMode.MARKDOWN_V2
                )

        except Exception as e:
            logger.error(f"Error in api_command: {e}")
            await update.message.reply_text("Failed to get API key.")

    async def gen_api_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/gen_api_key command — generate a new API key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested to generate new API key")

            try:
                from app_keys import list_app_ids
                from security import ALLOWED_APPS

                all_apps = ["default"] + list(ALLOWED_APPS.keys())

                keyboard = []
                for app_id in all_apps:
                    if app_id == "default":
                        label = "🔑 Default (in .env)"
                    else:
                        app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                        label = f"🔑 {app_name} ({app_id})"
                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                label, callback_data=f"gen_api_key:{app_id}"
                            )
                        ]
                    )

                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "🔐 Select a service to generate a new API key:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                await update.message.reply_text(
                    "⚠️ Safe mode does not show a new API key in Telegram.\n"
                    "Generate and save it locally on the server, then update `API_SECRET_KEY` in `.env`."
                )

        except Exception as e:
            logger.error(f"Error in gen_api_key_command: {e}")
            await update.message.reply_text("Failed to generate API key.")

    async def del_api_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/del_api_key command — delete an API key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested to delete API key")

            try:
                from app_keys import list_app_ids
                from security import ALLOWED_APPS

                app_ids = list_app_ids()
                if not app_ids:
                    await update.message.reply_text(
                        "❌ No saved individual keys."
                    )
                    return

                keyboard = []
                for app_id in app_ids:
                    app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                    label = f"🗑️ {app_name} ({app_id})"
                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                label, callback_data=f"del_api_key:{app_id}"
                            )
                        ]
                    )

                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "🗑️ Select a service to DELETE the API key:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                await update.message.reply_text("❌ app_keys module not found.")

        except Exception as e:
            logger.error(f"Error in del_api_key_command: {e}")
            await update.message.reply_text("Failed to delete API key.")

    async def encryption_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/encryption_key command — show a masked encryption key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested Encryption key info")

            try:
                from security import ALLOWED_APPS

                all_apps = ["default"] + list(ALLOWED_APPS.keys())

                keyboard = []
                for app_id in all_apps:
                    if app_id == "default":
                        label = "🔐 Default (from .env)"
                    else:
                        app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                        label = f"🔐 {app_name} ({app_id})"
                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                label, callback_data=f"encryption_key:{app_id}"
                            )
                        ]
                    )

                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "🔐 Select a service to view the encryption key:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                enc_key = os.getenv("ENCRYPTION_KEY", "not configured")
                masked = self._mask_secret(enc_key)
                await update.message.reply_text(
                    f"🔐 Encryption key: `{masked}`", parse_mode=ParseMode.MARKDOWN_V2
                )

        except Exception as e:
            logger.error(f"Error in encryption_key_command: {e}")
            await update.message.reply_text("Failed to get encryption key.")

    async def gen_encryption_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/gen_encryption_key command — generate a new encryption key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested to generate new encryption key")

            try:
                from app_keys import list_app_ids
                from security import ALLOWED_APPS

                all_apps = ["default"] + list(ALLOWED_APPS.keys())

                keyboard = []
                for app_id in all_apps:
                    if app_id == "default":
                        label = "🔐 Default (in .env)"
                    else:
                        app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                        label = f"🔐 {app_name} ({app_id})"
                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                label, callback_data=f"gen_encryption_key:{app_id}"
                            )
                        ]
                    )

                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "🔐 Select a service to generate a new encryption key:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                await update.message.reply_text(
                    "⚠️ Safe mode does not show a new encryption key in Telegram.\n"
                    "Generate and save it locally on the server, then update `ENCRYPTION_KEY` in `.env`."
                )

        except Exception as e:
            logger.error(f"Error in gen_encryption_key_command: {e}")
            await update.message.reply_text("Failed to generate encryption key.")

    async def del_encryption_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/del_encryption_key command — delete an encryption key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested to delete encryption key")

            try:
                from app_keys import list_app_ids
                from security import ALLOWED_APPS

                app_ids = list_app_ids()
                if not app_ids:
                    await update.message.reply_text(
                        "❌ No saved individual keys."
                    )
                    return

                keyboard = []
                for app_id in app_ids:
                    app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                    label = f"🗑️ {app_name} ({app_id})"
                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                label, callback_data=f"del_encryption_key:{app_id}"
                            )
                        ]
                    )

                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "🗑️ Select a service to DELETE the encryption key:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                await update.message.reply_text("❌ app_keys module not found.")

        except Exception as e:
            logger.error(f"Error in del_encryption_key_command: {e}")
            await update.message.reply_text("Failed to delete encryption key.")

    async def gen_chacha_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/gen_chacha_key command — generate a ChaCha20-Poly1305 key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested to generate ChaCha20-Poly1305 key")

            key_bytes = secrets.token_bytes(32)
            key_hex = key_bytes.hex()
            key_base64 = base64.b64encode(key_bytes).decode("utf-8")

            message = f"""✅ ChaCha20-Poly1305 key generated!

🔐 Key (hex, 64 characters):
`{key_hex}`

🔐 Key (base64):
`{key_base64}`

🔧 Algorithm: secrets.token_bytes(32) → 256-bit key
📊 Size: 32 bytes (256 bits)

💡 ChaCha20-Poly1305:
• Modern alternative to AES-256-GCM
• Excellent performance on ARM/mobile
• Used in WireGuard, Signal, TLS 1.3

⚠️ This is a test command"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in gen_chacha_key_command: {e}")
            await update.message.reply_text(
                "Failed to generate ChaCha20-Poly1305 key."
            )

    async def gen_pqc_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/gen_pqc_key command — generate a Post-Quantum Cryptography key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested to generate PQC key")

            key_bytes = secrets.token_bytes(48)
            key_hex = key_bytes.hex()
            key_base64 = base64.b64encode(key_bytes).decode("utf-8")

            message = f"""✅ Post-Quantum Cryptography key generated!

🔐 Key (hex, 96 characters):
`{key_hex}`

🔐 Key (base64):
`{key_base64}`

🔧 Size: 48 bytes (384 bits) - for CRYSTALS-Kyber-768
🛡️ Security level: NIST Level 3

💡 Post-Quantum Cryptography (PQC):
• Protection against quantum computers
• CRYSTALS-Kyber - NIST standard

⚠️ This is a test command"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in gen_pqc_key_command: {e}")
            await update.message.reply_text("Failed to generate PQC key.")

    # === USER MANAGEMENT ===

    async def admin_setcity(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/setcity command — set a city for a user."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ This command is admin-only."
            )
            return

        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text("Usage: /setcity <user_id> <city>")
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Invalid user_id")
            return

        city = " ".join(args[1:]).strip()
        if not city:
            await update.message.reply_text("City cannot be empty")
            return

        set_user_city(target_id, city)
        await update.message.reply_text(f"✅ City set for {target_id}: {city}")

    async def admin_setgreeting(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/setgreeting command — set a greeting for a user."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ This command is admin-only."
            )
            return

        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Usage: /setgreeting <user_id> <text>"
            )
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Invalid user_id")
            return

        greeting = " ".join(args[1:]).strip()
        if not greeting:
            await update.message.reply_text("Greeting cannot be empty")
            return

        set_user_greeting(target_id, greeting)
        await update.message.reply_text(f"✅ Greeting set for {target_id}")

    async def admin_special_add(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/special_add command — add a special user."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ This command is admin-only."
            )
            return

        args = context.args or []
        if len(args) != 1:
            await update.message.reply_text("Usage: /special_add <user_id>")
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Invalid user_id")
            return

        add_special_user(target_id)
        menu_note = ""
        try:
            await set_special_bot_menu(context.bot, target_id)
            menu_note = "\nSpecial command menu updated: /my_profile added."
        except Exception as exc:
            logger.warning("special menu setup failed for %s: %s", target_id, exc)
            menu_note = "\n⚠️ Could not refresh the command menu immediately; it will update after a bot restart."
        await update.message.reply_text(
            f"✅ User {target_id} added to special{menu_note}"
        )

    async def admin_special_remove(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/special_remove command — remove a special user."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ This command is admin-only."
            )
            return

        args = context.args or []
        if len(args) != 1:
            await update.message.reply_text("Usage: /special_remove <user_id>")
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Invalid user_id")
            return

        remove_special_user(target_id)
        menu_note = ""
        try:
            await clear_chat_bot_menu(context.bot, target_id)
            menu_note = "\nPersonal special menu cleared."
        except Exception as exc:
            logger.warning("special menu clear failed for %s: %s", target_id, exc)
            menu_note = "\n⚠️ Could not reset the command menu immediately; it will update after a bot restart."
        await update.message.reply_text(
            f"✅ User {target_id} removed from special{menu_note}"
        )

    async def admin_list_users(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/list_users command — admins (ADMIN_USER_IDS), special users (special_user_ids), others from the database."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.effective_message.reply_text(
                "⛔ This command is admin-only."
            )
            return

        special, users = storage_list_users()
        special_set = set(special)
        admin_ids = self.config.resolved_admin_user_ids()
        admin_set = set(admin_ids)

        def _fmt_user_line(uid: int, prefs: dict) -> str:
            username = prefs.get("username")
            first = prefs.get("first_name") or ""
            last = prefs.get("last_name") or ""
            name = (first + (" " + last if last else "")).strip()
            handle = f"@{username}" if username else ""
            city = prefs.get("city")
            parts = [f"`{uid}`"]
            if handle:
                parts.append(escape_markdown(handle))
            if name:
                parts.append(escape_markdown(name))
            if city:
                parts.append(f"🏙 {escape_markdown(city)}")
            if prefs.get("last_seen"):
                parts.append(f"🕒 {escape_markdown(prefs['last_seen'][:10])}")
            return " \\- ".join(parts)

        lines = [f"*Administrators* \\({len(admin_ids)}\\)*:*"]
        if admin_ids:
            for uid in admin_ids:
                prefs = users.get(uid, {})
                lines.append(_fmt_user_line(uid, prefs))
        else:
            lines.append("\\-")

        special_only = [uid for uid in special if uid not in admin_set]
        lines.append(f"\n*Special users* \\({len(special_only)}\\)*:*")
        if special_only:
            for uid in special_only:
                prefs = users.get(uid, {})
                lines.append(_fmt_user_line(uid, prefs))
        else:
            lines.append("\\-")

        regular = {
            uid: p
            for uid, p in users.items()
            if uid not in special_set and uid not in admin_set
        }
        lines.append(f"\n*Regular users* \\({len(regular)}\\)*:*")
        if regular:
            for uid, prefs in sorted(
                regular.items(), key=lambda kv: kv[1].get("last_seen", ""), reverse=True
            ):
                lines.append(_fmt_user_line(uid, prefs))
        else:
            lines.append("\\-")

        list_users_kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📒 Journal",
                        callback_data="lu_log",
                    ),
                    InlineKeyboardButton(
                        "⭐ Special status",
                        callback_data="lu_tog",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🧩 Profiles by protocol",
                        callback_data="lu_pr",
                    ),
                ],
            ]
        )
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=list_users_kb,
        )

    _LIST_USERS_SPECIAL_PAGE = 10
    _LIST_USERS_PROTOCOL_PAGE = 10

    def _list_users_special_candidates(self) -> list[int]:
        """All known user_id values from storage except admins (special is not clickable here for them)."""
        special, users = storage_list_users()
        admin_set = {int(x) for x in self.config.admin_user_ids}
        uids = sorted(set(users.keys()) | set(special))
        return [u for u in uids if u not in admin_set]

    def _list_users_protocol_candidates(self) -> list[int]:
        """
        Candidates for creating profiles by protocol: admin + special ONLY.
        Regular users are not shown here — create/
        delete/rotate of a profile is blocked at the UI and handler level.
        If that user really needs a profile —
        first move them to `special` via `/special_add <id>`.
        """
        special, _users = storage_list_users()
        admin_set = {
            int(x) for x in (self.config.admin_user_ids if self.config else [])
        }
        return sorted(set(special) | admin_set)

    def _is_profile_target_eligible(self, uid: int) -> bool:
        """
        Whether profiles can be managed for this TG ID (create /
        delete / rotate secrets). Allowed only for admin and
        special users; forbidden for regular users.
        """
        return self._is_privileged(uid)

    def _build_list_users_special_picker(
        self, page: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        special, users = storage_list_users()
        special_set = set(special)
        candidates = self._list_users_special_candidates()
        n = len(candidates)
        page_size = self._LIST_USERS_SPECIAL_PAGE
        total_pages = max(1, (n + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        chunk = candidates[page * page_size : (page + 1) * page_size]

        lines = [
            "⭐ Toggle special status",
            "",
            "Select a user. Admins (ADMIN_USER_IDS) are not shown here.",
            f"Page {page + 1}/{total_pages}, total: {n}.",
            "",
        ]
        if not chunk:
            lines.append("No users to select.")

        rows: list[list[InlineKeyboardButton]] = []
        for uid in chunk:
            info = users.get(uid, {})
            un = info.get("username") or ""
            mark = "★ " if uid in special_set else ""
            tail = f" @{un}" if un else ""
            label = f"{mark}{uid}{tail}"[:58]
            rows.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"lu_s:{uid}:{page}",
                    )
                ]
            )

        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️", callback_data=f"lu_p:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("▶️", callback_data=f"lu_p:{page + 1}"))
        if nav_row:
            rows.append(nav_row)
        rows.append([InlineKeyboardButton("✖️ Close", callback_data="lu_x")])

        text = "\n".join(lines)
        return text, InlineKeyboardMarkup(rows)

    async def _edit_special_user_confirm(self, query, uid: int, page: int) -> None:
        special, users = storage_list_users()
        special_set = set(special)
        info = users.get(uid, {})
        un = info.get("username") or "—"
        fn = (info.get("first_name") or "").strip()
        ln = (info.get("last_name") or "").strip()
        name = (fn + (" " + ln if ln else "")).strip() or "—"
        is_sp = uid in special_set
        role = "special" if is_sp else "regular"

        text = (
            f"👤 {uid}\n"
            f"Name: {name}\n"
            f"Username: {un}\n"
            f"Currently: {role}\n\n"
            "Choose an action:"
        )
        if is_sp:
            row_action = [
                InlineKeyboardButton(
                    "⬇️ Remove from special",
                    callback_data=f"lu_out:{uid}:{page}",
                )
            ]
        else:
            row_action = [
                InlineKeyboardButton(
                    "⬆️ Add to special",
                    callback_data=f"lu_in:{uid}:{page}",
                )
            ]
        kb = InlineKeyboardMarkup(
            [
                row_action,
                [
                    InlineKeyboardButton(
                        "◀️ To list",
                        callback_data=f"lu_p:{page}",
                    )
                ],
                [InlineKeyboardButton("✖️ Close", callback_data="lu_x")],
            ]
        )
        await query.message.edit_text(text, reply_markup=kb)

    async def _after_special_toggle(
        self, query, uid: int, page: int, *, added: bool
    ) -> None:
        action = "added to special" if added else "removed from special"
        text = f"✅ User {uid} {action}."
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "◀️ To list",
                        callback_data=f"lu_p:{page}",
                    )
                ],
                [InlineKeyboardButton("✖️ Close", callback_data="lu_x")],
            ]
        )
        await query.message.edit_text(text, reply_markup=kb)

    @staticmethod
    def _uid_profile_suffix(uid: int) -> str:
        raw = str(uid)
        if len(raw) <= 4:
            return raw
        return f"{raw[:2]}...{raw[-2:]}"

    def _build_protocol_profile_name(self, proto_key: str, uid: int) -> str:
        # Mieru — greenfield: canonical names from the start per plan_Mieru.md §7
        # (`Mieru_ID<first2>_<last2>`), without the legacy `Mieruxx...yy` name.
        if proto_key == "mieru":
            try:
                return provision_manager.make_client_name("mieru", int(uid))
            except Exception:
                pass
        base = self._uid_profile_suffix(uid)
        prefixes = {
            "vless_reality": "Vless",
            "hysteria2": "Hysteria2",
            "tuic": "Tuic",
            "anytls": "Anytls",
            "xhttp": "Xhttp",
            "mtproto": "Mtproto",
        }
        return f"{prefixes.get(proto_key, 'Profile')}{base}"

    def _xui_find_vless_client(
        self, client_obj, uid: int
    ) -> tuple[bool, dict, str, int]:
        """Find a VLESS client in the 3x-ui panel by UID.

        First the canonical name (`Vless_ID<first2>_<last2>`, as in /provision),
        then legacy (`Vless82...09`). Scans default_inbound and, if needed,
        the legacy bot_inbound_id.
        """
        cfg = xui_manager.load_config()
        default_id = int(cfg.get("default_inbound_id") or 0)
        bot_id = int(cfg.get("bot_inbound_id") or 0)
        inbound_ids: list[int] = []
        if default_id:
            inbound_ids.append(default_id)
        if bot_id and bot_id != default_id:
            inbound_ids.append(bot_id)

        emails: list[str] = []
        try:
            emails.append(provision_manager.make_client_name("vless", int(uid)))
        except Exception:
            pass
        legacy = self._build_protocol_profile_name("vless_reality", uid)
        if legacy not in emails:
            emails.append(legacy)

        for iid in inbound_ids:
            for em in emails:
                found, c = client_obj.find_client(iid, em)
                if found:
                    return True, c, em, iid
        prefer = emails[0] if emails else legacy
        return False, {}, prefer, default_id

    def _list_users_addable_protocols_live(self) -> list[tuple[str, str]]:
        """
        Protocols that:
        1) are supported by bot automation,
        2) are actually running (🟢 in live_status),
        3) can issue add_client in the current code.
        """
        supported = {
            "vless_reality",
            "hysteria2",
            "tuic",
            "anytls",
            "xhttp",
            "mtproto",
        }
        out: list[tuple[str, str]] = []
        snapshot = live_status.gather_full_snapshot()
        for st in snapshot.get("protocols", []):
            if st.key in supported and st.implemented and st.live is True:
                out.append((st.key, st.display))
        return out

    def _build_protocol_user_picker(
        self, page: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        # IMPORTANT: profile management uses admin + special only.
        # Regular users are excluded (policy: /special_add first).
        candidates = self._list_users_protocol_candidates()
        n = len(candidates)
        page_size = self._LIST_USERS_PROTOCOL_PAGE
        total_pages = max(1, (n + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        chunk = candidates[page * page_size : (page + 1) * page_size]

        lines = [
            "🧩 Create a profile by protocol",
            "",
            "Step 1/2: select a user.",
            "Only admin and special are shown.",
            "To add a regular user — first /special_add <id>.",
            f"Page {page + 1}/{total_pages}, total: {n}.",
            "",
        ]
        if not chunk:
            lines.append("No admin/special users to select.")

        special, users = storage_list_users()
        special_set = set(special)
        rows: list[list[InlineKeyboardButton]] = []
        for uid in chunk:
            info = users.get(uid, {})
            un = info.get("username") or ""
            mark = "★ " if uid in special_set else ""
            tail = f" @{un}" if un else ""
            label = f"{mark}{uid}{tail}"[:58]
            rows.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"lu_pru:{uid}:{page}",
                    )
                ]
            )

        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton("◀️", callback_data=f"lu_prp:{page - 1}")
            )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton("▶️", callback_data=f"lu_prp:{page + 1}")
            )
        if nav_row:
            rows.append(nav_row)
        rows.append([InlineKeyboardButton("✖️ Close", callback_data="lu_x")])
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    _PROTO_SHORT_LABELS: dict[str, str] = {
        "vless_reality": "VLESS",
        "hysteria2": "Hysteria2",
        "tuic": "TUIC",
        "anytls": "AnyTLS",
        "xhttp": "XHTTP",
        "mtproto": "MTProto",
    }

    def _protocol_client_exists_for_user(self, proto_key: str, uid: int) -> bool:
        """Whether the user has a client (canon / 3x-ui / legacy).

        Important: do not rely only on ``profiles_for_user`` — it looks at
        ``is_enabled()`` in JSON and skips Hy2 when the service is already live
        but the ``enabled`` flag is still false. That made the UI lie with “profile not found”.
        """
        proto_canon_map = {
            "vless_reality": "vless",
            "hysteria2": "hysteria2",
            "tuic": "tuic",
            "anytls": "anytls",
            "xhttp": "xhttp",
            "mtproto": "mtproto",
        }
        canon_key = proto_canon_map.get(proto_key)
        canon_name = ""
        if canon_key:
            try:
                canon_name = provision_manager.make_client_name(canon_key, int(uid))
            except Exception:
                canon_name = ""

        # 1) Canonical name — primary source after /provision and Create buttons.
        if canon_name:
            if proto_key == "vless_reality" and self._xui_is_active():
                try:
                    exists, _msg, _uri = xui_manager.find_named_client_uri(canon_name)
                    if exists:
                        return True
                except Exception as exc:
                    logger.warning(
                        "protocol exists xui(%s): %s", canon_name, exc
                    )
            else:
                mgr = {
                    "hysteria2": hysteria2_manager,
                    "tuic": tuic_manager,
                    "anytls": anytls_manager,
                    "xhttp": xhttp_manager,
                    "mtproto": mtproto_manager,
                    "vless_reality": vless_manager,
                }.get(proto_key)
                if mgr is not None:
                    try:
                        if mgr.get_client(canon_name) is not None:
                            return True
                    except Exception as exc:
                        logger.warning(
                            "protocol exists %s(%s): %s",
                            proto_key,
                            canon_name,
                            exc,
                        )

        # 2) Legacy /user card name (Vless52...49, Hysteria252...49).
        legacy_name = self._build_protocol_profile_name(proto_key, uid)
        if proto_key == "vless_reality":
            # When 3x-ui is active, legacy host-Xray does not count as “has profile”.
            if not self._xui_is_active():
                try:
                    if vless_manager.get_client(legacy_name) is not None:
                        return True
                except Exception:
                    pass
            else:
                try:
                    exists, _msg, _uri = xui_manager.find_named_client_uri(legacy_name)
                    if exists:
                        return True
                except Exception:
                    pass
        elif proto_key == "hysteria2":
            try:
                if hysteria2_manager.get_client(legacy_name) is not None:
                    return True
            except Exception:
                pass
        return False

    def _build_protocol_picker_for_user(
        self, uid: int, page: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        protocols = self._list_users_addable_protocols_live()
        rows: list[list[InlineKeyboardButton]] = []
        special, _users = storage_list_users()
        is_special_target = uid in set(special)
        # If a regular uid still lands here (e.g. via an old
        # keyboard) — hide management buttons and explain the rule.
        if not self._is_profile_target_eligible(uid):
            text = (
                f"👤 User: {uid}\n"
                "⛔ Profile creation is forbidden: the user is not admin or special.\n\n"
                "To issue a profile, first move them to special:\n"
                f"`/special_add {uid}`"
            )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "◀️ Back to user picker",
                            callback_data=f"lu_prp:{page}",
                        )
                    ],
                    [InlineKeyboardButton("✖️ Close", callback_data="lu_x")],
                ]
            )
            return text, kb

        for key, display in protocols:
            exists = self._protocol_client_exists_for_user(key, uid)
            short = self._PROTO_SHORT_LABELS.get(key, display)
            # One row per protocol: status is in the button label (previously
            # a separate “· … profile not found ·” looked like a second VLESS).
            if exists:
                row = [
                    InlineKeyboardButton(
                        f"✅ {short}: present",
                        callback_data=f"lu_prc:{key}:{uid}:{page}",
                    ),
                    InlineKeyboardButton(
                        "♻️ Replace",
                        callback_data=f"lu_prr:{key}:{uid}:{page}",
                    ),
                ]
            else:
                row = [
                    InlineKeyboardButton(
                        f"➕ {short}: create",
                        callback_data=f"lu_prc:{key}:{uid}:{page}",
                    )
                ]
            rows.append(row)

        if not rows:
            rows.append(
                [InlineKeyboardButton("Refresh", callback_data=f"lu_pru:{uid}:{page}")]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        "🔄 Refresh statuses",
                        callback_data=f"lu_pru:{uid}:{page}",
                    )
                ]
            )

        rows.append(
            [
                InlineKeyboardButton(
                    "◀️ Back to user picker", callback_data=f"lu_prp:{page}"
                )
            ]
        )
        rows.append([InlineKeyboardButton("✖️ Close", callback_data="lu_x")])

        if protocols:
            text = (
                f"👤 User: {uid}\n"
                "Step 2/2: one protocol — one button row "
                "(no more duplicate “status as a separate button”).\n\n"
                "✅ present / ➕ create — looking for Vless_ID… / Hys_ID… "
                "(and a legacy name, if any).\n"
                "The Hy2 service may be 🟢 while the user still has no client "
                "— then tap “create” or "
                f"/provision {uid}.\n"
                f"Special: {'yes' if is_special_target else 'no'}."
            )
        else:
            text = (
                f"👤 User: {uid}\n"
                "There are currently no 🟢 protocols supported for auto-create.\n"
                "Bring the protocol up and try again."
            )
        return text, InlineKeyboardMarkup(rows)

    async def _create_protocol_profile_for_user(
        self,
        query,
        uid: int,
        proto_key: str,
        page: int,
        *,
        replace_existing: bool = False,
    ) -> bool:
        # Policy: profiles are created only for admin/special.
        # A server-side gate is needed on top of the UI filter so that a stale
        # keyboard or a raw callback cannot bypass this rule.
        if not self._is_profile_target_eligible(uid):
            try:
                await query.message.reply_text(
                    f"⛔ Creating a profile for user {uid} is forbidden.\n"
                    "Profiles are issued only to admins and special users.\n"
                    f"To issue a profile, first: /special_add {uid}"
                )
            except Exception:
                pass
            logger.info(
                "list_users inline: admin %s tried to create profile for non-eligible uid=%s proto=%s",
                query.from_user.id,
                uid,
                proto_key,
            )
            return False
        # Stage 3: unified routing with canon-naming.
        #
        # - VLESS on a VPS with 3x-ui → xui_manager.provision_named_client
        #   (creates a client in the bot-managed inbound, name `Vless_ID*_*`).
        # - VLESS without xui → legacy vless_manager.add_client (host-Xray).
        # - Other protocols (Hys/Mtp/Tuic/AnyTLS/XHTTP) — always
        #   via their `*_manager.add_client(canon_name)`. That is what
        #   provision_manager does internally.
        #
        # Canonical name format is `<Prefix>_ID<first2>_<last2>`. Previously
        # the picker used legacy `<Proto>_uid_suffix` (`Vless82...09`),
        # which did not overlap with what /provision created.
        proto_canon_map = {
            "vless_reality": "vless",
            "hysteria2": "hysteria2",
            "tuic": "tuic",
            "anytls": "anytls",
            "xhttp": "xhttp",
            "mtproto": "mtproto",
        }
        canon_proto = proto_canon_map.get(proto_key)
        if canon_proto:
            try:
                name = provision_manager.make_client_name(canon_proto, uid)
            except Exception:
                name = self._build_protocol_profile_name(proto_key, uid)
        else:
            name = self._build_protocol_profile_name(proto_key, uid)

        ok = False
        details: list[str] = []
        uri_for_qr = ""
        try:
            xui_active = self._xui_is_active()

            if proto_key == "vless_reality" and xui_active:
                # Bot-managed inbound on 3x-ui (clone of default_inbound).
                if replace_existing:
                    rm_ok, rm_msg = xui_manager.remove_named_client(name)
                    details.append(f"[replace via xui] {rm_msg}")
                ok, msg, uri = xui_manager.provision_named_client(name, uid)
                details.append(f"[xui] {msg}")
                uri_for_qr = uri
            elif proto_key == "vless_reality":
                # Bare host-Xray (legacy path).
                if replace_existing:
                    _rm_ok, rm_msg = vless_manager.remove_client(name)
                    details.append(f"[replace] {rm_msg}")
                ok, msg, client = vless_manager.add_client(name, None)
                details.append(msg)
                if ok:
                    apply_ok, apply_msg = vless_manager.apply_xray_config()
                    details.append(apply_msg)
                    await self._reply_vless_qr(query.message, client.get("uuid", name))
                    ok = ok and apply_ok
            elif proto_key == "hysteria2":
                if replace_existing:
                    _rm_ok, rm_msg = hysteria2_manager.remove_client(name)
                    details.append(f"[replace] {rm_msg}")
                ok, msg, client = hysteria2_manager.add_client(name)
                details.append(msg)
                if ok:
                    apply_ok, apply_msg = hysteria2_manager.apply_config()
                    details.append(apply_msg)
                    await self._reply_hy2_qr(
                        query.message, client.get("password", name)
                    )
                    ok = ok and apply_ok
            elif proto_key == "tuic":
                if replace_existing:
                    _rm_ok, rm_msg = tuic_manager.remove_client(name)
                    details.append(f"[replace] {rm_msg}")
                ok, msg, _client = tuic_manager.add_client(name)
                details.append(msg)
            elif proto_key == "anytls":
                if replace_existing:
                    _rm_ok, rm_msg = anytls_manager.remove_client(name)
                    details.append(f"[replace] {rm_msg}")
                ok, msg, _client = anytls_manager.add_client(name)
                details.append(msg)
            elif proto_key == "xhttp":
                if replace_existing:
                    _rm_ok, rm_msg = xhttp_manager.remove_client(name)
                    details.append(f"[replace] {rm_msg}")
                ok, msg, _client = xhttp_manager.add_client(name)
                details.append(msg)
            elif proto_key == "mtproto":
                if replace_existing:
                    _rm_ok, rm_msg = mtproto_manager.remove_client(name)
                    details.append(f"[replace] {rm_msg}")
                ok, msg, _client = mtproto_manager.add_client(name)
                details.append(msg)
            else:
                details.append(f"Unknown protocol: {proto_key}")
                ok = False
        except Exception as exc:
            logger.error(
                "protocol profile create failed: proto=%s uid=%s err=%s",
                proto_key,
                uid,
                exc,
            )
            details.append(f"Error: {exc}")
            ok = False

        # If we got a ready URI for VLESS-Reality via xui —
        # send the QR in the same message so UX matches /provision.
        if ok and uri_for_qr:
            try:
                await self._reply_qr_for_link(query.message, uri_for_qr, name)
            except Exception as exc:
                logger.warning("post-create QR send failed: %s", exc)

        status = (
            "✅ Profile replaced"
            if (ok and replace_existing)
            else ("✅ Profile created" if ok else "❌ Failed to create profile")
        )
        text = (
            f"{status}\n"
            f"User: {uid}\n"
            f"Protocol: {proto_key}\n"
            f"Profile name: {name}\n\n" + "\n".join(details[:6])
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "➕ Add another protocol", callback_data=f"lu_pru:{uid}:{page}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "◀️ Back to user picker", callback_data=f"lu_prp:{page}"
                    )
                ],
                [InlineKeyboardButton("✖️ Close", callback_data="lu_x")],
            ]
        )
        # edit_text may fail if the original message cannot
        # be edited (too old, Markdown parse on the new text,
        # rate-limit). Fallback — send a new message.
        try:
            await query.message.edit_text(text, reply_markup=kb)
        except Exception as edit_exc:
            logger.warning(
                "edit_text after profile create failed (uid=%s proto=%s): %s",
                uid,
                proto_key,
                edit_exc,
            )
            try:
                await query.message.reply_text(text, reply_markup=kb)
            except Exception as reply_exc:
                logger.error("reply_text fallback also failed: %s", reply_exc)
        logger.info(
            "list_users inline: admin %s protocol profile uid=%s proto=%s name=%s ok=%s",
            query.from_user.id,
            uid,
            proto_key,
            name,
            ok,
        )
        return ok

    def _vless_link_via_xui(self, uid: int) -> tuple[bool, str, str, str]:
        """
        Get a VLESS-Reality client URI from 3x-ui (if integration is enabled).

        Returns (ok, message, link, panel_email). Looks up the client by canonical name
        `Vless_ID*_*`, then by the legacy `/user` card name.
        """
        try:
            if not xui_manager.is_enabled():
                return False, "xui disabled", "", ""
            client_obj, err = xui_manager.make_client_or_error()
            if client_obj is None:
                return False, err or "xui client not ready", "", ""
            snap = xui_manager.status_summary()
            found, client, email_used, inbound_id = self._xui_find_vless_client(
                client_obj, uid
            )
            if not found or not inbound_id:
                return False, "xui client not found", "", email_used
            ok_ib, msg_ib, inbound = client_obj.get_inbound(inbound_id)
            if not ok_ib:
                return False, msg_ib or "xui get_inbound failed", "", email_used
            fallback = xui_manager.panel_host(snap.get("base_url", ""))
            ok_link, msg_link, link = xui_manager.build_vless_reality_link(
                inbound, client, fallback_host=fallback
            )
            if not ok_link:
                return False, msg_link or "xui build link failed", "", email_used
            return True, "ok", link, email_used
        except Exception as exc:
            logger.warning("_vless_link_via_xui(uid=%s): %s", uid, exc)
            return False, f"xui error: {exc}", "", ""

    def _build_user_protocol_profile_lookup(self, uid: int) -> dict:
        """
        Find a user profile by the standard name template.
        Currently self-service is supported for VLESS and Hysteria2.

        For VLESS: if 3x-ui integration is enabled (`/xui_setup`), the link
        is built from the panel inbound (that is the Xray that actually runs
        on 443). Otherwise fall back to the bot local `vless_config.json`.
        """
        out: dict = {}
        v_name = self._build_protocol_profile_name("vless_reality", uid)
        h_name = self._build_protocol_profile_name("hysteria2", uid)

        xui_ok, xui_msg, xui_link, v_xui_email = self._vless_link_via_xui(uid)
        if xui_ok and xui_link:
            out["vless_reality"] = {
                "name": v_xui_email or v_name,
                "ok": True,
                "message": "link from 3x-ui (working panel inbound)",
                "url": xui_link,
                "source": "xui",
            }
        else:
            v_client = vless_manager.get_client(v_name)
            if v_client:
                xui_active = self._xui_is_active()
                if xui_active:
                    ok = False
                    msg = (
                        "inactive/test legacy profile: on this VPS "
                        "working VLESS is served by 3x-ui"
                    )
                    link = ""
                else:
                    ok, msg, link = vless_manager.generate_client_link(v_name)
                out["vless_reality"] = {
                    "name": v_name,
                    "ok": ok,
                    "message": msg,
                    "url": link if ok else "",
                    "source": "vless_config_test" if xui_active else "vless_config",
                }

        h_client = hysteria2_manager.get_client(h_name)
        if h_client:
            ok, msg, uri = hysteria2_manager.generate_client_uri(h_name)
            out["hysteria2"] = {
                "name": h_name,
                "ok": ok,
                "message": msg,
                "url": uri if ok else "",
            }
        return out

    # === User card (/user <id> + bare-number recognition) ===
    #
    # Goal: admin enters a TG ID and immediately gets a menu of all profiles for that
    # user (VLESS-Reality, Hysteria2, MTProto, NaiveProxy) with
    # create / delete / rotate secret / get QR.
    # Client names are built deterministically via `_build_protocol_profile_name`;
    # no separate owner-mapping DB is introduced.
    #
    # NaiveProxy is a special mode: one shared `basic_auth` on the server, so
    # actions differ (rotation changes credentials for everyone; creating
    # per-user clients is not supported).

    _USER_CARD_PROTOCOLS: tuple[tuple[str, str, str], ...] = (
        ("vless_reality", "🛡", "VLESS-Reality"),
        ("hysteria2", "⚡", "Hysteria2"),
        ("naiveproxy", "🌐", "NaiveProxy"),
        ("mtproto", "📡", "MTProto"),
        ("mieru", "🛰", "Mieru"),
    )

    def _user_role_label(self, uid: int) -> str:
        admin_set = {
            int(x) for x in (self.config.admin_user_ids if self.config else [])
        }
        special_set = set(storage_list_users()[0])
        if uid in admin_set:
            return "admin"
        if uid in special_set:
            return "special"
        return "regular"

    def _user_protocol_state(self, proto_key: str, uid: int) -> dict:
        """
        Determine whether this user has a profile in this protocol,
        and prepare display fields. Returns a dict with keys:
            name      — deterministic profile name
            exists    — bool
            active    — bool, whether the profile can be treated as a working source
            details   — short string for the card text (no secrets)
        """
        name = self._build_protocol_profile_name(proto_key, uid)
        info = {
            "name": name,
            "exists": False,
            "active": False,
            "details": "",
            "source": "",
        }

        try:
            if proto_key == "vless_reality":
                xui_active = self._xui_is_active()
                client = vless_manager.get_client(name)
                if client:
                    info["exists"] = True
                    info["source"] = "vless_config"
                    created = client.get("created_at", "")
                    created_label = f"created {created[:10]}" if created else "created"
                    if xui_active:
                        info["details"] = (
                            f"{created_label}; inactive/test legacy profile "
                            "(bot local xray, not 3x-ui)"
                        )
                    else:
                        info["active"] = True
                        info["details"] = created_label
                elif xui_active:
                    info["details"] = "working VLESS is issued via 3x-ui below"
            elif proto_key == "hysteria2":
                client = hysteria2_manager.get_client(name)
                if client:
                    info["exists"] = True
                    info["active"] = True
                    created = client.get("created_at", "")
                    info["details"] = f"created {created[:10]}" if created else "created"
            elif proto_key == "mtproto":
                client = mtproto_manager.get_client(name)
                if client:
                    info["exists"] = True
                    info["active"] = True
                    created = client.get("created_at", "")
                    info["details"] = f"created {created[:10]}" if created else "created"
            elif proto_key == "mieru":
                # Mieru: per-user clients with canonical name Mieru_ID82_09.
                client = mieru_manager.get_client(name)
                if client:
                    info["exists"] = True
                    info["active"] = True
                    created = client.get("created_at", "")
                    info["details"] = f"created {created[:10]}" if created else "created"
            elif proto_key == "naiveproxy":
                # NaiveProxy: no per-user clients. Show the shared credentials as info.
                raw = naiveproxy_manager.get_status()
                username = raw.get("username") or ""
                if username:
                    info["exists"] = True
                    info["active"] = True
                    info["name"] = username
                    info["details"] = "shared credentials (one basic_auth for everyone)"
                else:
                    info["details"] = (
                        "basic_auth is not configured (use /naive_gen_creds)"
                    )
        except Exception as exc:
            logger.warning("_user_protocol_state(%s, %s): %s", proto_key, uid, exc)
        return info

    def _user_card_compose(self, uid: int) -> tuple[str, InlineKeyboardMarkup]:
        """Build user-card text and the action inline keyboard."""

        def he(x) -> str:
            if x is None:
                return ""
            return html.escape(str(x), quote=False)

        _, users = storage_list_users()
        info = users.get(uid, {})

        username = info.get("username") or ""
        first = (info.get("first_name") or "").strip()
        last = (info.get("last_name") or "").strip()
        full_name = (first + (" " + last if last else "")).strip() or "—"
        city = info.get("city") or "—"
        last_seen = (info.get("last_seen") or "")[:10] or "—"
        role = self._user_role_label(uid)
        # Profile-issuance policy: admin/special only.
        eligible = self._is_profile_target_eligible(uid)

        # Live protocol check: show “✅ Vlessxx..yy” only if
        # the server process is actually running; otherwise the profile may be in JSON
        # but clients still will not use it.
        xui_present = False
        try:
            snapshot = live_status.gather_full_snapshot()
            live_by_key = {st.key: st for st in snapshot.get("protocols", [])}
            xui = snapshot.get("xui_panel")
            if xui is not None:
                # “Present” = actually running or at least installed.
                xui_present = (
                    xui.process_alive is True
                    or xui.configured is True
                    or (xui.container_status or {}).get("found") is True
                )
        except Exception:
            live_by_key = {}

        # HTML: MarkdownV2 broke on “.” in dates/paths and on esc() inside `code`.
        lines: list[str] = [
            "👤 <b>User card</b>",
            "",
            f"<b>ID:</b> <code>{he(uid)}</code>",
            f"<b>Name:</b> {he(full_name)}",
            f"<b>Username:</b> {he('@' + username if username else '—')}",
            f"<b>City:</b> {he(city)}",
            f"<b>Last seen:</b> {he(last_seen)}",
            f"<b>Role:</b> {he(role)}",
        ]
        if not eligible:
            lines.append(
                "<b>Profile access:</b> "
                + he("🔒 forbidden (admin/special only)")
            )
            lines.append("")
            lines.append(
                he(
                    "To issue a profile to this user, first move "
                    "them to special: /special_add "
                )
                + f"<code>{he(uid)}</code>"
            )
        if xui_present:
            # Do not block VLESS creation via the bot, but explain loudly
            # that the bot has its own Xray (`/usr/local/etc/xray`) and 3x-ui has its own,
            # and that clients added by the bot will NOT appear in the panel.
            lines.append("")
            lines.append(
                "⚠️ 3x-ui was found on the VPS. The bot writes VLESS clients to "
                "its <code>/usr/local/etc/xray</code>, 3x-ui — to its own "
                "<code>/etc/x-ui/x-ui.db</code>. "
                "Bot clients and panel clients do NOT overlap."
            )

        # If the admin has 3x-ui API integration configured — show
        # a separate “via 3x-ui” block with its own buttons. For
        # the card we only check whether a client exists, without revealing secrets.
        xui_on = False
        xui_inbound_id = 0
        xui_user_exists = False
        xui_user_name = ""
        try:
            if xui_manager.is_enabled():
                snap = xui_manager.status_summary()
                xui_inbound_id = int(snap.get("default_inbound_id") or 0)
                xui_on = xui_inbound_id > 0
                if xui_on:
                    client_obj, _err = xui_manager.make_client_or_error()
                    if client_obj is not None:
                        found, _client, email_used, _inbound = (
                            self._xui_find_vless_client(client_obj, uid)
                        )
                        xui_user_exists = bool(found)
                        xui_user_name = email_used or ""
        except Exception as exc:
            logger.warning("_user_card_compose: xui status failed: %s", exc)

        lines.append("")
        lines.append("<b>Profiles:</b>")

        rows: list[list[InlineKeyboardButton]] = []
        for proto_key, icon, display in self._USER_CARD_PROTOCOLS:
            state = self._user_protocol_state(proto_key, uid)
            live = live_by_key.get(proto_key)
            live_marker = ""
            if live is not None:
                live_marker = " " + (
                    "🟢"
                    if live.live is True
                    else ("🔴" if live.live is False else "⚪")
                )
            line = f"{icon} <b>{he(display)}</b>:{live_marker}  "
            if state["exists"]:
                status_icon = "✅" if state.get("active") else "🧪"
                line += f"{status_icon} <code>{he(state['name'])}</code>"
                if state["details"]:
                    line += f" — {he(state['details'])}"
            else:
                line += "❌ no profile"
                if state["details"]:
                    line += f" — {he(state['details'])}"
            lines.append(line)

            # Protocol buttons below are built differently depending on
            # whether a profile exists and which protocol it is.
            if proto_key == "naiveproxy":
                # NaiveProxy has one shared basic_auth: we can only show
                # the current credentials and rotate them (affects everyone).
                if state["exists"]:
                    naive_row = [
                        InlineKeyboardButton(
                            f"📲 {display} URI",
                            callback_data=f"uc_qr:{uid}:{proto_key}",
                        )
                    ]
                    # Global rotation affects all clients at once,
                    # so it does not cancel even the admin/special-only right;
                    # but we still hide it from ineligible recipients
                    # so we do not create accidental reasons to rotate the shared
                    # password from a regular-user card.
                    if eligible:
                        naive_row.append(
                            InlineKeyboardButton(
                                "♻️ Change shared password",
                                callback_data=f"uc_rot:{uid}:{proto_key}",
                            )
                        )
                    rows.append(naive_row)
                else:
                    if eligible:
                        rows.append(
                            [
                                InlineKeyboardButton(
                                    f"⚙️ Configure {display}",
                                    callback_data=f"uc_setup:{uid}:{proto_key}",
                                )
                            ]
                        )
                    else:
                        rows.append(
                            [
                                InlineKeyboardButton(
                                    "🔒 admin/special only",
                                    callback_data=f"uc_locked:{uid}:{proto_key}",
                                )
                            ]
                        )
                continue

            # VLESS / Hy2 / MTProto: per-client model.
            if state["exists"]:
                if proto_key == "vless_reality" and not state.get("active"):
                    row = []
                    if eligible:
                        row.append(
                            InlineKeyboardButton(
                                "🧹 Delete test",
                                callback_data=f"uc_del:{uid}:{proto_key}",
                            )
                        )
                    else:
                        row.append(
                            InlineKeyboardButton(
                                "🔒 admin/special only",
                                callback_data=f"uc_locked:{uid}:{proto_key}",
                            )
                        )
                    rows.append(row)
                    continue

                row = [
                    InlineKeyboardButton(
                        f"📲 {display} QR",
                        callback_data=f"uc_qr:{uid}:{proto_key}",
                    )
                ]
                # Profile rotation and deletion are also admin/special only.
                if eligible:
                    row.append(
                        InlineKeyboardButton(
                            "♻️ Rotate",
                            callback_data=f"uc_rot:{uid}:{proto_key}",
                        )
                    )
                    row.append(
                        InlineKeyboardButton(
                            "❌ Delete",
                            callback_data=f"uc_del:{uid}:{proto_key}",
                        )
                    )
                rows.append(row)
            else:
                if not eligible:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                "🔒 admin/special only",
                                callback_data=f"uc_locked:{uid}:{proto_key}",
                            )
                        ]
                    )
                elif proto_key == "vless_reality" and xui_on:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                "🛠 Working VLESS — in 3x-ui below",
                                callback_data="uc_xui_hint",
                            )
                        ]
                    )
                elif live and live.live is True:
                    # Create a client only if the protocol is actually running on the VPS.
                    rows.append(
                        [
                            InlineKeyboardButton(
                                f"➕ Create {display}",
                                callback_data=f"uc_create:{uid}:{proto_key}",
                            )
                        ]
                    )
                else:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                f"⚠️ {display} is not running",
                                callback_data="uc_nolive",
                            )
                        ]
                    )

        # “VLESS via 3x-ui” block — add only when integration
        # is actually configured and enabled. For ineligible users
        # show only an info line, no management buttons
        # (same policy as for bot VLESS-Reality).
        if xui_on:
            xui_state = (
                f"✅ working client <code>{he(xui_user_name)}</code> · inbound #{he(xui_inbound_id)}"
                if xui_user_exists
                else f"configured · inbound #{he(xui_inbound_id)} · client not found yet"
            )
            lines.append(f"🛠 <b>VLESS via 3x-ui:</b> " + xui_state)
            xui_btns: list[InlineKeyboardButton] = []
            if eligible:
                xui_btns.append(
                    InlineKeyboardButton(
                        "📲 QR (3x-ui)",
                        callback_data=f"uc_xq:{uid}",
                    )
                )
                xui_btns.append(
                    InlineKeyboardButton(
                        "➕ To 3x-ui",
                        callback_data=f"uc_xc:{uid}",
                    )
                )
                xui_btns.append(
                    InlineKeyboardButton(
                        "❌ From 3x-ui",
                        callback_data=f"uc_xd:{uid}",
                    )
                )
            else:
                xui_btns.append(
                    InlineKeyboardButton(
                        "🔒 admin/special only",
                        callback_data=f"uc_locked:{uid}:xui",
                    )
                )
            rows.append(xui_btns)

        rows.append(
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"uc_back:{uid}"),
                InlineKeyboardButton("✖️ Close", callback_data="uc_x"),
            ]
        )

        text = "\n".join(lines)
        return text, InlineKeyboardMarkup(rows)

    async def _user_card_ttl_job(
        self,
        bot,
        chat_id: int,
        message_id: int,
        key: tuple[int, int],
    ) -> None:
        """Delete the card after USER_CARD_TTL_SECONDS; cancel restarts the countdown."""
        my_task = asyncio.current_task()
        try:
            await asyncio.sleep(self.USER_CARD_TTL_SECONDS)
            try:
                await bot.delete_message(chat_id, message_id)
            except Exception as exc:
                logger.debug(
                    "user_card auto-delete: msg %s/%s already gone: %s",
                    chat_id,
                    message_id,
                    exc,
                )
        except asyncio.CancelledError:
            raise
        finally:
            if self._user_card_ttl_tasks.get(key) is my_task:
                self._user_card_ttl_tasks.pop(key, None)

    def _reschedule_user_card_ttl(self, msg) -> None:
        """Reset the auto-delete timer: each successful show/refresh adds +3 min."""
        if msg is None:
            return
        key = (msg.chat_id, msg.message_id)
        old = self._user_card_ttl_tasks.pop(key, None)
        if old is not None and not old.done():
            old.cancel()
        try:
            task = asyncio.create_task(
                self._user_card_ttl_job(msg.get_bot(), key[0], key[1], key)
            )
            self._user_card_ttl_tasks[key] = task
        except Exception as exc:
            logger.warning("reschedule_user_card_ttl failed: %s", exc)

    async def _show_user_card(
        self,
        target,
        uid: int,
        *,
        edit: bool = False,
    ) -> None:
        """Render the user card.

        target: either update.message (new send) or CallbackQuery.message
                (edit an existing message).
        """
        text, kb = self._user_card_compose(uid)
        card_msg = None
        try:
            if edit:
                await target.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
                card_msg = target
            else:
                card_msg = await target.reply_text(
                    text, parse_mode=ParseMode.HTML, reply_markup=kb
                )
        except Exception as exc:
            logger.warning("_show_user_card: HTML failed: %s; using plain", exc)
            plain = html.unescape(re.sub(r"<[^>]+>", "", text))
            try:
                if edit:
                    await target.edit_text(plain, reply_markup=kb)
                    card_msg = target
                else:
                    card_msg = await target.reply_text(plain, reply_markup=kb)
            except Exception as exc2:
                logger.error("_show_user_card: plain fallback failed: %s", exc2)
        if card_msg is not None:
            self._reschedule_user_card_ttl(card_msg)

    async def user_card_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/user <id> command — opens the user card."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /user <telegram_id>\n"
                    "Example: /user 8288584609\n\n"
                    "Tip: you can just send a numeric TG ID — the bot will recognize it.",
                )
                return
            try:
                uid = int(args[0].strip())
            except (TypeError, ValueError):
                await update.message.reply_text("❌ TG ID must be a number.")
                return
            self._track_user(user)
            await self._show_user_card(update.message, uid, edit=False)
        except Exception as exc:
            logger.error("user_card_command: %s", exc)
            await update.message.reply_text(f"Error: {exc}")

    async def admin_raw_id_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """
        Treat a bare number from an admin in chat as a TG ID and open the card.

        Fires only for admins and only when the message contains
        digits only (regex is on the handler). Ignored for other
        users and does not interfere with normal chat.
        """
        try:
            user = update.effective_user
            if not user or not self._is_admin(user.id):
                return  # silently ignore
            text = (update.message.text or "").strip()
            if not text.isdigit():
                return
            try:
                uid = int(text)
            except ValueError:
                return
            # TG user IDs are usually 5–15 digits; drop numbers that are too short/long
            # so they are not confused with other numeric payloads.
            if not (4 <= len(text) <= 15):
                return
            self._track_user(user)
            await self._show_user_card(update.message, uid, edit=False)
        except Exception as exc:
            logger.error("admin_raw_id_message: %s", exc)

    # === Card actions ===

    @staticmethod
    def _legacy_vless_reexport_text() -> str:
        """Hint to re-issue URI/QR after changing parameters that go into the link."""
        return (
            "\n\n📲 Then re-issue fresh URI/QR to clients:\n"
            "/profiles <id>   or   /my_profile   or   /vless_qr <name>"
        )

    @staticmethod
    def _legacy_vless_host_restart_text(*, port: int | None = None) -> str:
        """What to do on the VPS / in the bot after writing the Xray config (apply without restart)."""
        lines = [
            "",
            "⚠️ Config written, but Xray is still serving the old one. Next:",
            "",
            "On the VPS (SSH / Tabby):",
            "systemctl restart xray",
            "systemctl status xray --no-pager",
        ]
        if port is not None:
            lines.append(f"ufw allow {int(port)}/tcp")
        lines += [
            "",
            "Or from the bot: /xray_restart",
        ]
        return "\n".join(lines) + BotHandlersLite._legacy_vless_reexport_text()

    async def _legacy_vless_apply_followup(
        self, update: Update, *, port: int | None = None
    ) -> None:
        """Apply JSON → host Xray and show SSH/bot next-steps.

        Also automatically purges all previously issued links/QR for all
        users: after changing parameters that go into the vless:// link,
        old links are invalid and must not remain anywhere."""
        apply_ok, apply_msg = vless_manager.apply_xray_config()
        purged = await self._purge_all_profile_messages(update.get_bot())
        follow = self._legacy_vless_host_restart_text(port=port)
        if purged:
            follow += (
                f"\n\n🧹 Old links/QR purged for everyone ({purged}). "
                "Re-issue fresh ones: /profiles <id> or /my_profile."
            )
        await update.message.reply_text(apply_msg + follow)
        if not apply_ok:
            logger.warning("legacy vless apply followup failed: %s", apply_msg)

    @staticmethod
    def _hy2_apply_followup_text(*, port: int | None = None) -> str:
        """Next-steps after hy2_set_*: JSON is not in /etc/hysteria yet."""
        lines = [
            "",
            "➡️ Next in the bot: /hy2_apply",
            "(will write config.yaml and restart hysteria-server)",
        ]
        if port is not None:
            lines.append(f"On the VPS after a port change: ufw allow {int(port)}/udp")
        lines += [
            "",
            "Then re-issue URI/QR: /profiles <id>  or  /my_profile  or  /hy2_qr <name>",
        ]
        return "\n".join(lines)

    async def _user_card_action_create(self, query, uid: int, proto_key: str) -> None:
        """Create a client in the chosen protocol with a deterministic name."""
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Profiles are admin/special only. First /special_add.",
                show_alert=True,
            )
            return
        name = self._build_protocol_profile_name(proto_key, uid)
        ok, msg = False, ""
        try:
            if proto_key == "vless_reality":
                ok, msg, _ = vless_manager.add_client(name)
                if ok:
                    apply_ok, apply_msg = vless_manager.apply_xray_config()
                    msg = f"{msg}\n{apply_msg}{self._legacy_vless_host_restart_text()}"
                    ok = ok and apply_ok
            elif proto_key == "hysteria2":
                ok, msg, _ = hysteria2_manager.add_client(name)
            elif proto_key == "mtproto":
                ok, msg, _ = mtproto_manager.add_client(name)
            elif proto_key == "mieru":
                ok, msg, _ = mieru_manager.add_client(name=name, owner_id=int(uid))
                if ok:
                    msg += "\nℹ️ For the server mita to see the new client: /mieru_apply reload"
            else:
                msg = "Creation is not supported for this protocol"
        except Exception as exc:
            logger.error("uc_create %s/%s: %s", proto_key, uid, exc)
            msg = f"Error: {exc}"
        await query.message.reply_text(
            msg or ("✅ Created" if ok else "❌ Failed to create")
        )
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_delete(
        self, query, uid: int, proto_key: str, confirmed: bool
    ) -> None:
        """Delete a client: first tap is confirm, second is the action."""
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Profile management is admin/special only.",
                show_alert=True,
            )
            return
        name = self._build_protocol_profile_name(proto_key, uid)
        if not confirmed:
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Yes, delete",
                            callback_data=f"uc_delok:{uid}:{proto_key}",
                        ),
                        InlineKeyboardButton(
                            "↩️ Cancel",
                            callback_data=f"uc_back:{uid}",
                        ),
                    ]
                ]
            )
            await query.message.edit_text(
                "❗️ Delete profile "
                f"<code>{html.escape(name)}</code> ({html.escape(proto_key)}) "
                f"for user <code>{html.escape(str(uid))}</code>?\n\n"
                "After deletion the client will stop connecting. This cannot be undone.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            return

        ok, msg = False, ""
        try:
            if proto_key == "vless_reality":
                ok, msg = vless_manager.remove_client(name)
                if ok:
                    apply_ok, apply_msg = vless_manager.apply_xray_config()
                    msg = f"{msg}\n{apply_msg}{self._legacy_vless_host_restart_text()}"
                    ok = ok and apply_ok
            elif proto_key == "hysteria2":
                ok, msg = hysteria2_manager.remove_client(name)
            elif proto_key == "mtproto":
                ok, msg = mtproto_manager.remove_client(name)
            elif proto_key == "mieru":
                ok, msg = mieru_manager.remove_client(name)
                if ok:
                    msg += "\nℹ️ For the server mita to forget the client: /mieru_apply reload"
            else:
                msg = "Deletion is not supported for this protocol"
        except Exception as exc:
            logger.error("uc_delok %s/%s: %s", proto_key, uid, exc)
            msg = f"Error: {exc}"
        await query.message.reply_text(
            msg or ("✅ Deleted" if ok else "❌ Failed to delete")
        )
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_rotate(
        self, query, uid: int, proto_key: str, confirmed: bool
    ) -> None:
        """
        Secret rotation: creates a new client with the same name but a new
        UUID/password/secret. For NaiveProxy — a global basic_auth change.
        """
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Secret rotation is admin/special only.",
                show_alert=True,
            )
            return
        name = self._build_protocol_profile_name(proto_key, uid)
        if not confirmed:
            warn = ""
            if proto_key == "naiveproxy":
                warn = (
                    "\n\n⚠️ NaiveProxy uses a shared basic_auth for all "
                    "clients. This rotation will affect ALL users, "
                    "not only this one."
                )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Yes, rotate",
                            callback_data=f"uc_rotok:{uid}:{proto_key}",
                        ),
                        InlineKeyboardButton(
                            "↩️ Cancel",
                            callback_data=f"uc_back:{uid}",
                        ),
                    ]
                ]
            )
            await query.message.edit_text(
                "♻️ Generate a new secret for "
                f"<code>{html.escape(name)}</code> ({html.escape(proto_key)})?\n\n"
                "The old secret will stop working after rotation." + warn,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            return

        ok, msg = False, ""
        try:
            if proto_key == "naiveproxy":
                ok, msg, _ = naiveproxy_manager.generate_credentials()
                if ok:
                    msg += (
                        "\n\nFor the new credentials to actually apply on the server, "
                        "run `/naive_apply`."
                    )
            else:
                # For VLESS/Hy2/MTProto: remove → add keeps the name but mints a new secret.
                # Delete first, then create. If the first step fails — exit.
                if proto_key == "vless_reality":
                    rm_ok, rm_msg = vless_manager.remove_client(name)
                elif proto_key == "hysteria2":
                    rm_ok, rm_msg = hysteria2_manager.remove_client(name)
                elif proto_key == "mtproto":
                    rm_ok, rm_msg = mtproto_manager.remove_client(name)
                elif proto_key == "mieru":
                    rm_ok, rm_msg = mieru_manager.remove_client(name)
                else:
                    rm_ok, rm_msg = False, "Unknown protocol"
                if not rm_ok:
                    msg = f"Failed to delete the old secret: {rm_msg}"
                else:
                    if proto_key == "vless_reality":
                        ok, add_msg, _ = vless_manager.add_client(name)
                        if ok:
                            apply_ok, apply_msg = vless_manager.apply_xray_config()
                            add_msg = (
                                f"{add_msg}\n{apply_msg}"
                                f"{self._legacy_vless_host_restart_text()}"
                            )
                            ok = ok and apply_ok
                    elif proto_key == "hysteria2":
                        ok, add_msg, _ = hysteria2_manager.add_client(name)
                    elif proto_key == "mieru":
                        ok, add_msg, _ = mieru_manager.add_client(
                            name=name, owner_id=int(uid)
                        )
                        if ok:
                            add_msg += (
                                "\nℹ️ For the new secret to apply on mita: "
                                "/mieru_apply reload"
                            )
                    else:
                        ok, add_msg, _ = mtproto_manager.add_client(name)
                    msg = ("♻️ Secret rotated\n" + add_msg) if ok else add_msg
        except Exception as exc:
            logger.error("uc_rotok %s/%s: %s", proto_key, uid, exc)
            msg = f"Rotation error: {exc}"
        await query.message.reply_text(
            msg or ("✅ Done" if ok else "❌ Failed to rotate")
        )
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_xui_create(self, query, uid: int) -> None:
        """Create a VLESS client in 3x-ui (default inbound) with email = profile name."""
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Profiles are admin/special only.", show_alert=True
            )
            return
        client_obj, err = xui_manager.make_client_or_error()
        if client_obj is None:
            await query.message.reply_text(f"❌ {err}")
            return
        snap = xui_manager.status_summary()
        inbound_id = int(snap.get("default_inbound_id") or 0)
        if not inbound_id:
            await query.message.reply_text(
                "❌ /xui_status has no default inbound selected.\n"
                "Use /xui_set_inbound <id>."
            )
            return
        try:
            email = provision_manager.make_client_name("vless", int(uid))
        except Exception:
            email = self._build_protocol_profile_name("vless_reality", uid)
        ok, msg, created = client_obj.add_vless_reality_client(inbound_id, email)
        if ok:
            uuid_short = (created.get("id") or "")[:8]
            await query.message.reply_text(
                f"✅ Client created in 3x-ui (inbound #{inbound_id}).\n"
                f"email: <code>{html.escape(email)}</code>\n"
                f"uuid: <code>{html.escape(uuid_short)}…</code>",
                parse_mode=ParseMode.HTML,
            )
        elif msg == "exists":
            uuid_short = (created.get("id") or "")[:8]
            await query.message.reply_text(
                "ℹ️ Client "
                f"<code>{html.escape(email)}</code> already exists in 3x-ui "
                f"(uuid <code>{html.escape(uuid_short)}…</code>).",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.message.reply_text(f"❌ {msg}")
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_xui_delete(
        self, query, uid: int, confirmed: bool
    ) -> None:
        """Delete a VLESS client from 3x-ui by email."""
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Profile management is admin/special only.", show_alert=True
            )
            return
        try:
            email_hint = provision_manager.make_client_name("vless", int(uid))
        except Exception:
            email_hint = self._build_protocol_profile_name("vless_reality", uid)
        if not confirmed:
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Yes, delete",
                            callback_data=f"uc_xdok:{uid}",
                        ),
                        InlineKeyboardButton(
                            "↩️ Cancel",
                            callback_data=f"uc_back:{uid}",
                        ),
                    ]
                ]
            )
            await query.message.edit_text(
                "❗️ Delete client "
                f"<code>{html.escape(email_hint)}</code> from 3x-ui (default inbound)?\n\n"
                "This action cannot be undone.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            return
        client_obj, err = xui_manager.make_client_or_error()
        if client_obj is None:
            await query.message.reply_text(f"❌ {err}")
            return
        snap = xui_manager.status_summary()
        inbound_id = int(snap.get("default_inbound_id") or 0)
        if not inbound_id:
            await query.message.reply_text(
                "❌ /xui_status has no default inbound selected."
            )
            return
        # 3x-ui requires UUID, not email — find the client (canon, then legacy).
        found, c, email_used, inbound_resolved = self._xui_find_vless_client(
            client_obj, uid
        )
        if not found:
            await query.message.reply_text(
                "ℹ️ Client "
                f"<code>{html.escape(str(email_used))}</code> was not found in 3x-ui — maybe already deleted.",
                parse_mode=ParseMode.HTML,
            )
            await self._show_user_card(query.message, uid, edit=True)
            return
        ok, msg = client_obj.del_client(inbound_resolved, str(c.get("id") or ""))
        if ok:
            await query.message.reply_text(
                f"✅ Client <code>{html.escape(str(email_used))}</code> deleted from 3x-ui.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.message.reply_text(f"❌ {msg}")
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_xui_qr(self, query, uid: int) -> None:
        """Send a VLESS URI and QR from 3x-ui (or an explanation)."""
        client_obj, err = xui_manager.make_client_or_error()
        if client_obj is None:
            await query.message.reply_text(f"❌ {err}")
            return
        snap = xui_manager.status_summary()
        inbound_id = int(snap.get("default_inbound_id") or 0)
        if not inbound_id:
            await query.message.reply_text(
                "❌ No default inbound selected (see /xui_set_inbound)."
            )
            return
        found, client, email_used, inbound_resolved = self._xui_find_vless_client(
            client_obj, uid
        )
        if not found:
            await query.message.reply_text(
                f"ℹ️ Client {email_used} was not found in 3x-ui.\n"
                f"First tap “➕ To 3x-ui” or run /provision {uid}"
            )
            return
        ok_ib, msg_ib, inbound = client_obj.get_inbound(inbound_resolved)
        if not ok_ib:
            await query.message.reply_text(f"❌ get_inbound: {msg_ib}")
            return
        fallback = xui_manager.panel_host(snap.get("base_url", ""))
        ok, msg, link = xui_manager.build_vless_reality_link(
            inbound, client, fallback_host=fallback
        )
        if not ok:
            await query.message.reply_text(
                f"❌ Failed to build the link: {msg}\n"
                "Copy the link/QR directly from the 3x-ui panel."
            )
            return
        # HTML: legacy Markdown broke on '_' in email (Vless_ID82_09) and on URIs.
        await query.message.reply_text(
            "📲 VLESS via 3x-ui "
            f"(<code>{html.escape(str(email_used))}</code>)\n"
            f"<code>{html.escape(link)}</code>",
            parse_mode=ParseMode.HTML,
        )
        try:
            from io import BytesIO

            import qrcode  # type: ignore

            buf = BytesIO()
            img = qrcode.make(link)
            img.save(buf, format="PNG")
            buf.seek(0)
            buf.name = f"xui-{email_used}.png"
            await query.message.reply_photo(photo=buf)
        except Exception as exc:
            logger.warning("xui qr render failed: %s", exc)

    async def _user_card_action_qr(self, query, uid: int, proto_key: str) -> None:
        """Send a client QR/URI."""
        name = self._build_protocol_profile_name(proto_key, uid)
        try:
            if proto_key == "vless_reality":
                await self._reply_vless_qr(query.message, name)
            elif proto_key == "hysteria2":
                await self._reply_hy2_qr(query.message, name)
            elif proto_key == "mtproto":
                await self._reply_mt_qr(query.message, name)
            elif proto_key == "naiveproxy":
                try:
                    uri = naiveproxy_manager.build_client_uri()
                except ValueError as exc:
                    await query.message.reply_text(f"❌ {exc}")
                    return
                await query.message.reply_text(
                    "🌐 NaiveProxy URI (shared by all clients):\n"
                    f"<code>{html.escape(uri)}</code>",
                    parse_mode=ParseMode.HTML,
                )
            elif proto_key == "mieru":
                await self._reply_mieru_qr(query.message, name)
            else:
                await query.message.reply_text(
                    "❌ QR/URI is not supported for this protocol"
                )
        except Exception as exc:
            logger.error("uc_qr %s/%s: %s", proto_key, uid, exc)
            await query.message.reply_text(f"Failed to prepare QR: {exc}")

    async def _handle_user_card_callbacks(self, query, data: str) -> bool:
        """Router for user-card callbacks (`uc_*`)."""
        if not data.startswith("uc_"):
            return False
        try:
            if data == "uc_x":
                try:
                    await query.message.delete()
                except Exception:
                    await query.message.edit_text("✖️ Closed.")
                return True

            if data == "uc_nolive":
                await query.answer(
                    "The protocol is not running on this VPS. Bring the service up first, then /diag.",
                    show_alert=True,
                )
                return True

            if data == "uc_xui_hint":
                await query.answer(
                    "This VLESS is issued via the 3x-ui block: use QR (3x-ui) or ➕ To 3x-ui.",
                    show_alert=True,
                )
                return True

            if data.startswith("uc_locked:"):
                # Tapped “🔒 admin/special only” — explain the rule.
                await query.answer(
                    "Profiles are issued only to admins and special users. "
                    "First /special_add <id>.",
                    show_alert=True,
                )
                return True

            # 3x-ui actions: format `uc_x<c|d|dok|q>:<uid>` (no proto_key).
            if (
                data.startswith("uc_xc:")
                or data.startswith("uc_xd:")
                or data.startswith("uc_xdok:")
                or data.startswith("uc_xq:")
            ):
                head, _, tail = data.partition(":")
                try:
                    uid = int(tail)
                except (TypeError, ValueError):
                    return True
                if head == "uc_xc":
                    await self._user_card_action_xui_create(query, uid)
                elif head == "uc_xd":
                    await self._user_card_action_xui_delete(query, uid, confirmed=False)
                elif head == "uc_xdok":
                    await self._user_card_action_xui_delete(query, uid, confirmed=True)
                elif head == "uc_xq":
                    await self._user_card_action_xui_qr(query, uid)
                return True

            head, _, tail = data.partition(":")
            parts = tail.split(":")

            if head == "uc_back" and len(parts) == 1:
                uid = int(parts[0])
                await self._show_user_card(query.message, uid, edit=True)
                return True

            if len(parts) != 2:
                return True
            uid_str, proto_key = parts
            uid = int(uid_str)

            if head == "uc_create":
                await self._user_card_action_create(query, uid, proto_key)
                return True
            if head == "uc_qr":
                await self._user_card_action_qr(query, uid, proto_key)
                return True
            if head == "uc_del":
                await self._user_card_action_delete(
                    query, uid, proto_key, confirmed=False
                )
                return True
            if head == "uc_delok":
                await self._user_card_action_delete(
                    query, uid, proto_key, confirmed=True
                )
                return True
            if head == "uc_rot":
                await self._user_card_action_rotate(
                    query, uid, proto_key, confirmed=False
                )
                return True
            if head == "uc_rotok":
                await self._user_card_action_rotate(
                    query, uid, proto_key, confirmed=True
                )
                return True
            if head == "uc_setup":
                # Hint for unconfigured protocols: show the operator
                # the exact setup commands instead of trying to do everything in the UI.
                hints = {
                    "naiveproxy": (
                        "🌐 NaiveProxy is not configured yet. Basic playbook:\n\n"
                        "1) <code>/naive_set_domain your_domain</code>\n"
                        "2) <code>/naive_gen_creds</code>\n"
                        "3) <code>/naive_apply</code>\n\n"
                        "The domain must point at this VPS; Cloudflare proxy — OFF."
                    ),
                }
                await query.message.reply_text(
                    hints.get(proto_key, "Configure the protocol via its menu first."),
                    parse_mode=ParseMode.HTML,
                )
                return True
        except Exception as exc:
            logger.error("_handle_user_card_callbacks(%r): %s", data, exc)
            try:
                await query.message.reply_text(f"❌ Error: {exc}")
            except Exception:
                pass
            return True
        return False

    # === 3x-ui integration (external Xray control panel) ===
    #
    # The bot can optionally call the 3x-ui panel REST API (see
    # `xui_manager.py`). Credentials (URL, login, password, default inbound) are
    # entered by admin via the ConversationHandler below. Password:
    #   * is **never** printed in the bot chat;
    #   * the password message is deleted immediately after receipt (if the bot
    #     has enough rights in this chat);
    #   * is stored only in xui_config.json as AES-256-GCM,
    #     encrypted via `SecureMessenger` with the same key as
    #     the rest of the project secrets (`ENCRYPTION_KEY` / `API_SECRET_KEY`).
    #
    # Without these credentials the bot only detects whether
    # 3x-ui is present (`live_status._xui_panel_status`) and shows a soft
    # warning on the /user card. Full client CRUD works
    # only after `/xui_setup`.

    # ConversationHandler states for /xui_setup. Integer values
    # are fixed on the class object — bot.py reads them when building
    # the ConversationHandler.
    XUI_URL, XUI_USER, XUI_PWD, XUI_VERIFY, XUI_INBOUND = range(5)

    async def xui_setup_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """`/xui_setup` — start the step-by-step setup. Admin only."""
        from telegram.ext import ConversationHandler

        user = update.effective_user
        if not user or not self._is_admin(user.id):
            await update.message.reply_text("⛔ Admin only.")
            return ConversationHandler.END

        if not xui_manager.encryption_available():
            await update.message.reply_text(
                "❌ `.env` has no `ENCRYPTION_KEY` (or `API_SECRET_KEY`).\n"
                "Without it the 3x-ui password cannot be encrypted. Add a key "
                "to `.env`, restart the bot, then run `/xui_setup`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return ConversationHandler.END

        context.user_data["xui_setup"] = {}
        await update.message.reply_text(
            "🛠 3x-ui integration setup (1/4)\n\n"
            "Send the panel URL in one message. Example:\n"
            "`https://195.238.122.137:35421/mxmurl`\n\n"
            "`http://` and `https://` are both allowed. A trailing `/` can "
            "be omitted — it will be stripped.\n\n"
            "If you changed your mind — `/xui_cancel`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return self.XUI_URL

    async def xui_setup_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = (update.message.text or "").strip()
        ok, normalized, msg = xui_manager.normalize_base_url(raw)
        if not ok:
            await update.message.reply_text(
                f"❌ {msg}\nTry again or /xui_cancel."
            )
            return self.XUI_URL
        context.user_data.setdefault("xui_setup", {})["base_url"] = normalized
        # Detect mesh-IP (Tailscale/Headscale CGNAT). The bot in bridge-mode
        # cannot see the mesh — warn IMMEDIATELY so admin can
        # switch network_mode before entering the password.
        mesh_warning = ""
        try:
            if xui_manager.url_host_is_mesh_ip(normalized):
                mesh_warning = (
                    "\n\n⚠️ This URL is a <b>mesh-IP</b> "
                    "(Tailscale/Headscale, 100.64/10 or ULA).\n"
                    "The bot in Docker defaults to the bridge network and "
                    "will <b>not see</b> the host mesh interface.\n\n"
                    "If login on the next step fails with "
                    "<i>network: connection refused / timeout</i>, "
                    "switch the container to host networking:\n"
                    "<code>compose.yaml → telegram-helper → "
                    "network_mode: host</code> "
                    "(remove the <code>ports:</code> block)\n"
                    "and recreate: "
                    "<code>docker compose up -d --force-recreate</code>."
                )
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ URL accepted: <code>{html.escape(normalized)}</code>\n\n"
            "Step 2/4. Enter the panel admin <b>login</b>." + mesh_warning,
            parse_mode=ParseMode.HTML,
        )
        return self.XUI_USER

    async def xui_setup_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = (update.message.text or "").strip()
        if not name:
            await update.message.reply_text(
                "Login is empty. Enter it again or /xui_cancel."
            )
            return self.XUI_USER
        context.user_data.setdefault("xui_setup", {})["username"] = name
        await update.message.reply_text(
            "Step 3/4. Enter the *password* as a single line.\n\n"
            "⚠️ Right after receipt the password message will be deleted "
            "(if the bot has enough rights in this chat). It is not stored in chat "
            "and does not appear in logs.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return self.XUI_PWD

    async def xui_setup_pwd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pwd = update.message.text or ""
        chat_id = update.effective_chat.id
        # IMMEDIATELY try to delete the password message. This is the best protection —
        # if permissions allow, the password does not stay in chat history.
        try:
            await update.message.delete()
        except Exception as exc:
            logger.warning("xui_setup_pwd: delete pwd message failed: %s", exc)

        if not pwd:
            await context.bot.send_message(
                chat_id, "Password is empty. Enter it again or /xui_cancel."
            )
            return self.XUI_PWD

        context.user_data.setdefault("xui_setup", {})["password"] = pwd

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔓 self-signed (do not verify TLS)",
                        callback_data="xui_vfy_no",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔒 valid TLS (verify)",
                        callback_data="xui_vfy_yes",
                    )
                ],
                [InlineKeyboardButton("✖️ Cancel", callback_data="xui_vfy_cancel")],
            ]
        )
        await context.bot.send_message(
            chat_id,
            "Password accepted and encrypted in memory.\n\n"
            "Step 4/4. Verify the panel TLS certificate?\n"
            "For self-signed (typical 3x-ui on an IP) — pick "
            "the first option.",
            reply_markup=kb,
        )
        return self.XUI_VERIFY

    async def xui_setup_verify(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        from telegram.ext import ConversationHandler

        query = update.callback_query
        await query.answer()
        setup = context.user_data.get("xui_setup", {})

        if query.data == "xui_vfy_cancel":
            context.user_data.pop("xui_setup", None)
            await query.message.edit_text("Cancelled. Credentials were not saved.")
            return ConversationHandler.END

        verify_tls = query.data == "xui_vfy_yes"
        setup["verify_tls"] = verify_tls

        # Do a test login + fetch the inbound list in one step.
        client = xui_manager.XUIClient(
            base_url=setup.get("base_url", ""),
            username=setup.get("username", ""),
            password=setup.get("password", ""),
            verify_tls=verify_tls,
        )
        ok, msg = client.login()
        if not ok:
            # If the error looks like a network issue (connection refused/timed out
            # / no route / network) AND the URL is a mesh-IP, give a precise
            # host-networking instruction, otherwise a generic hint.
            base_url = setup.get("base_url", "")
            is_network_err = bool(
                msg
                and any(
                    s in str(msg).lower()
                    for s in (
                        "network:",
                        "connection refused",
                        "timed out",
                        "no route",
                        "name or service not known",
                        "failed to establish",
                    )
                )
            )
            is_mesh = False
            try:
                is_mesh = xui_manager.url_host_is_mesh_ip(base_url)
            except Exception:
                pass
            if is_network_err and is_mesh:
                hint = (
                    "🛰 The URL points at a <b>mesh-IP</b> "
                    "(Tailscale/Headscale, 100.64/10 or ULA), "
                    "and the bot could not reach it. "
                    "Most likely the container is on the bridge network and cannot see "
                    "the host mesh interface.\n\n"
                    "<b>Fix:</b> switch the container to host networking.\n"
                    "In <code>compose.yaml</code> for the "
                    "<code>telegram-helper</code> service add:\n"
                    "<pre>    network_mode: host</pre>"
                    "and remove the <code>ports:</code> block (it is unused and "
                    "conflicts with host-mode). Recreate:\n"
                    "<pre>docker compose up -d --force-recreate "
                    "telegram-helper</pre>"
                    "Then retry <code>/xui_setup</code> with the same URL."
                )
                await query.message.edit_text(
                    f"❌ 3x-ui login failed: <code>{html.escape(str(msg))}</code>\n\n"
                    + hint,
                    parse_mode=ParseMode.HTML,
                )
            else:
                await query.message.edit_text(
                    f"❌ 3x-ui login failed: <code>{html.escape(str(msg))}</code>\n\n"
                    "Check URL/login/password and run "
                    "<code>/xui_setup</code> again.",
                    parse_mode=ParseMode.HTML,
                )
            context.user_data.pop("xui_setup", None)
            return ConversationHandler.END

        ok, msg, inbounds = client.list_inbounds()
        if not ok:
            await query.message.edit_text(
                f"❌ Failed to get the inbound list: {msg}\n\n"
                "Check panel admin rights and run /xui_setup again."
            )
            context.user_data.pop("xui_setup", None)
            return ConversationHandler.END

        if not inbounds:
            await query.message.edit_text(
                "⚠️ The panel returned no inbounds.\n\n"
                "First create a VLESS-Reality inbound in 3x-ui, then "
                "retry /xui_setup."
            )
            context.user_data.pop("xui_setup", None)
            return ConversationHandler.END

        rows: list = []
        for ib in inbounds:
            try:
                ib_id = int(ib.get("id"))
            except (TypeError, ValueError):
                continue
            ib_name = ib.get("remark") or f"id={ib_id}"
            ib_proto = ib.get("protocol", "?")
            ib_port = ib.get("port", "?")
            label = f"#{ib_id} {ib_proto}:{ib_port} — {ib_name}"[:60]
            rows.append([InlineKeyboardButton(label, callback_data=f"xui_ib:{ib_id}")])
        rows.append([InlineKeyboardButton("✖️ Cancel", callback_data="xui_ib_cancel")])

        await query.message.edit_text(
            f"✅ Login succeeded, inbounds found: {len(inbounds)}.\n\n"
            f"Pick the default inbound — the bot will add "
            f"VLESS clients from the `/user` card into it. For VLESS-Reality "
            f"pick the matching VLESS inbound.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return self.XUI_INBOUND

    async def xui_setup_inbound(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        from telegram.ext import ConversationHandler

        query = update.callback_query
        await query.answer()
        if query.data == "xui_ib_cancel":
            context.user_data.pop("xui_setup", None)
            await query.message.edit_text("Cancelled. Credentials were not saved.")
            return ConversationHandler.END
        if not query.data.startswith("xui_ib:"):
            return self.XUI_INBOUND
        try:
            inbound_id = int(query.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer("Invalid inbound ID", show_alert=True)
            return self.XUI_INBOUND

        setup = context.user_data.get("xui_setup", {})
        ok, save_msg = xui_manager.save_credentials(
            base_url=setup.get("base_url", ""),
            username=setup.get("username", ""),
            password=setup.get("password", ""),
            verify_tls=bool(setup.get("verify_tls", False)),
            default_inbound_id=inbound_id,
        )
        # Clear the password from user_data (even if save failed).
        setup["password"] = ""
        url = setup.get("base_url", "")
        username = setup.get("username", "")
        context.user_data.pop("xui_setup", None)

        if not ok:
            await query.message.edit_text(f"❌ Failed to save: {save_msg}")
            return ConversationHandler.END

        # HTML mode is more stable than legacy Markdown — Markdown breaks on
        # underscores in `/xui_status`, `xui_config.json`, etc.
        await query.message.edit_text(
            "✅ 3x-ui integration configured\n\n"
            f"URL: <code>{html.escape(url)}</code>\n"
            f"Login: <code>{html.escape(username)}</code> "
            "(password encrypted in xui_config.json)\n"
            f"Default inbound: #{inbound_id}\n\n"
            "Commands: /xui_status · /xui_list · /xui_disable · /xui_clear",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    async def xui_setup_cancel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        from telegram.ext import ConversationHandler

        context.user_data.pop("xui_setup", None)
        if update.message:
            await update.message.reply_text("Cancelled. 3x-ui credentials were not saved.")
        return ConversationHandler.END

    async def xui_status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/xui_status` — integration state (no secrets)."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        snap = xui_manager.status_summary()
        if not snap["configured"]:
            await update.message.reply_text(
                "⚪ 3x-ui integration is not configured. Run /xui_setup."
            )
            return
        head = "🟢 on" if snap["enabled"] else "🔴 off"
        text = (
            f"🛠 <b>3x-ui integration:</b> {head}\n\n"
            f"URL: <code>{html.escape(snap['base_url'])}</code>\n"
            f"Login: <code>{html.escape(snap['username_masked'])}</code>\n"
            f"TLS verify: {snap['verify_tls']}\n"
            f"Default inbound: #{snap['default_inbound_id']}\n"
            f"Configured: {html.escape(snap['configured_at'] or '—')}"
        )
        # Live connectivity check with the panel.
        if snap["enabled"]:
            client, err = xui_manager.make_client_or_error()
            if client is None:
                text += f"\n\n⚠️ {html.escape(err or '')}"
            else:
                ok, msg = client.login()
                if ok:
                    ok2, _msg2, inbounds = client.list_inbounds()
                    text += "\n\n✅ Panel connectivity OK" + (
                        f", inbounds: {len(inbounds)}" if ok2 else ""
                    )
                    if msg and "auto-fallback" in msg:
                        # login() already rewrote base_url in xui_config.json
                        text += (
                            "\n🔄 Panel URL automatically switched from "
                            "mesh to loopback (Tailscale was unavailable).\n"
                            f"Now: <code>{html.escape(client.base_url)}</code>"
                        )
                else:
                    text += f"\n\n❌ Login failed: {html.escape(msg or '')}"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def xui_list_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/xui_list` — inbound list (for /xui_set_inbound)."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        client, err = xui_manager.make_client_or_error()
        if client is None:
            await update.message.reply_text(f"❌ {err}")
            return
        ok, msg = client.login()
        if not ok:
            await update.message.reply_text(f"❌ Login: {msg}")
            return
        ok, msg, inbounds = client.list_inbounds()
        if not ok:
            await update.message.reply_text(f"❌ list_inbounds: {msg}")
            return
        if not inbounds:
            await update.message.reply_text(
                "No inbounds. Create them in the 3x-ui panel itself."
            )
            return
        snap = xui_manager.status_summary()
        default_id = snap["default_inbound_id"]
        lines = ["🛠 <b>3x-ui inbounds:</b>", ""]
        for ib in inbounds:
            ib_id = ib.get("id", "?")
            mark = " ⭐" if ib_id == default_id else ""
            proto = html.escape(str(ib.get("protocol", "?")))
            port = html.escape(str(ib.get("port", "?")))
            remark = html.escape(str(ib.get("remark") or "—"))
            lines.append(
                f"• #{ib_id}{mark} — <code>{proto}</code>:<code>{port}</code> — {remark}"
            )
        lines.append("")
        lines.append("Change default: <code>/xui_set_inbound &lt;id&gt;</code>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def xui_set_inbound_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/xui_set_inbound <id>` — set the default inbound."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Usage: <code>/xui_set_inbound &lt;id&gt;</code>\n"
                "List: /xui_list",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            inbound_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ ID must be a number.")
            return
        ok, msg = xui_manager.set_default_inbound(inbound_id)
        if ok:
            await update.message.reply_text(f"✅ Default inbound: #{inbound_id}")
        else:
            await update.message.reply_text(f"❌ {msg}")

    async def xui_enable_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        ok, msg = xui_manager.set_enabled(True)
        await update.message.reply_text(
            "✅ 3x-ui integration enabled" if ok else f"❌ {msg}"
        )

    async def xui_disable_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        ok, msg = xui_manager.set_enabled(False)
        await update.message.reply_text(
            "🔴 3x-ui integration disabled (credentials kept)" if ok else f"❌ {msg}"
        )

    async def xui_clear_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/xui_clear` — wipe panel credentials after confirmation."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        args = context.args or []
        if not args or args[0].strip().upper() != "YES":
            await update.message.reply_text(
                "❗️ This will delete saved 3x-ui URL/login/password from "
                "<code>xui_config.json</code>.\n\n"
                "Confirm: <code>/xui_clear YES</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        ok, msg = xui_manager.clear_credentials()
        await update.message.reply_text(
            "🗑 3x-ui credentials deleted. Run /xui_setup when needed."
            if ok
            else f"❌ {msg}"
        )

    # === Bot-managed provisioning ===
    # Canonical client names: <Prefix>_ID<first2>_<last2> from Telegram-ID.
    # All enabled protocols are processed except NaiveProxy
    # (single-cred model). VLESS-Reality pending → handled in Phase 2.

    def _picker_privileged_and_regular_counts(self) -> tuple[int, int]:
        """Counters for the picker hint: admin∪special vs regular."""
        special_ids, users_data = storage_list_users()
        privileged = set(self.config.resolved_admin_user_ids()) | set(special_ids)
        regular_n = sum(1 for uid in users_data if int(uid) not in privileged)
        return len(privileged), regular_n

    def _picker_scope_hint_html(self) -> str:
        """Explain why regular users are not in the buttons."""
        priv_n, regular_n = self._picker_privileged_and_regular_counts()
        return (
            f"ℹ️ Buttons show only <b>admin + special</b> "
            f"(currently {priv_n}).\n"
            f"Regular users in the DB: <b>{regular_n}</b> — they get no VPN profiles "
            f"until you run "
            f"<code>/special_add &lt;id&gt;</code>, then "
            f"<code>/provision &lt;id&gt;</code>.\n"
            f"See all IDs in <code>/list_users</code>; you can also "
            f"run <code>/profiles &lt;id&gt;</code> manually."
        )

    def _build_user_picker_kb(self, action_prefix: str) -> InlineKeyboardMarkup:
        """InlineKeyboard with known TG IDs (admin ∪ special).

        Each button is `First Last (ID)` or `@username (ID)` from
        `users.json`. callback_data: `{action_prefix}:{uid}` or
        `{action_prefix}:cancel`. Used as autocomplete for
        `/provision`, `/profiles`, `/clean_user` with no argument.

        Regular users are intentionally excluded: bot-managed profiles
        are issued only to privileged roles (see `_picker_scope_hint_html`).
        """
        special_ids, users_data = storage_list_users()
        target_ids = sorted(
            set(self.config.resolved_admin_user_ids()) | set(special_ids)
        )
        rows: list = []
        for tid in target_ids:
            meta = users_data.get(int(tid), {}) or {}
            first = (meta.get("first_name") or "").strip()
            last = (meta.get("last_name") or "").strip()
            full_name = (first + " " + last).strip()
            username = (meta.get("username") or "").strip()
            if full_name:
                label = f"{full_name} ({tid})"
            elif username:
                label = f"@{username} ({tid})"
            else:
                label = str(tid)
            rows.append(
                [
                    InlineKeyboardButton(
                        label[:60], callback_data=f"{action_prefix}:{tid}"
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    "📒 All users (/list_users)",
                    callback_data=f"{action_prefix}:list_users",
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton("✖ Cancel", callback_data=f"{action_prefix}:cancel")]
        )
        return InlineKeyboardMarkup(rows)

    async def provision_picker_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Callback for provisioning-command inline pickers.

        Handles three prefixes:
            prov_pick:<uid>   run provision_user(<uid>)
            prof_pick:<uid>   show profiles_for_user(<uid>)
            clean_pick:<uid>  run clean_user(<uid>) (the tap is the
                              confirmation — the button already warns)
        Plus `<prefix>:cancel` → “Cancelled”.

        Admin only: non-admins get no reply (safe-fail —
        the callback is registered in bot.py before the generic handler).
        """
        query = update.callback_query
        await query.answer()
        if not self._is_admin(query.from_user.id):
            return
        data = query.data or ""
        if ":" not in data:
            return
        action_prefix, payload = data.split(":", 1)
        if payload == "cancel":
            try:
                await query.message.edit_text("Cancelled.")
            except Exception:
                pass
            return
        if payload == "list_users":
            # Hint button from the picker: regular users are not in the button list.
            try:
                await query.message.edit_text(
                    "📒 To see <b>regular</b> users "
                    "(and all roles), open:\n"
                    "<code>/list_users</code>\n\n"
                    + self._picker_scope_hint_html(),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return
        try:
            target_id = int(payload)
        except (TypeError, ValueError):
            return

        try:
            await query.message.edit_text(
                f"⏳ Running for <code>{target_id}</code>…",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        if action_prefix == "prov_pick":
            results = provision_manager.provision_user(target_id)
            try:
                storage_reset_my_profile_views(target_id)
            except Exception as exc:
                logger.warning("reset_my_profile_views(%s) failed: %s", target_id, exc)
            text = self._format_provision_results(
                target_id,
                results,
                header="🛠 <b>Provisioning for TG ID:</b>",
                mode="provision",
            )
            text += self._vless_firewall_hint()
            edited = await query.message.edit_text(text, parse_mode=ParseMode.HTML)
            self._schedule_admin_msg_ttl(edited, user_id=target_id)
            await self._send_qrs_for_results(query.message, results, user_id=target_id)
            return

        if action_prefix == "prof_pick":
            profiles = provision_manager.profiles_for_user(target_id)
            text = self._format_provision_results(
                target_id,
                profiles,
                header="📋 <b>Profiles for TG ID:</b>",
                mode="profiles",
            )
            none_existing = not any(p.get("exists") for p in profiles.values())
            if none_existing:
                text += (
                    f"\n\nNo profiles created. "
                    f"Run <code>/provision {target_id}</code>."
                )
            edited = await query.message.edit_text(text, parse_mode=ParseMode.HTML)
            self._schedule_admin_msg_ttl(edited, user_id=target_id)
            if not none_existing:
                await self._send_qrs_for_results(
                    query.message, profiles, user_id=target_id
                )
            return

        if action_prefix == "clean_pick":
            await self._delete_tracked_profile_messages(context.bot, target_id)
            results = provision_manager.clean_user(target_id)
            try:
                storage_reset_my_profile_views(target_id)
                storage_clear_my_profile_messages(target_id)
            except Exception as exc:
                logger.warning(
                    "reset my_profile state for %s failed: %s", target_id, exc
                )
            text = self._format_provision_results(
                target_id,
                results,
                header="🗑 <b>Cleanup for TG ID:</b>",
                mode="clean",
            )
            await query.message.edit_text(text, parse_mode=ParseMode.HTML)
            return

    @staticmethod
    def _xui_is_active() -> bool:
        """3x-ui integration is configured AND enabled. Used by legacy
        VLESS commands as a routing flag: if active — pull data
        from the panel and show local `vless_config.json` as fallback."""
        try:
            return xui_manager.is_configured() and xui_manager.is_enabled()
        except Exception:
            return False

    async def _legacy_per_client_guard(self, update: Update) -> bool:
        """Per-client legacy commands (`/<proto>_add_client`,
        `<proto>_del_client`, `/<proto>_qr <name>`) for Hys/Mtp/Tuic/
        AnyTLS/XHTTP always redirect to the unified bot-managed flow.

        After Stage 3 this removes “two paths to the same data”: one
        source of truth for per-user provisioning — `provision_manager`
        with canonical names `<Prefix>_ID<first2>_<last2>`. Per-inbound
        config (`/<proto>_set_*`, `/<proto>_gen_*`, `/<proto>_status`,
        `<proto>_export`) is left alone — that is a legitimate admin path.
        """
        text = (
            "🛑 Per-client operations now go through the unified "
            "<b>bot-managed flow</b> with canonical names.\n\n"
            "• <code>/provision &lt;user_id&gt;</code> — create profiles "
            "in all enabled protocols at once\n"
            "• <code>/profiles &lt;user_id&gt;</code> — view URL and QR\n"
            "• <code>/clean_user &lt;user_id&gt; YES</code> — delete\n"
            "• <code>/email_profile &lt;user_id&gt;</code> — send by email\n\n"
            "A read-only list of all bot-managed clients for the protocol "
            "is still available via <code>/&lt;proto&gt;_list_clients</code>."
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return True

    async def _legacy_vless_guard(self, update: Update, action_kind: str) -> bool:
        """If xui is active — send a redirect message for `action_kind`
        and return True. The caller must `return` immediately if True.

        action_kind:
          'config'   — inbound setup (server/port/keys/sni/fingerprint/...)
                       — done directly in the 3x-ui panel;
          'client'   — client management (add/del) — via
                       /provision and /clean_user;
          'service'  — on/off/test/sync/reset/qr — the real xray
                       is started by 3x-ui, not the bot.
        """
        if not self._xui_is_active():
            return False
        try:
            base_url = (xui_manager.load_config().get("base_url") or "").strip()
        except Exception:
            base_url = ""
        url_html = (
            f"<code>{html.escape(base_url)}</code>" if base_url else "<i>not set</i>"
        )
        if action_kind == "config":
            text = (
                "🛑 This command edits the local <code>vless_config.json</code> "
                "of the bot (legacy host-Xray flow).\n\n"
                "On this VPS <b>3x-ui integration</b> is active — all inbound "
                "settings (server/port/UUID/Reality keys/SNI/fingerprint) "
                "should be done <b>directly in the panel</b>: " + url_html + "\n\n"
                "Status: /vless_status · /xui_status · /xui_list"
            )
        elif action_kind == "client":
            text = (
                "🛑 Client management on this VPS goes through the "
                "<b>bot-managed flow</b> (3x-ui integration is active).\n\n"
                "• <code>/provision &lt;user_id&gt;</code> — create a profile\n"
                "• <code>/profiles &lt;user_id&gt;</code> — view existing ones\n"
                "• <code>/clean_user &lt;user_id&gt; YES</code> — delete\n"
                "• <code>/email_profile &lt;user_id&gt;</code> — send by email\n\n"
                "List of all bot-managed clients: /vless_list_clients"
            )
        else:  # service
            text = (
                "🛑 This command manages the local <code>xray.service</code> "
                "of the bot (legacy host-Xray). On this VPS the real xray "
                "is run by the <b>3x-ui panel</b>: " + url_html + "\n\n"
                "Manage the service via the panel / 3x-ui CLI on the server."
            )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return True

    @staticmethod
    def _count_inbound_clients(inbound) -> int:
        """Client count in the inbound (settings is a JSON string)."""
        try:
            s = inbound.get("settings") or "{}"
            if isinstance(s, str):
                s = json.loads(s)
            clients = s.get("clients") or []
            return len(clients) if isinstance(clients, list) else 0
        except Exception:
            return 0

    @staticmethod
    def _count_canon_clients(inbound) -> int:
        """How many inbound clients have a canonical name
        (`<Prefix>_ID<two>_<two>` from the bot-managed flow). Used in
        the overview so admin can see the manual / bot-managed split
        in one table."""
        import re

        canon_re = re.compile(r"^(Vless|Hys|Mtp|Tuic|Any|Xh)_ID\d{2}_\d{2}$")
        try:
            s = inbound.get("settings") or "{}"
            if isinstance(s, str):
                s = json.loads(s)
            clients = s.get("clients") or []
            if not isinstance(clients, list):
                return 0
            return sum(
                1
                for c in clients
                if isinstance(c, dict) and canon_re.match(str(c.get("email", "")))
            )
        except Exception:
            return 0

    async def _send_vless_xui_overview(self, update: Update, intent: str = "status"):
        """Send an HTML message “real VLESS-Reality state
        via 3x-ui” — for legacy `/vless_status` and `/vless_export`,
        so admin sees the live panel picture instead of the local
        `vless_config.json`.

        intent='status' → focus on state / inbounds / navigation.
        intent='export' → focus on per-user export (`/provision`,
                          `/profiles`, `/email_profile`).
        """
        cfg = xui_manager.load_config()
        base_url = cfg.get("base_url", "")
        default_id = int(cfg.get("default_inbound_id") or 0)
        # Legacy bot_inbound_id from the old scheme (Variant A) — may still
        # sit in the panel; show it as “orphan” if found.
        legacy_bot_id = int(cfg.get("bot_inbound_id") or 0)

        # Live data from the panel — best effort
        inbound_lines: list = []
        live_ok = False
        try:
            xclient = xui_manager.make_client_for_config(cfg)
            if xclient is not None:
                ok_login, _msg_login = xclient.login()
                if ok_login:
                    ok_list, _msg_list, inbounds = xclient.list_inbounds()
                    if ok_list:
                        live_ok = True
                        by_id = {int(i.get("id") or 0): i for i in inbounds}
                        if default_id and default_id in by_id:
                            ib = by_id[default_id]
                            total_clients = self._count_inbound_clients(ib)
                            canon_clients = self._count_canon_clients(ib)
                            manual_clients = total_clients - canon_clients
                            inbound_lines.append(
                                f"  • #{default_id} "
                                f"<code>{html.escape(ib.get('remark') or '')}</code> "
                                f"port={ib.get('port', '?')}: "
                                f"{total_clients} clients "
                                f"(<i>{manual_clients} manual + {canon_clients} bot-managed</i>)"
                            )
                        if (
                            legacy_bot_id
                            and legacy_bot_id != default_id
                            and legacy_bot_id in by_id
                        ):
                            ib = by_id[legacy_bot_id]
                            n = self._count_inbound_clients(ib)
                            inbound_lines.append(
                                f"  • #{legacy_bot_id} "
                                f"<code>{html.escape(ib.get('remark') or '')}</code> "
                                f"port={ib.get('port', '?')} "
                                f"<i>(legacy clone-inbound from the old scheme, "
                                f"can be deleted manually)</i>: {n} clients"
                            )
        except Exception as exc:
            logger.warning("vless_xui_overview live failed: %s", exc)

        header_emoji = "🛡" if intent == "status" else "📤"
        header_text = (
            "VLESS-Reality (via 3x-ui)"
            if intent == "status"
            else "VLESS-Reality export (via 3x-ui)"
        )
        lines = [
            f"{header_emoji} <b>{header_text}</b>",
            "",
            f"Source: 3x-ui panel — <code>{html.escape(base_url)}</code>",
        ]
        if not live_ok:
            lines.append(
                "⚠️ Failed to get live data from the panel — showing "
                "cached config from <code>xui_config.json</code> only."
            )

        if inbound_lines:
            lines.append("")
            lines.append("<b>Inbounds:</b>")
            lines.extend(inbound_lines)
            lines.append("")
            lines.append(
                "<i>Bot-managed clients are written to default_inbound (on :443) "
                "with canonical names (<code>Vless_ID*_*</code>). Manual "
                "admin clients (arbitrary names) are not touched by the bot.</i>"
            )

        if intent == "status":
            lines.append("")
            lines.append(
                "Manage: /xui_status · /xui_list · /provision · "
                "/profiles · /clean_user"
            )
        else:  # export
            lines.append("")
            lines.append("📤 <b>Get a client profile:</b>")
            lines.append("• <code>/provision &lt;user_id&gt;</code> — create a new one")
            lines.append(
                "• <code>/profiles &lt;user_id&gt;</code> — view an existing one"
            )
            lines.append(
                "• <code>/email_profile &lt;user_id&gt;</code> — send by email"
            )

        lines.append("")
        lines.append(
            "<i>Below is the bot local <code>vless_config.json</code> "
            "(legacy host-Xray, unused on this VPS when "
            "3x-ui integration is present).</i>"
        )

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    def _vless_firewall_hint(self) -> str:
        """No-op after Variant B: the bot writes canonical clients into the default
        inbound (usually :443, already open for VLESS-Reality).
        A separate bot-managed inbound on a non-standard port is no
        longer used — no firewall hint is needed."""
        return ""

    @staticmethod
    def _legacy_vless_restart_hint(results, mode: str) -> str:
        """Explain runtime steps for legacy VLESS on host Xray."""
        if mode not in ("provision", "clean"):
            return ""
        vless = (results or {}).get("vless") or {}
        if vless.get("source") != "legacy_xray":
            return ""
        if not vless.get("ok"):
            return (
                "\n\n⚠️ <b>Legacy VLESS was not fully applied.</b>\n"
                "Check the message above, then over SSH:\n"
                "<code>xray run -test -config /usr/local/etc/xray/config.json</code>\n"
                "<code>systemctl status xray --no-pager</code>"
            )
        if mode == "clean" and not vless.get("removed"):
            return ""
        return (
            "\n\nℹ️ <b>Legacy VLESS applied, Xray restarted automatically.</b>\n"
            "After issuing/replacing a profile, re-import the fresh link in the client "
            "(Karing and others). If there is no traffic, check the live log:\n"
            "<code>journalctl -u xray -f --no-pager</code>"
        )

    async def _send_qrs_for_results(self, message, results, user_id: int = 0) -> list:
        """For each result with a ready URI — send a QR image
        as a separate message (one per protocol) and **schedule
        auto-delete in 15 minutes** (via `_schedule_admin_msg_ttl`).

        Used in `/provision <id>` and `/profiles <id>` (and their picker
        callbacks) so admin immediately sees a working QR. Intentionally
        not called from batch `/provision_all` — that would be spam.

        Returns a list of sent Message objects (or an empty list) —
        the caller may also do something with them.
        """
        sent_messages: list = []
        if not results:
            return sent_messages
        for proto, r in results.items():
            uri = r.get("uri")
            if not uri:
                continue
            name = r.get("client_name") or proto
            try:
                qr_msg = await self._reply_qr_for_link(message, uri, name)
            except Exception as exc:
                logger.warning("send QR for %s/%s failed: %s", proto, name, exc)
                continue
            if qr_msg is not None:
                self._schedule_admin_msg_ttl(qr_msg, user_id=user_id)
                sent_messages.append(qr_msg)
        return sent_messages

    def _format_provision_results(
        self, target_id: int, results, header: str, mode: str
    ) -> str:
        """
        mode='provision' — shows already-existed/created/error
        mode='profiles'  — read-only shows present/absent
        mode='clean'     — deleted/was-not-there/error
        """
        lines = [f"{header} <code>{target_id}</code>", ""]
        if not results:
            lines.extend(
                [
                    "⚠️ No protocol results.",
                    "",
                    "Check:",
                    "• <code>/xui_status</code> — whether 3x-ui integration is configured",
                    "• <code>/xui_list</code> — whether a default inbound is selected",
                    "• <code>/diag</code> — which transports are actually live",
                    "",
                    "On an older VPS with local 3x-ui, after deploy you usually need "
                    "to run <code>/xui_setup</code> again.",
                ]
            )
            return "\n".join(lines)
        any_uri = False
        for proto, r in results.items():
            name_html = html.escape(r.get("client_name", ""))
            ok = r.get("ok", False) if mode != "profiles" else r.get("exists", False)
            emoji = "✅" if ok else ("⚪" if mode == "profiles" else "❌")
            if mode == "provision":
                if r.get("ok") and r.get("existed"):
                    label = "already existed"
                elif r.get("ok"):
                    label = "created"
                else:
                    label = "error"
            elif mode == "profiles":
                label = "present" if r.get("exists") else "absent"
            else:  # clean
                if r.get("ok") and r.get("removed"):
                    label = "deleted"
                elif r.get("ok"):
                    label = "was not there"
                else:
                    label = "error"
            lines.append(f"{emoji} <b>{proto}</b>: <code>{name_html}</code> ({label})")
            uri = r.get("uri")
            if uri:
                any_uri = True
                lines.append(f"  └ <code>{html.escape(uri)}</code>")
            if mode in ("provision", "clean") and r.get("message"):
                lines.append(f"  └ {html.escape(str(r['message']))}")
        if any_uri and mode in ("provision", "profiles"):
            lines.append("")
            lines.append(
                "📋 <i>Tap the URL to copy it. "
                "Or /email_profile &lt;id&gt; — send by email.</i>"
            )
        hint = self._legacy_vless_restart_hint(results, mode)
        if hint:
            lines.append(hint)
        return "\n".join(lines)

    async def provision_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/provision <user_id>` — provision clients in bot-managed inbounds."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        args = context.args or []
        if not args:
            kb = self._build_user_picker_kb("prov_pick")
            await update.message.reply_text(
                "🛠 <b>Who to provision?</b>\n"
                "Pick from the list or type manually: "
                "<code>/provision &lt;id&gt;</code>. "
                "Batch: /provision_all.\n\n"
                + self._picker_scope_hint_html(),
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id must be a number.")
            return

        enabled = provision_manager.list_enabled_protocols()
        if not enabled:
            await update.message.reply_text(
                "⚠️ No protocol on the VPS is marked enabled. "
                "Check /diag and /start."
            )
            return

        results = provision_manager.provision_user(target_id, enabled_protocols=enabled)
        # A new /provision = reset the /my_profile view counter
        # (give the user 3 fresh views).
        try:
            storage_reset_my_profile_views(target_id)
        except Exception as exc:
            logger.warning("reset_my_profile_views(%s) failed: %s", target_id, exc)
        text = self._format_provision_results(
            target_id,
            results,
            header="🛠 <b>Provisioning for TG ID:</b>",
            mode="provision",
        )
        text += self._vless_firewall_hint()
        sent_text = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        # 15-min TTL on text+QR — so working URL/QR do not sit in chat
        # forever. Same as /my_profile, but without a view counter.
        self._schedule_admin_msg_ttl(sent_text, user_id=target_id)
        # QR for each protocol with a ready URI — so admin can immediately
        # forward it to the user / show it from the screen.
        await self._send_qrs_for_results(update.message, results, user_id=target_id)

    async def provision_all_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/provision_all` — provision all special + admin in one command."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        special_ids, _users_data = storage_list_users()
        target_ids = sorted(set(self.config.admin_user_ids) | set(special_ids))
        if not target_ids:
            await update.message.reply_text(
                "There are no users in the admin / special lists."
            )
            return
        enabled = provision_manager.list_enabled_protocols()
        if not enabled:
            await update.message.reply_text(
                "⚠️ No protocol on the VPS is marked enabled."
            )
            return
        await update.message.reply_text(
            f"🛠 Provisioning {len(target_ids)} users × "
            f"{len(enabled)} protocols…"
        )
        summary_lines = ["<b>Done.</b>", ""]
        legacy_vless_changed = False
        for tid in target_ids:
            results = provision_manager.provision_user(tid)
            vless = results.get("vless") or {}
            if (
                vless.get("source") == "legacy_xray"
                and vless.get("ok")
                and not vless.get("existed")
            ):
                legacy_vless_changed = True
            try:
                storage_reset_my_profile_views(tid)
            except Exception as exc:
                logger.warning("reset_my_profile_views(%s) failed: %s", tid, exc)
            ok_cnt = sum(1 for r in results.values() if r.get("ok"))
            new_cnt = sum(
                1 for r in results.values() if r.get("ok") and not r.get("existed")
            )
            summary_lines.append(
                f"• <code>{tid}</code>: {ok_cnt}/{len(results)} ok ({new_cnt} new)"
            )
        summary_lines.append("")
        summary_lines.append("Per-user details: <code>/profiles &lt;id&gt;</code>")
        if legacy_vless_changed:
            summary_lines.append("")
            summary_lines.append(
                "⚠️ Legacy VLESS changed. Over SSH run: "
                "<code>systemctl restart xray</code>"
            )
        text = "\n".join(summary_lines) + self._vless_firewall_hint()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def profiles_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/profiles <user_id>` — show existing bot-managed profiles."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        args = context.args or []
        if not args:
            kb = self._build_user_picker_kb("prof_pick")
            await update.message.reply_text(
                "📋 <b>Whose profiles to show?</b>\n"
                "Pick from the list or type manually: "
                "<code>/profiles &lt;id&gt;</code>.\n\n"
                + self._picker_scope_hint_html(),
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id must be a number.")
            return

        profiles = provision_manager.profiles_for_user(target_id)
        text = self._format_provision_results(
            target_id,
            profiles,
            header="📋 <b>Profiles for TG ID:</b>",
            mode="profiles",
        )
        none_existing = not any(p.get("exists") for p in profiles.values())
        if none_existing:
            text += (
                f"\n\nNo profiles created. "
                f"Run <code>/provision {target_id}</code>."
            )
        sent_text = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        # 15-min TTL on text+QR. If there are no profiles the message is still
        # deleted by the timer (no URLs there, but for consistency).
        self._schedule_admin_msg_ttl(sent_text, user_id=target_id)
        if not none_existing:
            await self._send_qrs_for_results(
                update.message, profiles, user_id=target_id
            )

    async def clean_user_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/clean_user <user_id> YES` — wipe bot-managed clients for a TG ID."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        args = context.args or []
        if not args:
            kb = self._build_user_picker_kb("clean_pick")
            await update.message.reply_text(
                "🗑 <b>Who to clean?</b>\n"
                "Will delete <b>only</b> bot-managed clients with canonical names "
                "(<code>Vless_ID*_*</code>, <code>Hys_ID*_*</code>, …); "
                "manual clients are not touched. <b>Tap = confirmation</b>.\n\n"
                "Text also works: "
                "<code>/clean_user &lt;id&gt; YES</code>.\n\n"
                + self._picker_scope_hint_html(),
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id must be a number.")
            return
        if len(args) < 2 or args[1].strip().upper() != "YES":
            await update.message.reply_text(
                "❗️ Confirm with a second word <code>YES</code>:\n"
                f"<code>/clean_user {target_id} YES</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        await self._delete_tracked_profile_messages(context.bot, target_id)
        results = provision_manager.clean_user(target_id)
        # A manual /clean_user also resets /my_profile state.
        try:
            storage_reset_my_profile_views(target_id)
            storage_clear_my_profile_messages(target_id)
        except Exception as exc:
            logger.warning("reset my_profile state for %s failed: %s", target_id, exc)
        text = self._format_provision_results(
            target_id,
            results,
            header="🗑 <b>Cleanup for TG ID:</b>",
            mode="clean",
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    # === Email delivery of bot-managed profiles ===

    async def setemail_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/setemail <user_id> <email>` — bind an email to a TG ID.

        Email from blocked TLDs (default .ru/.su,
        configurable via SMTP_BLOCKED_TLDS in .env) is rejected.
        With no second argument — show or reset (if 'clear').
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Usage:\n"
                "<code>/setemail &lt;telegram_user_id&gt; &lt;email&gt;</code>\n"
                "<code>/setemail &lt;telegram_user_id&gt; clear</code> — reset\n"
                "<code>/setemail &lt;telegram_user_id&gt;</code> — show current",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id must be a number.")
            return
        if len(args) == 1:
            current = storage_get_user_email(target_id)
            if current:
                await update.message.reply_text(
                    f"📧 Email <code>{target_id}</code>: "
                    f"<code>{html.escape(current)}</code>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_text(
                    f"📭 No email set for <code>{target_id}</code>.",
                    parse_mode=ParseMode.HTML,
                )
            return
        value = args[1].strip()
        if value.lower() == "clear":
            storage_remove_user_email(target_id)
            await update.message.reply_text(
                f"🗑 Email for <code>{target_id}</code> deleted.",
                parse_mode=ParseMode.HTML,
            )
            return
        ok, err = email_manager.validate_email(value)
        if not ok:
            await update.message.reply_text(
                f"❌ Email rejected: {html.escape(err)}",
                parse_mode=ParseMode.HTML,
            )
            return
        storage_set_user_email(target_id, value)
        await update.message.reply_text(
            f"✅ Email saved for <code>{target_id}</code>: "
            f"<code>{html.escape(email_manager.normalize_email(value))}</code>",
            parse_mode=ParseMode.HTML,
        )

    async def email_profile_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/email_profile <user_id>` — send bot-managed profiles to
        the user's bound email. First you need an outbound channel
        (SMTP in .env or `GMAIL_OAUTH_CREDENTIALS`) and `/setemail <uid> <email>`.

        Optionally pass an email as a second argument
        without saving it in users.json — `/email_profile <uid> <email>`.
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Usage: <code>/email_profile &lt;telegram_user_id&gt; "
                "[email]</code>\n"
                "With no email — taken from <code>/setemail</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id must be a number.")
            return

        if not email_manager.is_configured():
            await update.message.reply_text(
                "⚠️ Outbound email is not configured. Set either "
                "<code>GMAIL_OAUTH_CREDENTIALS</code> (JSON Desktop OAuth), "
                "or SMTP: SMTP_HOST / SMTP_USER / SMTP_PASS "
                "(+ SMTP_PORT, SMTP_USE_TLS, SMTP_FROM) in "
                "<code>.env</code>, then restart/recreate the container.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Email — from the argument or from storage.
        if len(args) >= 2:
            to_email = args[1].strip()
        else:
            to_email = storage_get_user_email(target_id) or ""
        if not to_email:
            await update.message.reply_text(
                f"📭 No email bound for <code>{target_id}</code>.\n"
                f"Bind it: <code>/setemail {target_id} &lt;email&gt;</code> "
                f"or pass it as a second argument: "
                f"<code>/email_profile {target_id} &lt;email&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        ok_v, err_v = email_manager.validate_email(to_email)
        if not ok_v:
            await update.message.reply_text(
                f"❌ Email rejected: {html.escape(err_v)}",
                parse_mode=ParseMode.HTML,
            )
            return

        # Collect bot-managed profiles for this user.
        try:
            profiles = provision_manager.profiles_for_user(target_id)
        except Exception as exc:
            logger.warning("email_profile: profiles_for_user failed: %s", exc)
            profiles = {}
        existing = {
            proto: p
            for proto, p in profiles.items()
            if p.get("exists") and p.get("uri")
        }
        if not existing:
            await update.message.reply_text(
                f"📭 <code>{target_id}</code> has no bot-managed profiles.\n"
                f"First: <code>/provision {target_id}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # Generate QR PNGs for each URI.
        try:
            qr_images = {
                proto: email_manager.render_qr_png(p["uri"])
                for proto, p in existing.items()
            }

            await update.message.reply_text(
                f"📤 Sending to <code>{html.escape(to_email)}</code>…",
                parse_mode=ParseMode.HTML,
            )
            ok, msg = email_manager.send_profile_email(
                to_email=to_email,
                user_id=target_id,
                profiles=existing,
                qr_images=qr_images,
            )
            if ok:
                await self._delete_tracked_profile_messages(context.bot, target_id)
                await update.message.reply_text(
                    f"✅ Email sent to <code>{html.escape(to_email)}</code> "
                    f"({len(existing)} profiles, QR attached). "
                    f"Old chat messages with URL/QR were deleted.",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_text(
                    f"❌ Failed to send: {html.escape(msg)}",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as exc:
            logger.exception("email_profile: send failed for uid=%s", target_id)
            await update.message.reply_text(
                f"❌ Send error: <code>{html.escape(str(exc))}</code>\n"
                f"<i>See container logs: docker logs … --tail 80</i>",
                parse_mode=ParseMode.HTML,
            )

    # === /my_profile auto-delete & view-limit support ===

    MY_PROFILE_TTL_SECONDS = 15 * 60  # 15 minutes
    MY_PROFILE_VIEW_LIMIT = 3  # 3 successful views; the 4th wipes them
    USER_CARD_TTL_SECONDS = 3 * 60  # admin /user card — removed from chat after 3 min

    async def _delete_message_after_delay(
        self,
        bot,
        chat_id: int,
        message_id: int,
        user_id: int = 0,
        delay_seconds: int = MY_PROFILE_TTL_SECONDS,
    ):
        """Background task: delete the message after delay_seconds.

        Idempotent — if the message was already deleted manually / via cleanup,
        ignore the error. If `user_id != 0`, also remove
        the record from `users.json[<uid>].my_profile_messages` (needed for
        the self-service flow so the 3rd /my_profile call finds something to delete).
        For the admin flow (provision/profiles) pass `user_id=0` —
        do not touch storage.
        """
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception as exc:
            logger.debug(
                "auto-delete: msg %s/%s already gone: %s",
                chat_id,
                message_id,
                exc,
            )
        if user_id:
            try:
                storage_remove_my_profile_message(user_id, chat_id, message_id)
            except Exception as exc:
                logger.warning("my_profile storage cleanup failed: %s", exc)

    def _schedule_message_ttl(
        self, msg, *, delay_seconds: int, user_id: int = 0
    ) -> None:
        """Schedule message deletion after delay_seconds (no storage)."""
        if msg is None:
            return
        try:
            asyncio.create_task(
                self._delete_message_after_delay(
                    msg.get_bot(),
                    msg.chat_id,
                    msg.message_id,
                    user_id=user_id,
                    delay_seconds=delay_seconds,
                )
            )
        except Exception as exc:
            logger.warning("schedule_message_ttl failed: %s", exc)

    def _schedule_admin_msg_ttl(self, msg, user_id: int = 0) -> None:
        """Shortcut for the admin flow (provision/profiles/picker-callback):
        schedule auto-delete of the message after MY_PROFILE_TTL_SECONDS.
        If user_id is passed, message_id is stored in users.json so that
        a later /clean_user or /my_profile can wipe the link/QR even
        after a container restart."""
        if user_id:
            self._track_and_schedule_delete(msg, user_id)
            return
        self._schedule_message_ttl(
            msg, delay_seconds=self.MY_PROFILE_TTL_SECONDS, user_id=0
        )

    def _track_and_schedule_delete(self, sent_msg, user_id: int):
        """Store message_id and start a background auto-delete task."""
        if sent_msg is None:
            return
        chat_id = sent_msg.chat_id
        message_id = sent_msg.message_id
        try:
            storage_add_my_profile_message(user_id, chat_id, message_id)
        except Exception as exc:
            logger.warning("my_profile track failed: %s", exc)
        asyncio.create_task(
            self._delete_message_after_delay(
                sent_msg.get_bot(), chat_id, message_id, user_id
            )
        )

    async def _delete_tracked_profile_messages(self, bot, user_id: int) -> None:
        """Delete all recorded URL/QR messages for user_id and clear storage."""
        prev_msgs = storage_get_my_profile_messages(user_id)
        for m in prev_msgs:
            try:
                await bot.delete_message(m["chat_id"], m["message_id"])
            except Exception as exc:
                logger.debug("tracked profile message delete failed: %s", exc)
        storage_clear_my_profile_messages(user_id)

    async def _purge_all_profile_messages(self, bot) -> int:
        """Wipe ALL previously sent URL/QR messages for all users.

        Called after changing parameters that go into the link (port, SNI,
        fingerprint, short_id, server, Reality keys, UUID): old vless://
        links and QR become invalid and must not be shown again
        to anyone — admin or users. The bot regenerates fresh links/QR
        from the updated config on the next /profiles or
        /my_profile.
        """
        entries = storage_get_all_my_profile_messages()
        deleted = 0
        for m in entries:
            chat_id = m.get("chat_id")
            message_id = m.get("message_id")
            if chat_id is None or message_id is None:
                continue
            try:
                await bot.delete_message(chat_id, message_id)
                deleted += 1
            except Exception as exc:
                # The message may already be deleted / older than 48h (Bot API limit) —
                # that is not an error; still clear tracking below.
                logger.debug("purge profile message delete failed: %s", exc)
        storage_clear_all_my_profile_messages()
        if entries:
            logger.info(
                "Purged stale profile links/QR after config change: "
                "%s deleted of %s tracked",
                deleted,
                len(entries),
            )
        return deleted

    async def _notify_admins(self, context, text: str):
        """Broadcast text to every admin. Silently ignore unreachable ones."""
        for admin_id in self.config.admin_user_ids or []:
            try:
                await context.bot.send_message(
                    int(admin_id), text, parse_mode=ParseMode.HTML
                )
            except Exception as exc:
                logger.warning("notify admin %s failed: %s", admin_id, exc)

    async def _auto_cleanup_my_profile(self, context, update: Update, user_id: int):
        """View limit reached — wipe URL/QR from chat and clients
        from panels, reset the counter, notify admins."""
        # 1. Delete all previously sent URL/QR messages.
        await self._delete_tracked_profile_messages(context.bot, user_id)

        # 2. Wipe clients from all bot-managed inbounds.
        try:
            cleanup_results = provision_manager.clean_user(user_id)
        except Exception as exc:
            logger.warning("provision_manager.clean_user(%s) failed: %s", user_id, exc)
            cleanup_results = {}

        # 3. Reset the counter — after a new /provision give 2 more.
        storage_reset_my_profile_views(user_id)

        # 4. Message to the user.
        await update.effective_message.reply_text(
            f"🚫 <b>/my_profile view limit of {self.MY_PROFILE_VIEW_LIMIT} exhausted.</b>\n\n"
            f"All your bot-managed profiles were deleted from the VPS.\n"
            f"To get new links, ask an admin to run:\n"
            f"<code>/provision {user_id}</code>",
            parse_mode=ParseMode.HTML,
        )

        # 5. Notify admins.
        notif = [
            f"🗑 <b>Profile auto-cleanup</b>",
            f"User: <code>{user_id}</code>",
            f"Reason: /my_profile view limit of {self.MY_PROFILE_VIEW_LIMIT} exhausted.",
            "",
        ]
        if cleanup_results:
            for proto, r in cleanup_results.items():
                emoji = "✅" if r.get("removed") else ("⚪" if r.get("ok") else "❌")
                name = html.escape(str(r.get("client_name") or ""))
                if r.get("removed"):
                    label = "deleted"
                elif r.get("ok"):
                    label = "was not there"
                else:
                    label = f"error: {html.escape(str(r.get('message') or ''))}"
                notif.append(f"{emoji} <b>{proto}</b>: <code>{name}</code> ({label})")
        else:
            notif.append("⚠️ Protocol list is empty — clean_user did not run.")
        notif.append("")
        notif.append(f"To re-issue: <code>/provision {user_id}</code>")
        await self._notify_admins(context, "\n".join(notif))

    async def my_profile_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """
        /my_profile command — a special user gets their URL + QR.

        First look up bot-managed canonical profiles
        (`<Prefix>_ID<first2>_<last2>` from Telegram-ID, see provision_manager) —
        they appear after an admin `/provision <uid>`. If none —
        fall back to legacy lookup (`/user` flow + 3x-ui integration).

        Protection: URL+QR messages auto-delete after 15 minutes.
        Limit of 3 successful views — on the 4th, clients are wiped from chat
        and from the 3x-ui panel (see `_auto_cleanup_my_profile`).
        """
        try:
            user = update.effective_user
            msg = update.effective_message
            if not self._is_privileged(user.id):
                await msg.reply_text(
                    "⛔ This command is for admin or special users only."
                )
                return
            uid = int(user.id)

            # First — bot-managed canonical profiles (after /provision).
            try:
                bot_profiles = provision_manager.profiles_for_user(uid)
            except Exception as exc:
                logger.warning("my_profile: provision_manager failed: %s", exc)
                bot_profiles = {}
            existing_bot = {
                proto: p
                for proto, p in bot_profiles.items()
                if p.get("exists") and p.get("uri")
            }
            if existing_bot:
                is_admin = self._is_admin(uid)
                already_viewed = 0
                if not is_admin:
                    # The view limit applies only to special users.
                    # An admin can inspect their profiles without a counter or auto-cleanup.
                    already_viewed = storage_get_my_profile_views(uid)
                    if already_viewed >= self.MY_PROFILE_VIEW_LIMIT:
                        await self._auto_cleanup_my_profile(context, update, uid)
                        return

                proto_labels = {
                    "vless": "🛡 VLESS-Reality",
                    "hysteria2": "⚡ Hysteria2",
                    "mtproto": "💬 MTProto",
                    "tuic": "🚀 TUIC",
                    "anytls": "🔒 AnyTLS",
                    "xhttp": "🌐 XHTTP",
                    "mieru": "🛰 Mieru",
                }
                summary_lines = [
                    f"🔐 <b>Your profiles (user_id={uid}):</b>",
                    (
                        "<i>Admin access: no view limit. "
                        "Messages auto-delete after 15 min.</i>"
                        if is_admin
                        else f"<i>View {already_viewed + 1} of "
                        f"{self.MY_PROFILE_VIEW_LIMIT}. Messages auto-delete "
                        f"after 15 min.</i>"
                    ),
                    "",
                    "📋 <b>Tap a URL below to copy it.</b> "
                    "QR — scan with the client camera.",
                    "",
                ]
                for proto, p in existing_bot.items():
                    label = proto_labels.get(proto, proto)
                    summary_lines.append(
                        f"{label} — <code>{html.escape(p['client_name'])}</code>"
                    )
                summary_lines.append("")
                summary_lines.append(
                    "📲 Below, for each protocol: one URL message "
                    "(tap = copy) + a QR image."
                )
                sent = await msg.reply_text(
                    "\n".join(summary_lines).strip(), parse_mode=ParseMode.HTML
                )
                self._track_and_schedule_delete(sent, uid)
                # For each protocol: one URL-only message entirely in
                # <code> (the whole bubble is a tap-target, easy to copy), and
                # a QR image as a separate message.
                for proto, p in existing_bot.items():
                    label = proto_labels.get(proto, proto)
                    uri = str(p.get("uri") or "")
                    client_name = str(p.get("client_name") or proto)
                    usage_hint = ""
                    if proto == "vless":
                        usage_hint = "\n<i>Modern link for v2rayN / sing-box / Clash Meta.</i>"
                    elif proto == "hysteria2":
                        usage_hint = "\n<i>Primary hy2:// link for Karing and compatible clients.</i>"
                    elif proto == "mtproto":
                        usage_hint = "\n<i>Telegram-only: open in Telegram, do not paste into a VPN client.</i>"
                    url_msg = await msg.reply_text(
                        f"{label}{usage_hint}\n<code>{html.escape(uri)}</code>",
                        parse_mode=ParseMode.HTML,
                    )
                    self._track_and_schedule_delete(url_msg, uid)
                    if proto == "hysteria2":
                        hysteria2_alias = hysteria2_manager.to_hysteria2_uri(uri)
                        if hysteria2_alias and hysteria2_alias != uri:
                            alias_msg = await msg.reply_text(
                                "⚡ Hysteria2 alias\n"
                                "<i>Full hysteria2:// scheme for Karing and clients "
                                "that do not accept hy2://.</i>\n"
                                f"<code>{html.escape(hysteria2_alias)}</code>",
                                parse_mode=ParseMode.HTML,
                            )
                            self._track_and_schedule_delete(alias_msg, uid)
                    qr_msg = await self._reply_qr_for_link(msg, uri, client_name)
                    self._track_and_schedule_delete(qr_msg, uid)
                if not is_admin:
                    # Count a successful view only after a real send.
                    storage_inc_my_profile_views(uid)
                return

            # No bot-managed profiles — try legacy.
            found = self._build_user_protocol_profile_lookup(uid)
            if not found:
                # Admin ≠ automatically having a VPN client: /my_profile looks up
                # profiles after /provision; it does not check the role.
                if self._is_admin(uid):
                    hint = (
                        "📭 You do not have a VPN profile yet "
                        f"(user_id=<code>{uid}</code>).\n\n"
                        "✅ Admin rights are fine — this is not about bot access.\n"
                        "Create clients for yourself with:\n"
                        f"<code>/provision {uid}</code>\n\n"
                        "Then open /my_profile again — you will get "
                        "VLESS / Hysteria2 / etc. with QR codes.\n"
                        "Panel check: /xui_status"
                    )
                else:
                    hint = (
                        "📭 There is no ready profile for you yet "
                        f"(user_id=<code>{uid}</code>).\n\n"
                        "You are on the special list — /my_profile access is granted, "
                        "but VPN clients have not been created yet.\n"
                        "Ask an admin to run:\n"
                        f"<code>/provision {uid}</code>\n\n"
                        "Then open /my_profile again."
                    )
                await msg.reply_text(hint, parse_mode=ParseMode.HTML)
                return

            lines = [f"🔐 <b>Your profiles (user_id={html.escape(str(uid))}):</b>", ""]
            v_source = ""
            if "vless_reality" in found:
                item = found["vless_reality"]
                src = item.get("source") or ""
                v_source = src
                if src == "xui":
                    src_label = "from 3x-ui (working)"
                elif src == "vless_config_test":
                    src_label = "legacy vless_config.json (test/inactive)"
                else:
                    src_label = "from bot vless_config.json"
                lines.append(f"🛡 <b>VLESS-Reality</b> ({html.escape(src_label)})")
                lines.append(f"Name: <code>{html.escape(str(item['name']))}</code>")
                if item.get("url"):
                    vless_url = str(item["url"])
                    lines.append(f"URL: <code>{html.escape(vless_url)}</code>")
                else:
                    lines.append(
                        f"Status: {html.escape(str(item.get('message') or 'inactive'))}"
                    )
                lines.append("")
            if "hysteria2" in found:
                item = found["hysteria2"]
                lines.append("⚡ <b>Hysteria2</b>")
                lines.append(f"Name: <code>{html.escape(str(item['name']))}</code>")
                hy2_url = str(item["url"])
                lines.append(
                    "URL (Karing / compatible): "
                    f"<code>{html.escape(hy2_url)}</code>"
                )
                hysteria2_alias = hysteria2_manager.to_hysteria2_uri(hy2_url)
                if hysteria2_alias and hysteria2_alias != hy2_url:
                    lines.append(
                        "Alias for Karing/full scheme: "
                        f"<code>{html.escape(hysteria2_alias)}</code>"
                    )
                lines.append("")

            if v_source in ("vless_config", "vless_config_test"):
                lines.append(
                    "⚠️ <i>This VLESS link is built from the bot local JSON. "
                    "If VLESS on the VPS is served by 3x-ui rather than the bot "
                    "xray.service, the link will not work. Ask an admin "
                    f"to run /xui_setup and issue a profile from /user {html.escape(str(uid))}.</i>"
                )

            sent = await msg.reply_text(
                "\n".join(lines).strip(),
                parse_mode=ParseMode.HTML,
            )
            self._track_and_schedule_delete(sent, uid)

            if "vless_reality" in found and found["vless_reality"].get("ok"):
                vless_qr_msg = None
                if v_source == "xui":
                    vless_qr_msg = await self._reply_qr_for_link(
                        msg,
                        found["vless_reality"]["url"],
                        found["vless_reality"]["name"],
                    )
                else:
                    vless_qr_msg = await self._reply_qr_for_link(
                        msg,
                        found["vless_reality"]["url"],
                        found["vless_reality"]["name"],
                    )
                self._track_and_schedule_delete(vless_qr_msg, uid)
            if "hysteria2" in found:
                hy2_qr_msg = await self._reply_qr_for_link(
                    msg,
                    found["hysteria2"]["url"],
                    found["hysteria2"]["name"],
                )
                self._track_and_schedule_delete(hy2_qr_msg, uid)
        except Exception as e:
            logger.error(f"Error in my_profile_command: {e}")
            await update.effective_message.reply_text(
                "Failed to get your profile."
            )

    async def _reply_qr_for_link(self, message, link: str, name: str):
        """Send a QR image for a ready link. Returns Message or None."""
        try:
            from io import BytesIO

            import qrcode
            from qrcode.constants import ERROR_CORRECT_Q

            buf = BytesIO()
            qr = qrcode.QRCode(
                error_correction=ERROR_CORRECT_Q,
                box_size=14,
                border=4,
            )
            qr.add_data(link)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            img.save(buf, format="PNG")
            buf.seek(0)
            buf.name = f"profile-{name}.png"
            return await message.reply_photo(
                photo=buf,
                caption=f"📲 QR for {name}\nIf the scan does not import — copy the URL from the message above.",
            )
        except Exception as exc:
            logger.warning("_reply_qr_for_link(%s): %s", name, exc)
            return None

    async def _handle_list_users_callbacks(self, query, data: str) -> bool:
        """Inline buttons under /list_users: journal and special. Admin only (like the whole callback)."""
        if data == "lu_log":
            await self._deliver_users_log(query.from_user, query.message)
            return True
        if not data.startswith("lu_"):
            return False
        try:
            if data == "lu_tog":
                text, kb = self._build_list_users_special_picker(0)
                await query.message.reply_text(text, reply_markup=kb)
                return True
            if data == "lu_pr":
                text, kb = self._build_protocol_user_picker(0)
                await query.message.reply_text(text, reply_markup=kb)
                return True
            if data == "lu_x":
                await query.message.edit_text("Done.", reply_markup=None)
                return True
            if data.startswith("lu_p:"):
                page = int(data.split(":", 1)[1])
                text, kb = self._build_list_users_special_picker(page)
                await query.message.edit_text(text, reply_markup=kb)
                return True
            if data.startswith("lu_prp:"):
                page = int(data.split(":", 1)[1])
                text, kb = self._build_protocol_user_picker(page)
                await query.message.edit_text(text, reply_markup=kb)
                return True
            parts = data.split(":")
            if len(parts) == 3 and parts[0] == "lu_s":
                uid, page = int(parts[1]), int(parts[2])
                await self._edit_special_user_confirm(query, uid, page)
                return True
            if len(parts) == 3 and parts[0] == "lu_pru":
                uid, page = int(parts[1]), int(parts[2])
                text, kb = self._build_protocol_picker_for_user(uid, page)
                await query.message.edit_text(text, reply_markup=kb)
                return True
            if len(parts) == 4 and parts[0] == "lu_prc":
                proto_key, uid, page = parts[1], int(parts[2]), int(parts[3])
                await self._create_protocol_profile_for_user(
                    query, uid, proto_key, page, replace_existing=False
                )
                return True
            if len(parts) == 4 and parts[0] == "lu_prr":
                proto_key, uid, page = parts[1], int(parts[2]), int(parts[3])
                special, _users = storage_list_users()
                if uid not in set(special):
                    await query.message.edit_text(
                        "⛔ Replacing a profile is allowed only for a special user.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "◀️ Back", callback_data=f"lu_pru:{uid}:{page}"
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "✖️ Close", callback_data="lu_x"
                                    )
                                ],
                            ]
                        ),
                    )
                    return True
                await self._create_protocol_profile_for_user(
                    query, uid, proto_key, page, replace_existing=True
                )
                return True
            if len(parts) == 3 and parts[0] == "lu_in":
                uid, page = int(parts[1]), int(parts[2])
                add_special_user(uid)
                await self._after_special_toggle(query, uid, page, added=True)
                logger.info(
                    "list_users inline: admin %s added special %s",
                    query.from_user.id,
                    uid,
                )
                return True
            if len(parts) == 3 and parts[0] == "lu_out":
                uid, page = int(parts[1]), int(parts[2])
                remove_special_user(uid)
                await self._after_special_toggle(query, uid, page, added=False)
                logger.info(
                    "list_users inline: admin %s removed special %s",
                    query.from_user.id,
                    uid,
                )
                return True
        except Exception as e:
            err_text = str(e) or ""
            # "Message is not modified" — Telegram API returns 400 when
            # `edit_text` gets identical content (typical case — a tap
            # on a button that just refreshes the same screen). This is not
            # a bug; stay quiet: silently ack the callback and return True.
            if "not modified" in err_text.lower():
                try:
                    await query.answer()
                except Exception:
                    pass
                return True
            logger.exception("list_users inline callback %r: %s", data, e)
            # Show the real reason in chat (admin can see more
            # than a generic "Failed to handle the button"). Error type
            # + a shortened message so the traceback does not leak.
            err_kind = type(e).__name__
            err_msg = err_text[:200] if err_text else "(empty)"
            try:
                await query.message.reply_text(
                    f"❌ Failed to handle the button.\n"
                    f"<code>{html.escape(err_kind)}</code>: "
                    f"<code>{html.escape(err_msg)}</code>\n\n"
                    f"<i>Full traceback is in the logs "
                    f"<code>docker compose logs telegram-helper</code>.</i>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                # Last resort — the old message so we are not silent.
                try:
                    await query.message.reply_text("Failed to handle the button.")
                except Exception:
                    pass
            return True
        return False

    async def _deliver_users_log(self, user, message) -> None:
        """Send the user journal (text or .txt). Access: privileged."""
        if not self._is_privileged(user.id):
            await message.reply_text(
                "⛔ This command is for admin or special users only."
            )
            return

        self._track_user(user)

        special, users = storage_list_users()
        if not users:
            await message.reply_text(
                "📒 User journal is empty. Entries appear after the first contact with the bot."
            )
            return

        admin_ids = {int(x) for x in self.config.admin_user_ids}
        special_set = set(special)

        sorted_items = sorted(
            users.items(),
            key=lambda kv: kv[1].get("first_seen") or "",
            reverse=True,
        )

        def _short_ts(value) -> str:
            if not value:
                return "—"
            v = str(value).replace("T", " ")
            return v[:16]

        header = f"📒 User journal: {len(sorted_items)} (newest first)"
        lines: list[str] = [header, ""]
        for idx, (uid, info) in enumerate(sorted_items, 1):
            role = []
            if uid in admin_ids:
                role.append("admin")
            if uid in special_set and uid not in admin_ids:
                role.append("special")
            role_str = f" [{', '.join(role)}]" if role else ""

            username = info.get("username")
            handle = f"@{username}" if username else "—"
            first_name = info.get("first_name") or ""
            last_name = info.get("last_name") or ""
            full_name = (
                first_name + (" " + last_name if last_name else "")
            ).strip() or "—"
            first_seen = _short_ts(info.get("first_seen"))
            last_seen = _short_ts(info.get("last_seen"))

            lines.append(
                f"{idx}. {full_name} ({handle}) [id={uid}]{role_str}\n"
                f"   first: {first_seen} UTC · last: {last_seen} UTC"
            )

        body = "\n".join(lines)
        logger.info(
            "users_log: privileged user %s requested log (%d entries)",
            user.id,
            len(sorted_items),
        )

        if len(body) <= 3500:
            await message.reply_text(body)
        else:
            buf = BytesIO(body.encode("utf-8"))
            buf.name = "users_log.txt"
            await message.reply_document(
                document=buf,
                caption=f"User journal: {len(sorted_items)}",
            )

    async def users_log_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/users_log command — bot user journal with first-seen date.

        Access: administrator or special user.
        Data source: storage.track_user(...), which writes first_seen once
        (via setdefault) and updates last_seen on every contact. Duplicates are not
        created: for an existing user first_seen stays the same.
        """
        try:
            user = update.effective_user
            await self._deliver_users_log(user, update.message)
        except Exception as e:
            logger.error(f"Error in users_log_command: {e}")
            await update.message.reply_text(
                "Failed to build the user journal."
            )

    # === AI SETTINGS ===

    async def ai_set_provider(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/ai_provider command — choose an AI provider."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ This command is admin-only."
            )
            return

        args = context.args or []
        if len(args) != 1 or args[0].lower() not in ["openai", "anthropic"]:
            await update.message.reply_text(
                "Usage: /ai_provider <openai|anthropic>"
            )
            return

        provider = args[0].lower()
        os.environ["DEFAULT_AI_PROVIDER"] = provider

        emoji = "🔵" if provider == "openai" else "🟣"
        await update.message.reply_text(f"{emoji} AI provider set: {provider}")

    async def ch_model_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/ch_model command — switch the AI model."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            keyboard = [
                [
                    InlineKeyboardButton(
                        "OpenAI (GPT)", callback_data="model_select_openai"
                    ),
                    InlineKeyboardButton(
                        "Anthropic (Claude)", callback_data="model_select_anthropic"
                    ),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "⚙️ Select a provider to configure the model:", reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Error in ch_model_command: {e}")
            await update.message.reply_text("Failed to run the command.")

    # === VLESS-REALITY COMMANDS ===

    async def vless_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_status command — show VLESS-Reality status.

        If 3x-ui integration is configured (`/xui_setup`), first
        a block with the live panel state and the bot-managed
        inbound is sent, and only then the legacy block from local
        `vless_config.json` (marked as unused).
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested VLESS status")

            if self._xui_is_active():
                await self._send_vless_xui_overview(update, intent="status")

            status = vless_manager.get_vless_status()

            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            # Escape special characters for Markdown V2
            def escape_md2(text):
                if not text:
                    return "not configured"
                text = str(text)
                for char in [
                    "_",
                    "*",
                    "[",
                    "]",
                    "(",
                    ")",
                    "~",
                    "`",
                    ">",
                    "#",
                    "+",
                    "-",
                    "=",
                    "|",
                    "{",
                    "}",
                    ".",
                    "!",
                ]:
                    text = text.replace(char, f"\\{char}")
                return text

            server = escape_md2(status.get("server"))
            port = escape_md2(status.get("port", 443))
            sni = escape_md2(status.get("sni", "www.microsoft.com"))
            fingerprint = escape_md2(status.get("fingerprint", "chrome"))
            updated_at = escape_md2(status.get("updated_at", "never"))

            message = f"""🛡️ *VLESS\\-Reality Status*

*State:* {status_emoji} {"On" if status["enabled"] else "Off"}
*Config:* {config_emoji} {"Configured" if status["configured"] else "Not configured"}

*Parameters:*
• Server: `{server}`
• Port: `{port}`
• SNI: `{sni}`
• Fingerprint: `{fingerprint}`

*Keys:*
• UUID: {"✅" if status["has_uuid"] else "❌"}
• Public Key: {"✅" if status["has_public_key"] else "❌"}
• Private Key: {"✅" if status["has_private_key"] else "❌"}
• Short ID: {"✅" if status["has_short_id"] else "❌"}

*Source:* legacy `xray.service` \\+ `/usr/local/etc/xray/config.json`
_The 3x\\-ui panel is not used here\\. For current links: /vless\\_qr, /vless\\_export\\._

_Updated: {updated_at}_"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in vless_status: {e}")
            await update.message.reply_text("Failed to get VLESS status.")

    async def vless_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_on command — enable VLESS-Reality."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "service"):
                return
            logger.info(f"Admin {user.id} enabling VLESS-Reality")

            success, message = vless_manager.enable_vless()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_on: {e}")
            await update.message.reply_text("Failed to enable VLESS.")

    async def vless_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_off command — disable VLESS-Reality."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "service"):
                return
            logger.info(f"Admin {user.id} disabling VLESS-Reality")

            success, message = vless_manager.disable_vless()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_off: {e}")
            await update.message.reply_text("Failed to disable VLESS.")

    async def vless_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_config command — show and save the VLESS configuration to files."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested VLESS config")

            config = vless_manager.get_vless_config(include_secrets=False)

            # Save configs to files
            success, save_msg, created_files = vless_manager.save_vless_config_files()

            # Build the list of saved files
            files_list = ""
            if created_files:
                files_list = "\n\n📁 *Saved files:*\n"
                for f in created_files:
                    # Show only the file name without the full path
                    fname = os.path.basename(f)
                    files_list += f"• `{fname}`\n"

            # Escape for Markdown V2
            save_msg_escaped = escape_markdown(save_msg)

            message = f"""🔧 *VLESS\\-Reality configuration*

```json
{json.dumps(config, indent=2, ensure_ascii=False)}
```

{save_msg_escaped}{files_list}
💡 Secrets are hidden\\. For the full configuration use /vless\\_export"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in vless_config: {e}")
            await update.message.reply_text("Failed to get VLESS configuration.")

    async def vless_set_server(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/vless_set_server command — set the server address (auto-detect if no arguments)."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []

            # If an argument is given — use it, otherwise auto-detect
            if len(args) >= 1:
                server = args[0]
            else:
                await update.message.reply_text("🔍 Detecting server IP...")
                server = None  # Auto-detect

            success, message = vless_manager.set_vless_server(server)
            if success:
                message += (
                    self._legacy_vless_reexport_text()
                    + "\n\nℹ️ Xray restart is not needed — only the address in the URI changes."
                )
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_set_server: {e}")
            await update.message.reply_text("Failed to set the server.")

    async def vless_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_set_port command — set the port."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text("Usage: /vless_set_port <port>")
                return

            try:
                port = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ Port must be a number")
                return

            success, message = vless_manager.set_vless_port(port)
            await update.message.reply_text(message)

            if success:
                # Auto-apply to the real Xray config (legacy host-Xray
                # flow) — without this, xray.service keeps listening on the old port,
                # and new QR/links would point at a port the server is not listening on yet.
                await self._legacy_vless_apply_followup(update, port=port)

        except Exception as e:
            logger.error(f"Error in vless_set_port: {e}")
            await update.message.reply_text("Failed to set the port.")

    async def vless_add_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/vless_add_client command — add a VLESS client."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "client"):
                return
            args = context.args or []
            if len(args) < 1:
                await update.message.reply_text(
                    "Usage: /vless_add_client <name> [uuid]"
                )
                return

            name = args[0]
            client_uuid = args[1] if len(args) > 1 else None

            success, message, client = vless_manager.add_client(name, client_uuid)
            if success:
                uuid_display = client.get("uuid", "")
                response = f"{message}\nUUID: `{uuid_display}`"
                await self._reply_md2_safe(update.message, response)
                await self._reply_vless_qr(update.message, client.get("uuid", name))
                await self._legacy_vless_apply_followup(update)
            else:
                await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_add_client: {e}")
            await update.message.reply_text("Failed to add the client.")

    async def vless_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_qr command — show a QR for a VLESS client."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ VLESS QR codes are admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "client"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Usage: /vless_qr <client_name_or_uuid>"
                )
                return

            await self._reply_vless_qr(update.message, args[0])

        except Exception as e:
            logger.error(f"Error in vless_qr: {e}")
            await update.message.reply_text("Failed to show the client QR.")

    async def vless_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/vless_list_clients command — VLESS client list.

        When `/xui_setup` is configured — shows **bot-managed
        inbound** clients in 3x-ui (created via /provision). A manual inbound
        with hand-made clients is not touched (on the screenshot that is `195_Vless` —
        admin edits it in the panel).
        When not — the list from local `vless_config.json` (legacy).
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if self._xui_is_active():
                cfg = xui_manager.load_config()
                default_id = int(cfg.get("default_inbound_id") or 0)
                if not default_id:
                    await update.message.reply_text(
                        "ℹ️ default_inbound_id is not set in /xui_setup.\n"
                        "Without it the bot does not know which inbound to look at.",
                        parse_mode=ParseMode.HTML,
                    )
                    return
                xclient = xui_manager.make_client_for_config(cfg)
                if xclient is None:
                    await update.message.reply_text(
                        "❌ Failed to restore XUIClient (see /xui_status)."
                    )
                    return
                ok_l, msg_l = xclient.login()
                if not ok_l:
                    await update.message.reply_text(
                        f"❌ 3x-ui login: {html.escape(msg_l)}",
                        parse_mode=ParseMode.HTML,
                    )
                    return
                ok_g, msg_g, inbound = xclient.get_inbound(default_id)
                if not ok_g or not inbound:
                    await update.message.reply_text(
                        f"❌ Failed to get default inbound #{default_id}: "
                        f"{html.escape(msg_g or '')}",
                        parse_mode=ParseMode.HTML,
                    )
                    return
                try:
                    settings_raw = inbound.get("settings") or "{}"
                    settings = (
                        json.loads(settings_raw)
                        if isinstance(settings_raw, str)
                        else settings_raw
                    )
                    clients = settings.get("clients") or []
                except Exception:
                    clients = []
                # Filter by the canonical pattern — manual clients are not
                # shown as “bot-managed” (they are visible only in the panel).
                import re as _re

                canon_re = _re.compile(r"^(Vless|Hys|Mtp|Tuic|Any|Xh)_ID\d{2}_\d{2}$")
                bot_clients = [
                    c
                    for c in clients
                    if isinstance(c, dict) and canon_re.match(str(c.get("email", "")))
                ]
                manual_count = len(clients) - len(bot_clients)
                inbound_remark = inbound.get("remark") or ""
                inbound_port = inbound.get("port") or 0
                lines = [
                    f"🛡 <b>Bot-managed VLESS clients</b> "
                    f"(inbound #{default_id} — "
                    f"<code>{html.escape(str(inbound_remark))}</code>, "
                    f"port {inbound_port}):",
                    "",
                ]
                if not bot_clients:
                    lines.append(
                        "No bot-managed clients. "
                        "<code>/provision &lt;id&gt;</code> — add one."
                    )
                else:
                    for c in bot_clients:
                        email = c.get("email") or "?"
                        cuuid = c.get("id") or ""
                        enabled = c.get("enable", True)
                        emoji = "✅" if enabled else "⏸"
                        lines.append(
                            f"{emoji} <code>{html.escape(str(email))}</code>  "
                            f"uuid: <code>{html.escape(str(cuuid)[:8])}…</code>"
                        )
                lines.append("")
                lines.append(
                    f"<i>Besides bot-managed, this inbound also has "
                    f"<b>{manual_count}</b> manual admin clients — "
                    f"the bot does not touch them (edit them in the panel).</i>"
                )
                await update.message.reply_text(
                    "\n".join(lines), parse_mode=ParseMode.HTML
                )
                return

            # Legacy fallback
            clients = vless_manager.list_clients()
            if not clients:
                await update.message.reply_text("Client list is empty.")
                return

            lines = [
                "*VLESS clients \\(legacy Xray, no 3x\\-ui\\):*",
                "_Source: `vless_config.json` \\+ `/usr/local/etc/xray/config.json`\\._",
                "_For QR/URI use `/vless\\_qr <name>` or `/vless\\_export`\\._",
                "",
            ]
            for client in clients:
                name = escape_markdown(str(client.get("name", "client")))
                uuid = escape_markdown(str(client.get("uuid", "")))
                lines.append(f"• {name}: `{uuid}`")

            await self._reply_md2_safe(update.message, "\n".join(lines))

        except Exception as e:
            logger.error(f"Error in vless_list_clients: {e}")
            await update.message.reply_text("Failed to get the client list.")

    async def vless_del_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/vless_del_client command — delete a VLESS client."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "client"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Usage: /vless_del_client <name_or_uuid>"
                )
                return

            success, message = vless_manager.remove_client(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_del_client: {e}")
            await update.message.reply_text("Failed to delete the client.")

    async def vless_set_uuid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_set_uuid command — set UUID."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text("Usage: /vless_set_uuid <uuid>")
                return

            success, message = vless_manager.set_vless_uuid(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_uuid: {e}")
            await update.message.reply_text("Failed to set UUID.")

    async def vless_set_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_set_key command — set the Reality public key."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Usage: /vless_set_key <public_key>"
                )
                return

            success, message = vless_manager.set_vless_public_key(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_key: {e}")
            await update.message.reply_text("Failed to set the key.")

    async def vless_set_shortid(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/vless_set_shortid command — set Short ID."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Usage: /vless_set_shortid <hex_string>"
                )
                return

            success, message = vless_manager.set_vless_short_id(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_shortid: {e}")
            await update.message.reply_text("Failed to set Short ID.")

    async def vless_set_sni(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_set_sni command — set SNI for camouflage."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                # With no argument — show an interactive button list with
                # ready SNIs: one tap changes the domain, applies the config, and
                # offers to restart Xray. This is a “simple command with
                # hints” — no need to memorize domains.
                await self._show_vless_sni_picker(update.message)
                return

            success, message = vless_manager.set_vless_sni(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_sni: {e}")
            await update.message.reply_text("Failed to set SNI.")

    async def _show_vless_sni_picker(self, message) -> None:
        """Show SNI picker buttons (current marked ✅) with a hint."""
        try:
            current = (vless_manager.get_vless_status() or {}).get("sni", "")
        except Exception:
            current = ""

        # Human-readable names next to the domain — so the button list
        # makes it clear which site we are camouflaging (Cloudflare, Apple, etc.), not
        # just a bare domain.
        sni_labels = {
            "yahoo.com": "Yahoo",
            "www.cloudflare.com": "Cloudflare",
            "www.amazon.com": "Amazon",
            "www.microsoft.com": "Microsoft",
            "www.apple.com": "Apple",
            "www.google.com": "Google",
            "www.netflix.com": "Netflix",
        }

        rows: List[List[InlineKeyboardButton]] = []
        for domain in vless_manager.AVAILABLE_SNI:
            mark = "✅ " if domain == current else ""
            brand = sni_labels.get(domain, domain)
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{mark}{brand} — {domain}",
                        callback_data=f"vless_set_sni:{domain}",
                    )
                ]
            )

        hint = (
            "🌐 *SNI picker \\(Reality camouflage domain\\)*\n\n"
            f"Current: `{escape_markdown(current or '—')}`\n\n"
            "Tap a domain — the bot will change SNI, rewrite the Xray config, and offer "
            "a restart\\.\n\n"
            "💡 There is no single correct SNI: mobile operators \\(especially in RU\\) most "
            "often block `www\\.microsoft\\.com`\\. If it does not connect while the "
            "network/port/keys are fine — start with `yahoo\\.com`\\.\n\n"
            "Custom domain: `/vless_set_sni example\\.com`"
        )
        await self._reply_md2_safe(
            message, hint, reply_markup=InlineKeyboardMarkup(rows)
        )

    async def vless_set_fingerprint(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/vless_set_fingerprint command — set TLS fingerprint."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                fp_list = ", ".join(vless_manager.AVAILABLE_FINGERPRINTS)
                await update.message.reply_text(
                    f"Usage: /vless_set_fingerprint <fingerprint>\n\nAvailable: {fp_list}"
                )
                return

            success, message = vless_manager.set_vless_fingerprint(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_fingerprint: {e}")
            await update.message.reply_text("Failed to set fingerprint.")

    async def vless_gen_keys(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_gen_keys command — generate all VLESS-Reality keys."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            logger.info(f"Admin {user.id} generating VLESS keys")

            await update.message.reply_text("⏳ Generating keys...")

            success, keys, message = vless_manager.generate_all_keys()

            if success:
                uuid_escaped = escape_markdown(keys.get("uuid", ""))
                pk_escaped = escape_markdown(keys.get("public_key", ""))
                sid_escaped = escape_markdown(keys.get("short_id", ""))
                response = f"""{message}

🔑 *Generated keys:*

*UUID:*
`{uuid_escaped}`

*Public Key:*
`{pk_escaped}`

*Short ID:*
`{sid_escaped}`

⚠️ *Important:*
• Private Key is stored only on the server and is not sent to Telegram
• Public Key and Short ID are needed for the client
• UUID must match on the server and the client"""

                await self._reply_md2_safe(update.message, response)

                # Automatically apply the new keys to the real Xray config
                # (legacy host-Xray flow). Without this step client QR/links
                # contain the new keys while xray.service still serves the old
                # (or empty) config — the Reality handshake fails.
                await self._legacy_vless_apply_followup(update)
            else:
                await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_gen_keys: {e}")
            await update.message.reply_text("Failed to generate keys.")

    async def vless_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_test command — test connectivity to the server."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "service"):
                return
            logger.info(f"Admin {user.id} testing VLESS connection")

            await update.message.reply_text("⏳ Testing connectivity...")

            success, message = vless_manager.test_connection()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_test: {e}")
            await update.message.reply_text("Failed to test connectivity.")

    async def vless_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_export command — admin-only.

        Behavior depends on 3x-ui integration:
        - **xui active** → ONLY the overview block is sent, which
          points at working commands `/provision`, `/profiles`,
          `/email_profile`. The legacy JSON dump of local
          `vless_config.json` and the pack of 8 inline buttons are not
          shown — that was confusing.
        - **xui NOT active** (bare host-Xray VPS) → the old legacy
          export with all formats (it is useful in this setup).
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} exporting VLESS config")

            if self._xui_is_active():
                # On a VPS with 3x-ui the real source of client profiles is
                # /provision/profiles/email_profile. Legacy JSON
                # is no longer shown (it belongs to local xray.service,
                # which is not present here).
                await self._send_vless_xui_overview(update, intent="export")
                return

            # Client configuration
            client_config = vless_manager.export_client_config()

            # Xray configurations
            xray_client = vless_manager.export_xray_config(is_server=False)
            xray_server = vless_manager.export_xray_config(is_server=True)

            # Generate VLESS link
            vless_link = vless_manager.generate_vless_link()
            vless_link_escaped = escape_markdown(vless_link)

            message = f"""📤 *VLESS\\-Reality configuration export*

*Client configuration:*
```json
{json.dumps(client_config, indent=2)}
```

🔗 *Link for Hiddify / Foxray / v2rayNG:*
`{vless_link_escaped}`

For the full Xray configuration use the commands below\\."""

            keyboard = [
                [
                    InlineKeyboardButton(
                        "📱 Xray Client Config", callback_data="vless_export_client"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🖥️ Xray Server Config", callback_data="vless_export_server"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📷 QR by client", callback_data="vless_export_qr_menu"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📦 Subscription (base64)",
                        callback_data="vless_export_sub_base64",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📄 Subscription (raw)", callback_data="vless_export_sub_raw"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🧩 Sing-box Config", callback_data="vless_export_singbox"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🧩 Clash Meta Config", callback_data="vless_export_clash"
                    )
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Error in vless_export: {e}")
            await update.message.reply_text("Failed to export configuration.")

    async def vless_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_sync command — auto-configure and export for a VPN client."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "service"):
                return
            logger.info(f"Admin {user.id} syncing VLESS config for client")

            # First sync keys from the xray config (if xray is installed and running)
            # This ensures public_key in vless_config.json matches privateKey in xray
            sync_success, sync_msg = vless_manager.sync_from_xray_config()
            if sync_success and "Synced" in sync_msg:
                await update.message.reply_text(
                    "🔄 " + sync_msg.replace("`", ""), parse_mode=None
                )

            # Get the current configuration
            config = vless_manager.get_vless_config(include_secrets=True)

            auto_configured = False

            # If the server is not set — auto-detect
            if not config.get("server") or "..." in str(config.get("server", "")):
                await update.message.reply_text("🔍 Detecting server IP...")
                success, msg = vless_manager.set_vless_server(None)  # Auto-detect
                if not success:
                    await update.message.reply_text(msg)
                    return
                await update.message.reply_text(msg)
                auto_configured = True
                config = vless_manager.get_vless_config(include_secrets=True)

            # If keys are not generated — generate them
            if not config.get("uuid") or "..." in str(config.get("uuid", "")):
                await update.message.reply_text("🔑 Generating keys...")
                success, keys, msg = vless_manager.generate_all_keys()
                if not success:
                    await update.message.reply_text(msg)
                    return
                await update.message.reply_text(msg)
                auto_configured = True
                config = vless_manager.get_vless_config(include_secrets=True)

            # Get the full configuration for export
            full_config = vless_manager.export_client_config()

            # Escaping for Markdown
            server_escaped = escape_markdown(full_config["server"])
            uuid_escaped = escape_markdown(full_config["uuid"])
            pk_escaped = escape_markdown(full_config["public_key"])
            sid_escaped = escape_markdown(full_config["short_id"])

            if auto_configured:
                header = "✅ *VLESS\\-Reality configured automatically\\!*"
            else:
                header = "🔄 *VLESS\\-Reality for sing-box*"

            # Generate VLESS link
            vless_link = vless_manager.generate_vless_link()
            vless_link_escaped = escape_markdown(vless_link)

            message = f"""{header}

*Copy these values into Settings → Reality:*

📍 *Server:* `{server_escaped}`
🔌 *Port:* `{full_config["port"]}`
🆔 *UUID:* `{uuid_escaped}`
🔑 *Public Key:* `{pk_escaped}`
🏷️ *Short ID:* `{sid_escaped}`
🌐 *SNI:* `{full_config["sni"]}`
🎭 *Fingerprint:* `{full_config["fingerprint"]}`

🔗 *Link for Hiddify / Foxray / v2rayNG:*
`{vless_link_escaped}`

💡 _Open sing-box → Settings → VLESS\\-Reality → Configure Reality_"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in vless_sync: {e}")
            await update.message.reply_text("Failed to sync configuration.")

    async def vless_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/vless_reset command — reset the VLESS configuration."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            logger.info(f"Admin {user.id} resetting VLESS config")

            # Ask for confirmation
            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Yes, reset", callback_data="vless_reset_confirm"
                    ),
                    InlineKeyboardButton(
                        "❌ Cancel", callback_data="vless_reset_cancel"
                    ),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "⚠️ *Are you sure you want to reset the VLESS\\-Reality configuration?*\n\n"
                "All settings and keys will be deleted\\!",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup,
            )

        except Exception as e:
            logger.error(f"Error in vless_reset: {e}")
            await update.message.reply_text("Failed to reset configuration.")

    # === XRAY MANAGEMENT COMMANDS ===

    async def xray_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xray_status command — check Xray status."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} checking Xray status")

            installed, message, info = vless_manager.check_xray_installed()

            if not installed:
                message += "\n\n💡 To install: /xray\\_install"

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in xray_status: {e}")
            await update.message.reply_text("Failed to check Xray status.")

    async def xray_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xray_config command — show the Xray configuration."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} viewing Xray config")

            success, message, config = vless_manager.get_xray_config()
            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in xray_config: {e}")
            await update.message.reply_text("Failed to get Xray configuration.")

    async def xray_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xray_install command — install Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} installing Xray")

            await update.message.reply_text(
                "⏳ Installing Xray... (may take 1-2 minutes)"
            )

            success, message = vless_manager.install_xray()
            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in xray_install: {e}")
            await update.message.reply_text("Failed to install Xray.")

    async def xray_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xray_apply command — apply the VLESS configuration to Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} applying Xray config")

            success, message = vless_manager.apply_xray_config()
            await self._reply_md2_safe(update.message, message)
            if success:
                # Plain text: MD2 breaks on systemctl / underscores.
                await update.message.reply_text(self._legacy_vless_host_restart_text())

        except Exception as e:
            logger.error(f"Error in xray_apply: {e}")
            await update.message.reply_text("Failed to apply configuration.")

    async def xray_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xray_start command — start Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} starting Xray")

            success, message = vless_manager.start_xray()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in xray_start: {e}")
            await update.message.reply_text("Failed to start Xray.")

    async def xray_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xray_stop command — stop Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} stopping Xray")

            success, message = vless_manager.stop_xray()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in xray_stop: {e}")
            await update.message.reply_text("Failed to stop Xray.")

    async def xray_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xray_restart command — restart Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} restarting Xray")

            await update.message.reply_text("⏳ Restarting Xray...")

            success, message = vless_manager.restart_xray()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in xray_restart: {e}")
            await update.message.reply_text("Failed to restart Xray.")

    async def xray_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xray_logs command — show Xray logs."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} viewing Xray logs")

            # Parse line count from arguments
            args = context.args or []
            lines = 30
            if args and args[0].isdigit():
                lines = min(int(args[0]), 100)  # Max 100 lines

            success, message = vless_manager.get_xray_logs(lines)
            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in xray_logs: {e}")
            await update.message.reply_text("Failed to get logs.")

    # === NGINX SNI ROUTING COMMANDS ===

    async def nginx_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/nginx_status command — Nginx SNI fallback status."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            config = vless_manager._load_config()
            enabled = config.get("nginx_fallback_enabled", False)
            port = config.get("nginx_fallback_port", 8443)
            hs_domain = config.get("headscale_domain", "")
            ha_domain = config.get("ha_domain", "")

            status_emoji = "🟢" if enabled else "🔴"
            lines = [
                f"{status_emoji} *Nginx SNI Fallback*: {'on' if enabled else 'off'}",
                f"📍 *Port*: `{port}`",
                f"🌐 *Headscale domain*: `{escape_markdown(hs_domain or 'not set')}`",
            ]
            if ha_domain:
                lines.append(
                    f"🏠 *Home Assistant domain*: `{escape_markdown(ha_domain)}`"
                )

            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in nginx_status: {e}")
            await update.message.reply_text("Failed to get Nginx status.")

    async def nginx_enable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/nginx_enable command — enable Nginx SNI fallback."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            args = context.args or []
            port = int(args[0]) if args and args[0].isdigit() else 8443
            success, message = vless_manager.set_nginx_fallback(True, port)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in nginx_enable: {e}")
            await update.message.reply_text("Failed to enable Nginx fallback.")

    async def nginx_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/nginx_disable command — disable Nginx SNI fallback."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            success, message = vless_manager.set_nginx_fallback(False)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in nginx_disable: {e}")
            await update.message.reply_text("Failed to disable Nginx fallback.")

    async def nginx_set_domain(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/nginx_set_domain command <headscale_domain> [ha_domain]."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /nginx_set_domain <headscale_domain> [ha_domain]\n"
                    "Example: /nginx_set_domain headscale.example.com ha.example.com"
                )
                return

            headscale_domain = args[0]
            ha_domain = args[1] if len(args) > 1 else ""
            success, message = vless_manager.set_nginx_domains(
                headscale_domain, ha_domain
            )
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in nginx_set_domain: {e}")
            await update.message.reply_text("Failed to set the domain.")

    async def nginx_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/nginx_config command — print the Nginx config to copy onto the VPS."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            success, config_text = vless_manager.get_nginx_sni_config()
            if success:
                await update.message.reply_text(
                    f"```nginx\n{config_text}\n```",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(config_text)
        except Exception as e:
            logger.error(f"Error in nginx_config: {e}")
            await update.message.reply_text("Failed to generate Nginx config.")

    # === HEADSCALE COMMANDS ===

    async def headscale_host_tailscale_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/headscale command — Tailscale client IPv4/IPv6 on the VPS host (admin/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ This command is for admin or special users only."
                )
                return
            self._track_user(user)
            logger.info(
                f"Privileged user {user.id} requested /headscale (host Tailscale IP)"
            )

            _ok, text = headscale_manager.get_host_tailscale_client_summary()
            await update.message.reply_text(text)
        except Exception as e:
            logger.error(f"Error in headscale_host_tailscale_command: {e}")
            await update.message.reply_text(
                "Failed to detect the Tailscale address on the server."
            )

    async def headscale_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/headscale_status command — Headscale status."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            status = headscale_manager.get_status()
            enabled_emoji = "🟢" if status["enabled"] else "🔴"
            container_emoji = "🟢" if status["container_running"] else "🔴"

            lines = [
                f"{enabled_emoji} *Headscale*: {'on' if status['enabled'] else 'off'}",
                f"{container_emoji} *Container* `{escape_markdown(status['container_name'])}`: "
                f"{'running' if status['container_running'] else 'stopped'}",
                f"🌐 *URL*: `{escape_markdown(status['server_url'] or 'not set')}`",
                f"💻 *Nodes*: {status['node_count']}",
                f"👤 *Users*: {status['user_count']}",
            ]

            # Headplane (Web UI). Deployed as a separate container via
            # compose.headplane.yaml. If the container is found — show the URL
            # for the SSH tunnel. If not — do not clutter status.
            hp = status.get("headplane") or {}
            if hp.get("container_running"):
                browser = escape_markdown(hp.get("browser_url", ""))
                tunnel = escape_markdown(hp.get("tunnel_hint", ""))
                lines.append("")
                lines.append("🟢 *Headplane* \\(Web UI\\): running")
                lines.append(f"🔗 `{browser}`")
                lines.append(f"🚪 SSH\\-tunnel: `{tunnel}`")
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in headscale_status: {e}")
            await update.message.reply_text("Failed to get Headscale status.")

    async def headscale_enable(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/headscale_enable command."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return
            success, message = headscale_manager.enable_headscale()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_enable: {e}")
            await update.message.reply_text("Error.")

    async def headscale_disable(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/headscale_disable command."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return
            success, message = headscale_manager.disable_headscale()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_disable: {e}")
            await update.message.reply_text("Error.")

    async def headscale_set_url(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/headscale_set_url command <url>."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /headscale_set_url https://headscale.example.com"
                )
                return
            success, message = headscale_manager.set_server_url(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_set_url: {e}")
            await update.message.reply_text("Error.")

    async def headscale_gen(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/headscale_gen command [user] [expiration] — generate a Pre-Auth key.

        Arguments are position-independent: a token like ``720h``/``30m``/``7d``
        is treated as key lifetime; anything else is a username.
        Examples: ``/headscale_gen``, ``/headscale_gen 720h``,
        ``/headscale_gen alice``, ``/headscale_gen alice 720h``.
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            # Lifetime: number + unit (s/m/h/d). Everything else is a username.
            hs_user, hs_expiration = headscale_manager.parse_user_expiration(
                context.args or []
            )

            success, message, key = headscale_manager.create_preauth_key(
                user=hs_user, expiration=hs_expiration
            )
            if success and key:
                instructions = headscale_manager.export_client_instructions(key)
                await update.message.reply_text(
                    f"{message}\n\n```\n{instructions}\n```",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_gen: {e}")
            await update.message.reply_text("Failed to generate the key.")

    async def headscale_revoke(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/headscale_revoke command <key> [user] — revoke a Pre-Auth key.

        With no arguments, shows the list of active keys so there is something
        to revoke. Useful if a /headscale_gen key leaked or is no longer needed.
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            args = context.args or []
            if not args:
                ok, _msg, keys = headscale_manager.list_preauth_keys()
                if not ok:
                    await update.message.reply_text(_msg)
                    return
                if not keys:
                    await update.message.reply_text(
                        "No active Pre-Auth keys.\n"
                        "Usage: /headscale_revoke <key> [user]"
                    )
                    return
                lines = ["🔑 *Pre-Auth keys* \\(specify a key to revoke\\):"]
                for k in keys:
                    if not isinstance(k, dict):
                        continue
                    kid = str(k.get("key", k.get("id", "?")))
                    used = "used" if k.get("used") else "active"
                    reusable = "reusable" if k.get("reusable") else "one\\-time"
                    lines.append(f"• `{escape_markdown(kid)}` — {used}, {reusable}")
                await update.message.reply_text(
                    "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
                )
                return

            key = args[0]
            hs_user = args[1] if len(args) > 1 else None
            success, message = headscale_manager.revoke_preauth_key(key, user=hs_user)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_revoke: {e}")
            await update.message.reply_text("Failed to revoke the key.")

    async def headscale_list_nodes(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/headscale_list_nodes command — node list."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            success, message, nodes = headscale_manager.list_nodes()
            if not success:
                await update.message.reply_text(message)
                return

            if not nodes:
                await update.message.reply_text("📋 No connected nodes.")
                return

            lines = [f"📋 *Headscale nodes* \\({len(nodes)}\\):"]
            for node in nodes[:20]:  # Limit to 20
                name = escape_markdown(node.get("givenName", node.get("name", "?")))
                ip = (
                    node.get("ipAddresses", ["?"])[0]
                    if node.get("ipAddresses")
                    else "?"
                )
                online = "🟢" if node.get("online", False) else "🔴"
                lines.append(f"  {online} `{name}` — `{escape_markdown(ip)}`")

            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in headscale_list_nodes: {e}")
            await update.message.reply_text("Failed to get the node list.")

    async def headscale_create_user(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/headscale_create_user command <name>."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /headscale_create_user <username>"
                )
                return
            success, message = headscale_manager.create_user(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_create_user: {e}")
            await update.message.reply_text("Failed to create the user.")

    # === EXIT NODE (internet via the VPS coordinator) ===

    async def exit_node_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/exit_node command — exit node status + guide (admin + special)."""
        msg = update.effective_message
        try:
            user = update.effective_user
            self._track_user(user)
            if not self._is_privileged(user.id):
                await msg.reply_text(
                    "⛔ Available to admin or special users."
                )
                return

            status = headscale_manager.get_exit_node_status()
            if status.get("error"):
                ready = False
            else:
                ready = bool(status.get("advertising") and status.get("approved"))

            if ready:
                head = "🟢 Exit node ready — internet via VPS is available."
            elif status.get("error"):
                head = f"🔴 Exit node unavailable: {status['error']}."
            else:
                head = "🟡 Exit node is not up yet." + (
                    ""
                    if self._is_admin(user.id)
                    else " Ask an admin to enable it (/exit_node_on)."
                )

            lines = [head, ""]
            node_label = status.get("node_label") or ""
            if ready:
                lines.append(
                    headscale_manager.exit_node_client_instructions(node_label)
                )
            elif self._is_admin(user.id):
                # Show diagnostics to admin so they can see what is missing.
                adv = "✅" if status.get("advertising") else "❌"
                appr = "✅" if status.get("approved") else "❌"
                fwd4 = status.get("ip_forward_v4")
                fwd6 = status.get("ip_forward_v6")
                lines += [
                    f"{adv} advertise on the host",
                    f"{appr} route approved in Headscale",
                    f"forwarding IPv4: {fwd4 or '?'}, IPv6: {fwd6 or '?'}",
                    "",
                    "Enable: /exit_node_on",
                ]
            await msg.reply_text("\n".join(lines).strip())
        except Exception as e:
            logger.error(f"Error in exit_node_command: {e}")
            if msg:
                await msg.reply_text("Failed to get exit node status.")

    async def exit_node_on_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/exit_node_on command — make the VPS an exit node (admin only)."""
        msg = update.effective_message
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await msg.reply_text("⛔ This command is admin-only.")
                return
            ok, report = headscale_manager.enable_exit_node()
            prefix = "" if ok else "❌ "
            await msg.reply_text(f"{prefix}{report}")
        except Exception as e:
            logger.error(f"Error in exit_node_on_command: {e}")
            await msg.reply_text("Failed to enable exit node.")

    async def exit_node_off_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/exit_node_off command — disable exit node (admin only)."""
        msg = update.effective_message
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await msg.reply_text("⛔ This command is admin-only.")
                return
            ok, report = headscale_manager.disable_exit_node()
            await msg.reply_text(report)
        except Exception as e:
            logger.error(f"Error in exit_node_off_command: {e}")
            await msg.reply_text("Failed to disable exit node.")

    # === CALLBACK QUERY HANDLER ===

    async def _handle_menu_callbacks(
        self, update: Update, context, query, data: str
    ) -> bool:
        """`menu:*` callbacks — the only namespace available to non-admins.

        Role is checked on every action (spec §1), not only at handler
        entry. Returns True if the callback was handled.
        """
        if not data.startswith("menu:"):
            return False
        uid = query.from_user.id
        action = data.split(":", 1)[1]
        try:
            if action == "info":
                await self.info_command(update, context)
            elif action in ("help", "back"):
                if self._is_admin(uid):
                    await self._help_show_menu(query.message)
                else:
                    await self._menu_panel(
                        query.message,
                        self._user_help_panel_text(uid),
                        self._main_menu_keyboard(uid),
                        edit=True,
                    )
            elif action == "diag":
                # diag_command itself distinguishes roles (brief/full report).
                await self.diag_command(update, context)
            elif action == "clear":
                await self.clear_chat(update, context)
            elif action == "settings":
                text, kb = self._settings_panel(uid)
                await self._menu_panel(query.message, text, kb, edit=True)
            elif action.startswith("set_theme:"):
                name = action.split(":", 1)[1]
                if storage_get_ui_prefs(uid)["theme"] == name:
                    return True  # already selected — do not call edit_text
                try:
                    storage_set_ui_pref(uid, "theme", name)
                except ValueError:
                    await query.message.reply_text("Unknown theme.")
                    return True
                text, kb = self._settings_panel(uid)
                await self._menu_panel(query.message, text, kb, edit=True)
            elif action == "toggle_compact":
                prefs = storage_get_ui_prefs(uid)
                storage_set_ui_pref(uid, "compact", not prefs["compact"])
                text, kb = self._settings_panel(uid)
                await self._menu_panel(query.message, text, kb, edit=True)
            elif action == "my_profile":
                if not self._is_privileged(uid):
                    await query.message.reply_text(
                        "⛔ Available to admin or special users."
                    )
                    return True
                if self._is_admin(uid):
                    # Admins have no view limit — no confirmation.
                    await self.my_profile_command(update, context)
                    return True
                views = storage_get_my_profile_views(uid)
                icons = self._theme_icons(uid)
                text = (
                    "Open VPN\\-profiles?\n\n"
                    f"This will use view *{views + 1} of "
                    f"{self.MY_PROFILE_VIEW_LIMIT}*\\. Messages with URL and QR "
                    "will auto\\-delete after 15 minutes\\."
                )
                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                self._btn(icons, "ok", "Open"),
                                callback_data="menu:my_profile_go",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                self._btn(icons, "back", "Back"),
                                callback_data="menu:back",
                            )
                        ],
                    ]
                )
                await self._menu_panel(query.message, text, kb, edit=True)
            elif action == "my_profile_go":
                if not self._is_privileged(uid):
                    await query.message.reply_text(
                        "⛔ Available to admin or special users."
                    )
                    return True
                # Restore the panel to the normal state, then issue profiles
                # as new messages (the counter increments inside the command).
                await self._menu_panel(
                    query.message,
                    self._user_help_panel_text(uid),
                    self._main_menu_keyboard(uid),
                    edit=True,
                )
                await self.my_profile_command(update, context)
            elif action == "exit_node":
                if not self._is_privileged(uid):
                    await query.message.reply_text(
                        "⛔ Available to admin or special users."
                    )
                    return True
                await self.exit_node_command(update, context)
            elif action == "admin_help":
                if not self._is_admin(uid):
                    await query.message.reply_text("⛔ Admin only.")
                    return True
                await self._help_show_menu(query.message)
            elif action == "admin_users":
                if not self._is_admin(uid):
                    await query.message.reply_text("⛔ Admin only.")
                    return True
                await self.admin_list_users(update, context)
            elif action == "backup":
                if not self._is_privileged(uid):
                    await query.message.reply_text(
                        "⛔ Available to admin or special users."
                    )
                    return True
                await self.backup_status(update, context)
            else:
                logger.warning("unknown menu action: %r", data)
                await query.message.reply_text("Unknown menu action.")
            return True
        except Exception as exc:
            logger.error("menu callback %r failed: %s", data, exc)
            try:
                await query.message.reply_text("❌ Failed to run the menu action.")
            except Exception:
                pass
            return True

    async def callback_query_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle callback queries from inline keyboards."""
        query = update.callback_query
        await query.answer()

        data = query.data

        # `menu:` is the only namespace for all roles; the role is checked
        # inside on every action.
        if await self._handle_menu_callbacks(update, context, query, data):
            return

        if await self._handle_ai_translate_callbacks(update, context, query, data):
            return

        # All other callbacks are admin-only.
        if not self._is_admin(query.from_user.id):
            # For inline messages (via @bot) query.message is None —
            # silently ignore someone else's tap; there is nowhere to reply.
            if query.message:
                await query.message.reply_text("⛔ Admin only.")
            return

        if await self._handle_list_users_callbacks(query, data):
            return

        if await self._handle_user_card_callbacks(query, data):
            return

        # === Help section callbacks ===
        if data.startswith("help_"):
            try:
                if data == "help_back":
                    await self._help_show_menu(query.message)
                    return
                section_text = self._HELP_SECTIONS.get(data)
                if section_text:
                    back_kb = self._help_section_keyboard(data)
                    await query.message.edit_text(
                        section_text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=back_kb,
                    )
                    return
            except Exception as e:
                logger.error(f"Error in help callback '{data}': {e}")
                # Fallback: send a new message without MarkdownV2
                section_text = self._HELP_SECTIONS.get(data, "")
                if section_text:
                    plain = (
                        section_text.replace("\\", "").replace("*", "").replace("`", "")
                    )
                    back_kb = self._help_section_keyboard(data)
                    await query.message.reply_text(plain, reply_markup=back_kb)
                return

        # === API Key callbacks ===
        if data.startswith("api_key:"):
            app_id = data.split(":", 1)[1]
            await self._show_api_key(update, app_id)
            return

        if data.startswith("show_full_api_key:"):
            app_id = data.split(":", 1)[1]
            await self._show_full_api_key(update, app_id)
            return

        if data.startswith("encryption_key:"):
            app_id = data.split(":", 1)[1]
            await self._show_encryption_key(update, app_id)
            return

        if data.startswith("show_full_enc_key:"):
            app_id = data.split(":", 1)[1]
            await self._show_full_encryption_key(update, app_id)
            return

        if data.startswith("gen_api_key:"):
            app_id = data.split(":", 1)[1]
            await self._generate_api_key(update, app_id)
            return

        if data.startswith("gen_encryption_key:"):
            app_id = data.split(":", 1)[1]
            await self._generate_encryption_key(update, app_id)
            return

        if data.startswith("del_api_key:"):
            app_id = data.split(":", 1)[1]
            await self._delete_api_key(update, app_id)
            return

        if data.startswith("del_encryption_key:"):
            app_id = data.split(":", 1)[1]
            await self._delete_encryption_key(update, app_id)
            return

        # === Model selection callbacks ===
        if data.startswith("model_select_"):
            provider = data.replace("model_select_", "")
            await self._show_model_selection(update, context, provider)
            return

        if data.startswith("model_set_"):
            parts = data.replace("model_set_", "").split("_", 1)
            if len(parts) == 2:
                provider, model = parts
                if set_current_model(provider, model):
                    await query.message.edit_text(
                        f"✅ Model for {provider.upper()} changed to {model}"
                    )
                else:
                    await query.message.edit_text(
                        f"❌ Failed to set model {model}"
                    )
            return

        # === VLESS callbacks ===
        if data == "vless_export_client":
            xray_config = vless_manager.export_xray_config(is_server=False)
            await query.message.reply_text(
                f"📱 *Xray Client Config:*\n```json\n{json.dumps(xray_config, indent=2)}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if data == "vless_export_server":
            xray_config = vless_manager.export_xray_config(is_server=True)
            config_json = json.dumps(xray_config, indent=2)

            # Print the config
            await query.message.reply_text(
                f"🖥️ *Xray Server Config:*\n```json\n{config_json}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )

            # Print the instructions
            instructions = """💡 *How to apply on the server \\(SSH\\):*

*1\\. Connect to the server:*
```
ssh root@<SERVER\\_IP>
```

*2\\. Open the nano editor:*
```
nano /usr/local/etc/xray/config\\.json
```

*3\\. In nano:*
• Delete everything: hold `Ctrl\\+K` several times
• Paste JSON: `Ctrl\\+Shift\\+V` \\(or right\\-click → Paste\\)
• Save: `Ctrl\\+O`, then `Enter`
• Exit: `Ctrl\\+X`

*4\\. Check and start:*
```
xray \\-test \\-config /usr/local/etc/xray/config\\.json
systemctl restart xray
systemctl status xray
```

✅ If you see `Active: active \\(running\\)` \\- done\\!"""
            await query.message.reply_text(
                instructions, parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        if data == "vless_export_qr_menu":
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ VLESS QR codes are admin-only."
                )
                return
            await self._show_vless_qr_selection(query.message)
            return

        if data.startswith("vless_export_qr_uuid:"):
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ VLESS QR codes are admin-only."
                )
                return
            client_uuid = data.split(":", 1)[1]
            await self._reply_vless_qr(query.message, client_uuid)
            return

        if data == "vless_export_sub_base64":
            sub_base64 = vless_manager.export_subscription_base64()
            if not sub_base64:
                await query.message.reply_text(
                    "❌ No subscription data. Check /vless_sync"
                )
                return
            await self._reply_export_file(
                query.message,
                sub_base64,
                "vless-subscription-base64.txt",
                "📦 Subscription (base64)",
            )
            return

        if data == "vless_export_sub_raw":
            links = vless_manager.export_subscription_list()
            if not links:
                await query.message.reply_text(
                    "❌ No subscription data. Check /vless_sync"
                )
                return
            raw_list = "\n".join(links)
            await self._reply_export_file(
                query.message,
                raw_list,
                "vless-subscription-raw.txt",
                "📄 Subscription (raw)",
            )
            return

        if data == "vless_export_singbox":
            singbox_config = vless_manager.export_singbox_config()
            await self._reply_export_file(
                query.message,
                json.dumps(singbox_config, indent=2, ensure_ascii=False),
                "vless-singbox-config.json",
                "🧩 Sing-box Config",
            )
            return

        if data == "vless_export_clash":
            clash_config = vless_manager.export_clash_meta_config()
            await query.message.reply_text(
                f"🧩 *Clash Meta Config:*\n```yaml\n{clash_config}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return


        # === Hysteria2 export callbacks ===

        if data == "hy2_export_singbox":
            singbox_config = hysteria2_manager.export_singbox_config()
            await query.message.reply_text(
                f"🧩 *Sing\\-box Config \\(Hysteria2\\):*\n```json\n{json.dumps(singbox_config, indent=2)}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if data == "hy2_export_clash":
            clash_config = hysteria2_manager.export_clash_meta_config()
            await query.message.reply_text(
                f"🧩 *Clash Meta Config \\(Hysteria2\\):*\n```yaml\n{clash_config}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if data == "hy2_export_server":
            server_yaml = hysteria2_manager.export_server_config_yaml()
            await query.message.reply_text(
                f"🖥️ *Server Config \\(Hysteria2\\):*\n```yaml\n{server_yaml}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if data == "hy2_export_sub_base64":
            sub = hysteria2_manager.export_subscription_base64()
            if sub:
                await self._reply_export_file(
                    query.message,
                    sub,
                    "hy2-subscription-base64.txt",
                    "📦 Subscription (base64)",
                )
            else:
                await query.message.reply_text("❌ No subscription data")
            return

        if data == "hy2_export_qr_menu":
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ Hysteria2 QR codes are admin-only."
                )
                return
            await self._show_hy2_qr_selection(query.message)
            return

        if data.startswith("hy2_export_qr_pw:"):
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ Hysteria2 QR codes are admin-only."
                )
                return
            client_password = data.split(":", 1)[1]
            await self._reply_hy2_qr(query.message, client_password)
            return

        # === MTProto export callbacks ===
        if data == "mt_export_tg_link":
            link = mtproto_manager.generate_tg_link()
            if link:
                await query.message.reply_text(
                    f"📡 *MTProto tg link:*\n`{self._escape_md2(link)}`",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await query.message.reply_text(
                    "❌ Link unavailable (server or secret is not set)"
                )
            return

        if data == "mt_export_https_link":
            link = mtproto_manager.generate_https_link()
            if link:
                await query.message.reply_text(
                    f"📡 *MTProto HTTPS link:*\n`{self._escape_md2(link)}`",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await query.message.reply_text("❌ Link unavailable")
            return


        if data == "mt_export_sub_base64":
            sub = mtproto_manager.export_subscription_base64()
            if sub:
                await self._reply_export_file(
                    query.message,
                    sub,
                    "mtproto-subscription-base64.txt",
                    "📦 Subscription (base64)",
                )
            else:
                await query.message.reply_text("❌ No subscription data")
            return

        if data == "mt_export_qr_menu":
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ MTProto QR codes are admin-only."
                )
                return
            await self._show_mt_qr_selection(query.message)
            return

        if data.startswith("mt_export_qr_name:"):
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ MTProto QR codes are admin-only."
                )
                return
            client_name = data.split(":", 1)[1]
            await self._reply_mt_qr(query.message, client_name)
            return

        if data.startswith("vless_set_sni:"):
            domain = data.split(":", 1)[1]
            success, message = vless_manager.set_vless_sni(domain)
            if not success:
                await query.message.reply_text(message)
                return
            # Write the host-Xray config immediately so Reality serverNames/dest
            # update; leave the restart for the button — so admin sees
            # the config test result before restart.
            apply_ok, apply_msg = vless_manager.apply_xray_config()
            purged = await self._purge_all_profile_messages(query.get_bot())
            purge_note = (
                f"\n🧹 Old links/QR purged for everyone ({purged})."
                if purged
                else ""
            )
            text = (
                f"{message}\n{apply_msg}{purge_note}\n\n"
                "After restart, re-issue URI/QR to clients: "
                "/vless_qr <name>, /profiles <id> or /my_profile"
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Restart Xray",
                            callback_data="xray_restart_after_port",
                        )
                    ]
                ]
            )
            await query.message.reply_text(text, reply_markup=keyboard)
            if not apply_ok:
                logger.warning("vless_set_sni callback apply failed: %s", apply_msg)
            return

        if data.startswith("hy2_hub:"):
            if not self._is_admin(query.from_user.id):
                await query.answer("⛔ Admin only.", show_alert=True)
                return
            action = data.split(":", 1)[1]
            await query.answer()
            if action == "set_sni":
                await self._show_hy2_sni_picker(query.message)
                return
            if action == "status":
                # Light status without the full MD2 /hy2_status report.
                try:
                    st = hysteria2_manager.get_status()
                except Exception as exc:
                    await query.message.reply_text(f"❌ Status: {exc}")
                    return
                await query.message.reply_text(
                    "⚡ Hysteria2\n"
                    f"enabled: {'yes' if st.get('enabled') else 'no'}\n"
                    f"service: {st.get('service_active')}\n"
                    f"server: {st.get('server')}:{st.get('port')}\n"
                    f"sni: {st.get('sni') or '—'}\n"
                    f"insecure: {st.get('insecure')}\n"
                    f"clients: {st.get('clients_count')}\n\n"
                    "Details: /hy2_status"
                )
                return
            if action == "on":
                _ok, message = hysteria2_manager.enable()
                await query.message.reply_text(message)
                return
            if action == "apply":
                _ok, message = hysteria2_manager.apply_config()
                await query.message.reply_text(message)
                return
            if action == "start":
                _ok, message = hysteria2_manager.service_control("start")
                await query.message.reply_text(message)
                return
            if action == "gen_all":
                await query.message.reply_text(
                    "Send the command:\n/hy2_gen_all\n"
                    "(password + certificate + IP — better explicitly from chat)"
                )
                return
            await query.message.reply_text(f"Unknown action: {action}")
            return

        if data.startswith("hy2_set_sni:"):
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text("⛔ Admin only.")
                return
            domain = data.split(":", 1)[1]
            success, message = hysteria2_manager.set_sni(domain)
            if not success:
                await query.message.reply_text(message)
                return
            cert_ok, cert_msg = hysteria2_manager.generate_self_signed_cert(
                domain=domain
            )
            message = f"{message}\n{cert_msg}"
            if not cert_ok:
                logger.warning("hy2_set_sni callback cert failed: %s", cert_msg)
            purged = await self._purge_all_profile_messages(query.get_bot())
            purge_note = (
                f"\n🧹 Old links/QR purged for everyone ({purged})."
                if purged
                else ""
            )
            text = f"{message}{purge_note}{self._hy2_apply_followup_text()}"
            await query.message.reply_text(text)
            return

        if data == "vless_reset_confirm":
            success, message = vless_manager.reset_config()
            await query.message.edit_text(message)
            return

        # === Xray restart after port change ===
        if data == "xray_restart_after_port":
            await query.answer("⏳ Restarting Xray...")

            success, message = vless_manager.restart_xray()

            # Update the message with the result
            original_text = query.message.text
            new_text = (
                f"{original_text}\n\n{'✅' if success else '❌'} Restart: {message}"
            )

            await query.edit_message_text(
                text=new_text,
                reply_markup=None,  # Remove the button
            )
            return

        if data == "vless_reset_cancel":
            await query.message.edit_text("❌ Configuration reset cancelled")
            return

    # === HELPER METHODS ===

    async def _show_api_key(self, update: Update, app_id: str):
        """Show the API key for app_id (masked)."""
        try:
            from app_keys import get_api_key, has_api_key

            if app_id == "default":
                api_key = os.getenv("API_SECRET_KEY", "")
                source = "from \\.env"
            else:
                api_key = get_api_key(app_id)
                # Check whether this app_id has an individual key
                if has_api_key(app_id):
                    source = "individual"
                else:
                    source = "default"

            if api_key:
                masked = self._mask_secret(api_key).replace(".", "\\.")
                message = f"🔑 API key \\({source}\\):\n\n`{masked}`"
                if self._secret_reveal_allowed():
                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "👁️ Show in full",
                                callback_data=f"show_full_api_key:{app_id}",
                            )
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await update.callback_query.message.reply_text(
                        message,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=reply_markup,
                    )
                else:
                    message += "\n\n⚠️ Full secret reveal over Telegram is disabled by default\\."
                    await update.callback_query.message.reply_text(
                        message, parse_mode=ParseMode.MARKDOWN_V2
                    )
            else:
                app_id_escaped = escape_markdown(app_id)
                message = f"❌ API key not found for {app_id_escaped}"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error showing API key: {e}")
            await update.callback_query.message.reply_text(
                "Failed to get API key"
            )

    async def _show_full_api_key(self, update: Update, app_id: str):
        """Show the full API key for app_id."""
        try:
            if not self._secret_reveal_allowed():
                await update.callback_query.message.reply_text(
                    "⛔ Full API key reveal over Telegram is disabled. "
                    "If this is really needed, set `TELEGRAMHELPER_ALLOW_SECRET_REVEAL=true` only temporarily on the server."
                )
                return

            from app_keys import get_api_key, has_api_key

            if app_id == "default":
                api_key = os.getenv("API_SECRET_KEY", "")
                source = "from \\.env"
            else:
                api_key = get_api_key(app_id)
                # Check whether this app_id has an individual key
                if has_api_key(app_id):
                    source = "individual"
                else:
                    source = "default"

            # API server URL
            api_url = os.getenv("API_URL", "http://localhost:8000/ai_query")

            if api_key:
                message = f"""🔑 API key \\({source}\\):

📍 *URL:*
`{api_url}`

🔐 *API Key:*
`{api_key}`

⚠️ _Copy this and delete the message_"""
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                app_id_escaped = escape_markdown(app_id)
                message = f"❌ API key not found for {app_id_escaped}"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error showing full API key: {e}")
            await update.callback_query.message.reply_text(
                "Failed to get API key"
            )

    async def _show_encryption_key(self, update: Update, app_id: str):
        """Show the encryption key for app_id (masked)."""
        try:
            from app_keys import get_encryption_key, has_encryption_key

            if app_id == "default":
                enc_key = os.getenv("ENCRYPTION_KEY", "")
                source = "from \\.env"
            else:
                enc_key = get_encryption_key(app_id, force_reload=True)
                # Check whether this app_id has an individual key
                if has_encryption_key(app_id, force_reload=True):
                    source = "individual"
                else:
                    source = "default"

            if enc_key:
                masked = self._mask_secret(enc_key).replace(".", "\\.")
                message = f"🔐 Encryption key \\({source}\\):\n\n`{masked}`"
                if self._secret_reveal_allowed():
                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "👁️ Show in full",
                                callback_data=f"show_full_enc_key:{app_id}",
                            )
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await update.callback_query.message.reply_text(
                        message,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=reply_markup,
                    )
                else:
                    message += "\n\n⚠️ Full secret reveal over Telegram is disabled by default\\."
                    await update.callback_query.message.reply_text(
                        message, parse_mode=ParseMode.MARKDOWN_V2
                    )
            else:
                app_id_escaped = escape_markdown(app_id)
                message = f"❌ Encryption key not found for {app_id_escaped}"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error showing encryption key: {e}")
            await update.callback_query.message.reply_text(
                "Failed to get encryption key"
            )

    async def _show_full_encryption_key(self, update: Update, app_id: str):
        """Show the full encryption key for app_id."""
        try:
            if not self._secret_reveal_allowed():
                await update.callback_query.message.reply_text(
                    "⛔ Full encryption-key reveal over Telegram is disabled. "
                    "If this is really needed, set `TELEGRAMHELPER_ALLOW_SECRET_REVEAL=true` only temporarily on the server."
                )
                return

            from app_keys import get_encryption_key, has_encryption_key

            if app_id == "default":
                enc_key = os.getenv("ENCRYPTION_KEY", "")
                source = "from \\.env"
            else:
                enc_key = get_encryption_key(app_id, force_reload=True)
                # Check whether this app_id has an individual key
                if has_encryption_key(app_id, force_reload=True):
                    source = "individual"
                else:
                    source = "default"

            if enc_key:
                message = f"🔐 Encryption key \\({source}\\):\n\n`{enc_key}`\n\n⚠️ _Copy this and delete the message_"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                app_id_escaped = escape_markdown(app_id)
                message = f"❌ Encryption key not found for {app_id_escaped}"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error showing full encryption key: {e}")
            await update.callback_query.message.reply_text(
                "Failed to get encryption key"
            )

    async def _generate_api_key(self, update: Update, app_id: str):
        """Generate a new API key."""
        try:
            new_key = secrets.token_hex(32)

            if app_id == "default":
                if not self._secret_reveal_allowed():
                    await update.callback_query.message.reply_text(
                        "⛔ Generating the default API key via Telegram is disabled in safe mode.\n"
                        "Generate the key locally on the server and update `API_SECRET_KEY` in `.env`."
                    )
                    return

                message = f"""✅ New API key generated:

`{new_key}`

⚠️ Add it to \\.env as API\\_SECRET\\_KEY
🔄 After changing \\.env, restart the container"""
            else:
                from app_keys import set_api_key

                set_api_key(app_id, new_key)
                app_id_escaped = escape_markdown(app_id)
                masked = self._mask_secret(new_key).replace(".", "\\.")
                if self._secret_reveal_allowed():
                    message = f"""✅ API key for {app_id_escaped} generated and saved:

`{new_key}`

💾 Saved in app\\_keys\\.json
🔄 Changes apply on the next request"""
                else:
                    message = f"""✅ API key for {app_id_escaped} generated and saved:

`{masked}`

⚠️ The full secret is not sent over Telegram
💾 Saved in app\\_keys\\.json"""

            await update.callback_query.message.reply_text(
                message, parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error generating API key: {e}")
            await update.callback_query.message.reply_text(
                "Failed to generate API key"
            )

    async def _generate_encryption_key(self, update: Update, app_id: str):
        """Generate a new encryption key."""
        try:
            new_key = secrets.token_hex(32)

            if app_id == "default":
                if not self._secret_reveal_allowed():
                    await update.callback_query.message.reply_text(
                        "⛔ Generating the default encryption key via Telegram is disabled in safe mode.\n"
                        "Generate the key locally on the server and update `ENCRYPTION_KEY` in `.env`."
                    )
                    return

                message = f"""✅ New encryption key generated:

`{new_key}`

⚠️ Add it to \\.env as ENCRYPTION\\_KEY
🔄 After changing \\.env, restart the container"""
            else:
                from app_keys import set_encryption_key

                set_encryption_key(app_id, new_key)
                app_id_escaped = escape_markdown(app_id)
                masked = self._mask_secret(new_key).replace(".", "\\.")
                if self._secret_reveal_allowed():
                    message = f"""✅ Encryption key for {app_id_escaped} generated and saved:

`{new_key}`

💾 Saved in app\\_keys\\.json
🔄 Changes apply on the next request"""
                else:
                    message = f"""✅ Encryption key for {app_id_escaped} generated and saved:

`{masked}`

⚠️ The full secret is not sent over Telegram
💾 Saved in app\\_keys\\.json"""

            await update.callback_query.message.reply_text(
                message, parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error generating encryption key: {e}")
            await update.callback_query.message.reply_text(
                "Failed to generate encryption key"
            )

    async def _delete_api_key(self, update: Update, app_id: str):
        """Delete an API key."""
        try:
            from app_keys import delete_api_key

            if delete_api_key(app_id):
                message = f"✅ API key for {app_id} deleted"
            else:
                message = f"❌ Failed to delete API key for {app_id}"

            await update.callback_query.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error deleting API key: {e}")
            await update.callback_query.message.reply_text(
                "Failed to delete API key"
            )

    async def _delete_encryption_key(self, update: Update, app_id: str):
        """Delete an encryption key."""
        try:
            from app_keys import delete_encryption_key

            if delete_encryption_key(app_id):
                message = f"✅ Encryption key for {app_id} deleted"
            else:
                message = f"❌ Failed to delete encryption key for {app_id}"

            await update.callback_query.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error deleting encryption key: {e}")
            await update.callback_query.message.reply_text(
                "Failed to delete encryption key"
            )

    async def _show_model_selection(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, provider: str
    ):
        """Show model picker for a provider."""
        try:
            models = get_available_models(provider)
            current_model = get_current_model(provider)

            keyboard = []
            for model in models:
                label = f"✅ {model}" if model == current_model else model
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            label, callback_data=f"model_set_{provider}_{model}"
                        )
                    ]
                )

            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.message.edit_text(
                f"🤖 Select a model for {provider.upper()}:", reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error showing model selection: {e}")
            await update.callback_query.message.edit_text(
                "Failed to load the model list"
            )

    # ================================================================
    # HYSTERIA2 COMMANDS
    # ================================================================

    def _escape_md2(self, text):
        """Escape special characters for Telegram Markdown V2."""
        if not text:
            return "not configured"
        text = str(text)
        for char in [
            "_",
            "*",
            "[",
            "]",
            "(",
            ")",
            "~",
            "`",
            ">",
            "#",
            "+",
            "-",
            "=",
            "|",
            "{",
            "}",
            ".",
            "!",
        ]:
            text = text.replace(char, f"\\{char}")
        return text

    async def hy2_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2 command — Hysteria2 hub (shown in autocomplete when typing /hy2)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📊 Status", callback_data="hy2_hub:status"
                        ),
                        InlineKeyboardButton(
                            "🌐 SNI", callback_data="hy2_hub:set_sni"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🟢 On (for issuance)", callback_data="hy2_hub:on"
                        ),
                        InlineKeyboardButton(
                            "✅ Apply", callback_data="hy2_hub:apply"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "▶️ Start", callback_data="hy2_hub:start"
                        ),
                        InlineKeyboardButton(
                            "🛠 Gen all", callback_data="hy2_hub:gen_all"
                        ),
                    ],
                ]
            )
            await update.message.reply_text(
                "⚡ Hysteria2 — choose an action\n\n"
                "Manual commands: /hy2_status /hy2_set_sni /hy2_on "
                "/hy2_apply /hy2_start",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Error in hy2_command: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_status command — show Hysteria2 status."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested Hysteria2 status")
            status = hysteria2_manager.get_status()

            esc = self._escape_md2
            enabled = bool(status["enabled"])
            configured = bool(status["configured"])
            active = status.get("service_active")  # True / False / None

            profile_emoji = "🟢" if enabled else "🔴"
            config_emoji = "✅" if configured else "❌"
            if active is True:
                service_line = "🟢 running \\(active\\)"
            elif active is False:
                service_line = "🔴 stopped \\(inactive\\)"
            else:
                service_line = "❔ unknown"

            binary_path = status.get("binary_path") or ""
            unit_ok = bool(status.get("unit_exec_ok"))
            unit_path = status.get("unit_exec_path") or ""

            # Next-step hint — so the playbook does not dead-end.
            if not binary_path:
                next_step = (
                    "➡️ *Next:* `/hy2_install` — the Hysteria2 binary was not found "
                    "on the host \\(otherwise you get 203/EXEC\\)\\."
                )
            elif unit_path and not unit_ok:
                next_step = (
                    "➡️ *Next:* `/hy2_install` — systemd ExecStart points "
                    "at a missing file\\. The command will repair the unit\\."
                )
            elif not configured:
                next_step = "➡️ *Next:* `/hy2_gen_all` \\(password \\+ certificate \\+ IP\\), then `/hy2_apply`\\."
            elif not enabled:
                next_step = (
                    "➡️ *Next:* `/hy2_on` — mark the profile active "
                    "\\(needed for `/provision`, `/my_profile`, and issuing URI/QR\\)\\."
                )
            elif active is False:
                next_step = "➡️ *Next:* `/hy2_start` — the service is not running on the server\\."
            elif active is None:
                next_step = "⚠️ Could not check systemd \\(SSH/`systemctl`\\)\\. Check `/hy2_logs`\\."
            else:
                next_step = (
                    "✅ All set\\. Client: `/hy2_add_client <name>` → `/hy2_qr <name>` "
                    "or issue via `/provision <id>`\\."
                )

            binary_line = (
                f"• Binary: `{esc(binary_path)}`"
                if binary_path
                else "• Binary: ❌ not found"
            )
            if unit_path:
                unit_line = (
                    f"• Unit ExecStart: `{esc(unit_path)}` "
                    + ("✅" if unit_ok else "❌ no file")
                )
            else:
                unit_line = "• Unit ExecStart: ❌ unit not found"

            message = f"""⚡ *Hysteria2 Status*

*Profile \\(enabled\\):* {profile_emoji} {"on" if enabled else "off"}
*Service \\(systemd\\):* {service_line}
*Config:* {config_emoji} {"configured" if configured else "not configured"}

*Parameters:*
• Server: `{esc(status.get("server"))}`
• Port: `{esc(status.get("port", 443))}` \\(UDP\\)
• SNI: `{esc(status.get("sni") or "(auto)")}`
• Insecure: {"yes ⚠️" if status.get("insecure") else "no ✅"}
{binary_line}
{unit_line}

*Obfuscation:* {("✅ " + esc(status.get("obfs_type", ""))) if status.get("has_obfs") else "❌ off"}
*Speed:* ↑ {status.get("up_mbps", 0) or "auto"} / ↓ {status.get("down_mbps", 0) or "auto"} Mbps
*Masquerade:* `{esc(status.get("masquerade_url", ""))}`
*Password:* {"✅" if status["has_password"] else "❌"}
*Clients:* {status.get("clients_count", 0)}

{next_step}

*Updated:* {esc(status.get("updated_at", "never"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in hy2_status: {e}")
            await update.message.reply_text(f"Error: {e}")

    # === Reticulum / HA stack ===

    async def reticulum_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/reticulum_status command — HA stack and Reticulum bridge status."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return
            import reticulum_manager

            st = reticulum_manager.get_status()
            if not st["installed"]:
                await update.message.reply_text(
                    "🛰 HA stack / Reticulum is not installed on this server."
                )
                return

            def mark(b):
                return "🟢" if b else "🔴"

            svc = st["services"]
            lines = [
                "🛰 Reticulum / HA stack",
                "",
                f"{mark(svc.get('ha-reticulum-bridge'))} ha-reticulum-bridge",
                f"{mark(svc.get('ha-stub-grpc'))} ha-stub-grpc",
                f"{mark(svc.get('ha-stub-udp'))} ha-stub-udp",
                f"Bridge listening on :50061 — {'yes' if st['listening'] else 'no'}",
                "",
                f"Bridge hash: {st['bridge_hash'] or '(appears in the start log)'}",
            ]
            if st.get("i2pd_installed"):
                lines += [
                    "",
                    f"{mark(st.get('i2pd_active'))} i2pd (I2P, path 2)",
                    f"I2P b32: {st.get('i2p_b32') or '(tunnel building / none)'}",
                ]
            lines += [
                "",
                "Manage: /reticulum_restart, /reticulum_hash, /reticulum_i2p",
                "Round-trip test — from the UDP_gRPC_COM_Lite CLI (RETICULUM_GUIDE.md).",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in reticulum_status: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def reticulum_restart(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/reticulum_restart command — restart the HA stack (3 services)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            import reticulum_manager

            ok, msg = reticulum_manager.restart()
            await update.message.reply_text(("✅ " if ok else "❌ ") + msg)
        except Exception as e:
            logger.error(f"Error in reticulum_restart: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def reticulum_hash(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/reticulum_hash command — bridge destination hash (for clients)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            import reticulum_manager

            h = reticulum_manager.get_bridge_hash()
            if h:
                await update.message.reply_text(
                    f"🛰 Bridge hash:\n`{h}`", parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await update.message.reply_text(
                    "Bridge hash not found (bridge is not running or missing from the start log)."
                )
        except Exception as e:
            logger.error(f"Error in reticulum_hash: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def reticulum_i2p(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/reticulum_i2p command — I2P path status (i2pd + server tunnel b32)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            import reticulum_manager

            i = reticulum_manager.get_i2p_status()
            if not i["installed"]:
                await update.message.reply_text(
                    "🛰 i2pd is not installed — I2P path (stage 3) is not set up on this server."
                )
                return
            lines = [
                "🛰 Reticulum I2P (path 2)",
                "",
                f"{'🟢' if i['active'] else '🔴'} i2pd",
                f"Bridge b32: {i['b32'] or '(ha-bridge server tunnel building / none)'}",
                "",
                "Client: i2pd client tunnel → this b32, RNS over TCP on 127.0.0.1:50061.",
                "Details — RETICULUM_GUIDE.md §8 (I2P path).",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in reticulum_i2p: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def reticulum_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/reticulum_health command — i2pd health (network, tunnel success, leasesets)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            import reticulum_manager

            h = reticulum_manager.get_i2p_health()
            if not h["installed"]:
                await update.message.reply_text(
                    "🛰 i2pd is not installed — I2P path (stage 3) is not set up on this server."
                )
                return
            if not h["active"]:
                await update.message.reply_text("🔴 i2pd is not running. Start it: systemctl start i2pd")
                return
            lines = [
                "🩺 i2pd health (I2P, path 2)",
                "",
                f"Network status:  {h['network'] or '—'}",
                f"Tunnel success:  {h['success_rate'] or '—'}",
                f"Routers:         {h['routers'] or '—'}  (floodfills {h['floodfills'] or '—'})",
                f"LeaseSets:       {h['leasesets'] or '—'}",
                f"Transit tunnels: {h['transit'] or '—'}",
                f"Uptime:          {h['uptime'] or '—'}",
                "",
                "💡 Fresh node: low success rate and LeaseSets=0 is normal for the first minutes;",
                "bridge b32 is published after tunnels warm up. b32 — /reticulum_i2p.",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in reticulum_health: {e}")
            await update.message.reply_text(f"Error: {e}")

    # === Administrator management ===

    async def admin_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/admin_list command — administrator list (primary admins are protected)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            import storage

            founders = list(self.config.admin_user_ids or [])
            dynamic = [a for a in storage.get_dynamic_admins() if a not in founders]
            lines = ["👑 Administrators:", ""]
            for f in founders:
                lines.append(f"🔒 {f} — primary (cannot be removed)")
            for d in dynamic:
                lines.append(f"• {d} — assigned")
            if not dynamic:
                lines.append("(no dynamically assigned admins)")
            lines += [
                "",
                "Assign: /admin_add <user_id>",
                "Remove: /admin_remove <user_id>",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in admin_list: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def admin_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/admin_add command <user_id> — make a user an administrator."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /admin_add <user_id>\n"
                    "Assign from special users; add a regular user first: /special_add <id>"
                )
                return
            try:
                uid = int(args[0])
            except ValueError:
                await update.message.reply_text("user_id must be a number.")
                return
            if self.config.is_admin(uid):
                await update.message.reply_text(f"{uid} is already an administrator.")
                return
            import storage

            storage.add_dynamic_admin(uid)
            is_special = self.config.is_special_user(uid) or storage.is_special_user(
                uid
            )
            note = (
                ""
                if is_special
                else "\n⚠️ This user is not on the special list (you can /special_add)."
            )
            await update.message.reply_text(f"✅ {uid} is now an administrator.{note}")
        except Exception as e:
            logger.error(f"Error in admin_add: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def admin_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/admin_remove command <user_id> — remove an administrator (except primary)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /admin_remove <user_id>"
                )
                return
            try:
                uid = int(args[0])
            except ValueError:
                await update.message.reply_text("user_id must be a number.")
                return
            if self.config.is_founder_admin(uid):
                await update.message.reply_text(
                    "🔒 This is a primary admin (set at bot install) — cannot be removed."
                )
                return
            import storage

            if not storage.is_dynamic_admin(uid):
                await update.message.reply_text(
                    f"{uid} is not an assigned admin."
                )
                return
            storage.remove_dynamic_admin(uid)
            await update.message.reply_text(f"✅ {uid} was removed from administrators.")
        except Exception as e:
            logger.error(f"Error in admin_remove: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_on command — enable Hysteria2."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = hysteria2_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_on: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_off command — disable Hysteria2."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = hysteria2_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_off: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_config command — show the current configuration."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            config = hysteria2_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"⚡ Hysteria2 configuration:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Error in hy2_config: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_server(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_set_server command <ip> — set the server."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = hysteria2_manager.set_server(server)
            if success:
                message += (
                    "\n\n📲 Re-issue URI/QR: /profiles <id>  or  /my_profile"
                    "\nℹ️ /hy2_apply is not required — only the address in the client link changes."
                )
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_server: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_set_port command <port> — set the port."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /hy2_set_port <port>")
                return
            try:
                port = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ Port must be a number")
                return
            success, message = hysteria2_manager.set_port(port)
            if success:
                message += self._hy2_apply_followup_text(port=port)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_port: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/hy2_set_password command <pass> — set the password."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /hy2_set_password <password>"
                )
                return
            password = args[0]
            success, message = hysteria2_manager.set_password(password)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_password: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_obfs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_set_obfs command <type> <password> — set obfuscation."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage:\n"
                    "/hy2_set_obfs salamander <password> — enable\n"
                    "/hy2_set_obfs off — disable"
                )
                return
            obfs_type = args[0]
            if obfs_type == "off":
                success, message = hysteria2_manager.set_obfs("", "")
            else:
                obfs_password = args[1] if len(args) > 1 else ""
                success, message = hysteria2_manager.set_obfs(obfs_type, obfs_password)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_obfs: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_sni(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_set_sni command — Hy2 SNI/camouflage (buttons like /vless_set_sni)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                # Reply immediately so the command does not look “silent” while
                # buttons are built (get_status() used to hang on systemd).
                await update.message.reply_text("⏳ Hysteria2 SNI picker…")
                await self._show_hy2_sni_picker(update.message)
                return
            success, message = hysteria2_manager.set_sni(args[0])
            if success:
                # Self-signed cert CN must match the new SNI.
                cert_ok, cert_msg = hysteria2_manager.generate_self_signed_cert(
                    domain=args[0].strip()
                )
                message = f"{message}\n{cert_msg}"
                if not cert_ok:
                    logger.warning("hy2_set_sni cert regen failed: %s", cert_msg)
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_sni: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def _show_hy2_sni_picker(self, message) -> None:
        """Hy2 SNI picker buttons (current ✅); change = SNI+masquerade+cert."""
        # JSON only — no get_status()/systemd, otherwise the command “goes silent” for tens of seconds.
        try:
            current = (hysteria2_manager.get_config(include_secrets=False) or {}).get(
                "sni", ""
            ) or ""
        except Exception:
            current = ""

        domains = list(getattr(hysteria2_manager, "AVAILABLE_SNI", None) or [])
        if not domains:
            # Older image without AVAILABLE_SNI — still offer working options.
            domains = [
                "yahoo.com",
                "www.cloudflare.com",
                "www.amazon.com",
                "www.microsoft.com",
                "www.apple.com",
                "www.google.com",
            ]

        sni_labels = {
            "yahoo.com": "Yahoo",
            "www.cloudflare.com": "Cloudflare",
            "www.amazon.com": "Amazon",
            "www.microsoft.com": "Microsoft",
            "www.apple.com": "Apple",
            "www.google.com": "Google",
        }

        rows = []
        for domain in domains:
            mark = "✅ " if domain == current else ""
            brand = sni_labels.get(domain, domain)
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{mark}{brand} — {domain}",
                        callback_data=f"hy2_set_sni:{domain}",
                    )
                ]
            )

        keyboard = InlineKeyboardMarkup(rows)
        plain = (
            "🌐 SNI picker (Hysteria2 TLS)\n\n"
            f"Current: {current or '—'}\n\n"
            "Tap a domain — the bot will change SNI + masquerade, reissue "
            "the self-signed certificate (CN=SNI), and offer /hy2_apply.\n\n"
            "Custom domain: /hy2_set_sni example.com"
        )
        try:
            await message.reply_text(plain, reply_markup=keyboard)
        except Exception as exc:
            logger.error("_show_hy2_sni_picker failed: %s", exc)
            await message.reply_text(
                "Could not show buttons. Change it manually:\n"
                "/hy2_set_sni yahoo.com\n"
                "then /hy2_apply"
            )

    async def hy2_set_speed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_set_speed command <up> <down> — set speed (Mbps)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if len(args) < 2:
                await update.message.reply_text(
                    "Usage: /hy2_set_speed <up_mbps> <down_mbps>\n0 = auto"
                )
                return
            try:
                up = int(args[0])
                down = int(args[1])
            except ValueError:
                await update.message.reply_text("❌ Speed must be a number")
                return
            success, message = hysteria2_manager.set_speed(up, down)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_speed: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_masquerade(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/hy2_set_masquerade command <url> — set the masquerade URL."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /hy2_set_masquerade <url>"
                )
                return
            success, message = hysteria2_manager.set_masquerade(args[0])
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_masquerade: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_insecure(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/hy2_set_insecure command <1|0> — insecure TLS (self-signed)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args or args[0] not in ("0", "1"):
                await update.message.reply_text(
                    "Usage: /hy2_set_insecure 1  or  /hy2_set_insecure 0"
                )
                return
            insecure = args[0] == "1"
            success, message = hysteria2_manager.set_insecure(insecure)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_insecure: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_quic_safe(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/hy2_set_quic_safe command <1|0> — safe-QUIC defaults (fixes Windows clients)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args or args[0] not in ("0", "1"):
                await update.message.reply_text(
                    "Usage: /hy2_set_quic_safe 1 | 0\n\n"
                    "1 — enable safe QUIC defaults (disablePathMTUDiscovery + receive windows).\n"
                    "0 — disable (classic Hysteria2 config).\n\n"
                    "After the change: /hy2_apply"
                )
                return
            enabled = args[0] == "1"
            success, message = hysteria2_manager.set_quic_safe(enabled)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_quic_safe: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_set_quic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_set_quic command <param> <value> — fine-tune QUIC."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if len(args) < 2:
                await update.message.reply_text(
                    "Usage: /hy2_set_quic <param> <value>\n\n"
                    "Parameters:\n"
                    "enabled, disable_path_mtu_discovery,\n"
                    "init_stream_receive_window, max_stream_receive_window,\n"
                    "init_conn_receive_window, max_conn_receive_window,\n"
                    "max_idle_timeout, keep_alive_period\n\n"
                    "Examples:\n"
                    "/hy2_set_quic max_idle_timeout 45s\n"
                    "/hy2_set_quic keep_alive_period 15s\n"
                    "/hy2_set_quic init_conn_receive_window 2097152"
                )
                return
            success, message = hysteria2_manager.set_quic_param(args[0], args[1])
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_quic: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_gen_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/hy2_gen_password command — generate and set a password."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            password = hysteria2_manager.generate_password()
            success, message = hysteria2_manager.set_password(password)
            if success:
                await update.message.reply_text(
                    f"{message}\n🔑 Password: `{password}`",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_gen_password: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_gen_cert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_gen_cert command — generate a TLS certificate."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            await update.message.reply_text("⏳ Generating TLS certificate...")
            success, message = hysteria2_manager.generate_self_signed_cert()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_gen_cert: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_gen_all command — generate everything (password + certificate + IP)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            await update.message.reply_text(
                "⏳ Generating password, certificate, and detecting IP..."
            )
            success, data, message = hysteria2_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_gen_all: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_add_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_add_client command <name> — add a client."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /hy2_add_client <name>")
                return
            name = args[0]
            success, message, client = hysteria2_manager.add_client(name)
            if success and client:
                _ok, _msg, uri = hysteria2_manager.generate_client_uri(name)
                message += f"\n🔗 URI: `{uri}`"
            await update.message.reply_text(message)
            if success and client:
                await self._reply_hy2_qr(update.message, client.get("password", name))
        except Exception as e:
            logger.error(f"Error in hy2_add_client: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_qr command — show a QR for a Hysteria2 client."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text(
                    "⛔ Hysteria2 QR codes are admin-only."
                )
                return
            if await self._legacy_per_client_guard(update):
                return

            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Usage: /hy2_qr <client_name_or_password>"
                )
                return

            await self._reply_hy2_qr(update.message, args[0])
        except Exception as e:
            logger.error(f"Error in hy2_qr: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_del_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_del_client command <name> — remove a client."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /hy2_del_client <name>")
                return
            success, message = hysteria2_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_del_client: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/hy2_list_clients command — client list."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            clients = hysteria2_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 No clients")
                return
            lines = ["⚡ *Hysteria2 clients:*\n"]
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                pw = c.get("password", "")
                masked = f"{pw[:4]}..." if len(pw) > 4 else "***"
                created = c.get("created_at", "")[:10]
                lines.append(
                    f"{i}\\. `{self._escape_md2(name)}` — password: `{self._escape_md2(masked)}` \\({self._escape_md2(created)}\\)"
                )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in hy2_list_clients: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_install command — install Hysteria2 on the server."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            await update.message.reply_text("⏳ Installing Hysteria2...")
            success, message = hysteria2_manager.install_hysteria2()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_install: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_apply command — apply the config to the server."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = hysteria2_manager.apply_config()
            # Changing the Hysteria2 config changes hy2:// links — old links/QR
            # must no longer be shown to any user.
            purged = await self._purge_all_profile_messages(update.get_bot())
            if purged:
                message += (
                    f"\n\n🧹 Old links/QR purged for everyone ({purged}). "
                    "Re-issue fresh ones: /profiles <id> or /my_profile."
                )
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_apply: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_start command — start the service."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = hysteria2_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in hy2_start: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_stop command — stop the service."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = hysteria2_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_stop: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_restart command — restart the service."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = hysteria2_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_restart: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_logs command — show logs."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = hysteria2_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 Hysteria2 logs:\n```\n{output}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Error in hy2_logs: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def hy2_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/hy2_export command — export configurations."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return

            logger.info(f"Admin {update.effective_user.id} exporting Hysteria2 config")

            # Generate all export formats
            uri = hysteria2_manager.generate_hy2_uri()
            client_config = hysteria2_manager.export_client_config()
            singbox_config = hysteria2_manager.export_singbox_config()
            server_yaml = hysteria2_manager.export_server_config_yaml()

            esc = self._escape_md2

            parts = [f"⚡ *Hysteria2 Export*\n"]

            if uri:
                parts.append(f"*URI \\(for the client\\):*\n`{esc(uri)}`\n")

            parts.append(
                f"*Client Config \\(native\\):*\n```json\n{json.dumps(client_config, indent=2)}\n```\n"
            )
            parts.append(
                f"*Sing\\-Box Config:*\n```json\n{json.dumps(singbox_config, indent=2)}\n```\n"
            )
            parts.append(f"*Server Config \\(YAML\\):*\n```yaml\n{server_yaml}\n```")

            keyboard = [
                [
                    InlineKeyboardButton(
                        "📷 QR by client", callback_data="hy2_export_qr_menu"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🧩 Sing-box Config", callback_data="hy2_export_singbox"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🧩 Clash Meta Config", callback_data="hy2_export_clash"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🖥️ Server Config (YAML)", callback_data="hy2_export_server"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📦 Subscription (base64)",
                        callback_data="hy2_export_sub_base64",
                    )
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            message = "\n".join(parts)
            # Telegram has 4096 char limit — split if needed
            if len(message) > 4000:
                # Send URI first
                if uri:
                    await update.message.reply_text(
                        f"⚡ *Hysteria2 URI:*\n`{esc(uri)}`",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                # Send configs as file-like message with buttons
                await update.message.reply_text(
                    f"```json\n{json.dumps(client_config, indent=2)}\n```",
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup,
                )
            else:
                await update.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
                )

        except Exception as e:
            logger.error(f"Error in hy2_export: {e}")
            await update.message.reply_text(f"Error: {e}")


    # === MTPROTO PROXY COMMANDS ===

    async def mt_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_status command — show MTProto proxy status."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ This command is admin-only."
                )
                return

            logger.info(f"Admin {user.id} requested MTProto status")
            status = mtproto_manager.get_status()

            esc = self._escape_md2
            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            message = f"""📡 *MTProto Proxy Status*

*State:* {status_emoji} {"On" if status["enabled"] else "Off"}
*Config:* {config_emoji} {"Configured" if status["configured"] else "Not configured"}

*Parameters:*
• Server: `{esc(status.get("server") or "(not set)")}`
• Port: `{esc(str(status.get("port", 993)))}` \\(TCP\\)
• Mode: `{esc(status.get("secret_mode_label") or status.get("secret_mode") or "?")}`
• Secret: {"✅" if status["has_secret"] else "❌"}
• Fake\\-TLS: {"✅ " + esc(status.get("fake_tls_domain", "")) if status.get("is_fake_tls") else "❌ off"}
• Tag: `{esc(status.get("tag") or "(none)")}`
• Workers: {status.get("workers", 2)}
• Clients: {status.get("clients_count", 0)}

*Updated:* {esc(str(status.get("updated_at") or "never"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in mt_status: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_on command — enable MTProto proxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mtproto_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_on: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_off command — disable MTProto proxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mtproto_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_off: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_config command — show the current configuration."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            config = mtproto_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"📡 MTProto configuration:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Error in mt_config: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_set_server(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_set_server command <ip> — set the server."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = mtproto_manager.set_server(server)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_server: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_set_port command <port> — set the port."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /mt_set_port <port>")
                return
            try:
                port = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ Port must be a number")
                return
            success, message = mtproto_manager.set_port(port)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_port: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_set_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_set_mode command <dd_inline|ee_split> — switch MTProto mode."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /mt_set_mode <mode>\n"
                    f"Available: `{mtproto_manager.SECRET_MODE_DD_INLINE}`, `{mtproto_manager.SECRET_MODE_EE_SPLIT}`\n"
                    "For new servers `ee_split` is usually the right choice."
                )
                return
            success, message = mtproto_manager.set_secret_mode(args[0])
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in mt_set_mode: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_set_domain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_set_domain command <domain> — set the fake-TLS domain."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                domains = ", ".join(mtproto_manager.AVAILABLE_FAKE_TLS_DOMAINS)
                await update.message.reply_text(
                    f"Usage: /mt_set_domain <domain>\nExamples: {domains}"
                )
                return
            success, message = mtproto_manager.set_fake_tls_domain(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_domain: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_set_tag(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_set_tag command <hex> — set the stats tag."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /mt_set_tag <hex_tag>\n"
                    "Tag for @MTProxybot (proxy promotion).\n"
                    "/mt_set_tag off — remove the tag"
                )
                return
            tag = "" if args[0] == "off" else args[0]
            success, message = mtproto_manager.set_tag(tag)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_tag: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_set_workers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_set_workers command <n> — set the worker count."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /mt_set_workers <1-16>")
                return
            try:
                workers = int(args[0])
            except ValueError:
                await update.message.reply_text(
                    "❌ Worker count must be a number"
                )
                return
            success, message = mtproto_manager.set_workers(workers)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_workers: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_gen_secret(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_gen_secret command [domain] — generate and set the secret."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            domain = args[0] if args else None
            new_secret = mtproto_manager.generate_secret(domain)
            success, message = mtproto_manager.set_secret(new_secret)
            if success:
                status = mtproto_manager.get_status()
                await update.message.reply_text(
                    f"{message}\n🔑 Secret: `{new_secret}`\n🧭 Mode: `{status.get('secret_mode_label')}`",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_gen_secret: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_gen_all command — generate a secret + detect IP."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            await update.message.reply_text("⏳ Generating secret and detecting IP...")
            success, data, message = mtproto_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_gen_all: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_add_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_add_client command <name> — add a client."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /mt_add_client <name>")
                return
            name = args[0]
            success, message, client = mtproto_manager.add_client(name)
            if success and client:
                link = mtproto_manager.generate_tg_link(client.get("secret"))
                if link:
                    message += f"\n🔗 Link: `{link}`"
            await update.message.reply_text(message)
            if success and client:
                await self._reply_mt_qr(update.message, client.get("secret", name))
        except Exception as e:
            logger.error(f"Error in mt_add_client: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_qr command — show a QR for an MTProto client."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text(
                    "⛔ MTProto QR codes are admin-only."
                )
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Usage: /mt_qr <client_name_or_secret>"
                )
                return
            await self._reply_mt_qr(update.message, args[0])
        except Exception as e:
            logger.error(f"Error in mt_qr: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_del_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_del_client command <name> — remove a client."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /mt_del_client <name>")
                return
            success, message = mtproto_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_del_client: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_list_clients(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_list_clients command — client list."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            clients = mtproto_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 No clients")
                return
            lines = ["📡 *MTProto clients:*\n"]
            status = mtproto_manager.get_status()
            lines.append(
                f"*Mode:* `{self._escape_md2(status.get('secret_mode_label') or status.get('secret_mode') or '?')}`\n"
            )
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                secret = c.get("secret", "")
                masked = f"{secret[:6]}..." if len(secret) > 6 else "***"
                created = c.get("created_at", "")[:10]
                lines.append(
                    f"{i}\\. `{self._escape_md2(name)}` — secret: `{self._escape_md2(masked)}` \\({self._escape_md2(created)}\\)"
                )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in mt_list_clients: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_install command — install MTProto proxy on the server."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            await update.message.reply_text(
                "⏳ Installing MTProto proxy (compiling from source)..."
            )
            success, message = mtproto_manager.install_mtproto()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_install: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_apply command — apply config (write systemd unit, restart)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mtproto_manager.apply_config()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_apply: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_start command — start the service."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mtproto_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in mt_start: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_stop command — stop the service."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mtproto_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_stop: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_restart command — restart the service."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mtproto_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_restart: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_logs command [n] — show logs."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = mtproto_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 MTProto logs:\n```\n{output}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Error in mt_logs: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_fetch_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_fetch_config command — refresh proxy-secret and proxy-multi.conf."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            await update.message.reply_text(
                "⏳ Downloading proxy-secret and proxy-multi.conf..."
            )
            success, message = mtproto_manager.fetch_proxy_config()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_fetch_config: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mt_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mt_export command — export links and configs."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return

            logger.info(f"Admin {update.effective_user.id} exporting MTProto config")

            esc = self._escape_md2

            tg_link = mtproto_manager.generate_tg_link()
            https_link = mtproto_manager.generate_https_link()
            status = mtproto_manager.get_status()

            parts = ["📡 *MTProto Export*\n"]
            parts.append(
                f"*Mode:* `{esc(status.get('secret_mode_label') or status.get('secret_mode') or '?')}`\n"
            )

            if tg_link:
                parts.append(f"*tg link \\(for Telegram\\):*\n`{esc(tg_link)}`\n")
            if https_link:
                parts.append(f"*HTTPS link:*\n`{esc(https_link)}`\n")

            if not tg_link and not https_link:
                parts.append("❌ Server or secret is not set")

            keyboard = [
                [
                    InlineKeyboardButton(
                        "📲 tg:// Link", callback_data="mt_export_tg_link"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🌐 HTTPS Link", callback_data="mt_export_https_link"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📷 QR by client", callback_data="mt_export_qr_menu"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📦 Subscription (base64)", callback_data="mt_export_sub_base64"
                    )
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            message = "\n".join(parts)
            await update.message.reply_text(
                message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Error in mt_export: {e}")
            await update.message.reply_text(f"Error: {e}")

    # === ERROR HANDLER ===

    async def naive_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_status command — show NaiveProxy state."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            status = naiveproxy_manager.get_status()
            enabled = "🟢 on" if status.get("enabled") else "🔴 off"
            configured = "yes" if status.get("configured") else "no"
            systemd = status.get("systemd_output") or "unknown"
            text = (
                "🌐 NaiveProxy status\n\n"
                f"State: {enabled}\n"
                f"Configured: {configured}\n"
                f"Domain: {status.get('domain') or '-'}\n"
                f"Port: {status.get('port')}\n"
                f"User: {status.get('username') or '-'}\n"
                f"Scheme: {status.get('scheme')}\n"
                f"Padding: {status.get('padding')}\n"
                f"Probe resistance: {status.get('probe_resistance')}\n"
                f"Service: {status.get('service_name')}\n"
                f"systemd: {systemd}"
            )
            await update.message.reply_text(text)
        except Exception as e:
            logger.error(f"Error in naive_status: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_on command — enable NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = naiveproxy_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_on: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_off command — disable NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = naiveproxy_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_off: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_config command — show the current NaiveProxy config."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            config = naiveproxy_manager.get_config(
                include_secrets=self._secret_reveal_allowed()
            )
            text = (
                "🌐 NaiveProxy config\n\n"
                f"enabled: {config.get('enabled')}\n"
                f"domain: {config.get('domain') or '-'}\n"
                f"server: {config.get('server') or '-'}\n"
                f"port: {config.get('port')}\n"
                f"username: {config.get('username') or '-'}\n"
                f"password: {config.get('password') or '-'}\n"
                f"scheme: {config.get('scheme')}\n"
                f"local_socks_port: {config.get('local_socks_port')}\n"
                f"padding: {config.get('padding')}\n"
                f"probe_resistance: {config.get('probe_resistance')}\n"
                f"hide_ip: {config.get('hide_ip')}\n"
                f"hide_via: {config.get('hide_via')}\n"
                f"camouflage_url: {config.get('camouflage_url') or '-'}\n"
                f"caddyfile_path: {config.get('caddyfile_path')}\n"
                f"service_name: {config.get('service_name')}"
            )
            await update.message.reply_text(text)
        except Exception as e:
            logger.error(f"Error in naive_config: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_set_domain(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/naive_set_domain command <domain> — set the NaiveProxy domain."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /naive_set_domain <domain>"
                )
                return
            success, message = naiveproxy_manager.set_domain(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_domain: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_set_port command <port> — set the NaiveProxy port."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text("Usage: /naive_set_port <port>")
                return
            success, message = naiveproxy_manager.set_port(int(context.args[0]))
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_port: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_set_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_set_user command <username> — set the NaiveProxy username."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /naive_set_user <username>"
                )
                return
            success, message = naiveproxy_manager.set_username(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_user: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_set_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/naive_set_password command <password> — set the NaiveProxy password."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /naive_set_password <password>"
                )
                return
            success, message = naiveproxy_manager.set_password(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_password: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_set_dpi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_set_dpi command <param> <value> — fine-tune NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if len(args) < 2:
                await update.message.reply_text(
                    "Usage: /naive_set_dpi <param> <value>\n\n"
                    "Parameters:\n"
                    "scheme=https|quic\n"
                    "padding=on|off\n"
                    "local_socks_port=10808\n"
                    "probe_resistance=on|off\n"
                    "hide_ip=on|off\n"
                    "hide_via=on|off\n"
                    "camouflage_url=https://example.com or off"
                )
                return
            param = args[0]
            value = " ".join(args[1:])
            success, message = naiveproxy_manager.set_dpi_param(param, value)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_dpi: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_gen_creds(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_gen_creds command — generate user/password for NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message, creds = naiveproxy_manager.generate_credentials()
            if success:
                await update.message.reply_text(
                    f"{message}\nusername: {creds.get('username')}\npassword: {creds.get('password')}"
                )
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_gen_creds: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_install command — start NaiveProxy server install."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            await update.message.reply_text("⏳ Installing NaiveProxy...")
            success, message = naiveproxy_manager.install_naiveproxy()
            await update.message.reply_text(
                "✅ Install finished"
                if success
                else "❌ Install finished with an error"
            )
            await self._reply_export_file(
                update.message,
                message,
                "naive-install.log",
                "NaiveProxy install output",
            )
        except Exception as e:
            logger.error(f"Error in naive_install: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_uri(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_uri command — show the NaiveProxy client URI."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            uri = naiveproxy_manager.build_client_uri()
            await update.message.reply_text(f"🌐 NaiveProxy URI:\n{uri}")
        except Exception as e:
            logger.error(f"Error in naive_uri: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_apply command — write Caddyfile and restart the service."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = naiveproxy_manager.apply_server_config()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_apply: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def naive_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/naive_export command — export the NaiveProxy client."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            client_config = naiveproxy_manager.export_client_config()
            aping_profile = naiveproxy_manager.export_aping_profile()
            uri = naiveproxy_manager.build_client_uri()
            await update.message.reply_text(f"🌐 NaiveProxy URI:\n{uri}")
            await self._reply_export_file(
                update.message,
                json.dumps(client_config, ensure_ascii=False, indent=2),
                "naiveproxy-client.json",
                "NaiveProxy client config",
            )
            await self._reply_export_file(
                update.message,
                aping_profile,
                "aping-naive-profile.json",
                "NaiveProxy client profile",
            )
        except Exception as e:
            logger.error(f"Error in naive_export: {e}")
            await update.message.reply_text(f"Error: {e}")

    # =====================================================================
    # === TUIC COMMANDS ===
    # =====================================================================

    async def tuic_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/tuic_status command — show TUIC status."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return

            status = tuic_manager.get_status()
            esc = self._escape_md2
            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            message = f"""🔷 *TUIC Status*

*State:* {status_emoji} {"On" if status["enabled"] else "Off"}
*Config:* {config_emoji} {"Configured" if status["configured"] else "Not configured"}

*Parameters:*
• Server: `{esc(status.get("server"))}`
• Port: `{esc(status.get("port", 443))}` \\(UDP\\)
• SNI: `{esc(status.get("sni") or "(auto)")}`
• Insecure: {"yes ⚠️" if status.get("insecure") else "no ✅"}
• Congestion: `{esc(status.get("congestion_control", "bbr"))}`
• UDP relay: `{esc(status.get("udp_relay_mode", "native"))}`

*Clients:* {status.get("clients_count", 0)}
*Updated:* {esc(status.get("updated_at", "never"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in tuic_status: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def tuic_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = tuic_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = tuic_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            config = tuic_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"🔷 TUIC configuration:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_set_server(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = tuic_manager.set_server(server)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /tuic_set_port <port>")
                return
            port = int(args[0])
            success, message = tuic_manager.set_port(port)
            await update.message.reply_text(message)
        except ValueError:
            await update.message.reply_text("❌ Port must be a number")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_set_cc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/tuic_set_cc command <bbr|cubic|new_reno>."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /tuic_set_cc <bbr|cubic|new_reno>"
                )
                return
            success, message = tuic_manager.set_congestion_control(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_gen_cert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = tuic_manager.generate_self_signed_cert()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, results, message = tuic_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_add_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/tuic_add command <name>."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /tuic_add <name>")
                return
            name = args[0]
            success, message, client = tuic_manager.add_client(name)
            if success and client:
                _ok, _msg, uri = tuic_manager.generate_client_uri(name)
                message += f"\n🔗 URI: `{uri}`"
            await update.message.reply_text(message)
            if success and client:
                await self._reply_tuic_qr(update.message, name)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /tuic_qr <client_name>")
                return
            await self._reply_tuic_qr(update.message, args[0])
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_del_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /tuic_del <name>")
                return
            success, message = tuic_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            clients = tuic_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 No TUIC clients")
                return
            esc = self._escape_md2
            lines = ["🔷 *TUIC clients:*\n"]
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                uuid_short = c.get("uuid", "")[:8] + "..."
                created = c.get("created_at", "")[:10]
                lines.append(
                    f"{i}\\. `{esc(name)}` — uuid: `{esc(uuid_short)}` \\({esc(created)}\\)"
                )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = tuic_manager.apply_config()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = tuic_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = tuic_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = tuic_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = tuic_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 TUIC logs:\n```\n{output}\n```", parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def tuic_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/tuic_export command — export configurations."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return

            singbox_config = tuic_manager.export_singbox_config()
            server_config = tuic_manager.export_server_config_json()

            await self._reply_export_file(
                update.message,
                json.dumps(singbox_config, ensure_ascii=False, indent=2),
                "tuic-singbox-client.json",
                "🔷 TUIC sing-box client config",
            )
            await self._reply_export_file(
                update.message,
                server_config,
                "tuic-server-config.json",
                "🔷 TUIC server config (sing-box)",
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    # =====================================================================
    # === ANYTLS COMMANDS ===
    # =====================================================================

    async def anytls_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/anytls_status command — show AnyTLS status."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return

            status = anytls_manager.get_status()
            esc = self._escape_md2
            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            message = f"""🔶 *AnyTLS Status*

*State:* {status_emoji} {"On" if status["enabled"] else "Off"}
*Config:* {config_emoji} {"Configured" if status["configured"] else "Not configured"}

*Parameters:*
• Server: `{esc(status.get("server"))}`
• Port: `{esc(status.get("port", 443))}` \\(TCP\\)
• SNI: `{esc(status.get("sni") or "(auto)")}`
• Insecure: {"yes ⚠️" if status.get("insecure") else "no ✅"}

*Clients:* {status.get("clients_count", 0)}
*Updated:* {esc(status.get("updated_at", "never"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = anytls_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = anytls_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            config = anytls_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"🔶 AnyTLS configuration:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_set_server(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = anytls_manager.set_server(server)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /anytls_set_port <port>"
                )
                return
            port = int(args[0])
            success, message = anytls_manager.set_port(port)
            await update.message.reply_text(message)
        except ValueError:
            await update.message.reply_text("❌ Port must be a number")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_gen_cert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = anytls_manager.generate_self_signed_cert()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, results, message = anytls_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_add_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/anytls_add command <name>."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /anytls_add <name>")
                return
            name = args[0]
            success, message, client = anytls_manager.add_client(name)
            if success and client:
                _ok, _msg, uri = anytls_manager.generate_client_uri(name)
                message += f"\n🔗 URI: `{uri}`"
            await update.message.reply_text(message)
            if success and client:
                await self._reply_anytls_qr(update.message, name)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /anytls_qr <client_name>"
                )
                return
            await self._reply_anytls_qr(update.message, args[0])
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_del_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /anytls_del <name>")
                return
            success, message = anytls_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            clients = anytls_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 No AnyTLS clients")
                return
            esc = self._escape_md2
            lines = ["🔶 *AnyTLS clients:*\n"]
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                pw = c.get("password", "")
                masked = f"{pw[:4]}..." if len(pw) > 4 else "***"
                created = c.get("created_at", "")[:10]
                lines.append(
                    f"{i}\\. `{esc(name)}` — password: `{esc(masked)}` \\({esc(created)}\\)"
                )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = anytls_manager.apply_config()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = anytls_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = anytls_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = anytls_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = anytls_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 AnyTLS logs:\n```\n{output}\n```", parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def anytls_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return

            singbox_config = anytls_manager.export_singbox_config()
            server_config = anytls_manager.export_server_config_json()

            await self._reply_export_file(
                update.message,
                json.dumps(singbox_config, ensure_ascii=False, indent=2),
                "anytls-singbox-client.json",
                "🔶 AnyTLS sing-box client config",
            )
            await self._reply_export_file(
                update.message,
                server_config,
                "anytls-server-config.json",
                "🔶 AnyTLS server config (sing-box)",
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    # =====================================================================
    # === XHTTP COMMANDS ===
    # =====================================================================

    async def xhttp_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/xhttp_status command — show XHTTP status."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return

            status = xhttp_manager.get_status()
            esc = self._escape_md2
            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            message = f"""🌐 *XHTTP Status*

*State:* {status_emoji} {"On" if status["enabled"] else "Off"}
*Config:* {config_emoji} {"Configured" if status["configured"] else "Not configured"}

*Parameters:*
• Server: `{esc(status.get("server"))}`
• Port: `{esc(status.get("port", 443))}` \\(TCP\\)
• Path: `{esc(status.get("path", "/"))}`
• Host: `{esc(status.get("host") or "(empty)")}`
• Mode: `{esc(status.get("mode", "auto"))}`
• Security: `{esc(status.get("security", "tls"))}`
• SNI: `{esc(status.get("sni") or "(auto)")}`
• Insecure: {"yes ⚠️" if status.get("insecure") else "no ✅"}

*Clients:* {status.get("clients_count", 0)}
*Updated:* {esc(status.get("updated_at", "never"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = xhttp_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = xhttp_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            config = xhttp_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"🌐 XHTTP configuration:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_set_server(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = xhttp_manager.set_server(server)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /xhttp_set_port <port>")
                return
            port = int(args[0])
            success, message = xhttp_manager.set_port(port)
            await update.message.reply_text(message)
        except ValueError:
            await update.message.reply_text("❌ Port must be a number")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_set_path(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /xhttp_set_path <path>")
                return
            success, message = xhttp_manager.set_path(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_set_host(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            host = args[0] if args else ""
            success, message = xhttp_manager.set_host(host)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_set_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /xhttp_set_mode <auto|packet-up|stream-up>"
                )
                return
            success, message = xhttp_manager.set_mode(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_gen_cert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = xhttp_manager.generate_self_signed_cert()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, results, message = xhttp_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_add_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/xhttp_add command <name>."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /xhttp_add <name>")
                return
            name = args[0]
            success, message, client = xhttp_manager.add_client(name)
            if success and client:
                _ok, _msg, uri = xhttp_manager.generate_client_uri(name)
                message += f"\n🔗 URI: `{uri}`"
            await update.message.reply_text(message)
            if success and client:
                await self._reply_xhttp_qr(update.message, name)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /xhttp_qr <client_name>"
                )
                return
            await self._reply_xhttp_qr(update.message, args[0])
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_del_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Usage: /xhttp_del <name>")
                return
            success, message = xhttp_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            clients = xhttp_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 No XHTTP clients")
                return
            esc = self._escape_md2
            lines = ["🌐 *XHTTP clients:*\n"]
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                uuid_short = c.get("uuid", "")[:8] + "..."
                created = c.get("created_at", "")[:10]
                lines.append(
                    f"{i}\\. `{esc(name)}` — uuid: `{esc(uuid_short)}` \\({esc(created)}\\)"
                )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = xhttp_manager.apply_config()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = xhttp_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = xhttp_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = xhttp_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = xhttp_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 XHTTP logs:\n```\n{output}\n```", parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def xhttp_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return

            singbox_config = xhttp_manager.export_singbox_config()
            server_config = xhttp_manager.export_server_config_json()

            await self._reply_export_file(
                update.message,
                json.dumps(singbox_config, ensure_ascii=False, indent=2),
                "xhttp-singbox-client.json",
                "🌐 XHTTP sing-box client config",
            )
            await self._reply_export_file(
                update.message,
                server_config,
                "xhttp-server-config.json",
                "🌐 XHTTP server config (sing-box)",
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    # =====================================================================
    # === MIERU COMMANDS ===
    # =====================================================================

    async def mieru_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mieru_status command — Mieru (mita) state."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            status = mieru_manager.get_status()
            enabled = "🟢 on" if status.get("enabled") else "🔴 off"
            configured = "yes" if status.get("configured") else "no"
            bindings = status.get("port_bindings") or []
            bindings_str = (
                ", ".join(
                    (
                        f"{b['port']}/{b.get('protocol', 'TCP').lower()}"
                        if "port" in b
                        else f"{b['portRange']['from']}-{b['portRange']['to']}/{b.get('protocol', 'TCP').lower()}"
                    )
                    for b in bindings
                )
                or "—"
            )
            text = (
                "🛰 Mieru status\n\n"
                f"State: {enabled}\n"
                f"Configured: {configured}\n"
                f"Server: {status.get('server') or '-'}\n"
                f"Port bindings: {bindings_str}\n"
                f"MTU: {status.get('mtu')}\n"
                f"Multiplexing: {status.get('multiplexing')}\n"
                f"Handshake: {status.get('handshake_mode')}\n"
                f"SOCKS5 port (client): {status.get('socks5_port')}\n"
                f"Logging: {status.get('logging_level')}\n"
                f"Service: {status.get('service_name')}\n"
                f"systemd: {status.get('systemd_output') or 'unknown'}\n"
                f"mita status: {'OK' if status.get('mita_status_ok') else 'fail'}\n"
                f"Clients: {status.get('clients_count', 0)}"
            )
            await update.message.reply_text(text)
        except Exception as e:
            logger.error(f"Error in mieru_status: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mieru_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mieru_config command — current Mieru config without secrets (default)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            config = mieru_manager.get_config(
                include_secrets=self._secret_reveal_allowed()
            )
            payload = json.dumps(config, ensure_ascii=False, indent=2)
            await self._reply_export_file(
                update.message,
                payload,
                "mieru-config.json",
                "🛰 Mieru config (secrets masked)",
            )
        except Exception as e:
            logger.error(f"Error in mieru_config: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mieru_set_server(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /mieru_set_server <ip_or_domain>"
                )
                return
            success, message = mieru_manager.set_server(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Usage: /mieru_set_port <port> [tcp|udp]"
                )
                return
            try:
                port = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ port must be a number")
                return
            protocol = args[1] if len(args) > 1 else "tcp"
            success, message = mieru_manager.set_port(port, protocol)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_set_mtu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /mieru_set_mtu <1280..1500>"
                )
                return
            try:
                mtu = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ mtu must be a number")
                return
            success, message = mieru_manager.set_mtu(mtu)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_set_multiplexing(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /mieru_set_multiplexing <off|low|middle|high>"
                )
                return
            success, message = mieru_manager.set_multiplexing(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_set_handshake(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /mieru_set_handshake <standard|no_wait>"
                )
                return
            success, message = mieru_manager.set_handshake_mode(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_set_socks5_port(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /mieru_set_socks5_port <port>"
                )
                return
            try:
                port = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ port must be a number")
                return
            success, message = mieru_manager.set_socks5_port(port)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_gen_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Generate a password (for manual use or add_client)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            password = mieru_manager.generate_password()
            await update.message.reply_text(
                "✅ Generated password (use manually):\n"
                f"`{password}`\n\n"
                "To add a client immediately with auto-generation: /mieru_add_client <name>",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_add_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /mieru_add_client <name>"
                )
                return
            name = context.args[0]
            success, message, client = mieru_manager.add_client(name)
            if success and client:
                masked = self._mask_secret(client.get("password", ""))
                message += f"\npassword: {masked}\nto issue: /mieru_export {name}"
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            clients = mieru_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 No Mieru clients")
                return
            lines = ["🛰 Mieru clients:"]
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                owner = c.get("owner_id", "—")
                created = (c.get("created_at") or "")[:10]
                lines.append(f"{i}. {name}  owner={owner}  created={created}")
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_del_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Usage: /mieru_del_client <name>"
                )
                return
            success, message = mieru_manager.delete_client(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            await update.message.reply_text("⏳ Installing Mieru (mita)…")
            success, message = mieru_manager.install_mieru()
            await update.message.reply_text(
                "✅ Install finished"
                if success
                else "❌ Install finished with an error"
            )
            await self._reply_export_file(
                update.message,
                message,
                "mieru-install.log",
                "Mieru install output",
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mieru_apply [reload] — apply server config; reload = users/logging only."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            reload_only = bool(args) and args[0].strip().lower() in {"reload", "soft"}
            success, message = mieru_manager.apply_server_config(
                reload_only=reload_only
            )
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mieru_manager.start()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mieru_manager.stop()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            success, message = mieru_manager.restart()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            try:
                n = int(args[0]) if args else 80
            except ValueError:
                n = 80
            success, output = mieru_manager.logs(n)
            await self._reply_export_file(
                update.message,
                output,
                "mieru-logs.txt",
                f"🛰 Mieru logs (last {n} lines)",
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def mieru_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mieru_export [name] — issue client config + mierus:// + Clash + aping-profile."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            name = args[0] if args else None
            if not name:
                clients = mieru_manager.list_clients()
                if not clients:
                    await update.message.reply_text(
                        "❌ No clients. /mieru_add_client <name>"
                    )
                    return
                name = clients[0].get("name", "")
                await update.message.reply_text(
                    f"ℹ️ No name given — exporting the first one: {name}"
                )
            try:
                client_config = mieru_manager.export_client_config(name)
                uri = mieru_manager.build_simple_uri(name)
                clash = mieru_manager.export_clash_block(name)
                aping = mieru_manager.export_aping_profile(name)
            except ValueError as exc:
                await update.message.reply_text(f"❌ {exc}")
                return
            await update.message.reply_text(f"🛰 Mieru URI ({name}):\n{uri}")
            await self._reply_export_file(
                update.message,
                client_config,
                f"mieru-{name}-client.json",
                "Mieru client config",
            )
            await self._reply_export_file(
                update.message,
                clash,
                f"mieru-{name}-clash.yaml",
                "Mieru Clash/mihomo block",
            )
            await self._reply_export_file(
                update.message,
                aping,
                f"mieru-{name}-aping.json",
                "Mieru client profile (draft)",
            )
        except Exception as e:
            logger.error(f"Error in mieru_export: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def mieru_set_dpi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mieru_set_dpi <param> <value> — single knob for DPI parameters (plan §10)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Admin only.")
                return
            args = context.args or []
            if len(args) < 2:
                await update.message.reply_text(
                    "Usage: /mieru_set_dpi <param> <value>\n\n"
                    "Parameters:\n"
                    "protocol=tcp|udp\n"
                    "port=<1025..65535>\n"
                    "port_range=<from>-<to>\n"
                    "mtu=<1280..1500>\n"
                    "multiplexing=off|low|middle|high\n"
                    "handshake=standard|no_wait\n"
                    "socks5_port=<1025..65535>\n"
                    "logging=debug|info|warn|error\n\n"
                    "After changing port/MTU/protocol/multiplexing/handshake "
                    "run /mieru_apply and then /mieru_export again."
                )
                return
            param = args[0]
            value = " ".join(args[1:])
            success, message = mieru_manager.set_dpi_param(param, value)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Error handling."""
        logger.error(f"Update {update} caused error {context.error}")

        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred while handling the command. Try again later."
            )
