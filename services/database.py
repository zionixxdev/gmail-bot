"""
services/database.py — High-level async database helpers.

All functions accept/return SQLAlchemy model instances and use the
session context manager from db.session.  Callers should not need to
write raw SQL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, update, func

from config import NEW_USER_CREDITS, DEFAULT_DAILY_LIMIT, ADMIN_IDS
from db.models import BroadcastMessage, GmailAccount, Payment, User
from db.session import get_session

logger = logging.getLogger(__name__)


# ─── User helpers ────────────────────────────────────────────────────────────

async def get_or_create_user(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
) -> User:
    """Return an existing user or create a new one.

    Args:
        telegram_id: Telegram user ID (bigint).
        username:    Telegram @username (without @), may be None.
        first_name:  Display name, may be None.

    Returns:
        User ORM instance (always persisted).
    """
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                credits=NEW_USER_CREDITS,
                daily_limit=DEFAULT_DAILY_LIMIT,
                is_admin=telegram_id in ADMIN_IDS,
            )
            session.add(user)
            await session.flush()
            logger.info("New user created: tg_id=%d", telegram_id)
        else:
            # Keep profile info fresh
            user.username = username
            user.first_name = first_name
        return user


async def get_user_by_telegram_id(telegram_id: int) -> Optional[User]:
    """Fetch a user by Telegram ID, or None if not found."""
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()


async def get_user_by_id(user_id: int) -> Optional[User]:
    """Fetch a user by internal DB ID."""
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


async def add_credits(telegram_id: int, amount: int) -> int:
    """Add credits to a user's balance.

    Returns:
        New credit balance.
    """
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError(f"User {telegram_id} not found.")
        user.credits += amount
        new_balance = user.credits
        logger.info(
            "Credits added: tg_id=%d +%d → balance=%d", telegram_id, amount, new_balance
        )
        return new_balance


async def deduct_credit(telegram_id: int) -> int:
    """Deduct 1 credit from a user. Raises ValueError if insufficient.

    Returns:
        Remaining credit balance.
    """
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError(f"User {telegram_id} not found.")
        if user.credits <= 0:
            raise ValueError("Insufficient credits.")
        user.credits -= 1
        return user.credits


async def set_daily_limit(telegram_id: int, limit: int) -> None:
    """Update a user's daily inbox fetch limit."""
    async with get_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(daily_limit=limit)
        )


async def ban_user(telegram_id: int, banned: bool = True) -> None:
    """Ban or unban a user."""
    async with get_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(is_banned=banned)
        )


async def get_all_users() -> List[User]:
    """Return all users (used for broadcast)."""
    async with get_session() as session:
        result = await session.execute(select(User).where(User.is_banned == False))
        return list(result.scalars().all())


async def get_user_stats() -> dict:
    """Aggregate stats for the admin panel."""
    async with get_session() as session:
        total_users = (await session.execute(func.count(User.id).select())).scalar_one()
        total_accounts = (
            await session.execute(func.count(GmailAccount.id).select())
        ).scalar_one()
        total_credits = (
            await session.execute(func.sum(User.credits).select())
        ).scalar_one() or 0
        total_payments_completed = (
            await session.execute(
                select(func.count(Payment.id)).where(Payment.status == "completed")
            )
        ).scalar_one()
        return {
            "total_users": total_users,
            "total_accounts": total_accounts,
            "total_credits_in_system": total_credits,
            "total_completed_payments": total_payments_completed,
        }


# ─── Gmail account helpers ───────────────────────────────────────────────────

async def add_gmail_account(
    user_id: int, email: str, encrypted_refresh_token: str
) -> GmailAccount:
    """Persist a new linked Gmail account.

    Args:
        user_id:                   Internal DB user ID.
        email:                     Gmail address.
        encrypted_refresh_token:   Fernet-encrypted refresh token string.

    Returns:
        Created GmailAccount instance.
    """
    async with get_session() as session:
        account = GmailAccount(
            user_id=user_id,
            email=email,
            encrypted_refresh_token=encrypted_refresh_token,
        )
        session.add(account)
        await session.flush()
        logger.info("Gmail account linked: %s → user_id=%d", email, user_id)
        return account


async def get_gmail_accounts(user_id: int) -> List[GmailAccount]:
    """Return all Gmail accounts linked by a user (by internal DB ID)."""
    async with get_session() as session:
        result = await session.execute(
            select(GmailAccount)
            .where(GmailAccount.user_id == user_id)
            .order_by(GmailAccount.created_at)
        )
        return list(result.scalars().all())


async def get_gmail_account_by_id(account_id: int) -> Optional[GmailAccount]:
    """Fetch a GmailAccount by its primary key."""
    async with get_session() as session:
        result = await session.execute(
            select(GmailAccount).where(GmailAccount.id == account_id)
        )
        return result.scalar_one_or_none()


async def remove_gmail_account(account_id: int, user_id: int) -> bool:
    """Delete a Gmail account. Returns True if deleted, False if not found/unauthorized."""
    async with get_session() as session:
        result = await session.execute(
            select(GmailAccount).where(
                GmailAccount.id == account_id,
                GmailAccount.user_id == user_id,
            )
        )
        account = result.scalar_one_or_none()
        if account is None:
            return False
        await session.delete(account)
        logger.info("Gmail account removed: id=%d", account_id)
        return True


# ─── Payment helpers ─────────────────────────────────────────────────────────

async def create_payment(
    user_id: int,
    plan: str,
    amount_usd_cents: int,
    credits: int,
) -> Payment:
    """Create a new pending payment record."""
    async with get_session() as session:
        payment = Payment(
            user_id=user_id,
            plan=plan,
            amount_usd=amount_usd_cents,
            credits=credits,
            status="pending",
        )
        session.add(payment)
        await session.flush()
        logger.info(
            "Payment created: id=%d user_id=%d plan=%s", payment.id, user_id, plan
        )
        return payment


async def get_payment(payment_id: int) -> Optional[Payment]:
    """Fetch a payment by ID."""
    async with get_session() as session:
        result = await session.execute(
            select(Payment).where(Payment.id == payment_id)
        )
        return result.scalar_one_or_none()


async def get_pending_payments() -> List[Payment]:
    """All payments awaiting admin verification."""
    async with get_session() as session:
        result = await session.execute(
            select(Payment)
            .where(Payment.status == "pending")
            .order_by(Payment.created_at.desc())
        )
        return list(result.scalars().all())


async def complete_payment(payment_id: int, admin_note: Optional[str] = None) -> Payment:
    """Mark a payment as completed and credit the user.

    Returns:
        Updated Payment instance.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Payment).where(Payment.id == payment_id)
        )
        payment = result.scalar_one_or_none()
        if payment is None:
            raise ValueError(f"Payment {payment_id} not found.")
        if payment.status == "completed":
            raise ValueError(f"Payment {payment_id} already completed.")

        payment.status = "completed"
        payment.admin_note = admin_note
        payment.updated_at = datetime.now(timezone.utc)

        # Credit the user
        user_result = await session.execute(
            select(User).where(User.id == payment.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user:
            user.credits += payment.credits
            logger.info(
                "Payment %d completed → user_id=%d +%d credits",
                payment_id,
                user.id,
                payment.credits,
            )
        return payment


# ─── Broadcast helpers ───────────────────────────────────────────────────────

async def create_broadcast_record(
    admin_id: int,
    message_text: str,
    photo_file_id: Optional[str],
    button_text: Optional[str],
    button_url: Optional[str],
    total_recipients: int,
) -> BroadcastMessage:
    """Persist a broadcast record before dispatch."""
    async with get_session() as session:
        record = BroadcastMessage(
            admin_id=admin_id,
            message_text=message_text,
            photo_file_id=photo_file_id,
            button_text=button_text,
            button_url=button_url,
            total_recipients=total_recipients,
        )
        session.add(record)
        await session.flush()
        return record


async def update_broadcast_stats(
    broadcast_id: int, success: int, failure: int
) -> None:
    """Update delivery stats after broadcast completes."""
    async with get_session() as session:
        await session.execute(
            update(BroadcastMessage)
            .where(BroadcastMessage.id == broadcast_id)
            .values(success_count=success, failure_count=failure)
        )
