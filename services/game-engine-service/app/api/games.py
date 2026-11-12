"""Player-facing game session endpoints."""

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.dependencies import (
    get_redis_state_manager,
    get_role_client,
    get_voting_client,
)
from app.clients.role_client import RoleServiceClient
from app.clients.voting_client import VotingServiceClient
from app.config import Settings, get_settings
from app.database import get_db_session
from app.schemas.games import (
    GameSessionCreateRequest,
    GameSessionResponse,
    NightActionRequest,
    NightActionResponse,
)
from app.security.jwt import AuthenticatedUser, get_current_player
from app.services.action_service import ActionService
from app.services.game_service import GameService
from app.state.redis_state import RedisStateManager

router = APIRouter(prefix="/games", tags=["Games"])


def get_game_service(
    session: AsyncSession = Depends(get_db_session),
    redis_state: RedisStateManager = Depends(get_redis_state_manager),
    role_client: RoleServiceClient = Depends(get_role_client),
    voting_client: VotingServiceClient = Depends(get_voting_client),
    settings: Settings = Depends(get_settings),
) -> GameService:
    """Dependency provider for GameService."""
    return GameService(
        session=session,
        redis_state=redis_state,
        role_client=role_client,
        voting_client=voting_client,
        settings=settings,
    )


def get_action_service(
    redis_state: RedisStateManager = Depends(get_redis_state_manager),
    settings: Settings = Depends(get_settings),
) -> ActionService:
    """Dependency provider for ActionService."""
    return ActionService(redis_state=redis_state, settings=settings)


@router.post("", response_model=GameSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_game(
    payload: GameSessionCreateRequest,
    user: AuthenticatedUser = Depends(get_current_player),
    game_service: GameService = Depends(get_game_service),
) -> GameSessionResponse:
    """Create a new game session from an authorized player or room host."""
    # Ensure creating user is included in player list
    if user.player_id not in payload.player_ids:
        payload.player_ids.append(user.player_id)

    return await game_service.create_game(
        room_id=payload.room_id,
        player_ids=payload.player_ids,
        player_names=payload.player_names,
    )


@router.get("/{game_id}", response_model=GameSessionResponse)
async def get_game(
    game_id: str = Path(..., description="ID of the game session"),
    user: AuthenticatedUser = Depends(get_current_player),
    game_service: GameService = Depends(get_game_service),
) -> GameSessionResponse:
    """Get public state of a game session.

    Never reveals secret roles of living players or secret role mapping.
    """
    game = await game_service.get_game_or_404(game_id)
    participant_ids = {p["player_id"] for p in game.players}

    # Security check: Caller must be a participant in this game
    if user.player_id not in participant_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: You are not a participant in this game",
        )

    return await game_service.get_public_game(game_id)


@router.post("/{game_id}/actions", response_model=NightActionResponse)
async def submit_night_action(
    game_id: str = Path(..., description="ID of the game session"),
    action: NightActionRequest = Body(...),
    user: AuthenticatedUser = Depends(get_current_player),
    game_service: GameService = Depends(get_game_service),
    action_service: ActionService = Depends(get_action_service),
) -> NightActionResponse:
    """Submit role-specific night action (Mafia kill or Doctor protect)."""
    game = await game_service.get_game_or_404(game_id)

    # Validate action and buffer in Redis
    role = await action_service.validate_and_record_action(
        game=game,
        actor_id=user.player_id,
        target_id=action.target_id,
    )

    return NightActionResponse(
        status="ACCEPTED",
        phase=game.phase,
        message=f"Action successfully recorded for {role.value}",
    )
