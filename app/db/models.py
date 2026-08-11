"""
db/models.py

SQLAlchemy async ORM models. Kept intentionally small — just what the locked
scope needs (users, messages, memory_facts, watchlist, prefs).
"""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # HH:MM in the user's OWN local time (per `timezone` below) — deliberately
    # NOT converted/stored as UTC, so DST shifts never desync it; the
    # scheduler resolves local->UTC fresh on every check.
    briefing_time: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # IANA name (e.g. "Asia/Kolkata") or a plain label like "IST" / "UTC+5:30" —
    # whatever the user gave; stored as-is so briefing/alert times can be
    # reasoned about without re-deriving it from free-text memory facts.
    timezone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # 0..N while onboarding is in progress (index into ONBOARDING_QUESTIONS),
    # NULL once finished or skipped — never resumes after that.
    onboarding_step: Mapped[int | None] = mapped_column(nullable=True, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="user")
    memory_facts: Mapped[list["MemoryFact"]] = relationship(back_populates="user")
    watchlist_items: Mapped[list["WatchlistItem"]] = relationship(back_populates="user")
    google_credential: Mapped["GoogleCredential | None"] = relationship(
        back_populates="user", uselist=False
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="messages")


class MemoryFact(Base):
    __tablename__ = "memory_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    fact: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="memory_facts")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(20))
    alert_price: Mapped[float | None] = mapped_column(nullable=True)
    last_alert_sent_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="watchlist_items")


class GoogleCredential(Base):
    """One row per user once they connect their Google account (Gmail +
    Calendar share a single OAuth grant — one consent screen, both scopes)."""

    __tablename__ = "google_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="google_credential")
