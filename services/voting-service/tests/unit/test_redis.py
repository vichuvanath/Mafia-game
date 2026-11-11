"""Unit tests for Redis utilities, key prefixing, distributed locking, and rate limiting."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.redis_client import (
    check_rate_limit,
    distributed_lock,
    prefixed_key,
)


def test_prefixed_key() -> None:
    assert prefixed_key("sessions:123") == "voting:sessions:123"
    assert prefixed_key("lock:game-1") == "voting:lock:game-1"


@pytest.mark.asyncio
async def test_distributed_lock_acquisition_and_release() -> None:
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock(return_value=1)

    with patch("app.repositories.redis_client.get_redis_client", return_value=mock_redis):
        async with distributed_lock("test-round", timeout_seconds=1.0) as acquired:
            assert acquired is True

        # Assert set was called with NX PX
        mock_redis.set.assert_called_once()
        # Assert Lua script was executed to release the lock safely
        mock_redis.eval.assert_called_once()


@pytest.mark.asyncio
async def test_distributed_lock_contention_failure() -> None:
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=False)

    with patch("app.repositories.redis_client.get_redis_client", return_value=mock_redis):
        async with distributed_lock("busy-round", retry_delay=0.001, max_retries=3) as acquired:
            assert acquired is False


@pytest.mark.asyncio
async def test_sliding_window_rate_limiter() -> None:
    mock_redis = MagicMock()
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[0, 5, 1, True])  # current_count = 5
    mock_redis.pipeline.return_value = mock_pipe

    with patch("app.repositories.redis_client.get_redis_client", return_value=mock_redis):
        # Under limit (5 < 10)
        allowed = await check_rate_limit("user-1", limit=10, window_seconds=60)
        assert allowed is True

        # Over limit (5 >= 5)
        blocked = await check_rate_limit("user-1", limit=5, window_seconds=60)
        assert blocked is False
