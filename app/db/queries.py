"""
db/queries.py

Async engine/session setup + the small set of query helpers the bot needs.
Kept as plain functions over an AsyncSession rather than a repository class —
there aren't enough queries yet to justify the abstraction.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.config import settings
from app.db.models import Base, MemoryFact, Message, User, WatchlistItem

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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


async def add_memory_fact(db: AsyncSession, user_id: int, fact: str, category: str) -> None:
    db.add(MemoryFact(user_id=user_id, fact=fact, category=category))
    await db.commit()


async def get_watchlist(db: AsyncSession, user_id: int) -> list[str]:
    result = await db.execute(select(WatchlistItem).where(WatchlistItem.user_id == user_id))
    return [item.ticker for item in result.scalars().all()]


async def get_user_prefs(user: User) -> dict:
    return {"role": user.role, "briefing_time": user.briefing_time}
