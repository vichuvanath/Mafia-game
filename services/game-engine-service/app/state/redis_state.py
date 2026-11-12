"""Redis live state and deadline management."""

from __future__ import annotations

import json
from typing import Any, cast

import redis.asyncio as aioredis


class RedisStateManager:
    """Manages active game session cache, action buffers, and ephemeral timers in Redis.

    Prefix: 'game:'
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,  # type: ignore[type-arg]
        key_prefix: str = "game:",
    ) -> None:
        self.redis = redis_client
        self.key_prefix = key_prefix

    def _key(self, game_id: str, suffix: str) -> str:
        return f"{self.key_prefix}{game_id}:{suffix}"

    @property
    def deadlines_key(self) -> str:
        return f"{self.key_prefix}deadlines"

    # --- Live Session Caching ---
    async def set_live_session(
        self, game_id: str, data: dict[str, Any], ttl_seconds: int = 86400
    ) -> None:
        """Cache current game state in Redis for rapid read access."""
        key = self._key(game_id, "state")
        await self.redis.set(key, json.dumps(data), ex=ttl_seconds)

    async def get_live_session(self, game_id: str) -> dict[str, Any] | None:
        """Retrieve cached game state."""
        key = self._key(game_id, "state")
        raw = await self.redis.get(key)
        if raw is None:
            return None
        raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return cast(dict[str, Any], json.loads(raw_str))

    async def delete_live_session(self, game_id: str) -> None:
        """Evict cached session."""
        key = self._key(game_id, "state")
        await self.redis.delete(key)

    # --- Night Action Buffering ---
    async def record_night_action(
        self, game_id: str, role: str, actor_id: str, target_id: str
    ) -> None:
        """Buffer a night action under the role key in a Redis hash."""
        key = self._key(game_id, "night_actions")
        payload = json.dumps({"actor_id": actor_id, "target_id": target_id})
        await self.redis.hset(key, role, payload)

    async def get_night_actions(self, game_id: str) -> dict[str, dict[str, str]]:
        """Retrieve all buffered night actions for a game session."""
        key = self._key(game_id, "night_actions")
        raw = await self.redis.hgetall(key)
        actions: dict[str, dict[str, str]] = {}
        for role_key, data_raw in raw.items():
            role_str = role_key.decode("utf-8") if isinstance(role_key, bytes) else str(role_key)
            data_str = data_raw.decode("utf-8") if isinstance(data_raw, bytes) else str(data_raw)
            actions[role_str] = json.loads(data_str)
        return actions

    async def clear_night_actions(self, game_id: str) -> None:
        """Clear night action buffer after night resolution."""
        key = self._key(game_id, "night_actions")
        await self.redis.delete(key)

    # --- Redis-Backed Deadline Scheduler ---
    async def schedule_deadline(
        self,
        game_id: str,
        phase: str,
        round_number: int,
        version: int,
        deadline_epoch: float,
    ) -> None:
        """Register a phase deadline in the Redis Sorted Set."""
        member = f"{game_id}:{phase}:{round_number}:{version}"
        await self.redis.zadd(self.deadlines_key, {member: deadline_epoch})

    async def get_due_deadlines(self, current_epoch: float) -> list[dict[str, Any]]:
        """Fetch all deadlines that have expired as of current_epoch."""
        raw_members = await self.redis.zrangebyscore(
            self.deadlines_key,
            min=0,
            max=current_epoch,
        )
        due: list[dict[str, Any]] = []
        for raw in raw_members:
            item_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            parts = item_str.split(":")
            if len(parts) == 4:
                due.append(
                    {
                        "game_id": parts[0],
                        "phase": parts[1],
                        "round_number": int(parts[2]),
                        "version": int(parts[3]),
                        "member": item_str,
                    }
                )
        return due

    async def remove_deadline(self, member: str) -> None:
        """Remove a fired or invalidated deadline entry from the sorted set."""
        await self.redis.zrem(self.deadlines_key, member)

    async def cancel_deadlines_for_game(self, game_id: str) -> None:
        """Remove any scheduled deadlines associated with game_id."""
        raw_members = await self.redis.zrangebyscore(self.deadlines_key, min="-inf", max="+inf")
        to_remove: list[str] = []
        for raw in raw_members:
            item_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            if item_str.startswith(f"{game_id}:"):
                to_remove.append(item_str)
        if to_remove:
            await self.redis.zrem(self.deadlines_key, *to_remove)
