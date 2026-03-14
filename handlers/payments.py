"""
handlers/payments.py — Credit purchase and payment confirmation handlers.

Flow:
  1. menu:buy / plans_callback     → Show available plans.
  2. buy:plan:<key>                → Show payment instructions + create pending payment.
  3. buy:paid:<payment_id>         → Notify admin, show pending message.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from config import (
    PAYMENT_CRYPTO_ADDRESS,
    PAYMENT_NOTE,
    PAYMENT_UPI_ID,
    PLANS,
    ADMIN_IDS,
)
from services.database import get_or_create_user, get_payment
from services.payment_service import initiate_purchase
from utils.decorators import not_banned, rate_limit, require_joined
from utils.helpers import escape_html, safe_edit_or_reply
from utils.keyboards import (
    back_to_main_keyboard,
    payment_confirm_keyboard,
    plans_keyboard,
)

logger = logging.getLogger(__name__)


# ─── Plan listing ────────────────────────────────────────────────────────────

@require_joined
@not_banned
async def plans_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available credit plans."""
    query = update.callback_query
    if query:
        await query.answer()

    tg_user = update.effective_user
    db_user = await get_or_create_user(tg_user.id, tg_user.username, tg_user.first_name)

    plan_lines = "\n".join(
        f"💠 <b>{p.name}</b> — ${p.price_usd:.0f} → {p.credits} credits\n   {p.description}"
        for p in PLANS.values()
    )

    text = (
        f"💳 <b>Buy Credits</b>\n\n"
        f"Current balance: <b>{db_user.credits} credits</b>\n\n"
        f"{plan_lines}\n\n"
        f"Select a plan to continue:"
    )

    await safe_edit_or_reply(update, context, text=text, reply_markup=plans_keyboard())


# ─── Plan selected → payment instructions ────────────────────────────────────

@require_joined
@not_banned
@rate_limit(max_calls=3, action="buy_plan")
async def plan_select_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Create pending payment record and show payment instructions."""
    query = update.callback_query
    await query.answer()

    data = query.data  # buy:plan:<key>
    try:
        plan_key = data.split(":")[2]
    except IndexError:
        return

    plan = PLANS.get(plan_key)
    if plan is None:
        await query.edit_message_text("❌ Invalid plan.", reply_markup=back_to_main_keyboard())
        return

    tg_user = update.effective_user
    db_user = await get_or_create_user(tg_user.id, tg_user.username, tg_user.first_name)

    try:
        result = await initiate_purchase(db_user.id, plan_key)
    except Exception as exc:
        logger.error("initiate_purchase failed: %s", exc)
        await query.edit_message_text(
            f"⚠️ Could not create payment: {escape_html(str(exc))}",
            parse_mode="HTML",
            reply_markup=back_to_main_keyboard(),
        )
        return

    payment_id = result["payment_id"]

    text = (
        f"💳 <b>Payment Instructions</b>\n\n"
        f"Plan: <b>{plan.name}</b>\n"
        f"Amount: <b>${plan.price_usd:.2f} USD</b>\n"
        f"Credits: <b>{plan.credits}</b>\n"
        f"Payment ID: <code>{payment_id}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>Crypto:</b>\n<code>{PAYMENT_CRYPTO_ADDRESS}</code>\n\n"
        f"📲 <b>UPI:</b>\n<code>{PAYMENT_UPI_ID}</code>\n\n"
        f"📝 <i>{escape_html(PAYMENT_NOTE)}</i>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"After sending payment, click <b>I Have Paid</b> below.\n"
        f"An admin will verify and credit your account."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=payment_confirm_keyboard(payment_id),
    )


# ─── "I Paid" confirmation ────────────────────────────────────────────────────

@require_joined
@not_banned
async def payment_paid_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle 'I Have Paid' — notify admin and show pending status."""
    query = update.callback_query
    await query.answer()

    data = query.data  # buy:paid:<payment_id>
    try:
        payment_id = int(data.split(":")[2])
    except (IndexError, ValueError):
        return

    payment = await get_payment(payment_id)
    if payment is None:
        await query.edit_message_text("❌ Payment not found.", reply_markup=back_to_main_keyboard())
        return

    tg_user = update.effective_user
    plan = PLANS.get(payment.plan)
    plan_name = plan.name if plan else payment.plan

    # Notify all admins
    admin_msg = (
        f"💳 <b>New Payment Claim</b>\n\n"
        f"User: {tg_user.first_name or 'N/A'} (@{tg_user.username or 'N/A'})\n"
        f"Telegram ID: <code>{tg_user.id}</code>\n"
        f"Payment ID: <code>{payment_id}</code>\n"
        f"Plan: <b>{plan_name}</b>\n"
        f"Amount: <b>${payment.amount_usd / 100:.2f}</b>\n"
        f"Credits: <b>{payment.credits}</b>\n\n"
        f"Use /admin → Payments to approve or reject."
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_msg,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Could not notify admin %d: %s", admin_id, exc)

    await query.edit_message_text(
        f"⏳ <b>Payment Submitted</b>\n\n"
        f"Payment ID: <code>{payment_id}</code>\n"
        f"An admin has been notified and will verify your payment shortly.\n\n"
        f"Your credits will be added once confirmed.",
        parse_mode="HTML",
        reply_markup=back_to_main_keyboard(),
    )


# ─── Handler registration ─────────────────────────────────────────────────────

def register_payment_handlers(app) -> None:
    """Register all payment-related handlers."""
    app.add_handler(CallbackQueryHandler(plans_callback, pattern=r"^menu:buy$"))
    app.add_handler(CallbackQueryHandler(plan_select_callback, pattern=r"^buy:plan:[a-z]+$"))
    app.add_handler(CallbackQueryHandler(payment_paid_callback, pattern=r"^buy:paid:\d+$"))
