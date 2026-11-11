"""Concurrency tests verifying racing ballots, simultaneous close triggers,
and rapid vote revisions.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.outbox import OutboxMessage
from app.models.votes import Vote
from app.models.voting_sessions import VotingSession
from app.schemas.voting import OpenVotingRequest
from app.services.voting_service import VotingService


@pytest.fixture
async def concurrency_db_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_voters_voting_at_once(concurrency_db_factory) -> None:  # type: ignore[no-untyped-def]
    """Simultaneous vote submissions from 8 voters produce consistent counts and one result."""
    voters = [f"voter-{i:02d}" for i in range(1, 9)]
    target = voters[0]  # Everyone votes for voter-01
    round_id = "round-concurrent-1"
    game_id = "game-concurrent-1"

    async with concurrency_db_factory() as db:
        service = VotingService(db)
        req = OpenVotingRequest(
            round_number=1,
            voting_round_id=round_id,
            eligible_voters=voters,
            eligible_targets=voters,
            deadline=datetime.now(UTC) + timedelta(minutes=5),
            tie_rule="NO_ELIMINATION",
        )
        await service.open_voting_round(game_id, req)

    # 7 other voters vote concurrently for voter-01
    async def cast_vote(voter: str) -> None:
        async with concurrency_db_factory() as db:
            s = VotingService(db)
            await s.submit_vote(game_id, voter_id=voter, target_id=target)

    # Run all 7 in parallel
    await asyncio.gather(*(cast_vote(v) for v in voters[1:]))

    async with concurrency_db_factory() as db:
        # Check active ballots in DB
        votes = (
            (await db.execute(select(Vote).where(Vote.voting_round_id == round_id))).scalars().all()
        )
        assert len(votes) == 7
        assert all(v.target_id == target for v in votes)


@pytest.mark.asyncio
async def test_concurrent_vote_revisions_by_same_voter(concurrency_db_factory) -> None:  # type: ignore[no-untyped-def]
    """A voter concurrently changing their ballot multiple times produces
    exactly ONE active vote.
    """
    round_id = "round-rev-race"
    game_id = "game-rev-race"
    voter = "voter-speedy"
    candidates = ["candidate-A", "candidate-B", "candidate-C", "candidate-D"]

    async with concurrency_db_factory() as db:
        service = VotingService(db)
        req = OpenVotingRequest(
            round_number=1,
            voting_round_id=round_id,
            eligible_voters=[voter] + candidates,
            eligible_targets=candidates,
            deadline=datetime.now(UTC) + timedelta(minutes=5),
            tie_rule="NO_ELIMINATION",
            allow_vote_change=True,
        )
        await service.open_voting_round(game_id, req)

        # Cast initial vote
        await service.submit_vote(game_id, voter_id=voter, target_id=candidates[0])

    # Rapid concurrent vote changes for different candidates
    async def change_vote(target: str) -> None:
        async with concurrency_db_factory() as db:
            s = VotingService(db)
            with contextlib.suppress(Exception):
                await s.submit_vote(game_id, voter_id=voter, target_id=target)

    await asyncio.gather(*(change_vote(c) for c in candidates * 2))

    async with concurrency_db_factory() as db:
        votes = (
            (
                await db.execute(
                    select(Vote).where(Vote.voting_round_id == round_id, Vote.voter_id == voter)
                )
            )
            .scalars()
            .all()
        )
        # Invariant: unique constraint ensures exactly ONE active ballot remains
        assert len(votes) == 1
        assert votes[0].target_id in candidates


@pytest.mark.asyncio
async def test_last_vote_racing_engine_close(concurrency_db_factory) -> None:  # type: ignore[no-untyped-def]
    """Racing final vote (which triggers ALL_VOTED) against explicit close
    produces strictly ONE result event.
    """
    round_id = "round-race-close"
    game_id = "game-race-close"
    voters = ["p1", "p2"]

    async with concurrency_db_factory() as db:
        service = VotingService(db)
        req = OpenVotingRequest(
            round_number=1,
            voting_round_id=round_id,
            eligible_voters=voters,
            eligible_targets=voters,
            deadline=datetime.now(UTC) + timedelta(minutes=5),
            tie_rule="NO_ELIMINATION",
        )
        await service.open_voting_round(game_id, req)
        # p1 votes for p2
        await service.submit_vote(game_id, voter_id="p1", target_id="p2")

    # Now p2 voting (last vote -> auto-close) races engine close
    async def voter2_submits() -> None:
        async with concurrency_db_factory() as db:
            s = VotingService(db)
            with contextlib.suppress(Exception):
                await s.submit_vote(game_id, voter_id="p2", target_id="p1")

    async def engine_closes() -> None:
        async with concurrency_db_factory() as db:
            s = VotingService(db)
            with contextlib.suppress(Exception):
                await s.close_voting_round(game_id, round_id, reason="ENGINE_REQUEST")

    await asyncio.gather(voter2_submits(), engine_closes())

    async with concurrency_db_factory() as db:
        sess = (
            await db.execute(select(VotingSession).where(VotingSession.voting_round_id == round_id))
        ).scalar_one()
        assert sess.status == "CLOSED"

        # Check outbox has exactly one VOTE_RESULT_READY
        outbox = (
            (await db.execute(select(OutboxMessage).where(OutboxMessage.game_id == game_id)))
            .scalars()
            .all()
        )
        result_events = [e for e in outbox if e.event_type == "VOTE_RESULT_READY"]
        assert len(result_events) == 1
