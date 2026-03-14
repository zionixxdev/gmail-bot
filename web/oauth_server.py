"""
web/oauth_server.py — Flask OAuth 2.0 callback server.

This Flask app runs alongside the Telegram bot (in a separate process or
thread) and handles the Google OAuth redirect URI after the user authorizes
access.

Flow:
  1. User clicks the OAuth URL sent by the bot.
  2. Google redirects to GET /oauth/callback?code=...&state=...
  3. This server:
       a. Validates the state token against Redis.
       b. Exchanges the code for tokens via google_auth_oauthlib.
       c. Encrypts and stores the refresh token in the DB.
       d. Deducts 1 credit from the user.
       e. Notifies the user via Telegram Bot API (sendMessage).
       f. Returns a confirmation HTML page.

Run as:
    python web/oauth_server.py
Or via gunicorn:
    gunicorn "web.oauth_server:create_app()" --bind 0.0.0.0:8080
"""

from __future__ import annotations

import logging
import os
import sys

# Ensure project root in path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests as http_requests
from flask import Flask, jsonify, redirect, render_template_string, request, url_for

from config import (
    BOT_TOKEN,
    CREDITS_PER_ACCOUNT,
    OAUTH_SERVER_HOST,
    OAUTH_SERVER_PORT,
)
from services.encryption import encrypt
from services.gmail_service import exchange_code_for_tokens, validate_oauth_state

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ─── HTML templates ───────────────────────────────────────────────────────────

_SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Gmail Linked ✅</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           display:flex; align-items:center; justify-content:center;
           min-height:100vh; margin:0; background:#0f172a; color:#e2e8f0; }
    .card { background:#1e293b; border-radius:16px; padding:2.5rem 3rem;
            text-align:center; box-shadow:0 25px 50px rgba(0,0,0,.4);
            max-width:420px; width:90%; }
    .icon { font-size:3.5rem; margin-bottom:1rem; }
    h1 { margin:0 0 .5rem; font-size:1.5rem; color:#34d399; }
    p { color:#94a3b8; line-height:1.6; margin:.5rem 0; }
    .email { background:#0f172a; padding:.4rem .8rem; border-radius:8px;
             font-family:monospace; color:#38bdf8; font-size:.9rem;
             display:inline-block; margin:.5rem 0; }
    .btn { display:inline-block; margin-top:1.5rem; padding:.65rem 1.4rem;
           background:#2563eb; color:#fff; border-radius:9px;
           text-decoration:none; font-weight:600; font-size:.9rem; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Gmail Account Linked!</h1>
    <p>Successfully linked:</p>
    <span class="email">{{ email }}</span>
    <p>Return to Telegram to access your inbox.</p>
    <a class="btn" href="https://t.me/{{ bot_username }}">Open Bot →</a>
  </div>
</body>
</html>
"""

_ERROR_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Error ❌</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           display:flex; align-items:center; justify-content:center;
           min-height:100vh; margin:0; background:#0f172a; color:#e2e8f0; }
    .card { background:#1e293b; border-radius:16px; padding:2.5rem 3rem;
            text-align:center; max-width:420px; width:90%;
            box-shadow:0 25px 50px rgba(0,0,0,.4); }
    .icon { font-size:3.5rem; margin-bottom:1rem; }
    h1 { color:#f87171; font-size:1.4rem; margin:0 0 .5rem; }
    p  { color:#94a3b8; line-height:1.6; }
    code { background:#0f172a; padding:.2rem .5rem; border-radius:5px;
           font-size:.85rem; color:#fbbf24; }
    .btn { display:inline-block; margin-top:1.5rem; padding:.65rem 1.4rem;
           background:#374151; color:#fff; border-radius:9px;
           text-decoration:none; font-weight:600; font-size:.9rem; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">❌</div>
    <h1>Authorization Failed</h1>
    <p>{{ message }}</p>
    <p><code>{{ detail }}</code></p>
    <a class="btn" href="https://t.me/{{ bot_username }}">Back to Bot</a>
  </div>
</body>
</html>
"""


# ─── App factory ──────────────────────────────────────────────────────────────

def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder="templates")
    app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(32).hex())

    # ── Health check ──────────────────────────────────────────────────────────

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    # ── OAuth callback ────────────────────────────────────────────────────────

    @app.route("/oauth/callback")
    def oauth_callback():
        """Handle Google OAuth redirect."""
        code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")

        bot_username = os.getenv("BOT_USERNAME", "mybot")

        if error:
            logger.warning("OAuth callback error: %s", error)
            _notify_user_error(None, f"Authorization was denied or failed: {error}")
            return render_template_string(
                _ERROR_HTML,
                message="You declined authorization or an error occurred.",
                detail=error,
                bot_username=bot_username,
            ), 400

        if not code or not state:
            return render_template_string(
                _ERROR_HTML,
                message="Missing required parameters.",
                detail="code or state parameter absent",
                bot_username=bot_username,
            ), 400

        # Validate state token → get telegram_id
        telegram_id = validate_oauth_state(state)
        if telegram_id is None:
            return render_template_string(
                _ERROR_HTML,
                message="Invalid or expired authorization link.",
                detail="State token not found in Redis — link may have expired (10 min TTL).",
                bot_username=bot_username,
            ), 400

        # Exchange code for tokens
        try:
            email, encrypted_refresh_token = exchange_code_for_tokens(code)
        except ValueError as exc:
            logger.error("Token exchange failed for tg_id=%d: %s", telegram_id, exc)
            _notify_user_error(telegram_id, str(exc))
            return render_template_string(
                _ERROR_HTML,
                message="Token exchange failed.",
                detail=str(exc),
                bot_username=bot_username,
            ), 500

        # Persist account and deduct credit (synchronous DB ops via sync SQLAlchemy)
        try:
            _save_account_sync(telegram_id, email, encrypted_refresh_token)
        except Exception as exc:
            logger.error(
                "Failed to save Gmail account for tg_id=%d email=%s: %s",
                telegram_id, email, exc,
            )
            _notify_user_error(
                telegram_id,
                f"Account linking failed during database save: {exc}",
            )
            return render_template_string(
                _ERROR_HTML,
                message="Database error while saving your account.",
                detail=str(exc),
                bot_username=bot_username,
            ), 500

        # Notify the user via Telegram
        _notify_user_success(telegram_id, email)

        return render_template_string(
            _SUCCESS_HTML,
            email=email,
            bot_username=bot_username,
        )

    return app


# ─── Sync DB helpers (Flask runs sync) ───────────────────────────────────────

def _save_account_sync(telegram_id: int, email: str, encrypted_refresh_token: str) -> None:
    """Persist the Gmail account and deduct 1 credit using sync SQLAlchemy.

    Raises:
        ValueError: If the user doesn't exist or has insufficient credits.
        RuntimeError: If a DB operation fails.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from config import DATABASE_URL, CREDITS_PER_ACCOUNT
    from db.models import GmailAccount, User

    sync_url = (
        DATABASE_URL
        .replace("+aiosqlite", "")
        .replace("+asyncpg", "+psycopg2")
    )
    engine = create_engine(sync_url)

    with Session(engine) as session:
        # Get user
        user = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()

        if user is None:
            raise ValueError(f"User {telegram_id} not found in database.")

        if user.credits < CREDITS_PER_ACCOUNT:
            raise ValueError(
                f"Insufficient credits. Need {CREDITS_PER_ACCOUNT}, have {user.credits}."
            )

        # Check for duplicate account
        existing = session.execute(
            select(GmailAccount).where(
                GmailAccount.user_id == user.id,
                GmailAccount.email == email,
            )
        ).scalar_one_or_none()

        if existing:
            # Update the refresh token (re-linking)
            existing.encrypted_refresh_token = encrypted_refresh_token
            logger.info("Re-linked existing account: %s for user %d", email, telegram_id)
        else:
            # Create new account and deduct credit
            account = GmailAccount(
                user_id=user.id,
                email=email,
                encrypted_refresh_token=encrypted_refresh_token,
            )
            session.add(account)
            user.credits -= CREDITS_PER_ACCOUNT
            logger.info(
                "Linked new Gmail account: %s for tg_id=%d (credits: %d → %d)",
                email, telegram_id, user.credits + CREDITS_PER_ACCOUNT, user.credits,
            )

        session.commit()


# ─── Telegram notification helpers ────────────────────────────────────────────

def _notify_user_success(telegram_id: int, email: str) -> None:
    """Send a Telegram success notification to the user."""
    text = (
        f"✅ <b>Gmail Account Linked!</b>\n\n"
        f"📧 <code>{email}</code> has been successfully connected.\n\n"
        f"Use the <b>Inbox</b> button in the main menu to read your emails."
    )
    _send_telegram_message(telegram_id, text)


def _notify_user_error(telegram_id: int | None, reason: str) -> None:
    """Send a Telegram error notification to the user (if telegram_id is known)."""
    if telegram_id is None:
        return
    text = (
        f"❌ <b>Gmail Linking Failed</b>\n\n"
        f"Reason: {reason}\n\n"
        f"Please try again from the bot. If the problem persists, contact support."
    )
    _send_telegram_message(telegram_id, text)


def _send_telegram_message(chat_id: int, text: str) -> None:
    """Send a message via raw Telegram Bot API HTTP call."""
    try:
        resp = http_requests.post(
            TELEGRAM_API + "/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if not resp.ok:
            logger.warning(
                "Telegram notify failed for %d: %s %s", chat_id, resp.status_code, resp.text
            )
    except Exception as exc:
        logger.error("Failed to send Telegram message to %d: %s", chat_id, exc)


# ─── Standalone runner ────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info(
        "Starting OAuth callback server on %s:%d", OAUTH_SERVER_HOST, OAUTH_SERVER_PORT
    )
    app = create_app()
    app.run(host=OAUTH_SERVER_HOST, port=OAUTH_SERVER_PORT, debug=False)
