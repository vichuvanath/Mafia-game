"""Recovery tests verifying state restoration and overdue session resolution on startup."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.base import Base
from app.models.outbox import OutboxMessage
from app.models.voting_sessions import VotingSession
from app.schemas.voting import OpenVotingRequest
from app.services.recovery import run_startup_recovery
from app.services.scheduler import DeadlineScheduler
from app.services.voting_service import VotingService


@pytest.fixture
async def recovery_db_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_recovery_closes_overdue_and_rearms_future(
    recovery_db_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Startup recovery must close overdue sessions and re-arm future deadlines in scheduler."""
    mock_scheduler = AsyncMock(spec=DeadlineScheduler)

    now = datetime.now(UTC)
    overdue_deadline = now + timedelta(seconds=10)
    future_deadline = now + timedelta(minutes=10)
    recovery_eval_time = now + timedelta(seconds=20)

    # 1. Create two open sessions: one overdue at eval time, one future
    async with recovery_db_factory() as db:
        service = VotingService(db)

        # Overdue session
        req_overdue = OpenVotingRequest(
            round_number=1,
            voting_round_id="round-overdue-1",
            eligible_voters=["p1", "p2", "p3"],
            eligible_targets=["p1", "p2", "p3"],
            deadline=overdue_deadline,
            tie_rule="NO_ELIMINATION",
        )
        await service.open_voting_round("game-overdue", req_overdue)
        # Cast one vote in overdue session
        await service.submit_vote("game-overdue", "p1", "p2")

        # Future session
        req_future = OpenVotingRequest(
            round_number=1,
            voting_round_id="round-future-2",
            eligible_voters=["p1", "p2", "p3"],
            eligible_targets=["p1", "p2", "p3"],
            deadline=future_deadline,
            tie_rule="NO_ELIMINATION",
        )
        await service.open_voting_round("game-future", req_future)

    # 2. Simulate service restart by running startup recovery
    @asynccontextmanager
    async def fake_transaction_scope():  # type: ignore[no-untyped-def]
        async with recovery_db_factory() as session, session.begin():
            yield session

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.recovery.transaction_scope", fake_transaction_scope)
        closed, rearmed = await run_startup_recovery(
            scheduler=mock_scheduler, now=recovery_eval_time
        )

    assert closed == 1
    assert rearmed == 1

    # Overdue deadline cancelled from scheduler, future scheduled
    mock_scheduler.cancel_deadline.assert_called_with("round-overdue-1")
    assert mock_scheduler.schedule_deadline.call_count == 1
    rearmed_round_id, rearmed_dt = mock_scheduler.schedule_deadline.call_args[0]
    assert rearmed_round_id == "round-future-2"
    if rearmed_dt.tzinfo is None:
        rearmed_dt = rearmed_dt.replace(tzinfo=UTC)
    assert abs((rearmed_dt - future_deadline).total_seconds()) < 1.0

    # 3. Verify overdue round is CLOSED with correct outbox events
    async with recovery_db_factory() as db:
        overdue_sess = (
            await db.execute(
                select(VotingSession).where(VotingSession.voting_round_id == "round-overdue-1")
            )
        ).scalar_one()
        assert overdue_sess.status == "CLOSED"
        assert overdue_sess.close_reason == "DEADLINE"
        assert overdue_sess.outcome == "ELIMINATE"
        assert overdue_sess.eliminated_target_id == "p2"

        # Check outbox has VOTE_RESULT_READY
        outbox = (
            (await db.execute(select(OutboxMessage).where(OutboxMessage.game_id == "game-overdue")))
            .scalars()
            .all()
        )
        result_events = [e for e in outbox if e.event_type == "VOTE_RESULT_READY"]
        assert len(result_events) == 1

    # 4. Re-running recovery immediately must be a no-op (no duplicates)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.recovery.transaction_scope", fake_transaction_scope)
        closed_again, rearmed_again = await run_startup_recovery(
            scheduler=mock_scheduler, now=recovery_eval_time
        )

    assert closed_again == 0
    assert rearmed_again == 1  # Still re-arms the 1 active session
