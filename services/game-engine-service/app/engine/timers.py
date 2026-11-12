"""Timer protocols, deadline calculation, and Redis deadline scheduler worker."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.state.redis_state import RedisStateManager

logger = logging.getLogger("game_engine.timers")


class Clock(Protocol):
    """Protocol for injectable clock to allow deterministic testing."""

    def now(self) -> datetime:
        """Return current datetime."""
        ...


class SystemClock:
    """Real system clock implementation returning UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def calculate_phase_deadline(
    duration_seconds: int,
    clock: Clock | None = None,
) -> datetime:
    """Calculate future deadline timestamp given duration."""
    current_time = clock.now() if clock else datetime.now(UTC)
    return current_time + timedelta(seconds=duration_seconds)


def is_deadline_passed(
    deadline: datetime,
    clock: Clock | None = None,
) -> bool:
    """Check if the given deadline has elapsed."""
    current_time = clock.now() if clock else datetime.now(UTC)
    # Ensure timezone awareness
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return current_time >= deadline


class DeadlineSchedulerWorker:
    """Scans Redis-backed deadlines and triggers phase advancements when deadlines expire."""

    def __init__(
        self,
        redis_state: RedisStateManager,
        game_service_factory: Any,
        clock: Clock | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.redis_state = redis_state
        self.game_service_factory = game_service_factory
        self.clock = clock or SystemClock()
        self.poll_interval_seconds = poll_interval_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def check_and_trigger_due_deadlines(self, current_epoch: float | None = None) -> int:
        """Process all overdue deadlines registered in Redis.

        Returns number of deadlines processed.
        """
        now_ts = current_epoch if current_epoch is not None else self.clock.now().timestamp()
        due_deadlines = await self.redis_state.get_due_deadlines(now_ts)

        triggered_count = 0
        for item in due_deadlines:
            game_id = item["game_id"]
            expected_phase = item["phase"]
            expected_round = item["round_number"]
            expected_version = item["version"]
            member = item["member"]

            try:
                # Remove deadline from Redis first or after execution
                await self.redis_state.remove_deadline(member)

                # Execute advance using a fresh game_service instance
                async with self.game_service_factory() as service:
                    game = await service.repository.get_session(game_id, for_update=True)
                    if not game:
                        continue

                    # Guard against stale deadline triggers
                    if (
                        game.phase != expected_phase
                        or game.round_number != expected_round
                        or game.version != expected_version
                    ):
                        logger.debug(
                            f"Skipping stale deadline {member} for game {game_id} (current phase: {game.phase})"
                        )
                        continue

                    logger.info(
                        f"Deadline elapsed for game {game_id} (phase: {expected_phase}). Advancing phase."
                    )
                    await service.advance_phase(game_id=game_id, force=True)
                    triggered_count += 1
            except Exception as exc:
                logger.error(
                    f"Error processing deadline {member} for game {game_id}: {exc}",
                    extra={"game_id": game_id},
                )

        return triggered_count

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            import contextlib

            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.check_and_trigger_due_deadlines()
            except Exception as exc:
                logger.error(f"Error in deadline scheduler loop: {exc}")
            await asyncio.sleep(self.poll_interval_seconds)
