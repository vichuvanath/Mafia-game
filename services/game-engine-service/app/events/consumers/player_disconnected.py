"""Consumer for PLAYER_DISCONNECTED event."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.events.envelope import EventEnvelope
from app.repositories.game_repository import GameRepository
from app.services.game_service import to_public_session_response
from app.state.checkpoints import CheckpointManager
from app.state.redis_state import RedisStateManager

logger = logging.getLogger("game_engine.consumers.player_disconnected")


async def handle_player_disconnected(
    envelope: EventEnvelope,
    session: AsyncSession,
    redis_state: RedisStateManager,
) -> bool:
    """Consume PLAYER_DISCONNECTED idempotently and set disconnect grace period."""
    repo = GameRepository(session)
    if await repo.is_event_processed(envelope.event_id):
        logger.info(f"PLAYER_DISCONNECTED event {envelope.event_id} already processed. Skipping.")
        return False

    game = await repo.get_session(envelope.game_id, for_update=True)
    if not game:
        logger.warning(f"Game {envelope.game_id} not found for PLAYER_DISCONNECTED. Discarded.")
        return False

    payload = envelope.payload
    player_id = payload.get("player_id")

    player_found = False
    for p in game.players:
        if p["player_id"] == player_id:
            p["is_connected"] = False
            player_found = True
            break

    if not player_found:
        logger.warning(f"Player {player_id} not found in game {envelope.game_id}. Ignoring.")
        return False

    settings = get_settings()
    rules = settings.rules

    # Track disconnect in Redis with grace period TTL
    disconnect_key = f"{redis_state.key_prefix}{game.game_id}:disconnect:{player_id}"
    await redis_state.redis.set(
        disconnect_key, "disconnected", ex=rules.disconnect_grace_period_seconds
    )

    cm = CheckpointManager(session)
    saved = await cm.save_checkpoint(
        session_model=game,
        expected_version=game.version,
        phase_details={"player_disconnected": player_id},
    )

    public_resp = to_public_session_response(saved, reveal_role_on_death=rules.reveal_role_on_death)
    await redis_state.set_live_session(saved.game_id, public_resp.model_dump(mode="json"))

    await repo.record_processed_event(
        event_id=envelope.event_id,
        event_type=envelope.event_type,
        game_id=envelope.game_id,
    )
    await session.commit()
    logger.info(f"PLAYER_DISCONNECTED processed for player {player_id} in game {envelope.game_id}")
    return True
