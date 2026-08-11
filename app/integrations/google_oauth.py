"""
integrations/google_oauth.py

Manual OAuth2 flow (plain httpx calls to Google's endpoints, no
google-auth-oauthlib) — one combined consent for Gmail (read) + Calendar
(read/write events) + Drive (read), so the user connects all three at once
instead of separate flows. The `state` param carries the Telegram chat_id
so the callback knows which user just approved.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
from urllib.parse import urlencode

import httpx

from app.config import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/oauth/google/callback"


def _sign_chat_id(chat_id: int) -> str:
    # Signs the chat_id into the OAuth `state` param so the callback can't be
    # spoofed by a plain unauthenticated request supplying an arbitrary
    # state=<victim_chat_id> — the signature can only be produced with the
    # (server-only) client secret, so a forged state fails verification.
    return hmac.new(
        settings.google_oauth_client_secret.encode(), str(chat_id).encode(), hashlib.sha256
    ).hexdigest()[:16]


def build_authorize_url(chat_id: int) -> str:
    state = f"{chat_id}.{_sign_chat_id(chat_id)}"
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # ensures a refresh_token is issued every time
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def verify_and_parse_state(state: str) -> int | None:
    """Returns the chat_id if state's signature is valid, else None."""
    try:
        chat_id_str, signature = state.rsplit(".", 1)
        chat_id = int(chat_id_str)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign_chat_id(chat_id)):
        return None
    return chat_id


async def exchange_code_for_tokens(code: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


async def refresh_access_token(refresh_token: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "grant_type": "refresh_token",
                },
            )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


def expiry_from_token_response(token_data: dict) -> datetime.datetime:
    expires_in = token_data.get("expires_in", 3600)
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
