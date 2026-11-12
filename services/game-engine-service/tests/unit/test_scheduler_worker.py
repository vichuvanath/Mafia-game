"""Unit tests for DeadlineSchedulerWorker using deterministic clock."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.config import Settings
from app.config.rules import Phase
from app.engine.timers import DeadlineSchedulerWorker
from app.models.game_sessions import GameSessionModel
from app.repositories.game_repository import GameRepository
from app.services.game_service import GameService
from app.state.redis_state import RedisStateManager
from tests.conftest import FixedClock


@pytest.mark.asyncio
async def test_scheduler_worker_triggers_overdue_deadlines(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> None:
    clock = FixedClock(datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC))
    repo = GameRepository(async_db_session)

    deadline = clock.now() + timedelta(seconds=30)
    game = GameSessionModel(
        game_id="game-sched-1",
        room_id="room-sched-1",
        phase=Phase.NIGHT.value,
        version=1,
        round_number=1,
        phase_deadline=deadline,
        players=[
            {"player_id": "p1", "is_alive": True},
            {"player_id": "p2", "is_alive": True},
            {"player_id": "p3", "is_alive": True},
            {"player_id": "p4", "is_alive": True},
            {"player_id": "p5", "is_alive": True},
        ],
        role_assignments={
            "p1": "MAFIA",
            "p2": "DOCTOR",
            "p3": "VILLAGER",
            "p4": "VILLAGER",
            "p5": "VILLAGER",
        },
    )
    await repo.create_session(game)
    await async_db_session.commit()

    # Schedule deadline in Redis
    await redis_state_manager.schedule_deadline(
        game_id="game-sched-1",
        phase=Phase.NIGHT.value,
        round_number=1,
        version=1,
        deadline_epoch=deadline.timestamp(),
    )

    # GameService factory context manager
    @asynccontextmanager
    async def service_factory() -> AsyncGenerator[GameService, None]:
        yield GameService(
            session=async_db_session,
            redis_state=redis_state_manager,
            role_client=fake_role_client,
            voting_client=fake_voting_client,
            settings=test_settings,
        )

    worker = DeadlineSchedulerWorker(
        redis_state=redis_state_manager,
        game_service_factory=service_factory,
        clock=clock,
    )

    # 1. Before deadline: nothing triggered
    triggered = await worker.check_and_trigger_due_deadlines()
    assert triggered == 0
    refetched = await repo.get_session("game-sched-1")
    assert refetched is not None
    assert refetched.phase == Phase.NIGHT.value

    # 2. Advance clock past deadline: triggered and advanced to DAY
    clock.advance(seconds=35)
    triggered = await worker.check_and_trigger_due_deadlines()
    assert triggered == 1

    refetched_after = await repo.get_session("game-sched-1")
    assert refetched_after is not None
    assert refetched_after.phase == Phase.DAY.value
    assert refetched_after.version == 2


@pytest.mark.asyncio
async def test_scheduler_worker_skips_stale_deadline(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> None:
    clock = FixedClock(datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC))
    repo = GameRepository(async_db_session)

    # Game is already at version 3 in DAY phase
    game = GameSessionModel(
        game_id="game-sched-stale",
        room_id="room-sched-2",
        phase=Phase.DAY.value,
        version=3,
        round_number=1,
        players=[{"player_id": "p1", "is_alive": True}],
        role_assignments={"p1": "MAFIA"},
    )
    await repo.create_session(game)
    await async_db_session.commit()

    # Schedule a stale deadline from NIGHT phase version 1
    await redis_state_manager.schedule_deadline(
        game_id="game-sched-stale",
        phase=Phase.NIGHT.value,
        round_number=1,
        version=1,
        deadline_epoch=clock.now().timestamp() - 10,  # Already past
    )

    @asynccontextmanager
    async def service_factory() -> AsyncGenerator[GameService, None]:
        yield GameService(
            session=async_db_session,
            redis_state=redis_state_manager,
            role_client=fake_role_client,
            voting_client=fake_voting_client,
            settings=test_settings,
        )

    worker = DeadlineSchedulerWorker(
        redis_state=redis_state_manager,
        game_service_factory=service_factory,
        clock=clock,
    )

    # Worker detects stale deadline (phase/version mismatch), skips it, and removes entry
    triggered = await worker.check_and_trigger_due_deadlines()
    assert triggered == 0

    # Ensure stale deadline was cleaned up
    due = await redis_state_manager.get_due_deadlines(clock.now().timestamp() + 100)
    assert due == []


@pytest.mark.asyncio
async def test_scheduler_worker_start_and_stop(
    redis_state_manager: RedisStateManager,
) -> None:
    @asynccontextmanager
    async def dummy_factory() -> AsyncGenerator[None, None]:
        yield None

    worker = DeadlineSchedulerWorker(
        redis_state=redis_state_manager,
        game_service_factory=dummy_factory,
        poll_interval_seconds=0.01,
    )
    await worker.start()
    assert worker._running is True
    assert worker._task is not None

    # Wait brief moment and stop
    await asyncio.sleep(0.02)
    await worker.stop()
    assert worker._running is False
