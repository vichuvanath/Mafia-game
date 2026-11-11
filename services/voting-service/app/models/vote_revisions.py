"""SQLAlchemy model for historical vote revisions."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VoteRevision(Base):
    """Audit log tracking vote changes made by voters during an open round."""

    __tablename__ = "vote_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    voting_round_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    voter_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    previous_target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    new_target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (Index("idx_rev_round_voter", "voting_round_id", "voter_id"),)
