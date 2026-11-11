"""Application service orchestrating voting workflows, state changes, and events."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ballot_rules import VotingRuleError, is_deadline_expired, validate_ballot
from app.core.tally import compute_tally
from app.events.envelope import EventEnvelope
from app.models.voting_sessions import VotingSession
from app.repositories.redis_client import check_rate_limit, distributed_lock
from app.repositories.voting_repository import VotingRepository
from app.schemas.errors import VotingAppException
from app.schemas.voting import (
    OpenVotingRequest,
    SubmitVoteResponse,
    VotingSessionResponse,
    VotingStatusResponse,
)

logger = logging.getLogger("voting_service.domain")


class VotingService:
    """Coordinates business operations for voting rounds and ballot resolution."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = VotingRepository(db)

    async def open_voting_round(
        self, game_id: str, request: OpenVotingRequest
    ) -> VotingSessionResponse:
        """Open a new day-phase voting round for a game.

        Idempotent: Re-submitting the same voting_round_id returns the existing session.
        If another round is currently open for the game, raises 409 Conflict.
        """
        async with distributed_lock(f"game:{game_id}"):
            # 1. Check if this exact round ID already exists (idempotent replay)
            existing = await self.repo.get_session_by_round_id(request.voting_round_id)
            if existing is not None:
                logger.info(
                    "Idempotent open: round %s already exists.",
                    request.voting_round_id,
                )
                return VotingSessionResponse.model_validate(existing)

            # 2. Check if a different round is currently OPEN for this game
            active = await self.repo.get_open_session_by_game_id(game_id)
            if active is not None:
                raise VotingAppException(
                    code="ROUND_CONFLICT",
                    message="Another voting round is already open for this game.",
                    status_code=409,
                )

            # 3. Create and persist new OPEN round
            new_session = await self.repo.create_session(
                game_id=game_id,
                voting_round_id=request.voting_round_id,
                round_number=request.round_number,
                eligible_voters=request.eligible_voters,
                eligible_targets=request.eligible_targets,
                deadline=request.deadline,
                tie_rule=str(request.tie_rule),
                allow_vote_change=request.allow_vote_change,
                reveal_ballots_after_close=request.reveal_ballots_after_close,
            )
            await self.db.commit()

            # Schedule deadline in Redis-backed scheduler
            try:
                from app.services.scheduler import get_deadline_scheduler

                await get_deadline_scheduler().schedule_deadline(
                    request.voting_round_id, request.deadline
                )
            except Exception as e:
                logger.warning("Could not register deadline in Redis scheduler: %s", str(e))

            return VotingSessionResponse.model_validate(new_session)

    async def submit_vote(self, game_id: str, voter_id: str, target_id: str) -> SubmitVoteResponse:
        """Submit or revise a player's ballot.

        Enforces rate limiting, eligibility, deadlines, and auto-closing when all voted.
        """
        # 1. Rate limit per voter
        allowed = await check_rate_limit(f"voter:{voter_id}")
        if not allowed:
            raise VotingAppException(
                code="RATE_LIMIT_EXCEEDED",
                message="Too many vote attempts. Please slow down.",
                status_code=429,
            )

        # 2. Locate active round
        active_session = await self.repo.get_open_session_by_game_id(game_id)
        if active_session is None:
            raise VotingAppException(
                code="ROUND_NOT_FOUND",
                message="No active voting round found for this game.",
                status_code=404,
            )

        round_id = active_session.voting_round_id

        # 3. Synchronize with distributed lock on the round to prevent race conditions
        async with distributed_lock(f"session:{round_id}"):
            session = await self.repo.get_session_by_round_id(round_id, for_update=True)
            if session is None or session.status != "OPEN":
                raise VotingAppException(
                    code="VOTING_CLOSED",
                    message="Voting session is closed.",
                    status_code=400,
                )

            # Check if deadline passed
            now = datetime.now(UTC)
            if is_deadline_expired(session.deadline, now=now):
                await self._close_round_internal(session, reason="DEADLINE")
                await self.db.commit()
                raise VotingAppException(
                    code="VOTING_CLOSED",
                    message="Voting deadline has expired.",
                    status_code=400,
                )

            # 4. Fetch existing vote
            existing_vote = await self.repo.get_vote(round_id, voter_id)
            existing_target = existing_vote.target_id if existing_vote else None

            # 5. Pure ballot validation
            try:
                validate_ballot(
                    voter_id=voter_id,
                    target_id=target_id,
                    eligible_voters=session.eligible_voters,
                    eligible_targets=session.eligible_targets,
                    is_open=(session.status == "OPEN"),
                    deadline=session.deadline,
                    existing_target_id=existing_target,
                    allow_vote_change=session.allow_vote_change,
                    now=now,
                )
            except VotingRuleError as err:
                raise VotingAppException(
                    code=err.code,
                    message=err.message,
                    status_code=400,
                ) from err

            # 6. Persist ballot and audit log
            vote, is_revision = await self.repo.cast_or_update_vote(
                voting_round_id=round_id,
                game_id=game_id,
                voter_id=voter_id,
                target_id=target_id,
                allow_vote_change=session.allow_vote_change,
            )

            # 7. Record VOTE_SUBMITTED outbox event (NO identity revealed)
            all_votes = await self.repo.get_active_votes_for_round(round_id)
            votes_cast = len(all_votes)
            votes_expected = len(session.eligible_voters)

            vote_submitted_event = EventEnvelope(
                event_id=str(uuid.uuid4()),
                event_type="VOTE_SUBMITTED",
                game_id=game_id,
                version=session.version,
                payload={
                    "voting_round_id": round_id,
                    "votes_cast": votes_cast,
                    "votes_expected": votes_expected,
                },
            )
            await self.repo.add_outbox_message(
                event_id=vote_submitted_event.event_id,
                event_type=vote_submitted_event.event_type,
                game_id=vote_submitted_event.game_id,
                version=vote_submitted_event.version,
                occurred_at=vote_submitted_event.occurred_at,
                payload=vote_submitted_event.payload,
            )

            # 8. Check auto-close condition (all eligible players have voted)
            if votes_cast >= votes_expected:
                logger.info(
                    "All %d eligible voters have cast ballots for round %s. Auto-closing.",
                    votes_expected,
                    round_id,
                )
                await self._close_round_internal(session, reason="ALL_VOTED")

            await self.db.commit()

            return SubmitVoteResponse(
                voting_round_id=round_id,
                target_id=target_id,
                is_revision=is_revision,
                status="ACCEPTED",
            )

    async def close_voting_round(
        self, game_id: str, voting_round_id: str, reason: str = "ENGINE_REQUEST"
    ) -> VotingSessionResponse:
        """Close an active round, compute results, and queue outbox notifications.

        Idempotent: closing an already-closed round returns the stored result
        without duplicate events.
        """
        async with distributed_lock(f"session:{voting_round_id}"):
            session = await self.repo.get_session_by_round_id(voting_round_id, for_update=True)
            if session is None:
                raise VotingAppException(
                    code="ROUND_NOT_FOUND",
                    message="Voting round not found.",
                    status_code=404,
                )

            if session.status == "CLOSED":
                logger.info("Idempotent close: round %s is already closed.", voting_round_id)
                return VotingSessionResponse.model_validate(session)

            closed_session = await self._close_round_internal(session, reason=reason)
            await self.db.commit()
            return VotingSessionResponse.model_validate(closed_session)

    async def _close_round_internal(self, session: VotingSession, reason: str) -> VotingSession:
        """Internal helper executing tally computation and queuing outbox events."""
        ballots_map = await self.repo.get_active_ballots_map(session.voting_round_id)
        tally = compute_tally(
            active_ballots=ballots_map,
            eligible_voters=session.eligible_voters,
            tie_rule=session.tie_rule,
        )

        transition_won = await self.repo.close_session(
            voting_session=session,
            reason=reason,
            tally=tally,
        )

        if not transition_won:
            refreshed = await self.repo.get_session_by_round_id(session.voting_round_id)
            return refreshed or session

        # 1. Outbox: VOTING_CLOSED
        now = datetime.now(UTC)
        close_event = EventEnvelope(
            event_id=str(uuid.uuid4()),
            event_type="VOTING_CLOSED",
            game_id=session.game_id,
            version=session.version,
            occurred_at=now,
            payload={
                "voting_round_id": session.voting_round_id,
                "round_number": session.round_number,
                "reason": reason,
            },
        )
        await self.repo.add_outbox_message(
            event_id=close_event.event_id,
            event_type=close_event.event_type,
            game_id=close_event.game_id,
            version=close_event.version,
            occurred_at=close_event.occurred_at,
            payload=close_event.payload,
        )

        # 2. Outbox: VOTE_RESULT_READY
        result_event = EventEnvelope(
            event_id=str(uuid.uuid4()),
            event_type="VOTE_RESULT_READY",
            game_id=session.game_id,
            version=session.version,
            occurred_at=now,
            payload={
                "voting_round_id": session.voting_round_id,
                "round_number": session.round_number,
                "outcome": str(tally.outcome),
                "target_id": tally.target_id,
                "tied_player_ids": tally.tied_player_ids,
                "tally": tally.tally,
                "abstentions": tally.abstentions,
            },
        )
        await self.repo.add_outbox_message(
            event_id=result_event.event_id,
            event_type=result_event.event_type,
            game_id=result_event.game_id,
            version=result_event.version,
            occurred_at=result_event.occurred_at,
            payload=result_event.payload,
        )

        # Cancel deadline in scheduler if present
        try:
            from app.services.scheduler import get_deadline_scheduler

            await get_deadline_scheduler().cancel_deadline(session.voting_round_id)
        except Exception as e:
            logger.warning("Could not cancel deadline in Redis scheduler: %s", str(e))

        return session

    async def get_voting_status(self, game_id: str, current_user_id: str) -> VotingStatusResponse:
        """Return public voting progress and current user's vote without leaking others' ballots."""
        # Find active session, or most recent closed session
        session = await self.repo.get_open_session_by_game_id(game_id)
        if session is None:
            # Check latest session
            from sqlalchemy import desc, select

            stmt = (
                select(VotingSession)
                .where(VotingSession.game_id == game_id)
                .order_by(desc(VotingSession.created_at))
                .limit(1)
            )
            result = await self.db.execute(stmt)
            session = result.scalar_one_or_none()

        if session is None:
            raise VotingAppException(
                code="ROUND_NOT_FOUND",
                message="No voting round found for this game.",
                status_code=404,
            )

        active_votes = await self.repo.get_active_votes_for_round(session.voting_round_id)
        votes_cast = len(active_votes)
        my_vote_record = await self.repo.get_vote(session.voting_round_id, current_user_id)
        my_vote = my_vote_record.target_id if my_vote_record else None

        revealed_ballots: dict[str, str] | None = None
        if session.status == "CLOSED" and session.reveal_ballots_after_close:
            revealed_ballots = await self.repo.get_active_ballots_map(session.voting_round_id)

        return VotingStatusResponse(
            round_number=session.round_number,
            status=session.status,
            votes_cast=votes_cast,
            votes_expected=len(session.eligible_voters),
            deadline=session.deadline,
            my_vote=my_vote,
            revealed_ballots=revealed_ballots,
        )
