"""001_initial_schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-11-11 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. voting_sessions table
    op.create_table(
        "voting_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("voting_round_id", sa.String(length=64), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("eligible_voters", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column("eligible_targets", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tie_rule", sa.String(length=32), nullable=False),
        sa.Column(
            "allow_vote_change", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "reveal_ballots_after_close",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("close_reason", sa.String(length=32), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("eliminated_target_id", sa.String(length=64), nullable=True),
        sa.Column("tied_player_ids", sa.JSON().with_variant(JSONB, "postgresql"), nullable=True),
        sa.Column("tally_result", sa.JSON().with_variant(JSONB, "postgresql"), nullable=True),
        sa.Column("abstentions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("voting_round_id"),
    )
    op.create_index("idx_game_status", "voting_sessions", ["game_id", "status"])
    op.create_index("idx_deadline_status", "voting_sessions", ["deadline", "status"])
    op.create_index(op.f("ix_voting_sessions_game_id"), "voting_sessions", ["game_id"])
    op.create_index(
        op.f("ix_voting_sessions_voting_round_id"), "voting_sessions", ["voting_round_id"]
    )
    op.create_index(op.f("ix_voting_sessions_status"), "voting_sessions", ["status"])

    # 2. votes table
    op.create_table(
        "votes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("voting_round_id", sa.String(length=64), nullable=False),
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("voter_id", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("voting_round_id", "voter_id", name="uq_voting_round_voter"),
    )
    op.create_index("idx_round_target", "votes", ["voting_round_id", "target_id"])
    op.create_index(op.f("ix_votes_voting_round_id"), "votes", ["voting_round_id"])
    op.create_index(op.f("ix_votes_game_id"), "votes", ["game_id"])
    op.create_index(op.f("ix_votes_voter_id"), "votes", ["voter_id"])
    op.create_index(op.f("ix_votes_target_id"), "votes", ["target_id"])

    # 3. vote_revisions table
    op.create_table(
        "vote_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("voting_round_id", sa.String(length=64), nullable=False),
        sa.Column("voter_id", sa.String(length=64), nullable=False),
        sa.Column("previous_target_id", sa.String(length=64), nullable=False),
        sa.Column("new_target_id", sa.String(length=64), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rev_round_voter", "vote_revisions", ["voting_round_id", "voter_id"])
    op.create_index(
        op.f("ix_vote_revisions_voting_round_id"), "vote_revisions", ["voting_round_id"]
    )
    op.create_index(op.f("ix_vote_revisions_voter_id"), "vote_revisions", ["voter_id"])

    # 4. processed_events table
    op.create_table(
        "processed_events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5. outbox_messages table
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("idx_outbox_status_created", "outbox_messages", ["status", "created_at"])
    op.create_index(op.f("ix_outbox_messages_event_id"), "outbox_messages", ["event_id"])
    op.create_index(op.f("ix_outbox_messages_event_type"), "outbox_messages", ["event_type"])
    op.create_index(op.f("ix_outbox_messages_game_id"), "outbox_messages", ["game_id"])
    op.create_index(op.f("ix_outbox_messages_status"), "outbox_messages", ["status"])


def downgrade() -> None:
    op.drop_table("outbox_messages")
    op.drop_table("processed_events")
    op.drop_table("vote_revisions")
    op.drop_table("votes")
    op.drop_table("voting_sessions")
