"""Internal service endpoints protected by service token."""

from typing import Any

from fastapi import APIRouter, Body, Depends, Path
from pydantic import BaseModel

from app.api.games import get_game_service
from app.schemas.games import GameSessionResponse
from app.security.service_auth import verify_service_token
from app.services.game_service import GameService

router = APIRouter(
    prefix="/internal/games",
    tags=["Internal"],
    dependencies=[Depends(verify_service_token)],
)


class InternalAdvanceRequest(BaseModel):
    """Payload to trigger phase progression internally."""

    force: bool = False
    expected_version: int | None = None


class InternalGameDetailsResponse(BaseModel):
    """Internal diagnostic representation including role assignments."""

    game_id: str
    room_id: str
    phase: str
    version: int
    round_number: int
    role_assignments: dict[str, str]
    players: list[dict[str, Any]]
    winner: str | None


@router.post("/{game_id}/advance", response_model=GameSessionResponse)
async def advance_game_phase(
    game_id: str = Path(...),
    request: InternalAdvanceRequest = Body(default_factory=InternalAdvanceRequest),
    game_service: GameService = Depends(get_game_service),
) -> GameSessionResponse:
    """Manually or timer-triggered phase advance."""
    return await game_service.advance_phase(
        game_id=game_id, force=request.force, expected_version=request.expected_version
    )


@router.get("/{game_id}", response_model=InternalGameDetailsResponse)
async def get_internal_game_state(
    game_id: str = Path(...),
    game_service: GameService = Depends(get_game_service),
) -> InternalGameDetailsResponse:
    """Get internal state of a game session including role assignments."""
    game = await game_service.get_game_or_404(game_id)
    return InternalGameDetailsResponse(
        game_id=game.game_id,
        room_id=game.room_id,
        phase=game.phase,
        version=game.version,
        round_number=game.round_number,
        role_assignments=game.role_assignments,
        players=game.players,
        winner=game.winner,
    )
