"""Pydantic request and response schemas for voting endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.tie_rules import TieRule


class OpenVotingRequest(BaseModel):
    """Payload sent by Game Engine to open a new voting round."""

    round_number: int = Field(..., ge=1, description="Round number within current game")
    voting_round_id: str = Field(..., description="Unique round identifier (UUID)")
    eligible_voters: list[str] = Field(
        ..., min_length=1, description="List of living player UUIDs eligible to vote"
    )
    eligible_targets: list[str] = Field(
        ..., min_length=1, description="List of living player UUIDs eligible to be voted for"
    )
    deadline: datetime = Field(..., description="ISO-8601 timestamp when voting expires")
    tie_rule: TieRule = Field(default=TieRule.NO_ELIMINATION, description="Tie resolution strategy")
    allow_vote_change: bool = Field(
        default=False, description="Whether players may change cast votes"
    )
    reveal_ballots_after_close: bool = Field(
        default=False, description="Whether individual votes are revealed post-close"
    )


class CloseVotingRequest(BaseModel):
    """Payload sent by Game Engine to explicitly close an active voting round."""

    voting_round_id: str = Field(..., description="Unique round identifier to close")


class SubmitVoteRequest(BaseModel):
    """Payload submitted by an authenticated player."""

    target_id: str = Field(..., description="Target player UUID receiving the vote")


class SubmitVoteResponse(BaseModel):
    """Confirmation returned to the voter."""

    voting_round_id: str
    target_id: str
    is_revision: bool
    status: str = "ACCEPTED"


class VotingStatusResponse(BaseModel):
    """Public progress response shape without leaking secret ballots."""

    round_number: int
    status: str
    votes_cast: int
    votes_expected: int
    deadline: datetime
    my_vote: str | None = None
    revealed_ballots: dict[str, str] | None = None


class VotingSessionResponse(BaseModel):
    """Detailed voting session state returned to Game Engine."""

    model_config = ConfigDict(from_attributes=True)

    voting_round_id: str
    game_id: str
    round_number: int
    status: str
    eligible_voters: list[str]
    eligible_targets: list[str]
    deadline: datetime
    tie_rule: str
    allow_vote_change: bool
    reveal_ballots_after_close: bool
    close_reason: str | None = None
    outcome: str | None = None
    eliminated_target_id: str | None = None
    tied_player_ids: list[str] | None = None
    tally_result: dict[str, int] | None = None
    abstentions: int = 0
