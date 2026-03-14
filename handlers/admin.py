"""
handlers/admin.py — Admin panel handlers.

Admin commands:
  /admin                          → Show admin menu
  /addcredits <tg_id> <amount>    → Shortcut to add credits
  /ban <tg_id>                    → Ban a user
  /unban <tg_id>                  → Unban a user

Admin callback patterns:
  admin:menu          → Admin menu
  admin:stats         → Aggregate stats
  admin:payments      → List pending payments
  admin:pay_approve:<id>  → Approve payment
  admin:pay_reject:<id>   → Reject payment
  admin:users             → User search prompt
  admin:user_credits:<id> → Add credits to a user
  admin:ban:<tg_id>       → Ban user
  admin:unban:<tg_id>     → Unban user
  admin:broadcast         → Broadcast start
  admin:set_limit         → Change daily limit prompt
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_IDS
from services.database import (
    add_credits,
    ban_user,
    complete_payment,
    get_all_users,
    get_payment,
    get_pending_payments,
    get_user_by_telegram_id,
    get_user_stats,
    set_daily_limit,
    update_broadcast_stats,
    create_broadcast_record,
)
from services.payment_service import admin_approve_payment
from utils.decorators import admin_only
from utils.helpers import escape_html, format_datetime, safe_edit_or_reply
from utils.keyboards import (
    admin_menu_keyboard,
    admin_payment_keyboard,
    admin_user_manage_keyboard,
    back_to_main_keyboard,
)

logger = logging.getLogger(__name__)

# ConversationHandler states
(
    BROADCAST_TEXT,
    BROADCAST_PHOTO,
    BROADCAST_BUTTON,
    ADD_CREDITS_ID,
    ADD_CREDITS_AMOUNT,
    SET_LIMIT_ID,
    SET_LIMIT_VALUE,
) = range(7)


# ─── /admin command ──────────────────────────────────────────────────────────

@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the admin panel menu."""
    await update.message.reply_text(
        "🛠 <b>Admin Panel</b>\n\nSelect an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_keyboard(),
    )


@admin_only
async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🛠 <b>Admin Panel</b>\n\nSelect an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_keyboard(),
    )


# ─── Stats ───────────────────────────────────────────────────────────────────

@admin_only
async def admin_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    stats = await get_user_stats()

    text = (
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users: <b>{stats['total_users']}</b>\n"
        f"📬 Linked Gmail Accounts: <b>{stats['total_accounts']}</b>\n"
        f"💰 Credits in System: <b>{stats['total_credits_in_system']}</b>\n"
        f"✅ Completed Payments: <b>{stats['total_completed_payments']}</b>\n"
    )

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back", callback_data="admin:menu")]]
        ),
    )


# ─── Payments ────────────────────────────────────────────────────────────────

@admin_only
async def admin_payments_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    pending = await get_pending_payments()

    if not pending:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await query.edit_message_text(
            "💳 <b>Pending Payments</b>\n\nNo pending payments.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="admin:menu")]]
            ),
        )
        return

    lines = ["💳 <b>Pending Payments</b>\n"]
    for p in pending[:10]:
        lines.append(
            f"• ID <code>{p.id}</code> | Plan: {p.plan} | ${p.amount_usd / 100:.2f} | "
            f"user_id={p.user_id} | {format_datetime(p.created_at)}"
        )

    # Show first pending payment with action buttons
    first = pending[0]

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_payment_keyboard(first.id),
    )


@admin_only
async def admin_pay_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data  # admin:pay_approve:<id>
    try:
        payment_id = int(data.split(":")[2])
    except (IndexError, ValueError):
        return

    try:
        result = await admin_approve_payment(payment_id, admin_note="Approved via admin panel")
        payment = await get_payment(payment_id)
        # Notify user
        if payment:
            user = await get_user_by_telegram_id_by_db_id(payment.user_id, context)
            if user:
                try:
                    await context.bot.send_message(
                        chat_id=user.telegram_id,
                        text=(
                            f"✅ <b>Payment Approved!</b>\n\n"
                            f"Your payment (ID: <code>{payment_id}</code>) has been approved.\n"
                            f"<b>{result['credits_added']} credits</b> have been added to your account."
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as exc:
                    logger.warning("Could not notify user: %s", exc)

        await query.edit_message_text(
            f"✅ Payment <code>{payment_id}</code> approved. "
            f"{result['credits_added']} credits added.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_keyboard(),
        )
    except Exception as exc:
        await query.edit_message_text(
            f"❌ Error: {escape_html(str(exc))}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_keyboard(),
        )


async def get_user_by_telegram_id_by_db_id(user_db_id: int, context):
    """Helper to get user by internal DB id for notifications."""
    from sqlalchemy import select
    from db.models import User
    from db.session import get_session
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user_db_id))
        return result.scalar_one_or_none()


@admin_only
async def admin_pay_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    try:
        payment_id = int(data.split(":")[2])
    except (IndexError, ValueError):
        return

    from db.models import Payment
    from db.session import get_session
    from sqlalchemy import update as sql_update
    from datetime import datetime, timezone

    async with get_session() as session:
        await session.execute(
            sql_update(Payment)
            .where(Payment.id == payment_id)
            .values(status="failed", admin_note="Rejected by admin", updated_at=datetime.now(timezone.utc))
        )

    # Notify user
    payment = await get_payment(payment_id)
    if payment:
        user = await get_user_by_telegram_id_by_db_id(payment.user_id, context)
        if user:
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        f"❌ <b>Payment Rejected</b>\n\n"
                        f"Your payment (ID: <code>{payment_id}</code>) was not verified.\n"
                        f"Please contact support if you believe this is an error."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                logger.warning("Could not notify user about rejection: %s", exc)

    await query.edit_message_text(
        f"❌ Payment <code>{payment_id}</code> rejected.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_keyboard(),
    )


# ─── Add credits command ──────────────────────────────────────────────────────

@admin_only
async def addcredits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addcredits <telegram_id> <amount>"""
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /addcredits <telegram_id> <amount>", parse_mode=ParseMode.HTML
        )
        return

    try:
        tg_id = int(args[0])
        amount = int(args[1])
        if amount <= 0:
            raise ValueError("Amount must be positive.")
    except ValueError as exc:
        await update.message.reply_text(f"❌ Invalid input: {exc}")
        return

    try:
        new_balance = await add_credits(tg_id, amount)
        await update.message.reply_text(
            f"✅ Added <b>{amount}</b> credits to <code>{tg_id}</code>.\n"
            f"New balance: <b>{new_balance}</b>",
            parse_mode=ParseMode.HTML,
        )
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=f"💰 <b>{amount} credits</b> have been added to your account by an admin!\nNew balance: <b>{new_balance}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")


# ─── Ban/unban ────────────────────────────────────────────────────────────────

@admin_only
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ban <telegram_id>"""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /ban <telegram_id>")
        return
    try:
        tg_id = int(args[0])
        await ban_user(tg_id, True)
        await update.message.reply_text(f"🚫 User <code>{tg_id}</code> banned.", parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"❌ {exc}")


@admin_only
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unban <telegram_id>"""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /unban <telegram_id>")
        return
    try:
        tg_id = int(args[0])
        await ban_user(tg_id, False)
        await update.message.reply_text(f"✅ User <code>{tg_id}</code> unbanned.", parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"❌ {exc}")


# ─── Broadcast ConversationHandler ───────────────────────────────────────────

@admin_only
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the broadcast conversation — ask for message text."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📢 <b>Broadcast</b>\n\nSend the message text to broadcast.\n"
        "Type /cancel to abort.",
        parse_mode=ParseMode.HTML,
    )
    return BROADCAST_TEXT


async def broadcast_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive broadcast text, ask for optional photo."""
    context.user_data["broadcast_text"] = update.message.text
    await update.message.reply_text(
        "📷 Send a photo to attach, or type <b>skip</b> to skip.",
        parse_mode=ParseMode.HTML,
    )
    return BROADCAST_PHOTO


async def broadcast_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle optional photo, ask for optional button."""
    if update.message.photo:
        context.user_data["broadcast_photo"] = update.message.photo[-1].file_id
    else:
        context.user_data["broadcast_photo"] = None

    await update.message.reply_text(
        "🔗 Send a button in format: <code>Button Text | https://url.com</code>\n"
        "Or type <b>skip</b> to skip.",
        parse_mode=ParseMode.HTML,
    )
    return BROADCAST_BUTTON


async def broadcast_button_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle optional button and dispatch the broadcast RQ task."""
    text = update.message.text or ""
    button_text: Optional[str] = None
    button_url: Optional[str] = None

    if text.lower() != "skip" and "|" in text:
        parts = text.split("|", 1)
        button_text = parts[0].strip()
        button_url = parts[1].strip()

    message_text = context.user_data.get("broadcast_text", "")
    photo_file_id = context.user_data.get("broadcast_photo")

    users = await get_all_users()
    total = len(users)

    broadcast_record = await create_broadcast_record(
        admin_id=update.effective_user.id,
        message_text=message_text,
        photo_file_id=photo_file_id,
        button_text=button_text,
        button_url=button_url,
        total_recipients=total,
    )

    await update.message.reply_text(
        f"🚀 Broadcasting to <b>{total}</b> users…",
        parse_mode=ParseMode.HTML,
    )

    # Enqueue the RQ task
    try:
        import redis
        from rq import Queue
        from config import REDIS_URL
        from workers.tasks import broadcast_message_task

        r = redis.from_url(REDIS_URL)
        q = Queue(connection=r)
        q.enqueue(
            broadcast_message_task,
            broadcast_record.id,
            message_text,
            photo_file_id,
            button_text,
            button_url,
            [u.telegram_id for u in users],
        )
        await update.message.reply_text("✅ Broadcast queued successfully.")
    except Exception as exc:
        logger.error("Failed to queue broadcast: %s", exc)
        await update.message.reply_text(
            f"⚠️ Could not queue broadcast via RQ: {exc}\n"
            f"Sending synchronously instead…"
        )
        # Fallback: send inline
        success = failure = 0
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        kb = None
        if button_text and button_url:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(button_text, url=button_url)]])

        for user in users:
            try:
                if photo_file_id:
                    await context.bot.send_photo(
                        chat_id=user.telegram_id,
                        photo=photo_file_id,
                        caption=message_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=user.telegram_id,
                        text=message_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                    )
                success += 1
            except Exception:
                failure += 1

        await update_broadcast_stats(broadcast_record.id, success, failure)
        await update.message.reply_text(
            f"📢 Broadcast complete: ✅ {success} delivered, ❌ {failure} failed."
        )

    context.user_data.clear()
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Broadcast cancelled.")
    return ConversationHandler.END


def build_broadcast_conversation() -> ConversationHandler:
    """Build the broadcast ConversationHandler."""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_start, pattern=r"^admin:broadcast$")],
        states={
            BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_text_received)],
            BROADCAST_PHOTO: [
                MessageHandler(filters.PHOTO, broadcast_photo_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_photo_received),
            ],
            BROADCAST_BUTTON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_button_received)
            ],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
        allow_reentry=True,
    )


# ─── Handler registration ────────────────────────────────────────────────────

def register_admin_handlers(app) -> None:
    """Register all admin handlers and commands."""
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("addcredits", addcredits_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))

    app.add_handler(CallbackQueryHandler(admin_menu_callback, pattern=r"^admin:menu$"))
    app.add_handler(CallbackQueryHandler(admin_stats_callback, pattern=r"^admin:stats$"))
    app.add_handler(CallbackQueryHandler(admin_payments_callback, pattern=r"^admin:payments$"))
    app.add_handler(CallbackQueryHandler(admin_pay_approve_callback, pattern=r"^admin:pay_approve:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_pay_reject_callback, pattern=r"^admin:pay_reject:\d+$"))

    # Broadcast conversation
    app.add_handler(build_broadcast_conversation())
