"""Database repository for game sessions, audit history, idempotency and outbox."""

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game_phase_history import GamePhaseHistoryModel
from app.models.game_sessions import GameSessionModel
from app.models.outbox import OutboxEventModel
from app.models.processed_events import ProcessedEventModel
from app.state.checkpoints import ConcurrencyConflictError


class GameRepository:
    """Repository handling durable persistence in PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(
        self,
        game: GameSessionModel,
        outbox_events: list[OutboxEventModel] | None = None,
    ) -> GameSessionModel:
        """Persist a new game session and initial outbox events atomically."""
        self.session.add(game)
        if outbox_events:
            for ev in outbox_events:
                self.session.add(ev)
        await self.session.flush()
        return game

    async def get_session(
        self,
        game_id: str,
        for_update: bool = False,
    ) -> GameSessionModel | None:
        """Fetch a game session by ID, optionally acquiring row-level lock."""
        stmt = select(GameSessionModel).where(GameSessionModel.game_id == game_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_sessions(self) -> list[GameSessionModel]:
        """Fetch all sessions that are not GAME_OVER."""
        stmt = select(GameSessionModel).where(GameSessionModel.phase != "GAME_OVER")
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_session(
        self,
        game: GameSessionModel,
        expected_version: int,
        phase_details: dict[str, Any] | None = None,
        outbox_events: list[OutboxEventModel] | None = None,
    ) -> GameSessionModel:
        """Update game session with optimistic concurrency control.

        Raises ConcurrencyConflictError if expected_version does not match current version.
        """
        new_version = expected_version + 1
        stmt = (
            update(GameSessionModel)
            .where(
                GameSessionModel.game_id == game.game_id,
                GameSessionModel.version == expected_version,
            )
            .values(
                version=new_version,
                phase=game.phase,
                round_number=game.round_number,
                phase_deadline=game.phase_deadline,
                players=list(game.players) if game.players else [],
                role_assignments=dict(game.role_assignments) if game.role_assignments else {},
                winner=game.winner,
            )
            .execution_options(synchronize_session=False)
        )
        res = cast(CursorResult[Any], await self.session.execute(stmt))
        if res.rowcount == 0:
            actual = await self.session.scalar(
                select(GameSessionModel.version).where(GameSessionModel.game_id == game.game_id)
            )
            if actual is None:
                raise ValueError(f"Game session {game.game_id} does not exist")
            raise ConcurrencyConflictError(
                game_id=game.game_id,
                expected_version=expected_version,
                actual_version=actual,
            )

        game.version = new_version
        existing = game

        if phase_details is not None:
            history = GamePhaseHistoryModel(
                game_id=existing.game_id,
                round_number=existing.round_number,
                phase=existing.phase,
                details=phase_details,
            )
            self.session.add(history)

        if outbox_events:
            for ev in outbox_events:
                self.session.add(ev)

        await self.session.flush()
        return existing

    async def get_phase_history(self, game_id: str) -> list[GamePhaseHistoryModel]:
        """Fetch ordered phase history for a game."""
        stmt = (
            select(GamePhaseHistoryModel)
            .where(GamePhaseHistoryModel.game_id == game_id)
            .order_by(GamePhaseHistoryModel.id.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def is_event_processed(self, event_id: str) -> bool:
        """Check whether an event_id was already processed."""
        stmt = select(ProcessedEventModel).where(ProcessedEventModel.event_id == event_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def record_processed_event(
        self,
        event_id: str,
        event_type: str,
        game_id: str | None = None,
    ) -> ProcessedEventModel:
        """Record an event_id in idempotency store."""
        processed = ProcessedEventModel(
            event_id=event_id,
            event_type=event_type,
            game_id=game_id,
        )
        self.session.add(processed)
        await self.session.flush()
        return processed

    async def add_outbox_event(self, event: OutboxEventModel) -> OutboxEventModel:
        """Stage an outbox event in same transaction."""
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_pending_outbox_events(self, limit: int = 50) -> list[OutboxEventModel]:
        """Get batch of pending outbox events."""
        stmt = (
            select(OutboxEventModel)
            .where(OutboxEventModel.status == "PENDING")
            .order_by(OutboxEventModel.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
