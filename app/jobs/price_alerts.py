"""
jobs/price_alerts.py

Polls Finnhub for every tracked ticker and fires two kinds of alerts:
1. Automatic: ticker moved >5% today (fires once per calendar day).
2. Explicit: user set a target price via the set_price_alert tool (one-shot,
   cleared after firing).

No LLM call needed here — the trigger is deterministic and the data is
already live from Finnhub, so a templated message is faster and doesn't
burn free-tier Gemini quota on something that doesn't need reasoning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.ai.tools import get_quote
from app.db.queries import SessionLocal, get_all_watchlist_items, mark_alert_sent
from app.telegram.client import send_message

logger = logging.getLogger(__name__)

# Must match the interval this job is actually scheduled at (main.py) — kept
# here, next to daily_brief.py's equivalent RUN_INTERVAL_MINUTES, as the
# single source of truth rather than a second hardcoded copy in main.py.
RUN_INTERVAL_MINUTES = 15

DAILY_MOVE_THRESHOLD_PERCENT = 5.0
ALERT_COOLDOWN = timedelta(hours=20)  # prevents re-firing the daily-move alert every poll


def _already_alerted_recently(last_alert_sent_at) -> bool:
    if last_alert_sent_at is None:
        return False
    now = datetime.now(timezone.utc)
    if last_alert_sent_at.tzinfo is None:
        last_alert_sent_at = last_alert_sent_at.replace(tzinfo=timezone.utc)
    return (now - last_alert_sent_at) < ALERT_COOLDOWN


async def check_watchlist_item(db, item) -> None:
    quote = await get_quote(item.ticker)
    if "error" in quote:
        return

    if item.alert_price is not None:
        # We only poll current price (not a continuous stream) and don't
        # store the price at alert-set time, so we can't reliably tell
        # whether the user meant "rises to" or "drops to" this level.
        # Firing when price is close to the target approximates both
        # directions reasonably for a one-shot notification.
        near_target = abs(quote["current_price"] - item.alert_price) / item.alert_price < 0.02
        if near_target:
            message = (
                f"{item.ticker} just hit ${quote['current_price']:.2f} — "
                f"you asked to be told when it reached ${item.alert_price:.2f}."
            )
            await send_message(item.user.telegram_chat_id, message)
            await mark_alert_sent(db, item.id, clear_target=True)
            return

    if not _already_alerted_recently(item.last_alert_sent_at):
        percent_change = quote["percent_change"]
        if abs(percent_change) >= DAILY_MOVE_THRESHOLD_PERCENT:
            direction = "up" if percent_change > 0 else "down"
            message = (
                f"{item.ticker} is {direction} {abs(percent_change):.1f}% today, "
                f"now at ${quote['current_price']:.2f} — worth a look."
            )
            await send_message(item.user.telegram_chat_id, message)
            await mark_alert_sent(db, item.id, clear_target=False)


async def price_alert_job() -> None:
    async with SessionLocal() as db:
        items = await get_all_watchlist_items(db)
        for item in items:
            try:
                await check_watchlist_item(db, item)
            except Exception:
                logger.exception(
                    "Price alert check failed for ticker=%s item_id=%s", item.ticker, item.id
                )
