"""Standard event envelope according to event-driven specification."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.base import utc_now


class EventEnvelope(BaseModel):
    """Universal event envelope for all produced and consumed events."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    occurred_at: datetime = Field(default_factory=utc_now)
    game_id: str
    version: int
    payload: dict[str, Any]
