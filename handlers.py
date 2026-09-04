# -*- coding: utf-8 -*-
"""
Упрощённые обработчики команд бота с поддержкой VLESS-Reality.

Этот модуль содержит минимальный набор команд:
- Базовые: start, help, info, clear
- Админские: ver, dockhand, headscale, api, gen_api_key, del_api_key, encryption_key, gen_encryption_key,
             del_encryption_key, gen_chacha_key, gen_pqc_key
- Управление пользователями: list_users, users_log, setcity, setgreeting, special_add, special_remove
- Настройки ИИ: ai_provider, ch_model
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
    """Упрощённый класс обработчиков бота с поддержкой VLESS-Reality."""

    def __init__(self, config: Config = None):
        """Инициализация обработчиков."""
        self.config = config
        # (chat_id, message_id) -> Task; одна карточка /user — один таймер, сброс при обновлении
        self._user_card_ttl_tasks: dict[tuple[int, int], asyncio.Task] = {}

    async def _reply_export_file(
        self, message, content: str, filename: str, caption: str
    ):
        """Отправить экспорт как файл, чтобы не упираться в лимиты/MarkdownV2."""
        buffer = BytesIO(content.encode("utf-8"))
        buffer.name = filename
        await message.reply_document(document=buffer, caption=caption)

    def _is_admin(self, user_id: int) -> bool:
        """Проверить, является ли пользователь администратором."""
        if not self.config:
            return False
        return self.config.is_admin(user_id)

    def _is_privileged(self, user_id: int) -> bool:
        """Админ или пользователь из special-списка."""
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
        """Разрешён ли полный вывод секретов через удалённые каналы."""
        return os.getenv("TELEGRAMHELPER_ALLOW_SECRET_REVEAL", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _mask_secret(self, value: str) -> str:
        """Вернуть безопасное маскированное представление секрета."""
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

    # Темы оформления панелей (spec §7). Настоящие цвета чата Bot API не
    # контролирует — темы меняют только акценты в сообщениях самого бота.
    # Имена тем синхронизированы с storage._UI_THEMES_ALLOWED.
    _UI_THEMES: dict = {
        "classic": {
            "label": "Классика",
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
            "label": "Минимал",
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
            "label": "Неон",
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
        """Словарь акцентов темы пользователя (fallback на classic)."""
        prefs = storage_get_ui_prefs(user_id)
        return self._UI_THEMES.get(prefs["theme"], self._UI_THEMES["classic"])

    @staticmethod
    def _btn(icons: dict, key: str, text: str) -> str:
        """Подпись кнопки с акцентом темы; в minimal — без эмодзи."""
        icon = icons.get(key, "")
        return f"{icon} {text}".strip()

    async def _menu_panel(self, message, text: str, keyboard, *, edit: bool):
        """Показать/обновить панель меню: MarkdownV2 с fallback в plain text.

        edit=True — edit_text (навигация по панели), иначе reply_text.
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
        """Кнопки главной панели по роли (spec §2)."""
        icons = self._theme_icons(user_id)
        if self._is_admin(user_id):
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🗂 Панель /help", callback_data="menu:admin_help"
                        ),
                        InlineKeyboardButton(
                            self._btn(icons, "diag", "Полный diag"),
                            callback_data="menu:diag",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "👥 Пользователи", callback_data="menu:admin_users"
                        ),
                        InlineKeyboardButton(
                            "🌐 Интернет через VPS", callback_data="menu:exit_node"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            self._btn(icons, "settings", "Настройки"),
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
                        self._btn(icons, "vpn", "Мои VPN-профили"),
                        callback_data="menu:my_profile",
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        "🌐 Интернет через VPS",
                        callback_data="menu:exit_node",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    self._btn(icons, "profile", "Мой профиль"),
                    callback_data="menu:info",
                ),
                InlineKeyboardButton(
                    self._btn(icons, "help", "Справка"), callback_data="menu:help"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    self._btn(icons, "diag", "Диагностика"),
                    callback_data="menu:diag",
                ),
                InlineKeyboardButton(
                    self._btn(icons, "clear", "Очистить"),
                    callback_data="menu:clear",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    self._btn(icons, "settings", "Настройки"),
                    callback_data="menu:settings",
                )
            ]
        )
        return InlineKeyboardMarkup(rows)

    def _user_help_panel_text(self, user_id: int) -> str:
        """Текст панели /help (и menu:back) для обычных и special."""
        prefs = storage_get_ui_prefs(user_id)
        icons = self._UI_THEMES.get(prefs["theme"], self._UI_THEMES["classic"])
        version_info = get_app_version()
        ver = self._escape_md2(version_info.get("version", "N/A"))
        app_name = self._escape_md2(version_info.get("name", "TelegramHelper"))
        brand = f"{icons['brand']} " if icons["brand"] else ""
        lines = [f"{brand}*{app_name}* v{ver}", ""]
        if not prefs["compact"]:
            lines += [
                "Кнопки ниже выполняют команды за вас\\.",
                "Эти же действия доступны slash\\-командами из меню Telegram\\.",
                "",
            ]
        if storage_is_special_user(user_id):
            lines.append(
                "_VPN\\-профили выдаёт админ; сообщения с URL/QR авто\\-удаляются\\._"
            )
        return "\n".join(lines).strip()

    def _settings_panel(self, user_id: int):
        """(text, keyboard) экрана /settings (spec §7)."""
        prefs = storage_get_ui_prefs(user_id)
        icons = self._UI_THEMES.get(prefs["theme"], self._UI_THEMES["classic"])
        theme_label = self._escape_md2(self._UI_THEMES[prefs["theme"]]["label"])
        compact_label = "включён" if prefs["compact"] else "выключен"
        s = f"{icons['settings']} " if icons["settings"] else ""
        lines = [
            f"{s}*Настройки интерфейса*",
            "",
            f"Тема: *{theme_label}*",
            f"Компактный режим: *{compact_label}*",
        ]
        if not prefs["compact"]:
            lines += [
                "",
                "_Тема меняет оформление панелей бота\\. Цвета самого чата "
                "Telegram задаются в настройках приложения и боту "
                "недоступны\\._",
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
                        "Выключить компактный режим"
                        if prefs["compact"]
                        else "Включить компактный режим"
                    ),
                    callback_data="menu:toggle_compact",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    self._btn(icons, "back", "Назад"), callback_data="menu:back"
                )
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def _reply_vless_qr(self, message, name_or_uuid: str):
        """Отправить QR и ссылку для VLESS-клиента."""
        success, response, payload = vless_manager.build_client_qr_payload(name_or_uuid)
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"vless-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR для VLESS-клиента {payload['name']}",
        )
        await message.reply_text(
            "📲 VLESS QR для клиента {name}\n\n"
            "UUID: {uuid}\n\n"
            "Ссылка для импорта:\n{link}\n\n"
            "FoXray: Import/Scan QR -> наведи камеру на код или импортируй ссылку напрямую.".format(
                name=payload["name"],
                uuid=payload["uuid"],
                link=payload["link"],
            )
        )
        return True

    async def _show_vless_qr_selection(self, message):
        """Показать inline-меню выбора клиента для QR."""
        clients = vless_manager.list_clients()
        if not clients:
            await message.reply_text(
                "❌ Список клиентов пуст. Сначала используйте /vless_add_client"
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
            await message.reply_text("❌ У клиентов нет UUID для генерации QR")
            return

        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "Выберите клиента для показа QR:", reply_markup=reply_markup
        )

    async def _reply_hy2_qr(self, message, name_or_password: str):
        """Отправить QR и URI для Hysteria2-клиента."""
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
            caption=f"QR для Hysteria2-клиента {payload['name']}",
        )
        await message.reply_text(
            "⚡ Hysteria2 QR для клиента {name}\n\n"
            "Пароль: {password}\n\n"
            "URI для импорта:\n{uri}\n\n"
            "Поддерживаемый клиент может импортировать профиль по QR или напрямую по hy2:// ссылке.".format(
                name=payload["name"],
                password=payload["password"],
                uri=payload["uri"],
            )
        )
        return True

    async def _show_hy2_qr_selection(self, message):
        """Показать inline-меню выбора клиента Hysteria2 для QR."""
        clients = hysteria2_manager.list_clients()
        if not clients:
            await message.reply_text(
                "❌ Список клиентов пуст. Сначала используйте /hy2_add_client"
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
            await message.reply_text("❌ У клиентов нет пароля для генерации QR")
            return

        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "Выберите Hysteria2-клиента для показа QR:", reply_markup=reply_markup
        )

    async def _reply_mt_qr(self, message, name_or_secret: str):
        """Отправить QR и ссылки для MTProto-клиента."""
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
            caption=f"QR для MTProto-клиента {payload['name']}",
        )
        await message.reply_text(
            "📡 MTProto QR для клиента {name}\n\n"
            "Режим: {mode}\n\n"
            "Secret: {secret}\n\n"
            "HTTPS link:\n{https_link}\n\n"
            "tg:// link:\n{tg_link}\n\n"
            "Для QR используется HTTPS-ссылка, чтобы камера телефона надёжнее открывала Telegram.".format(
                name=payload["name"],
                mode=payload.get("secret_mode_label", "unknown"),
                secret=payload["secret"],
                https_link=payload["https_link"],
                tg_link=payload["tg_link"],
            )
        )
        return True

    async def _show_mt_qr_selection(self, message):
        """Показать inline-меню выбора MTProto-клиента для QR."""
        clients = mtproto_manager.list_clients()
        if not clients:
            await message.reply_text(
                "❌ Список клиентов пуст. Сначала используйте /mt_add_client"
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
            await message.reply_text("❌ У клиентов нет secret для генерации QR")
            return

        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "Выберите MTProto-клиента для показа QR:", reply_markup=reply_markup
        )

    async def _reply_tuic_qr(self, message, name: str):
        """Отправить QR и URI для TUIC-клиента."""
        success, response, payload = tuic_manager.build_client_qr_payload(name)
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"tuic-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR для TUIC-клиента {payload['name']}",
        )
        await message.reply_text(
            "🔷 TUIC QR для клиента {name}\n\n"
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
        """Отправить QR и URI для AnyTLS-клиента."""
        success, response, payload = anytls_manager.build_client_qr_payload(name)
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"anytls-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR для AnyTLS-клиента {payload['name']}",
        )
        await message.reply_text(
            "🔶 AnyTLS QR для клиента {name}\n\n"
            "Password: {password}\n\n"
            "URI:\n{uri}".format(
                name=payload["name"],
                password=payload["password"],
                uri=payload["uri"],
            )
        )
        return True

    async def _reply_xhttp_qr(self, message, name: str):
        """Отправить QR и URI для XHTTP-клиента."""
        success, response, payload = xhttp_manager.build_client_qr_payload(name)
        if not success:
            await message.reply_text(response)
            return False

        qr_buffer = payload["qr_buffer"]
        qr_buffer.name = f"xhttp-{payload['name']}.png"

        await message.reply_photo(
            photo=qr_buffer,
            caption=f"QR для XHTTP-клиента {payload['name']}",
        )
        await message.reply_text(
            "🌐 XHTTP QR для клиента {name}\n\nUUID: {uuid}\n\nURI:\n{uri}".format(
                name=payload["name"],
                uuid=payload["uuid"],
                uri=payload["uri"],
            )
        )
        return True

    async def _reply_mieru_qr(self, message, name: str):
        """Отправить URI и QR для Mieru-клиента (per-user; plan §7)."""
        try:
            uri = mieru_manager.build_simple_uri(name)
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}")
            return False

        await message.reply_text(
            "🛰 Mieru URI для клиента {name}\n\n{uri}".format(name=name, uri=uri)
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
                caption=f"QR для Mieru-клиента {name}",
            )
        except Exception as exc:
            logger.warning("mieru QR render failed: %s", exc)
        return True

    _HELP_MENU_KEYBOARD = [
        [
            InlineKeyboardButton("🚀 Быстрый старт", callback_data="help_roadmap"),
            InlineKeyboardButton("👥 Пользователи", callback_data="help_users"),
        ],
        [
            InlineKeyboardButton("🧩 Протоколы", callback_data="help_protocols"),
            InlineKeyboardButton("🔎 Диагностика", callback_data="help_diag"),
        ],
        [
            InlineKeyboardButton("🔧 Система и ключи", callback_data="help_admin"),
            InlineKeyboardButton("💾 Бэкапы", callback_data="help_backup"),
        ],
        [
            InlineKeyboardButton("⚙️ Настройки", callback_data="menu:settings"),
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
        """Показать главное меню /help с inline-кнопками.

        Args:
            message: Telegram message object.
            edit: если True — edit_text (для callback), иначе reply_text.
        """
        version_info = get_app_version()
        ver = self._escape_md2(version_info.get("version", "N/A"))
        app_name = self._escape_md2(version_info.get("name", "TelegramHelper"))
        text = (
            f"✨ *{app_name}* v{ver}\n\n"
            "*Админ\\-панель навигации*\n\n"
            "Slash\\-меню Telegram остаётся полным, но для ежедневной работы удобнее идти через разделы ниже\\.\n\n"
            "*Чаще всего:*\n"
            "• `/user <id>` — карточка пользователя с кнопками профилей\n"
            "• `/profiles <id>` — выданные профили пользователя\n"
            "• `/provision <id>` — создать bot\\-managed профили\n"
            "• `/diag` — состояние транспортов и портов\n\n"
            "_Внутри разделов — короткие сценарии, без длинной простыни команд\\._"
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
        """Навигация внутри admin help; обычный /help пользователей не затрагивает."""
        if section == "help_protocols":
            rows = [*self._HELP_PROTOCOL_KEYBOARD]
            rows.append(
                [
                    InlineKeyboardButton(
                        "◀️ Назад к главному меню", callback_data="help_back"
                    )
                ]
            )
            return InlineKeyboardMarkup(rows)
        if section in self._HELP_PROTOCOL_CALLBACKS:
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🧩 К протоколам", callback_data="help_protocols"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "◀️ Назад к главному меню", callback_data="help_back"
                        )
                    ],
                ]
            )
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "◀️ Назад к главному меню", callback_data="help_back"
                    )
                ]
            ]
        )

    # === БАЗОВЫЕ КОМАНДЫ ===

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /start - запуск бота и приветствие."""
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
                        "🌐 *Адрес VPS:* автоопределение недоступно "
                        "\\(задайте `/vless\\_set\\_server` или `DOCKHAND\\_SSH\\_HOST` в `.env`\\)"
                    )
                else:
                    vps_line = "🌐 *Адрес VPS:* `" + escape_markdown(p.host) + "`"
            except Exception as ex:
                logger.debug("start_command: VPS host hint failed: %s", ex)
                vps_line = "🌐 *Адрес VPS:* временно недоступен"

            if not self._is_admin(user.id):
                # Обычные и special: приветствие + адрес VPS + кнопки роли.
                # ReplyKeyboardRemove не нужен: inline-клавиатура живёт в
                # сообщении и не конфликтует с reply-клавиатурами.
                prefs = storage_get_ui_prefs(user.id)
                icons = self._theme_icons(user.id)
                brand = f"{icons['brand']} " if icons["brand"] else ""
                parts = [
                    f"Привет, {escape_markdown(user.first_name or 'Пользователь')}\\!",
                    "",
                    f"{brand}*TelegramHelper* — бот для личных задач и уведомлений\\.",
                    "",
                    vps_line,
                ]
                if not prefs["compact"]:
                    parts += [
                        "",
                        "_Кнопки ниже — основные действия\\. Полный список "
                        "команд — в меню Telegram\\._",
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

            welcome_message = f"""Привет, {escape_markdown(user.first_name or "Пользователь")}\\!

✨ *TelegramHelper* — бот для API, ключей и прокси\\-протоколов

{vps_line}

*Транспорты на этом VPS:*
{protocols_block}

Откройте `/help`, чтобы выбрать раздел и увидеть короткие примеры команд\\.
Подробная диагностика — `/diag`\\."""

            await self._menu_panel(
                update.message,
                welcome_message,
                self._main_menu_keyboard(user.id),
                edit=False,
            )

        except Exception as e:
            logger.error(f"Error in start_command: {e}")
            await update.message.reply_text(
                "Привет! Используйте /help для просмотра команд."
            )

    # === Диагностика транспортов ===

    def _build_protocol_status_lines(self, short: bool = True) -> list:
        """
        Сформировать список MarkdownV2-строк со статусом каждого протокола.

        short=True   — компактный вывод для /start (одна строка на протокол).
        short=False  — расширенный вывод для /diag (с портом, источниками
                       сигнала и заметками, без секретов).
        """
        lines: list = []
        try:
            snapshot = live_status.gather_full_snapshot()
            statuses = snapshot["protocols"]
            dockhand = snapshot["dockhand"]
            xui_panel = snapshot.get("xui_panel")
        except Exception as exc:
            logger.warning("_build_protocol_status_lines: gather failed: %s", exc)
            return ["⚠️ Не удалось собрать статус транспортов\\."]

        esc = self._escape_md2

        for st in statuses:
            indicator = st.short_indicator()
            label = st.short_label()
            name = esc(st.display)
            if not st.implemented and label == "выключен":
                # Не реализованным протоколам без живых сигналов выводим
                # явное «не реализовано», чтобы не путать с настроенным «выкл».
                label = "не реализовано в боте"
            if short:
                lines.append(f"{st.icon} *{name}:* {indicator} {esc(label)}")
            else:
                port_text = ""
                if st.port:
                    port_text = f" (порт {st.port}/{st.transport.upper()})"
                detail_bits = []
                if st.flag_enabled is True:
                    detail_bits.append("флаг JSON: вкл")
                elif st.flag_enabled is False:
                    detail_bits.append("флаг JSON: выкл")
                if st.process_alive is True:
                    procs = ", ".join(sorted(set(st.process_names))) or "yes"
                    detail_bits.append(f"процесс: {procs}")
                elif st.process_alive is False:
                    detail_bits.append("процесс: не найден")
                if st.port_listening is True:
                    detail_bits.append("порт слушает")
                elif st.port_listening is False:
                    detail_bits.append("порт не слушает")
                if not st.implemented:
                    detail_bits.append("серверная автоматизация в боте не реализована")
                if st.notes:
                    detail_bits.extend(st.notes)
                detail = "; ".join(esc(b) for b in detail_bits)
                line = f"{st.icon} *{name}:* {indicator} {esc(label)}{esc(port_text)}"
                if detail:
                    line += f"\n   ↳ {detail}"
                lines.append(line)

        # Dockhand — отдельным блоком (это панель, а не транспорт).
        di = dockhand.short_indicator()
        di_label = dockhand.short_label()
        if dockhand.notes and dockhand.live is None:
            # Если диагностика недоступна, используем мягкий статус.
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
                extra_bits.append("docker.sock не смонтирован")
            elif container.get("available") and not container.get("found"):
                extra_bits.append("контейнер не найден")
            extra = "; ".join(esc(b) for b in extra_bits)
            line = f"{dockhand.icon} *{dockhand_name}:* {di} {esc(di_label)}"
            if extra:
                line += f"\n   ↳ {extra}"
            lines.append(line)

        # 3x-ui — внешняя панель управления Xray. Показываем строку
        # только когда что-то реально найдено (живой процесс/контейнер,
        # либо хотя бы установленный бинарь). На «чистом» VPS строки нет
        # — чтобы /start и /diag не зашумлялись бесполезным сообщением.
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
                        detail_bits.append(f"процесс: {procs}")
                    elif xui_panel.process_alive is False:
                        detail_bits.append("процесс: не запущен")
                    if xui_panel.notes:
                        detail_bits.extend(xui_panel.notes)
                    detail = "; ".join(esc(b) for b in detail_bits)
                    line = f"{xui_panel.icon} *{xname}:* {xi} " + esc(
                        xui_panel.short_label()
                    )
                    if detail:
                        line += f"\n   ↳ {detail}"
                    line += "\n   ↳ " + esc(
                        "3x-ui — это панель управления Xray/VLESS, "
                        "а не второй VLESS-порт; клиентов и inbound смотрите в /vless_list_clients"
                    )
                    lines.append(line)

        return lines

    async def diag_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Команда `/diag`.

        Администратор: расширенная диагностика (транспорты, Dockhand, 3x-ui,
        слушающие порты, пояснения).

        Обычные и special-пользователи: та же компактная сводка, что в `/start`,
        без портов и без операторской легенды.
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
                        "❌ Не удалось собрать диагностику. См. логи бота.",
                    )
                    return
                message = (
                    "🔎 *Краткая диагностика*\n\n"
                    + "\n".join(lines)
                    + "\n\n_Полный отчёт \\(порты на хосте, детали процессов, пояснения\\) "
                    "доступен только администратору\\._"
                )
                if len(message) > 3800:
                    message = message[:3800] + "\n…\\(сокращено\\)"
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
                    "❌ Не удалось собрать диагностику. См. логи бота.",
                )
                return

            # Сводка по слушающим портам — полезна, чтобы быстро увидеть конфликты.
            try:
                host_ports = live_status.host_listen_ports()
                tcp_sample = sorted(host_ports.get("tcp", set()))
                udp_sample = sorted(host_ports.get("udp", set()))
            except Exception:
                tcp_sample, udp_sample = [], []

            def _fmt_ports(ports):
                if not ports:
                    return "нет данных"
                shown = ports[:25]
                more = len(ports) - len(shown)
                base = ", ".join(str(p) for p in shown)
                return base + (f" (+{more})" if more > 0 else "")

            tcp_line = esc(_fmt_ports(tcp_sample))
            udp_line = esc(_fmt_ports(udp_sample))

            diag_legend = (
                "\n\n*Не путать три разных компонента:*\n"
                "• *VLESS\\-Reality \\(бот → host Xray\\)* — транспорт, который ведёт бот "
                "\\(`xray` на хосте, `/usr/local/etc/xray`\\); это *не* веб\\-панель\\.\n"
                "• *Dockhand \\(Docker\\)* — Streamlit для логов и диагностики бота; "
                "не VPN и не панель клиентов Xray\\.\n"
                "• *3x\\-ui* — сторонняя веб\\-панель Xray; в её блоке строка "
                "*развёртывание 3x-ui* показывает *нативно на хосте* или *Docker*\\.\n"
            )

            footer = (
                "\n\n*Слушающие порты на хосте:*\n"
                f"• TCP: {tcp_line}\n"
                f"• UDP: {udp_line}\n\n"
                "_🟢 — реально работает; 🔴 — выключено/не запущено;_\n"
                "_⚪ — не удалось проверить \\(нет прав/нет данных\\)\\._"
            )

            message = (
                "🔎 *Диагностика транспортов*\n\n"
                + "\n\n".join(lines)
                + diag_legend
                + footer
            )
            # Telegram limit ~4096 chars; обрезаем по необходимости.
            if len(message) > 3800:
                message = message[:3800] + "\n…\\(сокращено\\)"

            try:
                await msg.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception as md_exc:
                # MarkdownV2 строгий: один не-экранированный символ в
                # любой из строк live_status'а валит весь reply. Чтобы
                # admin не оставался без диагностики — retry plain text.
                logger.warning(
                    "diag_command MD2 failed (%s) — fallback plain text",
                    md_exc,
                )
                # Снимаем MD2-экранирование (\\X → X) для читаемости.
                plain = (
                    message.replace("\\", "")
                    .replace("*", "")
                    .replace("_", "")
                    .replace("`", "")
                )
                await msg.reply_text(plain)
        except Exception as e:
            logger.error(f"Error in diag_command: {e}")
            await update.message.reply_text("Ошибка при сборе диагностики.")

    # === Help: section texts (class-level) ===

    _HELP_SECTIONS = {
        "help_main": (
            "📚 *Основные команды*\n\n"
            "• `/start` — приветствие и статус\n"
            "• `/help` — это меню\n"
            "• `/info` — профиль пользователя\n"
            "• `/diag` — краткий статус транспортов; полный отчёт — у админа\n"
            "• `/clear` — очистить чат"
        ),
        "help_protocols": (
            "🧩 *Протоколы*\n\n"
            "Выберите транспорт ниже\\. В каждом разделе есть короткий сценарий запуска, "
            "экспорт профиля и команды диагностики\\.\n\n"
            "*Рабочий flow для пользователя:*\n"
            "• `/provision <id>` — создать bot\\-managed профили\n"
            "• `/profiles <id>` — посмотреть и выдать ссылки/QR\n"
            "• `/user <id>` — карточка с кнопками создать/удалить/ротировать\n\n"
            "*Быстрая проверка всех транспортов:* `/diag`"
        ),
        "help_roadmap": (
            "🚀 *Быстрый старт / сервер с нуля*\n\n"
            "Короткий маршрут для чистого VPS: сначала поднимите основной профиль, затем добавляйте запасные протоколы\\.\n\n"
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
            "Проверка: `/PROTO\\_status` и `/PROTO\\_logs`"
        ),
        "help_admin": (
            "🔧 *Система и ключи*\n\n"
            "*Ежедневно полезно:*\n"
            "• `/info` — профиль и Telegram ID\n"
            "• `/ver` — версия, адрес VPS и полная сводка VLESS\\-Reality\n"
            "• `/dockhand` — доступ к панели Dockhand\n"
            "• `/headscale` — Tailscale IP сервера\n"
            "• `/diag` — транспортная диагностика\n\n"
            "*Ключи:*\n"
            "• `/api` и `/encryption\\_key` — показать маску\n"
            "• `/gen\\_api\\_key` / `/del\\_api\\_key` — API ключи\n"
            "• `/gen\\_encryption\\_key` / `/del\\_encryption\\_key` — ключи шифрования\n"
            "• `/gen\\_chacha\\_key` / `/gen\\_pqc\\_key` — доп\\. ключи\n\n"
            "*ИИ:*\n"
            "• `/ai\\_provider openai` — выбрать провайдера\n"
            "• `/ch\\_model` — выбрать модель"
        ),
        "help_backup": (
            "💾 *Бэкапы*\n\n"
            "Offsite backup работает через rclone и полезен перед обновлениями, "
            "переездом VPS или крупными изменениями конфигов\\.\n\n"
            "*Команды:*\n"
            "• `/rclone` — что это и как включить backup\n"
            "• `/backup\\_status` — статус rclone\n"
            "• `/backup\\_test` — проверить remote\n"
            "• `/backup\\_now` — создать backup\n"
            "• `/backup\\_list` — последние архивы\n\n"
            "Перед ручными правками `*_config\\.json` лучше сделать `/backup\\_now`\\."
        ),
        "help_diag": (
            "🔎 *Диагностика*\n\n"
            "*Главное:*\n"
            "• `/diag` — сводка транспортов, процессов и портов\n"
            "• `/ver` — версия, адрес VPS и VLESS\\-сводка\n"
            "• `/dockhand` — SSH\\-туннель к панели логов\n\n"
            "*По протоколам:*\n"
            "• `/PROTO\\_status` — состояние конкретного транспорта\n"
            "• `/PROTO\\_logs 80` — последние логи, где поддерживается\n"
            "• `/vless\\_test` — проверка VLESS порта\n\n"
            "Если неясно, кто занял порт `443`, сначала смотрите `/diag`, затем конкретный раздел в «Протоколах»\\."
        ),
        "help_users": (
            "👥 *Пользователи*\n\n"
            "*Основной рабочий сценарий:*\n"
            "• `/user 12345` — карточка пользователя с кнопками профилей\n"
            "• `/profiles 12345` — показать выданные профили\n"
            "• `/provision 12345` — создать bot\\-managed клиентов\n"
            "• `/email\\_profile 12345` — отправить профили на email\n\n"
            "*Списки и роли:*\n"
            "• `/list\\_users` — все известные пользователи\n"
            "• `/users\\_log` — журнал первого/последнего обращения\n"
            "• `/special\\_add 12345` — добавить в особые\n"
            "• `/special\\_remove 12345` — убрать из особых\n\n"
            "*Поля пользователя:*\n"
            "• `/setemail 12345 user@example\\.com` — email для профилей\n"
            "• `/setcity 12345 Moscow` — город пользователя\n"
            "• `/setgreeting 12345 Привет` — личное приветствие\n\n"
            "Подсказка: админу можно просто отправить TG ID числом — бот откроет карточку\\."
        ),
        "help_vless": (
            "🛡️ *VLESS\\-Reality*\n\n"
            "Основной профиль: маскирует трафик под обычный HTTPS\\.\n\n"
            "*Быстрый старт \\(legacy host\\-Xray\\):*\n"
            "`/vless\\_set\\_server IP` → `/vless\\_sync` → `/vless\\_on` → `/provision <id>`\n"
            "Прямой путь \\(без 3x\\-ui\\): `/vless\\_add\\_client phone` → `/vless\\_qr phone`\n\n"
            "⚠️ `/vless\\_on` обязателен: без него `/provision` не включит VLESS\\. "
            "При активной 3x\\-ui клиентами управляет панель — `/vless\\_add\\_client` "
            "редиректит на `/provision`\\.\n\n"
            "*Команды:*\n"
            "• `/vless\\_status` — статус\n"
            "• `/vless\\_config` — конфиг\n"
            "• `/vless\\_gen\\_keys` — ключи Reality\n"
            "• `/vless\\_set\\_port 443` — порт\n"
            "• `/vless\\_on` / `/vless\\_off` — включить или выключить\n"
            "• `/vless\\_test` — проверка порта\n"
            "• `/vless\\_export` — экспорт\n\n"
            "*Клиенты:*\n"
            "`/vless\\_list\\_clients` — список VLESS\\-клиентов\n"
            "`/vless\\_add\\_client phone` → `/vless\\_qr phone`\n"
            "`/vless\\_del\\_client phone` — удалить клиента\n\n"
            "Для пользователя по TG ID лучше использовать единый flow: "
            "`/provision <id>` → `/profiles <id>`\\.\n\n"
            "После смены SNI/fingerprint/short\\_id/Reality\\-ключей заново выдайте URI/QR: "
            "`/profiles <id>`, `/my\\_profile` или `/vless\\_export`\\.\n\n"
            "*Смена SNI \\(маскировка\\):*\n"
            "`/vless\\_set\\_sni` без домена — кнопки выбора \\(текущий помечен ✅\\)\\. "
            "Одно нажатие меняет SNI, пишет конфиг и предлагает перезапуск Xray\\. "
            "Мобильные операторы часто режут `www\\.microsoft\\.com` — тогда берите `yahoo\\.com`\\.\n\n"
            "*Если не подключается:*\n"
            "`/xray\\_status`, `/vless\\_test`, затем firewall: `ufw allow 443/tcp`"
        ),
        "help_hy2": (
            "⚡ *Hysteria2*\n\n"
            "Быстрый UDP/QUIC\\-профиль, хорош как запасной канал\\.\n\n"
            "*Быстрый старт \\(по порядку\\):*\n"
            "1\\. `/hy2\\_install`\n"
            "2\\. `/hy2\\_set\\_server IP`\n"
            "3\\. `/hy2\\_gen\\_all` — пароль \\+ сертификат \\+ IP\n"
            "4\\. `/hy2\\_apply` — записать конфиг на сервер\n"
            "5\\. `/hy2\\_on` — пометить профиль активным \\(для `/provision`\\)\n"
            "6\\. `/hy2\\_start` — запустить сервис\n"
            "7\\. `/hy2\\_add\\_client phone` → `/hy2\\_qr phone` — выдать QR\n\n"
            "Проверка: `/hy2\\_status` покажет 🟢 профиль \\+ 🟢 сервис и подскажет следующий шаг\\.\n\n"
            "*Команды:*\n"
            "• `/hy2\\_status` / `/hy2\\_config` — состояние\n"
            "• `/hy2\\_on` / `/hy2\\_off` — активировать / снять профиль\n"
            "• `/hy2\\_set\\_port 8443` — UDP порт\n"
            "• `/hy2\\_set\\_obfs salamander pass` — обфускация\n"
            "• `/hy2\\_set\\_speed 0 0` — авто скорость\n"
            "• `/hy2\\_set\\_quic\\_safe 1` — Windows\\-совместимость\n"
            "• `/hy2\\_logs` — диагностика\n\n"
            "После смены SNI/obfs/QUIC/порта: `/hy2\\_apply`, затем заново выдайте URI/QR через "
            "`/profiles <id>`, `/my\\_profile` или `/hy2\\_export`\\.\n\n"
            "Firewall: `ufw allow 8443/udp`"
        ),
        "help_tuic": (
            "🔷 *TUIC*\n\n"
            "Лёгкий QUIC\\-профиль с TLS сертификатом и QR для клиента\\.\n\n"
            "*Быстрый старт:*\n"
            "`/tuic\\_set\\_server IP` → `/tuic\\_gen\\_all` → `/tuic\\_apply` → `/tuic\\_start` → `/tuic\\_qr phone`\n\n"
            "*Команды:*\n"
            "• `/tuic\\_status` / `/tuic\\_config` — состояние\n"
            "• `/tuic\\_set\\_port 8444` — UDP порт\n"
            "• `/tuic\\_set\\_cc bbr` — congestion control\n"
            "• `/tuic\\_add phone` / `/tuic\\_list` — клиенты\n"
            "• `/tuic\\_logs` / `/tuic\\_export` — логи и экспорт\n\n"
            "Firewall: `ufw allow 8444/udp`"
        ),
        "help_anytls": (
            "🔶 *AnyTLS*\n\n"
            "TCP\\-профиль с TLS, простой для клиентов и диагностики\\.\n\n"
            "*Быстрый старт:*\n"
            "`/anytls\\_set\\_server IP` → `/anytls\\_gen\\_all` → `/anytls\\_apply` → `/anytls\\_start` → `/anytls\\_qr phone`\n\n"
            "*Команды:*\n"
            "• `/anytls\\_status` / `/anytls\\_config` — состояние\n"
            "• `/anytls\\_set\\_port 8445` — TCP порт\n"
            "• `/anytls\\_gen\\_cert` — сертификат\n"
            "• `/anytls\\_add phone` / `/anytls\\_list` — клиенты\n"
            "• `/anytls\\_logs` / `/anytls\\_export` — логи и экспорт\n\n"
            "Firewall: `ufw allow 8445/tcp`"
        ),
        "help_xhttp": (
            "🌐 *XHTTP \\(VLESS\\+XHTTP\\)*\n\n"
            "VLESS поверх HTTP\\-транспорта: удобно для нестандартных сетей\\.\n\n"
            "*Быстрый старт:*\n"
            "`/xhttp\\_set\\_server IP` → `/xhttp\\_gen\\_all` → `/xhttp\\_apply` → `/xhttp\\_start` → `/xhttp\\_qr phone`\n\n"
            "*Команды:*\n"
            "• `/xhttp\\_status` / `/xhttp\\_config` — состояние\n"
            "• `/xhttp\\_set\\_path /tg` — путь\n"
            "• `/xhttp\\_set\\_host example.com` — host\n"
            "• `/xhttp\\_set\\_mode auto` — режим\n"
            "• `/xhttp\\_logs` / `/xhttp\\_export` — логи и экспорт\n\n"
            "Если path занят: `/xhttp\\_set\\_path /new`"
        ),
        "help_naive": (
            "🌐 *NaiveProxy*\n\n"
            "HTTPS\\-прокси через Caddy, требует домен с корректным DNS\\.\n\n"
            "*Быстрый старт:*\n"
            "`/naive\\_install` → `/naive\\_set\\_domain example.com` → `/naive\\_gen\\_creds` → `/naive\\_apply` → `/naive\\_uri`\n\n"
            "*Команды:*\n"
            "• `/naive\\_status` / `/naive\\_config` — состояние\n"
            "• `/naive\\_set\\_port 443` — HTTPS порт\n"
            "• `/naive\\_set\\_user user` — логин\n"
            "• `/naive\\_set\\_password pass` — пароль\n"
            "• `/naive\\_set\\_dpi scheme https` / `padding on` / `probe\\_resistance on` — DPI\\-параметры\n"
            "• `/naive\\_export` — экспорт\n\n"
            "После клиентских DPI\\-параметров заново выдайте профиль через `/naive\\_export`\\. "
            "После серверных параметров сначала `/naive\\_apply`, потом `/naive\\_export`\\.\n\n"
            "Проверьте DNS домена перед запуском\\."
        ),
        "help_mt": (
            "📡 *MTProto Proxy*\n\n"
            "Нативный Telegram\\-прокси с fake\\-TLS секретом\\.\n\n"
            "*Быстрый старт:*\n"
            "`/mt\\_install` → `/mt\\_set\\_server IP` → `/mt\\_gen\\_all` → `/mt\\_apply` → `/mt\\_start` → `/mt\\_qr phone`\n\n"
            "*Команды:*\n"
            "• `/mt\\_status` / `/mt\\_config` — состояние\n"
            "• `/mt\\_set\\_mode ee\\_split` — режим секрета\n"
            "• `/mt\\_set\\_domain www.microsoft.com` — fake\\-TLS домен\n"
            "• `/mt\\_set\\_workers 4` — воркеры\n"
            "• `/mt\\_logs` / `/mt\\_export` — логи и экспорт\n\n"
            "Firewall: `ufw allow 8443/tcp`"
        ),
        "help_mieru": (
            "🕵️ *Mieru*\n\n"
            "Запасной TCP/UDP транспорт без домена и TLS\\-сертификата\\. "
            "Полезен, если VLESS/Hysteria2/NaiveProxy в сети нестабильны, "
            "и для DPI\\-экспериментов с port/MTU/multiplexing/handshake\\.\n\n"
            "*Quickstart \\(admin\\):*\n"
            "`/mieru\\_install`\n"
            "`/mieru\\_set\\_server IP`\n"
            "`/mieru\\_set\\_port 29999 tcp`\n"
            "`/mieru\\_add\\_client phone`\n"
            "`/mieru\\_apply`\n"
            "`/mieru\\_start`\n"
            "`/mieru\\_export phone`\n\n"
            "*Тонкая настройка:* `/mieru\\_set\\_dpi <param> <value>` — "
            "protocol/port/port\\_range/mtu/multiplexing/handshake/socks5\\_port/logging\\.\n"
            "*Логи и состояние:* `/mieru\\_status`, `/mieru\\_logs \\[N\\]`\\.\n\n"
            "⚠️ После изменения port/MTU/multiplexing/handshake заново выдайте "
            "URI/QR/export через `/mieru\\_export <name>`\\.\n"
            "ℹ️ Подробности — `MIERU\\_GUIDE\\.md`\\."
        ),
        "help_xui": (
            "🛠 *3x\\-ui интеграция \\(опционально\\)*\n\n"
            "Это отдельный режим для VPS, где реально установлена панель "
            "`3x\\-ui` и именно она управляет Xray/VLESS\\-Reality\\.\n\n"
            "Если `/vless\\_list\\_clients` пишет `legacy Xray`, значит VLESS "
            "на этом сервере работает *без панели* через `xray.service` и "
            "`/usr/local/etc/xray/config.json`\\. В таком режиме кнопки "
            "3x\\-ui не управляют текущими VLESS\\-клиентами — используйте "
            "`/vless\\_status`, `/vless\\_sync`, `/vless\\_qr`, "
            "`/vless\\_export`\\.\n\n"
            "Если панель есть \\(локально или через Headscale/Tailscale mesh\\), "
            "бот может ходить в её REST API и создавать/удалять клиентов "
            "в выбранном inbound\\.\n\n"
            "*Настройка:*\n"
            "• `/xui\\_setup` — пошаговый ввод URL → логин → пароль → "
            "выбор inbound\\.\n"
            "  ⚠️ Сообщение с паролем удаляется СРАЗУ после ввода\\.\n"
            "  Пароль хранится только в зашифрованном виде \\(AES\\-256\\-GCM "
            "ключом из `ENCRYPTION\\_KEY`\\)\\.\n"
            "• `/xui\\_status` — состояние, маскированный логин, last\\-seen "
            "связь с панелью\\.\n"
            "• `/xui\\_list` — список inbound'ов\\.\n"
            "• `/xui\\_set\\_inbound <id>` — выбрать дефолтный inbound\\.\n"
            "• `/xui\\_enable` / `/xui\\_disable` — включить/выключить "
            "интеграцию без удаления кредов\\.\n"
            "• `/xui\\_clear YES` — стереть креды полностью\\.\n"
            "• `/xui\\_cancel` — выйти из мастера `/xui\\_setup`\\.\n\n"
            "*В карточке `/user <id>`:*\n"
            "Если интеграция настроена и пользователь — `admin`/`special`, "
            "появляется блок «VLESS через 3x\\-ui» с кнопками "
            "*➕ В 3x\\-ui*, *❌ Из 3x\\-ui*, *📲 QR \\(3x\\-ui\\)*\\. Имя "
            "клиента в панели = имя профиля бота \\(`Vless82\\.\\.09`\\), "
            "так что одного TG ID достаточно для CRUD\\.\n\n"
            "*Как понять, какой режим сейчас:*\n"
            "• `/xui\\_status` — покажет, настроена ли REST\\-интеграция\\.\n"
            "• `/vless\\_list\\_clients` — покажет либо `3x\\-ui inbound`, "
            "либо `legacy Xray`\\.\n\n"
            "Это не пересекается с legacy Xray в `/usr/local/etc/xray`: "
            "3x\\-ui и `xray.service` — разные источники истины\\."
        ),
    }

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /help — главное меню справки с inline-кнопками по протоколам."""
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
            await update.message.reply_text("Ошибка при отображении справки.")

    async def info_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /info - информация о пользователе (доступна всем)."""
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
            await update.effective_message.reply_text("Не удалось получить информацию.")

    async def clear_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /clear - визуальная очистка чата."""
        try:
            user = update.effective_user
            message = update.effective_message
            if message is None:
                return

            if context.args:
                await message.reply_text(
                    "Telegram не даёт боту надёжно удалить произвольные старые "
                    "сообщения по числу. Используйте просто /clear."
                )
                return

            logger.info(f"User {user.id} requested visual chat clearing")

            clear_message = (
                "🧹 *Чат очищен* 🧹\n\n"
                "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
                "История сообщений выше этой отметки визуально отделена\\."
            )

            await message.reply_text(clear_message, parse_mode=ParseMode.MARKDOWN_V2)

        except Exception as e:
            logger.error(f"Error in clear_chat: {e}")
            if update.effective_message:
                await update.effective_message.reply_text("Не удалось очистить чат.")

    async def settings_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /settings — персональные настройки оформления панелей бота."""
        try:
            user = update.effective_user
            self._track_user(user)
            text, kb = self._settings_panel(user.id)
            await self._menu_panel(update.effective_message, text, kb, edit=False)
        except Exception as e:
            logger.error(f"Error in settings_command: {e}")
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Не удалось открыть настройки."
                )

    # === АДМИНСКИЕ КОМАНДЫ - СИСТЕМА И ИНФОРМАЦИЯ ===

    async def version_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /ver — версия приложения (всем); адрес VPS (всем); полный VLESS — только админ."""
        msg = update.effective_message
        if msg is None:
            logger.warning("/ver: effective_message is None")
            return
        try:
            user = update.effective_user
            logger.info(f"User {user.id} requested version info")

            def he(x) -> str:
                """HTML-escape для Telegram HTML parse mode."""
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

            # HTML: не смешивать с MarkdownV2 (в MDV2 `=` и экранирование внутри `code` ломали разбор).
            version_message = f"""📋 <b>Информация о версии</b>

🔖 Версия: <code>{he(ver_disp)}</code>
📦 Название: {name_disp}
📝 Описание: {desc_disp}"""

            # Адрес VPS — всем (как в /start).
            try:
                from dockhand_tunnel_hints import get_dockhand_ssh_params

                p = await asyncio.to_thread(
                    get_dockhand_ssh_params, resolve_public_ip=True
                )
                if p.host_is_placeholder:
                    version_message += (
                        "\n\n🌐 <b>Адрес VPS:</b> автоопределение недоступно "
                        "(задайте <code>/vless_set_server</code> или "
                        "<code>DOCKHAND_SSH_HOST</code> в <code>.env</code>)."
                    )
                else:
                    version_message += (
                        f"\n\n🌐 <b>Адрес VPS:</b> <code>{he(p.host)}</code>"
                    )
            except Exception as ex:
                logger.debug("version_command: VPS host hint failed: %s", ex)
                version_message += "\n\n🌐 <b>Адрес VPS:</b> временно недоступен"

            if not self._is_admin(user.id):
                version_message += (
                    "\n\n<i>Подробный блок VLESS-Reality и готовность конфигурации на сервере — "
                    "в ответе «/ver» у администратора.</i>"
                )
                await msg.reply_text(
                    version_message,
                    parse_mode=ParseMode.HTML,
                )
                return

            # Только администратор: расширенная сводка по VLESS (как раньше у admin/special).
            card = vless_manager.get_vless_version_card_fields()
            vless_status = card["status"]
            server_raw = (card.get("server") or "").strip()
            public_hint = (card.get("public_hint") or "").strip()
            missing_keys = card.get("missing_keys") or []

            if server_raw:
                server_block = (
                    f"Сервер (VLESS, адрес для клиентов): <code>{he(server_raw)}</code>"
                )
            else:
                server_block = (
                    "Сервер (VLESS): <b>не задан</b> — в конфиге нет публичного IP или домена, "
                    "куда клиенты подключаются по Reality.\n"
                    "Укажите: <code>/vless_set_server</code> или <code>/vless_sync</code>."
                )
                if public_hint:
                    server_block += (
                        f"\nНа этом VPS из <code>.env</code>/окружения известен адрес "
                        f"<code>{he(public_hint)}</code> "
                        f"(часто это тот же IP — его можно задать как сервер VLESS).\n"
                        f"Пример: <code>/vless_set_server {he(public_hint)}</code>"
                    )

            gaps_ru = {
                "server": "публичный адрес сервера",
                "uuid": "UUID клиента (корень конфига или запись в clients)",
                "public_key": "ключи Reality",
                "short_id": "short id",
            }
            cfg_line = ""
            if missing_keys:
                labels = [gaps_ru[k] for k in missing_keys if k in gaps_ru]
                if labels:
                    cfg_line = (
                        "\nДля полной конфигурации Reality в <code>vless_config.json</code> не хватает: "
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
                        "\n<b>На хосте:</b> процесс Xray слушает порт (см. <code>/diag</code>).\n"
                        "<b>Ниже:</b> флаги из <code>vless_config.json</code> "
                        "(поле enabled в JSON и полнота полей).\n"
                    )
                elif vr is not None and vr.process_alive is True:
                    runtime_line = (
                        "\n<b>На хосте:</b> процесс Xray найден; порт см. в <code>/diag</code>.\n"
                        "<b>Ниже:</b> <code>vless_config.json</code> "
                        "(не путать с работой бинаря на диске).\n"
                    )
            except Exception:
                runtime_line = ""

            vless_stat_line = (
                "🟢 Включён (JSON: enabled=true)"
                if vless_status["enabled"]
                else "🔴 Выключен (JSON: enabled=false; процесс Xray на VPS может быть запущен отдельно)"
            )
            version_message += f"""

🛡️ <b>VLESS-Reality</b>:{runtime_line}Статус: {vless_stat_line}
Сконфигурирован: {"✅ Да" if vless_status["configured"] else "❌ Нет"}
{server_block}{cfg_line}"""

            await msg.reply_text(
                version_message,
                parse_mode=ParseMode.HTML,
            )

        except Exception as e:
            logger.exception("Error in version_command: %s", e)
            try:
                await msg.reply_text("Ошибка при получении информации о версии.")
            except Exception:
                pass

    async def dockhand_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /dockhand — подсказка по SSH-туннелю к Dockhand (админ/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору или special-пользователю."
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
                "📋 Скопируйте в терминал на своём ПК (PowerShell, cmd или Terminal):\n\n"
                f"{cmd}"
            )

            notes_tail = ""
            if params.notes:
                notes_tail = "\n\n⚠️ " + params.notes[0]
            placeholder_warn = ""
            if params.host_is_placeholder:
                placeholder_warn = (
                    "\n\n⚠️ В команде выше остался плейсхолдер YOUR_SERVER_IP — "
                    "задайте DOCKHAND_SSH_HOST в .env рядом с compose или настройте /vless_set_server."
                )

            body = (
                "Dockhand — панель диагностики на сервере; порт 8501 слушает только 127.0.0.1 на VPS "
                "(см. DOCKHAND_GUIDE.md в репозитории).\n\n"
                "Windows: встроенный OpenSSH (Windows 10/11) — тот же ssh в cmd или PowerShell.\n"
                "macOS / Linux: обычный Terminal — те же команды.\n\n"
                "После установки туннеля откройте на этом же компьютере в браузере:\n"
                "http://localhost:8501\n\n"
                "Фоновый туннель (без интерактивной сессии, удобно на macOS/Linux):\n"
                f"{cmd_bg}\n\n"
                "Остановка фонового процесса (macOS/Linux): "
                'pkill -f "ssh.*127.0.0.1:8501" или найдите PID через ps aux | grep ssh. '
                "На Windows для фона чаще используют отдельное окно или WSL.\n\n"
                f"Подставлено: {params.user}@{params.host}, SSH-порт {params.port}."
                f"{notes_tail}{placeholder_warn}"
            )
            await update.message.reply_text(
                body,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Error in dockhand_command: {e}")
            await update.message.reply_text(
                "Ошибка при формировании подсказки Dockhand."
            )









    async def backup_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /backup_status — статус rclone backup (админ/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору или special-пользователю."
                )
                return
            self._track_user(user)
            await update.message.reply_text(rclone_manager.format_status())
        except Exception as e:
            logger.error(f"Error in backup_status: {e}")
            await update.message.reply_text("Ошибка при проверке backup-статуса.")

    async def rclone_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Короткая справка /rclone с базовыми шагами запуска offsite backup."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору или special-пользователю."
                )
                return

            self._track_user(user)
            await update.message.reply_text(
                "📦 Rclone backup (кратко)\n\n"
                "Если offsite backup ещё не настроен, начните так:\n"
                "1) Подготовьте rclone config на сервере.\n"
                "2) Добавьте в .env минимум: RCLONE_REMOTE, RCLONE_CONFIG.\n"
                "3) Пересоздайте контейнер бота: docker compose up -d --force-recreate telegram-helper.\n\n"
                "Проверка и запуск:\n"
                "• /backup_status — текущий статус\n"
                "• /backup_test — проверка remote\n"
                "• /backup_now — создать backup сейчас\n"
                "• /backup_list — последние архивы."
            )
        except Exception as e:
            logger.error(f"Error in rclone_command: {e}")
            await update.message.reply_text("Ошибка при выводе справки по rclone.")

    async def backup_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /backup_test — проверить доступ к rclone remote (админ/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору или special-пользователю."
                )
                return
            self._track_user(user)
            result = rclone_manager.test_remote()
            await update.message.reply_text(
                rclone_manager.format_command_result("Rclone remote test", result)
            )
        except Exception as e:
            logger.error(f"Error in backup_test: {e}")
            await update.message.reply_text("Ошибка при проверке rclone remote.")

    async def backup_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /backup_now — создать offsite backup runtime-файлов (админ/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору или special-пользователю."
                )
                return
            self._track_user(user)
            await update.message.reply_text("⏳ Запускаю backup runtime-файлов...")
            result = rclone_manager.create_backup()
            await update.message.reply_text(rclone_manager.format_backup_result(result))
        except Exception as e:
            logger.error(f"Error in backup_now: {e}")
            await update.message.reply_text("Ошибка при создании backup.")

    async def backup_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /backup_list — показать последние backup-архивы (админ/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору или special-пользователю."
                )
                return
            self._track_user(user)
            result = rclone_manager.list_backups()
            await update.message.reply_text(
                rclone_manager.format_command_result("Rclone backups", result)
            )
        except Exception as e:
            logger.error(f"Error in backup_list: {e}")
            await update.message.reply_text("Ошибка при чтении списка backup.")

    async def api_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /api - показать маскированный API ключ."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested API key info")

            try:
                from security import ALLOWED_APPS

                all_apps = ["default"] + list(ALLOWED_APPS.keys())

                keyboard = []
                for app_id in all_apps:
                    if app_id == "default":
                        label = "🔑 По умолчанию (из .env)"
                    else:
                        app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                        label = f"🔑 {app_name} ({app_id})"
                    keyboard.append(
                        [InlineKeyboardButton(label, callback_data=f"api_key:{app_id}")]
                    )

                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "🔐 Выберите сервис для просмотра API ключа:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                # Если модуль security не найден, показываем дефолтный ключ
                api_key = os.getenv("API_SECRET_KEY", "не настроен")
                masked = self._mask_secret(api_key)
                await update.message.reply_text(
                    f"🔑 API ключ: `{masked}`", parse_mode=ParseMode.MARKDOWN_V2
                )

        except Exception as e:
            logger.error(f"Error in api_command: {e}")
            await update.message.reply_text("Ошибка при получении API ключа.")

    async def gen_api_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /gen_api_key - сгенерировать новый API ключ."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
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
                        label = "🔑 По умолчанию (в .env)"
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
                    "🔐 Выберите сервис для генерации нового API ключа:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                await update.message.reply_text(
                    "⚠️ Безопасный режим не показывает новый API ключ в Telegram.\n"
                    "Сгенерируйте и сохраните его локально на сервере, затем обновите `API_SECRET_KEY` в `.env`."
                )

        except Exception as e:
            logger.error(f"Error in gen_api_key_command: {e}")
            await update.message.reply_text("Ошибка при генерации API ключа.")

    async def del_api_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /del_api_key - удалить API ключ."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested to delete API key")

            try:
                from app_keys import list_app_ids
                from security import ALLOWED_APPS

                app_ids = list_app_ids()
                if not app_ids:
                    await update.message.reply_text(
                        "❌ Нет сохранённых индивидуальных ключей."
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
                    "🗑️ Выберите сервис для УДАЛЕНИЯ API ключа:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                await update.message.reply_text("❌ Модуль app_keys не найден.")

        except Exception as e:
            logger.error(f"Error in del_api_key_command: {e}")
            await update.message.reply_text("Ошибка при удалении API ключа.")

    async def encryption_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /encryption_key - показать маскированный ключ шифрования."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested Encryption key info")

            try:
                from security import ALLOWED_APPS

                all_apps = ["default"] + list(ALLOWED_APPS.keys())

                keyboard = []
                for app_id in all_apps:
                    if app_id == "default":
                        label = "🔐 По умолчанию (из .env)"
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
                    "🔐 Выберите сервис для просмотра ключа шифрования:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                enc_key = os.getenv("ENCRYPTION_KEY", "не настроен")
                masked = self._mask_secret(enc_key)
                await update.message.reply_text(
                    f"🔐 Ключ шифрования: `{masked}`", parse_mode=ParseMode.MARKDOWN_V2
                )

        except Exception as e:
            logger.error(f"Error in encryption_key_command: {e}")
            await update.message.reply_text("Ошибка при получении ключа шифрования.")

    async def gen_encryption_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /gen_encryption_key - сгенерировать новый ключ шифрования."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
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
                        label = "🔐 По умолчанию (в .env)"
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
                    "🔐 Выберите сервис для генерации нового ключа шифрования:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                await update.message.reply_text(
                    "⚠️ Безопасный режим не показывает новый ключ шифрования в Telegram.\n"
                    "Сгенерируйте и сохраните его локально на сервере, затем обновите `ENCRYPTION_KEY` в `.env`."
                )

        except Exception as e:
            logger.error(f"Error in gen_encryption_key_command: {e}")
            await update.message.reply_text("Ошибка при генерации ключа шифрования.")

    async def del_encryption_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /del_encryption_key - удалить ключ шифрования."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested to delete encryption key")

            try:
                from app_keys import list_app_ids
                from security import ALLOWED_APPS

                app_ids = list_app_ids()
                if not app_ids:
                    await update.message.reply_text(
                        "❌ Нет сохранённых индивидуальных ключей."
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
                    "🗑️ Выберите сервис для УДАЛЕНИЯ ключа шифрования:",
                    reply_markup=reply_markup,
                )
            except ImportError:
                await update.message.reply_text("❌ Модуль app_keys не найден.")

        except Exception as e:
            logger.error(f"Error in del_encryption_key_command: {e}")
            await update.message.reply_text("Ошибка при удалении ключа шифрования.")

    async def gen_chacha_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /gen_chacha_key - сгенерировать ключ ChaCha20-Poly1305."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested to generate ChaCha20-Poly1305 key")

            key_bytes = secrets.token_bytes(32)
            key_hex = key_bytes.hex()
            key_base64 = base64.b64encode(key_bytes).decode("utf-8")

            message = f"""✅ Ключ для ChaCha20-Poly1305 сгенерирован!

🔐 Ключ (hex, 64 символа):
`{key_hex}`

🔐 Ключ (base64):
`{key_base64}`

🔧 Алгоритм: secrets.token_bytes(32) → 256-битный ключ
📊 Размер: 32 байта (256 бит)

💡 ChaCha20-Poly1305:
• Современная альтернатива AES-256-GCM
• Отличная производительность на ARM/мобильных
• Используется в WireGuard, Signal, TLS 1.3

⚠️ Это тестовая команда"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in gen_chacha_key_command: {e}")
            await update.message.reply_text(
                "Ошибка при генерации ключа ChaCha20-Poly1305."
            )

    async def gen_pqc_key_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /gen_pqc_key - сгенерировать ключ для Post-Quantum Cryptography."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested to generate PQC key")

            key_bytes = secrets.token_bytes(48)
            key_hex = key_bytes.hex()
            key_base64 = base64.b64encode(key_bytes).decode("utf-8")

            message = f"""✅ Ключ для Post-Quantum Cryptography сгенерирован!

🔐 Ключ (hex, 96 символов):
`{key_hex}`

🔐 Ключ (base64):
`{key_base64}`

🔧 Размер: 48 байт (384 бит) - для CRYSTALS-Kyber-768
🛡️ Уровень безопасности: NIST Level 3

💡 Post-Quantum Cryptography (PQC):
• Защита от квантовых компьютеров
• CRYSTALS-Kyber - стандарт NIST

⚠️ Это тестовая команда"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in gen_pqc_key_command: {e}")
            await update.message.reply_text("Ошибка при генерации PQC ключа.")

    # === УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ===

    async def admin_setcity(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /setcity - установить город для пользователя."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ Эта команда доступна только администратору."
            )
            return

        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text("Использование: /setcity <user_id> <city>")
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Неверный user_id")
            return

        city = " ".join(args[1:]).strip()
        if not city:
            await update.message.reply_text("Город не может быть пустым")
            return

        set_user_city(target_id, city)
        await update.message.reply_text(f"✅ Город установлен для {target_id}: {city}")

    async def admin_setgreeting(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /setgreeting - установить приветствие для пользователя."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ Эта команда доступна только администратору."
            )
            return

        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Использование: /setgreeting <user_id> <text>"
            )
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Неверный user_id")
            return

        greeting = " ".join(args[1:]).strip()
        if not greeting:
            await update.message.reply_text("Приветствие не может быть пустым")
            return

        set_user_greeting(target_id, greeting)
        await update.message.reply_text(f"✅ Приветствие установлено для {target_id}")

    async def admin_special_add(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /special_add - добавить особого пользователя."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ Эта команда доступна только администратору."
            )
            return

        args = context.args or []
        if len(args) != 1:
            await update.message.reply_text("Использование: /special_add <user_id>")
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Неверный user_id")
            return

        add_special_user(target_id)
        menu_note = ""
        try:
            await set_special_bot_menu(context.bot, target_id)
            menu_note = "\nМеню команд special обновлено: /my_profile добавлен."
        except Exception as exc:
            logger.warning("special menu setup failed for %s: %s", target_id, exc)
            menu_note = "\n⚠️ Не удалось обновить меню команд сразу; обновится после рестарта бота."
        await update.message.reply_text(
            f"✅ Пользователь {target_id} добавлен в особые{menu_note}"
        )

    async def admin_special_remove(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /special_remove - удалить особого пользователя."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ Эта команда доступна только администратору."
            )
            return

        args = context.args or []
        if len(args) != 1:
            await update.message.reply_text("Использование: /special_remove <user_id>")
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Неверный user_id")
            return

        remove_special_user(target_id)
        menu_note = ""
        try:
            await clear_chat_bot_menu(context.bot, target_id)
            menu_note = "\nПерсональное меню special сброшено."
        except Exception as exc:
            logger.warning("special menu clear failed for %s: %s", target_id, exc)
            menu_note = "\n⚠️ Не удалось сбросить меню команд сразу; обновится после рестарта бота."
        await update.message.reply_text(
            f"✅ Пользователь {target_id} удалён из особых{menu_note}"
        )

    async def admin_list_users(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /list_users — администраторы (ADMIN_USER_IDS), особые (special_user_ids), остальные из базы."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.effective_message.reply_text(
                "⛔ Эта команда доступна только администратору."
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

        lines = [f"*Администраторы* \\({len(admin_ids)}\\)*:*"]
        if admin_ids:
            for uid in admin_ids:
                prefs = users.get(uid, {})
                lines.append(_fmt_user_line(uid, prefs))
        else:
            lines.append("\\-")

        special_only = [uid for uid in special if uid not in admin_set]
        lines.append(f"\n*Особые пользователи* \\({len(special_only)}\\)*:*")
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
        lines.append(f"\n*Обычные пользователи* \\({len(regular)}\\)*:*")
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
                        "📒 Журнал",
                        callback_data="lu_log",
                    ),
                    InlineKeyboardButton(
                        "⭐ Статус special",
                        callback_data="lu_tog",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🧩 Профили по протоколам",
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
        """Все известные user_id из storage, кроме админов (special для них не кликаем здесь)."""
        special, users = storage_list_users()
        admin_set = {int(x) for x in self.config.admin_user_ids}
        uids = sorted(set(users.keys()) | set(special))
        return [u for u in uids if u not in admin_set]

    def _list_users_protocol_candidates(self) -> list[int]:
        """
        Кандидаты для создания профилей по протоколам: ТОЛЬКО admin + special.
        «Обычные» пользователи здесь не показываются — для них создание/
        удаление/ротация профиля запрещены на уровне UI и обработчиков.
        Если такого пользователя действительно надо снабдить профилем —
        сначала переведите его в `special` через `/special_add <id>`.
        """
        special, _users = storage_list_users()
        admin_set = {
            int(x) for x in (self.config.admin_user_ids if self.config else [])
        }
        return sorted(set(special) | admin_set)

    def _is_profile_target_eligible(self, uid: int) -> bool:
        """
        Можно ли управлять профилями для данного TG ID (создавать /
        удалять / ротировать секреты). Разрешено только админу и
        special-пользователям; для обычных — запрещено.
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
            "⭐ Переключение статуса special",
            "",
            "Выберите пользователя. Админы (ADMIN_USER_IDS) здесь не показываются.",
            f"Страница {page + 1}/{total_pages}, всего: {n}.",
            "",
        ]
        if not chunk:
            lines.append("Нет пользователей для выбора.")

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
        rows.append([InlineKeyboardButton("✖️ Закрыть", callback_data="lu_x")])

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
        role = "особый (special)" if is_sp else "обычный"

        text = (
            f"👤 {uid}\n"
            f"Имя: {name}\n"
            f"Username: {un}\n"
            f"Сейчас: {role}\n\n"
            "Выберите действие:"
        )
        if is_sp:
            row_action = [
                InlineKeyboardButton(
                    "⬇️ Убрать из особых",
                    callback_data=f"lu_out:{uid}:{page}",
                )
            ]
        else:
            row_action = [
                InlineKeyboardButton(
                    "⬆️ В особые",
                    callback_data=f"lu_in:{uid}:{page}",
                )
            ]
        kb = InlineKeyboardMarkup(
            [
                row_action,
                [
                    InlineKeyboardButton(
                        "◀️ К списку",
                        callback_data=f"lu_p:{page}",
                    )
                ],
                [InlineKeyboardButton("✖️ Закрыть", callback_data="lu_x")],
            ]
        )
        await query.message.edit_text(text, reply_markup=kb)

    async def _after_special_toggle(
        self, query, uid: int, page: int, *, added: bool
    ) -> None:
        action = "добавлен в особые" if added else "убран из особых"
        text = f"✅ Пользователь {uid} {action}."
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "◀️ К списку",
                        callback_data=f"lu_p:{page}",
                    )
                ],
                [InlineKeyboardButton("✖️ Закрыть", callback_data="lu_x")],
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
        # Mieru — greenfield: канон с самого начала по plan_Mieru.md §7
        # (`Mieru_ID<first2>_<last2>`), без legacy-имени `Mieruxx...yy`.
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
        """Найти VLESS-клиента в панели 3x-ui по UID.

        Сначала каноническое имя (`Vless_ID<first2>_<last2>`, как у /provision),
        затем legacy (`Vless82...09`). Просматривает default_inbound и при
        необходимости legacy bot_inbound_id.
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
        Протоколы, которые:
        1) поддержаны автоматизацией в боте,
        2) реально работают (🟢 в live_status),
        3) умеют выдавать add_client в текущем коде.
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
        # ВАЖНО: для управления профилями берём только admin + special.
        # Обычные пользователи сюда не попадают (политика: сначала /special_add).
        candidates = self._list_users_protocol_candidates()
        n = len(candidates)
        page_size = self._LIST_USERS_PROTOCOL_PAGE
        total_pages = max(1, (n + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        chunk = candidates[page * page_size : (page + 1) * page_size]

        lines = [
            "🧩 Создание профиля по протоколу",
            "",
            "Шаг 1/2: выберите пользователя.",
            "Показываются только admin и special.",
            "Чтобы добавить обычного пользователя — сначала /special_add <id>.",
            f"Страница {page + 1}/{total_pages}, всего: {n}.",
            "",
        ]
        if not chunk:
            lines.append("Нет admin/special пользователей для выбора.")

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
        rows.append([InlineKeyboardButton("✖️ Закрыть", callback_data="lu_x")])
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
        """Есть ли у пользователя клиент (canon / 3x-ui / legacy).

        Важно: не опираемся только на ``profiles_for_user`` — он смотрит
        ``is_enabled()`` в JSON и пропускает Hy2, когда сервис уже live,
        а флаг ``enabled`` ещё false. Из-за этого UI врал «профиль не найден».
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

        # 1) Канон-имя — основной источник после /provision и кнопок Create.
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

        # 2) Legacy-имя карточки /user (Vless52...49, Hysteria252...49).
        legacy_name = self._build_protocol_profile_name(proto_key, uid)
        if proto_key == "vless_reality":
            # При активной 3x-ui legacy host-Xray не считаем «есть профиль».
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
        # Если кто-то всё-таки попал сюда с обычным uid (например, через старую
        # клавиатуру) — не показываем кнопки управления, объясняем правило.
        if not self._is_profile_target_eligible(uid):
            text = (
                f"👤 Пользователь: {uid}\n"
                "⛔ Создание профилей запрещено: пользователь не admin и не special.\n\n"
                "Чтобы выдать профиль, сначала переведите его в special:\n"
                f"`/special_add {uid}`"
            )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "◀️ К выбору пользователя",
                            callback_data=f"lu_prp:{page}",
                        )
                    ],
                    [InlineKeyboardButton("✖️ Закрыть", callback_data="lu_x")],
                ]
            )
            return text, kb

        for key, display in protocols:
            exists = self._protocol_client_exists_for_user(key, uid)
            short = self._PROTO_SHORT_LABELS.get(key, display)
            # Одна строка на протокол: статус в подписи кнопки (раньше
            # отдельная «· … профиль не найден ·» выглядела как второй VLESS).
            if exists:
                row = [
                    InlineKeyboardButton(
                        f"✅ {short}: есть",
                        callback_data=f"lu_prc:{key}:{uid}:{page}",
                    ),
                    InlineKeyboardButton(
                        "♻️ Заменить",
                        callback_data=f"lu_prr:{key}:{uid}:{page}",
                    ),
                ]
            else:
                row = [
                    InlineKeyboardButton(
                        f"➕ {short}: создать",
                        callback_data=f"lu_prc:{key}:{uid}:{page}",
                    )
                ]
            rows.append(row)

        if not rows:
            rows.append(
                [InlineKeyboardButton("Обновить", callback_data=f"lu_pru:{uid}:{page}")]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        "🔄 Обновить статусы",
                        callback_data=f"lu_pru:{uid}:{page}",
                    )
                ]
            )

        rows.append(
            [
                InlineKeyboardButton(
                    "◀️ К выбору пользователя", callback_data=f"lu_prp:{page}"
                )
            ]
        )
        rows.append([InlineKeyboardButton("✖️ Закрыть", callback_data="lu_x")])

        if protocols:
            text = (
                f"👤 Пользователь: {uid}\n"
                "Шаг 2/2: один протокол — одна строка кнопок "
                "(больше нет дубля «статус отдельной кнопкой»).\n\n"
                "✅ есть / ➕ создать — ищем Vless_ID… / Hys_ID… "
                "(и legacy-имя, если есть).\n"
                "Сервис Hy2 может быть 🟢, а клиента у пользователя "
                "ещё нет — тогда жмите «создать» или "
                f"/provision {uid}.\n"
                f"Special: {'да' if is_special_target else 'нет'}."
            )
        else:
            text = (
                f"👤 Пользователь: {uid}\n"
                "Сейчас нет протоколов со статусом 🟢, поддержанных для автосоздания.\n"
                "Поднимите протокол и повторите."
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
        # Политика: профили создаются только для admin/special.
        # Серверный гейт нужен в дополнение к UI-фильтру, чтобы устаревшая
        # клавиатура или прямой callback не могли «пробить» это правило.
        if not self._is_profile_target_eligible(uid):
            try:
                await query.message.reply_text(
                    f"⛔ Создание профиля для пользователя {uid} запрещено.\n"
                    "Профили выдаются только админам и special-пользователям.\n"
                    f"Чтобы выдать профиль, сначала: /special_add {uid}"
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
        # Stage 3: единый routing с canon-naming.
        #
        # - VLESS на VPS с 3x-ui → xui_manager.provision_named_client
        #   (создаёт клиента в bot-managed inbound, имя `Vless_ID*_*`).
        # - VLESS без xui → legacy vless_manager.add_client (host-Xray).
        # - Остальные протоколы (Hys/Mtp/Tuic/AnyTLS/XHTTP) — всегда
        #   через свои `*_manager.add_client(canon_name)`. Это то, что
        #   делает provision_manager изнутри.
        #
        # Канон-имя имеет формат `<Prefix>_ID<first2>_<last2>`. Раньше
        # picker использовал legacy `<Proto>_uid_suffix` (`Vless82...09`),
        # которое не пересекалось с тем, что создавалось через /provision.
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
                # Bot-managed inbound на 3x-ui (clone от default_inbound).
                if replace_existing:
                    rm_ok, rm_msg = xui_manager.remove_named_client(name)
                    details.append(f"[replace via xui] {rm_msg}")
                ok, msg, uri = xui_manager.provision_named_client(name, uid)
                details.append(f"[xui] {msg}")
                uri_for_qr = uri
            elif proto_key == "vless_reality":
                # Bare host-Xray (legacy путь).
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
                details.append(f"Неизвестный протокол: {proto_key}")
                ok = False
        except Exception as exc:
            logger.error(
                "protocol profile create failed: proto=%s uid=%s err=%s",
                proto_key,
                uid,
                exc,
            )
            details.append(f"Ошибка: {exc}")
            ok = False

        # Если для VLESS-Reality через xui мы получили готовый URI —
        # отправим QR этим же сообщением, чтобы UX был как у /provision.
        if ok and uri_for_qr:
            try:
                await self._reply_qr_for_link(query.message, uri_for_qr, name)
            except Exception as exc:
                logger.warning("post-create QR send failed: %s", exc)

        status = (
            "✅ Профиль заменён"
            if (ok and replace_existing)
            else ("✅ Профиль создан" if ok else "❌ Не удалось создать профиль")
        )
        text = (
            f"{status}\n"
            f"Пользователь: {uid}\n"
            f"Протокол: {proto_key}\n"
            f"Имя профиля: {name}\n\n" + "\n".join(details[:6])
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "➕ Добавить ещё протокол", callback_data=f"lu_pru:{uid}:{page}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "◀️ К выбору пользователя", callback_data=f"lu_prp:{page}"
                    )
                ],
                [InlineKeyboardButton("✖️ Закрыть", callback_data="lu_x")],
            ]
        )
        # edit_text может упасть если оригинальное сообщение нельзя
        # править (слишком старое, Markdown-парс на новом тексте,
        # rate-limit). Fallback — отправить новым сообщением.
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
        Получить VLESS-Reality URI клиента из 3x-ui (если интеграция включена).

        Возвращает (ok, message, link, email_в_панели). Ищет клиента по канону
        `Vless_ID*_*`, затем по legacy-имени карточки `/user`.
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
        Найти профиль пользователя по стандартному шаблону имени.
        Сейчас поддерживаем self-service для VLESS и Hysteria2.

        Для VLESS: если включена интеграция с 3x-ui (`/xui_setup`), ссылка
        строится из inbound панели (это и есть тот Xray, что реально работает
        на 443). Иначе fallback в локальный `vless_config.json` бота.
        """
        out: dict = {}
        v_name = self._build_protocol_profile_name("vless_reality", uid)
        h_name = self._build_protocol_profile_name("hysteria2", uid)

        xui_ok, xui_msg, xui_link, v_xui_email = self._vless_link_via_xui(uid)
        if xui_ok and xui_link:
            out["vless_reality"] = {
                "name": v_xui_email or v_name,
                "ok": True,
                "message": "ссылка из 3x-ui (рабочий inbound панели)",
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
                        "неактивный/тестовый legacy-профиль: на этом VPS "
                        "рабочий VLESS обслуживает 3x-ui"
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

    # === Карточка пользователя (/user <id> + распознавание голого числа) ===
    #
    # Цель: админ вводит TG ID и сразу получает меню по всем профилям этого
    # пользователя (VLESS-Reality, Hysteria2, MTProto, NaiveProxy) с
    # возможностью создать / удалить / ротировать секрет / получить QR.
    # Имена клиентов детерминированно строятся по `_build_protocol_profile_name`,
    # отдельной БД owner-маппинга не вводим.
    #
    # NaiveProxy — спец-режим: на сервере один общий `basic_auth`, поэтому
    # действия отличаются (ротация меняет учётку для всех, создания
    # отдельных клиентов нет).

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
        return "обычный"

    def _user_protocol_state(self, proto_key: str, uid: int) -> dict:
        """
        Определить, есть ли профиль данного пользователя в данном протоколе,
        и подготовить отображаемые поля. Возвращает dict с ключами:
            name      — детерминированное имя профиля
            exists    — bool
            active    — bool, можно ли считать профиль рабочим источником
            details   — короткая строка для текста карточки (без секретов)
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
                    created_label = f"создан {created[:10]}" if created else "создан"
                    if xui_active:
                        info["details"] = (
                            f"{created_label}; неактивный/тестовый legacy-профиль "
                            "(локальный xray бота, не 3x-ui)"
                        )
                    else:
                        info["active"] = True
                        info["details"] = created_label
                elif xui_active:
                    info["details"] = "рабочий VLESS выдаётся через 3x-ui ниже"
            elif proto_key == "hysteria2":
                client = hysteria2_manager.get_client(name)
                if client:
                    info["exists"] = True
                    info["active"] = True
                    created = client.get("created_at", "")
                    info["details"] = f"создан {created[:10]}" if created else "создан"
            elif proto_key == "mtproto":
                client = mtproto_manager.get_client(name)
                if client:
                    info["exists"] = True
                    info["active"] = True
                    created = client.get("created_at", "")
                    info["details"] = f"создан {created[:10]}" if created else "создан"
            elif proto_key == "mieru":
                # Mieru: per-user клиенты с canonical-именем Mieru_ID82_09.
                client = mieru_manager.get_client(name)
                if client:
                    info["exists"] = True
                    info["active"] = True
                    created = client.get("created_at", "")
                    info["details"] = f"создан {created[:10]}" if created else "создан"
            elif proto_key == "naiveproxy":
                # NaiveProxy: per-user клиентов нет. Покажем общую учётку как информацию.
                raw = naiveproxy_manager.get_status()
                username = raw.get("username") or ""
                if username:
                    info["exists"] = True
                    info["active"] = True
                    info["name"] = username
                    info["details"] = "общая учётка (один basic_auth для всех)"
                else:
                    info["details"] = (
                        "basic_auth не настроен (используйте /naive_gen_creds)"
                    )
        except Exception as exc:
            logger.warning("_user_protocol_state(%s, %s): %s", proto_key, uid, exc)
        return info

    def _user_card_compose(self, uid: int) -> tuple[str, InlineKeyboardMarkup]:
        """Собрать текст карточки пользователя и инлайн-клавиатуру действий."""

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
        # Политика выдачи профилей: только admin/special.
        eligible = self._is_profile_target_eligible(uid)

        # Live-проверка протоколов: показываем «✅ Vlessxx..yy» только если
        # серверный процесс реально работает; иначе профиль может быть в JSON,
        # но клиенты его всё равно не используют.
        xui_present = False
        try:
            snapshot = live_status.gather_full_snapshot()
            live_by_key = {st.key: st for st in snapshot.get("protocols", [])}
            xui = snapshot.get("xui_panel")
            if xui is not None:
                # «Присутствует» = реально работает либо хотя бы установлен.
                xui_present = (
                    xui.process_alive is True
                    or xui.configured is True
                    or (xui.container_status or {}).get("found") is True
                )
        except Exception:
            live_by_key = {}

        # HTML: MarkdownV2 ломался на «.» в датах/путях и на esc() внутри `code`.
        lines: list[str] = [
            "👤 <b>Карточка пользователя</b>",
            "",
            f"<b>ID:</b> <code>{he(uid)}</code>",
            f"<b>Имя:</b> {he(full_name)}",
            f"<b>Username:</b> {he('@' + username if username else '—')}",
            f"<b>Город:</b> {he(city)}",
            f"<b>Last seen:</b> {he(last_seen)}",
            f"<b>Роль:</b> {he(role)}",
        ]
        if not eligible:
            lines.append(
                "<b>Доступ к профилям:</b> "
                + he("🔒 запрещён (только для admin/special)")
            )
            lines.append("")
            lines.append(
                he(
                    "Чтобы выдать этому пользователю профиль, сначала переведите "
                    "его в special: /special_add "
                )
                + f"<code>{he(uid)}</code>"
            )
        if xui_present:
            # Не блокируем создание VLESS через бота, но громко поясняем,
            # что у бота свой Xray (`/usr/local/etc/xray`), а у 3x-ui свой,
            # и что клиенты, добавленные ботом, в панели НЕ появятся.
            lines.append("")
            lines.append(
                "⚠️ На VPS обнаружена 3x-ui. Бот пишет VLESS-клиентов в "
                "свой <code>/usr/local/etc/xray</code>, 3x-ui — в свой "
                "<code>/etc/x-ui/x-ui.db</code>. "
                "Клиенты бота и панели НЕ пересекаются."
            )

        # Если у админа настроена интеграция через API 3x-ui — показываем
        # отдельный блок «через 3x-ui» с собственным набором кнопок. Для
        # карточки проверяем только наличие клиента без раскрытия секретов.
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
        lines.append("<b>Профили:</b>")

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
                line += "❌ нет профиля"
                if state["details"]:
                    line += f" — {he(state['details'])}"
            lines.append(line)

            # Кнопки протокола ниже — формируем по разному в зависимости от
            # того, есть ли профиль и какой это протокол.
            if proto_key == "naiveproxy":
                # У NaiveProxy один общий basic_auth: можно только показать
                # текущую учётку и ротировать её (затронет всех).
                if state["exists"]:
                    naive_row = [
                        InlineKeyboardButton(
                            f"📲 {display} URI",
                            callback_data=f"uc_qr:{uid}:{proto_key}",
                        )
                    ]
                    # Глобальная ротация затрагивает всех клиентов сразу,
                    # поэтому даже право «admin/special-only» она не отменяет;
                    # но мы всё равно скрываем её для не-eligible получателей,
                    # чтобы не плодить «случайных» поводов крутить общий
                    # пароль с карточки обычного пользователя.
                    if eligible:
                        naive_row.append(
                            InlineKeyboardButton(
                                "♻️ Сменить общий пароль",
                                callback_data=f"uc_rot:{uid}:{proto_key}",
                            )
                        )
                    rows.append(naive_row)
                else:
                    if eligible:
                        rows.append(
                            [
                                InlineKeyboardButton(
                                    f"⚙️ Настроить {display}",
                                    callback_data=f"uc_setup:{uid}:{proto_key}",
                                )
                            ]
                        )
                    else:
                        rows.append(
                            [
                                InlineKeyboardButton(
                                    "🔒 только для admin/special",
                                    callback_data=f"uc_locked:{uid}:{proto_key}",
                                )
                            ]
                        )
                continue

            # VLESS / Hy2 / MTProto: per-client модель.
            if state["exists"]:
                if proto_key == "vless_reality" and not state.get("active"):
                    row = []
                    if eligible:
                        row.append(
                            InlineKeyboardButton(
                                "🧹 Удалить тестовый",
                                callback_data=f"uc_del:{uid}:{proto_key}",
                            )
                        )
                    else:
                        row.append(
                            InlineKeyboardButton(
                                "🔒 только для admin/special",
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
                # Ротация и удаление профиля — тоже только для admin/special.
                if eligible:
                    row.append(
                        InlineKeyboardButton(
                            "♻️ Ротация",
                            callback_data=f"uc_rot:{uid}:{proto_key}",
                        )
                    )
                    row.append(
                        InlineKeyboardButton(
                            "❌ Удалить",
                            callback_data=f"uc_del:{uid}:{proto_key}",
                        )
                    )
                rows.append(row)
            else:
                if not eligible:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                "🔒 только для admin/special",
                                callback_data=f"uc_locked:{uid}:{proto_key}",
                            )
                        ]
                    )
                elif proto_key == "vless_reality" and xui_on:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                "🛠 Рабочий VLESS — в 3x-ui ниже",
                                callback_data="uc_xui_hint",
                            )
                        ]
                    )
                elif live and live.live is True:
                    # Создание клиента — только если протокол реально работает на VPS.
                    rows.append(
                        [
                            InlineKeyboardButton(
                                f"➕ Создать {display}",
                                callback_data=f"uc_create:{uid}:{proto_key}",
                            )
                        ]
                    )
                else:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                f"⚠️ {display} не запущен",
                                callback_data="uc_nolive",
                            )
                        ]
                    )

        # Блок «VLESS через 3x-ui» — добавляем только когда интеграция
        # действительно настроена и включена. Для не-eligible пользователей
        # показываем только информационную строку, кнопок управления нет
        # (та же политика, что и для VLESS-Reality бота).
        if xui_on:
            xui_state = (
                f"✅ рабочий клиент <code>{he(xui_user_name)}</code> · inbound #{he(xui_inbound_id)}"
                if xui_user_exists
                else f"настроена · inbound #{he(xui_inbound_id)} · клиент ещё не найден"
            )
            lines.append(f"🛠 <b>VLESS через 3x-ui:</b> " + xui_state)
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
                        "➕ В 3x-ui",
                        callback_data=f"uc_xc:{uid}",
                    )
                )
                xui_btns.append(
                    InlineKeyboardButton(
                        "❌ Из 3x-ui",
                        callback_data=f"uc_xd:{uid}",
                    )
                )
            else:
                xui_btns.append(
                    InlineKeyboardButton(
                        "🔒 только для admin/special",
                        callback_data=f"uc_locked:{uid}:xui",
                    )
                )
            rows.append(xui_btns)

        rows.append(
            [
                InlineKeyboardButton("🔄 Обновить", callback_data=f"uc_back:{uid}"),
                InlineKeyboardButton("✖️ Закрыть", callback_data="uc_x"),
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
        """Удалить карточку через USER_CARD_TTL_SECONDS; отмена перезапускает отсчёт."""
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
        """Сброс таймера автоудаления карточки: каждое успешное отображение/обновление +3 мин."""
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
        """Отрисовать карточку пользователя.

        target: либо update.message (новая отправка), либо CallbackQuery.message
                (редактирование существующего сообщения).
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
        """Команда /user <id> — открывает карточку пользователя."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /user <telegram_id>\n"
                    "Пример: /user 8288584609\n\n"
                    "Подсказка: можно просто отправить TG ID числом — бот распознает.",
                )
                return
            try:
                uid = int(args[0].strip())
            except (TypeError, ValueError):
                await update.message.reply_text("❌ TG ID должен быть числом.")
                return
            self._track_user(user)
            await self._show_user_card(update.message, uid, edit=False)
        except Exception as exc:
            logger.error("user_card_command: %s", exc)
            await update.message.reply_text(f"Ошибка: {exc}")

    async def admin_raw_id_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """
        Распознать «голое число» от админа в чате как TG ID и открыть карточку.

        Срабатывает только для админов и только когда сообщение содержит
        строго цифры (regex навешен на хендлере). Игнорируется для других
        пользователей и не мешает обычному чату.
        """
        try:
            user = update.effective_user
            if not user or not self._is_admin(user.id):
                return  # тихо игнорируем
            text = (update.message.text or "").strip()
            if not text.isdigit():
                return
            try:
                uid = int(text)
            except ValueError:
                return
            # TG user IDs обычно 5–15 цифр; отсечём слишком короткие/длинные
            # числа, чтобы не путать с другими цифровыми посылками.
            if not (4 <= len(text) <= 15):
                return
            self._track_user(user)
            await self._show_user_card(update.message, uid, edit=False)
        except Exception as exc:
            logger.error("admin_raw_id_message: %s", exc)

    # === Действия в карточке ===

    @staticmethod
    def _legacy_vless_reexport_text() -> str:
        """Подсказка перевыдать URI/QR после смены параметров, попадающих в ссылку."""
        return (
            "\n\n📲 Затем перевыдайте клиентам свежие URI/QR:\n"
            "/profiles <id>   или   /my_profile   или   /vless_qr <имя>"
        )

    @staticmethod
    def _legacy_vless_host_restart_text(*, port: int | None = None) -> str:
        """Что сделать на VPS / в боте после записи Xray-конфига (apply без restart)."""
        lines = [
            "",
            "⚠️ Конфиг записан, но Xray ещё слушает старый. Дальше:",
            "",
            "На VPS (SSH / Tabby):",
            "systemctl restart xray",
            "systemctl status xray --no-pager",
        ]
        if port is not None:
            lines.append(f"ufw allow {int(port)}/tcp")
        lines += [
            "",
            "Или из бота: /xray_restart",
        ]
        return "\n".join(lines) + BotHandlersLite._legacy_vless_reexport_text()

    async def _legacy_vless_apply_followup(
        self, update: Update, *, port: int | None = None
    ) -> None:
        """Применить JSON → host Xray и показать SSH/бот next-steps.

        Дополнительно автоматически сносит все ранее выданные ссылки/QR у всех
        пользователей: после смены параметров, попадающих в vless://-ссылку,
        старые ссылки невалидны и не должны нигде оставаться."""
        apply_ok, apply_msg = vless_manager.apply_xray_config()
        purged = await self._purge_all_profile_messages(update.get_bot())
        follow = self._legacy_vless_host_restart_text(port=port)
        if purged:
            follow += (
                f"\n\n🧹 Старые ссылки/QR удалены у всех ({purged}). "
                "Свежие выдайте заново: /profiles <id> или /my_profile."
            )
        await update.message.reply_text(apply_msg + follow)
        if not apply_ok:
            logger.warning("legacy vless apply followup failed: %s", apply_msg)

    @staticmethod
    def _hy2_apply_followup_text(*, port: int | None = None) -> str:
        """Next-steps после hy2_set_*: JSON ещё не в /etc/hysteria."""
        lines = [
            "",
            "➡️ Дальше в боте: /hy2_apply",
            "(запишет config.yaml и перезапустит hysteria-server)",
        ]
        if port is not None:
            lines.append(f"На VPS при смене порта: ufw allow {int(port)}/udp")
        lines += [
            "",
            "Затем перевыдайте URI/QR: /profiles <id>  или  /my_profile  или  /hy2_qr <имя>",
        ]
        return "\n".join(lines)

    async def _user_card_action_create(self, query, uid: int, proto_key: str) -> None:
        """Создать клиента в нужном протоколе с детерминированным именем."""
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Профили доступны только admin/special. Сначала /special_add.",
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
                    msg += "\nℹ️ Чтобы серверный mita увидел нового клиента: /mieru_apply reload"
            else:
                msg = "Создание для этого протокола не поддерживается"
        except Exception as exc:
            logger.error("uc_create %s/%s: %s", proto_key, uid, exc)
            msg = f"Ошибка: {exc}"
        await query.message.reply_text(
            msg or ("✅ Создан" if ok else "❌ Не удалось создать")
        )
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_delete(
        self, query, uid: int, proto_key: str, confirmed: bool
    ) -> None:
        """Удаление клиента: первый клик — подтверждение, второй — действие."""
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Управление профилем доступно только для admin/special.",
                show_alert=True,
            )
            return
        name = self._build_protocol_profile_name(proto_key, uid)
        if not confirmed:
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Да, удалить",
                            callback_data=f"uc_delok:{uid}:{proto_key}",
                        ),
                        InlineKeyboardButton(
                            "↩️ Отмена",
                            callback_data=f"uc_back:{uid}",
                        ),
                    ]
                ]
            )
            await query.message.edit_text(
                "❗️ Удалить профиль "
                f"<code>{html.escape(name)}</code> ({html.escape(proto_key)}) "
                f"для пользователя <code>{html.escape(str(uid))}</code>?\n\n"
                "После удаления клиент перестанет подключаться. Это нельзя отменить.",
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
                    msg += "\nℹ️ Чтобы серверный mita забыл клиента: /mieru_apply reload"
            else:
                msg = "Удаление для этого протокола не поддерживается"
        except Exception as exc:
            logger.error("uc_delok %s/%s: %s", proto_key, uid, exc)
            msg = f"Ошибка: {exc}"
        await query.message.reply_text(
            msg or ("✅ Удалён" if ok else "❌ Не удалось удалить")
        )
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_rotate(
        self, query, uid: int, proto_key: str, confirmed: bool
    ) -> None:
        """
        Ротация секрета: создаёт нового клиента с тем же именем, но новым
        UUID/паролем/secret. Для NaiveProxy — глобальная смена basic_auth.
        """
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Ротация секрета доступна только для admin/special.",
                show_alert=True,
            )
            return
        name = self._build_protocol_profile_name(proto_key, uid)
        if not confirmed:
            warn = ""
            if proto_key == "naiveproxy":
                warn = (
                    "\n\n⚠️ NaiveProxy использует общий basic_auth для всех "
                    "клиентов. Эта ротация затронет ВСЕХ пользователей, "
                    "не только этого."
                )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Да, ротировать",
                            callback_data=f"uc_rotok:{uid}:{proto_key}",
                        ),
                        InlineKeyboardButton(
                            "↩️ Отмена",
                            callback_data=f"uc_back:{uid}",
                        ),
                    ]
                ]
            )
            await query.message.edit_text(
                "♻️ Сгенерировать новый секрет для "
                f"<code>{html.escape(name)}</code> ({html.escape(proto_key)})?\n\n"
                "Старый секрет перестанет работать после ротации." + warn,
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
                        "\n\nЧтобы новые креды реально применились на сервере, "
                        "выполните `/naive_apply`."
                    )
            else:
                # Для VLESS/Hy2/MTProto: remove → add сохраняет имя, но рождает новый секрет.
                # Сначала удалим, потом создадим. Если первый шаг не удался — выходим.
                if proto_key == "vless_reality":
                    rm_ok, rm_msg = vless_manager.remove_client(name)
                elif proto_key == "hysteria2":
                    rm_ok, rm_msg = hysteria2_manager.remove_client(name)
                elif proto_key == "mtproto":
                    rm_ok, rm_msg = mtproto_manager.remove_client(name)
                elif proto_key == "mieru":
                    rm_ok, rm_msg = mieru_manager.remove_client(name)
                else:
                    rm_ok, rm_msg = False, "Неизвестный протокол"
                if not rm_ok:
                    msg = f"Не удалось удалить старый секрет: {rm_msg}"
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
                                "\nℹ️ Чтобы новый секрет применился на mita: "
                                "/mieru_apply reload"
                            )
                    else:
                        ok, add_msg, _ = mtproto_manager.add_client(name)
                    msg = ("♻️ Секрет ротирован\n" + add_msg) if ok else add_msg
        except Exception as exc:
            logger.error("uc_rotok %s/%s: %s", proto_key, uid, exc)
            msg = f"Ошибка ротации: {exc}"
        await query.message.reply_text(
            msg or ("✅ Готово" if ok else "❌ Не удалось ротировать")
        )
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_xui_create(self, query, uid: int) -> None:
        """Создать VLESS-клиента в 3x-ui (default inbound) с email = profile name."""
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Профили доступны только admin/special.", show_alert=True
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
                "❌ В /xui_status не выбран inbound по умолчанию.\n"
                "Используйте /xui_set_inbound <id>."
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
                f"✅ Клиент создан в 3x-ui (inbound #{inbound_id}).\n"
                f"email: <code>{html.escape(email)}</code>\n"
                f"uuid: <code>{html.escape(uuid_short)}…</code>",
                parse_mode=ParseMode.HTML,
            )
        elif msg == "exists":
            uuid_short = (created.get("id") or "")[:8]
            await query.message.reply_text(
                "ℹ️ Клиент "
                f"<code>{html.escape(email)}</code> уже существует в 3x-ui "
                f"(uuid <code>{html.escape(uuid_short)}…</code>).",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.message.reply_text(f"❌ {msg}")
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_xui_delete(
        self, query, uid: int, confirmed: bool
    ) -> None:
        """Удалить VLESS-клиента из 3x-ui по email."""
        if not self._is_profile_target_eligible(uid):
            await query.answer(
                "⛔ Управление профилями только для admin/special.", show_alert=True
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
                            "✅ Да, удалить",
                            callback_data=f"uc_xdok:{uid}",
                        ),
                        InlineKeyboardButton(
                            "↩️ Отмена",
                            callback_data=f"uc_back:{uid}",
                        ),
                    ]
                ]
            )
            await query.message.edit_text(
                "❗️ Удалить клиента "
                f"<code>{html.escape(email_hint)}</code> из 3x-ui (default inbound)?\n\n"
                "Действие нельзя отменить.",
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
                "❌ В /xui_status не выбран inbound по умолчанию."
            )
            return
        # 3x-ui требует UUID, не email — найдём клиента (канон, затем legacy).
        found, c, email_used, inbound_resolved = self._xui_find_vless_client(
            client_obj, uid
        )
        if not found:
            await query.message.reply_text(
                "ℹ️ Клиент "
                f"<code>{html.escape(str(email_used))}</code> в 3x-ui не найден — возможно, уже удалён.",
                parse_mode=ParseMode.HTML,
            )
            await self._show_user_card(query.message, uid, edit=True)
            return
        ok, msg = client_obj.del_client(inbound_resolved, str(c.get("id") or ""))
        if ok:
            await query.message.reply_text(
                f"✅ Клиент <code>{html.escape(str(email_used))}</code> удалён из 3x-ui.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.message.reply_text(f"❌ {msg}")
        await self._show_user_card(query.message, uid, edit=True)

    async def _user_card_action_xui_qr(self, query, uid: int) -> None:
        """Отправить VLESS-URI и QR клиента из 3x-ui (или explanation)."""
        client_obj, err = xui_manager.make_client_or_error()
        if client_obj is None:
            await query.message.reply_text(f"❌ {err}")
            return
        snap = xui_manager.status_summary()
        inbound_id = int(snap.get("default_inbound_id") or 0)
        if not inbound_id:
            await query.message.reply_text(
                "❌ Не выбран inbound по умолчанию (см. /xui_set_inbound)."
            )
            return
        found, client, email_used, inbound_resolved = self._xui_find_vless_client(
            client_obj, uid
        )
        if not found:
            await query.message.reply_text(
                f"ℹ️ Клиент {email_used} в 3x-ui не найден.\n"
                f"Сначала «➕ В 3x-ui» или выполните /provision {uid}"
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
                f"❌ Не удалось собрать ссылку: {msg}\n"
                "Скопируйте ссылку/QR прямо из самой панели 3x-ui."
            )
            return
        # HTML: legacy Markdown ломался на '_' в email (Vless_ID82_09) и на URI.
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
        """Отправить QR/URI клиента."""
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
                    "🌐 NaiveProxy URI (общий для всех клиентов):\n"
                    f"<code>{html.escape(uri)}</code>",
                    parse_mode=ParseMode.HTML,
                )
            elif proto_key == "mieru":
                await self._reply_mieru_qr(query.message, name)
            else:
                await query.message.reply_text(
                    "❌ QR/URI для этого протокола не поддерживается"
                )
        except Exception as exc:
            logger.error("uc_qr %s/%s: %s", proto_key, uid, exc)
            await query.message.reply_text(f"Ошибка при подготовке QR: {exc}")

    async def _handle_user_card_callbacks(self, query, data: str) -> bool:
        """Маршрутизатор callback'ов карточки пользователя (`uc_*`)."""
        if not data.startswith("uc_"):
            return False
        try:
            if data == "uc_x":
                try:
                    await query.message.delete()
                except Exception:
                    await query.message.edit_text("✖️ Закрыто.")
                return True

            if data == "uc_nolive":
                await query.answer(
                    "Протокол не запущен на этом VPS. Сначала поднимите сервис, потом /diag.",
                    show_alert=True,
                )
                return True

            if data == "uc_xui_hint":
                await query.answer(
                    "Этот VLESS выдаётся через блок 3x-ui: используйте QR (3x-ui) или ➕ В 3x-ui.",
                    show_alert=True,
                )
                return True

            if data.startswith("uc_locked:"):
                # Кликнули по «🔒 только для admin/special» — поясняем правило.
                await query.answer(
                    "Профили выдаются только админам и special-пользователям. "
                    "Сначала /special_add <id>.",
                    show_alert=True,
                )
                return True

            # 3x-ui actions: формат `uc_x<c|d|dok|q>:<uid>` (без proto_key).
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
                # Подсказка для не-настроенных протоколов: показать оператору
                # точные команды для setup'а, а не пытаться сделать всё через UI.
                hints = {
                    "naiveproxy": (
                        "🌐 NaiveProxy ещё не настроен. Базовый сценарий:\n\n"
                        "1) <code>/naive_set_domain ваш_домен</code>\n"
                        "2) <code>/naive_gen_creds</code>\n"
                        "3) <code>/naive_apply</code>\n\n"
                        "Домен должен смотреть на этот VPS, Cloudflare proxy — OFF."
                    ),
                }
                await query.message.reply_text(
                    hints.get(proto_key, "Сначала настройте протокол через его меню."),
                    parse_mode=ParseMode.HTML,
                )
                return True
        except Exception as exc:
            logger.error("_handle_user_card_callbacks(%r): %s", data, exc)
            try:
                await query.message.reply_text(f"❌ Ошибка: {exc}")
            except Exception:
                pass
            return True
        return False

    # === Интеграция с 3x-ui (внешняя панель управления Xray) ===
    #
    # Бот опционально умеет ходить в REST API панели 3x-ui (см.
    # `xui_manager.py`). Креды (URL, логин, пароль, default inbound) админ
    # вводит через ConversationHandler ниже. Пароль:
    #   * **никогда** не печатается в чат бота;
    #   * сразу после получения сообщение с паролем удаляется (если у бота
    #     достаточно прав в этом чате);
    #   * хранится только в xui_config.json в виде AES-256-GCM,
    #     зашифрованного через `SecureMessenger` тем же ключом, что и
    #     остальные секреты проекта (`ENCRYPTION_KEY` / `API_SECRET_KEY`).
    #
    # Без этих кредов всё, что делает бот, — детектирует факт наличия
    # 3x-ui (`live_status._xui_panel_status`) и показывает мягкое
    # предупреждение в /user-карточке. Полный CRUD клиентов работает
    # только после `/xui_setup`.

    # State'ы для ConversationHandler /xui_setup. Целочисленные значения
    # фиксированы внутри объекта класса — bot.py читает их при сборке
    # ConversationHandler.
    XUI_URL, XUI_USER, XUI_PWD, XUI_VERIFY, XUI_INBOUND = range(5)

    async def xui_setup_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """`/xui_setup` — старт пошаговой настройки. Только админ."""
        from telegram.ext import ConversationHandler

        user = update.effective_user
        if not user or not self._is_admin(user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return ConversationHandler.END

        if not xui_manager.encryption_available():
            await update.message.reply_text(
                "❌ В `.env` не задан `ENCRYPTION_KEY` (или `API_SECRET_KEY`).\n"
                "Без него пароль 3x-ui нечем шифровать. Сначала добавьте ключ "
                "в `.env`, перезапустите бот, потом запускайте `/xui_setup`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return ConversationHandler.END

        context.user_data["xui_setup"] = {}
        await update.message.reply_text(
            "🛠 Настройка интеграции с 3x-ui (1/4)\n\n"
            "Введите URL панели одним сообщением. Пример:\n"
            "`https://195.238.122.137:35421/mxmurl`\n\n"
            "Допускаются и `http://`, и `https://`. Хвостовой `/` можно "
            "не ставить — он будет убран.\n\n"
            "Если передумали — `/xui_cancel`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return self.XUI_URL

    async def xui_setup_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = (update.message.text or "").strip()
        ok, normalized, msg = xui_manager.normalize_base_url(raw)
        if not ok:
            await update.message.reply_text(
                f"❌ {msg}\nПовторите ввод или /xui_cancel."
            )
            return self.XUI_URL
        context.user_data.setdefault("xui_setup", {})["base_url"] = normalized
        # Detect mesh-IP (Tailscale/Headscale CGNAT). Бот в bridge-mode
        # не видит mesh — предупреждаем СРАЗУ, чтобы admin успел
        # переключить network_mode до того, как введёт пароль.
        mesh_warning = ""
        try:
            if xui_manager.url_host_is_mesh_ip(normalized):
                mesh_warning = (
                    "\n\n⚠️ Этот URL — <b>mesh-IP</b> "
                    "(Tailscale/Headscale, 100.64/10 или ULA).\n"
                    "Бот в Docker по умолчанию на bridge-сети и "
                    "<b>не увидит</b> mesh-интерфейс хоста.\n\n"
                    "Если login на следующем шаге упадёт с "
                    "<i>network: connection refused / timeout</i>, "
                    "переключите контейнер в host-сеть:\n"
                    "<code>compose.yaml → telegram-helper → "
                    "network_mode: host</code> "
                    "(удалите блок <code>ports:</code>)\n"
                    "и пересоберите: "
                    "<code>docker compose up -d --force-recreate</code>."
                )
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ URL принят: <code>{html.escape(normalized)}</code>\n\n"
            "Шаг 2/4. Введите <b>логин</b> администратора панели." + mesh_warning,
            parse_mode=ParseMode.HTML,
        )
        return self.XUI_USER

    async def xui_setup_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = (update.message.text or "").strip()
        if not name:
            await update.message.reply_text(
                "Логин пуст. Введите ещё раз или /xui_cancel."
            )
            return self.XUI_USER
        context.user_data.setdefault("xui_setup", {})["username"] = name
        await update.message.reply_text(
            "Шаг 3/4. Введите *пароль* одной строкой.\n\n"
            "⚠️ Сразу после получения сообщение с паролем будет удалено "
            "(если у бота достаточно прав в этом чате). В чате он не "
            "сохраняется и в логах не появляется.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return self.XUI_PWD

    async def xui_setup_pwd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pwd = update.message.text or ""
        chat_id = update.effective_chat.id
        # СРАЗУ пытаемся удалить сообщение с паролем. Это лучшая защита —
        # если права позволяют, пароль не остаётся в истории чата.
        try:
            await update.message.delete()
        except Exception as exc:
            logger.warning("xui_setup_pwd: delete pwd message failed: %s", exc)

        if not pwd:
            await context.bot.send_message(
                chat_id, "Пароль пуст. Введите ещё раз или /xui_cancel."
            )
            return self.XUI_PWD

        context.user_data.setdefault("xui_setup", {})["password"] = pwd

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔓 self-signed (не проверять TLS)",
                        callback_data="xui_vfy_no",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔒 валидный TLS (проверять)",
                        callback_data="xui_vfy_yes",
                    )
                ],
                [InlineKeyboardButton("✖️ Отмена", callback_data="xui_vfy_cancel")],
            ]
        )
        await context.bot.send_message(
            chat_id,
            "Пароль принят и зашифрован в памяти.\n\n"
            "Шаг 4/4. Проверять ли TLS-сертификат панели?\n"
            "Для self-signed (типичный случай 3x-ui на IP) — выбирайте "
            "первый вариант.",
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
            await query.message.edit_text("Отменено. Креды не сохранены.")
            return ConversationHandler.END

        verify_tls = query.data == "xui_vfy_yes"
        setup["verify_tls"] = verify_tls

        # Делаем тестовый login + получаем список inbound'ов в одном шаге.
        client = xui_manager.XUIClient(
            base_url=setup.get("base_url", ""),
            username=setup.get("username", ""),
            password=setup.get("password", ""),
            verify_tls=verify_tls,
        )
        ok, msg = client.login()
        if not ok:
            # Если ошибка похожа на сетевую (connection refused/timed out
            # / no route / network) И URL — mesh-IP, дадим точную
            # инструкцию о host-networking, иначе обычный совет.
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
                    "🛰 URL ведёт на <b>mesh-IP</b> "
                    "(Tailscale/Headscale, 100.64/10 или ULA), "
                    "и бот не смог достучаться. "
                    "Скорее всего, контейнер на bridge-сети и не видит "
                    "mesh-интерфейс хоста.\n\n"
                    "<b>Фикс:</b> переключите контейнер в host-сеть.\n"
                    "В <code>compose.yaml</code> у сервиса "
                    "<code>telegram-helper</code> добавьте:\n"
                    "<pre>    network_mode: host</pre>"
                    "и удалите блок <code>ports:</code> (он не нужен и "
                    "конфликтует с host-mode). Пересоберите:\n"
                    "<pre>docker compose up -d --force-recreate "
                    "telegram-helper</pre>"
                    "После этого повторите <code>/xui_setup</code> с тем же URL."
                )
                await query.message.edit_text(
                    f"❌ Логин в 3x-ui не удался: <code>{html.escape(str(msg))}</code>\n\n"
                    + hint,
                    parse_mode=ParseMode.HTML,
                )
            else:
                await query.message.edit_text(
                    f"❌ Логин в 3x-ui не удался: <code>{html.escape(str(msg))}</code>\n\n"
                    "Проверьте URL/логин/пароль и запустите "
                    "<code>/xui_setup</code> заново.",
                    parse_mode=ParseMode.HTML,
                )
            context.user_data.pop("xui_setup", None)
            return ConversationHandler.END

        ok, msg, inbounds = client.list_inbounds()
        if not ok:
            await query.message.edit_text(
                f"❌ Не удалось получить список inbound'ов: {msg}\n\n"
                "Проверьте права админа панели и запустите /xui_setup заново."
            )
            context.user_data.pop("xui_setup", None)
            return ConversationHandler.END

        if not inbounds:
            await query.message.edit_text(
                "⚠️ Панель не вернула ни одного inbound'а.\n\n"
                "Сначала создайте VLESS-Reality inbound в 3x-ui, потом "
                "повторите /xui_setup."
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
        rows.append([InlineKeyboardButton("✖️ Отмена", callback_data="xui_ib_cancel")])

        await query.message.edit_text(
            f"✅ Логин успешен, найдено inbound'ов: {len(inbounds)}.\n\n"
            f"Выберите inbound по умолчанию — в него бот будет добавлять "
            f"VLESS-клиентов из карточки `/user`. Для VLESS-Reality "
            f"берите соответствующий VLESS inbound.",
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
            await query.message.edit_text("Отменено. Креды не сохранены.")
            return ConversationHandler.END
        if not query.data.startswith("xui_ib:"):
            return self.XUI_INBOUND
        try:
            inbound_id = int(query.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer("Неверный inbound ID", show_alert=True)
            return self.XUI_INBOUND

        setup = context.user_data.get("xui_setup", {})
        ok, save_msg = xui_manager.save_credentials(
            base_url=setup.get("base_url", ""),
            username=setup.get("username", ""),
            password=setup.get("password", ""),
            verify_tls=bool(setup.get("verify_tls", False)),
            default_inbound_id=inbound_id,
        )
        # Чистим пароль из user_data (даже если сохранение упало).
        setup["password"] = ""
        url = setup.get("base_url", "")
        username = setup.get("username", "")
        context.user_data.pop("xui_setup", None)

        if not ok:
            await query.message.edit_text(f"❌ Не удалось сохранить: {save_msg}")
            return ConversationHandler.END

        # HTML-режим стабильнее Markdown legacy — Markdown ломается на
        # подчёркиваниях в `/xui_status`, `xui_config.json` и т.д.
        await query.message.edit_text(
            "✅ 3x-ui интеграция настроена\n\n"
            f"URL: <code>{html.escape(url)}</code>\n"
            f"Логин: <code>{html.escape(username)}</code> "
            "(пароль зашифрован в xui_config.json)\n"
            f"Inbound по умолчанию: #{inbound_id}\n\n"
            "Команды: /xui_status · /xui_list · /xui_disable · /xui_clear",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    async def xui_setup_cancel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        from telegram.ext import ConversationHandler

        context.user_data.pop("xui_setup", None)
        if update.message:
            await update.message.reply_text("Отменено. Креды 3x-ui не сохранены.")
        return ConversationHandler.END

    async def xui_status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/xui_status` — состояние интеграции (без секретов)."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        snap = xui_manager.status_summary()
        if not snap["configured"]:
            await update.message.reply_text(
                "⚪ 3x-ui интеграция не настроена. Запустите /xui_setup."
            )
            return
        head = "🟢 включена" if snap["enabled"] else "🔴 выключена"
        text = (
            f"🛠 <b>3x-ui интеграция:</b> {head}\n\n"
            f"URL: <code>{html.escape(snap['base_url'])}</code>\n"
            f"Логин: <code>{html.escape(snap['username_masked'])}</code>\n"
            f"TLS verify: {snap['verify_tls']}\n"
            f"Inbound по умолчанию: #{snap['default_inbound_id']}\n"
            f"Настроено: {html.escape(snap['configured_at'] or '—')}"
        )
        # Лайв-проверка связи с панелью.
        if snap["enabled"]:
            client, err = xui_manager.make_client_or_error()
            if client is None:
                text += f"\n\n⚠️ {html.escape(err or '')}"
            else:
                ok, msg = client.login()
                if ok:
                    ok2, _msg2, inbounds = client.list_inbounds()
                    text += "\n\n✅ Связь с панелью OK" + (
                        f", inbound'ов: {len(inbounds)}" if ok2 else ""
                    )
                    if msg and "auto-fallback" in msg:
                        # login() уже переписал base_url в xui_config.json
                        text += (
                            "\n🔄 URL панели автоматически переключён с "
                            "mesh на loopback (Tailscale был недоступен).\n"
                            f"Сейчас: <code>{html.escape(client.base_url)}</code>"
                        )
                else:
                    text += f"\n\n❌ Логин не удаётся: {html.escape(msg or '')}"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def xui_list_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/xui_list` — список inbound'ов (для выбора /xui_set_inbound)."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        client, err = xui_manager.make_client_or_error()
        if client is None:
            await update.message.reply_text(f"❌ {err}")
            return
        ok, msg = client.login()
        if not ok:
            await update.message.reply_text(f"❌ Логин: {msg}")
            return
        ok, msg, inbounds = client.list_inbounds()
        if not ok:
            await update.message.reply_text(f"❌ list_inbounds: {msg}")
            return
        if not inbounds:
            await update.message.reply_text(
                "Inbound'ов нет. Создайте их в самой панели 3x-ui."
            )
            return
        snap = xui_manager.status_summary()
        default_id = snap["default_inbound_id"]
        lines = ["🛠 <b>Inbound'ы 3x-ui:</b>", ""]
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
        lines.append("Сменить дефолтный: <code>/xui_set_inbound &lt;id&gt;</code>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def xui_set_inbound_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/xui_set_inbound <id>` — выбрать inbound по умолчанию."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Использование: <code>/xui_set_inbound &lt;id&gt;</code>\n"
                "Список: /xui_list",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            inbound_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ ID должен быть числом.")
            return
        ok, msg = xui_manager.set_default_inbound(inbound_id)
        if ok:
            await update.message.reply_text(f"✅ Inbound по умолчанию: #{inbound_id}")
        else:
            await update.message.reply_text(f"❌ {msg}")

    async def xui_enable_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        ok, msg = xui_manager.set_enabled(True)
        await update.message.reply_text(
            "✅ 3x-ui интеграция включена" if ok else f"❌ {msg}"
        )

    async def xui_disable_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        ok, msg = xui_manager.set_enabled(False)
        await update.message.reply_text(
            "🔴 3x-ui интеграция выключена (креды сохранены)" if ok else f"❌ {msg}"
        )

    async def xui_clear_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/xui_clear` — стереть креды панели после подтверждения."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        args = context.args or []
        if not args or args[0].strip().upper() != "YES":
            await update.message.reply_text(
                "❗️ Это удалит сохранённые URL/логин/пароль 3x-ui из "
                "<code>xui_config.json</code>.\n\n"
                "Подтвердите: <code>/xui_clear YES</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        ok, msg = xui_manager.clear_credentials()
        await update.message.reply_text(
            "🗑 Креды 3x-ui удалены. Запустите /xui_setup, когда понадобится."
            if ok
            else f"❌ {msg}"
        )

    # === Bot-managed provisioning ===
    # Канон имён клиентов: <Prefix>_ID<first2>_<last2> от Telegram-ID.
    # Обрабатываются все включённые протоколы, кроме NaiveProxy
    # (single-cred модель). VLESS-Reality pending → решается в Phase 2.

    def _picker_privileged_and_regular_counts(self) -> tuple[int, int]:
        """Счётчики для подсказки picker'а: admin∪special vs обычные."""
        special_ids, users_data = storage_list_users()
        privileged = set(self.config.resolved_admin_user_ids()) | set(special_ids)
        regular_n = sum(1 for uid in users_data if int(uid) not in privileged)
        return len(privileged), regular_n

    def _picker_scope_hint_html(self) -> str:
        """Пояснить, почему в кнопках нет обычных пользователей."""
        priv_n, regular_n = self._picker_privileged_and_regular_counts()
        return (
            f"ℹ️ В кнопках только <b>admin + special</b> "
            f"(сейчас {priv_n}).\n"
            f"Обычных в базе: <b>{regular_n}</b> — им VPN-профили не "
            f"выдаются, пока не сделаете "
            f"<code>/special_add &lt;id&gt;</code>, затем "
            f"<code>/provision &lt;id&gt;</code>.\n"
            f"Все ID смотрите в <code>/list_users</code>; вручную тоже "
            f"можно: <code>/profiles &lt;id&gt;</code>."
        )

    def _build_user_picker_kb(self, action_prefix: str) -> InlineKeyboardMarkup:
        """InlineKeyboard со списком известных TG-ID (admin ∪ special).

        Каждая кнопка — `Имя Фамилия (ID)` или `@username (ID)` из
        `users.json`. callback_data: `{action_prefix}:{uid}` либо
        `{action_prefix}:cancel`. Используется как «autocomplete» для
        команд `/provision`, `/profiles`, `/clean_user` без аргумента.

        Обычные пользователи намеренно не включаются: bot-managed профили
        выдаются только privileged-ролям (см. `_picker_scope_hint_html`).
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
                    "📒 Все пользователи (/list_users)",
                    callback_data=f"{action_prefix}:list_users",
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton("✖ Отмена", callback_data=f"{action_prefix}:cancel")]
        )
        return InlineKeyboardMarkup(rows)

    async def provision_picker_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Callback для inline-picker'ов команд провизионинга.

        Обрабатывает три префикса:
            prov_pick:<uid>   запустить provision_user(<uid>)
            prof_pick:<uid>   показать profiles_for_user(<uid>)
            clean_pick:<uid>  выполнить clean_user(<uid>) (клик и есть
                              подтверждение — кнопка с предупреждением)
        Плюс `<prefix>:cancel` → «Отменено».

        Только для админа: не-админам ничего не отвечает (safe-fail —
        callback зарегистрирован в bot.py до общего handler'а).
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
                await query.message.edit_text("Отменено.")
            except Exception:
                pass
            return
        if payload == "list_users":
            # Кнопка-подсказка из picker'а: обычные не в списке кнопок.
            try:
                await query.message.edit_text(
                    "📒 Чтобы увидеть <b>обычных</b> пользователей "
                    "(и все роли), откройте:\n"
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
                f"⏳ Выполняю для <code>{target_id}</code>…",
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
                header="🛠 <b>Провизионинг для TG-ID:</b>",
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
                header="📋 <b>Профили TG-ID:</b>",
                mode="profiles",
            )
            none_existing = not any(p.get("exists") for p in profiles.values())
            if none_existing:
                text += (
                    f"\n\nПрофили не созданы. "
                    f"Запустите <code>/provision {target_id}</code>."
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
                header="🗑 <b>Очистка TG-ID:</b>",
                mode="clean",
            )
            await query.message.edit_text(text, parse_mode=ParseMode.HTML)
            return

    @staticmethod
    def _xui_is_active() -> bool:
        """3x-ui интеграция настроена И включена. Используется legacy
        VLESS-командами как роутинг-флаг: если активна — данные тянем
        из панели, локальный `vless_config.json` показываем как fallback."""
        try:
            return xui_manager.is_configured() and xui_manager.is_enabled()
        except Exception:
            return False

    async def _legacy_per_client_guard(self, update: Update) -> bool:
        """Per-client legacy команды (`/<proto>_add_client`,
        `/<proto>_del_client`, `/<proto>_qr <name>`) для Hys/Mtp/Tuic/
        AnyTLS/XHTTP всегда редиректят на единый bot-managed flow.

        После Stage 3 это снимает «два пути на одни данные»: один
        источник правды для per-user провизионинга — `provision_manager`
        с canonical-именами `<Prefix>_ID<first2>_<last2>`. Per-inbound
        конфиг (`/<proto>_set_*`, `/<proto>_gen_*`, `/<proto>_status`,
        `/<proto>_export`) не трогаем — это легитимный путь admin'а.
        """
        text = (
            "🛑 Per-client операции теперь идут через единый "
            "<b>bot-managed flow</b> с canonical-именами.\n\n"
            "• <code>/provision &lt;user_id&gt;</code> — создать профили "
            "во всех включённых протоколах сразу\n"
            "• <code>/profiles &lt;user_id&gt;</code> — посмотреть URL и QR\n"
            "• <code>/clean_user &lt;user_id&gt; YES</code> — удалить\n"
            "• <code>/email_profile &lt;user_id&gt;</code> — отправить на email\n\n"
            "Список всех bot-managed клиентов протокола (read-only) "
            "по-прежнему доступен через <code>/&lt;proto&gt;_list_clients</code>."
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return True

    async def _legacy_vless_guard(self, update: Update, action_kind: str) -> bool:
        """Если xui активен — отправляет redirect-сообщение под `action_kind`
        и возвращает True. Caller должен сразу `return`, если True.

        action_kind:
          'config'   — настройка inbound'а (server/port/keys/sni/fingerprint/...)
                       — делается напрямую в панели 3x-ui;
          'client'   — управление клиентами (add/del) — через
                       /provision и /clean_user;
          'service'  — on/off/test/sync/reset/qr — реальный xray
                       запускает 3x-ui, не bot.
        """
        if not self._xui_is_active():
            return False
        try:
            base_url = (xui_manager.load_config().get("base_url") or "").strip()
        except Exception:
            base_url = ""
        url_html = (
            f"<code>{html.escape(base_url)}</code>" if base_url else "<i>не задан</i>"
        )
        if action_kind == "config":
            text = (
                "🛑 Эта команда правит локальный <code>vless_config.json</code> "
                "бота (legacy host-Xray flow).\n\n"
                "На этом VPS активна <b>3x-ui интеграция</b> — все inbound-"
                "настройки (server/port/UUID/Reality keys/SNI/fingerprint) "
                "делайте <b>напрямую в панели</b>: " + url_html + "\n\n"
                "Состояние: /vless_status · /xui_status · /xui_list"
            )
        elif action_kind == "client":
            text = (
                "🛑 Управление клиентами на этом VPS идёт через "
                "<b>bot-managed flow</b> (3x-ui интеграция активна).\n\n"
                "• <code>/provision &lt;user_id&gt;</code> — создать профиль\n"
                "• <code>/profiles &lt;user_id&gt;</code> — посмотреть существующие\n"
                "• <code>/clean_user &lt;user_id&gt; YES</code> — удалить\n"
                "• <code>/email_profile &lt;user_id&gt;</code> — отправить на email\n\n"
                "Список всех bot-managed клиентов: /vless_list_clients"
            )
        else:  # service
            text = (
                "🛑 Эта команда управляет локальным <code>xray.service</code> "
                "бота (legacy host-Xray). На этом VPS реальный xray "
                "запущен <b>панелью 3x-ui</b>: " + url_html + "\n\n"
                "Управляйте сервисом через панель / 3x-ui CLI на сервере."
            )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return True

    @staticmethod
    def _count_inbound_clients(inbound) -> int:
        """Количество клиентов в inbound (settings — JSON-string)."""
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
        """Сколько в inbound клиентов с canonical-именем
        (`<Prefix>_ID<two>_<two>` от bot-managed flow). Используется в
        overview, чтобы admin видел разделение manual / bot-managed
        в одной таблице."""
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
        """Отправляет HTML-сообщение «реальное состояние VLESS-Reality
        через 3x-ui» — для legacy `/vless_status` и `/vless_export`,
        чтобы admin видел актуальную картину панели вместо локального
        `vless_config.json`.

        intent='status' → акцент на состояние / inbound'ы / навигацию.
        intent='export' → акцент на per-user выгрузку (`/provision`,
                          `/profiles`, `/email_profile`).
        """
        cfg = xui_manager.load_config()
        base_url = cfg.get("base_url", "")
        default_id = int(cfg.get("default_inbound_id") or 0)
        # Legacy bot_inbound_id от старой схемы (Variant A) — может ещё
        # висеть в панели, показываем как «orphan» если найдём.
        legacy_bot_id = int(cfg.get("bot_inbound_id") or 0)

        # Live-данные с панели — best effort
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
                                f"{total_clients} клиентов "
                                f"(<i>{manual_clients} ручных + {canon_clients} bot-managed</i>)"
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
                                f"<i>(legacy от старой схемы клон-inbound, "
                                f"можно удалить вручную)</i>: {n} клиентов"
                            )
        except Exception as exc:
            logger.warning("vless_xui_overview live failed: %s", exc)

        header_emoji = "🛡" if intent == "status" else "📤"
        header_text = (
            "VLESS-Reality (через 3x-ui)"
            if intent == "status"
            else "VLESS-Reality экспорт (через 3x-ui)"
        )
        lines = [
            f"{header_emoji} <b>{header_text}</b>",
            "",
            f"Источник: панель 3x-ui — <code>{html.escape(base_url)}</code>",
        ]
        if not live_ok:
            lines.append(
                "⚠️ Не удалось получить live-данные с панели — показываю "
                "только cached config из <code>xui_config.json</code>."
            )

        if inbound_lines:
            lines.append("")
            lines.append("<b>Inbounds:</b>")
            lines.extend(inbound_lines)
            lines.append("")
            lines.append(
                "<i>Bot-managed клиенты пишутся в default_inbound (на :443) "
                "с canonical-именами (<code>Vless_ID*_*</code>). Manual "
                "клиенты админа (произвольные имена) бот не трогает.</i>"
            )

        if intent == "status":
            lines.append("")
            lines.append(
                "Управление: /xui_status · /xui_list · /provision · "
                "/profiles · /clean_user"
            )
        else:  # export
            lines.append("")
            lines.append("📤 <b>Получить клиентский профиль:</b>")
            lines.append("• <code>/provision &lt;user_id&gt;</code> — создать новый")
            lines.append(
                "• <code>/profiles &lt;user_id&gt;</code> — посмотреть существующий"
            )
            lines.append(
                "• <code>/email_profile &lt;user_id&gt;</code> — отправить на email"
            )

        lines.append("")
        lines.append(
            "<i>Ниже — локальный <code>vless_config.json</code> бота "
            "(legacy host-Xray, на этом VPS не используется при наличии "
            "3x-ui-интеграции).</i>"
        )

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    def _vless_firewall_hint(self) -> str:
        """No-op после Variant B: bot пишет canonical-клиентов в default
        inbound (обычно :443, который уже открыт под VLESS-Reality).
        Отдельного bot-managed inbound на нестандартном порту больше
        нет — firewall-подсказка не нужна."""
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
                "\n\n⚠️ <b>Legacy VLESS не применён полностью.</b>\n"
                "Проверьте сообщение выше, затем по SSH:\n"
                "<code>xray run -test -config /usr/local/etc/xray/config.json</code>\n"
                "<code>systemctl status xray --no-pager</code>"
            )
        if mode == "clean" and not vless.get("removed"):
            return ""
        return (
            "\n\nℹ️ <b>Legacy VLESS применён, Xray перезапущен автоматически.</b>\n"
            "После выдачи/замены профиля переимпортируйте свежую ссылку в клиенте "
            "(Karing и др.). Если трафика нет, проверьте live-log:\n"
            "<code>journalctl -u xray -f --no-pager</code>"
        )

    async def _send_qrs_for_results(self, message, results, user_id: int = 0) -> list:
        """Для каждого результата с готовым URI — отправить QR-картинку
        отдельным сообщением (по одной на протокол) и **запланировать
        авто-удаление через 15 минут** (через `_schedule_admin_msg_ttl`).

        Используется в `/provision <id>` и `/profiles <id>` (и их picker-
        callback'ах) чтобы admin сразу увидел рабочий QR. В пакетном
        `/provision_all` намеренно не зовётся — там это спамом было бы.

        Возвращает список отправленных Message (или пустой список) —
        caller может тоже что-то с ними сделать.
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
        mode='provision' — показывает уже-был/создан/ошибка
        mode='profiles'  — read-only показывает есть/нет
        mode='clean'     — удалён/не было/ошибка
        """
        lines = [f"{header} <code>{target_id}</code>", ""]
        if not results:
            lines.extend(
                [
                    "⚠️ Нет результатов по протоколам.",
                    "",
                    "Проверьте:",
                    "• <code>/xui_status</code> — настроена ли 3x-ui интеграция",
                    "• <code>/xui_list</code> — выбран ли inbound по умолчанию",
                    "• <code>/diag</code> — какие транспорты реально live",
                    "",
                    "Для старого VPS с локальной 3x-ui после деплоя обычно нужно "
                    "заново выполнить <code>/xui_setup</code>.",
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
                    label = "уже был"
                elif r.get("ok"):
                    label = "создан"
                else:
                    label = "ошибка"
            elif mode == "profiles":
                label = "есть" if r.get("exists") else "нет"
            else:  # clean
                if r.get("ok") and r.get("removed"):
                    label = "удалён"
                elif r.get("ok"):
                    label = "не было"
                else:
                    label = "ошибка"
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
                "📋 <i>Тапни на URL чтобы скопировать в буфер. "
                "Или /email_profile &lt;id&gt; — отправить на email.</i>"
            )
        hint = self._legacy_vless_restart_hint(results, mode)
        if hint:
            lines.append(hint)
        return "\n".join(lines)

    async def provision_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/provision <user_id>` — провизионить клиентов в bot-managed inbound'ах."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        args = context.args or []
        if not args:
            kb = self._build_user_picker_kb("prov_pick")
            await update.message.reply_text(
                "🛠 <b>Кому провизионить?</b>\n"
                "Выбери из списка или введи вручную: "
                "<code>/provision &lt;id&gt;</code>. "
                "Пакетно: /provision_all.\n\n"
                + self._picker_scope_hint_html(),
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id должен быть числом.")
            return

        enabled = provision_manager.list_enabled_protocols()
        if not enabled:
            await update.message.reply_text(
                "⚠️ На VPS ни один протокол не помечен enabled. "
                "Проверьте /diag и /start."
            )
            return

        results = provision_manager.provision_user(target_id, enabled_protocols=enabled)
        # Новый /provision = сброс счётчика просмотров /my_profile
        # (даём пользователю свежие 3 просмотра).
        try:
            storage_reset_my_profile_views(target_id)
        except Exception as exc:
            logger.warning("reset_my_profile_views(%s) failed: %s", target_id, exc)
        text = self._format_provision_results(
            target_id,
            results,
            header="🛠 <b>Провизионинг для TG-ID:</b>",
            mode="provision",
        )
        text += self._vless_firewall_hint()
        sent_text = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        # 15-мин TTL на text+QR — чтобы рабочие URL/QR не висели в чате
        # бессрочно. То же что в /my_profile, но без счётчика просмотров.
        self._schedule_admin_msg_ttl(sent_text, user_id=target_id)
        # QR на каждый протокол с готовым URI — чтобы admin мог сразу
        # переслать пользователю / показать с экрана.
        await self._send_qrs_for_results(update.message, results, user_id=target_id)

    async def provision_all_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/provision_all` — провизионить всех special + admin одной командой."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        special_ids, _users_data = storage_list_users()
        target_ids = sorted(set(self.config.admin_user_ids) | set(special_ids))
        if not target_ids:
            await update.message.reply_text(
                "В списках admin / special нет ни одного пользователя."
            )
            return
        enabled = provision_manager.list_enabled_protocols()
        if not enabled:
            await update.message.reply_text(
                "⚠️ На VPS ни один протокол не помечен enabled."
            )
            return
        await update.message.reply_text(
            f"🛠 Провизионинг {len(target_ids)} пользователей × "
            f"{len(enabled)} протоколов…"
        )
        summary_lines = ["<b>Готово.</b>", ""]
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
                f"• <code>{tid}</code>: {ok_cnt}/{len(results)} ok ({new_cnt} новых)"
            )
        summary_lines.append("")
        summary_lines.append("Деталь по одному: <code>/profiles &lt;id&gt;</code>")
        if legacy_vless_changed:
            summary_lines.append("")
            summary_lines.append(
                "⚠️ Legacy VLESS изменён. По SSH выполните: "
                "<code>systemctl restart xray</code>"
            )
        text = "\n".join(summary_lines) + self._vless_firewall_hint()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def profiles_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/profiles <user_id>` — показать существующие bot-managed профили."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        args = context.args or []
        if not args:
            kb = self._build_user_picker_kb("prof_pick")
            await update.message.reply_text(
                "📋 <b>Чьи профили показать?</b>\n"
                "Выбери из списка или введи вручную: "
                "<code>/profiles &lt;id&gt;</code>.\n\n"
                + self._picker_scope_hint_html(),
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id должен быть числом.")
            return

        profiles = provision_manager.profiles_for_user(target_id)
        text = self._format_provision_results(
            target_id,
            profiles,
            header="📋 <b>Профили TG-ID:</b>",
            mode="profiles",
        )
        none_existing = not any(p.get("exists") for p in profiles.values())
        if none_existing:
            text += (
                f"\n\nПрофили не созданы. "
                f"Запустите <code>/provision {target_id}</code>."
            )
        sent_text = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        # 15-мин TTL на text+QR. Если профилей нет — сообщение всё равно
        # удалится по таймеру (там нет URL'ов, но за консистентностью).
        self._schedule_admin_msg_ttl(sent_text, user_id=target_id)
        if not none_existing:
            await self._send_qrs_for_results(
                update.message, profiles, user_id=target_id
            )

    async def clean_user_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/clean_user <user_id> YES` — снести bot-managed клиентов TG-ID."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        args = context.args or []
        if not args:
            kb = self._build_user_picker_kb("clean_pick")
            await update.message.reply_text(
                "🗑 <b>Кого очистить?</b>\n"
                "Удалит <b>только</b> bot-managed клиентов с канон-именами "
                "(<code>Vless_ID*_*</code>, <code>Hys_ID*_*</code>, …); "
                "ручные клиенты не трогаются. <b>Клик = подтверждение</b>.\n\n"
                "Текстом тоже работает: "
                "<code>/clean_user &lt;id&gt; YES</code>.\n\n"
                + self._picker_scope_hint_html(),
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id должен быть числом.")
            return
        if len(args) < 2 or args[1].strip().upper() != "YES":
            await update.message.reply_text(
                "❗️ Подтвердите вторым словом <code>YES</code>:\n"
                f"<code>/clean_user {target_id} YES</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        await self._delete_tracked_profile_messages(context.bot, target_id)
        results = provision_manager.clean_user(target_id)
        # При ручном /clean_user сбрасываем состояние /my_profile тоже.
        try:
            storage_reset_my_profile_views(target_id)
            storage_clear_my_profile_messages(target_id)
        except Exception as exc:
            logger.warning("reset my_profile state for %s failed: %s", target_id, exc)
        text = self._format_provision_results(
            target_id,
            results,
            header="🗑 <b>Очистка TG-ID:</b>",
            mode="clean",
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    # === Email delivery of bot-managed profiles ===

    async def setemail_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/setemail <user_id> <email>` — привязать email к TG-ID.

        Email с заблокированных TLD (по умолчанию .ru/.su,
        настраивается через SMTP_BLOCKED_TLDS в .env) отвергается.
        Без второго аргумента — показать или сбросить (если 'clear').
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Использование:\n"
                "<code>/setemail &lt;telegram_user_id&gt; &lt;email&gt;</code>\n"
                "<code>/setemail &lt;telegram_user_id&gt; clear</code> — сбросить\n"
                "<code>/setemail &lt;telegram_user_id&gt;</code> — показать текущий",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id должен быть числом.")
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
                    f"📭 У <code>{target_id}</code> email не задан.",
                    parse_mode=ParseMode.HTML,
                )
            return
        value = args[1].strip()
        if value.lower() == "clear":
            storage_remove_user_email(target_id)
            await update.message.reply_text(
                f"🗑 Email для <code>{target_id}</code> удалён.",
                parse_mode=ParseMode.HTML,
            )
            return
        ok, err = email_manager.validate_email(value)
        if not ok:
            await update.message.reply_text(
                f"❌ Email отвергнут: {html.escape(err)}",
                parse_mode=ParseMode.HTML,
            )
            return
        storage_set_user_email(target_id, value)
        await update.message.reply_text(
            f"✅ Email сохранён для <code>{target_id}</code>: "
            f"<code>{html.escape(email_manager.normalize_email(value))}</code>",
            parse_mode=ParseMode.HTML,
        )

    async def email_profile_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """`/email_profile <user_id>` — отправить bot-managed профили на
        привязанный email пользователя. Перед этим нужен исходящий канал
        (SMTP в .env или `GMAIL_OAUTH_CREDENTIALS`) и `/setemail <uid> <email>`.

        Опционально вторым аргументом можно передать email напрямую,
        не сохраняя его в users.json — `/email_profile <uid> <email>`.
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администратора.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Использование: <code>/email_profile &lt;telegram_user_id&gt; "
                "[email]</code>\n"
                "Без email — берётся из <code>/setemail</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            target_id = int(args[0].strip())
        except (TypeError, ValueError):
            await update.message.reply_text("❌ user_id должен быть числом.")
            return

        if not email_manager.is_configured():
            await update.message.reply_text(
                "⚠️ Исходящая почта не настроена. Задайте либо "
                "<code>GMAIL_OAUTH_CREDENTIALS</code> (JSON Desktop OAuth), "
                "либо SMTP: SMTP_HOST / SMTP_USER / SMTP_PASS "
                "(+ SMTP_PORT, SMTP_USE_TLS, SMTP_FROM) в "
                "<code>.env</code>, затем перезапуск/пересбор контейнера.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Email — из аргумента или из storage.
        if len(args) >= 2:
            to_email = args[1].strip()
        else:
            to_email = storage_get_user_email(target_id) or ""
        if not to_email:
            await update.message.reply_text(
                f"📭 У <code>{target_id}</code> email не привязан.\n"
                f"Привяжите: <code>/setemail {target_id} &lt;email&gt;</code> "
                f"или передайте вторым аргументом: "
                f"<code>/email_profile {target_id} &lt;email&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        ok_v, err_v = email_manager.validate_email(to_email)
        if not ok_v:
            await update.message.reply_text(
                f"❌ Email отвергнут: {html.escape(err_v)}",
                parse_mode=ParseMode.HTML,
            )
            return

        # Собираем bot-managed профили этого пользователя.
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
                f"📭 У <code>{target_id}</code> нет bot-managed профилей.\n"
                f"Сначала: <code>/provision {target_id}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # Генерируем QR-PNG'и для каждого URI.
        try:
            qr_images = {
                proto: email_manager.render_qr_png(p["uri"])
                for proto, p in existing.items()
            }

            await update.message.reply_text(
                f"📤 Отправляю на <code>{html.escape(to_email)}</code>…",
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
                    f"✅ Письмо отправлено на <code>{html.escape(to_email)}</code> "
                    f"({len(existing)} профилей, QR во вложении). "
                    f"Старые сообщения с URL/QR в чате удалены.",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_text(
                    f"❌ Не удалось отправить: {html.escape(msg)}",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as exc:
            logger.exception("email_profile: send failed for uid=%s", target_id)
            await update.message.reply_text(
                f"❌ Ошибка отправки: <code>{html.escape(str(exc))}</code>\n"
                f"<i>См. лог контейнера: docker logs … --tail 80</i>",
                parse_mode=ParseMode.HTML,
            )

    # === /my_profile auto-delete & view-limit support ===

    MY_PROFILE_TTL_SECONDS = 15 * 60  # 15 минут
    MY_PROFILE_VIEW_LIMIT = 3  # 3 успешных просмотра, на 4-й — снос
    USER_CARD_TTL_SECONDS = 3 * 60  # карточка /user у админа — с чата через 3 мин

    async def _delete_message_after_delay(
        self,
        bot,
        chat_id: int,
        message_id: int,
        user_id: int = 0,
        delay_seconds: int = MY_PROFILE_TTL_SECONDS,
    ):
        """Фоновая задача: удалить сообщение через delay_seconds.

        Идемпотентно — если сообщение уже удалено вручную / через cleanup,
        игнорируем ошибку. Если `user_id != 0`, дополнительно убираем
        запись из `users.json[<uid>].my_profile_messages` (нужно для
        self-service flow, чтобы 3-й вызов /my_profile нашёл что удалять).
        Для admin-флоу (provision/profiles) передаём `user_id=0` —
        storage не трогаем.
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
        """Запланировать удаление сообщения через delay_seconds (без storage)."""
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
        """Шорткат для admin-флоу (provision/profiles/picker-callback):
        запланировать авто-удаление сообщения через MY_PROFILE_TTL_SECONDS.
        Если передан user_id, message_id сохраняется в users.json, чтобы
        последующий /clean_user или /my_profile мог снести ссылку/QR даже
        после перезапуска контейнера."""
        if user_id:
            self._track_and_schedule_delete(msg, user_id)
            return
        self._schedule_message_ttl(
            msg, delay_seconds=self.MY_PROFILE_TTL_SECONDS, user_id=0
        )

    def _track_and_schedule_delete(self, sent_msg, user_id: int):
        """Записать message_id в storage и завести фоновую задачу авто-удаления."""
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
        """Удалить все записанные URL/QR сообщения для user_id и очистить storage."""
        prev_msgs = storage_get_my_profile_messages(user_id)
        for m in prev_msgs:
            try:
                await bot.delete_message(m["chat_id"], m["message_id"])
            except Exception as exc:
                logger.debug("tracked profile message delete failed: %s", exc)
        storage_clear_my_profile_messages(user_id)

    async def _purge_all_profile_messages(self, bot) -> int:
        """Снести ВСЕ ранее отправленные URL/QR сообщения у всех пользователей.

        Вызывается после смены параметров, попадающих в ссылку (порт, SNI,
        fingerprint, short_id, сервер, Reality-ключи, UUID): старые vless://
        ссылки и QR становятся невалидными и не должны больше показываться
        никому — ни админу, ни пользователям. Свежие ссылки/QR бот
        перегенерирует из обновлённого конфига при следующем /profiles или
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
                # Сообщение могло быть уже удалено/старше 48ч (лимит Bot API) —
                # это не ошибка, всё равно чистим трекинг ниже.
                logger.debug("purge profile message delete failed: %s", exc)
        storage_clear_all_my_profile_messages()
        if entries:
            logger.info(
                "Purged stale profile links/QR after config change: "
                "%s удалено из %s отслеживаемых",
                deleted,
                len(entries),
            )
        return deleted

    async def _notify_admins(self, context, text: str):
        """Разослать text каждому admin'у. Молча игнорируем недоступных."""
        for admin_id in self.config.admin_user_ids or []:
            try:
                await context.bot.send_message(
                    int(admin_id), text, parse_mode=ParseMode.HTML
                )
            except Exception as exc:
                logger.warning("notify admin %s failed: %s", admin_id, exc)

    async def _auto_cleanup_my_profile(self, context, update: Update, user_id: int):
        """Достигнут лимит просмотров — снести URL/QR из чата и клиентов
        с панелей, сбросить счётчик, нотифицировать админов."""
        # 1. Удаляем все ранее отправленные сообщения с URL/QR.
        await self._delete_tracked_profile_messages(context.bot, user_id)

        # 2. Сносим клиентов из всех bot-managed inbound'ов.
        try:
            cleanup_results = provision_manager.clean_user(user_id)
        except Exception as exc:
            logger.warning("provision_manager.clean_user(%s) failed: %s", user_id, exc)
            cleanup_results = {}

        # 3. Сбрасываем счётчик — после нового /provision дадим ещё 2.
        storage_reset_my_profile_views(user_id)

        # 4. Сообщение пользователю.
        await update.effective_message.reply_text(
            f"🚫 <b>Лимит {self.MY_PROFILE_VIEW_LIMIT} просмотров /my_profile исчерпан.</b>\n\n"
            f"Все ваши bot-managed профили удалены с VPS.\n"
            f"Чтобы получить новые ссылки, попросите админа выполнить:\n"
            f"<code>/provision {user_id}</code>",
            parse_mode=ParseMode.HTML,
        )

        # 5. Нотификация админам.
        notif = [
            f"🗑 <b>Auto-cleanup профилей</b>",
            f"Пользователь: <code>{user_id}</code>",
            f"Причина: исчерпан лимит {self.MY_PROFILE_VIEW_LIMIT} просмотров /my_profile.",
            "",
        ]
        if cleanup_results:
            for proto, r in cleanup_results.items():
                emoji = "✅" if r.get("removed") else ("⚪" if r.get("ok") else "❌")
                name = html.escape(str(r.get("client_name") or ""))
                if r.get("removed"):
                    label = "удалён"
                elif r.get("ok"):
                    label = "не было"
                else:
                    label = f"ошибка: {html.escape(str(r.get('message') or ''))}"
                notif.append(f"{emoji} <b>{proto}</b>: <code>{name}</code> ({label})")
        else:
            notif.append("⚠️ Список протоколов пуст — clean_user не отработал.")
        notif.append("")
        notif.append(f"Чтобы выдать снова: <code>/provision {user_id}</code>")
        await self._notify_admins(context, "\n".join(notif))

    async def my_profile_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """
        Команда /my_profile — special-пользователь получает свои URL + QR.

        Сначала ищем bot-managed канонизированные профили
        (`<Prefix>_ID<first2>_<last2>` от Telegram-ID, см. provision_manager) —
        они появляются после `/provision <uid>` админа. Если их нет —
        fallback на legacy-lookup (`/user`-flow + интеграция 3x-ui).

        Защита: сообщения с URL+QR авто-удаляются через 15 минут.
        Лимит 3 успешных просмотра — на 4-м клиенты сносятся и из чата,
        и с панели 3x-ui (см. `_auto_cleanup_my_profile`).
        """
        try:
            user = update.effective_user
            msg = update.effective_message
            if not self._is_privileged(user.id):
                await msg.reply_text(
                    "⛔ Эта команда доступна только администратору или special-пользователю."
                )
                return
            uid = int(user.id)

            # Сначала — bot-managed канонизированные профили (после /provision).
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
                    # Лимит просмотров применяется только к special-пользователям.
                    # Админ может проверять свои профили без счётчика и auto-cleanup.
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
                    f"🔐 <b>Ваши профили (user_id={uid}):</b>",
                    (
                        "<i>Админ-доступ: без лимита просмотров. "
                        "Сообщения авто-удалятся через 15 мин.</i>"
                        if is_admin
                        else f"<i>Просмотр {already_viewed + 1} из "
                        f"{self.MY_PROFILE_VIEW_LIMIT}. Сообщения авто-удалятся "
                        f"через 15 мин.</i>"
                    ),
                    "",
                    "📋 <b>Тапни на URL ниже чтобы скопировать в буфер.</b> "
                    "QR — для скана камерой клиента.",
                    "",
                ]
                for proto, p in existing_bot.items():
                    label = proto_labels.get(proto, proto)
                    summary_lines.append(
                        f"{label} — <code>{html.escape(p['client_name'])}</code>"
                    )
                summary_lines.append("")
                summary_lines.append(
                    "📲 Ниже на каждый протокол: одно сообщение с URL "
                    "(тап = копировать) + QR картинкой."
                )
                sent = await msg.reply_text(
                    "\n".join(summary_lines).strip(), parse_mode=ParseMode.HTML
                )
                self._track_and_schedule_delete(sent, uid)
                # На каждый протокол: одно URL-only сообщение целиком из
                # <code> (весь bubble — tap-target, удобно скопировать), и
                # отдельным сообщением QR картинка.
                for proto, p in existing_bot.items():
                    label = proto_labels.get(proto, proto)
                    uri = str(p.get("uri") or "")
                    client_name = str(p.get("client_name") or proto)
                    usage_hint = ""
                    if proto == "vless":
                        usage_hint = "\n<i>Современная ссылка для v2rayN / sing-box / Clash Meta.</i>"
                    elif proto == "hysteria2":
                        usage_hint = "\n<i>Основная ссылка hy2:// для Karing и совместимых клиентов.</i>"
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
                                "<i>Полная схема hysteria2:// для Karing и клиентов, "
                                "которые не принимают hy2://.</i>\n"
                                f"<code>{html.escape(hysteria2_alias)}</code>",
                                parse_mode=ParseMode.HTML,
                            )
                            self._track_and_schedule_delete(alias_msg, uid)
                    qr_msg = await self._reply_qr_for_link(msg, uri, client_name)
                    self._track_and_schedule_delete(qr_msg, uid)
                if not is_admin:
                    # Засчитываем успешный просмотр только после реальной отправки.
                    storage_inc_my_profile_views(uid)
                return

            # Bot-managed нет — пробуем legacy.
            found = self._build_user_protocol_profile_lookup(uid)
            if not found:
                # Админ ≠ автоматически есть VPN-клиент: /my_profile ищет
                # профили после /provision, а не проверяет роль.
                if self._is_admin(uid):
                    hint = (
                        "📭 У вас пока нет VPN-профиля "
                        f"(user_id=<code>{uid}</code>).\n\n"
                        "✅ Права админа в порядке — это не про доступ к боту.\n"
                        "Создайте клиенты себе командой:\n"
                        f"<code>/provision {uid}</code>\n\n"
                        "После этого снова откройте /my_profile — появятся "
                        "VLESS / Hysteria2 / и т.д. с QR-кодами.\n"
                        "Проверка панели: /xui_status"
                    )
                else:
                    hint = (
                        "📭 Для вас пока нет готового профиля "
                        f"(user_id=<code>{uid}</code>).\n\n"
                        "Вы в списке special — доступ к /my_profile есть, "
                        "но VPN-клиенты ещё не созданы.\n"
                        "Попросите админа выполнить:\n"
                        f"<code>/provision {uid}</code>\n\n"
                        "После этого снова откройте /my_profile."
                    )
                await msg.reply_text(hint, parse_mode=ParseMode.HTML)
                return

            lines = [f"🔐 <b>Ваши профили (user_id={html.escape(str(uid))}):</b>", ""]
            v_source = ""
            if "vless_reality" in found:
                item = found["vless_reality"]
                src = item.get("source") or ""
                v_source = src
                if src == "xui":
                    src_label = "из 3x-ui (рабочая)"
                elif src == "vless_config_test":
                    src_label = "legacy vless_config.json (тестовая/неактивная)"
                else:
                    src_label = "из vless_config.json бота"
                lines.append(f"🛡 <b>VLESS-Reality</b> ({html.escape(src_label)})")
                lines.append(f"Имя: <code>{html.escape(str(item['name']))}</code>")
                if item.get("url"):
                    vless_url = str(item["url"])
                    lines.append(f"URL: <code>{html.escape(vless_url)}</code>")
                else:
                    lines.append(
                        f"Статус: {html.escape(str(item.get('message') or 'неактивна'))}"
                    )
                lines.append("")
            if "hysteria2" in found:
                item = found["hysteria2"]
                lines.append("⚡ <b>Hysteria2</b>")
                lines.append(f"Имя: <code>{html.escape(str(item['name']))}</code>")
                hy2_url = str(item["url"])
                lines.append(
                    "URL (Karing / совместимые): "
                    f"<code>{html.escape(hy2_url)}</code>"
                )
                hysteria2_alias = hysteria2_manager.to_hysteria2_uri(hy2_url)
                if hysteria2_alias and hysteria2_alias != hy2_url:
                    lines.append(
                        "Alias для Karing/full scheme: "
                        f"<code>{html.escape(hysteria2_alias)}</code>"
                    )
                lines.append("")

            if v_source in ("vless_config", "vless_config_test"):
                lines.append(
                    "⚠️ <i>Эта VLESS-ссылка собрана из локального JSON бота. "
                    "Если VLESS на VPS обслуживает 3x-ui, а не ботовый "
                    "xray.service, ссылка не подойдёт. Попросите админа "
                    f"выполнить /xui_setup и выдать профиль из /user {html.escape(str(uid))}.</i>"
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
                "Ошибка при получении вашего профиля."
            )

    async def _reply_qr_for_link(self, message, link: str, name: str):
        """Отправить QR-картинку по готовой ссылке. Возвращает Message или None."""
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
                caption=f"📲 QR для {name}\nЕсли скан не импортируется — скопируйте URL сообщением выше.",
            )
        except Exception as exc:
            logger.warning("_reply_qr_for_link(%s): %s", name, exc)
            return None

    async def _handle_list_users_callbacks(self, query, data: str) -> bool:
        """Inline-кнопки под /list_users: журнал и special. Только админ (как весь callback)."""
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
                await query.message.edit_text("Готово.", reply_markup=None)
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
                        "⛔ Заменять профиль можно только для special-пользователя.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "◀️ Назад", callback_data=f"lu_pru:{uid}:{page}"
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "✖️ Закрыть", callback_data="lu_x"
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
            # "Message is not modified" — Telegram-API возвращает 400 когда
            # `edit_text` получает идентичный контент (типичный кейс — клик
            # на кнопку, которая просто refresh'ит тот же экран). Это не
            # баг, не шумим: молча ack'аем callback и возвращаем True.
            if "not modified" in err_text.lower():
                try:
                    await query.answer()
                except Exception:
                    pass
                return True
            logger.exception("list_users inline callback %r: %s", data, e)
            # Показываем реальную причину прямо в чате (admin'у виднее
            # чем generic "Ошибка при обработке кнопки"). Тип ошибки
            # + сокращённое сообщение, чтобы не утечь стектрейсом.
            err_kind = type(e).__name__
            err_msg = err_text[:200] if err_text else "(пусто)"
            try:
                await query.message.reply_text(
                    f"❌ Ошибка при обработке кнопки.\n"
                    f"<code>{html.escape(err_kind)}</code>: "
                    f"<code>{html.escape(err_msg)}</code>\n\n"
                    f"<i>Полный стектрейс — в логах "
                    f"<code>docker compose logs telegram-helper</code>.</i>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                # На крайний случай — старое сообщение, чтобы не молчать.
                try:
                    await query.message.reply_text("Ошибка при обработке кнопки.")
                except Exception:
                    pass
            return True
        return False

    async def _deliver_users_log(self, user, message) -> None:
        """Отправить журнал пользователей (текст или .txt). Доступ: privileged."""
        if not self._is_privileged(user.id):
            await message.reply_text(
                "⛔ Эта команда доступна только администратору или special-пользователю."
            )
            return

        self._track_user(user)

        special, users = storage_list_users()
        if not users:
            await message.reply_text(
                "📒 Журнал пользователей пуст. Записи появятся после первого обращения к боту."
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

        header = f"📒 Журнал пользователей: {len(sorted_items)} (новые сверху)"
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
                f"   первый: {first_seen} UTC · последний: {last_seen} UTC"
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
                caption=f"Журнал пользователей: {len(sorted_items)}",
            )

    async def users_log_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /users_log — журнал пользователей бота с датой первого обращения.

        Доступ: администратор или special-пользователь.
        Источник данных: storage.track_user(...), который пишет first_seen один раз
        (через setdefault) и обновляет last_seen при каждом обращении. Дубликаты не
        создаются: для существующего пользователя first_seen остаётся прежним.
        """
        try:
            user = update.effective_user
            await self._deliver_users_log(user, update.message)
        except Exception as e:
            logger.error(f"Error in users_log_command: {e}")
            await update.message.reply_text(
                "Ошибка при формировании журнала пользователей."
            )

    # === НАСТРОЙКИ ИИ ===

    async def ai_set_provider(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /ai_provider - выбрать провайдера ИИ."""
        user = update.effective_user
        if not self._is_admin(user.id):
            await update.message.reply_text(
                "⛔ Эта команда доступна только администратору."
            )
            return

        args = context.args or []
        if len(args) != 1 or args[0].lower() not in ["openai", "anthropic"]:
            await update.message.reply_text(
                "Использование: /ai_provider <openai|anthropic>"
            )
            return

        provider = args[0].lower()
        os.environ["DEFAULT_AI_PROVIDER"] = provider

        emoji = "🔵" if provider == "openai" else "🟣"
        await update.message.reply_text(f"{emoji} Провайдер ИИ установлен: {provider}")

    async def ch_model_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /ch_model - переключить AI модель."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
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
                "⚙️ Выберите провайдера для настройки модели:", reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Error in ch_model_command: {e}")
            await update.message.reply_text("Ошибка при выполнении команды.")

    # === КОМАНДЫ VLESS-REALITY ===

    async def vless_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_status - показать статус VLESS-Reality.

        Если настроена интеграция с 3x-ui (`/xui_setup`), сначала
        отправляется блок с реальным состоянием панели и bot-managed
        inbound'а, и только потом — legacy-блок из локального
        `vless_config.json` (с пометкой что он не используется).
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested VLESS status")

            if self._xui_is_active():
                await self._send_vless_xui_overview(update, intent="status")

            status = vless_manager.get_vless_status()

            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            # Экранируем спецсимволы для Markdown V2
            def escape_md2(text):
                if not text:
                    return "не настроен"
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
            updated_at = escape_md2(status.get("updated_at", "никогда"))

            message = f"""🛡️ *VLESS\\-Reality Статус*

*Состояние:* {status_emoji} {"Включён" if status["enabled"] else "Выключен"}
*Конфигурация:* {config_emoji} {"Настроена" if status["configured"] else "Не настроена"}

*Параметры:*
• Сервер: `{server}`
• Порт: `{port}`
• SNI: `{sni}`
• Fingerprint: `{fingerprint}`

*Ключи:*
• UUID: {"✅" if status["has_uuid"] else "❌"}
• Public Key: {"✅" if status["has_public_key"] else "❌"}
• Private Key: {"✅" if status["has_private_key"] else "❌"}
• Short ID: {"✅" if status["has_short_id"] else "❌"}

*Источник:* legacy `xray.service` \\+ `/usr/local/etc/xray/config.json`
_Панель 3x\\-ui здесь не используется\\. Для текущих ссылок: /vless\\_qr, /vless\\_export\\._

_Обновлено: {updated_at}_"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in vless_status: {e}")
            await update.message.reply_text("Ошибка при получении статуса VLESS.")

    async def vless_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_on - включить VLESS-Reality."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "service"):
                return
            logger.info(f"Admin {user.id} enabling VLESS-Reality")

            success, message = vless_manager.enable_vless()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_on: {e}")
            await update.message.reply_text("Ошибка при включении VLESS.")

    async def vless_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_off - выключить VLESS-Reality."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "service"):
                return
            logger.info(f"Admin {user.id} disabling VLESS-Reality")

            success, message = vless_manager.disable_vless()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_off: {e}")
            await update.message.reply_text("Ошибка при выключении VLESS.")

    async def vless_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_config - показать и сохранить конфигурацию VLESS в файлы."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested VLESS config")

            config = vless_manager.get_vless_config(include_secrets=False)

            # Сохраняем конфиги в файлы
            success, save_msg, created_files = vless_manager.save_vless_config_files()

            # Формируем список сохранённых файлов
            files_list = ""
            if created_files:
                files_list = "\n\n📁 *Сохранённые файлы:*\n"
                for f in created_files:
                    # Показываем только имя файла без полного пути
                    fname = os.path.basename(f)
                    files_list += f"• `{fname}`\n"

            # Escape для Markdown V2
            save_msg_escaped = escape_markdown(save_msg)

            message = f"""🔧 *Конфигурация VLESS\\-Reality*

```json
{json.dumps(config, indent=2, ensure_ascii=False)}
```

{save_msg_escaped}{files_list}
💡 Секретные данные скрыты\\. Для полной конфигурации используйте /vless\\_export"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in vless_config: {e}")
            await update.message.reply_text("Ошибка при получении конфигурации VLESS.")

    async def vless_set_server(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /vless_set_server - установить адрес сервера (автоопределение если без аргументов)."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []

            # Если аргумент указан - используем его, иначе автоопределение
            if len(args) >= 1:
                server = args[0]
            else:
                await update.message.reply_text("🔍 Определяю IP сервера...")
                server = None  # Автоопределение

            success, message = vless_manager.set_vless_server(server)
            if success:
                message += (
                    self._legacy_vless_reexport_text()
                    + "\n\nℹ️ Restart Xray не нужен — меняется только адрес в URI."
                )
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_set_server: {e}")
            await update.message.reply_text("Ошибка при установке сервера.")

    async def vless_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_set_port - установить порт."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text("Использование: /vless_set_port <port>")
                return

            try:
                port = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ Порт должен быть числом")
                return

            success, message = vless_manager.set_vless_port(port)
            await update.message.reply_text(message)

            if success:
                # Автоприменение к реальному Xray-конфигу (legacy host-Xray
                # flow) — без этого xray.service продолжит слушать старый порт,
                # а новые QR/ссылки будут указывать на порт, который сервер ещё не слушает.
                await self._legacy_vless_apply_followup(update, port=port)

        except Exception as e:
            logger.error(f"Error in vless_set_port: {e}")
            await update.message.reply_text("Ошибка при установке порта.")

    async def vless_add_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /vless_add_client - добавить клиента VLESS."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "client"):
                return
            args = context.args or []
            if len(args) < 1:
                await update.message.reply_text(
                    "Использование: /vless_add_client <name> [uuid]"
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
            await update.message.reply_text("Ошибка при добавлении клиента.")

    async def vless_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_qr - показать QR для VLESS-клиента."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ QR-коды VLESS доступны только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "client"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Использование: /vless_qr <client_name_or_uuid>"
                )
                return

            await self._reply_vless_qr(update.message, args[0])

        except Exception as e:
            logger.error(f"Error in vless_qr: {e}")
            await update.message.reply_text("Ошибка при показе QR клиента.")

    async def vless_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /vless_list_clients - список клиентов VLESS.

        Когда `/xui_setup` настроен — показывает клиентов **bot-managed
        inbound** в 3x-ui (созданных через /provision). Manual inbound
        с ручными клиентами не трогает (на скрине это `195_Vless` —
        админ его правит сам в панели).
        Когда нет — список из локального `vless_config.json` (legacy).
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if self._xui_is_active():
                cfg = xui_manager.load_config()
                default_id = int(cfg.get("default_inbound_id") or 0)
                if not default_id:
                    await update.message.reply_text(
                        "ℹ️ default_inbound_id не задан в /xui_setup.\n"
                        "Без него бот не знает, в какой inbound смотреть.",
                        parse_mode=ParseMode.HTML,
                    )
                    return
                xclient = xui_manager.make_client_for_config(cfg)
                if xclient is None:
                    await update.message.reply_text(
                        "❌ Не удалось восстановить XUIClient (см. /xui_status)."
                    )
                    return
                ok_l, msg_l = xclient.login()
                if not ok_l:
                    await update.message.reply_text(
                        f"❌ Login в 3x-ui: {html.escape(msg_l)}",
                        parse_mode=ParseMode.HTML,
                    )
                    return
                ok_g, msg_g, inbound = xclient.get_inbound(default_id)
                if not ok_g or not inbound:
                    await update.message.reply_text(
                        f"❌ Не удалось получить default inbound #{default_id}: "
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
                # Фильтруем по canonical-паттерну — manual клиенты не
                # показываем как «bot-managed» (их видно только в панели).
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
                    f"🛡 <b>Bot-managed VLESS клиенты</b> "
                    f"(inbound #{default_id} — "
                    f"<code>{html.escape(str(inbound_remark))}</code>, "
                    f"порт {inbound_port}):",
                    "",
                ]
                if not bot_clients:
                    lines.append(
                        "Bot-managed клиентов нет. "
                        "<code>/provision &lt;id&gt;</code> — добавить."
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
                    f"<i>Кроме bot-managed, в этом же inbound живут "
                    f"<b>{manual_count}</b> ручных клиентов админа — "
                    f"их бот не трогает (правится напрямую в панели).</i>"
                )
                await update.message.reply_text(
                    "\n".join(lines), parse_mode=ParseMode.HTML
                )
                return

            # Legacy fallback
            clients = vless_manager.list_clients()
            if not clients:
                await update.message.reply_text("Список клиентов пуст.")
                return

            lines = [
                "*VLESS клиенты \\(legacy Xray, без 3x\\-ui\\):*",
                "_Источник: `vless_config.json` \\+ `/usr/local/etc/xray/config.json`\\._",
                "_Для QR/URI используйте `/vless\\_qr <name>` или `/vless\\_export`\\._",
                "",
            ]
            for client in clients:
                name = escape_markdown(str(client.get("name", "client")))
                uuid = escape_markdown(str(client.get("uuid", "")))
                lines.append(f"• {name}: `{uuid}`")

            await self._reply_md2_safe(update.message, "\n".join(lines))

        except Exception as e:
            logger.error(f"Error in vless_list_clients: {e}")
            await update.message.reply_text("Ошибка при получении списка клиентов.")

    async def vless_del_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /vless_del_client - удалить клиента VLESS."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "client"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Использование: /vless_del_client <name_or_uuid>"
                )
                return

            success, message = vless_manager.remove_client(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_del_client: {e}")
            await update.message.reply_text("Ошибка при удалении клиента.")

    async def vless_set_uuid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_set_uuid - установить UUID."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text("Использование: /vless_set_uuid <uuid>")
                return

            success, message = vless_manager.set_vless_uuid(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_uuid: {e}")
            await update.message.reply_text("Ошибка при установке UUID.")

    async def vless_set_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_set_key - установить публичный ключ Reality."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Использование: /vless_set_key <public_key>"
                )
                return

            success, message = vless_manager.set_vless_public_key(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_key: {e}")
            await update.message.reply_text("Ошибка при установке ключа.")

    async def vless_set_shortid(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /vless_set_shortid - установить Short ID."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Использование: /vless_set_shortid <hex_string>"
                )
                return

            success, message = vless_manager.set_vless_short_id(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_shortid: {e}")
            await update.message.reply_text("Ошибка при установке Short ID.")

    async def vless_set_sni(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_set_sni - установить SNI для маскировки."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                # Без аргумента — показываем интерактивный список кнопок с
                # готовыми SNI: одно нажатие меняет домен, применяет конфиг и
                # предлагает перезапустить Xray. Это «простая команда с
                # подсказками» — не нужно помнить домены наизусть.
                await self._show_vless_sni_picker(update.message)
                return

            success, message = vless_manager.set_vless_sni(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_sni: {e}")
            await update.message.reply_text("Ошибка при установке SNI.")

    async def _show_vless_sni_picker(self, message) -> None:
        """Показать кнопки выбора SNI (текущий помечен ✅) с подсказкой."""
        try:
            current = (vless_manager.get_vless_status() or {}).get("sni", "")
        except Exception:
            current = ""

        # Явные «человеческие» названия рядом с доменом — чтобы в списке кнопок
        # было понятно, что за сайт маскируем (Cloudflare, Apple и т.д.), а не
        # только голый домен.
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
            "🌐 *Выбор SNI \\(маскировочный домен Reality\\)*\n\n"
            f"Текущий: `{escape_markdown(current or '—')}`\n\n"
            "Нажмите домен — бот сменит SNI, перезапишет конфиг Xray и предложит "
            "перезапуск\\.\n\n"
            "💡 Единого верного SNI нет: мобильные операторы \\(особенно в РФ\\) чаще "
            "всего режут `www\\.microsoft\\.com`\\. Если не подключается при рабочих "
            "сети/порте/ключах — начните с `yahoo\\.com`\\.\n\n"
            "Свой домен: `/vless_set_sni example\\.com`"
        )
        await self._reply_md2_safe(
            message, hint, reply_markup=InlineKeyboardMarkup(rows)
        )

    async def vless_set_fingerprint(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /vless_set_fingerprint - установить TLS fingerprint."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            args = context.args or []
            if len(args) != 1:
                fp_list = ", ".join(vless_manager.AVAILABLE_FINGERPRINTS)
                await update.message.reply_text(
                    f"Использование: /vless_set_fingerprint <fingerprint>\n\nДоступные: {fp_list}"
                )
                return

            success, message = vless_manager.set_vless_fingerprint(args[0])
            await update.message.reply_text(message)
            if success:
                await self._legacy_vless_apply_followup(update)

        except Exception as e:
            logger.error(f"Error in vless_set_fingerprint: {e}")
            await update.message.reply_text("Ошибка при установке fingerprint.")

    async def vless_gen_keys(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_gen_keys - сгенерировать все ключи VLESS-Reality."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            logger.info(f"Admin {user.id} generating VLESS keys")

            await update.message.reply_text("⏳ Генерация ключей...")

            success, keys, message = vless_manager.generate_all_keys()

            if success:
                uuid_escaped = escape_markdown(keys.get("uuid", ""))
                pk_escaped = escape_markdown(keys.get("public_key", ""))
                sid_escaped = escape_markdown(keys.get("short_id", ""))
                response = f"""{message}

🔑 *Сгенерированные ключи:*

*UUID:*
`{uuid_escaped}`

*Public Key:*
`{pk_escaped}`

*Short ID:*
`{sid_escaped}`

⚠️ *Важно:*
• Private Key сохранён только на сервере и не отправляется в Telegram
• Public Key и Short ID нужны для клиента
• UUID должен совпадать на сервере и клиенте"""

                await self._reply_md2_safe(update.message, response)

                # Автоматически применяем новые ключи к реальному Xray-конфигу
                # (legacy host-Xray flow). Без этого шага QR/ссылки клиента
                # содержат новые ключи, а xray.service продолжает слушать старый
                # (или пустой) конфиг — Reality-хендшейк не проходит.
                await self._legacy_vless_apply_followup(update)
            else:
                await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_gen_keys: {e}")
            await update.message.reply_text("Ошибка при генерации ключей.")

    async def vless_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_test - тест подключения к серверу."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "service"):
                return
            logger.info(f"Admin {user.id} testing VLESS connection")

            await update.message.reply_text("⏳ Тестирование подключения...")

            success, message = vless_manager.test_connection()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in vless_test: {e}")
            await update.message.reply_text("Ошибка при тестировании подключения.")

    async def vless_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_export — admin-only.

        Поведение зависит от 3x-ui-интеграции:
        - **xui активен** → отправляется ТОЛЬКО overview-блок, который
          указывает рабочие команды `/provision`, `/profiles`,
          `/email_profile`. Legacy JSON-дамп локального
          `vless_config.json` и вся пачка из 8 inline-кнопок не
          показывается — это путало.
        - **xui НЕ активен** (bare host-Xray VPS) → старый legacy
          экспорт со всеми форматами (он полезен в этой конфигурации).
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} exporting VLESS config")

            if self._xui_is_active():
                # На VPS с 3x-ui реальный source клиентских профилей —
                # через /provision/profiles/email_profile. Legacy JSON
                # больше не показываем (он от локального xray.service,
                # которого здесь нет).
                await self._send_vless_xui_overview(update, intent="export")
                return

            # Клиентская конфигурация
            client_config = vless_manager.export_client_config()

            # Xray конфигурации
            xray_client = vless_manager.export_xray_config(is_server=False)
            xray_server = vless_manager.export_xray_config(is_server=True)

            # Generate VLESS link
            vless_link = vless_manager.generate_vless_link()
            vless_link_escaped = escape_markdown(vless_link)

            message = f"""📤 *Экспорт конфигурации VLESS\\-Reality*

*Конфигурация клиента:*
```json
{json.dumps(client_config, indent=2)}
```

🔗 *Ссылка для Hiddify / Foxray / v2rayNG:*
`{vless_link_escaped}`

Для полной конфигурации Xray используйте команды ниже\\."""

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
                        "📷 QR по клиенту", callback_data="vless_export_qr_menu"
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
            await update.message.reply_text("Ошибка при экспорте конфигурации.")

    async def vless_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_sync - автонастройка и экспорт для VPN-клиента."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "service"):
                return
            logger.info(f"Admin {user.id} syncing VLESS config for client")

            # Сначала синхронизируем ключи из xray config (если xray установлен и работает)
            # Это гарантирует что public_key в vless_config.json соответствует privateKey в xray
            sync_success, sync_msg = vless_manager.sync_from_xray_config()
            if sync_success and "Синхронизировано" in sync_msg:
                await update.message.reply_text(
                    "🔄 " + sync_msg.replace("`", ""), parse_mode=None
                )

            # Получаем текущую конфигурацию
            config = vless_manager.get_vless_config(include_secrets=True)

            auto_configured = False

            # Если сервер не настроен - автоопределение
            if not config.get("server") or "..." in str(config.get("server", "")):
                await update.message.reply_text("🔍 Определяю IP сервера...")
                success, msg = vless_manager.set_vless_server(None)  # Автоопределение
                if not success:
                    await update.message.reply_text(msg)
                    return
                await update.message.reply_text(msg)
                auto_configured = True
                config = vless_manager.get_vless_config(include_secrets=True)

            # Если ключи не сгенерированы - генерируем
            if not config.get("uuid") or "..." in str(config.get("uuid", "")):
                await update.message.reply_text("🔑 Генерирую ключи...")
                success, keys, msg = vless_manager.generate_all_keys()
                if not success:
                    await update.message.reply_text(msg)
                    return
                await update.message.reply_text(msg)
                auto_configured = True
                config = vless_manager.get_vless_config(include_secrets=True)

            # Получаем полную конфигурацию для экспорта
            full_config = vless_manager.export_client_config()

            # Escaping для Markdown
            server_escaped = escape_markdown(full_config["server"])
            uuid_escaped = escape_markdown(full_config["uuid"])
            pk_escaped = escape_markdown(full_config["public_key"])
            sid_escaped = escape_markdown(full_config["short_id"])

            if auto_configured:
                header = "✅ *VLESS\\-Reality настроен автоматически\\!*"
            else:
                header = "🔄 *VLESS\\-Reality для sing-box*"

            # Generate VLESS link
            vless_link = vless_manager.generate_vless_link()
            vless_link_escaped = escape_markdown(vless_link)

            message = f"""{header}

*Скопируйте эти значения в Settings → Reality:*

📍 *Server:* `{server_escaped}`
🔌 *Port:* `{full_config["port"]}`
🆔 *UUID:* `{uuid_escaped}`
🔑 *Public Key:* `{pk_escaped}`
🏷️ *Short ID:* `{sid_escaped}`
🌐 *SNI:* `{full_config["sni"]}`
🎭 *Fingerprint:* `{full_config["fingerprint"]}`

🔗 *Ссылка для Hiddify / Foxray / v2rayNG:*
`{vless_link_escaped}`

💡 _Откройте sing-box → Settings → VLESS\\-Reality → Configure Reality_"""

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in vless_sync: {e}")
            await update.message.reply_text("Ошибка при синхронизации конфигурации.")

    async def vless_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /vless_reset - сбросить конфигурацию VLESS."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if await self._legacy_vless_guard(update, "config"):
                return
            logger.info(f"Admin {user.id} resetting VLESS config")

            # Запрашиваем подтверждение
            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Да, сбросить", callback_data="vless_reset_confirm"
                    ),
                    InlineKeyboardButton(
                        "❌ Отмена", callback_data="vless_reset_cancel"
                    ),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "⚠️ *Вы уверены, что хотите сбросить конфигурацию VLESS\\-Reality?*\n\n"
                "Все настройки и ключи будут удалены\\!",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup,
            )

        except Exception as e:
            logger.error(f"Error in vless_reset: {e}")
            await update.message.reply_text("Ошибка при сбросе конфигурации.")

    # === XRAY MANAGEMENT COMMANDS ===

    async def xray_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xray_status - проверить статус Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} checking Xray status")

            installed, message, info = vless_manager.check_xray_installed()

            if not installed:
                message += "\n\n💡 Для установки: /xray\\_install"

            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in xray_status: {e}")
            await update.message.reply_text("Ошибка при проверке статуса Xray.")

    async def xray_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xray_config - показать конфигурацию Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} viewing Xray config")

            success, message, config = vless_manager.get_xray_config()
            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in xray_config: {e}")
            await update.message.reply_text("Ошибка при получении конфигурации Xray.")

    async def xray_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xray_install - установить Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} installing Xray")

            await update.message.reply_text(
                "⏳ Устанавливаю Xray... (может занять 1-2 минуты)"
            )

            success, message = vless_manager.install_xray()
            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in xray_install: {e}")
            await update.message.reply_text("Ошибка при установке Xray.")

    async def xray_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xray_apply - применить VLESS конфигурацию к Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} applying Xray config")

            success, message = vless_manager.apply_xray_config()
            await self._reply_md2_safe(update.message, message)
            if success:
                # Plain text: в MD2 ломаются systemctl / подчёркивания.
                await update.message.reply_text(self._legacy_vless_host_restart_text())

        except Exception as e:
            logger.error(f"Error in xray_apply: {e}")
            await update.message.reply_text("Ошибка при применении конфигурации.")

    async def xray_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xray_start - запустить Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} starting Xray")

            success, message = vless_manager.start_xray()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in xray_start: {e}")
            await update.message.reply_text("Ошибка при запуске Xray.")

    async def xray_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xray_stop - остановить Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} stopping Xray")

            success, message = vless_manager.stop_xray()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in xray_stop: {e}")
            await update.message.reply_text("Ошибка при остановке Xray.")

    async def xray_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xray_restart - перезапустить Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} restarting Xray")

            await update.message.reply_text("⏳ Перезапускаю Xray...")

            success, message = vless_manager.restart_xray()
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in xray_restart: {e}")
            await update.message.reply_text("Ошибка при перезапуске Xray.")

    async def xray_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xray_logs - показать логи Xray."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} viewing Xray logs")

            # Парсим количество строк из аргументов
            args = context.args or []
            lines = 30
            if args and args[0].isdigit():
                lines = min(int(args[0]), 100)  # Максимум 100 строк

            success, message = vless_manager.get_xray_logs(lines)
            await self._reply_md2_safe(update.message, message)

        except Exception as e:
            logger.error(f"Error in xray_logs: {e}")
            await update.message.reply_text("Ошибка при получении логов.")

    # === NGINX SNI ROUTING COMMANDS ===

    async def nginx_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /nginx_status - статус Nginx SNI fallback."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            config = vless_manager._load_config()
            enabled = config.get("nginx_fallback_enabled", False)
            port = config.get("nginx_fallback_port", 8443)
            hs_domain = config.get("headscale_domain", "")
            ha_domain = config.get("ha_domain", "")

            status_emoji = "🟢" if enabled else "🔴"
            lines = [
                f"{status_emoji} *Nginx SNI Fallback*: {'включён' if enabled else 'выключен'}",
                f"📍 *Порт*: `{port}`",
                f"🌐 *Headscale домен*: `{escape_markdown(hs_domain or 'не задан')}`",
            ]
            if ha_domain:
                lines.append(
                    f"🏠 *Home Assistant домен*: `{escape_markdown(ha_domain)}`"
                )

            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in nginx_status: {e}")
            await update.message.reply_text("Ошибка при получении статуса Nginx.")

    async def nginx_enable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /nginx_enable - включить Nginx SNI fallback."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            args = context.args or []
            port = int(args[0]) if args and args[0].isdigit() else 8443
            success, message = vless_manager.set_nginx_fallback(True, port)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in nginx_enable: {e}")
            await update.message.reply_text("Ошибка при включении Nginx fallback.")

    async def nginx_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /nginx_disable - выключить Nginx SNI fallback."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            success, message = vless_manager.set_nginx_fallback(False)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in nginx_disable: {e}")
            await update.message.reply_text("Ошибка при выключении Nginx fallback.")

    async def nginx_set_domain(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /nginx_set_domain <headscale_domain> [ha_domain]."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /nginx_set_domain <headscale_domain> [ha_domain]\n"
                    "Пример: /nginx_set_domain headscale.example.com ha.example.com"
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
            await update.message.reply_text("Ошибка при установке домена.")

    async def nginx_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /nginx_config - вывести Nginx конфиг для копирования на VPS."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
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
            await update.message.reply_text("Ошибка при генерации конфига Nginx.")

    # === HEADSCALE COMMANDS ===

    async def headscale_host_tailscale_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /headscale — IPv4/IPv6 клиента Tailscale на хосте VPS (админ/special)."""
        try:
            user = update.effective_user
            if not self._is_privileged(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору или special-пользователю."
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
                "Ошибка при определении адреса Tailscale на сервере."
            )

    async def headscale_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /headscale_status - статус Headscale."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            status = headscale_manager.get_status()
            enabled_emoji = "🟢" if status["enabled"] else "🔴"
            container_emoji = "🟢" if status["container_running"] else "🔴"

            lines = [
                f"{enabled_emoji} *Headscale*: {'включён' if status['enabled'] else 'выключен'}",
                f"{container_emoji} *Контейнер* `{escape_markdown(status['container_name'])}`: "
                f"{'запущен' if status['container_running'] else 'остановлен'}",
                f"🌐 *URL*: `{escape_markdown(status['server_url'] or 'не задан')}`",
                f"💻 *Ноды*: {status['node_count']}",
                f"👤 *Пользователи*: {status['user_count']}",
            ]

            # Headplane (Web UI). Развернут отдельным контейнером через
            # compose.headplane.yaml. Если контейнер найден — показываем URL
            # для SSH-туннеля. Если нет — не зашумляем status.
            hp = status.get("headplane") or {}
            if hp.get("container_running"):
                browser = escape_markdown(hp.get("browser_url", ""))
                tunnel = escape_markdown(hp.get("tunnel_hint", ""))
                lines.append("")
                lines.append("🟢 *Headplane* \\(Web UI\\): запущен")
                lines.append(f"🔗 `{browser}`")
                lines.append(f"🚪 SSH\\-туннель: `{tunnel}`")
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in headscale_status: {e}")
            await update.message.reply_text("Ошибка при получении статуса Headscale.")

    async def headscale_enable(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /headscale_enable."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return
            success, message = headscale_manager.enable_headscale()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_enable: {e}")
            await update.message.reply_text("Ошибка.")

    async def headscale_disable(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /headscale_disable."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return
            success, message = headscale_manager.disable_headscale()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_disable: {e}")
            await update.message.reply_text("Ошибка.")

    async def headscale_set_url(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /headscale_set_url <url>."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /headscale_set_url https://headscale.example.com"
                )
                return
            success, message = headscale_manager.set_server_url(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_set_url: {e}")
            await update.message.reply_text("Ошибка.")

    async def headscale_gen(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /headscale_gen [user] [expiration] - генерация Pre-Auth ключа.

        Аргументы позиционно-независимы: токен вида ``720h``/``30m``/``7d``
        распознаётся как срок жизни ключа, любой другой — как имя пользователя.
        Примеры: ``/headscale_gen``, ``/headscale_gen 720h``,
        ``/headscale_gen alice``, ``/headscale_gen alice 720h``.
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            # Срок: число + единица (s/m/h/d). Всё остальное — имя пользователя.
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
            await update.message.reply_text("Ошибка при генерации ключа.")

    async def headscale_revoke(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /headscale_revoke <key> [user] — отозвать Pre-Auth ключ.

        Без аргументов показывает список активных ключей, чтобы было что
        отзывать. Полезно, если ключ из /headscale_gen утёк или больше не нужен.
        """
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
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
                        "Активных Pre-Auth ключей нет.\n"
                        "Использование: /headscale_revoke <key> [user]"
                    )
                    return
                lines = ["🔑 *Pre-Auth ключи* \\(укажите ключ для отзыва\\):"]
                for k in keys:
                    if not isinstance(k, dict):
                        continue
                    kid = str(k.get("key", k.get("id", "?")))
                    used = "использован" if k.get("used") else "активен"
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
            await update.message.reply_text("Ошибка при отзыве ключа.")

    async def headscale_list_nodes(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /headscale_list_nodes - список нод."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            success, message, nodes = headscale_manager.list_nodes()
            if not success:
                await update.message.reply_text(message)
                return

            if not nodes:
                await update.message.reply_text("📋 Подключённых нод нет.")
                return

            lines = [f"📋 *Ноды Headscale* \\({len(nodes)}\\):"]
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
            await update.message.reply_text("Ошибка при получении списка нод.")

    async def headscale_create_user(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /headscale_create_user <name>."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /headscale_create_user <username>"
                )
                return
            success, message = headscale_manager.create_user(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in headscale_create_user: {e}")
            await update.message.reply_text("Ошибка при создании пользователя.")

    # === EXIT NODE (выход в интернет через VPS-координатор) ===

    async def exit_node_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /exit_node — статус exit node + гайд (admin + special)."""
        msg = update.effective_message
        try:
            user = update.effective_user
            self._track_user(user)
            if not self._is_privileged(user.id):
                await msg.reply_text(
                    "⛔ Доступно администратору или special-пользователю."
                )
                return

            status = headscale_manager.get_exit_node_status()
            if status.get("error"):
                ready = False
            else:
                ready = bool(status.get("advertising") and status.get("approved"))

            if ready:
                head = "🟢 Exit node готов — можно выходить в интернет через VPS."
            elif status.get("error"):
                head = f"🔴 Exit node недоступен: {status['error']}."
            else:
                head = "🟡 Exit node ещё не поднят." + (
                    ""
                    if self._is_admin(user.id)
                    else " Попросите админа включить его (/exit_node_on)."
                )

            lines = [head, ""]
            node_label = status.get("node_label") or ""
            if ready:
                lines.append(
                    headscale_manager.exit_node_client_instructions(node_label)
                )
            elif self._is_admin(user.id):
                # Админу показываем диагностику, чтобы понять чего не хватает.
                adv = "✅" if status.get("advertising") else "❌"
                appr = "✅" if status.get("approved") else "❌"
                fwd4 = status.get("ip_forward_v4")
                fwd6 = status.get("ip_forward_v6")
                lines += [
                    f"{adv} advertise на хосте",
                    f"{appr} approve маршрута в Headscale",
                    f"forwarding IPv4: {fwd4 or '?'}, IPv6: {fwd6 or '?'}",
                    "",
                    "Включить: /exit_node_on",
                ]
            await msg.reply_text("\n".join(lines).strip())
        except Exception as e:
            logger.error(f"Error in exit_node_command: {e}")
            if msg:
                await msg.reply_text("Ошибка при получении статуса exit node.")

    async def exit_node_on_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /exit_node_on — сделать VPS exit node'ом (только админ)."""
        msg = update.effective_message
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await msg.reply_text("⛔ Эта команда доступна только администратору.")
                return
            ok, report = headscale_manager.enable_exit_node()
            prefix = "" if ok else "❌ "
            await msg.reply_text(f"{prefix}{report}")
        except Exception as e:
            logger.error(f"Error in exit_node_on_command: {e}")
            await msg.reply_text("Ошибка при включении exit node.")

    async def exit_node_off_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /exit_node_off — выключить exit node (только админ)."""
        msg = update.effective_message
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await msg.reply_text("⛔ Эта команда доступна только администратору.")
                return
            ok, report = headscale_manager.disable_exit_node()
            await msg.reply_text(report)
        except Exception as e:
            logger.error(f"Error in exit_node_off_command: {e}")
            await msg.reply_text("Ошибка при выключении exit node.")

    # === CALLBACK QUERY HANDLER ===

    async def _handle_menu_callbacks(
        self, update: Update, context, query, data: str
    ) -> bool:
        """Callback'и `menu:*` — единственный namespace, доступный не-админам.

        Роль проверяется на каждое действие (spec §1), а не на входе
        handler'а. Возвращает True, если callback обработан.
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
                # diag_command сам различает роли (краткий/полный отчёт).
                await self.diag_command(update, context)
            elif action == "clear":
                await self.clear_chat(update, context)
            elif action == "settings":
                text, kb = self._settings_panel(uid)
                await self._menu_panel(query.message, text, kb, edit=True)
            elif action.startswith("set_theme:"):
                name = action.split(":", 1)[1]
                if storage_get_ui_prefs(uid)["theme"] == name:
                    return True  # уже выбрана — не дёргаем edit_text
                try:
                    storage_set_ui_pref(uid, "theme", name)
                except ValueError:
                    await query.message.reply_text("Неизвестная тема.")
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
                        "⛔ Доступно администратору или special-пользователю."
                    )
                    return True
                if self._is_admin(uid):
                    # У админа нет лимита просмотров — без подтверждения.
                    await self.my_profile_command(update, context)
                    return True
                views = storage_get_my_profile_views(uid)
                icons = self._theme_icons(uid)
                text = (
                    "Открыть VPN\\-профили?\n\n"
                    f"Это потратит просмотр *{views + 1} из "
                    f"{self.MY_PROFILE_VIEW_LIMIT}*\\. Сообщения с URL и QR "
                    "авто\\-удалятся через 15 минут\\."
                )
                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                self._btn(icons, "ok", "Открыть"),
                                callback_data="menu:my_profile_go",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                self._btn(icons, "back", "Назад"),
                                callback_data="menu:back",
                            )
                        ],
                    ]
                )
                await self._menu_panel(query.message, text, kb, edit=True)
            elif action == "my_profile_go":
                if not self._is_privileged(uid):
                    await query.message.reply_text(
                        "⛔ Доступно администратору или special-пользователю."
                    )
                    return True
                # Вернуть панель в обычное состояние, затем выдать профили
                # новыми сообщениями (счётчик инкрементится внутри команды).
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
                        "⛔ Доступно администратору или special-пользователю."
                    )
                    return True
                await self.exit_node_command(update, context)
            elif action == "admin_help":
                if not self._is_admin(uid):
                    await query.message.reply_text("⛔ Только для администратора.")
                    return True
                await self._help_show_menu(query.message)
            elif action == "admin_users":
                if not self._is_admin(uid):
                    await query.message.reply_text("⛔ Только для администратора.")
                    return True
                await self.admin_list_users(update, context)
            elif action == "backup":
                if not self._is_privileged(uid):
                    await query.message.reply_text(
                        "⛔ Доступно администратору или special-пользователю."
                    )
                    return True
                await self.backup_status(update, context)
            else:
                logger.warning("unknown menu action: %r", data)
                await query.message.reply_text("Неизвестное действие меню.")
            return True
        except Exception as exc:
            logger.error("menu callback %r failed: %s", data, exc)
            try:
                await query.message.reply_text("❌ Не удалось выполнить действие меню.")
            except Exception:
                pass
            return True

    async def callback_query_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработка callback queries от inline клавиатур."""
        query = update.callback_query
        await query.answer()

        data = query.data

        # `menu:` — единственный namespace для всех ролей; роль проверяется
        # внутри на каждое действие.
        if await self._handle_menu_callbacks(update, context, query, data):
            return

        if await self._handle_ai_translate_callbacks(update, context, query, data):
            return

        # Все остальные callback'и доступны только администраторам.
        if not self._is_admin(query.from_user.id):
            # У inline-сообщений (via @бот) query.message is None —
            # молча игнорируем чужой тап, отвечать некуда.
            if query.message:
                await query.message.reply_text("⛔ Только для администратора.")
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
                # Fallback: отправить новым сообщением без MarkdownV2
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
                        f"✅ Модель для {provider.upper()} изменена на {model}"
                    )
                else:
                    await query.message.edit_text(
                        f"❌ Ошибка при установке модели {model}"
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

            # Выводим конфиг
            await query.message.reply_text(
                f"🖥️ *Xray Server Config:*\n```json\n{config_json}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )

            # Выводим инструкцию
            instructions = """💡 *Как применить на сервере \\(SSH\\):*

*1\\. Подключитесь к серверу:*
```
ssh root@<IP\\_СЕРВЕРА>
```

*2\\. Откройте редактор nano:*
```
nano /usr/local/etc/xray/config\\.json
```

*3\\. В nano:*
• Удалите всё: зажмите `Ctrl\\+K` несколько раз
• Вставьте JSON: `Ctrl\\+Shift\\+V` \\(или ПКМ → Вставить\\)
• Сохраните: `Ctrl\\+O`, затем `Enter`
• Выйдите: `Ctrl\\+X`

*4\\. Проверьте и запустите:*
```
xray \\-test \\-config /usr/local/etc/xray/config\\.json
systemctl restart xray
systemctl status xray
```

✅ Если видите `Active: active \\(running\\)` \\- готово\\!"""
            await query.message.reply_text(
                instructions, parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        if data == "vless_export_qr_menu":
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ QR-коды VLESS доступны только администратору."
                )
                return
            await self._show_vless_qr_selection(query.message)
            return

        if data.startswith("vless_export_qr_uuid:"):
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ QR-коды VLESS доступны только администратору."
                )
                return
            client_uuid = data.split(":", 1)[1]
            await self._reply_vless_qr(query.message, client_uuid)
            return

        if data == "vless_export_sub_base64":
            sub_base64 = vless_manager.export_subscription_base64()
            if not sub_base64:
                await query.message.reply_text(
                    "❌ Нет данных для subscription. Проверьте /vless_sync"
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
                    "❌ Нет данных для subscription. Проверьте /vless_sync"
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
                await query.message.reply_text("❌ Нет данных для subscription")
            return

        if data == "hy2_export_qr_menu":
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ QR-коды Hysteria2 доступны только администратору."
                )
                return
            await self._show_hy2_qr_selection(query.message)
            return

        if data.startswith("hy2_export_qr_pw:"):
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ QR-коды Hysteria2 доступны только администратору."
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
                    "❌ Ссылка недоступна (не настроен сервер или секрет)"
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
                await query.message.reply_text("❌ Ссылка недоступна")
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
                await query.message.reply_text("❌ Нет данных для subscription")
            return

        if data == "mt_export_qr_menu":
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ QR-коды MTProto доступны только администратору."
                )
                return
            await self._show_mt_qr_selection(query.message)
            return

        if data.startswith("mt_export_qr_name:"):
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text(
                    "⛔ QR-коды MTProto доступны только администратору."
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
            # Сразу записываем конфиг host-Xray, чтобы Reality serverNames/dest
            # обновились; сам перезапуск оставляем на кнопку — так админ видит
            # результат теста конфига до рестарта.
            apply_ok, apply_msg = vless_manager.apply_xray_config()
            purged = await self._purge_all_profile_messages(query.get_bot())
            purge_note = (
                f"\n🧹 Старые ссылки/QR удалены у всех ({purged})."
                if purged
                else ""
            )
            text = (
                f"{message}\n{apply_msg}{purge_note}\n\n"
                "После перезапуска заново выдайте клиентам URI/QR: "
                "/vless_qr <имя>, /profiles <id> или /my_profile"
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Перезапустить Xray",
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
                await query.answer("⛔ Только для администратора.", show_alert=True)
                return
            action = data.split(":", 1)[1]
            await query.answer()
            if action == "set_sni":
                await self._show_hy2_sni_picker(query.message)
                return
            if action == "status":
                # Лёгкий статус без полного MD2-отчёта /hy2_status.
                try:
                    st = hysteria2_manager.get_status()
                except Exception as exc:
                    await query.message.reply_text(f"❌ Статус: {exc}")
                    return
                await query.message.reply_text(
                    "⚡ Hysteria2\n"
                    f"enabled: {'yes' if st.get('enabled') else 'no'}\n"
                    f"service: {st.get('service_active')}\n"
                    f"server: {st.get('server')}:{st.get('port')}\n"
                    f"sni: {st.get('sni') or '—'}\n"
                    f"insecure: {st.get('insecure')}\n"
                    f"clients: {st.get('clients_count')}\n\n"
                    "Подробно: /hy2_status"
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
                    "Отправьте команду:\n/hy2_gen_all\n"
                    "(пароль + сертификат + IP — лучше явно из чата)"
                )
                return
            await query.message.reply_text(f"Неизвестное действие: {action}")
            return

        if data.startswith("hy2_set_sni:"):
            if not self._is_admin(query.from_user.id):
                await query.message.reply_text("⛔ Только для администратора.")
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
                f"\n🧹 Старые ссылки/QR удалены у всех ({purged})."
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
            await query.answer("⏳ Перезапускаю Xray...")

            success, message = vless_manager.restart_xray()

            # Обновляем сообщение с результатом
            original_text = query.message.text
            new_text = (
                f"{original_text}\n\n{'✅' if success else '❌'} Перезапуск: {message}"
            )

            await query.edit_message_text(
                text=new_text,
                reply_markup=None,  # Убираем кнопку
            )
            return

        if data == "vless_reset_cancel":
            await query.message.edit_text("❌ Сброс конфигурации отменён")
            return

    # === ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ===

    async def _show_api_key(self, update: Update, app_id: str):
        """Показать API ключ для app_id (маскированный)."""
        try:
            from app_keys import get_api_key, has_api_key

            if app_id == "default":
                api_key = os.getenv("API_SECRET_KEY", "")
                source = "из \\.env"
            else:
                api_key = get_api_key(app_id)
                # Проверяем, есть ли индивидуальный ключ для этого app_id
                if has_api_key(app_id):
                    source = "индивидуальный"
                else:
                    source = "дефолтный"

            if api_key:
                masked = self._mask_secret(api_key).replace(".", "\\.")
                message = f"🔑 API ключ \\({source}\\):\n\n`{masked}`"
                if self._secret_reveal_allowed():
                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "👁️ Показать полностью",
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
                    message += "\n\n⚠️ Полный вывод секретов через Telegram отключён по умолчанию\\."
                    await update.callback_query.message.reply_text(
                        message, parse_mode=ParseMode.MARKDOWN_V2
                    )
            else:
                app_id_escaped = escape_markdown(app_id)
                message = f"❌ API ключ не найден для {app_id_escaped}"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error showing API key: {e}")
            await update.callback_query.message.reply_text(
                "Ошибка при получении API ключа"
            )

    async def _show_full_api_key(self, update: Update, app_id: str):
        """Показать полный API ключ для app_id."""
        try:
            if not self._secret_reveal_allowed():
                await update.callback_query.message.reply_text(
                    "⛔ Полный вывод API ключей через Telegram отключён. "
                    "Если это действительно нужно, включите `TELEGRAMHELPER_ALLOW_SECRET_REVEAL=true` только временно на сервере."
                )
                return

            from app_keys import get_api_key, has_api_key

            if app_id == "default":
                api_key = os.getenv("API_SECRET_KEY", "")
                source = "из \\.env"
            else:
                api_key = get_api_key(app_id)
                # Проверяем, есть ли индивидуальный ключ для этого app_id
                if has_api_key(app_id):
                    source = "индивидуальный"
                else:
                    source = "дефолтный"

            # URL API сервера
            api_url = os.getenv("API_URL", "http://localhost:8000/ai_query")

            if api_key:
                message = f"""🔑 API ключ \\({source}\\):

📍 *URL:*
`{api_url}`

🔐 *API Key:*
`{api_key}`

⚠️ _Скопируйте и удалите это сообщение_"""
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                app_id_escaped = escape_markdown(app_id)
                message = f"❌ API ключ не найден для {app_id_escaped}"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error showing full API key: {e}")
            await update.callback_query.message.reply_text(
                "Ошибка при получении API ключа"
            )

    async def _show_encryption_key(self, update: Update, app_id: str):
        """Показать ключ шифрования для app_id (маскированный)."""
        try:
            from app_keys import get_encryption_key, has_encryption_key

            if app_id == "default":
                enc_key = os.getenv("ENCRYPTION_KEY", "")
                source = "из \\.env"
            else:
                enc_key = get_encryption_key(app_id, force_reload=True)
                # Проверяем, есть ли индивидуальный ключ для этого app_id
                if has_encryption_key(app_id, force_reload=True):
                    source = "индивидуальный"
                else:
                    source = "дефолтный"

            if enc_key:
                masked = self._mask_secret(enc_key).replace(".", "\\.")
                message = f"🔐 Ключ шифрования \\({source}\\):\n\n`{masked}`"
                if self._secret_reveal_allowed():
                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "👁️ Показать полностью",
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
                    message += "\n\n⚠️ Полный вывод секретов через Telegram отключён по умолчанию\\."
                    await update.callback_query.message.reply_text(
                        message, parse_mode=ParseMode.MARKDOWN_V2
                    )
            else:
                app_id_escaped = escape_markdown(app_id)
                message = f"❌ Ключ шифрования не найден для {app_id_escaped}"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error showing encryption key: {e}")
            await update.callback_query.message.reply_text(
                "Ошибка при получении ключа шифрования"
            )

    async def _show_full_encryption_key(self, update: Update, app_id: str):
        """Показать полный ключ шифрования для app_id."""
        try:
            if not self._secret_reveal_allowed():
                await update.callback_query.message.reply_text(
                    "⛔ Полный вывод ключей шифрования через Telegram отключён. "
                    "Если это действительно нужно, включите `TELEGRAMHELPER_ALLOW_SECRET_REVEAL=true` только временно на сервере."
                )
                return

            from app_keys import get_encryption_key, has_encryption_key

            if app_id == "default":
                enc_key = os.getenv("ENCRYPTION_KEY", "")
                source = "из \\.env"
            else:
                enc_key = get_encryption_key(app_id, force_reload=True)
                # Проверяем, есть ли индивидуальный ключ для этого app_id
                if has_encryption_key(app_id, force_reload=True):
                    source = "индивидуальный"
                else:
                    source = "дефолтный"

            if enc_key:
                message = f"🔐 Ключ шифрования \\({source}\\):\n\n`{enc_key}`\n\n⚠️ _Скопируйте и удалите это сообщение_"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                app_id_escaped = escape_markdown(app_id)
                message = f"❌ Ключ шифрования не найден для {app_id_escaped}"
                await update.callback_query.message.reply_text(
                    message, parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error showing full encryption key: {e}")
            await update.callback_query.message.reply_text(
                "Ошибка при получении ключа шифрования"
            )

    async def _generate_api_key(self, update: Update, app_id: str):
        """Сгенерировать новый API ключ."""
        try:
            new_key = secrets.token_hex(32)

            if app_id == "default":
                if not self._secret_reveal_allowed():
                    await update.callback_query.message.reply_text(
                        "⛔ Генерация дефолтного API ключа через Telegram отключена в безопасном режиме.\n"
                        "Сгенерируйте ключ локально на сервере и обновите `API_SECRET_KEY` в `.env`."
                    )
                    return

                message = f"""✅ Новый API ключ сгенерирован:

`{new_key}`

⚠️ Добавьте в \\.env как API\\_SECRET\\_KEY
🔄 После изменения \\.env перезапустите контейнер"""
            else:
                from app_keys import set_api_key

                set_api_key(app_id, new_key)
                app_id_escaped = escape_markdown(app_id)
                masked = self._mask_secret(new_key).replace(".", "\\.")
                if self._secret_reveal_allowed():
                    message = f"""✅ API ключ для {app_id_escaped} сгенерирован и сохранён:

`{new_key}`

💾 Сохранено в app\\_keys\\.json
🔄 Изменения применятся при следующем запросе"""
                else:
                    message = f"""✅ API ключ для {app_id_escaped} сгенерирован и сохранён:

`{masked}`

⚠️ Полный секрет не отправляется через Telegram
💾 Сохранено в app\\_keys\\.json"""

            await update.callback_query.message.reply_text(
                message, parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error generating API key: {e}")
            await update.callback_query.message.reply_text(
                "Ошибка при генерации API ключа"
            )

    async def _generate_encryption_key(self, update: Update, app_id: str):
        """Сгенерировать новый ключ шифрования."""
        try:
            new_key = secrets.token_hex(32)

            if app_id == "default":
                if not self._secret_reveal_allowed():
                    await update.callback_query.message.reply_text(
                        "⛔ Генерация дефолтного ключа шифрования через Telegram отключена в безопасном режиме.\n"
                        "Сгенерируйте ключ локально на сервере и обновите `ENCRYPTION_KEY` в `.env`."
                    )
                    return

                message = f"""✅ Новый ключ шифрования сгенерирован:

`{new_key}`

⚠️ Добавьте в \\.env как ENCRYPTION\\_KEY
🔄 После изменения \\.env перезапустите контейнер"""
            else:
                from app_keys import set_encryption_key

                set_encryption_key(app_id, new_key)
                app_id_escaped = escape_markdown(app_id)
                masked = self._mask_secret(new_key).replace(".", "\\.")
                if self._secret_reveal_allowed():
                    message = f"""✅ Ключ шифрования для {app_id_escaped} сгенерирован и сохранён:

`{new_key}`

💾 Сохранено в app\\_keys\\.json
🔄 Изменения применятся при следующем запросе"""
                else:
                    message = f"""✅ Ключ шифрования для {app_id_escaped} сгенерирован и сохранён:

`{masked}`

⚠️ Полный секрет не отправляется через Telegram
💾 Сохранено в app\\_keys\\.json"""

            await update.callback_query.message.reply_text(
                message, parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error generating encryption key: {e}")
            await update.callback_query.message.reply_text(
                "Ошибка при генерации ключа шифрования"
            )

    async def _delete_api_key(self, update: Update, app_id: str):
        """Удалить API ключ."""
        try:
            from app_keys import delete_api_key

            if delete_api_key(app_id):
                message = f"✅ API ключ для {app_id} удалён"
            else:
                message = f"❌ Не удалось удалить API ключ для {app_id}"

            await update.callback_query.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error deleting API key: {e}")
            await update.callback_query.message.reply_text(
                "Ошибка при удалении API ключа"
            )

    async def _delete_encryption_key(self, update: Update, app_id: str):
        """Удалить ключ шифрования."""
        try:
            from app_keys import delete_encryption_key

            if delete_encryption_key(app_id):
                message = f"✅ Ключ шифрования для {app_id} удалён"
            else:
                message = f"❌ Не удалось удалить ключ шифрования для {app_id}"

            await update.callback_query.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error deleting encryption key: {e}")
            await update.callback_query.message.reply_text(
                "Ошибка при удалении ключа шифрования"
            )

    async def _show_model_selection(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, provider: str
    ):
        """Показать выбор модели для провайдера."""
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
                f"🤖 Выберите модель для {provider.upper()}:", reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error showing model selection: {e}")
            await update.callback_query.message.edit_text(
                "Ошибка при загрузке списка моделей"
            )

    # ================================================================
    # HYSTERIA2 COMMANDS
    # ================================================================

    def _escape_md2(self, text):
        """Экранирование спецсимволов для Telegram Markdown V2."""
        if not text:
            return "не настроен"
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
        """Команда /hy2 — хаб Hysteria2 (видна в автодополнении при наборе /hy2)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📊 Статус", callback_data="hy2_hub:status"
                        ),
                        InlineKeyboardButton(
                            "🌐 SNI", callback_data="hy2_hub:set_sni"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🟢 On (в выдачу)", callback_data="hy2_hub:on"
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
                "⚡ Hysteria2 — выберите действие\n\n"
                "Команды вручную: /hy2_status /hy2_set_sni /hy2_on "
                "/hy2_apply /hy2_start",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Error in hy2_command: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_status — показать статус Hysteria2."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
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
                service_line = "🟢 запущен \\(active\\)"
            elif active is False:
                service_line = "🔴 остановлен \\(inactive\\)"
            else:
                service_line = "❔ не определено"

            binary_path = status.get("binary_path") or ""
            unit_ok = bool(status.get("unit_exec_ok"))
            unit_path = status.get("unit_exec_path") or ""

            # Подсказка следующего шага — чтобы инструкция не вела в тупик.
            if not binary_path:
                next_step = (
                    "➡️ *Дальше:* `/hy2_install` — бинарник Hysteria2 не найден "
                    "на хосте \\(иначе будет 203/EXEC\\)\\."
                )
            elif unit_path and not unit_ok:
                next_step = (
                    "➡️ *Дальше:* `/hy2_install` — systemd ExecStart указывает "
                    "на отсутствующий файл\\. Команда починит unit\\."
                )
            elif not configured:
                next_step = "➡️ *Дальше:* `/hy2_gen_all` \\(пароль \\+ сертификат \\+ IP\\), затем `/hy2_apply`\\."
            elif not enabled:
                next_step = (
                    "➡️ *Дальше:* `/hy2_on` — пометить профиль активным "
                    "\\(нужно для `/provision`, `/my_profile` и выдачи URI/QR\\)\\."
                )
            elif active is False:
                next_step = "➡️ *Дальше:* `/hy2_start` — сервис не запущен на сервере\\."
            elif active is None:
                next_step = "⚠️ Не удалось проверить systemd \\(SSH/`systemctl`\\)\\. Проверьте `/hy2_logs`\\."
            else:
                next_step = (
                    "✅ Всё готово\\. Клиент: `/hy2_add_client <имя>` → `/hy2_qr <имя>` "
                    "или раздайте через `/provision <id>`\\."
                )

            binary_line = (
                f"• Binary: `{esc(binary_path)}`"
                if binary_path
                else "• Binary: ❌ не найден"
            )
            if unit_path:
                unit_line = (
                    f"• Unit ExecStart: `{esc(unit_path)}` "
                    + ("✅" if unit_ok else "❌ нет файла")
                )
            else:
                unit_line = "• Unit ExecStart: ❌ unit не найден"

            message = f"""⚡ *Hysteria2 Статус*

*Профиль \\(enabled\\):* {profile_emoji} {"включён" if enabled else "выключен"}
*Сервис \\(systemd\\):* {service_line}
*Конфигурация:* {config_emoji} {"настроена" if configured else "не настроена"}

*Параметры:*
• Сервер: `{esc(status.get("server"))}`
• Порт: `{esc(status.get("port", 443))}` \\(UDP\\)
• SNI: `{esc(status.get("sni") or "(авто)")}`
• Insecure: {"да ⚠️" if status.get("insecure") else "нет ✅"}
{binary_line}
{unit_line}

*Обфускация:* {("✅ " + esc(status.get("obfs_type", ""))) if status.get("has_obfs") else "❌ выключена"}
*Скорость:* ↑ {status.get("up_mbps", 0) or "авто"} / ↓ {status.get("down_mbps", 0) or "авто"} Mbps
*Masquerade:* `{esc(status.get("masquerade_url", ""))}`
*Пароль:* {"✅" if status["has_password"] else "❌"}
*Клиентов:* {status.get("clients_count", 0)}

{next_step}

*Обновлено:* {esc(status.get("updated_at", "никогда"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in hy2_status: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    # === Reticulum / HA-стек ===

    async def reticulum_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /reticulum_status — статус HA-стека и Reticulum-моста."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return
            import reticulum_manager

            st = reticulum_manager.get_status()
            if not st["installed"]:
                await update.message.reply_text(
                    "🛰 HA-стек / Reticulum не установлен на этом сервере."
                )
                return

            def mark(b):
                return "🟢" if b else "🔴"

            svc = st["services"]
            lines = [
                "🛰 Reticulum / HA-стек",
                "",
                f"{mark(svc.get('ha-reticulum-bridge'))} ha-reticulum-bridge",
                f"{mark(svc.get('ha-stub-grpc'))} ha-stub-grpc",
                f"{mark(svc.get('ha-stub-udp'))} ha-stub-udp",
                f"Мост слушает :50061 — {'да' if st['listening'] else 'нет'}",
                "",
                f"Bridge hash: {st['bridge_hash'] or '(появится в логе старта)'}",
            ]
            if st.get("i2pd_installed"):
                lines += [
                    "",
                    f"{mark(st.get('i2pd_active'))} i2pd (I2P, путь 2)",
                    f"I2P b32: {st.get('i2p_b32') or '(туннель строится / нет)'}",
                ]
            lines += [
                "",
                "Управление: /reticulum_restart, /reticulum_hash, /reticulum_i2p",
                "Тест round-trip — из UDP_gRPC_COM_Lite CLI (RETICULUM_TESTING.md).",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in reticulum_status: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def reticulum_restart(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /reticulum_restart — перезапустить HA-стек (3 сервиса)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            import reticulum_manager

            ok, msg = reticulum_manager.restart()
            await update.message.reply_text(("✅ " if ok else "❌ ") + msg)
        except Exception as e:
            logger.error(f"Error in reticulum_restart: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def reticulum_hash(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /reticulum_hash — bridge destination hash (для клиентов)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            import reticulum_manager

            h = reticulum_manager.get_bridge_hash()
            if h:
                await update.message.reply_text(
                    f"🛰 Bridge hash:\n`{h}`", parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await update.message.reply_text(
                    "Bridge hash не найден (мост не запущен или нет в логе старта)."
                )
        except Exception as e:
            logger.error(f"Error in reticulum_hash: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def reticulum_i2p(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /reticulum_i2p — статус I2P-пути (i2pd + b32 серверного туннеля)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            import reticulum_manager

            i = reticulum_manager.get_i2p_status()
            if not i["installed"]:
                await update.message.reply_text(
                    "🛰 i2pd не установлен — I2P-путь (этап 3) не настроен на этом сервере."
                )
                return
            lines = [
                "🛰 Reticulum I2P (путь 2)",
                "",
                f"{'🟢' if i['active'] else '🔴'} i2pd",
                f"b32 моста: {i['b32'] or '(серверный туннель ha-bridge строится / нет)'}",
                "",
                "Клиент: i2pd client-туннель → этот b32, RNS по TCP на 127.0.0.1:50061.",
                "Детали — RETICULUM_TESTING.md (режим C), RETICULUM_VPS.md §12.",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in reticulum_i2p: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def reticulum_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /reticulum_health — здоровье i2pd (сеть, tunnel success, leasesets)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            import reticulum_manager

            h = reticulum_manager.get_i2p_health()
            if not h["installed"]:
                await update.message.reply_text(
                    "🛰 i2pd не установлен — I2P-путь (этап 3) не настроен на этом сервере."
                )
                return
            if not h["active"]:
                await update.message.reply_text("🔴 i2pd не запущен. Подними: systemctl start i2pd")
                return
            lines = [
                "🩺 i2pd health (I2P, путь 2)",
                "",
                f"Network status:  {h['network'] or '—'}",
                f"Tunnel success:  {h['success_rate'] or '—'}",
                f"Routers:         {h['routers'] or '—'}  (floodfills {h['floodfills'] or '—'})",
                f"LeaseSets:       {h['leasesets'] or '—'}",
                f"Transit tunnels: {h['transit'] or '—'}",
                f"Uptime:          {h['uptime'] or '—'}",
                "",
                "💡 Свежий узел: низкий success rate и LeaseSets=0 — норма первых минут;",
                "b32 моста публикуется после прогрева туннелей. b32 — /reticulum_i2p.",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in reticulum_health: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    # === Управление администраторами ===

    async def admin_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /admin_list — список администраторов (первичные защищены)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            import storage

            founders = list(self.config.admin_user_ids or [])
            dynamic = [a for a in storage.get_dynamic_admins() if a not in founders]
            lines = ["👑 Администраторы:", ""]
            for f in founders:
                lines.append(f"🔒 {f} — первичный (нельзя снять)")
            for d in dynamic:
                lines.append(f"• {d} — назначенный")
            if not dynamic:
                lines.append("(назначенных динамически нет)")
            lines += [
                "",
                "Назначить: /admin_add <user_id>",
                "Снять: /admin_remove <user_id>",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in admin_list: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def admin_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /admin_add <user_id> — назначить пользователя администратором."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /admin_add <user_id>\n"
                    "Назначайте из особых; обычного сначала добавьте: /special_add <id>"
                )
                return
            try:
                uid = int(args[0])
            except ValueError:
                await update.message.reply_text("user_id должен быть числом.")
                return
            if self.config.is_admin(uid):
                await update.message.reply_text(f"{uid} уже администратор.")
                return
            import storage

            storage.add_dynamic_admin(uid)
            is_special = self.config.is_special_user(uid) or storage.is_special_user(
                uid
            )
            note = (
                ""
                if is_special
                else "\n⚠️ Этого пользователя нет в списке особых (можно /special_add)."
            )
            await update.message.reply_text(f"✅ {uid} назначен администратором.{note}")
        except Exception as e:
            logger.error(f"Error in admin_add: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def admin_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /admin_remove <user_id> — снять администратора (кроме первичного)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /admin_remove <user_id>"
                )
                return
            try:
                uid = int(args[0])
            except ValueError:
                await update.message.reply_text("user_id должен быть числом.")
                return
            if self.config.is_founder_admin(uid):
                await update.message.reply_text(
                    "🔒 Это первичный админ (задан при установке бота) — снять нельзя."
                )
                return
            import storage

            if not storage.is_dynamic_admin(uid):
                await update.message.reply_text(
                    f"{uid} не является назначенным админом."
                )
                return
            storage.remove_dynamic_admin(uid)
            await update.message.reply_text(f"✅ {uid} снят с администраторов.")
        except Exception as e:
            logger.error(f"Error in admin_remove: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_on — включить Hysteria2."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = hysteria2_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_on: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_off — выключить Hysteria2."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = hysteria2_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_off: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_config — показать текущую конфигурацию."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            config = hysteria2_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"⚡ Конфигурация Hysteria2:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Error in hy2_config: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_server(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_set_server <ip> — установить сервер."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = hysteria2_manager.set_server(server)
            if success:
                message += (
                    "\n\n📲 Перевыдайте URI/QR: /profiles <id>  или  /my_profile"
                    "\nℹ️ /hy2_apply не обязателен — меняется адрес в клиентской ссылке."
                )
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_server: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_set_port <port> — установить порт."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /hy2_set_port <port>")
                return
            try:
                port = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ Порт должен быть числом")
                return
            success, message = hysteria2_manager.set_port(port)
            if success:
                message += self._hy2_apply_followup_text(port=port)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_port: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /hy2_set_password <pass> — установить пароль."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /hy2_set_password <password>"
                )
                return
            password = args[0]
            success, message = hysteria2_manager.set_password(password)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_password: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_obfs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_set_obfs <type> <password> — установить обфускацию."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование:\n"
                    "/hy2_set_obfs salamander <password> — включить\n"
                    "/hy2_set_obfs off — выключить"
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_sni(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_set_sni — SNI/маскировка Hy2 (кнопки как у /vless_set_sni)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                # Сразу ответить, чтобы команда не выглядела «молчащей», пока
                # собираются кнопки (раньше get_status() мог висеть на systemd).
                await update.message.reply_text("⏳ Выбор SNI Hysteria2…")
                await self._show_hy2_sni_picker(update.message)
                return
            success, message = hysteria2_manager.set_sni(args[0])
            if success:
                # Self-signed cert CN должен совпадать с новым SNI.
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def _show_hy2_sni_picker(self, message) -> None:
        """Кнопки выбора Hy2 SNI (текущий ✅); смена = SNI+masquerade+cert."""
        # Только JSON — без get_status()/systemd, иначе команда «молчит» десятки секунд.
        try:
            current = (hysteria2_manager.get_config(include_secrets=False) or {}).get(
                "sni", ""
            ) or ""
        except Exception:
            current = ""

        domains = list(getattr(hysteria2_manager, "AVAILABLE_SNI", None) or [])
        if not domains:
            # Старый образ без AVAILABLE_SNI — всё равно даём рабочие варианты.
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
            "🌐 Выбор SNI (Hysteria2 TLS)\n\n"
            f"Текущий: {current or '—'}\n\n"
            "Нажмите домен — бот сменит SNI + masquerade, перевыпустит "
            "self-signed сертификат (CN=SNI) и предложит /hy2_apply.\n\n"
            "Свой домен: /hy2_set_sni example.com"
        )
        try:
            await message.reply_text(plain, reply_markup=keyboard)
        except Exception as exc:
            logger.error("_show_hy2_sni_picker failed: %s", exc)
            await message.reply_text(
                "Не удалось показать кнопки. Смените вручную:\n"
                "/hy2_set_sni yahoo.com\n"
                "затем /hy2_apply"
            )

    async def hy2_set_speed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_set_speed <up> <down> — установить скорость (Mbps)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if len(args) < 2:
                await update.message.reply_text(
                    "Использование: /hy2_set_speed <up_mbps> <down_mbps>\n0 = авто"
                )
                return
            try:
                up = int(args[0])
                down = int(args[1])
            except ValueError:
                await update.message.reply_text("❌ Скорость должна быть числом")
                return
            success, message = hysteria2_manager.set_speed(up, down)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_speed: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_masquerade(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /hy2_set_masquerade <url> — установить URL маскировки."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /hy2_set_masquerade <url>"
                )
                return
            success, message = hysteria2_manager.set_masquerade(args[0])
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_masquerade: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_insecure(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /hy2_set_insecure <1|0> — insecure TLS (self-signed)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args or args[0] not in ("0", "1"):
                await update.message.reply_text(
                    "Использование: /hy2_set_insecure 1  или  /hy2_set_insecure 0"
                )
                return
            insecure = args[0] == "1"
            success, message = hysteria2_manager.set_insecure(insecure)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_insecure: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_quic_safe(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /hy2_set_quic_safe <1|0> — safe-QUIC defaults (чинит Windows-клиентов)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args or args[0] not in ("0", "1"):
                await update.message.reply_text(
                    "Использование: /hy2_set_quic_safe 1 | 0\n\n"
                    "1 — включить safe QUIC-defaults (disablePathMTUDiscovery + receive windows).\n"
                    "0 — выключить (классический Hysteria2-конфиг).\n\n"
                    "После смены: /hy2_apply"
                )
                return
            enabled = args[0] == "1"
            success, message = hysteria2_manager.set_quic_safe(enabled)
            if success:
                message += self._hy2_apply_followup_text()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_set_quic_safe: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_set_quic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_set_quic <param> <value> — тонкая настройка QUIC."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if len(args) < 2:
                await update.message.reply_text(
                    "Использование: /hy2_set_quic <param> <value>\n\n"
                    "Параметры:\n"
                    "enabled, disable_path_mtu_discovery,\n"
                    "init_stream_receive_window, max_stream_receive_window,\n"
                    "init_conn_receive_window, max_conn_receive_window,\n"
                    "max_idle_timeout, keep_alive_period\n\n"
                    "Примеры:\n"
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_gen_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /hy2_gen_password — сгенерировать и установить пароль."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            password = hysteria2_manager.generate_password()
            success, message = hysteria2_manager.set_password(password)
            if success:
                await update.message.reply_text(
                    f"{message}\n🔑 Пароль: `{password}`",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_gen_password: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_gen_cert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_gen_cert — сгенерировать TLS сертификат."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            await update.message.reply_text("⏳ Генерация TLS сертификата...")
            success, message = hysteria2_manager.generate_self_signed_cert()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_gen_cert: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_gen_all — сгенерировать всё (пароль + сертификат + IP)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            await update.message.reply_text(
                "⏳ Генерация пароля, сертификата и определение IP..."
            )
            success, data, message = hysteria2_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_gen_all: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_add_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_add_client <name> — добавить клиента."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /hy2_add_client <имя>")
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_qr - показать QR для Hysteria2-клиента."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text(
                    "⛔ QR-коды Hysteria2 доступны только администратору."
                )
                return
            if await self._legacy_per_client_guard(update):
                return

            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Использование: /hy2_qr <client_name_or_password>"
                )
                return

            await self._reply_hy2_qr(update.message, args[0])
        except Exception as e:
            logger.error(f"Error in hy2_qr: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_del_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_del_client <name> — удалить клиента."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /hy2_del_client <имя>")
                return
            success, message = hysteria2_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_del_client: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /hy2_list_clients — список клиентов."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            clients = hysteria2_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 Клиентов нет")
                return
            lines = ["⚡ *Клиенты Hysteria2:*\n"]
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                pw = c.get("password", "")
                masked = f"{pw[:4]}..." if len(pw) > 4 else "***"
                created = c.get("created_at", "")[:10]
                lines.append(
                    f"{i}\\. `{self._escape_md2(name)}` — пароль: `{self._escape_md2(masked)}` \\({self._escape_md2(created)}\\)"
                )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in hy2_list_clients: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_install — установить Hysteria2 на сервер."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            await update.message.reply_text("⏳ Установка Hysteria2...")
            success, message = hysteria2_manager.install_hysteria2()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_install: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_apply — применить конфиг к серверу."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = hysteria2_manager.apply_config()
            # Смена конфига Hysteria2 меняет hy2://-ссылки — старые ссылки/QR
            # у всех пользователей больше не должны показываться.
            purged = await self._purge_all_profile_messages(update.get_bot())
            if purged:
                message += (
                    f"\n\n🧹 Старые ссылки/QR удалены у всех ({purged}). "
                    "Свежие выдайте заново: /profiles <id> или /my_profile."
                )
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_apply: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_start — запустить сервис."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = hysteria2_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in hy2_start: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_stop — остановить сервис."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = hysteria2_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_stop: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_restart — перезапустить сервис."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = hysteria2_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in hy2_restart: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_logs — показать логи."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = hysteria2_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 Логи Hysteria2:\n```\n{output}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Error in hy2_logs: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def hy2_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /hy2_export — экспорт конфигураций."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
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
                parts.append(f"*URI \\(для клиента\\):*\n`{esc(uri)}`\n")

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
                        "📷 QR по клиенту", callback_data="hy2_export_qr_menu"
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
            await update.message.reply_text(f"Ошибка: {e}")


    # === MTPROTO PROXY COMMANDS ===

    async def mt_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_status — показать статус MTProto proxy."""
        try:
            user = update.effective_user
            if not self._is_admin(user.id):
                await update.message.reply_text(
                    "⛔ Эта команда доступна только администратору."
                )
                return

            logger.info(f"Admin {user.id} requested MTProto status")
            status = mtproto_manager.get_status()

            esc = self._escape_md2
            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            message = f"""📡 *MTProto Proxy Статус*

*Состояние:* {status_emoji} {"Включён" if status["enabled"] else "Выключен"}
*Конфигурация:* {config_emoji} {"Настроена" if status["configured"] else "Не настроена"}

*Параметры:*
• Сервер: `{esc(status.get("server") or "(не задан)")}`
• Порт: `{esc(str(status.get("port", 993)))}` \\(TCP\\)
• Режим: `{esc(status.get("secret_mode_label") or status.get("secret_mode") or "?")}`
• Секрет: {"✅" if status["has_secret"] else "❌"}
• Fake\\-TLS: {"✅ " + esc(status.get("fake_tls_domain", "")) if status.get("is_fake_tls") else "❌ выключен"}
• Тег: `{esc(status.get("tag") or "(нет)")}`
• Воркеры: {status.get("workers", 2)}
• Клиентов: {status.get("clients_count", 0)}

*Обновлено:* {esc(str(status.get("updated_at") or "никогда"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in mt_status: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_on — включить MTProto proxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mtproto_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_on: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_off — выключить MTProto proxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mtproto_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_off: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_config — показать текущую конфигурацию."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            config = mtproto_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"📡 Конфигурация MTProto:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Error in mt_config: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_set_server(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_set_server <ip> — установить сервер."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = mtproto_manager.set_server(server)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_server: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_set_port <port> — установить порт."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /mt_set_port <port>")
                return
            try:
                port = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ Порт должен быть числом")
                return
            success, message = mtproto_manager.set_port(port)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_port: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_set_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_set_mode <dd_inline|ee_split> — переключить режим MTProto."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /mt_set_mode <mode>\n"
                    f"Доступно: `{mtproto_manager.SECRET_MODE_DD_INLINE}`, `{mtproto_manager.SECRET_MODE_EE_SPLIT}`\n"
                    "Для новых серверов обычно подходит `ee_split`."
                )
                return
            success, message = mtproto_manager.set_secret_mode(args[0])
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in mt_set_mode: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_set_domain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_set_domain <domain> — установить fake-TLS домен."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                domains = ", ".join(mtproto_manager.AVAILABLE_FAKE_TLS_DOMAINS)
                await update.message.reply_text(
                    f"Использование: /mt_set_domain <domain>\nПримеры: {domains}"
                )
                return
            success, message = mtproto_manager.set_fake_tls_domain(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_domain: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_set_tag(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_set_tag <hex> — установить статистический тег."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /mt_set_tag <hex_tag>\n"
                    "Тег для @MTProxybot (промоутирование прокси).\n"
                    "/mt_set_tag off — удалить тег"
                )
                return
            tag = "" if args[0] == "off" else args[0]
            success, message = mtproto_manager.set_tag(tag)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_tag: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_set_workers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_set_workers <n> — установить число воркеров."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /mt_set_workers <1-16>")
                return
            try:
                workers = int(args[0])
            except ValueError:
                await update.message.reply_text(
                    "❌ Количество воркеров должно быть числом"
                )
                return
            success, message = mtproto_manager.set_workers(workers)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_set_workers: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_gen_secret(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_gen_secret [domain] — сгенерировать и установить секрет."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            domain = args[0] if args else None
            new_secret = mtproto_manager.generate_secret(domain)
            success, message = mtproto_manager.set_secret(new_secret)
            if success:
                status = mtproto_manager.get_status()
                await update.message.reply_text(
                    f"{message}\n🔑 Секрет: `{new_secret}`\n🧭 Режим: `{status.get('secret_mode_label')}`",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_gen_secret: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_gen_all — сгенерировать секрет + определить IP."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            await update.message.reply_text("⏳ Генерация секрета и определение IP...")
            success, data, message = mtproto_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_gen_all: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_add_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_add_client <name> — добавить клиента."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /mt_add_client <имя>")
                return
            name = args[0]
            success, message, client = mtproto_manager.add_client(name)
            if success and client:
                link = mtproto_manager.generate_tg_link(client.get("secret"))
                if link:
                    message += f"\n🔗 Ссылка: `{link}`"
            await update.message.reply_text(message)
            if success and client:
                await self._reply_mt_qr(update.message, client.get("secret", name))
        except Exception as e:
            logger.error(f"Error in mt_add_client: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_qr - показать QR для MTProto-клиента."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text(
                    "⛔ QR-коды MTProto доступны только администратору."
                )
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if len(args) != 1:
                await update.message.reply_text(
                    "Использование: /mt_qr <client_name_or_secret>"
                )
                return
            await self._reply_mt_qr(update.message, args[0])
        except Exception as e:
            logger.error(f"Error in mt_qr: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_del_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_del_client <name> — удалить клиента."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /mt_del_client <имя>")
                return
            success, message = mtproto_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_del_client: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_list_clients(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_list_clients — список клиентов."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            clients = mtproto_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 Клиентов нет")
                return
            lines = ["📡 *Клиенты MTProto:*\n"]
            status = mtproto_manager.get_status()
            lines.append(
                f"*Режим:* `{self._escape_md2(status.get('secret_mode_label') or status.get('secret_mode') or '?')}`\n"
            )
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                secret = c.get("secret", "")
                masked = f"{secret[:6]}..." if len(secret) > 6 else "***"
                created = c.get("created_at", "")[:10]
                lines.append(
                    f"{i}\\. `{self._escape_md2(name)}` — секрет: `{self._escape_md2(masked)}` \\({self._escape_md2(created)}\\)"
                )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error in mt_list_clients: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_install — установить MTProto proxy на сервер."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            await update.message.reply_text(
                "⏳ Установка MTProto proxy (компиляция из исходников)..."
            )
            success, message = mtproto_manager.install_mtproto()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_install: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_apply — применить конфиг (записать systemd unit, перезапустить)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mtproto_manager.apply_config()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_apply: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_start — запустить сервис."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mtproto_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in mt_start: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_stop — остановить сервис."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mtproto_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_stop: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_restart — перезапустить сервис."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mtproto_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_restart: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_logs [n] — показать логи."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = mtproto_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 Логи MTProto:\n```\n{output}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Error in mt_logs: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_fetch_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_fetch_config — обновить proxy-secret и proxy-multi.conf."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            await update.message.reply_text(
                "⏳ Загрузка proxy-secret и proxy-multi.conf..."
            )
            success, message = mtproto_manager.fetch_proxy_config()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in mt_fetch_config: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mt_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mt_export — экспорт ссылок и конфигов."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return

            logger.info(f"Admin {update.effective_user.id} exporting MTProto config")

            esc = self._escape_md2

            tg_link = mtproto_manager.generate_tg_link()
            https_link = mtproto_manager.generate_https_link()
            status = mtproto_manager.get_status()

            parts = ["📡 *MTProto Export*\n"]
            parts.append(
                f"*Режим:* `{esc(status.get('secret_mode_label') or status.get('secret_mode') or '?')}`\n"
            )

            if tg_link:
                parts.append(f"*tg link \\(для Telegram\\):*\n`{esc(tg_link)}`\n")
            if https_link:
                parts.append(f"*HTTPS link:*\n`{esc(https_link)}`\n")

            if not tg_link and not https_link:
                parts.append("❌ Не настроен сервер или секрет")

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
                        "📷 QR по клиенту", callback_data="mt_export_qr_menu"
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
            await update.message.reply_text(f"Ошибка: {e}")

    # === ERROR HANDLER ===

    async def naive_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_status — показать состояние NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            status = naiveproxy_manager.get_status()
            enabled = "🟢 включен" if status.get("enabled") else "🔴 выключен"
            configured = "да" if status.get("configured") else "нет"
            systemd = status.get("systemd_output") or "unknown"
            text = (
                "🌐 NaiveProxy status\n\n"
                f"Состояние: {enabled}\n"
                f"Сконфигурирован: {configured}\n"
                f"Домен: {status.get('domain') or '-'}\n"
                f"Порт: {status.get('port')}\n"
                f"Пользователь: {status.get('username') or '-'}\n"
                f"Scheme: {status.get('scheme')}\n"
                f"Padding: {status.get('padding')}\n"
                f"Probe resistance: {status.get('probe_resistance')}\n"
                f"Service: {status.get('service_name')}\n"
                f"systemd: {systemd}"
            )
            await update.message.reply_text(text)
        except Exception as e:
            logger.error(f"Error in naive_status: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_on — включить NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = naiveproxy_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_on: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_off — выключить NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = naiveproxy_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_off: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_config — показать текущий конфиг NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_set_domain(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /naive_set_domain <domain> — задать домен NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /naive_set_domain <domain>"
                )
                return
            success, message = naiveproxy_manager.set_domain(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_domain: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_set_port <port> — задать порт NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text("Использование: /naive_set_port <port>")
                return
            success, message = naiveproxy_manager.set_port(int(context.args[0]))
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_port: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_set_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_set_user <username> — задать пользователя NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /naive_set_user <username>"
                )
                return
            success, message = naiveproxy_manager.set_username(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_user: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_set_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /naive_set_password <password> — задать пароль NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /naive_set_password <password>"
                )
                return
            success, message = naiveproxy_manager.set_password(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_password: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_set_dpi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_set_dpi <param> <value> — тонкая настройка NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if len(args) < 2:
                await update.message.reply_text(
                    "Использование: /naive_set_dpi <param> <value>\n\n"
                    "Параметры:\n"
                    "scheme=https|quic\n"
                    "padding=on|off\n"
                    "local_socks_port=10808\n"
                    "probe_resistance=on|off\n"
                    "hide_ip=on|off\n"
                    "hide_via=on|off\n"
                    "camouflage_url=https://example.com или off"
                )
                return
            param = args[0]
            value = " ".join(args[1:])
            success, message = naiveproxy_manager.set_dpi_param(param, value)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_set_dpi: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_gen_creds(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_gen_creds — сгенерировать user/password для NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_install — запустить серверную установку NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            await update.message.reply_text("⏳ Установка NaiveProxy...")
            success, message = naiveproxy_manager.install_naiveproxy()
            await update.message.reply_text(
                "✅ Установка завершена"
                if success
                else "❌ Установка завершилась с ошибкой"
            )
            await self._reply_export_file(
                update.message,
                message,
                "naive-install.log",
                "NaiveProxy install output",
            )
        except Exception as e:
            logger.error(f"Error in naive_install: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_uri(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_uri — показать клиентский URI NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            uri = naiveproxy_manager.build_client_uri()
            await update.message.reply_text(f"🌐 NaiveProxy URI:\n{uri}")
        except Exception as e:
            logger.error(f"Error in naive_uri: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_apply — записать Caddyfile и перезапустить сервис."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = naiveproxy_manager.apply_server_config()
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Error in naive_apply: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def naive_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /naive_export — экспорт клиента NaiveProxy."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
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
            await update.message.reply_text(f"Ошибка: {e}")

    # =====================================================================
    # === TUIC COMMANDS ===
    # =====================================================================

    async def tuic_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /tuic_status — показать статус TUIC."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return

            status = tuic_manager.get_status()
            esc = self._escape_md2
            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            message = f"""🔷 *TUIC Статус*

*Состояние:* {status_emoji} {"Включён" if status["enabled"] else "Выключен"}
*Конфигурация:* {config_emoji} {"Настроена" if status["configured"] else "Не настроена"}

*Параметры:*
• Сервер: `{esc(status.get("server"))}`
• Порт: `{esc(status.get("port", 443))}` \\(UDP\\)
• SNI: `{esc(status.get("sni") or "(авто)")}`
• Insecure: {"да ⚠️" if status.get("insecure") else "нет ✅"}
• Congestion: `{esc(status.get("congestion_control", "bbr"))}`
• UDP relay: `{esc(status.get("udp_relay_mode", "native"))}`

*Клиентов:* {status.get("clients_count", 0)}
*Обновлено:* {esc(status.get("updated_at", "никогда"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Error in tuic_status: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = tuic_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = tuic_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            config = tuic_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"🔷 Конфигурация TUIC:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_set_server(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = tuic_manager.set_server(server)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /tuic_set_port <порт>")
                return
            port = int(args[0])
            success, message = tuic_manager.set_port(port)
            await update.message.reply_text(message)
        except ValueError:
            await update.message.reply_text("❌ Порт должен быть числом")
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_set_cc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /tuic_set_cc <bbr|cubic|new_reno>."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /tuic_set_cc <bbr|cubic|new_reno>"
                )
                return
            success, message = tuic_manager.set_congestion_control(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_gen_cert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = tuic_manager.generate_self_signed_cert()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, results, message = tuic_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_add_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /tuic_add <name>."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /tuic_add <имя>")
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /tuic_qr <имя_клиента>")
                return
            await self._reply_tuic_qr(update.message, args[0])
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_del_client(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /tuic_del <имя>")
                return
            success, message = tuic_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            clients = tuic_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 Клиентов TUIC нет")
                return
            esc = self._escape_md2
            lines = ["🔷 *Клиенты TUIC:*\n"]
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = tuic_manager.apply_config()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = tuic_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = tuic_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = tuic_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = tuic_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 Логи TUIC:\n```\n{output}\n```", parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def tuic_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /tuic_export — экспорт конфигураций."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
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
            await update.message.reply_text(f"Ошибка: {e}")

    # =====================================================================
    # === ANYTLS COMMANDS ===
    # =====================================================================

    async def anytls_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /anytls_status — показать статус AnyTLS."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return

            status = anytls_manager.get_status()
            esc = self._escape_md2
            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            message = f"""🔶 *AnyTLS Статус*

*Состояние:* {status_emoji} {"Включён" if status["enabled"] else "Выключен"}
*Конфигурация:* {config_emoji} {"Настроена" if status["configured"] else "Не настроена"}

*Параметры:*
• Сервер: `{esc(status.get("server"))}`
• Порт: `{esc(status.get("port", 443))}` \\(TCP\\)
• SNI: `{esc(status.get("sni") or "(авто)")}`
• Insecure: {"да ⚠️" if status.get("insecure") else "нет ✅"}

*Клиентов:* {status.get("clients_count", 0)}
*Обновлено:* {esc(status.get("updated_at", "никогда"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = anytls_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = anytls_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            config = anytls_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"🔶 Конфигурация AnyTLS:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_set_server(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = anytls_manager.set_server(server)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /anytls_set_port <порт>"
                )
                return
            port = int(args[0])
            success, message = anytls_manager.set_port(port)
            await update.message.reply_text(message)
        except ValueError:
            await update.message.reply_text("❌ Порт должен быть числом")
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_gen_cert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = anytls_manager.generate_self_signed_cert()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, results, message = anytls_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_add_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /anytls_add <name>."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /anytls_add <имя>")
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /anytls_qr <имя_клиента>"
                )
                return
            await self._reply_anytls_qr(update.message, args[0])
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_del_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /anytls_del <имя>")
                return
            success, message = anytls_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            clients = anytls_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 Клиентов AnyTLS нет")
                return
            esc = self._escape_md2
            lines = ["🔶 *Клиенты AnyTLS:*\n"]
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                pw = c.get("password", "")
                masked = f"{pw[:4]}..." if len(pw) > 4 else "***"
                created = c.get("created_at", "")[:10]
                lines.append(
                    f"{i}\\. `{esc(name)}` — пароль: `{esc(masked)}` \\({esc(created)}\\)"
                )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = anytls_manager.apply_config()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = anytls_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = anytls_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = anytls_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = anytls_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 Логи AnyTLS:\n```\n{output}\n```", parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def anytls_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
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
            await update.message.reply_text(f"Ошибка: {e}")

    # =====================================================================
    # === XHTTP COMMANDS ===
    # =====================================================================

    async def xhttp_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /xhttp_status — показать статус XHTTP."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return

            status = xhttp_manager.get_status()
            esc = self._escape_md2
            status_emoji = "🟢" if status["enabled"] else "🔴"
            config_emoji = "✅" if status["configured"] else "❌"

            message = f"""🌐 *XHTTP Статус*

*Состояние:* {status_emoji} {"Включён" if status["enabled"] else "Выключен"}
*Конфигурация:* {config_emoji} {"Настроена" if status["configured"] else "Не настроена"}

*Параметры:*
• Сервер: `{esc(status.get("server"))}`
• Порт: `{esc(status.get("port", 443))}` \\(TCP\\)
• Path: `{esc(status.get("path", "/"))}`
• Host: `{esc(status.get("host") or "(пусто)")}`
• Mode: `{esc(status.get("mode", "auto"))}`
• Security: `{esc(status.get("security", "tls"))}`
• SNI: `{esc(status.get("sni") or "(авто)")}`
• Insecure: {"да ⚠️" if status.get("insecure") else "нет ✅"}

*Клиентов:* {status.get("clients_count", 0)}
*Обновлено:* {esc(status.get("updated_at", "никогда"))}"""

            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = xhttp_manager.enable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = xhttp_manager.disable()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            config = xhttp_manager.get_config(include_secrets=False)
            config_str = json.dumps(config, ensure_ascii=False, indent=2)
            await update.message.reply_text(
                f"🌐 Конфигурация XHTTP:\n```json\n{config_str}\n```",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_set_server(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            server = args[0] if args else None
            success, message = xhttp_manager.set_server(server)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /xhttp_set_port <порт>")
                return
            port = int(args[0])
            success, message = xhttp_manager.set_port(port)
            await update.message.reply_text(message)
        except ValueError:
            await update.message.reply_text("❌ Порт должен быть числом")
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_set_path(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /xhttp_set_path <path>")
                return
            success, message = xhttp_manager.set_path(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_set_host(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            host = args[0] if args else ""
            success, message = xhttp_manager.set_host(host)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_set_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /xhttp_set_mode <auto|packet-up|stream-up>"
                )
                return
            success, message = xhttp_manager.set_mode(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_gen_cert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = xhttp_manager.generate_self_signed_cert()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_gen_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, results, message = xhttp_manager.generate_all()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_add_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Команда /xhttp_add <name>."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /xhttp_add <имя>")
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /xhttp_qr <имя_клиента>"
                )
                return
            await self._reply_xhttp_qr(update.message, args[0])
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_del_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if await self._legacy_per_client_guard(update):
                return
            args = context.args or []
            if not args:
                await update.message.reply_text("Использование: /xhttp_del <имя>")
                return
            success, message = xhttp_manager.remove_client(args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            clients = xhttp_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 Клиентов XHTTP нет")
                return
            esc = self._escape_md2
            lines = ["🌐 *Клиенты XHTTP:*\n"]
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = xhttp_manager.apply_config()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = xhttp_manager.service_control("start")
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = xhttp_manager.service_control("stop")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = xhttp_manager.service_control("restart")
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            lines_count = int(args[0]) if args else 30
            success, output = xhttp_manager.get_logs(lines_count)
            await update.message.reply_text(
                f"📋 Логи XHTTP:\n```\n{output}\n```", parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def xhttp_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
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
            await update.message.reply_text(f"Ошибка: {e}")

    # =====================================================================
    # === MIERU COMMANDS ===
    # =====================================================================

    async def mieru_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mieru_status — состояние Mieru (mita)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            status = mieru_manager.get_status()
            enabled = "🟢 включен" if status.get("enabled") else "🔴 выключен"
            configured = "да" if status.get("configured") else "нет"
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
                f"Состояние: {enabled}\n"
                f"Сконфигурирован: {configured}\n"
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
                f"Клиентов: {status.get('clients_count', 0)}"
            )
            await update.message.reply_text(text)
        except Exception as e:
            logger.error(f"Error in mieru_status: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mieru_config — текущий конфиг Mieru без секретов (по умолчанию)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            config = mieru_manager.get_config(
                include_secrets=self._secret_reveal_allowed()
            )
            payload = json.dumps(config, ensure_ascii=False, indent=2)
            await self._reply_export_file(
                update.message,
                payload,
                "mieru-config.json",
                "🛰 Mieru config (секреты маскированы)",
            )
        except Exception as e:
            logger.error(f"Error in mieru_config: {e}")
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_set_server(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /mieru_set_server <ip_or_domain>"
                )
                return
            success, message = mieru_manager.set_server(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_set_port(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if not args:
                await update.message.reply_text(
                    "Использование: /mieru_set_port <port> [tcp|udp]"
                )
                return
            try:
                port = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ port должен быть числом")
                return
            protocol = args[1] if len(args) > 1 else "tcp"
            success, message = mieru_manager.set_port(port, protocol)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_set_mtu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /mieru_set_mtu <1280..1500>"
                )
                return
            try:
                mtu = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ mtu должен быть числом")
                return
            success, message = mieru_manager.set_mtu(mtu)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_set_multiplexing(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /mieru_set_multiplexing <off|low|middle|high>"
                )
                return
            success, message = mieru_manager.set_multiplexing(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_set_handshake(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /mieru_set_handshake <standard|no_wait>"
                )
                return
            success, message = mieru_manager.set_handshake_mode(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_set_socks5_port(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /mieru_set_socks5_port <port>"
                )
                return
            try:
                port = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ port должен быть числом")
                return
            success, message = mieru_manager.set_socks5_port(port)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_gen_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Сгенерировать password (для ручного использования или add_client)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            password = mieru_manager.generate_password()
            await update.message.reply_text(
                "✅ Сгенерирован пароль (использовать вручную):\n"
                f"`{password}`\n\n"
                "Чтобы добавить клиента сразу с автогенерацией: /mieru_add_client <name>",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_add_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /mieru_add_client <имя>"
                )
                return
            name = context.args[0]
            success, message, client = mieru_manager.add_client(name)
            if success and client:
                masked = self._mask_secret(client.get("password", ""))
                message += f"\npassword: {masked}\nдля выдачи: /mieru_export {name}"
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_list_clients(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            clients = mieru_manager.list_clients()
            if not clients:
                await update.message.reply_text("📋 Клиентов Mieru нет")
                return
            lines = ["🛰 Клиенты Mieru:"]
            for i, c in enumerate(clients, 1):
                name = c.get("name", "?")
                owner = c.get("owner_id", "—")
                created = (c.get("created_at") or "")[:10]
                lines.append(f"{i}. {name}  owner={owner}  created={created}")
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_del_client(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            if not context.args:
                await update.message.reply_text(
                    "Использование: /mieru_del_client <имя>"
                )
                return
            success, message = mieru_manager.delete_client(context.args[0])
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_install(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            await update.message.reply_text("⏳ Установка Mieru (mita)…")
            success, message = mieru_manager.install_mieru()
            await update.message.reply_text(
                "✅ Установка завершена"
                if success
                else "❌ Установка завершилась с ошибкой"
            )
            await self._reply_export_file(
                update.message,
                message,
                "mieru-install.log",
                "Mieru install output",
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_apply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mieru_apply [reload] — применить серверный config; reload = только users/logging."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            reload_only = bool(args) and args[0].strip().lower() in {"reload", "soft"}
            success, message = mieru_manager.apply_server_config(
                reload_only=reload_only
            )
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mieru_manager.start()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mieru_manager.stop()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            success, message = mieru_manager.restart()
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
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
                f"🛰 Mieru logs (последние {n} строк)",
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mieru_export [name] — выдать client config + mierus:// + Clash + aping-profile."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            name = args[0] if args else None
            if not name:
                clients = mieru_manager.list_clients()
                if not clients:
                    await update.message.reply_text(
                        "❌ Нет клиентов. /mieru_add_client <имя>"
                    )
                    return
                name = clients[0].get("name", "")
                await update.message.reply_text(
                    f"ℹ️ Имя не указано — экспортирую первого: {name}"
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
            await update.message.reply_text(f"Ошибка: {e}")

    async def mieru_set_dpi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mieru_set_dpi <param> <value> — единая ручка для DPI-параметров (plan §10)."""
        try:
            if not self._is_admin(update.effective_user.id):
                await update.message.reply_text("⛔ Только для администратора.")
                return
            args = context.args or []
            if len(args) < 2:
                await update.message.reply_text(
                    "Использование: /mieru_set_dpi <param> <value>\n\n"
                    "Параметры:\n"
                    "protocol=tcp|udp\n"
                    "port=<1025..65535>\n"
                    "port_range=<from>-<to>\n"
                    "mtu=<1280..1500>\n"
                    "multiplexing=off|low|middle|high\n"
                    "handshake=standard|no_wait\n"
                    "socks5_port=<1025..65535>\n"
                    "logging=debug|info|warn|error\n\n"
                    "После изменения port/MTU/protocol/multiplexing/handshake "
                    "нужен /mieru_apply и заново /mieru_export."
                )
                return
            param = args[0]
            value = " ".join(args[1:])
            success, message = mieru_manager.set_dpi_param(param, value)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ошибок."""
        logger.error(f"Update {update} caused error {context.error}")

        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Произошла ошибка при обработке команды. Попробуйте позже."
            )
