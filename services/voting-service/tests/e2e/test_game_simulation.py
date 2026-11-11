"""End-to-end simulation tests acting as a fake Game Engine across 5 to 12 simulated players."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.main import app
from app.models.base import Base
from app.models.outbox import OutboxMessage
from app.repositories.database import get_db
from app.security.jwt import create_access_token

SERVICE_TOKEN = "dev-secret-internal-service-token-change-in-production"
HEADERS_INTERNAL = {"X-Service-Token": SERVICE_TOKEN}


@pytest.fixture
async def sim_db() -> AsyncSession:  # type: ignore[misc]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def sim_client(sim_db: AsyncSession) -> AsyncClient:  # type: ignore[misc]
    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        yield sim_db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_sim_case_1_clear_elimination(sim_client: AsyncClient, sim_db: AsyncSession) -> None:
    """Simulation 1: 7 players; majority vote targets Player 2 -> outcome ELIMINATE."""
    game_id = "sim-game-001"
    round_id = "sim-round-clear"
    players = [f"player-{i}" for i in range(1, 8)]

    # 1. Fake Game Engine opens voting round
    open_res = await sim_client.post(
        f"/internal/games/{game_id}/voting/open",
        json={
            "round_number": 1,
            "voting_round_id": round_id,
            "eligible_voters": players,
            "eligible_targets": players,
            "deadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "tie_rule": "NO_ELIMINATION",
        },
        headers=HEADERS_INTERNAL,
    )
    assert open_res.status_code == 200

    # 2. Players cast ballots: 4 vote for player-2, 1 votes for player-3, 2 abstain
    target_leader = "player-2"
    for voter in players[2:6]:  # player-3, 4, 5, 6 vote for player-2
        token = create_access_token(user_id=voter)
        res = await sim_client.post(
            f"/games/{game_id}/votes",
            json={"target_id": target_leader},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200

    # Player 1 votes for player-3
    p1_token = create_access_token(user_id=players[0])
    await sim_client.post(
        f"/games/{game_id}/votes",
        json={"target_id": "player-3"},
        headers={"Authorization": f"Bearer {p1_token}"},
    )

    # 3. Game Engine closes round
    close_res = await sim_client.post(
        f"/internal/games/{game_id}/voting/close",
        json={"voting_round_id": round_id},
        headers=HEADERS_INTERNAL,
    )
    assert close_res.status_code == 200
    data = close_res.json()
    assert data["outcome"] == "ELIMINATE"
    assert data["eliminated_target_id"] == target_leader
    assert data["abstentions"] == 2

    # Verify published VOTE_RESULT_READY outbox event
    outbox = (
        (
            await sim_db.execute(
                select(OutboxMessage).where(
                    OutboxMessage.game_id == game_id,
                    OutboxMessage.event_type == "VOTE_RESULT_READY",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(outbox) == 1
    payload = outbox[0].payload
    assert payload["outcome"] == "ELIMINATE"
    assert payload["target_id"] == target_leader
    assert payload["tally"][target_leader] == 4
    assert payload["abstentions"] == 2


@pytest.mark.asyncio
async def test_sim_case_2_tie_with_no_elimination(
    sim_client: AsyncClient, sim_db: AsyncSession
) -> None:
    """Simulation 2: 6 players; two-way tie with tie_rule=NO_ELIMINATION
    results in outcome NO_ELIMINATION.
    """
    game_id = "sim-game-002"
    round_id = "sim-round-tie-no-elim"
    players = [f"p-{i}" for i in range(1, 7)]

    await sim_client.post(
        f"/internal/games/{game_id}/voting/open",
        json={
            "round_number": 1,
            "voting_round_id": round_id,
            "eligible_voters": players,
            "eligible_targets": players,
            "deadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "tie_rule": "NO_ELIMINATION",
        },
        headers=HEADERS_INTERNAL,
    )

    # p-1, p-2 vote for p-5
    for voter in [players[0], players[1]]:
        token = create_access_token(user_id=voter)
        await sim_client.post(
            f"/games/{game_id}/votes",
            json={"target_id": "p-5"},
            headers={"Authorization": f"Bearer {token}"},
        )

    # p-3, p-4 vote for p-6 (2 votes each, 2 abstain)
    for voter in [players[2], players[3]]:
        token = create_access_token(user_id=voter)
        await sim_client.post(
            f"/games/{game_id}/votes",
            json={"target_id": "p-6"},
            headers={"Authorization": f"Bearer {token}"},
        )

    close_res = await sim_client.post(
        f"/internal/games/{game_id}/voting/close",
        json={"voting_round_id": round_id},
        headers=HEADERS_INTERNAL,
    )
    data = close_res.json()
    assert data["outcome"] == "NO_ELIMINATION"
    assert data["eliminated_target_id"] is None
    assert data["tied_player_ids"] == ["p-5", "p-6"]

    outbox = (
        (
            await sim_db.execute(
                select(OutboxMessage).where(
                    OutboxMessage.game_id == game_id,
                    OutboxMessage.event_type == "VOTE_RESULT_READY",
                )
            )
        )
        .scalars()
        .all()
    )
    assert outbox[0].payload["outcome"] == "NO_ELIMINATION"
    assert outbox[0].payload["tied_player_ids"] == ["p-5", "p-6"]


@pytest.mark.asyncio
async def test_sim_case_3_tie_with_revote(sim_client: AsyncClient, sim_db: AsyncSession) -> None:
    """Simulation 3: 6 voters; two-way tie with tie_rule=REVOTE_AMONG_TIED -> outcome TIE."""
    game_id = "sim-game-003"
    round_id = "sim-round-revote"
    candidates = ["cand-A", "cand-B"]
    voters = [f"voter-{i}" for i in range(1, 7)]

    await sim_client.post(
        f"/internal/games/{game_id}/voting/open",
        json={
            "round_number": 1,
            "voting_round_id": round_id,
            "eligible_voters": voters,
            "eligible_targets": candidates,
            "deadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "tie_rule": "REVOTE_AMONG_TIED",
        },
        headers=HEADERS_INTERNAL,
    )

    # 3 vote for cand-A, 3 vote for cand-B
    for voter in voters[:3]:
        token = create_access_token(user_id=voter)
        await sim_client.post(
            f"/games/{game_id}/votes",
            json={"target_id": "cand-A"},
            headers={"Authorization": f"Bearer {token}"},
        )
    for voter in voters[3:]:
        token = create_access_token(user_id=voter)
        await sim_client.post(
            f"/games/{game_id}/votes",
            json={"target_id": "cand-B"},
            headers={"Authorization": f"Bearer {token}"},
        )

    outbox = (
        (
            await sim_db.execute(
                select(OutboxMessage).where(
                    OutboxMessage.game_id == game_id,
                    OutboxMessage.event_type == "VOTE_RESULT_READY",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(outbox) == 1
    assert outbox[0].payload["outcome"] == "TIE"
    assert outbox[0].payload["tied_player_ids"] == ["cand-A", "cand-B"]


@pytest.mark.asyncio
async def test_sim_case_4_all_abstain(sim_client: AsyncClient, sim_db: AsyncSession) -> None:
    """Simulation 4: 5 players, 0 votes cast -> outcome NO_ELIMINATION, abstentions=5."""
    game_id = "sim-game-004"
    round_id = "sim-round-abstain"
    players = [f"p-{i}" for i in range(1, 6)]

    await sim_client.post(
        f"/internal/games/{game_id}/voting/open",
        json={
            "round_number": 2,
            "voting_round_id": round_id,
            "eligible_voters": players,
            "eligible_targets": players,
            "deadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "tie_rule": "NO_ELIMINATION",
        },
        headers=HEADERS_INTERNAL,
    )

    # Engine closes without any player votes
    close_res = await sim_client.post(
        f"/internal/games/{game_id}/voting/close",
        json={"voting_round_id": round_id},
        headers=HEADERS_INTERNAL,
    )
    data = close_res.json()
    assert data["outcome"] == "NO_ELIMINATION"
    assert data["eliminated_target_id"] is None
    assert data["abstentions"] == 5

    outbox = (
        (
            await sim_db.execute(
                select(OutboxMessage).where(
                    OutboxMessage.game_id == game_id,
                    OutboxMessage.event_type == "VOTE_RESULT_READY",
                )
            )
        )
        .scalars()
        .all()
    )
    assert outbox[0].payload["outcome"] == "NO_ELIMINATION"
    assert outbox[0].payload["abstentions"] == 5


@pytest.mark.asyncio
async def test_sim_case_5_early_close_on_all_voted(
    sim_client: AsyncClient, sim_db: AsyncSession
) -> None:
    """Simulation 5: 5 players; as soon as the 5th player votes, round auto-closes."""
    game_id = "sim-game-005"
    round_id = "sim-round-early-close"
    voters = [f"sim-voter-{i}" for i in range(1, 6)]
    target = "sim-target-prime"

    await sim_client.post(
        f"/internal/games/{game_id}/voting/open",
        json={
            "round_number": 1,
            "voting_round_id": round_id,
            "eligible_voters": voters,
            "eligible_targets": [target],
            "deadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "tie_rule": "NO_ELIMINATION",
        },
        headers=HEADERS_INTERNAL,
    )

    # Cast 4 votes (round remains OPEN)
    for voter in voters[:4]:
        token = create_access_token(user_id=voter)
        res = await sim_client.post(
            f"/games/{game_id}/votes",
            json={"target_id": target},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200

    # 5th vote: final eligible player votes
    token5 = create_access_token(user_id=voters[4])
    res5 = await sim_client.post(
        f"/games/{game_id}/votes",
        json={"target_id": target},
        headers={"Authorization": f"Bearer {token5}"},
    )
    assert res5.status_code == 200

    # Verify session automatically closed with reason "ALL_VOTED"
    outbox_close = (
        (
            await sim_db.execute(
                select(OutboxMessage).where(
                    OutboxMessage.game_id == game_id,
                    OutboxMessage.event_type == "VOTING_CLOSED",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(outbox_close) == 1
    assert outbox_close[0].payload["reason"] == "ALL_VOTED"

    outbox_result = (
        (
            await sim_db.execute(
                select(OutboxMessage).where(
                    OutboxMessage.game_id == game_id,
                    OutboxMessage.event_type == "VOTE_RESULT_READY",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(outbox_result) == 1
    assert outbox_result[0].payload["outcome"] == "ELIMINATE"
    assert outbox_result[0].payload["target_id"] == target
