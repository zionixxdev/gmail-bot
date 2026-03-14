"""
workers/tasks.py — RQ background task definitions.

Tasks run in a separate worker process (workers/worker.py).
They communicate with Telegram via direct Bot API HTTP calls (requests),
since the async PTB Application is not available in the worker context.

Tasks:
  broadcast_message_task  — Send a broadcast to a list of user IDs.
  fetch_and_notify_task   — Fetch new emails and notify a user.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import requests as http_requests

from config import BOT_TOKEN, INBOX_PAGE_SIZE

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ─── Telegram helpers ─────────────────────────────────────────────────────────

def _tg_send_message(
    chat_id: int,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[dict] = None,
) -> bool:
    """Send a Telegram message via raw HTTP (used inside RQ worker)."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = http_requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as exc:
        logger.error("_tg_send_message failed for %d: %s", chat_id, exc)
        return False


def _tg_send_photo(
    chat_id: int,
    photo: str,
    caption: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[dict] = None,
) -> bool:
    """Send a Telegram photo with caption via raw HTTP."""
    payload = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = http_requests.post(f"{TELEGRAM_API}/sendPhoto", json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as exc:
        logger.error("_tg_send_photo failed for %d: %s", chat_id, exc)
        return False


# ─── Broadcast task ───────────────────────────────────────────────────────────

def broadcast_message_task(
    broadcast_id: int,
    message_text: str,
    photo_file_id: Optional[str],
    button_text: Optional[str],
    button_url: Optional[str],
    user_ids: List[int],
) -> dict:
    """RQ task: send a broadcast message to all provided user IDs.

    Args:
        broadcast_id:  DB BroadcastMessage ID for stat tracking.
        message_text:  Message body (HTML).
        photo_file_id: Telegram file_id for the photo, or None.
        button_text:   Inline button label, or None.
        button_url:    Inline button URL, or None.
        user_ids:      List of Telegram user IDs to message.

    Returns:
        Dict with success_count and failure_count.
    """
    logger.info(
        "Broadcast task started: id=%d recipients=%d", broadcast_id, len(user_ids)
    )

    reply_markup: Optional[dict] = None
    if button_text and button_url:
        reply_markup = {
            "inline_keyboard": [[{"text": button_text, "url": button_url}]]
        }

    success = 0
    failure = 0

    for user_id in user_ids:
        try:
            if photo_file_id:
                ok = _tg_send_photo(user_id, photo_file_id, message_text, reply_markup=reply_markup)
            else:
                ok = _tg_send_message(user_id, message_text, reply_markup=reply_markup)

            if ok:
                success += 1
            else:
                failure += 1
        except Exception as exc:
            logger.warning("Broadcast send failed for %d: %s", user_id, exc)
            failure += 1

        # Respect Telegram rate limits: ~30 messages/second max
        time.sleep(0.04)

    # Update DB stats (synchronous via psycopg2/sqlite3 directly or via SQLAlchemy sync)
    _update_broadcast_stats_sync(broadcast_id, success, failure)

    logger.info(
        "Broadcast task complete: id=%d success=%d failure=%d",
        broadcast_id, success, failure,
    )
    return {"success": success, "failure": failure}


def _update_broadcast_stats_sync(broadcast_id: int, success: int, failure: int) -> None:
    """Update broadcast stats using synchronous SQLAlchemy (for RQ worker context)."""
    try:
        from sqlalchemy import create_engine, update
        from sqlalchemy.orm import Session
        from config import DATABASE_URL
        from db.models import BroadcastMessage

        # Strip async driver prefix for sync usage
        sync_url = DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)
        with Session(engine) as session:
            session.execute(
                update(BroadcastMessage)
                .where(BroadcastMessage.id == broadcast_id)
                .values(success_count=success, failure_count=failure)
            )
            session.commit()
    except Exception as exc:
        logger.error("Failed to update broadcast stats: %s", exc)


# ─── Fetch and notify task ────────────────────────────────────────────────────

def fetch_and_notify_task(user_telegram_id: int, account_id: int) -> None:
    """RQ task: fetch recent emails for an account and notify the user.

    This is useful for background email polling / push-style notifications.

    Args:
        user_telegram_id: The user's Telegram ID.
        account_id:       DB GmailAccount ID.
    """
    logger.info(
        "fetch_and_notify started: tg_id=%d account_id=%d",
        user_telegram_id, account_id,
    )
    try:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session
        from config import DATABASE_URL
        from db.models import GmailAccount
        from services.gmail_service import fetch_inbox_summary

        sync_url = DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        with Session(engine) as session:
            account = session.execute(
                select(GmailAccount).where(GmailAccount.id == account_id)
            ).scalar_one_or_none()

            if account is None:
                logger.warning("fetch_and_notify: account_id=%d not found", account_id)
                return

            result = fetch_inbox_summary(
                account.encrypted_refresh_token, max_results=INBOX_PAGE_SIZE
            )

        messages = result.get("messages", [])
        if not messages:
            _tg_send_message(user_telegram_id, f"📭 No new messages in {account.email}.")
            return

        lines = [f"📥 <b>New Emails — {account.email}</b>\n"]
        for msg in messages[:5]:
            lines.append(
                f"• <b>{msg['subject'][:50]}</b>\n"
                f"  From: {msg['from'][:40]}\n"
            )

        _tg_send_message(user_telegram_id, "\n".join(lines))
        logger.info(
            "fetch_and_notify complete: tg_id=%d %d messages",
            user_telegram_id, len(messages),
        )

    except Exception as exc:
        logger.error(
            "fetch_and_notify failed: tg_id=%d account_id=%d error=%s",
            user_telegram_id, account_id, exc,
        )
        _tg_send_message(
            user_telegram_id,
            f"⚠️ Failed to fetch emails. Please try again from the bot.",
        )
