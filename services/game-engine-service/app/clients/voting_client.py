"""Voting Service client interface and HTTP implementation."""

from typing import Protocol

import httpx
from pydantic import BaseModel


class VotingResultDto(BaseModel):
    """Result returned by Voting Service."""

    game_id: str
    round_number: int
    eliminated_player_id: str | None = None
    votes: dict[str, str | None] = {}  # voter_id -> target_id (or None)
    is_tie: bool = False
    tied_player_ids: list[str] = []


class VotingServiceClient(Protocol):
    """Protocol for interacting with external Voting Service."""

    async def open_voting(
        self,
        game_id: str,
        round_number: int,
        eligible_voter_ids: list[str],
        candidate_ids: list[str],
        duration_seconds: int,
    ) -> None:
        """Instruct voting service to open a voting ballot."""
        ...

    async def close_voting(
        self,
        game_id: str,
        round_number: int,
    ) -> VotingResultDto:
        """Instruct voting service to close ballot and compute tally."""
        ...


class HttpVotingServiceClient:
    """HTTP client communicating with real Voting Service."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def open_voting(
        self,
        game_id: str,
        round_number: int,
        eligible_voter_ids: list[str],
        candidate_ids: list[str],
        duration_seconds: int,
    ) -> None:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            resp = await client.post(
                "/internal/voting/open",
                json={
                    "game_id": game_id,
                    "round_number": round_number,
                    "eligible_voter_ids": eligible_voter_ids,
                    "candidate_ids": candidate_ids,
                    "duration_seconds": duration_seconds,
                },
            )
            resp.raise_for_status()

    async def close_voting(
        self,
        game_id: str,
        round_number: int,
    ) -> VotingResultDto:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            resp = await client.post(
                "/internal/voting/close",
                json={"game_id": game_id, "round_number": round_number},
            )
            resp.raise_for_status()
            data = resp.json()
            return VotingResultDto(**data)
