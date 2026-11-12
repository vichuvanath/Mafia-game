"""Game session ORM model."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class GameSessionModel(Base):
    """Durable state of a game session."""

    __tablename__ = "game_sessions"

    game_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    room_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    phase_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Players list: [{'player_id': '...', 'username': '...', 'is_alive': True, 'is_connected': True}]
    players: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    # Internal-only role assignments: {'player_id': 'MAFIA', ...}
    role_assignments: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    # Game outcome
    winner: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
