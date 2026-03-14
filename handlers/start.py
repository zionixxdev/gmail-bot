"""
handlers/start.py — /start command and force-join re-check callback.

/start:
  1. Register or update the user in the database.
  2. (Force join is checked via the @require_joined decorator on most handlers,
     but /start itself is left open so the user sees a welcome message with join links
     if applicable.)
  3. Show the main menu.

callback `forcejoin:check`:
  Re-check channel membership and proceed to the main menu if all joined.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from config import BOT_NAME, REQUIRED_CHANNELS, SUPPORT_USERNAME
from services.database import get_or_create_user
from utils.keyboards import force_join_keyboard, main_menu_keyboard
from utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)


# ─── /start ──────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — register user, optionally check force join, show menu."""
    tg_user = update.effective_user
    if tg_user is None:
        return

    # Upsert user
    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )

    # Check if user must join channels
    if REQUIRED_CHANNELS:
        missing = []
        for channel in REQUIRED_CHANNELS:
            try:
                member = await context.bot.get_chat_member(channel, tg_user.id)
                if member.status not in ("member", "administrator", "creator"):
                    missing.append(channel)
            except Exception as exc:
                logger.warning("Force join check failed for %s: %s", channel, exc)

        if missing:
            await update.message.reply_text(
                f"👋 Welcome to <b>{BOT_NAME}</b>!\n\n"
                f"To use this bot, please join our channel(s) first:",
                parse_mode="HTML",
                reply_markup=force_join_keyboard(missing),
            )
            return

    welcome = (
        f"👋 Welcome to <b>{BOT_NAME}</b>, {tg_user.first_name or 'there'}!\n\n"
        f"🔗 Link your Gmail accounts and manage your inbox right from Telegram.\n\n"
        f"💰 <b>Credits:</b> {db_user.credits}\n"
        f"📬 <b>Linked accounts:</b> Use the menu below to get started.\n\n"
        f"Need help? Contact {SUPPORT_USERNAME}"
    )

    await update.message.reply_text(
        welcome,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(is_admin=db_user.is_admin),
    )


# ─── Force join re-check ─────────────────────────────────────────────────────

async def forcejoin_check_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Re-check membership when user clicks '✅ I Joined'."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    if tg_user is None:
        return

    missing = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, tg_user.id)
            if member.status not in ("member", "administrator", "creator"):
                missing.append(channel)
        except Exception as exc:
            logger.warning("Force join re-check failed for %s: %s", channel, exc)

    if missing:
        await query.edit_message_text(
            f"❌ You haven't joined all required channels yet.\n\n"
            f"Please join:\n" + "\n".join(f"• {ch}" for ch in missing),
            parse_mode="HTML",
            reply_markup=force_join_keyboard(missing),
        )
        return

    # All joined — show main menu
    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )

    await query.edit_message_text(
        f"✅ <b>All channels joined!</b>\n\n"
        f"Welcome to <b>{BOT_NAME}</b>. Use the menu below to get started.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(is_admin=db_user.is_admin),
    )


# ─── Handler registration ────────────────────────────────────────────────────

def register_start_handlers(app) -> None:
    """Register all start-related handlers with the Application."""
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(
        CallbackQueryHandler(forcejoin_check_callback, pattern=r"^forcejoin:check$")
    )
