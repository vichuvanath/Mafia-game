"""Startup recovery for in-flight games."""

from __future__ import annotations

import logging
from datetime import UTC
from typing import TYPE_CHECKING, Any

from app.engine.timers import Clock, is_deadline_passed
from app.repositories.game_repository import GameRepository
from app.state.redis_state import RedisStateManager

if TYPE_CHECKING:
    from app.services.game_service import GameService

logger = logging.getLogger("game_engine.recovery")


class GameRecoveryManager:
    """Manages reloading and reconciling active games upon process startup."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def evaluate_game_recovery_action(
        self,
        game_session: dict[str, Any],
    ) -> str:
        """Determine what recovery action is needed for an active game session.

        Returns one of:
        - "EXPIRED_ADVANCE": Deadline has already passed; immediately advance phase.
        - "REARM_TIMER": Deadline is in the future; re-arm timer/scheduler.
        - "NOOP": Game is ended or has no deadline.
        """
        deadline = game_session.get("phase_deadline")
        if not deadline:
            return "NOOP"

        if is_deadline_passed(deadline, self.clock):
            return "EXPIRED_ADVANCE"

        return "REARM_TIMER"


async def recover_active_games(
    repository: GameRepository,
    redis_state: RedisStateManager,
    game_service: GameService,
    clock: Clock,
) -> dict[str, int]:
    """Reload all active game sessions from PostgreSQL and re-arm or immediately fire overdue deadlines."""
    from app.services.game_service import to_public_session_response

    active_games = await repository.get_active_sessions()
    stats = {"rearmed": 0, "advanced": 0, "skipped": 0}

    logger.info(f"Starting recovery for {len(active_games)} active game sessions...")

    for game in active_games:
        if game.phase == "GAME_OVER":
            stats["skipped"] += 1
            continue

        deadline = game.phase_deadline
        if not deadline:
            stats["skipped"] += 1
            continue

        # Ensure deadline is timezone-aware for correct comparison
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)

        if is_deadline_passed(deadline, clock):
            logger.warning(
                f"Game {game.game_id} phase {game.phase} deadline ({deadline}) passed during downtime. "
                "Immediately advancing phase."
            )
            try:
                await game_service.advance_phase(game.game_id, force=True)
                stats["advanced"] += 1
            except Exception as exc:
                logger.error(
                    f"Failed to advance overdue game {game.game_id} during recovery: {exc}"
                )
        else:
            logger.info(
                f"Game {game.game_id} phase {game.phase} deadline ({deadline}) is in future. "
                "Re-arming in Redis scheduler."
            )
            # Re-arm deadline in Redis
            await redis_state.schedule_deadline(
                game_id=game.game_id,
                phase=game.phase,
                round_number=game.round_number,
                version=game.version,
                deadline_epoch=deadline.timestamp(),
            )
            # Restore Redis live cache from durable PostgreSQL checkpoint
            public_resp = to_public_session_response(
                game, reveal_role_on_death=game_service.settings.rules.reveal_role_on_death
            )
            await redis_state.set_live_session(game.game_id, public_resp.model_dump(mode="json"))
            stats["rearmed"] += 1

    logger.info(f"Recovery complete. Stats: {stats}")
    return stats
