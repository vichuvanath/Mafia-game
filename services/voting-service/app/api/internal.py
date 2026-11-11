"""Internal endpoints for Game Engine communication."""

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.database import get_db
from app.schemas.voting import (
    CloseVotingRequest,
    OpenVotingRequest,
    VotingSessionResponse,
)
from app.security.service_auth import verify_service_token
from app.services.voting_service import VotingService

router = APIRouter(
    prefix="/internal/games/{game_id}/voting",
    tags=["Internal Game Engine"],
    dependencies=[Depends(verify_service_token)],
)


@router.post(
    "/open",
    response_model=VotingSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def open_voting(
    request: OpenVotingRequest,
    game_id: str = Path(..., description="Target game UUID"),
    db: AsyncSession = Depends(get_db),
) -> VotingSessionResponse:
    """Open a new voting round.

    Requires valid X-Service-Token header.
    Idempotent: repeating the same voting_round_id returns the existing session.
    """
    service = VotingService(db)
    return await service.open_voting_round(game_id=game_id, request=request)


@router.post(
    "/close",
    response_model=VotingSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def close_voting(
    request: CloseVotingRequest,
    game_id: str = Path(..., description="Target game UUID"),
    db: AsyncSession = Depends(get_db),
) -> VotingSessionResponse:
    """Explicitly close an active voting round and compute proposed elimination.

    Requires valid X-Service-Token header.
    Idempotent: repeating close on an already-closed round returns the computed result.
    """
    service = VotingService(db)
    return await service.close_voting_round(
        game_id=game_id,
        voting_round_id=request.voting_round_id,
        reason="ENGINE_REQUEST",
    )
