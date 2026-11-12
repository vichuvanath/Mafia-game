"""Concurrency tests for Game Engine Service.

Verifies:
1. Two workers attempting to advance the exact same game concurrently; exactly one succeeds and the version increments once.
2. Stale worker gets ConcurrencyConflictError (HTTP 409).
3. Concurrent API requests to advance phase.
4. Concurrent idempotency handling (e.g. duplicate ROOM_READY events).
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.clients.dependencies import (
    get_redis_state_manager,
    get_role_client,
    get_voting_client,
)
from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.config import get_settings
from app.config.rules import Phase
from app.database import get_db_session
from app.events.consumers.room_ready import handle_room_ready
from app.events.envelope import EventEnvelope
from app.main import create_app
from app.models.base import Base
from app.models.game_sessions import GameSessionModel
from app.repositories.game_repository import GameRepository
from app.services.game_service import GameService
from app.state.checkpoints import ConcurrencyConflictError
from tests.conftest import generate_test_jwt
from tests.fixtures.fake_redis import FakeAsyncRedis


@pytest.fixture
async def shared_db_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Create file-backed SQLite with WAL mode so concurrent sessions have independent connections."""
    import os
    import tempfile

    from sqlalchemy import text

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}?timeout=30", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA busy_timeout=5000;"))
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
    yield session_factory

    await engine.dispose()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_concurrent_advance_same_game_optimistic_locking(
    shared_db_session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeAsyncRedis,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
) -> None:
    """Two workers try to advance the same game at version 1 concurrently.

    Requirement: Exactly one succeeds and the version increments once (to 2).
    The losing worker fails with ConcurrencyConflictError.
    """
    from app.state.redis_state import RedisStateManager

    settings = get_settings()
    redis_state = RedisStateManager(redis_client=fake_redis, key_prefix="game:")  # type: ignore[arg-type]

    # 1. Initialize a game session at version 1 in NIGHT phase
    game_id = "concurrent-game-1"
    async with shared_db_session_factory() as init_session:
        repo = GameRepository(init_session)
        game_model = GameSessionModel(
            game_id=game_id,
            room_id="room-concurrent-1",
            phase=Phase.NIGHT.value,
            version=1,
            round_number=1,
            players=[
                {"player_id": "p1", "username": "p1", "is_alive": True},
                {"player_id": "p2", "username": "p2", "is_alive": True},
                {"player_id": "p3", "username": "p3", "is_alive": True},
                {"player_id": "p4", "username": "p4", "is_alive": True},
                {"player_id": "p5", "username": "p5", "is_alive": True},
            ],
            role_assignments={
                "p1": "MAFIA",
                "p2": "DOCTOR",
                "p3": "VILLAGER",
                "p4": "VILLAGER",
                "p5": "VILLAGER",
            },
        )
        await repo.create_session(game_model)
        await init_session.commit()

    # 2. Worker task simulating concurrent phase advance with expected_version=1
    async def worker_advance(worker_name: str) -> Any:
        async with shared_db_session_factory() as session:
            service = GameService(
                session=session,
                redis_state=redis_state,
                role_client=fake_role_client,
                voting_client=fake_voting_client,
                settings=settings,
            )
            try:
                res = await service.advance_phase(game_id=game_id, expected_version=1)
                await session.commit()
                return res
            except Exception as e:
                await session.rollback()
                raise e

    # 3. Fire both workers concurrently
    results = await asyncio.gather(
        worker_advance("Worker-A"),
        worker_advance("Worker-B"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    # Assert exactly one succeeded and one failed
    assert (
        len(successes) == 1
    ), f"Expected 1 success, got {len(successes)}: {successes}, failures: {[type(f).__name__ + ': ' + str(f) for f in failures]}"
    assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}: {failures}"

    # Assert the failure is a concurrency conflict
    exc = failures[0]
    from fastapi import HTTPException

    assert isinstance(exc, ConcurrencyConflictError | HTTPException)
    if isinstance(exc, HTTPException):
        assert exc.status_code == 409

    # 4. Verify durable database state
    async with shared_db_session_factory() as verify_session:
        verify_repo = GameRepository(verify_session)
        final_game = await verify_repo.get_session(game_id)
        assert final_game is not None
        # Version must have incremented exactly once from 1 to 2
        assert final_game.version == 2
        assert final_game.phase == Phase.DAY.value

        # Exactly 1 phase history record
        history = await verify_repo.get_phase_history(game_id)
        assert len(history) == 1


@pytest.mark.asyncio
async def test_concurrent_api_advance_requests(
    shared_db_session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeAsyncRedis,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
) -> None:
    """Two concurrent HTTP requests attempting to advance the same game session."""
    from app.state.redis_state import RedisStateManager

    settings = get_settings()
    redis_state = RedisStateManager(redis_client=fake_redis, key_prefix="game:")  # type: ignore[arg-type]
    app = create_app()

    async def get_test_db() -> AsyncGenerator[AsyncSession, None]:
        async with shared_db_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = get_test_db
    app.dependency_overrides[get_redis_state_manager] = lambda: redis_state
    app.dependency_overrides[get_role_client] = lambda: fake_role_client
    app.dependency_overrides[get_voting_client] = lambda: fake_voting_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create game
        token = generate_test_jwt("p1")
        create_resp = await client.post(
            "/games",
            json={"room_id": "room-concur-api", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        game_id = create_resp.json()["game_id"]
        assert create_resp.json()["version"] == 1

        headers = {"X-Internal-Token": settings.INTERNAL_SERVICE_TOKEN}

        # Send 2 advance requests concurrently with expected_version=1
        task1 = client.post(
            f"/internal/games/{game_id}/advance",
            headers=headers,
            json={"expected_version": 1},
        )
        task2 = client.post(
            f"/internal/games/{game_id}/advance",
            headers=headers,
            json={"expected_version": 1},
        )

        responses = await asyncio.gather(task1, task2)

        statuses = [r.status_code for r in responses]
        assert 200 in statuses
        assert 409 in statuses

        # Verify state in DB
        async with shared_db_session_factory() as session:
            repo = GameRepository(session)
            game = await repo.get_session(game_id)
            assert game is not None
            assert game.version == 2


@pytest.mark.asyncio
async def test_concurrent_idempotent_room_ready_events(
    shared_db_session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeAsyncRedis,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
) -> None:
    """Two workers receive the same ROOM_READY event concurrently. Exactly one processes it."""
    from datetime import UTC, datetime

    from app.state.redis_state import RedisStateManager

    settings = get_settings()
    redis_state = RedisStateManager(redis_client=fake_redis, key_prefix="game:")  # type: ignore[arg-type]
    event_id = "evt-room-ready-concur"

    event = EventEnvelope(
        event_id=event_id,
        event_type="ROOM_READY",
        occurred_at=datetime.now(UTC),
        game_id="room-concur-evt",
        version=1,
        payload={
            "room_id": "room-concur-evt",
            "player_ids": ["p1", "p2", "p3", "p4", "p5"],
        },
    )

    async def worker_handle() -> Any:
        async with shared_db_session_factory() as session:
            service = GameService(
                session=session,
                redis_state=redis_state,
                role_client=fake_role_client,
                voting_client=fake_voting_client,
                settings=settings,
            )
            res = await handle_room_ready(
                envelope=event,
                session=session,
                game_service=service,
            )
            await session.commit()
            return res

    res1, res2 = await asyncio.gather(worker_handle(), worker_handle(), return_exceptions=True)

    # Both calls return safely without unhandled exception
    assert not isinstance(res1, Exception), f"res1 raised {res1}"
    assert not isinstance(res2, Exception), f"res2 raised {res2}"

    # Exactly one processed the event (returned True), one was skipped (returned False)
    results = [res1, res2]
    assert True in results
    assert False in results

    # Verify DB marked event as processed
    async with shared_db_session_factory() as session:
        repo = GameRepository(session)
        assert await repo.is_event_processed(event_id) is True
