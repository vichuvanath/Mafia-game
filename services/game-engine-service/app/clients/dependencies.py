"""FastAPI dependency providers for clients and external services."""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import Depends

from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.clients.role_client import HttpRoleServiceClient, RoleServiceClient
from app.clients.voting_client import HttpVotingServiceClient, VotingServiceClient
from app.config import Settings, get_settings
from app.state.redis_state import RedisStateManager

_redis_client: aioredis.Redis | None = None  # type: ignore[type-arg]
_fake_role_client: FakeRoleServiceClient = FakeRoleServiceClient()
_fake_voting_client: FakeVotingServiceClient = FakeVotingServiceClient()


def get_role_client(settings: Settings = Depends(get_settings)) -> RoleServiceClient:
    """Provide RoleServiceClient based on CLIENT_MODE setting."""
    if settings.CLIENT_MODE == "fake":
        return _fake_role_client
    return HttpRoleServiceClient(base_url=settings.ROLE_SERVICE_URL)


def get_voting_client(settings: Settings = Depends(get_settings)) -> VotingServiceClient:
    """Provide VotingServiceClient based on CLIENT_MODE setting."""
    if settings.CLIENT_MODE == "fake":
        return _fake_voting_client
    return HttpVotingServiceClient(base_url=settings.VOTING_SERVICE_URL)


def get_redis_client(settings: Settings = Depends(get_settings)) -> aioredis.Redis:  # type: ignore[type-arg]
    """Provide async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    return _redis_client


def get_redis_state_manager(
    redis_client: aioredis.Redis = Depends(get_redis_client),  # type: ignore[type-arg]
    settings: Settings = Depends(get_settings),
) -> RedisStateManager:
    """Provide RedisStateManager."""
    return RedisStateManager(redis_client=redis_client, key_prefix=settings.REDIS_KEY_PREFIX)
