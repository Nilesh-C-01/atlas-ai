"""add reminders table + watchlist_items custom-alert/news-check columns

Revision ID: 0003_reminders_and_watchlist_extras
Revises: 0002_google_offer_declines
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_reminders_watchlist"
down_revision = "0002_google_offer_declines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("watchlist_items", sa.Column("alert_move_percent", sa.Float(), nullable=True))
    op.add_column(
        "watchlist_items",
        sa.Column("last_news_check_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("remind_at_local", sa.String(length=20), nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_reminders_user_id", table_name="reminders")
    op.drop_table("reminders")
    op.drop_column("watchlist_items", "last_news_check_at")
    op.drop_column("watchlist_items", "alert_move_percent")
