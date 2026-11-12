"""initial schema

Revision ID: 20261112_0001
Revises:
Create Date: 2026-11-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # game_sessions table
    op.create_table(
        "game_sessions",
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("room_id", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("round_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("phase_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("players", sa.JSON(), nullable=False),
        sa.Column("role_assignments", sa.JSON(), nullable=False),
        sa.Column("winner", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("game_id"),
    )
    op.create_index(op.f("ix_game_sessions_game_id"), "game_sessions", ["game_id"], unique=False)
    op.create_index(op.f("ix_game_sessions_room_id"), "game_sessions", ["room_id"], unique=False)
    op.create_index(op.f("ix_game_sessions_phase"), "game_sessions", ["phase"], unique=False)

    # game_phase_history table
    op.create_table(
        "game_phase_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["game_sessions.game_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_game_phase_history_game_id"), "game_phase_history", ["game_id"], unique=False
    )

    # processed_events table
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("game_id", sa.String(length=64), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        op.f("ix_processed_events_game_id"), "processed_events", ["game_id"], unique=False
    )

    # outbox_events table
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(op.f("ix_outbox_events_event_id"), "outbox_events", ["event_id"], unique=True)
    op.create_index(
        op.f("ix_outbox_events_event_type"), "outbox_events", ["event_type"], unique=False
    )
    op.create_index(op.f("ix_outbox_events_game_id"), "outbox_events", ["game_id"], unique=False)
    op.create_index(op.f("ix_outbox_events_status"), "outbox_events", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_outbox_events_status"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_game_id"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_event_type"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_event_id"), table_name="outbox_events")
    op.drop_table("outbox_events")

    op.drop_index(op.f("ix_processed_events_game_id"), table_name="processed_events")
    op.drop_table("processed_events")

    op.drop_index(op.f("ix_game_phase_history_game_id"), table_name="game_phase_history")
    op.drop_table("game_phase_history")

    op.drop_index(op.f("ix_game_sessions_phase"), table_name="game_sessions")
    op.drop_index(op.f("ix_game_sessions_room_id"), table_name="game_sessions")
    op.drop_index(op.f("ix_game_sessions_game_id"), table_name="game_sessions")
    op.drop_table("game_sessions")
