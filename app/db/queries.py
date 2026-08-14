"""
db/queries.py

Async engine/session setup + the small set of query helpers the bot needs.
Kept as plain functions over an AsyncSession rather than a repository class —
there aren't enough queries yet to justify the abstraction.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import Base, GoogleCredential, MemoryFact, Message, User, WatchlistItem

# Common non-IANA labels models might pass despite being asked for an IANA
# name — mapped here so a plain "IST"/"EST" from the model doesn't fail
# zoneinfo lookup and force a re-ask.
_TZ_ALIASES = {
    "IST": "Asia/Kolkata",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "GMT": "UTC",
    "BST": "Europe/London",
    "CET": "Europe/Paris",
    "JST": "Asia/Tokyo",
    "SGT": "Asia/Singapore",
    "AEST": "Australia/Sydney",
}


def resolve_timezone(raw: str) -> ZoneInfo | None:
    """Best-effort resolution of a timezone label to a real ZoneInfo — the
    only arithmetic-adjacent step we trust an LLM's raw string for; actual
    UTC conversion always happens here in Python, never in the model."""
    candidate = _TZ_ALIASES.get(raw.strip().upper(), raw.strip())
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return None

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def run_migrations() -> None:
    """Applies any pending Alembic migrations (new columns/tables since the
    last deploy) — run this once at startup instead of hand-writing ALTER
    TABLE in Railway's console. Sync on purpose (Alembic's command API is
    sync; migrations/env.py handles the actual async DB connection itself),
    so callers on the async startup path must wrap it in asyncio.to_thread."""
    import pathlib

    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import ProgrammingError

    repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(repo_root / "alembic.ini"))
    try:
        command.upgrade(cfg, "head")
    except ProgrammingError as exc:
        # The very first run against a DB that already has these tables
        # (created pre-Alembic via create_all, e.g. the live Railway DB as
        # of this migration) has no alembic_version row yet, so it tries to
        # CREATE TABLE on tables that already exist. Self-heal once: mark
        # the baseline as already satisfied, then re-run for anything
        # actually new (like this same deploy's new column).
        if "already exists" not in str(exc).lower():
            raise
        command.stamp(cfg, "0001_baseline")
        command.upgrade(cfg, "head")


async def get_or_create_user(db: AsyncSession, telegram_chat_id: int) -> User:
    result = await db.execute(select(User).where(User.telegram_chat_id == telegram_chat_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_chat_id=telegram_chat_id)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


async def save_message(db: AsyncSession, user_id: int, role: str, content: str) -> None:
    db.add(Message(user_id=user_id, role=role, content=content))
    await db.commit()


async def get_recent_messages(db: AsyncSession, user_id: int, limit: int = 20) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.user_id == user_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def get_memory_facts(db: AsyncSession, user_id: int, limit: int = 20) -> list[dict]:
    result = await db.execute(
        select(MemoryFact)
        .where(MemoryFact.user_id == user_id)
        .order_by(MemoryFact.created_at.desc())
        .limit(limit)
    )
    facts = result.scalars().all()
    return [{"fact": f.fact, "category": f.category} for f in facts]


# Categories that represent a single current-state attribute rather than an
# accumulating list — restating one (e.g. "actually I'm a founder now")
# should replace the old fact, not add a second, contradictory-sounding one
# alongside it (this caused the bot to say "a student ... and a finance
# professional" after two separate role statements both got kept).
SINGLETON_FACT_CATEGORIES = {"role"}


async def add_memory_fact(db: AsyncSession, user_id: int, fact: str, category: str) -> None:
    if category in SINGLETON_FACT_CATEGORIES:
        await db.execute(
            delete(MemoryFact).where(MemoryFact.user_id == user_id, MemoryFact.category == category)
        )
    db.add(MemoryFact(user_id=user_id, fact=fact, category=category))
    await db.commit()


async def get_watchlist(db: AsyncSession, user_id: int) -> list[str]:
    result = await db.execute(select(WatchlistItem).where(WatchlistItem.user_id == user_id))
    return [item.ticker for item in result.scalars().all()]


async def add_watchlist_item(db: AsyncSession, user_id: int, ticker: str) -> None:
    ticker = ticker.strip().upper()
    existing = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user_id, WatchlistItem.ticker == ticker
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    db.add(WatchlistItem(user_id=user_id, ticker=ticker))
    await db.commit()


async def get_user_prefs(user: User) -> dict:
    return {
        "role": user.role,
        "briefing_time": user.briefing_time,
        "timezone": user.timezone,
        "google_offer_declines": user.google_offer_declines,
    }


async def note_google_offer_declined(db: AsyncSession, user_id: int) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.google_offer_declines += 1
    await db.commit()
    return {"acknowledged": True, "declines_so_far": user.google_offer_declines}


async def set_briefing_time_local(
    db: AsyncSession, user_id: int, local_time_24h: str
) -> dict:
    """Sets the daily briefing time given in the user's OWN local time
    (HH:MM, no conversion expected from the caller or from this function).
    Stored as-is — deliberately NOT converted to UTC here. A fixed stored
    UTC offset would drift by an hour across DST transitions in the user's
    zone; instead the scheduler re-resolves local-time -> UTC fresh against
    today's date on every check (see jobs/daily_brief.py), so it's always
    correct regardless of DST. This also means set_briefing_time and
    set_timezone are fully independent — neither has to wait on the other."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.briefing_time = local_time_24h
    await db.commit()
    return {"set": True, "local_time": local_time_24h}


async def set_user_timezone(db: AsyncSession, user_id: int, timezone: str) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()

    tz = resolve_timezone(timezone)
    if tz is None:
        return {"error": f"'{timezone}' isn't a recognizable timezone — ask the user for a city name or IANA zone instead"}

    user.timezone = str(tz.key) if hasattr(tz, "key") else timezone
    await db.commit()
    return {"set": True}


async def get_users_with_watchlist(db: AsyncSession) -> list[User]:
    result = await db.execute(
        select(User).join(WatchlistItem, WatchlistItem.user_id == User.id).distinct()
    )
    return list(result.scalars().all())


async def set_price_alert(db: AsyncSession, user_id: int, ticker: str, target_price: float) -> None:
    ticker = ticker.strip().upper()
    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user_id, WatchlistItem.ticker == ticker
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        item = WatchlistItem(user_id=user_id, ticker=ticker)
        db.add(item)
    item.alert_price = target_price
    await db.commit()


async def get_all_watchlist_items(db: AsyncSession) -> list[WatchlistItem]:
    result = await db.execute(select(WatchlistItem).options(selectinload(WatchlistItem.user)))
    return list(result.scalars().all())


async def mark_alert_sent(db: AsyncSession, item_id: int, clear_target: bool) -> None:
    result = await db.execute(select(WatchlistItem).where(WatchlistItem.id == item_id))
    item = result.scalar_one()
    item.last_alert_sent_at = func.now()
    if clear_target:
        item.alert_price = None
    await db.commit()


async def advance_onboarding(db: AsyncSession, user_id: int, stop: bool, total_questions: int) -> None:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    if stop or user.onboarding_step is None:
        user.onboarding_step = None
    else:
        next_step = user.onboarding_step + 1
        user.onboarding_step = next_step if next_step < total_questions else None
    await db.commit()


async def get_user_by_chat_id(db: AsyncSession, telegram_chat_id: int) -> User | None:
    result = await db.execute(select(User).where(User.telegram_chat_id == telegram_chat_id))
    return result.scalar_one_or_none()


async def save_google_credential(
    db: AsyncSession, user_id: int, access_token: str, refresh_token: str, expires_at
) -> None:
    result = await db.execute(select(GoogleCredential).where(GoogleCredential.user_id == user_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        cred = GoogleCredential(user_id=user_id)
        db.add(cred)
    cred.access_token = access_token
    # Google only issues a refresh_token on first consent (or when
    # prompt=consent forces it) — don't overwrite with an empty one if a
    # re-auth response omits it.
    if refresh_token:
        cred.refresh_token = refresh_token
    cred.expires_at = expires_at
    await db.commit()


async def get_google_credential(db: AsyncSession, user_id: int) -> GoogleCredential | None:
    result = await db.execute(select(GoogleCredential).where(GoogleCredential.user_id == user_id))
    return result.scalar_one_or_none()


async def update_google_access_token(db: AsyncSession, cred_id: int, access_token: str, expires_at) -> None:
    result = await db.execute(select(GoogleCredential).where(GoogleCredential.id == cred_id))
    cred = result.scalar_one()
    cred.access_token = access_token
    cred.expires_at = expires_at
    await db.commit()
