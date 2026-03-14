"""
handlers/inbox.py — Inbox browsing and email reading handlers.

Callback patterns:
  inbox:select:<account_id>             → Show first page of inbox
  inbox:next:<account_id>:<page_token>  → Load next page
  email:read:<account_id>:<message_id>  → Fetch and display full email
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from services.database import (
    get_gmail_account_by_id,
    get_gmail_accounts,
    get_or_create_user,
)
from services.gmail_service import chunk_text, fetch_full_email, fetch_inbox_summary
from utils.decorators import not_banned, rate_limit, require_joined
from utils.helpers import escape_html, safe_edit_or_reply, truncate
from utils.keyboards import (
    back_to_main_keyboard,
    email_detail_keyboard,
    inbox_keyboard,
    select_account_keyboard,
)

logger = logging.getLogger(__name__)


# ─── Account selector ────────────────────────────────────────────────────────

@require_joined
@not_banned
async def inbox_select_account_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show account selector OR jump straight to inbox if only one account."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db_user = await get_or_create_user(tg_user.id, tg_user.username, tg_user.first_name)
    accounts = await get_gmail_accounts(db_user.id)

    if not accounts:
        await query.edit_message_text(
            "📭 You have no linked Gmail accounts.\n\n"
            "Go to <b>My Accounts</b> to link one first.",
            parse_mode="HTML",
            reply_markup=back_to_main_keyboard(),
        )
        return

    # If callback came from inbox:select:<id>, jump straight to that account
    data = query.data or ""
    if data.startswith("inbox:select:") and len(data.split(":")) == 3:
        try:
            account_id = int(data.split(":")[2])
            # Validate ownership
            account = next((a for a in accounts if a.id == account_id), None)
            if account:
                await _show_inbox_page(update, context, account.id, page_token=None)
                return
        except (ValueError, IndexError):
            pass

    # Show account picker
    await query.edit_message_text(
        "📥 <b>Select an account to view:</b>",
        parse_mode="HTML",
        reply_markup=select_account_keyboard(accounts),
    )


# ─── Show inbox page ─────────────────────────────────────────────────────────

async def _show_inbox_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    page_token: str | None,
) -> None:
    """Fetch and render a page of inbox messages for a given account."""
    query = update.callback_query

    account = await get_gmail_account_by_id(account_id)
    if account is None:
        await query.edit_message_text(
            "❌ Account not found.", reply_markup=back_to_main_keyboard()
        )
        return

    # Show loading indicator
    await query.edit_message_text(
        f"⏳ <b>Loading inbox for {escape_html(account.email)}…</b>",
        parse_mode="HTML",
    )

    try:
        # Run in executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: fetch_inbox_summary(
                account.encrypted_refresh_token,
                max_results=5,
                page_token=page_token,
            ),
        )
    except Exception as exc:
        logger.error("Inbox fetch failed for account_id=%d: %s", account_id, exc)
        await query.edit_message_text(
            f"⚠️ <b>Failed to load inbox:</b>\n<code>{escape_html(str(exc))}</code>",
            parse_mode="HTML",
            reply_markup=back_to_main_keyboard(),
        )
        return

    messages = result["messages"]
    next_page_token = result.get("next_page_token")

    if not messages:
        await query.edit_message_text(
            f"📭 No messages in inbox for <b>{escape_html(account.email)}</b>.",
            parse_mode="HTML",
            reply_markup=back_to_main_keyboard(),
        )
        return

    lines = [f"📥 <b>Inbox — {escape_html(account.email)}</b>\n"]
    for i, msg in enumerate(messages, 1):
        sender = truncate(escape_html(msg["from"]), 40)
        subject = truncate(escape_html(msg["subject"]), 50)
        snippet = truncate(escape_html(msg["snippet"]), 80)
        lines.append(
            f"{i}. <b>{subject}</b>\n"
            f"   From: {sender}\n"
            f"   {snippet}\n"
        )

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=inbox_keyboard(messages, account_id, next_page_token),
    )


@require_joined
@not_banned
@rate_limit(max_calls=10, action="inbox_page")
async def inbox_next_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle pagination: inbox:next:<account_id>:<page_token>."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 3)  # inbox:next:<account_id>:<page_token>
    if len(parts) < 4:
        return
    try:
        account_id = int(parts[2])
        page_token = parts[3]
    except (ValueError, IndexError):
        return

    await _show_inbox_page(update, context, account_id, page_token)


# ─── Read full email ─────────────────────────────────────────────────────────

@require_joined
@not_banned
@rate_limit(max_calls=15, action="read_email")
async def read_email_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Fetch and display the full content of an email.

    Callback pattern: email:read:<account_id>:<message_id>
    """
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 3)
    if len(parts) < 4:
        return
    try:
        account_id = int(parts[2])
        message_id = parts[3]
    except (ValueError, IndexError):
        return

    account = await get_gmail_account_by_id(account_id)
    if account is None:
        await query.edit_message_text("❌ Account not found.", reply_markup=back_to_main_keyboard())
        return

    await query.edit_message_text(
        "⏳ <b>Fetching email…</b>",
        parse_mode="HTML",
    )

    try:
        loop = asyncio.get_event_loop()
        email_data = await loop.run_in_executor(
            None,
            lambda: fetch_full_email(account.encrypted_refresh_token, message_id),
        )
    except Exception as exc:
        logger.error("Failed to fetch email %s: %s", message_id, exc)
        await query.edit_message_text(
            f"⚠️ <b>Failed to load email:</b>\n<code>{escape_html(str(exc))}</code>",
            parse_mode="HTML",
            reply_markup=email_detail_keyboard(account_id, message_id),
        )
        return

    header = (
        f"📩 <b>{escape_html(email_data['subject'])}</b>\n"
        f"From: {escape_html(email_data['from'])}\n"
        f"Date: {escape_html(email_data['date'])}\n"
        f"{'─' * 30}\n"
    )
    body = email_data["body"]

    # Split if message is too long
    full_text = header + escape_html(body)
    chunks = chunk_text(full_text, max_len=3800)

    # Send the first chunk by editing, then send the rest as new messages
    try:
        await query.edit_message_text(
            chunks[0],
            parse_mode="HTML",
            reply_markup=email_detail_keyboard(account_id, message_id) if len(chunks) == 1 else None,
        )
        for i, chunk in enumerate(chunks[1:], 1):
            is_last = i == len(chunks) - 1
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=chunk,
                parse_mode="HTML",
                reply_markup=email_detail_keyboard(account_id, message_id) if is_last else None,
            )
    except Exception as exc:
        logger.error("Failed to send email content: %s", exc)


# ─── Handler registration ────────────────────────────────────────────────────

def register_inbox_handlers(app) -> None:
    """Register all inbox-related handlers."""
    app.add_handler(
        CallbackQueryHandler(inbox_select_account_callback, pattern=r"^inbox:select(:\d+)?$")
    )
    app.add_handler(
        CallbackQueryHandler(inbox_next_page_callback, pattern=r"^inbox:next:\d+:.+$")
    )
    app.add_handler(
        CallbackQueryHandler(read_email_callback, pattern=r"^email:read:\d+:.+$")
    )
