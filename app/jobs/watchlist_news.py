"""
jobs/watchlist_news.py

Polls each watchlist ticker for fresh headlines and pushes a Telegram
message the moment something new shows up, rather than making the user
wait for the next scheduled daily brief. This is the closest honest
equivalent to "notify me of major announcements" the current data source
(Finnhub company-news) supports — it is NOT pulling from EDGAR/SEC filings
directly, just news coverage, which commonly includes regulatory and
filing-related stories but isn't guaranteed to catch every filing.

No LLM call needed — same reasoning as price_alerts.py: the trigger
(new headline since last check) is deterministic, so a templated message
is faster and doesn't spend free-tier Gemini quota.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.ai.tools import get_news
from app.db.queries import SessionLocal, get_all_watchlist_items, mark_news_checked
from app.telegram.client import send_message

logger = logging.getLogger(__name__)

# Must match the interval this job is actually scheduled at (main.py).
RUN_INTERVAL_MINUTES = 20

MAX_HEADLINES_PER_CHECK = 2


async def check_watchlist_item(db, item) -> None:
    news = await get_news(item.ticker, days_back=1)
    if "error" in news:
        return

    checked_at = datetime.now(timezone.utc)
    last_check = item.last_news_check_at
    if last_check is None:
        # First time this ticker's ever been checked — establish a baseline
        # instead of blasting every headline from the last day as if it
        # just broke, the moment someone adds a ticker they've followed
        # for months.
        await mark_news_checked(db, item.id, checked_at)
        return

    if last_check.tzinfo is None:
        last_check = last_check.replace(tzinfo=timezone.utc)

    fresh = [
        h
        for h in news["headlines"]
        if h.get("datetime") and datetime.fromtimestamp(h["datetime"], tz=timezone.utc) > last_check
    ]
    if fresh:
        for headline in fresh[:MAX_HEADLINES_PER_CHECK]:
            message = f"{item.ticker} — {headline['headline']}"
            if headline.get("summary"):
                message += f"\n{headline['summary']}"
            if headline.get("url"):
                message += f"\n{headline['url']}"
            await send_message(item.user.telegram_chat_id, message)

    await mark_news_checked(db, item.id, checked_at)


async def watchlist_news_job() -> None:
    async with SessionLocal() as db:
        items = await get_all_watchlist_items(db)
        for item in items:
            try:
                await check_watchlist_item(db, item)
            except Exception:
                logger.exception(
                    "Watchlist news check failed for ticker=%s item_id=%s", item.ticker, item.id
                )
