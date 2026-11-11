"""Pytest configuration and common fixtures."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure services/voting-service is on sys.path
SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def mock_redis_for_unit_tests(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    """Automatically mock Redis unless marked as real_containers."""
    if "real_containers" in request.keywords:
        yield None
        return

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock(return_value=1)
    mock_redis.zadd = AsyncMock(return_value=1)
    mock_redis.zrem = AsyncMock(return_value=1)
    mock_redis.zrangebyscore = AsyncMock(return_value=[])
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 0, 1, True])
    mock_redis.pipeline.return_value = pipe
    from app.repositories.redis_client import set_redis_client

    set_redis_client(mock_redis)
    try:
        yield mock_redis
    finally:
        set_redis_client(None)
