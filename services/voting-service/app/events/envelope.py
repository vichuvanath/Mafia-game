"""Event envelope schema matching the platform specification."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Standard event envelope structure:
    { "event_id", "event_type", "occurred_at", "game_id", "version", "payload" }
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    game_id: str
    version: int = 1
    payload: dict[str, Any]
