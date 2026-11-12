"""Consumer for ROOM_READY event."""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.envelope import EventEnvelope
from app.repositories.game_repository import GameRepository
from app.services.game_service import GameService

logger = logging.getLogger("game_engine.consumers.room_ready")


async def handle_room_ready(
    envelope: EventEnvelope,
    session: AsyncSession,
    game_service: GameService,
) -> bool:
    """Consume ROOM_READY idempotently and initialize a game session.

    Returns True if processed, False if ignored due to duplication.
    """
    repo = GameRepository(session)
    if await repo.is_event_processed(envelope.event_id):
        logger.info(f"ROOM_READY event {envelope.event_id} already processed. Skipping.")
        return False

    payload = envelope.payload
    room_id = payload["room_id"]
    player_ids = payload["player_ids"]
    player_names = payload.get("player_names", {})

    try:
        # Create session using the envelope's game_id
        await game_service.create_game(
            room_id=room_id,
            player_ids=player_ids,
            player_names=player_names,
            custom_game_id=envelope.game_id,
        )

        await repo.record_processed_event(
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            game_id=envelope.game_id,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.info(
            f"ROOM_READY event {envelope.event_id} already processed concurrently. Skipping."
        )
        return False
    logger.info(
        f"ROOM_READY event {envelope.event_id} successfully processed for game {envelope.game_id}"
    )
    return True
