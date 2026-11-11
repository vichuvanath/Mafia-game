"""Public REST API endpoints for voting and ballot progress."""

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.database import get_db
from app.schemas.voting import (
    SubmitVoteRequest,
    SubmitVoteResponse,
    VotingStatusResponse,
)
from app.security.jwt import get_current_user_id
from app.services.voting_service import VotingService

router = APIRouter(
    prefix="/games/{game_id}",
    tags=["Player Voting"],
)


@router.post(
    "/votes",
    response_model=SubmitVoteResponse,
    status_code=status.HTTP_200_OK,
)
async def submit_vote(
    request: SubmitVoteRequest,
    game_id: str = Path(..., description="Target game UUID"),
    current_user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SubmitVoteResponse:
    """Submit or update the caller's vote for the active round.

    Requires Bearer JWT. Target must be eligible and self-voting is prohibited.
    """
    service = VotingService(db)
    return await service.submit_vote(
        game_id=game_id,
        voter_id=current_user_id,
        target_id=request.target_id,
    )


@router.get(
    "/voting/status",
    response_model=VotingStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def get_voting_status(
    game_id: str = Path(..., description="Target game UUID"),
    current_user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> VotingStatusResponse:
    """Fetch public voting progress for the caller's game.

    Reveals participation totals and only the caller's own vote.
    """
    service = VotingService(db)
    return await service.get_voting_status(game_id=game_id, current_user_id=current_user_id)
