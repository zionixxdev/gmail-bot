"""
utils/decorators.py — Reusable async decorators for handler functions.

Available decorators:
  @require_joined    — Enforce channel membership before command executes.
  @admin_only        — Restrict handler to ADMIN_IDS.
  @rate_limit        — Per-user rate limiting using TTLCache.
  @not_banned        — Block banned users from interacting.
"""

from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Callable

from cachetools import TTLCache
from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS, FORCE_JOIN_CACHE_TTL, REQUIRED_CHANNELS
from utils.keyboards import force_join_keyboard

logger = logging.getLogger(__name__)

# ─── Caches ──────────────────────────────────────────────────────────────────

# Cache for force-join checks: key = (user_id, channel) → True
_join_cache: TTLCache = TTLCache(maxsize=10_000, ttl=FORCE_JOIN_CACHE_TTL)

# Cache for rate limiting: key = (user_id, action) → call count
_rate_cache: TTLCache = TTLCache(maxsize=50_000, ttl=60)  # 1-minute window


# ─── Force join ──────────────────────────────────────────────────────────────

def require_joined(func: Callable) -> Callable:
    """Decorator: check user has joined all REQUIRED_CHANNELS.

    If any check fails, send a message with join buttons and abort the handler.
    Results are cached per user for FORCE_JOIN_CACHE_TTL seconds.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or not REQUIRED_CHANNELS:
            return await func(update, context, *args, **kwargs)

        missing = []
        for channel in REQUIRED_CHANNELS:
            cache_key = (user.id, channel)
            if cache_key in _join_cache:
                continue
            try:
                member = await context.bot.get_chat_member(channel, user.id)
                if member.status in ("member", "administrator", "creator"):
                    _join_cache[cache_key] = True
                else:
                    missing.append(channel)
            except Exception as exc:
                logger.warning("Could not check membership for %s: %s", channel, exc)

        if missing:
            msg = (
                "🚫 <b>Please join our channel(s) to use this bot:</b>\n\n"
                + "\n".join(f"• {ch}" for ch in missing)
            )
            if update.callback_query:
                await update.callback_query.answer("You must join the required channels first.", show_alert=True)
                await update.callback_query.message.reply_text(
                    msg, parse_mode="HTML", reply_markup=force_join_keyboard(missing)
                )
            else:
                message = update.message or (update.callback_query and update.callback_query.message)
                if message:
                    await message.reply_text(
                        msg, parse_mode="HTML", reply_markup=force_join_keyboard(missing)
                    )
            return None

        return await func(update, context, *args, **kwargs)

    return wrapper


# ─── Admin only ──────────────────────────────────────────────────────────────

def admin_only(func: Callable) -> Callable:
    """Decorator: allow execution only for users in ADMIN_IDS."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            msg = "⛔ You don't have permission to use this command."
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.message:
                await update.message.reply_text(msg)
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper


# ─── Rate limiting ───────────────────────────────────────────────────────────

def rate_limit(max_calls: int = 5, action: str = "default"):
    """Decorator factory: limit a user to max_calls per 60 seconds for a given action.

    Args:
        max_calls: Maximum allowed calls per minute.
        action:    Identifier for the action being rate-limited.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user = update.effective_user
            if not user:
                return await func(update, context, *args, **kwargs)

            cache_key = (user.id, action)
            count = _rate_cache.get(cache_key, 0)
            if count >= max_calls:
                msg = f"⏳ You're going too fast. Please wait a moment before using this feature again."
                if update.callback_query:
                    await update.callback_query.answer(msg, show_alert=True)
                elif update.message:
                    await update.message.reply_text(msg)
                return None

            _rate_cache[cache_key] = count + 1
            return await func(update, context, *args, **kwargs)

        return wrapper
    return decorator


# ─── Ban check ───────────────────────────────────────────────────────────────

def not_banned(func: Callable) -> Callable:
    """Decorator: silently drop updates from banned users."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        from services.database import get_user_by_telegram_id
        user = update.effective_user
        if user:
            db_user = await get_user_by_telegram_id(user.id)
            if db_user and db_user.is_banned:
                if update.message:
                    await update.message.reply_text("🚫 Your account has been suspended. Contact support.")
                elif update.callback_query:
                    await update.callback_query.answer("🚫 Your account has been suspended.", show_alert=True)
                return None
        return await func(update, context, *args, **kwargs)

    return wrapper
