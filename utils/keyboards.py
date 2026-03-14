"""
utils/keyboards.py — Centralized inline keyboard factory functions.

All keyboards return InlineKeyboardMarkup objects ready to pass as the
``reply_markup`` parameter to send_message / edit_message_reply_markup.
"""

from __future__ import annotations

from typing import List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import PLANS, REQUIRED_CHANNELS


# ─── Main menu ───────────────────────────────────────────────────────────────

def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Top-level navigation keyboard shown after /start."""
    rows = [
        [
            InlineKeyboardButton("📧 My Accounts", callback_data="menu:accounts"),
            InlineKeyboardButton("📥 Inbox", callback_data="menu:inbox"),
        ],
        [
            InlineKeyboardButton("💳 Buy Credits", callback_data="menu:buy"),
            InlineKeyboardButton("👤 Profile", callback_data="menu:profile"),
        ],
        [
            InlineKeyboardButton("ℹ️ Help", callback_data="menu:help"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin:menu")])
    return InlineKeyboardMarkup(rows)


# ─── Force join ──────────────────────────────────────────────────────────────

def force_join_keyboard(channels: Optional[List[str]] = None) -> InlineKeyboardMarkup:
    """Keyboard with links to required channels and a re-check button."""
    chs = channels or REQUIRED_CHANNELS
    rows = [
        [InlineKeyboardButton(f"➡️ Join {ch}", url=f"https://t.me/{ch.lstrip('@')}")]
        for ch in chs
    ]
    rows.append([InlineKeyboardButton("✅ I Joined — Check Again", callback_data="forcejoin:check")])
    return InlineKeyboardMarkup(rows)


# ─── Accounts list ───────────────────────────────────────────────────────────

def accounts_list_keyboard(accounts: list, show_add: bool = True) -> InlineKeyboardMarkup:
    """Show linked Gmail accounts with remove buttons."""
    rows = []
    for acc in accounts:
        rows.append(
            [
                InlineKeyboardButton(f"📬 {acc.email}", callback_data=f"account:view:{acc.id}"),
                InlineKeyboardButton("🗑 Remove", callback_data=f"account:remove:{acc.id}"),
            ]
        )
    if show_add:
        rows.append([InlineKeyboardButton("➕ Link New Gmail Account", callback_data="account:link")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def confirm_remove_keyboard(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes, Remove", callback_data=f"account:remove_confirm:{account_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="menu:accounts"),
            ]
        ]
    )


# ─── Inbox ───────────────────────────────────────────────────────────────────

def select_account_keyboard(accounts: list) -> InlineKeyboardMarkup:
    """Let user pick which Gmail account to view inbox for."""
    rows = [
        [InlineKeyboardButton(f"📬 {acc.email}", callback_data=f"inbox:select:{acc.id}")]
        for acc in accounts
    ]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def inbox_keyboard(
    messages: list,
    account_id: int,
    next_page_token: Optional[str] = None,
    prev_offset: int = 0,
) -> InlineKeyboardMarkup:
    """Email listing keyboard with read + pagination buttons."""
    rows = []
    for i, msg in enumerate(messages):
        subject = msg["subject"][:35] + "…" if len(msg["subject"]) > 35 else msg["subject"]
        rows.append(
            [InlineKeyboardButton(f"📩 {subject}", callback_data=f"email:read:{account_id}:{msg['id']}")]
        )

    nav_buttons = []
    if prev_offset > 0:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"inbox:page:{account_id}:prev:{prev_offset}")
        )
    if next_page_token:
        nav_buttons.append(
            InlineKeyboardButton("➡️ Next", callback_data=f"inbox:next:{account_id}:{next_page_token}")
        )
    if nav_buttons:
        rows.append(nav_buttons)

    rows.append(
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"inbox:select:{account_id}"),
            InlineKeyboardButton("🔙 Back", callback_data="menu:inbox"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def email_detail_keyboard(account_id: int, message_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔙 Back to Inbox", callback_data=f"inbox:select:{account_id}")]
        ]
    )


# ─── Payment / plans ─────────────────────────────────────────────────────────

def plans_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"💠 {plan.name} — ${plan.price_usd:.0f} ({plan.credits} credits)",
                callback_data=f"buy:plan:{key}",
            )
        ]
        for key, plan in PLANS.items()
    ]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def payment_confirm_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ I Have Paid", callback_data=f"buy:paid:{payment_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:main")],
        ]
    )


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")]]
    )


# ─── Admin ───────────────────────────────────────────────────────────────────

def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Stats", callback_data="admin:stats"),
                InlineKeyboardButton("👥 Users", callback_data="admin:users"),
            ],
            [
                InlineKeyboardButton("💳 Payments", callback_data="admin:payments"),
                InlineKeyboardButton("➕ Add Credits", callback_data="admin:add_credits"),
            ],
            [
                InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcast"),
                InlineKeyboardButton("⚙️ Set Limit", callback_data="admin:set_limit"),
            ],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:main")],
        ]
    )


def admin_payment_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve", callback_data=f"admin:pay_approve:{payment_id}"
                ),
                InlineKeyboardButton(
                    "❌ Reject", callback_data=f"admin:pay_reject:{payment_id}"
                ),
            ],
            [InlineKeyboardButton("🔙 Back", callback_data="admin:payments")],
        ]
    )


def admin_user_manage_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Credits", callback_data=f"admin:user_credits:{telegram_id}")],
            [InlineKeyboardButton("🚫 Ban User", callback_data=f"admin:ban:{telegram_id}")],
            [InlineKeyboardButton("✅ Unban User", callback_data=f"admin:unban:{telegram_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin:menu")],
        ]
    )
