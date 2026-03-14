"""
utils/helpers.py — Miscellaneous utility functions shared across handlers.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Optional

from telegram import Message, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def escape_html(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return html.escape(text)


def format_datetime(dt: Optional[datetime]) -> str:
    """Format a datetime for display. Returns 'N/A' for None."""
    if dt is None:
        return "N/A"
    # If tz-naive, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def truncate(text: str, max_len: int = 50, suffix: str = "…") -> str:
    """Truncate a string to max_len characters, adding suffix if cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split a long message into Telegram-safe chunks.

    Attempts to split on newline boundaries where possible.
    """
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


async def safe_edit_or_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> Optional[Message]:
    """Edit the existing message if from callback, else send a new message.

    This is a convenience wrapper to avoid duplicating edit/reply logic in
    every callback handler.
    """
    try:
        if update.callback_query:
            await update.callback_query.answer()
            return await update.callback_query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        elif update.message:
            return await update.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
    except Exception as exc:
        logger.warning("safe_edit_or_reply failed: %s", exc)
        # Fallback: try sending a new message
        if update.effective_chat:
            try:
                return await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
            except Exception as inner_exc:
                logger.error("Fallback send_message also failed: %s", inner_exc)
    return None


def user_display(user) -> str:
    """Return a short display string for a Telegram user."""
    if user.username:
        return f"@{user.username}"
    return f"{user.first_name or 'User'} (ID: {user.id})"
