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
    # Times the user has declined/skipped a proactive Gmail/Calendar/Drive
    # connect offer (onboarding wrap-up counts as one). Capped at 2 in
    # practice — past that the persona stops offering unprompted so it never
    # nags; the user can still ask to connect anytime.
    google_offer_declines: Mapped[int] = mapped_column(default=0, server_default="0")
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
    # Overrides price_alerts.py's default 5% daily-move threshold for this
    # one ticker when the user explicitly asks for a custom percentage.
    alert_move_percent: Mapped[float | None] = mapped_column(nullable=True)
    # Last time watchlist_news.py checked this ticker for fresh headlines —
    # NULL means "never checked yet", used to establish a baseline on the
    # first pass instead of blasting every existing headline as if it were
    # breaking news the moment a ticker is added.
    last_news_check_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="watchlist_items")


class Reminder(Base):
    """One-off (non-recurring) reminders the user asks for, e.g. 'remind me
    an hour before Apple's earnings call'. Separate from briefing_time
    (recurring, HH:MM only) and price alerts (deterministic, no message
    text) — this is an arbitrary future local moment plus free text."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    message: Mapped[str] = mapped_column(Text)
    # "YYYY-MM-DD HH:MM" in the user's OWN local time, same storage
    # philosophy as briefing_time — resolved to UTC fresh at check-time via
    # the user's stored timezone so DST never causes drift.
    remind_at_local: Mapped[str] = mapped_column(String(20))
    sent: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()


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
