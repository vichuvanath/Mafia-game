"""Unit tests for PostgreSQL persistence, checkpoints, optimistic locking, and outbox."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game_sessions import GameSessionModel
from app.models.outbox import OutboxEventModel
from app.repositories.game_repository import GameRepository
from app.state.checkpoints import CheckpointManager, ConcurrencyConflictError


@pytest.mark.asyncio
async def test_create_and_fetch_game_session(async_db_session: AsyncSession) -> None:
    repo = GameRepository(async_db_session)

    session = GameSessionModel(
        game_id="game-101",
        room_id="room-alpha",
        phase="ROLE_ASSIGNMENT",
        version=1,
        round_number=1,
        players=[
            {"player_id": "p1", "username": "Alice", "is_alive": True, "is_connected": True},
            {"player_id": "p2", "username": "Bob", "is_alive": True, "is_connected": True},
        ],
        role_assignments={"p1": "MAFIA", "p2": "DOCTOR"},
        winner=None,
    )
    await repo.create_session(session)

    fetched = await repo.get_session("game-101")
    assert fetched is not None
    assert fetched.game_id == "game-101"
    assert fetched.room_id == "room-alpha"
    assert fetched.phase == "ROLE_ASSIGNMENT"
    assert fetched.version == 1
    assert len(fetched.players) == 2
    assert fetched.role_assignments["p1"] == "MAFIA"


@pytest.mark.asyncio
async def test_optimistic_concurrency_success_and_conflict(
    async_db_session: AsyncSession,
) -> None:
    repo = GameRepository(async_db_session)
    session = GameSessionModel(
        game_id="game-102",
        room_id="room-beta",
        phase="NIGHT",
        version=1,
        round_number=1,
        players=[{"player_id": "p1", "username": "Alice", "is_alive": True}],
        role_assignments={"p1": "MAFIA"},
    )
    await repo.create_session(session)

    # 1. Update with correct expected version (1) -> succeeds and bumps to 2
    session.phase = "NIGHT_RESOLUTION"
    updated = await repo.update_session(
        game=session,
        expected_version=1,
        phase_details={"killed_player_id": None},
    )
    assert updated.version == 2
    assert updated.phase == "NIGHT_RESOLUTION"

    # Verify phase history was recorded
    history = await repo.get_phase_history("game-102")
    assert len(history) == 1
    assert history[0].phase == "NIGHT_RESOLUTION"
    assert history[0].details == {"killed_player_id": None}

    # 2. Update with stale expected version (1 instead of 2) -> raises ConcurrencyConflictError
    session.phase = "DAY"
    with pytest.raises(ConcurrencyConflictError) as exc_info:
        await repo.update_session(
            game=session,
            expected_version=1,
        )
    assert exc_info.value.game_id == "game-102"
    assert exc_info.value.expected_version == 1
    assert exc_info.value.actual_version == 2


@pytest.mark.asyncio
async def test_checkpoint_manager_with_outbox(async_db_session: AsyncSession) -> None:
    cm = CheckpointManager(async_db_session)
    repo = GameRepository(async_db_session)

    session = GameSessionModel(
        game_id="game-103",
        room_id="room-gamma",
        phase="DAY",
        version=1,
        round_number=1,
        players=[{"player_id": "p1", "username": "Alice", "is_alive": True}],
        role_assignments={"p1": "MAFIA"},
    )
    await repo.create_session(session)

    outbox_event = OutboxEventModel(
        event_id="evt-999",
        event_type="PHASE_CHANGED",
        game_id="game-103",
        occurred_at=datetime.now(UTC),
        version=2,
        payload={"phase": "DISCUSSION"},
    )

    session.phase = "DISCUSSION"
    saved = await cm.save_checkpoint(
        session_model=session,
        expected_version=1,
        phase_details={"announcement": "Morning has broken"},
        outbox_events=[outbox_event],
    )
    assert saved.version == 2

    # Verify outbox event was saved atomically in the same transaction
    pending = await repo.get_pending_outbox_events()
    assert len(pending) == 1
    assert pending[0].event_id == "evt-999"
    assert pending[0].event_type == "PHASE_CHANGED"
    assert pending[0].status == "PENDING"


@pytest.mark.asyncio
async def test_processed_events_idempotency(async_db_session: AsyncSession) -> None:
    repo = GameRepository(async_db_session)

    assert await repo.is_event_processed("evt-001") is False

    await repo.record_processed_event(
        event_id="evt-001",
        event_type="ROOM_READY",
        game_id="game-104",
    )

    assert await repo.is_event_processed("evt-001") is True


@pytest.mark.asyncio
async def test_repository_active_sessions_and_outbox_helpers(
    async_db_session: AsyncSession,
) -> None:
    repo = GameRepository(async_db_session)

    outbox_event = OutboxEventModel(
        event_id="evt-init",
        event_type="GAME_STARTED",
        game_id="game-active-1",
        occurred_at=datetime.now(UTC),
        version=1,
        payload={"started": True},
    )

    # 1. Create with outbox events
    session1 = GameSessionModel(
        game_id="game-active-1",
        room_id="room-act",
        phase="NIGHT",
        version=1,
        round_number=1,
        players=[{"player_id": "p1", "is_alive": True}],
    )
    await repo.create_session(session1, outbox_events=[outbox_event])

    session2 = GameSessionModel(
        game_id="game-over-1",
        room_id="room-done",
        phase="GAME_OVER",
        version=5,
        round_number=3,
        players=[{"player_id": "p1", "is_alive": False}],
    )
    await repo.create_session(session2)

    # 2. get_active_sessions filters out GAME_OVER
    active = await repo.get_active_sessions()
    active_ids = {g.game_id for g in active}
    assert "game-active-1" in active_ids
    assert "game-over-1" not in active_ids

    # 3. add_outbox_event directly
    extra_outbox = OutboxEventModel(
        event_id="evt-extra",
        event_type="PHASE_CHANGED",
        game_id="game-active-1",
        occurred_at=datetime.now(UTC),
        version=2,
        payload={"phase": "DAY"},
    )
    await repo.add_outbox_event(extra_outbox)
    pending = await repo.get_pending_outbox_events()
    assert len(pending) == 2

    # 4. update_session for nonexistent game raises ValueError
    ghost = GameSessionModel(
        game_id="ghost-id",
        room_id="room-ghost",
        phase="DAY",
    )
    with pytest.raises(ValueError):
        await repo.update_session(ghost, expected_version=1)


@pytest.mark.asyncio
async def test_checkpoint_manager_insert_new(async_db_session: AsyncSession) -> None:
    cm = CheckpointManager(async_db_session)
    session = GameSessionModel(
        game_id="game-fresh",
        room_id="room-fresh",
        phase="ROLE_ASSIGNMENT",
        version=0,
        players=[],
    )
    saved = await cm.save_checkpoint(
        session_model=session,
        expected_version=0,
        phase_details={"step": "init"},
    )
    assert saved.version == 1
    assert saved.game_id == "game-fresh"
