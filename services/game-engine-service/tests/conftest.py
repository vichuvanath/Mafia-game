"""Global pytest configuration and fixtures."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, cast

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.clients.dependencies import (
    get_redis_state_manager,
    get_role_client,
    get_voting_client,
)
from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.config import Settings
from app.config.rules import GameRulesSettings
from app.database import get_db_session
from app.engine.timers import Clock
from app.main import create_app
from app.models.base import Base
from app.state.redis_state import RedisStateManager
from tests.fixtures.fake_redis import FakeAsyncRedis


class FixedClock(Clock):
    """Deterministic clock for testing."""

    def __init__(self, start_time: datetime | None = None) -> None:
        self._current_time = start_time or datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._current_time

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._current_time += timedelta(seconds=seconds)


@pytest.fixture
def fixed_clock() -> FixedClock:
    """Fixture providing a controllable clock."""
    return FixedClock()


@pytest.fixture
def test_rules() -> GameRulesSettings:
    """Fixture providing customizable game rules settings."""
    return GameRulesSettings(
        min_players=5,
        max_players=12,
        role_assignment_timeout_seconds=5,
        night_phase_duration_seconds=5,
        night_resolution_duration_seconds=1,
        day_phase_duration_seconds=1,
        discussion_phase_duration_seconds=5,
        voting_phase_duration_seconds=5,
        elimination_phase_duration_seconds=1,
        doctor_can_self_protect=True,
    )


@pytest.fixture
def test_settings(test_rules: GameRulesSettings) -> Settings:
    """Fixture providing application settings with test defaults."""
    return Settings(
        APP_ENV="test",
        JWT_SECRET_KEY="dev-jwt-secret-key-32-chars-minimum-required!",
        INTERNAL_SERVICE_TOKEN="dev-secret-internal-service-token",
        CLIENT_MODE="fake",
        rules=test_rules,
    )


@pytest.fixture
def fake_role_client() -> FakeRoleServiceClient:
    """Fixture providing in-memory fake role service."""
    return FakeRoleServiceClient()


@pytest.fixture
def fake_voting_client() -> FakeVotingServiceClient:
    """Fixture providing in-memory fake voting service."""
    return FakeVotingServiceClient()


@pytest.fixture
async def async_db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite async database session for fast unit/persistence tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def fake_redis() -> FakeAsyncRedis:
    """Fixture providing in-memory fake Redis."""
    return FakeAsyncRedis()


@pytest.fixture
def redis_state_manager(fake_redis: FakeAsyncRedis) -> RedisStateManager:
    """Fixture providing RedisStateManager wired to fake Redis."""
    return RedisStateManager(redis_client=cast(Any, fake_redis), key_prefix="game:")


@pytest.fixture
async def api_client(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> AsyncGenerator[AsyncClient, None]:
    """Test AsyncClient with in-memory DB and fake dependencies overridden."""
    app = create_app()

    # Override dependencies
    app.dependency_overrides[get_db_session] = lambda: async_db_session
    app.dependency_overrides[get_redis_state_manager] = lambda: redis_state_manager
    app.dependency_overrides[get_role_client] = lambda: fake_role_client
    app.dependency_overrides[get_voting_client] = lambda: fake_voting_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


def generate_test_jwt(
    player_id: str,
    username: str = "TestPlayer",
    room_id: str = "room-1",
    secret: str = "dev-jwt-secret-key-32-chars-minimum-required!",
    algorithm: str = "HS256",
    expires_in_seconds: int = 3600,
) -> str:
    """Helper to generate valid JWTs for test requests."""
    from datetime import timedelta

    payload: dict[str, Any] = {
        "sub": player_id,
        "username": username,
        "room_id": room_id,
        "exp": datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)
