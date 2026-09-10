# -*- coding: utf-8 -*-
"""
Normalize command text pasted from /help (iOS / copy-paste).

On some clients Telegram sends a "plain" message without a
MessageEntity.BOT_COMMAND entity — then CommandHandler in python-telegram-bot
(filters.COMMAND) never fires. Invisible characters, U+2044 FRACTION SLASH ⁄
("flat" slash), NFKC, and a code/pre block from /help (iOS): without
BOT_COMMAND, CommandHandler in PTB is not invoked.

The handler from bot.py is registered in group=-1 and only fixes text/entities
before the other handlers run.
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

# Visual lookalikes of SOLIDUS (U+002F): /help typography and iPhone paste
# often send U+2044 FRACTION SLASH ⁄ — looks "flatter" than a normal /.
_SLASH_ALIASES: tuple[str, ...] = (
    "\uff0f",  # U+FF0F FULLWIDTH SOLIDUS ／
    "\u2215",  # U+2215 DIVISION SLASH ∕
    "\u29f8",  # U+29F8 BIG SOLIDUS ⧸
    "\u2044",  # U+2044 FRACTION SLASH ⁄ (typical wrong slash from Rich/UIs)
    "\u2571",  # U+2571 BOX DRAWINGS LIGHT DIAGONAL ╱
)

# Invisible / formatting chars often carried over from the iPhone clipboard
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

# Command prefix: /name or /name@bot (as in Telegram)
_CMD_PREFIX_RE = re.compile(r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?")


def _normalize_first_token_solidus(text: str) -> str:
    """Replace a lookalike slash only in the first token; NFKC folds ⁄／ to /."""
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
    """Length of the first command token (including @bot), without trailing args."""
    if not text:
        return 0
    m = _CMD_PREFIX_RE.match(text)
    return m.end() if m else 0


def normalize_pasted_command_text(text: str) -> str:
    """Strip invisible chars, normalize the slash to ASCII, tighten spaces on /command."""
    if not text:
        return text
    t = text
    for ch in _STRIP_INVISIBLE:
        t = t.replace(ch, "")
    t = t.strip()
    if not t:
        return t
    # Slash — only in the first word (the command), so fractions later in the message stay intact
    t = _normalize_first_token_solidus(t)
    if t.startswith("/"):
        # "/   cmd" -> "/cmd" only at the start; args after the first space are left as-is
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
    """Add or fix BOT_COMMAND at offset=0 when it is safe to do so."""
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
        # Single code/pre entity on the whole message — paste from /help on iOS (command without BOT_COMMAND)
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
        # Another entity already starts at offset 0 (bold, etc.) — do not shift offsets
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
    """MessageHandler (group=-1): fix text + entities for CommandHandler."""
    message = update.effective_message
    if message is None or message.text is None:
        return

    original = message.text
    normalized = normalize_pasted_command_text(original)
    if normalized != original:
        _set_message_text(message, normalized)
        # Entity offsets belonged to the old string — reset them or PTB gets confused
        _set_message_entities(message, ())
        logger.debug("Normalized pasted command text: %r -> %r", original, normalized)

    # Main iOS fix: no BOT_COMMAND → CommandHandler stays silent
    _ensure_bot_command_entity(message)
