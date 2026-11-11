"""Outbox worker relaying database outbox messages to message broker."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.events.envelope import EventEnvelope
from app.events.publisher import EventPublisher, get_event_publisher
from app.models.outbox import OutboxMessage
from app.repositories.database import transaction_scope

logger = logging.getLogger("voting_service.outbox_worker")


class OutboxWorker:
    """Dispatches pending transactional outbox records to the event broker."""

    def __init__(self, publisher: EventPublisher | None = None) -> None:
        self.publisher = publisher or get_event_publisher()
        self._running = False

    async def process_batch(self, limit: int = 50) -> int:
        """Process a single batch of pending outbox messages atomically.

        Returns:
            Number of successfully dispatched messages.
        """
        dispatched_count = 0
        async with transaction_scope() as session:
            # Lock pending messages to avoid multi-worker concurrency duplication
            stmt = (
                select(OutboxMessage)
                .where(OutboxMessage.status == "PENDING")
                .order_by(OutboxMessage.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            messages = list(result.scalars().all())

            if not messages:
                return 0

            for msg in messages:
                try:
                    envelope = EventEnvelope(
                        event_id=msg.event_id,
                        event_type=msg.event_type,
                        occurred_at=msg.occurred_at,
                        game_id=msg.game_id,
                        version=msg.version,
                        payload=msg.payload,
                    )
                    await self.publisher.publish(envelope)

                    msg.status = "PUBLISHED"
                    msg.published_at = datetime.now(UTC)
                    dispatched_count += 1
                except Exception as exc:
                    logger.error(
                        "Failed to publish outbox event %s: %s",
                        msg.event_id,
                        str(exc),
                    )
                    msg.retry_count += 1
                    if msg.retry_count >= 10:
                        msg.status = "FAILED"

            await session.commit()

        return dispatched_count

    async def run_loop(self, poll_interval_seconds: float = 1.0) -> None:
        """Continuously poll and dispatch outbox records until cancelled."""
        self._running = True
        logger.info("Outbox worker loop started.")
        while self._running:
            try:
                processed = await self.process_batch()
                if processed == 0:
                    await asyncio.sleep(poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Outbox worker loop error: %s", str(exc))
                await asyncio.sleep(poll_interval_seconds)
        logger.info("Outbox worker loop stopped.")

    def stop(self) -> None:
        """Signal worker loop termination."""
        self._running = False
