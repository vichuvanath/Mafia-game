"""Idempotency tests: repeated open commands, concurrent close triggers, and outbox uniqueness."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.events.outbox_worker import OutboxWorker
from app.events.publisher import EventPublisher
from app.models.base import Base
from app.models.outbox import OutboxMessage
from app.models.voting_sessions import VotingSession
from app.schemas.voting import OpenVotingRequest
from app.services.voting_service import VotingService


@pytest.fixture
async def memory_db_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_open_voting_round_repeated_calls(memory_db_factory) -> None:  # type: ignore[no-untyped-def]
    """Opening the exact same voting_round_id 5 times creates exactly
    one session and preserves state.
    """
    round_id = "round-idem-open-100"
    game_id = "game-idem-100"
    req = OpenVotingRequest(
        round_number=1,
        voting_round_id=round_id,
        eligible_voters=["p1", "p2", "p3"],
        eligible_targets=["p1", "p2", "p3"],
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        tie_rule="NO_ELIMINATION",
    )

    results = []
    for _ in range(5):
        async with memory_db_factory() as db:
            service = VotingService(db)
            res = await service.open_voting_round(game_id, req)
            results.append(res)

    # All 5 invocations must return the same session
    assert len(results) == 5
    for r in results:
        assert r.voting_round_id == round_id
        assert r.status == "OPEN"

    # Verify database has strictly 1 record
    async with memory_db_factory() as db:
        sessions = (await db.execute(select(VotingSession))).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].voting_round_id == round_id


@pytest.mark.asyncio
async def test_close_voting_round_repeated_calls(memory_db_factory) -> None:  # type: ignore[no-untyped-def]
    """Closing the same round multiple times generates strictly ONE VOTE_RESULT_READY event."""
    round_id = "round-idem-close-200"
    game_id = "game-idem-200"
    req = OpenVotingRequest(
        round_number=1,
        voting_round_id=round_id,
        eligible_voters=["p1", "p2"],
        eligible_targets=["p1", "p2"],
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        tie_rule="NO_ELIMINATION",
    )

    async with memory_db_factory() as db:
        service = VotingService(db)
        await service.open_voting_round(game_id, req)
        # Cast one vote
        await service.submit_vote(game_id, voter_id="p1", target_id="p2")

    # Close sequentially 4 times
    close_results = []
    for _ in range(4):
        async with memory_db_factory() as db:
            service = VotingService(db)
            res = await service.close_voting_round(game_id, round_id, reason="ENGINE_REQUEST")
            close_results.append(res)

    # All returns have same outcome
    assert len(close_results) == 4
    for cr in close_results:
        assert cr.status == "CLOSED"
        assert cr.outcome == "ELIMINATE"
        assert cr.eliminated_target_id == "p2"

    # Verify exactly one VOTE_RESULT_READY and one VOTING_CLOSED in outbox
    async with memory_db_factory() as db:
        outbox_events = (
            (await db.execute(select(OutboxMessage).where(OutboxMessage.game_id == game_id)))
            .scalars()
            .all()
        )
        result_events = [e for e in outbox_events if e.event_type == "VOTE_RESULT_READY"]
        close_events = [e for e in outbox_events if e.event_type == "VOTING_CLOSED"]

        assert len(result_events) == 1
        assert len(close_events) == 1


@pytest.mark.asyncio
async def test_outbox_worker_publishes_and_marks_published(memory_db_factory) -> None:  # type: ignore[no-untyped-def]
    """Outbox worker polls pending events, publishes to broker, and marks status PUBLISHED."""
    mock_publisher = AsyncMock(spec=EventPublisher)

    async with memory_db_factory() as db:
        service = VotingService(db)
        req = OpenVotingRequest(
            round_number=1,
            voting_round_id="round-worker-test",
            eligible_voters=["v1", "v2"],
            eligible_targets=["v1", "v2"],
            deadline=datetime.now(UTC) + timedelta(minutes=5),
            tie_rule="NO_ELIMINATION",
        )
        await service.open_voting_round("game-worker-1", req)
        await service.submit_vote("game-worker-1", voter_id="v1", target_id="v2")
        await service.close_voting_round("game-worker-1", "round-worker-test")

    # Run outbox worker batch using a patched transaction_scope
    worker = OutboxWorker(publisher=mock_publisher)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction_scope():  # type: ignore[no-untyped-def]
        async with memory_db_factory() as session, session.begin():
            yield session

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.events.outbox_worker.transaction_scope", fake_transaction_scope)
        dispatched = await worker.process_batch(limit=20)

    assert dispatched > 0
    assert mock_publisher.publish.call_count == dispatched

    # Verify all messages are now marked PUBLISHED
    async with memory_db_factory() as db:
        pending = (
            (await db.execute(select(OutboxMessage).where(OutboxMessage.status == "PENDING")))
            .scalars()
            .all()
        )
        assert len(pending) == 0
