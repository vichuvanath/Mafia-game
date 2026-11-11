"""Startup crash recovery routine for uncompleted voting rounds."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.ballot_rules import is_deadline_expired
from app.models.voting_sessions import VotingSession
from app.repositories.database import transaction_scope
from app.repositories.redis_client import distributed_lock
from app.services.scheduler import DeadlineScheduler, get_deadline_scheduler
from app.services.voting_service import VotingService

logger = logging.getLogger("voting_service.recovery")


async def run_startup_recovery(
    scheduler: DeadlineScheduler | None = None,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Execute startup reconciliation of active voting sessions after service restart.

    1. Scans for any sessions currently marked 'OPEN'.
    2. Overdue sessions (deadline <= now) are immediately tallied, closed with
       reason 'DEADLINE', and outbox events generated.
    3. Non-expired open sessions have their deadlines re-armed in the Redis scheduler.

    Returns:
        Tuple of (closed_overdue_count, rearmed_count).
    """
    active_scheduler = scheduler or get_deadline_scheduler()
    eval_time = now or datetime.now(UTC)
    closed_count = 0
    rearmed_count = 0

    logger.info("Executing startup recovery check...")

    async with transaction_scope() as session:
        # Fetch all open rounds
        stmt = select(VotingSession).where(VotingSession.status == "OPEN").with_for_update()
        result = await session.execute(stmt)
        open_sessions = list(result.scalars().all())

        for voting_session in open_sessions:
            round_id = voting_session.voting_round_id
            async with distributed_lock(f"session:{round_id}"):
                if is_deadline_expired(voting_session.deadline, now=eval_time):
                    logger.warning(
                        "Recovery: closing overdue round %s (deadline %s)",
                        round_id,
                        voting_session.deadline.isoformat(),
                    )
                    service = VotingService(session)
                    await service._close_round_internal(voting_session, reason="DEADLINE")
                    # Cancel any stale scheduler entry
                    await active_scheduler.cancel_deadline(round_id)
                    closed_count += 1
                else:
                    logger.info(
                        "Recovery: re-arming future deadline for round %s (deadline %s)",
                        round_id,
                        voting_session.deadline.isoformat(),
                    )
                    await active_scheduler.schedule_deadline(round_id, voting_session.deadline)
                    rearmed_count += 1

        await session.commit()

    logger.info(
        "Startup recovery finished: %d overdue rounds closed, %d deadlines re-armed.",
        closed_count,
        rearmed_count,
    )
    return closed_count, rearmed_count
