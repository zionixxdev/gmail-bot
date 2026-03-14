"""
services/gmail_service.py — Gmail API integration via OAuth 2.0.

Responsibilities:
  - Build OAuth authorization URL with CSRF state token.
  - Exchange authorization code for tokens.
  - Refresh access tokens transparently.
  - Fetch inbox messages (list + full).
  - Parse message headers and body.
"""

from __future__ import annotations

import base64
import email as email_lib
import json
import logging
import re
import secrets
import textwrap
from typing import Any, Dict, List, Optional, Tuple

import redis
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    GOOGLE_SCOPES,
    OAUTH_STATE_PREFIX,
    OAUTH_STATE_TTL,
    REDIS_URL,
)
from services.encryption import decrypt, encrypt

logger = logging.getLogger(__name__)

# ─── Redis client ─────────────────────────────────────────────────────────────

_redis_client: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


# ─── OAuth flow ───────────────────────────────────────────────────────────────

def _build_flow() -> Flow:
    """Construct a google_auth_oauthlib Flow from env credentials."""
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uris": [GOOGLE_REDIRECT_URI],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    return flow


def generate_oauth_url(telegram_id: int) -> str:
    """Generate an OAuth authorization URL and store the state in Redis.

    A unique, cryptographically random state token is created, which
    encodes the user's Telegram ID. The state is stored in Redis with a
    short TTL to prevent CSRF attacks.

    Args:
        telegram_id: The user's Telegram ID to embed in state.

    Returns:
        Full authorization URL to send to the user.
    """
    r = _get_redis()
    state_token = secrets.token_urlsafe(32)
    state_data = json.dumps({"telegram_id": telegram_id, "nonce": secrets.token_hex(8)})
    encrypted_state = encrypt(state_data)

    r.setex(
        f"{OAUTH_STATE_PREFIX}{state_token}",
        OAUTH_STATE_TTL,
        encrypted_state,
    )

    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=state_token,
        prompt="consent",  # Force consent to always get refresh_token
    )
    logger.info("OAuth URL generated for tg_id=%d", telegram_id)
    return auth_url


def validate_oauth_state(state_token: str) -> Optional[int]:
    """Validate the OAuth state token from the callback.

    Args:
        state_token: The ``state`` parameter received in the callback URL.

    Returns:
        The Telegram ID embedded in the state, or None if invalid/expired.
    """
    r = _get_redis()
    redis_key = f"{OAUTH_STATE_PREFIX}{state_token}"
    encrypted_state = r.get(redis_key)
    if not encrypted_state:
        logger.warning("OAuth state not found or expired: %s", state_token)
        return None

    try:
        state_data = json.loads(decrypt(encrypted_state))
        telegram_id = int(state_data["telegram_id"])
        r.delete(redis_key)  # One-time use
        logger.info("OAuth state validated for tg_id=%d", telegram_id)
        return telegram_id
    except Exception as exc:
        logger.error("Failed to validate OAuth state: %s", exc)
        return None


def exchange_code_for_tokens(code: str) -> Tuple[str, str]:
    """Exchange an authorization code for an access + refresh token pair.

    Args:
        code: The authorization code from Google's callback.

    Returns:
        Tuple of (email_address, encrypted_refresh_token).

    Raises:
        ValueError: If token exchange fails or no refresh token was returned.
    """
    flow = _build_flow()
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        raise ValueError(f"Token exchange failed: {exc}") from exc

    credentials = flow.credentials
    if not credentials.refresh_token:
        raise ValueError(
            "No refresh token returned. The user may have already authorized this app — "
            "revoke access at https://myaccount.google.com/permissions and retry."
        )

    # Fetch the user's email address
    service = build("oauth2", "v2", credentials=credentials)
    user_info = service.userinfo().get().execute()
    email = user_info.get("email", "")

    encrypted_refresh_token = encrypt(credentials.refresh_token)
    logger.info("Tokens exchanged for email=%s", email)
    return email, encrypted_refresh_token


# ─── Gmail service builder ───────────────────────────────────────────────────

def _build_gmail_service(encrypted_refresh_token: str):
    """Reconstruct a Google Credentials object and build the Gmail service.

    The credentials are refreshed transparently if the access token has expired.

    Args:
        encrypted_refresh_token: Fernet-encrypted refresh token from DB.

    Returns:
        An authorized Gmail API Resource object.
    """
    refresh_token = decrypt(encrypted_refresh_token)
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=GOOGLE_SCOPES,
    )
    if not credentials.valid:
        credentials.refresh(Request())

    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


# ─── Inbox ───────────────────────────────────────────────────────────────────

def fetch_inbox_summary(
    encrypted_refresh_token: str, max_results: int = 5, page_token: Optional[str] = None
) -> Dict[str, Any]:
    """Fetch a page of inbox messages (summary: sender, subject, snippet).

    Args:
        encrypted_refresh_token: From DB.
        max_results:             Number of messages to return (default 5).
        page_token:              For pagination; pass the nextPageToken from
                                 a previous call.

    Returns:
        Dict with keys:
            ``messages``      — list of message summary dicts.
            ``next_page_token`` — token for the next page, or None.
    """
    service = _build_gmail_service(encrypted_refresh_token)
    try:
        list_response = (
            service.users()
            .messages()
            .list(
                userId="me",
                labelIds=["INBOX"],
                maxResults=max_results,
                pageToken=page_token or None,
            )
            .execute()
        )
    except HttpError as exc:
        raise ValueError(f"Gmail API error listing messages: {exc}") from exc

    messages_raw = list_response.get("messages", [])
    next_page_token = list_response.get("nextPageToken")

    summaries = []
    for msg_ref in messages_raw:
        try:
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            summaries.append(
                {
                    "id": msg["id"],
                    "thread_id": msg.get("threadId", ""),
                    "from": headers.get("From", "Unknown"),
                    "subject": headers.get("Subject", "(no subject)"),
                    "date": headers.get("Date", ""),
                    "snippet": msg.get("snippet", ""),
                }
            )
        except HttpError as exc:
            logger.warning("Could not fetch message %s: %s", msg_ref["id"], exc)

    return {"messages": summaries, "next_page_token": next_page_token}


def fetch_full_email(
    encrypted_refresh_token: str, message_id: str
) -> Dict[str, str]:
    """Fetch the full content of a single email.

    Attempts to retrieve plain-text body; falls back to HTML → strip tags.

    Args:
        encrypted_refresh_token: From DB.
        message_id:              Gmail message ID.

    Returns:
        Dict with keys: id, from, subject, date, body.
    """
    service = _build_gmail_service(encrypted_refresh_token)
    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except HttpError as exc:
        raise ValueError(f"Gmail API error fetching message {message_id}: {exc}") from exc

    headers = {
        h["name"]: h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }

    body = _extract_body(msg.get("payload", {}))
    return {
        "id": message_id,
        "from": headers.get("From", "Unknown"),
        "subject": headers.get("Subject", "(no subject)"),
        "date": headers.get("Date", ""),
        "body": body or "(empty body)",
    }


# ─── Body extraction helpers ─────────────────────────────────────────────────

def _extract_body(payload: Dict) -> str:
    """Recursively extract plain-text (or HTML-stripped) body from payload."""
    mime_type = payload.get("mimeType", "")
    parts = payload.get("parts", [])

    if mime_type == "text/plain":
        return _decode_body_data(payload.get("body", {}).get("data", ""))

    if mime_type == "text/html":
        raw_html = _decode_body_data(payload.get("body", {}).get("data", ""))
        return _strip_html(raw_html)

    # Multipart: prefer text/plain part
    plain_body = ""
    html_body = ""
    for part in parts:
        result = _extract_body(part)
        if part.get("mimeType") == "text/plain" and result:
            plain_body = result
        elif part.get("mimeType") == "text/html" and result:
            html_body = result
        elif result:
            plain_body = plain_body or result

    return plain_body or html_body


def _decode_body_data(data: str) -> str:
    """Decode Gmail's URL-safe base64 body data."""
    if not data:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        return decoded
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    """Minimal HTML tag stripper for email bodies."""
    clean = re.sub(r"<[^>]+>", "", html)
    clean = re.sub(r"&nbsp;", " ", clean)
    clean = re.sub(r"&amp;", "&", clean)
    clean = re.sub(r"&lt;", "<", clean)
    clean = re.sub(r"&gt;", ">", clean)
    clean = re.sub(r"\s{3,}", "\n\n", clean)
    return clean.strip()


def chunk_text(text: str, max_len: int = 3800) -> List[str]:
    """Split a long string into Telegram-safe chunks.

    Args:
        text:    The text to split.
        max_len: Maximum length of each chunk.

    Returns:
        List of string chunks, each at most max_len characters.
    """
    if len(text) <= max_len:
        return [text]
    return textwrap.wrap(text, width=max_len, break_long_words=True, break_on_hyphens=False)
