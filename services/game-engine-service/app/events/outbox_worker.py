"""Outbox background worker dispatching pending events to broker."""

import asyncio
import contextlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.events.envelope import EventEnvelope
from app.events.publisher import EventPublisher
from app.models.outbox import OutboxEventModel

logger = logging.getLogger("game_engine.outbox_worker")


class OutboxWorker:
    """Polls outbox_events table and publishes pending events."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.session_factory = session_factory
        self.publisher = publisher
        self.poll_interval_seconds = poll_interval_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def process_pending_events(self) -> int:
        """Process a single batch of pending outbox events."""
        async with self.session_factory() as session:
            stmt = (
                select(OutboxEventModel)
                .where(OutboxEventModel.status == "PENDING")
                .order_by(OutboxEventModel.id.asc())
                .limit(50)
            )
            result = await session.execute(stmt)
            events = result.scalars().all()

            count = 0
            for item in events:
                try:
                    envelope = EventEnvelope(
                        event_id=item.event_id,
                        event_type=item.event_type,
                        occurred_at=item.occurred_at,
                        game_id=item.game_id,
                        version=item.version,
                        payload=item.payload,
                    )
                    await self.publisher.publish(envelope)
                    item.status = "PUBLISHED"
                    count += 1
                except Exception as exc:
                    logger.error(
                        f"Failed publishing outbox event {item.event_id}: {exc}",
                        extra={"game_id": item.game_id},
                    )
                    item.retry_count += 1
                    if item.retry_count > 5:
                        item.status = "FAILED"

            if count > 0:
                await session.commit()
            return count

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.process_pending_events()
            except Exception as e:
                logger.error(f"Error in outbox worker loop: {e}")
            await asyncio.sleep(self.poll_interval_seconds)
