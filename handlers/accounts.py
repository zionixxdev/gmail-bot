"""
handlers/accounts.py — Gmail account management handlers.

Callbacks handled:
  account:list            → Show linked accounts
  account:link            → Start OAuth link flow
  account:view:<id>       → Show account detail
  account:remove:<id>     → Ask for confirmation
  account:remove_confirm:<id> → Delete account (refund credit TBD)
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from config import CREDITS_PER_ACCOUNT
from services.database import (
    deduct_credit,
    get_gmail_accounts,
    get_or_create_user,
    remove_gmail_account,
)
from services.gmail_service import generate_oauth_url
from utils.decorators import not_banned, rate_limit, require_joined
from utils.helpers import format_datetime, safe_edit_or_reply
from utils.keyboards import (
    accounts_list_keyboard,
    back_to_main_keyboard,
    confirm_remove_keyboard,
)

logger = logging.getLogger(__name__)


# ─── List accounts ───────────────────────────────────────────────────────────

@require_joined
@not_banned
async def accounts_list_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show all linked Gmail accounts for the current user."""
    tg_user = update.effective_user
    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    accounts = await get_gmail_accounts(db_user.id)

    if not accounts:
        text = (
            f"📬 <b>My Gmail Accounts</b>\n\n"
            f"You haven't linked any Gmail accounts yet.\n\n"
            f"💰 Credits: <b>{db_user.credits}</b>\n"
            f"Linking a new account costs <b>{CREDITS_PER_ACCOUNT} credit</b>."
        )
    else:
        text = (
            f"📬 <b>My Gmail Accounts</b> ({len(accounts)} linked)\n\n"
            f"💰 Credits: <b>{db_user.credits}</b>\n"
            f"Linking costs <b>{CREDITS_PER_ACCOUNT} credit</b> per account.\n\n"
            + "\n".join(f"• {acc.email}" for acc in accounts)
        )

    await safe_edit_or_reply(
        update, context,
        text=text,
        reply_markup=accounts_list_keyboard(accounts, show_add=True),
    )


# ─── Link new account ────────────────────────────────────────────────────────

@require_joined
@not_banned
@rate_limit(max_calls=3, action="link_account")
async def account_link_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Start the OAuth flow: check credits, generate URL, send to user."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )

    if db_user.credits < CREDITS_PER_ACCOUNT:
        await query.edit_message_text(
            f"❌ <b>Insufficient Credits</b>\n\n"
            f"You need <b>{CREDITS_PER_ACCOUNT} credit</b> to link an account.\n"
            f"Your balance: <b>{db_user.credits}</b>\n\n"
            f"Use <i>Buy Credits</i> to top up.",
            parse_mode="HTML",
            reply_markup=back_to_main_keyboard(),
        )
        return

    try:
        auth_url = generate_oauth_url(tg_user.id)
    except Exception as exc:
        logger.error("Failed to generate OAuth URL for tg_id=%d: %s", tg_user.id, exc)
        await query.edit_message_text(
            "⚠️ Failed to generate the authorization link. Please try again later.",
            parse_mode="HTML",
            reply_markup=back_to_main_keyboard(),
        )
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔐 Authorize Gmail", url=auth_url)],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:accounts")],
        ]
    )

    await query.edit_message_text(
        f"🔐 <b>Link Gmail Account</b>\n\n"
        f"1️⃣ Click <b>Authorize Gmail</b> below.\n"
        f"2️⃣ Sign in and allow read-only access.\n"
        f"3️⃣ You'll be redirected and we'll link your account automatically.\n\n"
        f"⚠️ This link expires in <b>10 minutes</b>.\n"
        f"💰 Cost: <b>{CREDITS_PER_ACCOUNT} credit</b> (deducted on success).",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ─── View account detail ─────────────────────────────────────────────────────

@require_joined
@not_banned
async def account_view_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show detail for a single linked account."""
    query = update.callback_query
    await query.answer()

    data = query.data  # account:view:<id>
    try:
        account_id = int(data.split(":")[2])
    except (IndexError, ValueError):
        return

    tg_user = update.effective_user
    db_user = await get_or_create_user(tg_user.id, tg_user.username, tg_user.first_name)
    accounts = await get_gmail_accounts(db_user.id)

    account = next((a for a in accounts if a.id == account_id), None)
    if account is None:
        await query.edit_message_text("❌ Account not found.", reply_markup=back_to_main_keyboard())
        return

    from utils.keyboards import accounts_list_keyboard
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 View Inbox", callback_data=f"inbox:select:{account_id}")],
            [InlineKeyboardButton("🗑 Remove Account", callback_data=f"account:remove:{account_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu:accounts")],
        ]
    )

    await query.edit_message_text(
        f"📬 <b>Account Details</b>\n\n"
        f"📧 Email: <code>{account.email}</code>\n"
        f"📅 Linked: {format_datetime(account.created_at)}\n",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ─── Remove account ──────────────────────────────────────────────────────────

@require_joined
@not_banned
async def account_remove_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Ask the user to confirm account removal."""
    query = update.callback_query
    await query.answer()

    data = query.data  # account:remove:<id>
    try:
        account_id = int(data.split(":")[2])
    except (IndexError, ValueError):
        return

    await query.edit_message_text(
        f"⚠️ <b>Remove Account?</b>\n\n"
        f"Are you sure you want to remove this account?\n"
        f"<i>Note: Credits are not refunded.</i>",
        parse_mode="HTML",
        reply_markup=confirm_remove_keyboard(account_id),
    )


@require_joined
@not_banned
async def account_remove_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Perform account removal after user confirms."""
    query = update.callback_query
    await query.answer()

    data = query.data  # account:remove_confirm:<id>
    try:
        account_id = int(data.split(":")[2])
    except (IndexError, ValueError):
        return

    tg_user = update.effective_user
    db_user = await get_or_create_user(tg_user.id, tg_user.username, tg_user.first_name)
    deleted = await remove_gmail_account(account_id, db_user.id)

    if deleted:
        await query.edit_message_text(
            "✅ Account removed successfully.",
            parse_mode="HTML",
            reply_markup=back_to_main_keyboard(),
        )
    else:
        await query.edit_message_text(
            "❌ Could not remove account. It may already be deleted.",
            parse_mode="HTML",
            reply_markup=back_to_main_keyboard(),
        )


# ─── Handler registration ────────────────────────────────────────────────────

def register_account_handlers(app) -> None:
    """Register all account management handlers."""
    app.add_handler(CallbackQueryHandler(accounts_list_callback, pattern=r"^menu:accounts$"))
    app.add_handler(CallbackQueryHandler(account_link_callback, pattern=r"^account:link$"))
    app.add_handler(CallbackQueryHandler(account_view_callback, pattern=r"^account:view:\d+$"))
    app.add_handler(CallbackQueryHandler(account_remove_callback, pattern=r"^account:remove:\d+$"))
    app.add_handler(
        CallbackQueryHandler(account_remove_confirm_callback, pattern=r"^account:remove_confirm:\d+$")
    )
