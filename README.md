# GmailSaaS Bot 📬

A production-ready Telegram SaaS bot that lets users link their Gmail accounts via OAuth 2.0 and read their inbox directly from Telegram. Built with Python 3.11+, `python-telegram-bot` v20+, SQLAlchemy 2.0, Redis, and the official Gmail API.

---

## Features

- **Gmail OAuth 2.0** — Secure account linking (read-only scope)
- **Inbox Browsing** — View, paginate, and read full emails in Telegram
- **Credit System** — Buy credits to link accounts; manual admin payment verification
- **Admin Panel** — Stats, user management, payment approval, broadcast messages
- **Background Jobs** — Redis + RQ for broadcasts and email fetching
- **Force Join** — Optional channel membership enforcement
- **Encryption** — All refresh tokens encrypted with Fernet (AES-128)
- **Rate Limiting** — Per-user TTLCache-based protection
- **Multi-database** — SQLite (dev) or PostgreSQL (prod) via SQLAlchemy

---

## Project Structure

```
gmail_saas_bot/
├── bot.py                  # Main entry point
├── config.py               # All settings loaded from .env
├── init_db.py              # DB table creation script
├── generate_key.py         # Fernet key generator utility
├── requirements.txt
├── .env.example
├── db/
│   ├── base.py             # SQLAlchemy DeclarativeBase
│   ├── models.py           # ORM models: User, GmailAccount, Payment, BroadcastMessage
│   └── session.py          # Async engine + session factory
├── handlers/
│   ├── start.py            # /start command, force-join re-check
│   ├── menu.py             # Main menu navigation callbacks
│   ├── accounts.py         # Link, list, remove Gmail accounts
│   ├── inbox.py            # Inbox browsing + full email reader
│   ├── payments.py         # Buy credits, plan selection, "I paid" flow
│   └── admin.py            # Admin panel: stats, payments, broadcast, ban
├── services/
│   ├── encryption.py       # Fernet encrypt/decrypt
│   ├── gmail_service.py    # OAuth flow, Gmail API client
│   ├── database.py         # High-level async DB helpers
│   └── payment_service.py  # Payment initiation and approval logic
├── utils/
│   ├── keyboards.py        # All InlineKeyboardMarkup builders
│   ├── decorators.py       # @require_joined, @admin_only, @rate_limit, @not_banned
│   └── helpers.py          # Formatting, message splitting, safe edit/reply
├── workers/
│   ├── tasks.py            # RQ tasks: broadcast, fetch_and_notify
│   └── worker.py           # RQ worker entry point
├── web/
│   └── oauth_server.py     # Flask app for Google OAuth callback
└── logs/
    └── bot.log             # Runtime log file
```

---

## Prerequisites

- Python 3.11+
- Redis (local or managed)
- A Google Cloud project with Gmail API enabled
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

---

## Setup Guide

### 1. Clone and install

```bash
git clone https://github.com/zionixxdev/gmail-bot.git
cd gmail-saas-bot
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in every variable. Key ones:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | From @BotFather |
| `ADMIN_IDS` | Your Telegram user ID(s) |
| `ENCRYPTION_KEY` | Generate with `python generate_key.py` |
| `GOOGLE_CLIENT_ID` | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | From Google Cloud Console |
| `GOOGLE_REDIRECT_URI` | Must match exactly in Google Console |
| `DATABASE_URL` | SQLite (default) or PostgreSQL |
| `REDIS_URL` | Redis connection string |

### 3. Generate encryption key

```bash
python generate_key.py
# Copy the output key into ENCRYPTION_KEY in your .env
```

> ⚠️ **Never change this key after first use.** It will invalidate all stored tokens.

### 4. Set up Google Cloud

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable **Gmail API** and **Google People API** (`userinfo.email`)
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Application type: **Web application**
6. Add authorized redirect URI:
   - Development: `http://localhost:8080/oauth/callback`
   - Production: `https://yourdomain.com/oauth/callback`
7. Copy **Client ID** and **Client Secret** into `.env`
8. Configure the **OAuth consent screen** (add your test users while in dev mode)

### 5. Initialize the database

```bash
python init_db.py
```

### 6. Start Redis

```bash
# macOS (Homebrew)
brew services start redis

# Linux
sudo systemctl start redis

# Docker
docker run -d -p 6379:6379 redis:7-alpine
```

### 7. Start the OAuth callback server

In a separate terminal:

```bash
python web/oauth_server.py
# Or with gunicorn for production:
# gunicorn "web.oauth_server:create_app()" --bind 0.0.0.0:8080
```

### 8. Start the RQ worker (optional but recommended)

```bash
python workers/worker.py
```

### 9. Start the bot

```bash
python bot.py
```

---

## Production Deployment

### Using systemd (Linux)

Create `/etc/systemd/system/gmailbot.service`:

```ini
[Unit]
Description=GmailSaaS Telegram Bot
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/gmail-saas-bot
EnvironmentFile=/home/ubuntu/gmail-saas-bot/.env
ExecStart=/home/ubuntu/gmail-saas-bot/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Repeat for `gmailbot-worker.service` and `gmailbot-oauth.service`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gmailbot gmailbot-worker gmailbot-oauth
```

### Using Docker Compose

```yaml
version: "3.9"
services:
  redis:
    image: redis:7-alpine
    restart: always

  bot:
    build: .
    command: python bot.py
    env_file: .env
    depends_on: [redis]
    restart: always
    volumes:
      - ./logs:/app/logs
      - ./data.db:/app/data.db

  worker:
    build: .
    command: python workers/worker.py
    env_file: .env
    depends_on: [redis]
    restart: always

  oauth:
    build: .
    command: gunicorn "web.oauth_server:create_app()" --bind 0.0.0.0:8080
    env_file: .env
    ports:
      - "8080:8080"
    restart: always
```

### PostgreSQL setup

1. Change `DATABASE_URL` in `.env`:
   ```
   DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/gmailsaas
   ```
2. Run `python init_db.py` — tables are created automatically.

### Expose the OAuth callback server

For production, the OAuth server must be publicly accessible via HTTPS.

**Using Nginx as a reverse proxy:**

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location /oauth/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Admin Commands

| Command | Description |
|---|---|
| `/admin` | Open admin panel |
| `/addcredits <tg_id> <amount>` | Add credits to a user |
| `/ban <tg_id>` | Ban a user |
| `/unban <tg_id>` | Unban a user |

---

## Credit System

| Plan | Credits | Price |
|---|---|---|
| Basic | 20 | $5 |
| Pro | 100 | $20 |
| Enterprise | Unlimited | $50 |

1 credit = 1 linked Gmail account.

---

## Security Notes

- All OAuth refresh tokens are Fernet-encrypted at rest
- OAuth state tokens are single-use with a 10-minute TTL in Redis
- Only read-only Gmail scope is requested
- Admin IDs are validated against a static allowlist in `.env`
- Force-join checks are cached for 5 minutes to avoid API spam
- Rate limiting is applied per-user per-action (configurable)

---

## Branding

Built by [@zionixpy](https://t.me/zionixpy) — [@zionixportal](https://t.me/zionixportal)
