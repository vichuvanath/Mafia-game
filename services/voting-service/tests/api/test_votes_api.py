"""API tests for player voting and internal Game Engine management endpoints."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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

GAME_ID = "game-test-001"
ROUND_ID = "round-uuid-001"
VOTER_1 = "voter-001"
VOTER_2 = "voter-002"
VOTER_3 = "voter-003"
OUTSIDER = "outsider-004"

SERVICE_TOKEN = "dev-secret-internal-service-token-change-in-production"


@pytest.fixture
async def test_db() -> AsyncSession:  # type: ignore[misc]
    """In-memory database session fixture overriding FastAPI get_db dependency."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def mock_redis() -> MagicMock:
    """Mock Redis client permitting distributed locks and rate limits without Redis server."""
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1)
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 0, 1, True])
    redis.pipeline.return_value = pipe
    return redis


@pytest.fixture
async def client(test_db: AsyncSession, mock_redis: MagicMock) -> AsyncClient:  # type: ignore[misc]
    """AsyncClient wired with in-memory DB and mocked Redis."""

    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.repositories.redis_client.get_redis_client", return_value=mock_redis):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_internal_endpoints_service_token_auth(client: AsyncClient) -> None:
    """Internal endpoints must reject requests with missing or invalid X-Service-Token."""
    payload = {
        "round_number": 1,
        "voting_round_id": ROUND_ID,
        "eligible_voters": [VOTER_1, VOTER_2],
        "eligible_targets": [VOTER_1, VOTER_2],
        "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "tie_rule": "NO_ELIMINATION",
    }

    # Missing token
    res_missing = await client.post(f"/internal/games/{GAME_ID}/voting/open", json=payload)
    assert res_missing.status_code == 401
    assert res_missing.json()["code"] == "UNAUTHORIZED"

    # Invalid token
    res_wrong = await client.post(
        f"/internal/games/{GAME_ID}/voting/open",
        json=payload,
        headers={"X-Service-Token": "bad-token"},
    )
    assert res_wrong.status_code == 401
    assert res_wrong.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_open_voting_round_idempotency_and_conflict(client: AsyncClient) -> None:
    """Opening same voting_round_id is idempotent; opening a second open round conflicts (409)."""
    headers = {"X-Service-Token": SERVICE_TOKEN}
    payload = {
        "round_number": 1,
        "voting_round_id": ROUND_ID,
        "eligible_voters": [VOTER_1, VOTER_2, VOTER_3],
        "eligible_targets": [VOTER_1, VOTER_2, VOTER_3],
        "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "tie_rule": "NO_ELIMINATION",
        "allow_vote_change": True,
    }

    # First open
    res1 = await client.post(
        f"/internal/games/{GAME_ID}/voting/open", json=payload, headers=headers
    )
    assert res1.status_code == 200
    assert res1.json()["status"] == "OPEN"

    # Idempotent replay of same voting_round_id
    res_repeat = await client.post(
        f"/internal/games/{GAME_ID}/voting/open", json=payload, headers=headers
    )
    assert res_repeat.status_code == 200
    assert res_repeat.json()["voting_round_id"] == ROUND_ID

    # Opening different round while first is OPEN returns 409
    payload_conflict = dict(payload, voting_round_id="different-round-999")
    res_conflict = await client.post(
        f"/internal/games/{GAME_ID}/voting/open",
        json=payload_conflict,
        headers=headers,
    )
    assert res_conflict.status_code == 409
    assert res_conflict.json()["code"] == "ROUND_CONFLICT"


@pytest.mark.asyncio
async def test_public_voting_jwt_required(client: AsyncClient) -> None:
    """Public endpoints reject calls without valid Bearer JWT."""
    res_no_auth = await client.post(f"/games/{GAME_ID}/votes", json={"target_id": VOTER_2})
    assert res_no_auth.status_code == 401
    assert res_no_auth.json()["code"] == "UNAUTHORIZED"

    res_status_no_auth = await client.get(f"/games/{GAME_ID}/voting/status")
    assert res_status_no_auth.status_code == 401
    assert res_status_no_auth.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_vote_submission_validation_rules(client: AsyncClient) -> None:
    """Validate player eligibility, target validity, and self-vote rejection."""
    headers_svc = {"X-Service-Token": SERVICE_TOKEN}
    payload_open = {
        "round_number": 1,
        "voting_round_id": "round-rules-test",
        "eligible_voters": [VOTER_1, VOTER_2, VOTER_3],
        "eligible_targets": [VOTER_1, VOTER_2, VOTER_3],
        "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "tie_rule": "NO_ELIMINATION",
        "allow_vote_change": False,
    }
    await client.post(
        f"/internal/games/{GAME_ID}/voting/open",
        json=payload_open,
        headers=headers_svc,
    )

    # 1. Self-vote rejection
    token_voter1 = create_access_token(user_id=VOTER_1)
    res_self = await client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": VOTER_1},
        headers={"Authorization": f"Bearer {token_voter1}"},
    )
    assert res_self.status_code == 400
    assert res_self.json()["code"] == "SELF_VOTE"

    # 2. Non-eligible voter rejection
    token_outsider = create_access_token(user_id=OUTSIDER)
    res_outsider = await client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": VOTER_2},
        headers={"Authorization": f"Bearer {token_outsider}"},
    )
    assert res_outsider.status_code == 400
    assert res_outsider.json()["code"] == "NOT_ELIGIBLE"

    # 3. Invalid target rejection
    res_bad_target = await client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": OUTSIDER},
        headers={"Authorization": f"Bearer {token_voter1}"},
    )
    assert res_bad_target.status_code == 400
    assert res_bad_target.json()["code"] == "INVALID_TARGET"

    # 4. Valid vote succeeds
    res_valid = await client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": VOTER_2},
        headers={"Authorization": f"Bearer {token_voter1}"},
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["status"] == "ACCEPTED"
    assert res_valid.json()["is_revision"] is False

    # 5. Vote change disallowed
    res_change_denied = await client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": VOTER_3},
        headers={"Authorization": f"Bearer {token_voter1}"},
    )
    assert res_change_denied.status_code == 400
    assert res_change_denied.json()["code"] == "VOTE_CHANGE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_vote_change_allowed_and_status_shape(client: AsyncClient) -> None:
    """When allow_vote_change is True, revising votes succeeds and status reports progress."""
    headers_svc = {"X-Service-Token": SERVICE_TOKEN}
    payload_open = {
        "round_number": 1,
        "voting_round_id": "round-change-test",
        "eligible_voters": [VOTER_1, VOTER_2, VOTER_3],
        "eligible_targets": [VOTER_1, VOTER_2, VOTER_3],
        "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "tie_rule": "NO_ELIMINATION",
        "allow_vote_change": True,
    }
    await client.post(
        f"/internal/games/{GAME_ID}/voting/open",
        json=payload_open,
        headers=headers_svc,
    )

    token_v1 = create_access_token(user_id=VOTER_1)
    token_v2 = create_access_token(user_id=VOTER_2)

    # Initial vote
    await client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": VOTER_2},
        headers={"Authorization": f"Bearer {token_v1}"},
    )

    # Status check by Voter 1: shows 1 of 3 voted, my_vote is VOTER_2
    res_stat1 = await client.get(
        f"/games/{GAME_ID}/voting/status",
        headers={"Authorization": f"Bearer {token_v1}"},
    )
    assert res_stat1.status_code == 200
    data1 = res_stat1.json()
    assert data1["votes_cast"] == 1
    assert data1["votes_expected"] == 3
    assert data1["my_vote"] == VOTER_2

    # Status check by Voter 2: shows 1 of 3 voted, my_vote is None
    res_stat2 = await client.get(
        f"/games/{GAME_ID}/voting/status",
        headers={"Authorization": f"Bearer {token_v2}"},
    )
    assert res_stat2.json()["my_vote"] is None

    # Voter 1 changes vote to VOTER_3
    res_change = await client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": VOTER_3},
        headers={"Authorization": f"Bearer {token_v1}"},
    )
    assert res_change.status_code == 200
    assert res_change.json()["is_revision"] is True

    # Confirm updated vote in status
    res_stat1_updated = await client.get(
        f"/games/{GAME_ID}/voting/status",
        headers={"Authorization": f"Bearer {token_v1}"},
    )
    assert res_stat1_updated.json()["my_vote"] == VOTER_3
    assert res_stat1_updated.json()["votes_cast"] == 1  # Still 1 vote total


@pytest.mark.asyncio
async def test_close_voting_and_idempotent_reclose(client: AsyncClient) -> None:
    """Closing round computes result; repeated close returns stored result without errors."""
    headers_svc = {"X-Service-Token": SERVICE_TOKEN}
    round_id = "round-close-test"
    payload_open = {
        "round_number": 1,
        "voting_round_id": round_id,
        "eligible_voters": [VOTER_1, VOTER_2, VOTER_3],
        "eligible_targets": [VOTER_1, VOTER_2, VOTER_3],
        "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "tie_rule": "NO_ELIMINATION",
    }
    await client.post(
        f"/internal/games/{GAME_ID}/voting/open",
        json=payload_open,
        headers=headers_svc,
    )

    # Voter 1 votes for Voter 2
    token_v1 = create_access_token(user_id=VOTER_1)
    await client.post(
        f"/games/{GAME_ID}/votes",
        json={"target_id": VOTER_2},
        headers={"Authorization": f"Bearer {token_v1}"},
    )

    # Engine closes voting
    res_close = await client.post(
        f"/internal/games/{GAME_ID}/voting/close",
        json={"voting_round_id": round_id},
        headers=headers_svc,
    )
    assert res_close.status_code == 200
    close_data = res_close.json()
    assert close_data["status"] == "CLOSED"
    assert close_data["outcome"] == "ELIMINATE"
    assert close_data["eliminated_target_id"] == VOTER_2
    assert close_data["abstentions"] == 2

    # Idempotent re-close returns same result
    res_reclose = await client.post(
        f"/internal/games/{GAME_ID}/voting/close",
        json={"voting_round_id": round_id},
        headers=headers_svc,
    )
    assert res_reclose.status_code == 200
    assert res_reclose.json()["status"] == "CLOSED"
    assert res_reclose.json()["eliminated_target_id"] == VOTER_2
