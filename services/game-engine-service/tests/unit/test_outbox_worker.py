"""Unit tests for Transactional Outbox Worker."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.envelope import EventEnvelope
from app.events.outbox_worker import OutboxWorker
from app.models.outbox import OutboxEventModel
from app.repositories.game_repository import GameRepository
from tests.fixtures.fake_publisher import FakeEventPublisher


@pytest.mark.asyncio
async def test_outbox_worker_publishes_pending_events(
    async_db_session: AsyncSession,
) -> None:
    repo = GameRepository(async_db_session)
    publisher = FakeEventPublisher()

    # Stage 2 pending events
    ev1 = OutboxEventModel(
        event_id="evt-ob-1",
        event_type="GAME_STARTED",
        game_id="game-ob-1",
        occurred_at=datetime.now(UTC),
        version=1,
        payload={"game_id": "game-ob-1"},
    )
    ev2 = OutboxEventModel(
        event_id="evt-ob-2",
        event_type="PHASE_CHANGED",
        game_id="game-ob-1",
        occurred_at=datetime.now(UTC),
        version=2,
        payload={"new_phase": "DAY"},
    )
    await repo.add_outbox_event(ev1)
    await repo.add_outbox_event(ev2)
    await async_db_session.commit()

    # Create dummy session factory that yields async_db_session
    class DummySessionFactory:
        def __call__(self) -> AsyncSession:
            return async_db_session

    worker = OutboxWorker(
        session_factory=DummySessionFactory(),  # type: ignore[arg-type]
        publisher=publisher,
    )

    processed_count = await worker.process_pending_events()
    assert processed_count == 2
    assert len(publisher.published_events) == 2
    assert publisher.published_events[0].event_id == "evt-ob-1"
    assert publisher.published_events[1].event_id == "evt-ob-2"

    # Verify status in database is now PUBLISHED
    pending = await repo.get_pending_outbox_events()
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_outbox_worker_handles_publish_failure(
    async_db_session: AsyncSession,
) -> None:
    repo = GameRepository(async_db_session)

    class FailingPublisher(FakeEventPublisher):
        async def publish(self, event: EventEnvelope) -> None:
            raise ConnectionError("RabbitMQ broker unreachable")

    ev = OutboxEventModel(
        event_id="evt-fail-1",
        event_type="GAME_STARTED",
        game_id="game-fail",
        occurred_at=datetime.now(UTC),
        version=1,
        payload={},
        retry_count=5,  # Next failure exceeds retry limit (>5)
    )
    await repo.add_outbox_event(ev)
    await async_db_session.commit()

    class DummySessionFactory:
        def __call__(self) -> AsyncSession:
            return async_db_session

    worker = OutboxWorker(
        session_factory=DummySessionFactory(),  # type: ignore[arg-type]
        publisher=FailingPublisher(),
    )

    await worker.process_pending_events()
    assert ev.retry_count == 6
    assert ev.status == "FAILED"
