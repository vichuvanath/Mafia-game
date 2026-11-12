"""Consumer for VOTE_RESULT_READY event."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.config.rules import Phase, Role
from app.engine.timers import calculate_phase_deadline
from app.engine.voting_rules import evaluate_votes
from app.engine.win_conditions import evaluate_winner
from app.events.envelope import EventEnvelope
from app.models.base import utc_now
from app.models.outbox import OutboxEventModel
from app.repositories.game_repository import GameRepository
from app.services.game_service import to_public_session_response
from app.state.checkpoints import CheckpointManager
from app.state.redis_state import RedisStateManager

logger = logging.getLogger("game_engine.consumers.vote_result")


async def handle_vote_result_ready(
    envelope: EventEnvelope,
    session: AsyncSession,
    redis_state: RedisStateManager,
) -> bool:
    """Consume VOTE_RESULT_READY idempotently, validate votes, and apply elimination."""
    repo = GameRepository(session)
    if await repo.is_event_processed(envelope.event_id):
        logger.info(f"VOTE_RESULT_READY event {envelope.event_id} already processed. Skipping.")
        return False

    game = await repo.get_session(envelope.game_id, for_update=True)
    if not game:
        logger.warning(f"Game {envelope.game_id} not found for VOTE_RESULT_READY. Discarded.")
        return False

    if game.phase != Phase.VOTING.value:
        logger.warning(
            f"Game {envelope.game_id} not in VOTING phase (current: {game.phase}). Ignoring."
        )
        return False

    payload = envelope.payload
    round_number = payload.get("round_number", game.round_number)
    if round_number != game.round_number:
        logger.warning(
            f"VOTE_RESULT_READY round {round_number} does not match game round {game.round_number}. Ignoring."
        )
        return False

    settings = get_settings()
    rules = settings.rules
    living_pids = {p["player_id"] for p in game.players if p.get("is_alive", True)}
    votes = payload.get("votes", {})

    # Validate vote results using pure engine logic
    vote_result = evaluate_votes(
        votes=votes,
        living_player_ids=living_pids,
        tie_rule=rules.tie_rule,
        max_revotes=rules.max_revotes,
        abstention_handling=rules.abstention_handling,
    )

    outbox_events: list[OutboxEventModel] = []
    now = utc_now()
    expected_version = game.version

    if vote_result.requires_revote:
        # Revote within VOTING
        game.phase = Phase.VOTING.value
        game.phase_deadline = calculate_phase_deadline(rules.voting_phase_duration_seconds)
    else:
        # Transition to ELIMINATION
        game.phase = Phase.ELIMINATION.value
        game.phase_deadline = calculate_phase_deadline(rules.elimination_phase_duration_seconds)

        eliminated_id = vote_result.eliminated_player_id
        if eliminated_id:
            for p in game.players:
                if p["player_id"] == eliminated_id:
                    p["is_alive"] = False
                    if rules.reveal_role_on_death:
                        p["revealed_role"] = game.role_assignments.get(eliminated_id)

            outbox_events.append(
                OutboxEventModel(
                    event_id=str(uuid.uuid4()),
                    event_type="PLAYER_ELIMINATION_CONFIRMED",
                    game_id=game.game_id,
                    occurred_at=now,
                    version=expected_version + 1,
                    payload={
                        "player_id": eliminated_id,
                        "round_number": game.round_number,
                        "revealed_role": game.role_assignments.get(eliminated_id)
                        if rules.reveal_role_on_death
                        else None,
                    },
                )
            )

        # Check win condition
        living_roles = [
            Role(game.role_assignments[p["player_id"]])
            for p in game.players
            if p.get("is_alive", True)
        ]
        winner = evaluate_winner(living_roles)
        if winner:
            game.phase = Phase.GAME_OVER.value
            game.winner = winner.value
            game.phase_deadline = None
            outbox_events.append(
                OutboxEventModel(
                    event_id=str(uuid.uuid4()),
                    event_type="GAME_OVER",
                    game_id=game.game_id,
                    occurred_at=now,
                    version=expected_version + 1,
                    payload={"winner": winner.value, "round_number": game.round_number},
                )
            )

    outbox_events.append(
        OutboxEventModel(
            event_id=str(uuid.uuid4()),
            event_type="PHASE_CHANGED",
            game_id=game.game_id,
            occurred_at=now,
            version=expected_version + 1,
            payload={
                "game_id": game.game_id,
                "previous_phase": Phase.VOTING.value,
                "new_phase": game.phase,
                "round_number": game.round_number,
                "deadline": game.phase_deadline.isoformat() if game.phase_deadline else None,
            },
        )
    )

    cm = CheckpointManager(session)
    saved = await cm.save_checkpoint(
        session_model=game,
        expected_version=expected_version,
        phase_details={"vote_result": vote_result.summary},
        outbox_events=outbox_events,
    )

    if saved.phase_deadline:
        await redis_state.schedule_deadline(
            game_id=saved.game_id,
            phase=saved.phase,
            round_number=saved.round_number,
            version=saved.version,
            deadline_epoch=saved.phase_deadline.timestamp(),
        )
    else:
        await redis_state.cancel_deadlines_for_game(saved.game_id)

    public_resp = to_public_session_response(saved, reveal_role_on_death=rules.reveal_role_on_death)
    await redis_state.set_live_session(saved.game_id, public_resp.model_dump(mode="json"))

    await repo.record_processed_event(
        event_id=envelope.event_id,
        event_type=envelope.event_type,
        game_id=envelope.game_id,
    )
    await session.commit()
    logger.info(
        f"VOTE_RESULT_READY successfully processed for game {envelope.game_id} (phase: {saved.phase})"
    )
    return True
