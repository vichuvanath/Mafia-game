"""Recovery tests verifying crash-tolerance and startup reconciliation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.config import Settings
from app.config.rules import Phase
from app.engine.recovery import recover_active_games
from app.models.game_sessions import GameSessionModel
from app.repositories.game_repository import GameRepository
from app.services.game_service import GameService
from app.state.redis_state import RedisStateManager
from tests.conftest import FixedClock


@pytest.mark.asyncio
async def test_recovery_with_future_deadline(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> None:
    clock = FixedClock(datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC))
    repo = GameRepository(async_db_session)

    future_deadline = clock.now() + timedelta(seconds=60)
    game = GameSessionModel(
        game_id="game-rec-future",
        room_id="room-rec-1",
        phase=Phase.NIGHT.value,
        version=1,
        round_number=1,
        phase_deadline=future_deadline,
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

    # Simulate service crash: wipe Redis entirely
    await redis_state_manager.redis.flushdb()
    assert await redis_state_manager.get_live_session("game-rec-future") is None
    assert await redis_state_manager.get_due_deadlines(clock.now().timestamp() + 100) == []

    # Run recovery on startup
    service = GameService(
        session=async_db_session,
        redis_state=redis_state_manager,
        role_client=fake_role_client,
        voting_client=fake_voting_client,
        settings=test_settings,
    )
    stats = await recover_active_games(
        repository=repo,
        redis_state=redis_state_manager,
        game_service=service,
        clock=clock,
    )

    assert stats["rearmed"] == 1
    assert stats["advanced"] == 0

    # Assert Redis live state is restored from durable DB checkpoint
    cached = await redis_state_manager.get_live_session("game-rec-future")
    assert cached is not None
    assert cached["phase"] == Phase.NIGHT.value

    # Assert deadline is re-armed in Redis
    due = await redis_state_manager.get_due_deadlines(future_deadline.timestamp() + 1)
    assert len(due) == 1
    assert due[0]["game_id"] == "game-rec-future"


@pytest.mark.asyncio
async def test_recovery_with_overdue_deadline(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> None:
    start_time = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FixedClock(start_time)
    repo = GameRepository(async_db_session)

    # Deadline was 30 seconds after start
    past_deadline = start_time + timedelta(seconds=30)
    game = GameSessionModel(
        game_id="game-rec-overdue",
        room_id="room-rec-2",
        phase=Phase.NIGHT.value,
        version=1,
        round_number=1,
        phase_deadline=past_deadline,
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

    # Buffer an action before crash
    await redis_state_manager.record_night_action("game-rec-overdue", "MAFIA", "p1", "p3")

    # Time passes while service is down: advance clock past deadline
    clock.advance(seconds=120)

    # Restart service and run recovery
    service = GameService(
        session=async_db_session,
        redis_state=redis_state_manager,
        role_client=fake_role_client,
        voting_client=fake_voting_client,
        settings=test_settings,
    )
    stats = await recover_active_games(
        repository=repo,
        redis_state=redis_state_manager,
        game_service=service,
        clock=clock,
    )

    assert stats["advanced"] == 1
    assert stats["rearmed"] == 0

    # Overdue NIGHT immediately resolved and advanced to DAY
    refetched = await repo.get_session("game-rec-overdue")
    assert refetched is not None
    assert refetched.phase == Phase.DAY.value
    assert refetched.version == 2
