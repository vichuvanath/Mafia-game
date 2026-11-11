"""Data access repository for voting sessions, votes, outbox, and audit revisions."""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tally import TallyResult
from app.models.outbox import OutboxMessage
from app.models.processed_events import ProcessedEvent
from app.models.vote_revisions import VoteRevision
from app.models.votes import Vote
from app.models.voting_sessions import VotingSession


class VotingRepository:
    """Async repository providing transactional access to voting domain entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_session_by_round_id(
        self, voting_round_id: str, for_update: bool = False
    ) -> VotingSession | None:
        """Fetch a voting session by voting_round_id, optionally locking the row."""
        stmt = select(VotingSession).where(VotingSession.voting_round_id == voting_round_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_session_by_game_id(
        self, game_id: str, for_update: bool = False
    ) -> VotingSession | None:
        """Fetch an active OPEN voting session for the given game."""
        stmt = select(VotingSession).where(
            VotingSession.game_id == game_id,
            VotingSession.status == "OPEN",
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_session(
        self,
        game_id: str,
        voting_round_id: str,
        round_number: int,
        eligible_voters: list[str],
        eligible_targets: list[str],
        deadline: datetime,
        tie_rule: str,
        allow_vote_change: bool = False,
        reveal_ballots_after_close: bool = False,
    ) -> VotingSession:
        """Create and persist a new OPEN voting session."""
        voting_session = VotingSession(
            id=uuid.uuid4(),
            game_id=game_id,
            voting_round_id=voting_round_id,
            round_number=round_number,
            status="OPEN",
            eligible_voters=eligible_voters,
            eligible_targets=eligible_targets,
            deadline=deadline,
            tie_rule=tie_rule,
            allow_vote_change=allow_vote_change,
            reveal_ballots_after_close=reveal_ballots_after_close,
            version=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.session.add(voting_session)
        await self.session.flush()
        return voting_session

    async def get_active_votes_for_round(self, voting_round_id: str) -> list[Vote]:
        """Fetch all active ballots submitted for a voting round."""
        stmt = select(Vote).where(Vote.voting_round_id == voting_round_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_ballots_map(self, voting_round_id: str) -> dict[str, str]:
        """Fetch a mapping of voter_id -> target_id for all cast ballots."""
        votes = await self.get_active_votes_for_round(voting_round_id)
        return {v.voter_id: v.target_id for v in votes}

    async def get_vote(self, voting_round_id: str, voter_id: str) -> Vote | None:
        """Fetch an individual voter's active ballot in a round."""
        stmt = select(Vote).where(
            Vote.voting_round_id == voting_round_id,
            Vote.voter_id == voter_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def cast_or_update_vote(
        self,
        voting_round_id: str,
        game_id: str,
        voter_id: str,
        target_id: str,
        allow_vote_change: bool = False,
    ) -> tuple[Vote, bool]:
        """Cast a new vote or revise an existing vote atomically.

        Returns:
            Tuple of (Vote, is_revision: bool)
        """
        existing_vote = await self.get_vote(voting_round_id, voter_id)
        now = datetime.now(UTC)

        if existing_vote is None:
            # First-time vote
            new_vote = Vote(
                id=uuid.uuid4(),
                voting_round_id=voting_round_id,
                game_id=game_id,
                voter_id=voter_id,
                target_id=target_id,
                created_at=now,
                updated_at=now,
            )
            self.session.add(new_vote)
            await self.session.flush()
            return new_vote, False

        # Vote update: record revision audit record
        prev_target = existing_vote.target_id
        if prev_target != target_id:
            existing_vote.target_id = target_id
            existing_vote.updated_at = now

            revision = VoteRevision(
                id=uuid.uuid4(),
                voting_round_id=voting_round_id,
                voter_id=voter_id,
                previous_target_id=prev_target,
                new_target_id=target_id,
                changed_at=now,
            )
            self.session.add(revision)
            await self.session.flush()

        return existing_vote, True

    async def close_session(
        self,
        voting_session: VotingSession,
        reason: str,
        tally: TallyResult,
    ) -> bool:
        """Atomically transition voting session from OPEN to CLOSED using conditional update.

        Returns:
            True if this call successfully transitioned the state from OPEN to CLOSED;
            False if another concurrent transaction already closed it.
        """
        now = datetime.now(UTC)
        stmt = (
            update(VotingSession)
            .where(
                VotingSession.id == voting_session.id,
                VotingSession.status == "OPEN",
            )
            .values(
                status="CLOSED",
                close_reason=reason,
                outcome=str(tally.outcome),
                eliminated_target_id=tally.target_id,
                tied_player_ids=tally.tied_player_ids,
                tally_result=tally.tally,
                abstentions=tally.abstentions,
                closed_at=now,
                updated_at=now,
                version=voting_session.version + 1,
            )
        )
        res = cast(CursorResult[Any], await self.session.execute(stmt))
        if res.rowcount == 0:
            return False

        await self.session.refresh(voting_session)
        return True

    async def add_outbox_message(
        self,
        event_id: str,
        event_type: str,
        game_id: str,
        version: int,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> OutboxMessage:
        """Persist an event to the transactional outbox table within the current DB transaction."""
        message = OutboxMessage(
            id=uuid.uuid4(),
            event_id=event_id,
            event_type=event_type,
            game_id=game_id,
            version=version,
            occurred_at=occurred_at,
            payload=payload,
            status="PENDING",
            retry_count=0,
            created_at=datetime.now(UTC),
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def get_pending_outbox_messages(self, limit: int = 50) -> list[OutboxMessage]:
        """Fetch pending outbox messages ordered chronologically."""
        stmt = (
            select(OutboxMessage)
            .where(OutboxMessage.status == "PENDING")
            .order_by(OutboxMessage.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_outbox_published(self, event_id: str) -> None:
        """Mark an outbox message as successfully PUBLISHED."""
        stmt = (
            update(OutboxMessage)
            .where(OutboxMessage.event_id == event_id)
            .values(status="PUBLISHED", published_at=datetime.now(UTC))
        )
        await self.session.execute(stmt)

    async def is_event_processed(self, event_id: str) -> bool:
        """Check if an event or command has already been processed."""
        stmt = select(ProcessedEvent).where(ProcessedEvent.id == event_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def record_processed_event(self, event_id: str, event_type: str) -> ProcessedEvent:
        """Record an idempotency key as processed."""
        processed = ProcessedEvent(
            id=event_id,
            event_type=event_type,
            processed_at=datetime.now(UTC),
        )
        self.session.add(processed)
        await self.session.flush()
        return processed
