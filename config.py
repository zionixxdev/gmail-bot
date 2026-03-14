"""
config.py — Environment variables, constants, and plan definitions.
All settings are loaded from .env via python-dotenv.
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()


# ─── Telegram ────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: List[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]
REQUIRED_CHANNELS: List[str] = [
    x.strip() for x in os.getenv("REQUIRED_CHANNELS", "").split(",") if x.strip()
]
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "mybot")

# ─── Database ────────────────────────────────────────────────────────────────

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data.db")
# For PostgreSQL: postgresql+asyncpg://user:pass@host/dbname

# ─── Redis ───────────────────────────────────────────────────────────────────

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ─── Encryption ──────────────────────────────────────────────────────────────

ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")  # Fernet 32-byte base64 key

# ─── Google OAuth ────────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI: str = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8080/oauth/callback"
)
GOOGLE_SCOPES: List[str] = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

# ─── OAuth callback server ───────────────────────────────────────────────────

OAUTH_SERVER_HOST: str = os.getenv("OAUTH_SERVER_HOST", "0.0.0.0")
OAUTH_SERVER_PORT: int = int(os.getenv("OAUTH_SERVER_PORT", "8080"))

# Redis key prefix for OAuth state tokens
OAUTH_STATE_PREFIX: str = "oauth_state:"
OAUTH_STATE_TTL: int = 600  # seconds (10 minutes)

# ─── Credit / plan system ────────────────────────────────────────────────────

CREDITS_PER_ACCOUNT: int = int(os.getenv("CREDITS_PER_ACCOUNT", "1"))
DEFAULT_DAILY_LIMIT: int = int(os.getenv("DEFAULT_DAILY_LIMIT", "20"))  # inbox fetches/day
NEW_USER_CREDITS: int = int(os.getenv("NEW_USER_CREDITS", "0"))


@dataclass
class Plan:
    name: str
    credits: int
    price_usd: float
    description: str
    is_unlimited: bool = False


PLANS: Dict[str, Plan] = {
    "basic": Plan(
        name="Basic",
        credits=20,
        price_usd=5.0,
        description="20 credits — link up to 20 accounts",
    ),
    "pro": Plan(
        name="Pro",
        credits=100,
        price_usd=20.0,
        description="100 credits — link up to 100 accounts",
    ),
    "enterprise": Plan(
        name="Enterprise",
        credits=99999,
        price_usd=50.0,
        description="Unlimited credits — no limits",
        is_unlimited=True,
    ),
}

# ─── Payment ─────────────────────────────────────────────────────────────────

PAYMENT_CRYPTO_ADDRESS: str = os.getenv("PAYMENT_CRYPTO_ADDRESS", "YOUR_CRYPTO_ADDRESS")
PAYMENT_UPI_ID: str = os.getenv("PAYMENT_UPI_ID", "yourname@upi")
PAYMENT_NOTE: str = os.getenv(
    "PAYMENT_NOTE",
    "Send exact amount and include your Telegram ID in the note/memo.",
)

# ─── Force join cache ────────────────────────────────────────────────────────

FORCE_JOIN_CACHE_TTL: int = 300  # seconds (5 minutes)

# ─── Inbox display ───────────────────────────────────────────────────────────

INBOX_PAGE_SIZE: int = 5       # emails shown per page
EMAIL_BODY_MAX_LEN: int = 3800  # Telegram message limit is 4096; leave buffer

# ─── Logging ─────────────────────────────────────────────────────────────────

LOG_FILE: str = os.getenv("LOG_FILE", "logs/bot.log")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ─── Branding ────────────────────────────────────────────────────────────────

BOT_NAME: str = os.getenv("BOT_NAME", "GmailSaaS Bot")
SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "@zionixportal")
