"""Consumer for ROLE_ASSIGNMENT_COMPLETED event."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.config.rules import Phase
from app.engine.timers import calculate_phase_deadline
from app.events.envelope import EventEnvelope
from app.models.base import utc_now
from app.models.outbox import OutboxEventModel
from app.repositories.game_repository import GameRepository
from app.services.game_service import to_public_session_response
from app.state.checkpoints import CheckpointManager
from app.state.redis_state import RedisStateManager

logger = logging.getLogger("game_engine.consumers.role_assignment")


async def handle_role_assignment_completed(
    envelope: EventEnvelope,
    session: AsyncSession,
    redis_state: RedisStateManager,
) -> bool:
    """Consume ROLE_ASSIGNMENT_COMPLETED idempotently and transition game to NIGHT."""
    repo = GameRepository(session)
    if await repo.is_event_processed(envelope.event_id):
        logger.info(
            f"ROLE_ASSIGNMENT_COMPLETED event {envelope.event_id} already processed. Skipping."
        )
        return False

    game = await repo.get_session(envelope.game_id, for_update=True)
    if not game:
        logger.warning(
            f"Game {envelope.game_id} not found for ROLE_ASSIGNMENT_COMPLETED. Event discarded."
        )
        return False

    if game.phase != Phase.ROLE_ASSIGNMENT.value:
        logger.warning(
            f"Game {envelope.game_id} not in ROLE_ASSIGNMENT (current: {game.phase}). Ignoring."
        )
        return False

    settings = get_settings()
    rules = settings.rules

    assignments = envelope.payload.get("assignments", {})
    game.role_assignments = assignments
    game.phase = Phase.NIGHT.value
    deadline = calculate_phase_deadline(rules.night_phase_duration_seconds)
    game.phase_deadline = deadline

    now = utc_now()
    phase_event = OutboxEventModel(
        event_id=str(uuid.uuid4()),
        event_type="PHASE_CHANGED",
        game_id=game.game_id,
        occurred_at=now,
        version=game.version + 1,
        payload={
            "game_id": game.game_id,
            "previous_phase": Phase.ROLE_ASSIGNMENT.value,
            "new_phase": Phase.NIGHT.value,
            "round_number": game.round_number,
            "deadline": deadline.isoformat(),
        },
    )

    cm = CheckpointManager(session)
    saved = await cm.save_checkpoint(
        session_model=game,
        expected_version=game.version,
        phase_details={"role_assignment_completed": True},
        outbox_events=[phase_event],
    )

    await redis_state.schedule_deadline(
        game_id=saved.game_id,
        phase=saved.phase,
        round_number=saved.round_number,
        version=saved.version,
        deadline_epoch=deadline.timestamp(),
    )
    public_resp = to_public_session_response(saved, reveal_role_on_death=rules.reveal_role_on_death)
    await redis_state.set_live_session(saved.game_id, public_resp.model_dump(mode="json"))

    await repo.record_processed_event(
        event_id=envelope.event_id,
        event_type=envelope.event_type,
        game_id=envelope.game_id,
    )
    await session.commit()
    logger.info(f"ROLE_ASSIGNMENT_COMPLETED processed for game {envelope.game_id}. Night started.")
    return True
