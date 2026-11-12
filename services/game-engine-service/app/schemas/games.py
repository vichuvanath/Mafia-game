"""Game API request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.config.rules import Phase


class PlayerPublicState(BaseModel):
    """Publicly visible player information (never includes role if alive)."""

    player_id: str
    username: str
    is_alive: bool = True
    is_connected: bool = True
    revealed_role: str | None = None


class GameSessionCreateRequest(BaseModel):
    """Payload to initiate a new game session."""

    room_id: str
    player_ids: list[str] = Field(min_length=3)
    player_names: dict[str, str] = Field(default_factory=dict)


class GameSessionResponse(BaseModel):
    """Public game session state."""

    game_id: str
    room_id: str
    phase: Phase
    version: int
    round_number: int
    phase_deadline: datetime | None
    players: list[PlayerPublicState]
    winner: str | None
    created_at: datetime
    updated_at: datetime


class NightActionRequest(BaseModel):
    """Night action payload submitted by an authorized player."""

    target_id: str


class NightActionResponse(BaseModel):
    """Confirmation of night action receipt."""

    status: str = "ACCEPTED"
    phase: str
    message: str = "Action recorded"
