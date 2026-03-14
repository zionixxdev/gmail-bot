"""
bot.py — Main entry point for the GmailSaaS Telegram bot.

Responsibilities:
  - Configure logging (file + console).
  - Initialize the database (create tables).
  - Build the PTB Application with all handlers registered.
  - Start the bot (polling mode by default; webhook-ready via env flag).
  - Gracefully shut down on exit.

Usage:
    python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from telegram import Update
from telegram.ext import Application, ContextTypes

from config import BOT_NAME, BOT_TOKEN, LOG_FILE, LOG_LEVEL


# ─── Logging setup (must happen before any other imports that use logging) ────

def _setup_logging() -> None:
    """Configure root logger with both file and console handlers."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    log_level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
    for h in handlers:
        h.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))

    logging.basicConfig(level=log_level, handlers=handlers, force=True)

    # Silence overly verbose third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger(__name__)


# ─── Handler imports (after logging is set up) ────────────────────────────────

from db.session import dispose_engine, init_db
from handlers.accounts import register_account_handlers
from handlers.admin import register_admin_handlers
from handlers.inbox import register_inbox_handlers
from handlers.menu import register_menu_handlers
from handlers.payments import register_payment_handlers
from handlers.start import register_start_handlers


# ─── Global error handler ────────────────────────────────────────────────────

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all unhandled exceptions and notify the user with a friendly message."""
    logger.error(
        "Unhandled exception | Update: %s | Error: %s",
        update,
        context.error,
        exc_info=context.error,
    )

    user_message = (
        "⚠️ <b>Something went wrong.</b>\n\n"
        "An unexpected error occurred. Please try again, or use /start to restart the bot."
    )

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=user_message,
                parse_mode="HTML",
            )
        except Exception as send_exc:
            logger.error("Could not send error message to user: %s", send_exc)


# ─── /cancel command ──────────────────────────────────────────────────────────

from telegram.ext import CommandHandler


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel any ongoing operation and return to the main menu."""
    context.user_data.clear()
    from utils.keyboards import main_menu_keyboard
    from services.database import get_or_create_user

    tg_user = update.effective_user
    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    await update.message.reply_text(
        "✅ Operation cancelled. Returning to the main menu.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(is_admin=db_user.is_admin),
    )


# ─── Application builder ─────────────────────────────────────────────────────

def build_application() -> Application:
    """Construct and configure the PTB Application with all handlers."""
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set. Check your .env file.")
        sys.exit(1)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Register global error handler
    app.add_error_handler(global_error_handler)

    # Register /cancel command
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Register feature handlers (order matters for overlapping patterns)
    register_start_handlers(app)
    register_admin_handlers(app)       # Admin before menu to catch admin:* first
    register_account_handlers(app)
    register_inbox_handlers(app)
    register_payment_handlers(app)
    register_menu_handlers(app)        # Menu last (catches menu:* fallback)

    logger.info("All handlers registered.")
    return app


# ─── Startup / shutdown hooks ─────────────────────────────────────────────────

async def on_startup(app: Application) -> None:
    """Run once before the bot starts polling."""
    logger.info("Initializing database…")
    await init_db()
    logger.info("Database ready.")
    logger.info("%s is starting…", BOT_NAME)


async def on_shutdown(app: Application) -> None:
    """Run once after the bot stops polling."""
    logger.info("Disposing database engine…")
    await dispose_engine()
    logger.info("%s shut down cleanly.", BOT_NAME)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point: build application and start polling."""
    app = build_application()

    # Register startup/shutdown hooks
    app.post_init = on_startup       # type: ignore[assignment]
    app.post_shutdown = on_shutdown  # type: ignore[assignment]

    logger.info("Starting polling…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
