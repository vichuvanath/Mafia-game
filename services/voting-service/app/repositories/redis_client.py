"""Redis client connection pool, distributed lock, and rate limiting."""

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger("voting_service.redis")
settings = get_settings()

_redis_pool: aioredis.ConnectionPool | None = None
_redis_client: Redis | None = None


def get_redis_client() -> Redis:
    """Retrieve or create the global async Redis client."""
    global _redis_pool, _redis_client
    if _redis_client is None:
        _redis_pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
        _redis_client = aioredis.Redis(connection_pool=_redis_pool)
    return _redis_client


def set_redis_client(client: Redis | None) -> None:
    """Explicitly set or mock the global Redis client."""
    global _redis_client
    _redis_client = client


async def close_redis() -> None:
    """Close the global Redis client and connection pool."""
    global _redis_client, _redis_pool
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
    if _redis_pool is not None:
        await _redis_pool.disconnect()
        _redis_pool = None


def prefixed_key(suffix: str) -> str:
    """Generate a service-namespaced Redis key."""
    prefix = settings.REDIS_KEY_PREFIX
    if not prefix.endswith(":"):
        prefix = f"{prefix}:"
    return f"{prefix}{suffix}"


@asynccontextmanager
async def distributed_lock(
    lock_key: str,
    timeout_seconds: float = 10.0,
    retry_delay: float = 0.05,
    max_retries: int = 50,
) -> AsyncGenerator[bool, None]:
    """Distributed lock implementation using Redis SET NX PX.

    Ensures mutual exclusion across multiple service instances.
    """
    redis = get_redis_client()
    full_key = prefixed_key(f"lock:{lock_key}")
    lock_value = str(uuid.uuid4())
    acquired = False
    ttl_ms = int(timeout_seconds * 1000)

    for _ in range(max_retries):
        try:
            # SET NX PX ensures atomicity
            res = await redis.set(full_key, lock_value, nx=True, px=ttl_ms)
            if res:
                acquired = True
                break
        except Exception as e:
            logger.warning("Redis lock attempt failed: %s", str(e))
        import asyncio

        await asyncio.sleep(retry_delay)

    if not acquired:
        yield False
        return

    try:
        yield True
    finally:
        # Release lock using Lua script to guarantee only the holder unlocks
        unlock_lua = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            await redis.eval(unlock_lua, 1, full_key, lock_value)  # type: ignore[no-untyped-call]
        except Exception as e:
            logger.error("Failed to release lock '%s': %s", full_key, str(e))


async def check_rate_limit(
    identifier: str,
    limit: int | None = None,
    window_seconds: int = 60,
) -> bool:
    """Sliding window rate limiter using Redis sorted sets.

    Returns:
        True if the request is permitted within limits, False if rate-limited.
    """
    effective_limit = limit or settings.RATE_LIMIT_VOTES_PER_MINUTE
    redis = get_redis_client()
    key = prefixed_key(f"ratelimit:{identifier}")
    now = time.time()
    window_start = now - window_seconds

    pipe = redis.pipeline()
    try:
        # Remove timestamps outside sliding window
        pipe.zremrangebyscore(key, 0, window_start)
        # Count requests in window
        pipe.zcard(key)
        # Add current timestamp
        pipe.zadd(key, {str(now): now})
        # Set expiry for cleanup
        pipe.expire(key, window_seconds + 5)
        results = await pipe.execute()
        current_count = results[1]
        return bool(current_count < effective_limit)
    except Exception as e:
        logger.warning("Rate limit check failed, failing open: %s", str(e))
        return True
