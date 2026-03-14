"""
handlers/menu.py — Navigation callbacks for the main menu and profile.

Handles callback_data patterns:
  menu:main     → re-render the main menu
  menu:accounts → route to accounts list
  menu:inbox    → route to inbox account selector
  menu:buy      → route to payment plans
  menu:profile  → show user profile / credit balance
  menu:help     → show help message
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from config import BOT_NAME, SUPPORT_USERNAME
from services.database import get_or_create_user, get_gmail_accounts
from utils.decorators import not_banned, require_joined
from utils.helpers import format_datetime, safe_edit_or_reply
from utils.keyboards import main_menu_keyboard

logger = logging.getLogger(__name__)


# ─── Main menu ───────────────────────────────────────────────────────────────

@require_joined
@not_banned
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-render the main menu."""
    tg_user = update.effective_user
    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    await safe_edit_or_reply(
        update, context,
        text=(
            f"🏠 <b>{BOT_NAME} — Main Menu</b>\n\n"
            f"💰 Credits: <b>{db_user.credits}</b>\n"
            f"Choose an option below:"
        ),
        reply_markup=main_menu_keyboard(is_admin=db_user.is_admin),
    )


# ─── Profile ─────────────────────────────────────────────────────────────────

@require_joined
@not_banned
async def profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's profile and statistics."""
    tg_user = update.effective_user
    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    accounts = await get_gmail_accounts(db_user.id)

    text = (
        f"👤 <b>Your Profile</b>\n\n"
        f"🆔 Telegram ID: <code>{tg_user.id}</code>\n"
        f"👋 Name: {tg_user.first_name or 'N/A'}\n"
        f"🔖 Username: @{tg_user.username or 'N/A'}\n\n"
        f"💰 Credits: <b>{db_user.credits}</b>\n"
        f"📬 Linked accounts: <b>{len(accounts)}</b>\n"
        f"📊 Daily limit: <b>{db_user.daily_limit}</b> fetches/day\n"
        f"📅 Member since: {format_datetime(db_user.joined_at)}\n"
    )

    from utils.keyboards import back_to_main_keyboard
    await safe_edit_or_reply(update, context, text=text, reply_markup=back_to_main_keyboard())


# ─── Help ────────────────────────────────────────────────────────────────────

@require_joined
@not_banned
async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the help / FAQ message."""
    text = (
        f"ℹ️ <b>Help — {BOT_NAME}</b>\n\n"
        f"<b>How it works:</b>\n"
        f"1️⃣ Buy credits using the <i>Buy Credits</i> menu.\n"
        f"2️⃣ Link a Gmail account — costs <b>1 credit</b> per account.\n"
        f"3️⃣ Open your inbox and read emails directly in Telegram.\n\n"
        f"<b>Commands:</b>\n"
        f"/start — restart the bot\n"
        f"/cancel — cancel current operation\n\n"
        f"<b>Support:</b> {SUPPORT_USERNAME}\n\n"
        f"<b>Privacy:</b> Your Gmail OAuth tokens are encrypted at rest "
        f"using AES-256 (Fernet). We only request <i>read-only</i> Gmail access."
    )
    from utils.keyboards import back_to_main_keyboard
    await safe_edit_or_reply(update, context, text=text, reply_markup=back_to_main_keyboard())


# ─── Stub routers ────────────────────────────────────────────────────────────

async def _route_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route to the accounts listing (defined in handlers/accounts.py)."""
    # Import here to avoid circular imports at module load time
    from handlers.accounts import accounts_list_callback
    await accounts_list_callback(update, context)


async def _route_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from handlers.inbox import inbox_select_account_callback
    await inbox_select_account_callback(update, context)


async def _route_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from handlers.payments import plans_callback
    await plans_callback(update, context)


# ─── Handler registration ─────────────────────────────────────────────────────

def register_menu_handlers(app) -> None:
    """Register menu navigation callbacks with the Application."""
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern=r"^menu:main$"))
    app.add_handler(CallbackQueryHandler(profile_callback, pattern=r"^menu:profile$"))
    app.add_handler(CallbackQueryHandler(help_callback, pattern=r"^menu:help$"))
    app.add_handler(CallbackQueryHandler(_route_accounts, pattern=r"^menu:accounts$"))
    app.add_handler(CallbackQueryHandler(_route_inbox, pattern=r"^menu:inbox$"))
    app.add_handler(CallbackQueryHandler(_route_buy, pattern=r"^menu:buy$"))
