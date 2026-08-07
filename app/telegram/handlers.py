"""
telegram/handlers.py

Routes an incoming Telegram update to the right handling path. Only text is
wired up for day 1 — voice/image come later (locked scope, not skipped).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import handle_user_message
from app.ai.prompts import ONBOARDING_KICKOFF
from app.db.queries import get_or_create_user, save_message
from app.telegram.client import send_message


async def handle_update(update: dict[str, Any], db: AsyncSession) -> None:
    message = update.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    user = await get_or_create_user(db, chat_id)

    text = message.get("text")
    if text is None:
        await send_message(
            chat_id,
            "I can only read text for now — voice and photo support is coming soon.",
        )
        return

    is_first_message = text.strip() in ("/start", "start")

    try:
        if is_first_message:
            reply = await handle_user_message(db, user, ONBOARDING_KICKOFF)
        else:
            await save_message(db, user.id, "user", text)
            reply = await handle_user_message(db, user, text)
        await save_message(db, user.id, "assistant", reply)
    except Exception:
        reply = "Something went wrong on my end — mind trying that again?"

    await send_message(chat_id, reply)
