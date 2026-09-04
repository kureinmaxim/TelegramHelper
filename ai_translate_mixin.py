# -*- coding: utf-8 -*-
"""AI chat, translation, and prompt-template commands restored from TelegramHelper v2."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from utils import (
    ANTHROPIC_AVAILABLE,
    OPENAI_AVAILABLE,
    TRANSLATOR_AVAILABLE,
    get_ai_completion,
    get_ai_translation,
    get_language_name,
    get_prompt_categories,
    load_prompt_templates,
    render_prompt,
    translate_text,
)

logger = logging.getLogger(__name__)

_LANG_BUTTONS = {"RU": "ru", "EN": "en", "FR": "fr"}


class AITranslateMixin:
    """Mixin with /ai, /tr, /prompt and translation keyboard handlers."""

    def _translation_source_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
        args = context.args or []
        message = update.effective_message
        if message and message.reply_to_message and message.reply_to_message.text and not args:
            return message.reply_to_message.text.strip()
        if args:
            return " ".join(args).strip()
        if context.chat_data:
            stored = context.chat_data.get("last_non_button_message")
            if stored and stored.strip().upper() not in _LANG_BUTTONS:
                return stored.strip()
        return None

    async def remember_chat_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Remember the last non-command text for /tr and RU/EN/FR keyboard."""
        message = update.effective_message
        if not message or not message.text:
            return
        text = message.text.strip()
        if not text or text.startswith("/"):
            return
        if text.upper() in _LANG_BUTTONS:
            await self.translate_keyboard_click(update, context)
            return
        context.chat_data["last_non_button_message"] = text
        context.chat_data["last_message"] = text

    async def id_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await update.message.reply_text(f"Your Telegram ID: `{user.id}`", parse_mode=ParseMode.MARKDOWN)

    async def ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not OPENAI_AVAILABLE and not ANTHROPIC_AVAILABLE:
            await update.message.reply_text(
                "AI is not available. Install: pip install openai anthropic"
            )
            return
        query = self._translation_source_text(update, context)
        if not query:
            await update.message.reply_text("Usage: /ai <question> or reply to a message with /ai")
            return
        processing = await update.message.reply_text("Generating an answer...")
        try:
            response = get_ai_completion(query)
            if not response:
                await processing.edit_text("Could not get an AI response. Check API keys.")
                return
            await processing.edit_text(f"AI:\n\n{response}")
        except Exception as exc:
            logger.error("ai_command failed: %s", exc)
            await processing.edit_text("Sorry, the AI request failed.")

    async def translate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not TRANSLATOR_AVAILABLE:
            await update.message.reply_text(
                "Translator is not available. Install: pip install deep-translator"
            )
            return
        text = self._translation_source_text(update, context)
        if not text:
            await update.message.reply_text(
                "Translator\n\n"
                "Send /tr <text>, reply to a message with /tr, "
                "or use /ru /en /fr for a direct translation."
            )
            return
        user_id = update.effective_user.id
        context.user_data[f"translate_text_{user_id}"] = text
        keyboard = [
            [
                InlineKeyboardButton("RU→EN", callback_data=f"tr_direction_ru_en_{user_id}"),
                InlineKeyboardButton("EN→RU", callback_data=f"tr_direction_en_ru_{user_id}"),
            ],
            [
                InlineKeyboardButton("RU→FR", callback_data=f"tr_direction_ru_fr_{user_id}"),
                InlineKeyboardButton("FR→RU", callback_data=f"tr_direction_fr_ru_{user_id}"),
            ],
            [
                InlineKeyboardButton("EN→FR", callback_data=f"tr_direction_en_fr_{user_id}"),
                InlineKeyboardButton("FR→EN", callback_data=f"tr_direction_fr_en_{user_id}"),
            ],
        ]
        preview = text if len(text) <= 200 else text[:200] + "..."
        await update.message.reply_text(
            f"Choose a direction for:\n\n{preview}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def translate_to_lang(self, update: Update, context: ContextTypes.DEFAULT_TYPE, target_lang: str):
        if not TRANSLATOR_AVAILABLE:
            await update.message.reply_text(
                "Translator is not available. Install: pip install deep-translator"
            )
            return
        text = self._translation_source_text(update, context)
        if not text:
            await update.message.reply_text(
                f"Usage: /{target_lang} <text> or reply to a message with /{target_lang}"
            )
            return
        try:
            translated, src, dst = translate_text(text, target_lang)
            src_name = get_language_name(src)
            dst_name = get_language_name(dst)
            await update.message.reply_text(f"{src_name} → {dst_name}\n\n{translated}")
        except Exception as exc:
            logger.error("translate_to_lang failed: %s", exc)
            await update.message.reply_text("Translation failed.")

    async def translate_ru(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.translate_to_lang(update, context, "ru")

    async def translate_en(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.translate_to_lang(update, context, "en")

    async def translate_fr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.translate_to_lang(update, context, "fr")

    async def translate_ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not OPENAI_AVAILABLE and not ANTHROPIC_AVAILABLE:
            await update.message.reply_text("AI translation needs openai or anthropic.")
            return
        text = self._translation_source_text(update, context)
        if not text:
            await update.message.reply_text("Usage: /tr_ai <text> or reply with /tr_ai")
            return
        processing = await update.message.reply_text("Translating with AI...")
        try:
            result = get_ai_translation(text)
            if not result:
                await processing.edit_text("AI translation failed. Check API keys.")
                return
            await processing.edit_text(result)
        except Exception as exc:
            logger.error("translate_ai_command failed: %s", exc)
            await processing.edit_text("AI translation failed.")

    async def prompt_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        categories = get_prompt_categories()
        keyboard = [
            [InlineKeyboardButton(cat, callback_data=f"prompt_{cat}")] for cat in categories
        ]
        source = None
        if update.message.reply_to_message and update.message.reply_to_message.text:
            source = update.message.reply_to_message.text
        elif context.chat_data.get("last_non_button_message"):
            source = context.chat_data.get("last_non_button_message")
        if source:
            context.user_data["prompt_input"] = source
            preview = source if len(source) <= 80 else source[:80] + "..."
            text = f"Prompt generator\n\nChoose a category for:\n{preview}"
        else:
            text = (
                "Prompt generator\n\n"
                "Reply to a message with /prompt, or send text first and then /prompt."
            )
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def prompt_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        if len(args) != 1:
            await update.message.reply_text("Usage: /prompt_show <category>")
            return
        templates = load_prompt_templates()
        meta = templates.get(args[0])
        if not meta:
            await update.message.reply_text("Unknown category.")
            return
        await update.message.reply_text(
            f"Category: {meta.get('title', args[0])}\n\nTemplate:\n{meta.get('template', '{input}')}"
        )

    async def help_prompt_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "How to use /prompt\n\n"
            "1. Send or reply to the text you want structured.\n"
            "2. Run /prompt and pick a category.\n"
            "3. The bot builds a ready-made prompt you can send to /ai.\n\n"
            "Show a template: /prompt_show science"
        )

    async def kb_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [["RU", "EN", "FR"]]
        await update.message.reply_text(
            "Translation keyboard is on. Send text, then tap RU / EN / FR.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        )

    async def kb_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Translation keyboard hidden.",
            reply_markup=ReplyKeyboardRemove(),
        )

    async def kb_translate(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.kb_on(update, context)

    async def kb_hide(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.kb_off(update, context)

    async def translate_keyboard_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip().upper()
        target = _LANG_BUTTONS.get(text)
        if not target:
            return
        source = None
        if context.chat_data:
            source = context.chat_data.get("last_non_button_message")
        if not source:
            await update.message.reply_text("Send some text first, then tap RU / EN / FR.")
            return
        try:
            translated, src, dst = translate_text(source, target)
            await update.message.reply_text(
                f"{get_language_name(src)} → {get_language_name(dst)}\n\n{translated}"
            )
        except Exception as exc:
            logger.error("keyboard translate failed: %s", exc)
            await update.message.reply_text("Translation failed.")

    async def _handle_ai_translate_callbacks(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query, data: str) -> bool:
        """Public callbacks for translation direction and prompt categories."""
        if data.startswith("tr_direction_"):
            parts = data.split("_")
            # tr_direction_<src>_<dst>_<userid>
            if len(parts) < 5:
                return True
            source_lang, target_lang, owner = parts[2], parts[3], parts[4]
            if str(query.from_user.id) != owner:
                await query.message.reply_text("This translation button belongs to another user.")
                return True
            stored = context.user_data.get(f"translate_text_{query.from_user.id}")
            if not stored:
                await query.edit_message_text("Nothing to translate. Send /tr again.")
                return True
            try:
                translated, src, dst = translate_text(stored, target_lang, source_lang)
                await query.edit_message_text(
                    f"{get_language_name(src)} → {get_language_name(dst)}\n\n{translated}"
                )
            except Exception as exc:
                logger.error("tr_direction callback failed: %s", exc)
                await query.edit_message_text("Translation failed.")
            return True

        if data.startswith("prompt_"):
            category = data[len("prompt_") :]
            source = context.user_data.get("prompt_input") or (
                context.chat_data.get("last_non_button_message") if context.chat_data else None
            )
            if not source:
                await query.edit_message_text(
                    "No source text. Reply to a message with /prompt first."
                )
                return True
            rendered = render_prompt(category, source)
            if not rendered:
                await query.edit_message_text("Unknown prompt category.")
                return True
            await query.edit_message_text(f"Ready prompt ({category}):\n\n{rendered}")
            return True

        return False
