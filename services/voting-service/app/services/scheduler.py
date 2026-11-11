"""Redis-backed deadline scheduler and background expiration processor."""

import asyncio
import logging
from datetime import UTC, datetime

from app.repositories.database import transaction_scope
from app.repositories.redis_client import distributed_lock, get_redis_client, prefixed_key
from app.services.voting_service import VotingService

logger = logging.getLogger("voting_service.scheduler")


class DeadlineScheduler:
    """Manages voting deadlines using Redis sorted sets without in-memory sleep timers."""

    def __init__(self, key_name: str = "deadlines") -> None:
        self.key = prefixed_key(key_name)
        self._running = False

    async def schedule_deadline(self, voting_round_id: str, deadline: datetime) -> None:
        """Register or update a round deadline timestamp in Redis sorted set."""
        redis = get_redis_client()
        timestamp = deadline.timestamp()
        await redis.zadd(self.key, {voting_round_id: timestamp})
        logger.info(
            "Scheduled deadline for round %s at %s (%f)",
            voting_round_id,
            deadline.isoformat(),
            timestamp,
        )

    async def cancel_deadline(self, voting_round_id: str) -> None:
        """Remove a round deadline from the scheduler (e.g. on early close)."""
        redis = get_redis_client()
        await redis.zrem(self.key, voting_round_id)

    async def get_expired_round_ids(self, current_time: datetime | None = None) -> list[str]:
        """Fetch all round IDs whose deadlines have elapsed."""
        redis = get_redis_client()
        now_ts = (current_time or datetime.now(UTC)).timestamp()
        # Retrieve members with score <= current timestamp
        expired = await redis.zrangebyscore(self.key, min=0, max=now_ts)
        return list(expired)

    async def pop_expired_round_ids(self, current_time: datetime | None = None) -> list[str]:
        """Atomically fetch and remove expired round IDs from the sorted set."""
        redis = get_redis_client()
        now_ts = (current_time or datetime.now(UTC)).timestamp()

        # Lua script to atomically read and remove expired items
        lua_pop_expired = """
        local items = redis.call('zrangebyscore', KEYS[1], 0, ARGV[1])
        if #items > 0 then
            redis.call('zremrangebyscore', KEYS[1], 0, ARGV[1])
        end
        return items
        """
        result = await redis.eval(lua_pop_expired, 1, self.key, str(now_ts))  # type: ignore[no-untyped-call]
        if not result:
            return []
        return [item if isinstance(item, str) else item.decode("utf-8") for item in result]

    async def process_expired_deadlines(self, current_time: datetime | None = None) -> int:
        """Check for overdue rounds and trigger automatic deadline closure.

        Returns:
            Number of rounds closed due to deadline expiration.
        """
        expired_ids = await self.pop_expired_round_ids(current_time=current_time)
        closed_count = 0

        for round_id in expired_ids:
            try:
                async with (
                    distributed_lock(f"session:{round_id}"),
                    transaction_scope() as session,
                ):
                    service = VotingService(session)
                    target_session = await service.repo.get_session_by_round_id(
                        round_id, for_update=True
                    )
                    if target_session and target_session.status == "OPEN":
                        logger.info(
                            "Closing round %s due to deadline expiration",
                            round_id,
                        )
                        await service._close_round_internal(target_session, reason="DEADLINE")
                        closed_count += 1
                    await session.commit()
            except Exception as exc:
                logger.error(
                    "Error processing expired deadline for round %s: %s",
                    round_id,
                    str(exc),
                )

        return closed_count

    async def run_scheduler_loop(self, poll_interval_seconds: float = 1.0) -> None:
        """Background poll loop checking and triggering expired deadlines."""
        self._running = True
        logger.info("Deadline scheduler loop started.")
        while self._running:
            try:
                await self.process_expired_deadlines()
                await asyncio.sleep(poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in scheduler loop: %s", str(exc))
                await asyncio.sleep(poll_interval_seconds)
        logger.info("Deadline scheduler loop stopped.")

    def stop(self) -> None:
        """Signal scheduler loop termination."""
        self._running = False


_scheduler: DeadlineScheduler | None = None


def get_deadline_scheduler() -> DeadlineScheduler:
    """Retrieve singleton deadline scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = DeadlineScheduler()
    return _scheduler
