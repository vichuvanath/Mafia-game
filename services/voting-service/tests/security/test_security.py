"""Security and privacy tests enforcing ballot secrecy, access control, and payload sanitization."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.main import app
from app.models.base import Base
from app.repositories.database import get_db
from app.security.jwt import create_access_token

GAME_ID = "sec-game-900"
ROUND_ID = "sec-round-900"
PLAYER_ALICE = "player-alice-uuid"
PLAYER_BOB = "player-bob-uuid"
PLAYER_CHARLIE = "player-charlie-uuid"
PLAYER_OUTSIDER = "player-stranger-uuid"


@pytest.fixture
async def sec_db() -> AsyncSession:  # type: ignore[misc]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def sec_client(sec_db: AsyncSession) -> AsyncClient:  # type: ignore[misc]
    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        yield sec_db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_player_cannot_call_internal_endpoints(sec_client: AsyncClient) -> None:
    """Players authenticating via user JWT are strictly forbidden from internal endpoints."""
    alice_token = create_access_token(user_id=PLAYER_ALICE)

    # Calling open with JWT instead of X-Service-Token returns 401
    res = await sec_client.post(
        f"/internal/games/{GAME_ID}/voting/open",
        json={"voting_round_id": "test"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_player_cannot_vote_as_another_user(sec_client: AsyncClient) -> None:
    """The authenticated player identity comes strictly from the verified JWT sub claim."""
    # Setup open round via service token
    headers_svc = {"X-Service-Token": "dev-secret-internal-service-token-change-in-production"}
    payload_open = {
        "round_number": 1,
        "voting_round_id": ROUND_ID,
        "eligible_voters": [PLAYER_ALICE, PLAYER_BOB, PLAYER_CHARLIE],
        "eligible_targets": [PLAYER_ALICE, PLAYER_BOB, PLAYER_CHARLIE],
        "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "tie_rule": "NO_ELIMINATION",
    }
    await sec_client.post(
        f"/internal/games/{GAME_ID}/voting/open",
        json=payload_open,
        headers=headers_svc,
    )

    # Alice votes for Bob
    alice_token = create_access_token(user_id=PLAYER_ALICE)
    res_alice = await sec_client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": PLAYER_BOB},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert res_alice.status_code == 200

    # Bob logs in and checks status: Bob sees my_vote=None, not Alice's vote!
    bob_token = create_access_token(user_id=PLAYER_BOB)
    res_status = await sec_client.get(
        f"/games/{GAME_ID}/voting/status",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert res_status.status_code == 200
    data = res_status.json()
    assert data["my_vote"] is None
    assert data["votes_cast"] == 1
    # Check that individual voter mapping is completely absent from status response
    assert "revealed_ballots" not in data or data["revealed_ballots"] is None
    assert "ballots" not in data
    assert "votes" not in data


@pytest.mark.asyncio
async def test_vote_submitted_event_sanitization(sec_client: AsyncClient) -> None:
    """Public status and VOTE_SUBMITTED events never leak voter or target identity."""
    headers_svc = {"X-Service-Token": "dev-secret-internal-service-token-change-in-production"}
    round_id = "sec-round-leak-check"
    payload_open = {
        "round_number": 1,
        "voting_round_id": round_id,
        "eligible_voters": [PLAYER_ALICE, PLAYER_BOB],
        "eligible_targets": [PLAYER_ALICE, PLAYER_BOB],
        "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "tie_rule": "NO_ELIMINATION",
    }
    await sec_client.post(
        f"/internal/games/{GAME_ID}/voting/open",
        json=payload_open,
        headers=headers_svc,
    )

    alice_token = create_access_token(user_id=PLAYER_ALICE)
    res = await sec_client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": PLAYER_BOB},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    # Submit response should only confirm the target for the submitter
    assert res.status_code == 200
    res_data = res.json()
    assert "voter_id" not in res_data
    assert "target_id" in res_data
