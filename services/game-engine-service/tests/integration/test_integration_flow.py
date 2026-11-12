"""Integration tests connecting to real PostgreSQL, Redis, and RabbitMQ services.

Verifies:
1. Persistence and checkpoints on real PostgreSQL (including optimistic locking).
2. Redis state manager caching, night action buffering, and deadline scheduling on real Redis.
3. OutboxWorker publishing events from PostgreSQL outbox table to real RabbitMQ exchange.
4. Real queue consumption, envelope validation, and end-to-end event idempotency.
5. Consistency between Redis cache and PostgreSQL durable checkpoints.
"""

import json
import uuid
from datetime import UTC, datetime

import aio_pika
import pytest
import redis.asyncio as aioredis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.config import Settings
from app.config.rules import GameRulesSettings, Phase
from app.events.consumers.room_ready import handle_room_ready
from app.events.envelope import EventEnvelope
from app.events.outbox_worker import OutboxWorker
from app.events.publisher import RabbitMQEventPublisher
from app.models.game_sessions import GameSessionModel
from app.models.outbox import OutboxEventModel
from app.repositories.game_repository import GameRepository
from app.services.game_service import GameService
from app.state.checkpoints import CheckpointManager, ConcurrencyConflictError
from app.state.redis_state import RedisStateManager

# Real local container ports (from docker-compose.test.yml)
REAL_PG_PORT = 5434
REAL_REDIS_PORT = 6379
REAL_RABBITMQ_PORT = 5672

REAL_PG_URL = f"postgresql+asyncpg://game_engine_user:game_engine_pass@localhost:{REAL_PG_PORT}/game_engine_db"
REAL_REDIS_URL = f"redis://localhost:{REAL_REDIS_PORT}/1"
REAL_RABBITMQ_URL = f"amqp://guest:guest@localhost:{REAL_RABBITMQ_PORT}/"
REAL_EXCHANGE = "mafia.test.integration"


def _is_service_reachable(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (OSError, ConnectionRefusedError):
        return False


requires_real_postgres = pytest.mark.skipif(
    not _is_service_reachable("localhost", REAL_PG_PORT),
    reason=f"Real PostgreSQL service not reachable on port {REAL_PG_PORT}",
)
requires_real_redis = pytest.mark.skipif(
    not _is_service_reachable("localhost", REAL_REDIS_PORT),
    reason=f"Real Redis service not reachable on port {REAL_REDIS_PORT}",
)
requires_real_rabbitmq = pytest.mark.skipif(
    not _is_service_reachable("localhost", REAL_RABBITMQ_PORT),
    reason=f"Real RabbitMQ service not reachable on port {REAL_RABBITMQ_PORT}",
)


@pytest.fixture
async def real_db_session() -> AsyncSession:
    """Session connecting to the real PostgreSQL container."""
    engine = create_async_engine(REAL_PG_URL, echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def real_redis_client() -> aioredis.Redis:
    """Client connecting to real Redis container."""
    client = aioredis.from_url(REAL_REDIS_URL)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@requires_real_postgres
@pytest.mark.asyncio
async def test_real_postgresql_persistence_and_checkpoints(real_db_session: AsyncSession) -> None:
    """Verify durable checkpoints and optimistic locking on real PostgreSQL."""
    repo = GameRepository(real_db_session)
    cm = CheckpointManager(real_db_session)
    game_id = f"real-pg-game-{uuid.uuid4()}"

    # 1. Create session
    session = GameSessionModel(
        game_id=game_id,
        room_id="room-real-pg",
        phase=Phase.NIGHT.value,
        version=1,
        round_number=1,
        players=[
            {"player_id": "p1", "username": "p1", "is_alive": True},
            {"player_id": "p2", "username": "p2", "is_alive": True},
        ],
        role_assignments={"p1": "MAFIA", "p2": "DOCTOR"},
    )
    await repo.create_session(session)
    await real_db_session.commit()

    # 2. Advance checkpoint (version 1 -> 2)
    session.phase = Phase.DAY.value
    saved = await cm.save_checkpoint(
        session_model=session,
        expected_version=1,
        phase_details={"night_resolution": {"someone_died": False}},
    )
    await real_db_session.commit()
    assert saved.version == 2

    # Verify persisted state
    fetched = await repo.get_session(game_id)
    assert fetched is not None
    assert fetched.version == 2
    assert fetched.phase == Phase.DAY.value

    # Verify phase history on real PostgreSQL
    history = await repo.get_phase_history(game_id)
    assert len(history) == 1
    assert history[0].phase == Phase.DAY.value

    # 3. Optimistic locking: Attempt update with stale version 1 -> ConcurrencyConflictError
    session.phase = Phase.DISCUSSION.value
    with pytest.raises(ConcurrencyConflictError) as exc_info:
        await cm.save_checkpoint(
            session_model=session,
            expected_version=1,  # stale version
        )
    assert exc_info.value.expected_version == 1
    assert exc_info.value.actual_version == 2
    await real_db_session.rollback()


@requires_real_redis
@pytest.mark.asyncio
async def test_real_redis_state_and_cache_consistency(real_redis_client: aioredis.Redis) -> None:
    """Verify live session caching, night action buffering, and deadline sorting on real Redis."""
    redis_state = RedisStateManager(redis_client=real_redis_client, key_prefix="game:test:")
    game_id = f"real-redis-game-{uuid.uuid4()}"

    # 1. Record night actions in Redis hash
    await redis_state.record_night_action(game_id, "MAFIA", "p1", "p3")
    await redis_state.record_night_action(game_id, "DOCTOR", "p2", "p4")

    actions = await redis_state.get_night_actions(game_id)
    assert "MAFIA" in actions
    assert actions["MAFIA"]["target_id"] == "p3"
    assert "DOCTOR" in actions
    assert actions["DOCTOR"]["target_id"] == "p4"

    # Clear actions
    await redis_state.clear_night_actions(game_id)
    cleared = await redis_state.get_night_actions(game_id)
    assert len(cleared) == 0

    # 2. Schedule deadline in Redis sorted set
    now = datetime.now(UTC).timestamp()
    await redis_state.schedule_deadline(
        game_id=game_id,
        phase="NIGHT",
        round_number=1,
        version=1,
        deadline_epoch=now + 60.0,
    )

    overdue = await redis_state.get_due_deadlines(now + 120.0)
    assert len(overdue) >= 1
    matching = [d for d in overdue if d["game_id"] == game_id]
    assert len(matching) == 1
    assert matching[0]["phase"] == "NIGHT"

    # Cancel deadline
    await redis_state.cancel_deadlines_for_game(game_id)
    overdue_after = await redis_state.get_due_deadlines(now + 120.0)
    assert not any(d["game_id"] == game_id for d in overdue_after)


@requires_real_postgres
@requires_real_rabbitmq
@pytest.mark.asyncio
async def test_real_rabbitmq_outbox_publish_and_consume(
    real_db_session: AsyncSession,
) -> None:
    """Verify OutboxWorker publishes from PostgreSQL to real RabbitMQ and consumer receives message."""
    repo = GameRepository(real_db_session)
    game_id = f"real-rmq-game-{uuid.uuid4()}"
    event_id = str(uuid.uuid4())

    # Clean outbox table for clean test isolation
    await real_db_session.execute(delete(OutboxEventModel))
    await real_db_session.commit()

    # 1. Stage an outbox event in PostgreSQL
    outbox_item = OutboxEventModel(
        event_id=event_id,
        event_type="GAME_STARTED",
        game_id=game_id,
        occurred_at=datetime.now(UTC),
        version=1,
        payload={"game_id": game_id, "room_id": "room-rmq-test", "player_count": 5},
    )
    await repo.add_outbox_event(outbox_item)
    await real_db_session.commit()

    # 2. Connect to RabbitMQ and bind a dedicated test queue
    connection = await aio_pika.connect_robust(REAL_RABBITMQ_URL)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        REAL_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    queue = await channel.declare_queue(f"test_queue_{uuid.uuid4()}", exclusive=True)
    await queue.bind(exchange, routing_key="game.#")

    # 3. Process batch with OutboxWorker
    engine = create_async_engine(REAL_PG_URL, echo=False)
    worker_session_factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False
    )
    publisher = RabbitMQEventPublisher(rabbitmq_url=REAL_RABBITMQ_URL, exchange_name=REAL_EXCHANGE)
    worker = OutboxWorker(
        session_factory=worker_session_factory,
        publisher=publisher,
        poll_interval_seconds=0.1,
    )

    published_count = await worker.process_pending_events()
    assert published_count >= 1

    # Verify event status updated to PUBLISHED in PostgreSQL
    real_db_session.expire_all()
    saved_event = await real_db_session.scalar(
        select(OutboxEventModel).where(OutboxEventModel.event_id == event_id)
    )
    assert saved_event is not None
    assert saved_event.status == "PUBLISHED"

    # 4. Consume from RabbitMQ and assert envelope contents
    incoming_msg = await queue.get(timeout=5.0)
    assert incoming_msg is not None
    async with incoming_msg.process():
        body = json.loads(incoming_msg.body.decode())
        assert body["event_id"] == event_id
        assert body["event_type"] == "GAME_STARTED"
        assert body["game_id"] == game_id
        assert body["version"] == 1
        assert body["payload"]["room_id"] == "room-rmq-test"

    await queue.delete()
    await channel.close()
    await connection.close()
    await publisher.close()
    await engine.dispose()


@requires_real_postgres
@requires_real_redis
@pytest.mark.asyncio
async def test_real_event_consumption_and_idempotency_flow(
    real_db_session: AsyncSession,
    real_redis_client: aioredis.Redis,
) -> None:
    """Verify consumer processes real event into PostgreSQL, ensuring second delivery is skipped."""
    repo = GameRepository(real_db_session)
    redis_state = RedisStateManager(redis_client=real_redis_client, key_prefix="game:test:")
    event_id = str(uuid.uuid4())
    room_id = f"room-real-idemp-{uuid.uuid4()}"
    game_id = str(uuid.uuid4())

    settings = Settings(
        DATABASE_URL=REAL_PG_URL,
        REDIS_URL=REAL_REDIS_URL,
        RABBITMQ_URL=REAL_RABBITMQ_URL,
        rules=GameRulesSettings(),
    )

    game_service = GameService(
        session=real_db_session,
        redis_state=redis_state,
        role_client=FakeRoleServiceClient(),
        voting_client=FakeVotingServiceClient(),
        settings=settings,
    )

    envelope = EventEnvelope(
        event_id=event_id,
        event_type="ROOM_READY",
        occurred_at=datetime.now(UTC),
        game_id=game_id,
        version=1,
        payload={
            "room_id": room_id,
            "player_ids": ["p1", "p2", "p3", "p4", "p5"],
        },
    )

    # First consumption: processed -> True
    first_res = await handle_room_ready(
        envelope=envelope,
        session=real_db_session,
        game_service=game_service,
    )
    assert first_res is True

    # Verify session persisted in real PostgreSQL
    created_session = await repo.get_session(game_id)
    assert created_session is not None
    assert created_session.room_id == room_id
    assert created_session.phase == Phase.NIGHT.value

    # Verify event recorded in processed_events table
    assert await repo.is_event_processed(event_id) is True

    # Second consumption: duplicate -> False (skipped, no duplicate created)
    second_res = await handle_room_ready(
        envelope=envelope,
        session=real_db_session,
        game_service=game_service,
    )
    assert second_res is False
