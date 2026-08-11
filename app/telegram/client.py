"""
telegram/client.py

Thin wrapper around the Telegram Bot HTTP API — just the calls Atlas needs
(send message, set webhook). No bot framework; webhooks are handled directly
in FastAPI so behavior stays easy to trace under time pressure.
"""

from __future__ import annotations

import httpx

from app.config import settings

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


async def send_message(chat_id: int, text: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
        except httpx.HTTPError:
            # Nothing we can do if Telegram itself is unreachable — swallow
            # so a flaky delivery doesn't crash the webhook handler.
            pass


async def send_typing_action(chat_id: int) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(
                f"{TELEGRAM_API}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )
        except httpx.HTTPError:
            pass


async def set_webhook(url: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        params = {"url": url}
        if settings.telegram_webhook_secret:
            params["secret_token"] = settings.telegram_webhook_secret
        resp = await client.post(f"{TELEGRAM_API}/setWebhook", params=params)
        return resp.json()


async def get_file_path(file_id: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
            data = resp.json()
    except httpx.HTTPError:
        return None
    if not data.get("ok"):
        return None
    return data["result"]["file_path"]


def file_download_url(file_path: str) -> str:
    return f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"


async def download_file(file_id: str) -> bytes | None:
    file_path = await get_file_path(file_id)
    if file_path is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(file_download_url(file_path))
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return resp.content
