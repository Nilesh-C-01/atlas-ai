"""
jobs/daily_brief.py

Proactive daily brief: for each user with a watchlist, ask Gemini (via the
normal tool-calling loop, so it can pull live quotes/news itself) to
synthesize what's actually material. Stays silent if nothing meaningful
happened — never sends filler just to have sent something.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.ai.agent import handle_user_message
from app.ai.prompts import DAILY_BRIEF_SYSTEM_SUFFIX
from app.db.queries import SessionLocal, get_users_with_watchlist, resolve_timezone, save_message
from app.telegram.client import send_message

logger = logging.getLogger(__name__)

NOTHING_TO_REPORT = "NOTHING_TO_REPORT"
DEFAULT_BRIEFING_TIME = "08:00"  # used when the user hasn't set a preference

# The scheduler runs this job every RUN_INTERVAL_MINUTES; a user's briefing
# fires the first run whose time falls within that window of their preferred
# time, so the interval must match how often daily_brief_job is scheduled.
RUN_INTERVAL_MINUTES = 15

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

BRIEF_KICKOFF = (
    "Generate today's proactive daily brief for this user, using their "
    "watchlist and tools to check live prices/news as needed."
)


def _user_local_now(user, utc_now: datetime) -> datetime:
    """Resolves 'now' in the user's own timezone, freshly, on every call —
    this is what makes DST handled correctly with zero stored-offset drift,
    unlike caching a precomputed UTC target time would. Falls back to UTC
    if no/invalid timezone is on file, matching briefing_time's own
    fallback assumption in that case."""
    if user.timezone:
        tz = resolve_timezone(user.timezone)
        if tz is not None:
            return utc_now.astimezone(tz)
    return utc_now


def _is_due(briefing_time: str | None, local_now: datetime) -> bool:
    match = TIME_RE.search(briefing_time or DEFAULT_BRIEFING_TIME)
    if not match:
        match = TIME_RE.search(DEFAULT_BRIEFING_TIME)
    hour, minute = int(match.group(1)), int(match.group(2))
    target_minutes = hour * 60 + minute
    now_minutes = local_now.hour * 60 + local_now.minute
    # Modulo 1440 (minutes/day) so a target near midnight (e.g. 23:55) still
    # matches correctly right after midnight — plain subtraction would stay
    # negative forever and permanently skip that user's brief every day.
    return 0 <= (now_minutes - target_minutes) % 1440 < RUN_INTERVAL_MINUTES


async def run_daily_brief_for_user(db, user) -> None:
    try:
        reply = await handle_user_message(
            db, user, BRIEF_KICKOFF, system_suffix=DAILY_BRIEF_SYSTEM_SUFFIX
        )
    except Exception:
        logger.exception("Daily brief generation failed for user_id=%s", user.id)
        return

    if reply.strip() == NOTHING_TO_REPORT:
        return

    await send_message(user.telegram_chat_id, reply)
    await save_message(db, user.id, "assistant", reply)


async def daily_brief_job() -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        users = await get_users_with_watchlist(db)
        for user in users:
            if _is_due(user.briefing_time, _user_local_now(user, now)):
                await run_daily_brief_for_user(db, user)
