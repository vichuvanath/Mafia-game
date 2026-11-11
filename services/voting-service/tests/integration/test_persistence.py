"""Integration tests for persistence layer, database models, and repository."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.tally import TallyResult
from app.core.tie_rules import VoteOutcome
from app.models.base import Base
from app.models.outbox import OutboxMessage
from app.models.vote_revisions import VoteRevision
from app.models.votes import Vote
from app.repositories.voting_repository import VotingRepository


@pytest.fixture
async def db_session() -> AsyncSession:  # type: ignore[misc]
    """Create an isolated in-memory SQLite database session for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_fetch_voting_session(db_session: AsyncSession) -> None:
    repo = VotingRepository(db_session)
    deadline = datetime.now(UTC) + timedelta(minutes=5)

    session = await repo.create_session(
        game_id="game-101",
        voting_round_id="round-alpha",
        round_number=1,
        eligible_voters=["p1", "p2", "p3"],
        eligible_targets=["p1", "p2", "p3"],
        deadline=deadline,
        tie_rule="NO_ELIMINATION",
        allow_vote_change=True,
        reveal_ballots_after_close=False,
    )
    await db_session.commit()

    assert session.status == "OPEN"
    assert session.version == 1

    fetched = await repo.get_session_by_round_id("round-alpha")
    assert fetched is not None
    assert fetched.game_id == "game-101"
    assert fetched.eligible_voters == ["p1", "p2", "p3"]

    open_sess = await repo.get_open_session_by_game_id("game-101")
    assert open_sess is not None
    assert open_sess.voting_round_id == "round-alpha"


@pytest.mark.asyncio
async def test_cast_vote_and_enforce_unique_constraint(db_session: AsyncSession) -> None:
    repo = VotingRepository(db_session)
    await repo.create_session(
        game_id="game-101",
        voting_round_id="round-unique",
        round_number=1,
        eligible_voters=["voter-1", "voter-2"],
        eligible_targets=["voter-1", "voter-2"],
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        tie_rule="NO_ELIMINATION",
    )

    vote, is_revision = await repo.cast_or_update_vote(
        voting_round_id="round-unique",
        game_id="game-101",
        voter_id="voter-1",
        target_id="voter-2",
    )
    await db_session.commit()

    assert is_revision is False
    assert vote.voter_id == "voter-1"
    assert vote.target_id == "voter-2"

    # Inserting duplicate (voting_round_id, voter_id) must trigger IntegrityError
    duplicate_vote = Vote(
        voting_round_id="round-unique",
        game_id="game-101",
        voter_id="voter-1",
        target_id="voter-2",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(duplicate_vote)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_vote_revision_audit_trail(db_session: AsyncSession) -> None:
    repo = VotingRepository(db_session)
    await repo.create_session(
        game_id="game-102",
        voting_round_id="round-rev",
        round_number=1,
        eligible_voters=["voter-1", "voter-2", "voter-3"],
        eligible_targets=["voter-1", "voter-2", "voter-3"],
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        tie_rule="NO_ELIMINATION",
        allow_vote_change=True,
    )

    # Initial vote
    await repo.cast_or_update_vote(
        voting_round_id="round-rev",
        game_id="game-102",
        voter_id="voter-1",
        target_id="voter-2",
        allow_vote_change=True,
    )
    await db_session.commit()

    # Change vote to voter-3
    updated_vote, is_revision = await repo.cast_or_update_vote(
        voting_round_id="round-rev",
        game_id="game-102",
        voter_id="voter-1",
        target_id="voter-3",
        allow_vote_change=True,
    )
    await db_session.commit()

    assert is_revision is True
    assert updated_vote.target_id == "voter-3"

    # Verify revision record created in vote_revisions
    revisions = (
        (
            await db_session.execute(
                select(VoteRevision).where(VoteRevision.voting_round_id == "round-rev")
            )
        )
        .scalars()
        .all()
    )
    assert len(revisions) == 1
    assert revisions[0].previous_target_id == "voter-2"
    assert revisions[0].new_target_id == "voter-3"
    assert revisions[0].voter_id == "voter-1"


@pytest.mark.asyncio
async def test_close_session_and_tally_persistence(db_session: AsyncSession) -> None:
    repo = VotingRepository(db_session)
    session = await repo.create_session(
        game_id="game-103",
        voting_round_id="round-close",
        round_number=2,
        eligible_voters=["v1", "v2", "v3"],
        eligible_targets=["v1", "v2", "v3"],
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        tie_rule="NO_ELIMINATION",
    )
    await db_session.commit()

    tally = TallyResult(
        outcome=VoteOutcome.ELIMINATE,
        target_id="v2",
        tied_player_ids=[],
        tally={"v2": 2, "v3": 1},
        abstentions=0,
        votes_cast=3,
        votes_expected=3,
    )

    is_closed = await repo.close_session(
        voting_session=session,
        reason="ALL_VOTED",
        tally=tally,
    )
    await db_session.commit()

    assert is_closed is True
    assert session.status == "CLOSED"
    assert session.close_reason == "ALL_VOTED"
    assert session.outcome == "ELIMINATE"
    assert session.eliminated_target_id == "v2"
    assert session.version == 2
    assert session.closed_at is not None


@pytest.mark.asyncio
async def test_outbox_pattern_lifecycle(db_session: AsyncSession) -> None:
    repo = VotingRepository(db_session)
    now = datetime.now(UTC)

    msg = await repo.add_outbox_message(
        event_id="evt-1001",
        event_type="VOTE_SUBMITTED",
        game_id="game-104",
        version=1,
        occurred_at=now,
        payload={"votes_cast": 1, "votes_expected": 4},
    )
    await db_session.commit()

    assert msg.status == "PENDING"
    assert msg.event_id == "evt-1001"

    pending = await repo.get_pending_outbox_messages(limit=10)
    assert len(pending) == 1
    assert pending[0].event_id == "evt-1001"

    await repo.mark_outbox_published("evt-1001")
    await db_session.commit()

    refreshed = (
        await db_session.execute(select(OutboxMessage).where(OutboxMessage.event_id == "evt-1001"))
    ).scalar_one()
    assert refreshed.status == "PUBLISHED"
    assert refreshed.published_at is not None


@pytest.mark.asyncio
async def test_idempotency_processed_events(db_session: AsyncSession) -> None:
    repo = VotingRepository(db_session)

    assert await repo.is_event_processed("cmd-open-round-1") is False

    await repo.record_processed_event("cmd-open-round-1", "OPEN_VOTING")
    await db_session.commit()

    assert await repo.is_event_processed("cmd-open-round-1") is True
