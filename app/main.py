"""
main.py

FastAPI app: Telegram webhook endpoint + DB startup. Kept minimal — routing
logic lives in telegram/handlers.py, everything else in its own module.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.db.queries import (
    SessionLocal,
    get_user_by_chat_id,
    init_db,
    run_migrations,
    save_google_credential,
    save_message,
)
from app.integrations.google_oauth import (
    exchange_code_for_tokens,
    expiry_from_token_response,
    verify_and_parse_state,
)
from app.jobs.daily_brief import RUN_INTERVAL_MINUTES, daily_brief_job
from app.jobs.price_alerts import RUN_INTERVAL_MINUTES as PRICE_ALERT_INTERVAL_MINUTES, price_alert_job
from app.telegram.client import send_message
from app.telegram.handlers import handle_update

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_all first so a brand new, empty DB (e.g. fresh local dev) has
    # every current table before migrations run; run_migrations then applies
    # anything create_all can't do (new columns on tables that already
    # existed) — both are no-ops when there's nothing pending, so this is
    # safe to run on every startup/redeploy, no manual SQL required anymore.
    await init_db()
    await asyncio.to_thread(run_migrations)
    # next_run_time=now makes the first check happen immediately on startup
    # instead of waiting a full interval — otherwise every restart/redeploy
    # opens a dead zone of up to RUN_INTERVAL_MINUTES before briefs/alerts
    # get checked at all.
    # misfire_grace_time=None: if the process is briefly delayed (slow
    # request, host scheduling hiccup, local dev machine sleep) and a tick
    # is late, run it anyway rather than APScheduler's default of silently
    # SKIPPING it — a late brief/alert is fine, a silently skipped one is
    # exactly the "bot went dark" failure mode this app can't afford.
    scheduler.add_job(
        daily_brief_job,
        IntervalTrigger(minutes=RUN_INTERVAL_MINUTES),
        next_run_time=datetime.now(),
        misfire_grace_time=None,
    )
    scheduler.add_job(
        price_alert_job,
        IntervalTrigger(minutes=PRICE_ALERT_INTERVAL_MINUTES),
        next_run_time=datetime.now(),
        misfire_grace_time=None,
    )
    scheduler.start()
    yield
    scheduler.shutdown()


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

    try:
        async with SessionLocal() as db:
            await handle_update(update, db)
    except Exception:
        # handle_update already guards its own internal failures and replies
        # to the user; this is only reached for something outside that (e.g.
        # a malformed update payload). Still return 200 so Telegram doesn't
        # retry-storm the same broken update.
        logging.exception("Unhandled error processing Telegram update")

    return {"ok": True}


@app.get("/oauth/google/callback", response_class=HTMLResponse)
async def google_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> str:
    if error or not code or not state:
        return "<h3>Something went wrong connecting your account — head back to Telegram and try again.</h3>"

    chat_id = verify_and_parse_state(state)
    if chat_id is None:
        return "<h3>Invalid or expired request — head back to Telegram and try again.</h3>"

    token_data = await exchange_code_for_tokens(code)
    if token_data is None:
        return "<h3>Couldn't complete the connection — head back to Telegram and try again.</h3>"

    async with SessionLocal() as db:
        user = await get_user_by_chat_id(db, chat_id)
        if user is None:
            return "<h3>Couldn't find your account — message the bot first, then try connecting again.</h3>"

        await save_google_credential(
            db,
            user.id,
            token_data["access_token"],
            token_data.get("refresh_token", ""),
            expiry_from_token_response(token_data),
        )

    confirmation = "Your Google account is connected — Gmail, Calendar, and Drive are all good to go."
    await send_message(chat_id, confirmation)
    # This message comes from an out-of-band HTTP callback, not the normal
    # conversation loop — without saving it, the model has no memory the
    # connection happened and can't answer "is it connected?" correctly.
    async with SessionLocal() as db:
        user = await get_user_by_chat_id(db, chat_id)
        if user is not None:
            await save_message(db, user.id, "assistant", confirmation)

    return "<h3>Connected. You can close this tab and go back to Telegram.</h3>"
