"""Durable PostgreSQL checkpoint management with optimistic concurrency control."""

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game_phase_history import GamePhaseHistoryModel
from app.models.game_sessions import GameSessionModel
from app.models.outbox import OutboxEventModel


class ConcurrencyConflictError(Exception):
    """Raised when an optimistic concurrency version mismatch occurs."""

    def __init__(self, game_id: str, expected_version: int, actual_version: int) -> None:
        super().__init__(
            f"Concurrency conflict for game {game_id}: expected version {expected_version}, actual {actual_version}"
        )
        self.game_id = game_id
        self.expected_version = expected_version
        self.actual_version = actual_version


class CheckpointManager:
    """Handles atomic database commits and checkpointing."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_checkpoint(
        self,
        session_model: GameSessionModel,
        expected_version: int,
        phase_details: dict[str, Any] | None = None,
        outbox_events: list[OutboxEventModel] | None = None,
    ) -> GameSessionModel:
        """Save state update with optimistic lock check and record phase history and outbox."""
        if expected_version > 0:
            new_version = expected_version + 1
            stmt = (
                update(GameSessionModel)
                .where(
                    GameSessionModel.game_id == session_model.game_id,
                    GameSessionModel.version == expected_version,
                )
                .values(
                    version=new_version,
                    phase=session_model.phase,
                    round_number=session_model.round_number,
                    phase_deadline=session_model.phase_deadline,
                    players=list(session_model.players) if session_model.players else [],
                    role_assignments=dict(session_model.role_assignments)
                    if session_model.role_assignments
                    else {},
                    winner=session_model.winner,
                )
                .execution_options(synchronize_session=False)
            )
            res = cast(CursorResult[Any], await self.session.execute(stmt))
            if res.rowcount == 0:
                actual = await self.session.scalar(
                    select(GameSessionModel.version).where(
                        GameSessionModel.game_id == session_model.game_id
                    )
                )
                raise ConcurrencyConflictError(
                    game_id=session_model.game_id,
                    expected_version=expected_version,
                    actual_version=actual if actual is not None else -1,
                )
            session_model.version = new_version
            saved_model = session_model
        else:
            session_model.version = 1
            self.session.add(session_model)
            saved_model = session_model

        if phase_details is not None:
            history = GamePhaseHistoryModel(
                game_id=saved_model.game_id,
                round_number=saved_model.round_number,
                phase=saved_model.phase,
                details=phase_details,
            )
            self.session.add(history)

        if outbox_events:
            for ev in outbox_events:
                self.session.add(ev)

        await self.session.flush()
        return saved_model
