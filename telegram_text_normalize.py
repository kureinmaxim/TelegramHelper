# -*- coding: utf-8 -*-
"""
Нормализация вставленного из /help текста команд (iOS / копипаст).

Telegram на части клиентов отправляет «чёрное» сообщение без сущности
MessageEntity.BOT_COMMAND — тогда CommandHandler в python-telegram-bot
(фильтр filters.COMMAND) не вызывается. Невидимые символы, U+2044 FRACTION SLASH ⁄ («плоский» слэш), NFKC, блок code/pre
из /help (iOS): без BOT_COMMAND CommandHandler в PTB не вызывается.

Обработчик из bot.py вешается в group=-1 и только правит text/entities
до остальных хендлеров.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import TYPE_CHECKING

from telegram import MessageEntity, Update
from telegram.constants import MessageEntityType

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Визуальные «двойники» SOLIDUS (U+002F): из /help с типографикой и с iPhone
# часто прилетает U+2044 FRACTION SLASH ⁄ — выглядит «положе» обычного /.
_SLASH_ALIASES: tuple[str, ...] = (
    "\uff0f",  # U+FF0F FULLWIDTH SOLIDUS ／
    "\u2215",  # U+2215 DIVISION SLASH ∕
    "\u29f8",  # U+29F8 BIG SOLIDUS ⧸
    "\u2044",  # U+2044 FRACTION SLASH ⁄ (типичный «не тот» слэш из Rich/интерфейсов)
    "\u2571",  # U+2571 BOX DRAWINGS LIGHT DIAGONAL ╱
)

# Невидимые / форматирующие, часто протаскиваются с iPhone в буфер обмена
_STRIP_INVISIBLE = (
    "\ufeff",  # BOM
    "\u200b",  # zero-width space
    "\u200c",  # ZWNJ
    "\u200d",  # ZWJ
    "\u2060",  # word joiner
    "\u00ad",  # soft hyphen
    "\u200e",  # LRM
    "\u200f",  # RLM
)

# Префикс команды: /name или /name@bot (как в Telegram)
_CMD_PREFIX_RE = re.compile(r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?")


def _normalize_first_token_solidus(text: str) -> str:
    """Заменяет «не тот» слэш только в первом токене; NFKC сворачивает ⁄／ к /."""
    if not text:
        return text
    split = text.split(maxsplit=1)
    first, rest = split[0], split[1] if len(split) > 1 else ""
    for ch in _SLASH_ALIASES:
        first = first.replace(ch, "/")
    try:
        first = unicodedata.normalize("NFKC", first)
    except Exception:
        pass
    return first + (" " + rest if rest else "")


def _command_prefix_length(text: str) -> int:
    """Длина первого токена-команды (с учётом @bot), без хвостовых аргументов."""
    if not text:
        return 0
    m = _CMD_PREFIX_RE.match(text)
    return m.end() if m else 0


def normalize_pasted_command_text(text: str) -> str:
    """Убирает невидимые символы, приводит слэш к ASCII, поджимает пробелы у /command."""
    if not text:
        return text
    t = text
    for ch in _STRIP_INVISIBLE:
        t = t.replace(ch, "")
    t = t.strip()
    if not t:
        return t
    # Слэш — только в первом слове (команда), чтобы не трогать дроби в хвосте сообщения
    t = _normalize_first_token_solidus(t)
    if t.startswith("/"):
        # "/   cmd" -> "/cmd" только в начале; аргументы после первого пробела не трогаем
        head, sep, tail = t.partition(" ")
        head_clean = "/" + head.lstrip("/").strip()
        t = head_clean + (sep + tail if sep else "")
    return t


def _set_message_text(message, new_text: str) -> None:
    try:
        message.text = new_text
    except (AttributeError, TypeError):
        object.__setattr__(message, "text", new_text)


def _set_message_entities(message, entities: tuple[MessageEntity, ...]) -> None:
    try:
        message.entities = entities
    except (AttributeError, TypeError):
        object.__setattr__(message, "entities", entities)


def _ensure_bot_command_entity(message) -> None:
    """Добавляет или поправляет BOT_COMMAND у offset=0, если это безопасно."""
    text = message.text
    if not text or not text.startswith("/"):
        return

    prefix_len = _command_prefix_length(text)
    if prefix_len <= 0:
        return

    raw_entities = list(message.entities) if message.entities else []

    if raw_entities:
        first = raw_entities[0]
        if first.type == MessageEntityType.BOT_COMMAND and first.offset == 0:
            if first.length != prefix_len:
                raw_entities[0] = MessageEntity(
                    MessageEntityType.BOT_COMMAND, 0, prefix_len
                )
                _set_message_entities(message, tuple(raw_entities))
            return
        # Одна сущность code/pre на всё сообщение — копирование из /help на iOS (команда без BOT_COMMAND)
        if (
            len(raw_entities) == 1
            and first.offset == 0
            and first.type in (MessageEntityType.CODE, MessageEntityType.PRE)
            and first.length >= prefix_len
        ):
            _set_message_entities(
                message,
                (MessageEntity(MessageEntityType.BOT_COMMAND, 0, prefix_len),),
            )
            return
        # Уже есть другая сущность с offset 0 (bold и т.д.) — не порти оффсеты
        if first.offset == 0:
            return
        raw_entities.insert(
            0,
            MessageEntity(MessageEntityType.BOT_COMMAND, 0, prefix_len),
        )
        _set_message_entities(message, tuple(raw_entities))
        return

    _set_message_entities(
        message,
        (MessageEntity(MessageEntityType.BOT_COMMAND, 0, prefix_len),),
    )


async def normalize_pasted_command_update(
    update: Update, _context: "ContextTypes.DEFAULT_TYPE"
) -> None:
    """MessageHandler (group=-1): починить text + entities для CommandHandler."""
    message = update.effective_message
    if message is None or message.text is None:
        return

    original = message.text
    normalized = normalize_pasted_command_text(original)
    if normalized != original:
        _set_message_text(message, normalized)
        # offset'ы entities относились к старой строке — сбросить, иначе PTB путается
        _set_message_entities(message, ())
        logger.debug("Normalized pasted command text: %r -> %r", original, normalized)

    # Главный фикc для iOS: нет BOT_COMMAND → CommandHandler молчит
    _ensure_bot_command_entity(message)
