"""Timeout tests verifying injectable clock deadline closures and late vote rejection."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.base import Base
from app.schemas.errors import VotingAppException
from app.schemas.voting import OpenVotingRequest
from app.services.scheduler import DeadlineScheduler
from app.services.voting_service import VotingService


@pytest.fixture
async def timeout_db_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_deadline_scheduler_clock_injection(timeout_db_factory) -> None:  # type: ignore[no-untyped-def]
    """Injectable clock triggers deadline expiration without real sleeping."""
    now = datetime.now(UTC)
    deadline = now + timedelta(minutes=5)
    t_after = deadline + timedelta(minutes=1)

    round_id = "round-timeout-1"
    game_id = "game-timeout-1"

    # Open round
    async with timeout_db_factory() as db:
        service = VotingService(db)
        req = OpenVotingRequest(
            round_number=1,
            voting_round_id=round_id,
            eligible_voters=["p1", "p2", "p3"],
            eligible_targets=["p1", "p2", "p3"],
            deadline=deadline,
            tie_rule="NO_ELIMINATION",
        )
        await service.open_voting_round(game_id, req)
        # p1 votes for p2
        await service.submit_vote(game_id, "p1", "p2")

    scheduler = DeadlineScheduler()

    # Mock Redis to simulate pop_expired_round_ids returning round_id when evaluated at t_after
    scheduler.pop_expired_round_ids = AsyncMock(return_value=[round_id])  # type: ignore[method-assign]

    @asynccontextmanager
    async def fake_transaction_scope():  # type: ignore[no-untyped-def]
        async with timeout_db_factory() as session, session.begin():
            yield session

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.scheduler.transaction_scope", fake_transaction_scope)
        closed_count = await scheduler.process_expired_deadlines(current_time=t_after)

    assert closed_count == 1

    # Verify session is now CLOSED due to DEADLINE
    async with timeout_db_factory() as db:
        service = VotingService(db)
        sess = await service.repo.get_session_by_round_id(round_id)
        assert sess is not None
        assert sess.status == "CLOSED"
        assert sess.close_reason == "DEADLINE"
        assert sess.outcome == "ELIMINATE"
        assert sess.eliminated_target_id == "p2"


@pytest.mark.asyncio
async def test_late_votes_after_deadline_rejected(timeout_db_factory) -> None:  # type: ignore[no-untyped-def]
    """Submitting a ballot after deadline timestamp is rejected with VOTING_CLOSED."""
    t0 = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    deadline = t0 + timedelta(seconds=1)
    round_id = "round-late-vote"
    game_id = "game-late-vote"

    async with timeout_db_factory() as db:
        service = VotingService(db)
        req = OpenVotingRequest(
            round_number=1,
            voting_round_id=round_id,
            eligible_voters=["p1", "p2"],
            eligible_targets=["p1", "p2"],
            deadline=deadline,
            tie_rule="NO_ELIMINATION",
        )
        await service.open_voting_round(game_id, req)

    # Fast forward time past deadline
    t_late = t0 + timedelta(seconds=5)

    async with timeout_db_factory() as db:
        service = VotingService(db)
        with patch("app.services.voting_service.datetime") as mock_dt:
            mock_dt.now.return_value = t_late
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            with pytest.raises(VotingAppException) as exc_info:
                await service.submit_vote(game_id, voter_id="p1", target_id="p2")

            assert exc_info.value.code == "VOTING_CLOSED"
            assert "expired" in exc_info.value.message.lower()
