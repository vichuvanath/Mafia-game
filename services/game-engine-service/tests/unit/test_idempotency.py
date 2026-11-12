"""Idempotency tests verifying duplicate event delivery does not corrupt state."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.config import Settings
from app.config.rules import Phase
from app.events.consumers.player_disconnected import handle_player_disconnected
from app.events.consumers.role_assignment import handle_role_assignment_completed
from app.events.consumers.room_ready import handle_room_ready
from app.events.consumers.vote_result import handle_vote_result_ready
from app.events.envelope import EventEnvelope
from app.models.game_sessions import GameSessionModel
from app.repositories.game_repository import GameRepository
from app.services.game_service import GameService
from app.state.redis_state import RedisStateManager


@pytest.mark.asyncio
async def test_room_ready_duplicate_delivery(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> None:
    game_service = GameService(
        session=async_db_session,
        redis_state=redis_state_manager,
        role_client=fake_role_client,
        voting_client=fake_voting_client,
        settings=test_settings,
    )
    repo = GameRepository(async_db_session)

    envelope = EventEnvelope(
        event_id="evt-dup-rr-1",
        event_type="ROOM_READY",
        occurred_at=datetime.now(UTC),
        game_id="game-dup-1",
        version=1,
        payload={
            "room_id": "room-dup-1",
            "player_ids": ["p1", "p2", "p3", "p4", "p5"],
        },
    )

    # First delivery: processed
    first = await handle_room_ready(envelope, async_db_session, game_service)
    assert first is True

    # Second delivery: ignored
    second = await handle_room_ready(envelope, async_db_session, game_service)
    assert second is False

    # Third delivery: ignored
    third = await handle_room_ready(envelope, async_db_session, game_service)
    assert third is False

    # Verify session in DB is created only once and version is 1
    session = await repo.get_session("game-dup-1")
    assert session is not None
    assert session.version == 1
    assert session.phase == Phase.NIGHT.value


@pytest.mark.asyncio
async def test_vote_result_ready_duplicate_delivery(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
) -> None:
    repo = GameRepository(async_db_session)

    # Seed a game in VOTING phase with version 3
    game = GameSessionModel(
        game_id="game-dup-vote",
        room_id="room-dv",
        phase=Phase.VOTING.value,
        version=3,
        round_number=1,
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

    envelope = EventEnvelope(
        event_id="evt-dup-vote-1",
        event_type="VOTE_RESULT_READY",
        occurred_at=datetime.now(UTC),
        game_id="game-dup-vote",
        version=3,
        payload={
            "round_number": 1,
            "eliminated_player_id": "p3",
            "votes": {"p1": "p3", "p2": "p3", "p4": "p3", "p5": "p3", "p3": "p1"},
            "is_tie": False,
            "tied_player_ids": [],
        },
    )

    # First delivery: processes elimination of p3 and bumps version to 4
    res1 = await handle_vote_result_ready(envelope, async_db_session, redis_state_manager)
    assert res1 is True

    # Second delivery with same event_id: must be rejected
    res2 = await handle_vote_result_ready(envelope, async_db_session, redis_state_manager)
    assert res2 is False

    # Third delivery: must be rejected
    res3 = await handle_vote_result_ready(envelope, async_db_session, redis_state_manager)
    assert res3 is False

    # Verify version advanced exactly once and p3 was eliminated once
    refetched = await repo.get_session("game-dup-vote")
    assert refetched is not None
    assert refetched.version == 4
    players_map = {p["player_id"]: p for p in refetched.players}
    assert players_map["p3"]["is_alive"] is False


@pytest.mark.asyncio
async def test_player_disconnected_duplicate_delivery(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
) -> None:
    repo = GameRepository(async_db_session)
    game = GameSessionModel(
        game_id="game-dup-dc",
        room_id="room-dc",
        phase=Phase.NIGHT.value,
        version=1,
        players=[{"player_id": "p1", "is_connected": True, "is_alive": True}],
        role_assignments={"p1": "MAFIA"},
    )
    await repo.create_session(game)
    await async_db_session.commit()

    envelope = EventEnvelope(
        event_id="evt-dup-dc-1",
        event_type="PLAYER_DISCONNECTED",
        occurred_at=datetime.now(UTC),
        game_id="game-dup-dc",
        version=1,
        payload={"player_id": "p1", "disconnected_at": datetime.now(UTC).isoformat()},
    )

    # Multiple deliveries
    r1 = await handle_player_disconnected(envelope, async_db_session, redis_state_manager)
    assert r1 is True

    r2 = await handle_player_disconnected(envelope, async_db_session, redis_state_manager)
    assert r2 is False

    refetched = await repo.get_session("game-dup-dc")
    assert refetched is not None
    assert refetched.version == 2
    assert refetched.players[0]["is_connected"] is False


@pytest.mark.asyncio
async def test_role_assignment_completed_duplicate_delivery(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
) -> None:
    repo = GameRepository(async_db_session)
    game = GameSessionModel(
        game_id="game-dup-rac",
        room_id="room-rac",
        phase=Phase.ROLE_ASSIGNMENT.value,
        version=1,
        players=[
            {"player_id": "p1", "is_alive": True},
            {"player_id": "p2", "is_alive": True},
            {"player_id": "p3", "is_alive": True},
        ],
        role_assignments={},
    )
    await repo.create_session(game)
    await async_db_session.commit()

    envelope = EventEnvelope(
        event_id="evt-dup-rac-1",
        event_type="ROLE_ASSIGNMENT_COMPLETED",
        occurred_at=datetime.now(UTC),
        game_id="game-dup-rac",
        version=1,
        payload={
            "assignments": {
                "p1": "MAFIA",
                "p2": "DOCTOR",
                "p3": "VILLAGER",
            }
        },
    )

    # First delivery: transitions to NIGHT and bumps version to 2
    r1 = await handle_role_assignment_completed(envelope, async_db_session, redis_state_manager)
    assert r1 is True

    # Second delivery: duplicate event_id ignored
    r2 = await handle_role_assignment_completed(envelope, async_db_session, redis_state_manager)
    assert r2 is False

    refetched = await repo.get_session("game-dup-rac")
    assert refetched is not None
    assert refetched.version == 2
    assert refetched.phase == Phase.NIGHT.value
    assert refetched.role_assignments["p1"] == "MAFIA"
