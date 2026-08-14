"""
jobs/reminders.py

Polls for due one-off reminders (set via the set_reminder tool, e.g. "remind
me an hour before Apple's earnings call") and pushes them the moment their
local target time arrives. Templated message, no LLM call — the trigger
(local now >= target) is deterministic.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.db.queries import (
    SessionLocal,
    get_pending_reminders,
    mark_reminder_sent,
    user_local_now,
)
from app.telegram.client import send_message

logger = logging.getLogger(__name__)

# Must match the interval this job is actually scheduled at (main.py) —
# tighter than the other jobs since reminders are time-specific asks, not
# daily/slow-moving checks.
RUN_INTERVAL_MINUTES = 5

REMIND_AT_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2}) (\d{1,2}):(\d{2})")


def _is_due(remind_at_local: str, local_now: datetime) -> bool:
    match = REMIND_AT_RE.match(remind_at_local)
    if not match:
        logger.warning("Unparseable remind_at_local value: %r", remind_at_local)
        return False
    year, month, day, hour, minute = (int(g) for g in match.groups())
    target = datetime(year, month, day, hour, minute, tzinfo=local_now.tzinfo)
    return local_now >= target


async def reminder_job() -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        reminders = await get_pending_reminders(db)
        for reminder in reminders:
            try:
                if _is_due(reminder.remind_at_local, user_local_now(reminder.user, now)):
                    await send_message(reminder.user.telegram_chat_id, f"Reminder: {reminder.message}")
                    await mark_reminder_sent(db, reminder.id)
            except Exception:
                logger.exception("Reminder check failed for reminder_id=%s", reminder.id)
