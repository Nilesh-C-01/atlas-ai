"""
main.py

FastAPI app: Telegram webhook endpoint + DB startup. Kept minimal — routing
logic lives in telegram/handlers.py, everything else in its own module.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from app.config import settings
from app.db.queries import SessionLocal, init_db
from app.telegram.handlers import handle_update


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=401, detail="bad secret token")

    update = await request.json()

    async with SessionLocal() as db:
        await handle_update(update, db)

    return {"ok": True}
