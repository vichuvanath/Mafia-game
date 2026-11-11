"""SQLAlchemy model for voting sessions."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base

# Compatible JSON column type fallback for SQLite/PostgreSQL
JSONType = JSON().with_variant(JSONB, "postgresql")


class VotingSession(Base):
    """Voting session representing a day-phase voting round for a game."""

    __tablename__ = "voting_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    game_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    voting_round_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OPEN", index=True
    )  # OPEN, CLOSED

    eligible_voters: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    eligible_targets: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tie_rule: Mapped[str] = mapped_column(String(32), nullable=False)
    allow_vote_change: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reveal_ballots_after_close: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    close_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    eliminated_target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tied_player_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    tally_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    abstentions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_game_status", "game_id", "status"),
        Index("idx_deadline_status", "deadline", "status"),
    )
