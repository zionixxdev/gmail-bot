"""
db/models.py — SQLAlchemy 2.0-style ORM models.

Tables:
    users              — registered Telegram users
    gmail_accounts     — linked Gmail accounts (refresh tokens encrypted)
    payments           — credit purchase records
    broadcast_messages — admin broadcast log
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


# ─── User ────────────────────────────────────────────────────────────────────

class User(Base):
    """Telegram user registered with the bot."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    total_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # relationships
    gmail_accounts: Mapped[List["GmailAccount"]] = relationship(
        "GmailAccount", back_populates="user", cascade="all, delete-orphan"
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User tg_id={self.telegram_id} credits={self.credits}>"


# ─── Gmail Account ───────────────────────────────────────────────────────────

class GmailAccount(Base):
    """A Gmail account linked by a user via OAuth 2.0."""

    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # relationships
    user: Mapped["User"] = relationship("User", back_populates="gmail_accounts")

    def __repr__(self) -> str:
        return f"<GmailAccount email={self.email} user_id={self.user_id}>"


# ─── Payment ─────────────────────────────────────────────────────────────────

class Payment(Base):
    """Credit purchase record. Manually verified by admin."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False)  # basic / pro / enterprise
    amount_usd: Mapped[float] = mapped_column(Integer, nullable=False)  # stored as cents
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # pending | completed | failed | refunded
    payment_method: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tx_reference: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    admin_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # relationships
    user: Mapped["User"] = relationship("User", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment id={self.id} plan={self.plan} status={self.status}>"


# ─── Broadcast Message ───────────────────────────────────────────────────────

class BroadcastMessage(Base):
    """Admin broadcast log with delivery stats."""

    __tablename__ = "broadcast_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    button_text: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    button_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    total_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Broadcast id={self.id} "
            f"ok={self.success_count} fail={self.failure_count}>"
        )
