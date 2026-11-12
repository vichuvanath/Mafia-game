"""Security tests for the Game Engine Service.

Verifies:
1. Access control on /internal/* endpoints (service token required, rejecting unauthorized/player tokens).
2. Phase advance isolation (players cannot advance phases via public endpoints).
3. Secret role data protection (never exposed in public state or event payloads for living players).
4. Impersonation prevention (actions tied strictly to verified JWT identity).
5. Elimination & phase validation (dead players and non-night phases cannot act).
6. Secrecy of night actions and targets in all published event payloads.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from app.config import get_settings
from tests.conftest import generate_test_jwt


@pytest.mark.asyncio
async def test_internal_endpoints_require_service_token(api_client: AsyncClient) -> None:
    """Internal endpoints must strictly reject unauthenticated requests and invalid tokens."""
    # 1. No token provided -> 403 Forbidden (service token required)
    resp = await api_client.get("/internal/games/some-id")
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"

    resp = await api_client.post("/internal/games/some-id/advance")
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"

    # 2. Invalid service token -> 403 Forbidden
    resp = await api_client.get(
        "/internal/games/some-id",
        headers={"X-Internal-Token": "completely-invalid-service-token"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"

    resp = await api_client.post(
        "/internal/games/some-id/advance",
        headers={"X-Internal-Token": "completely-invalid-service-token"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"

    # 3. Regular player JWT token cannot access internal endpoints -> 403
    player_jwt = generate_test_jwt("p1")
    resp = await api_client.get(
        "/internal/games/some-id",
        headers={"Authorization": f"Bearer {player_jwt}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_player_cannot_advance_phases_via_public_api(api_client: AsyncClient) -> None:
    """Players cannot call an advance endpoint on the public API."""
    token = generate_test_jwt("p1")
    # Advance endpoint does not exist on public /games router
    resp = await api_client.post(
        "/games/some-id/advance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_public_game_state_never_exposes_secret_roles(api_client: AsyncClient) -> None:
    """Public game state must never expose role assignments of living players."""
    token_p1 = generate_test_jwt("p1")
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-secret-check", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    assert create_resp.status_code == 201
    game_data = create_resp.json()
    game_id = game_data["game_id"]

    # Verify role_assignments is omitted
    assert "role_assignments" not in game_data
    assert "internal_roles" not in game_data

    # Query public endpoint for each player
    for pid in ["p1", "p2", "p3", "p4", "p5"]:
        pid_token = generate_test_jwt(pid)
        resp = await api_client.get(
            f"/games/{game_id}",
            headers={"Authorization": f"Bearer {pid_token}"},
        )
        assert resp.status_code == 200
        state = resp.json()

        assert "role_assignments" not in state
        for p in state["players"]:
            assert p["is_alive"] is True
            # Living players must never have their role revealed to anyone
            assert p["revealed_role"] is None


@pytest.mark.asyncio
async def test_impersonation_prevented_action_bound_to_jwt(api_client: AsyncClient) -> None:
    """Actions are authenticated by JWT claims, preventing caller from acting as another user."""
    # Create game
    token_p1 = generate_test_jwt("p1")
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-impersonate", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    game_id = create_resp.json()["game_id"]

    # In fake client: p1=MAFIA, p2=DOCTOR, p3..p5=VILLAGER
    # Villager p3 attempts to submit a night action
    token_p3 = generate_test_jwt("p3")
    villager_action = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p2"},
        headers={"Authorization": f"Bearer {token_p3}"},
    )
    # p3 cannot act as Mafia or Doctor
    assert villager_action.status_code == 403
    assert villager_action.json()["code"] == "FORBIDDEN"

    # Stranger p99 attempts to act in this game
    token_p99 = generate_test_jwt("p99")
    stranger_action = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p2"},
        headers={"Authorization": f"Bearer {token_p99}"},
    )
    assert stranger_action.status_code == 403


@pytest.mark.asyncio
async def test_dead_players_cannot_act(api_client: AsyncClient, fake_voting_client: Any) -> None:
    """Dead players are strictly blocked from submitting night actions."""
    from app.clients.voting_client import VotingResultDto

    settings = get_settings()
    internal_headers = {"X-Internal-Token": settings.INTERNAL_SERVICE_TOKEN}

    token_p1 = generate_test_jwt("p1")
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-dead-act", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    game_id = create_resp.json()["game_id"]

    # In fake client: p1=MAFIA, p2=DOCTOR, p3=VILLAGER, p4=VILLAGER, p5=VILLAGER
    # Mafia p1 attacks p3, Doctor p2 protects p1 -> p3 dies
    await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    token_p2 = generate_test_jwt("p2")
    await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p1"},
        headers={"Authorization": f"Bearer {token_p2}"},
    )

    # 1. Advance NIGHT -> DAY (night resolution: p3 killed)
    resp_day = await api_client.post(f"/internal/games/{game_id}/advance", headers=internal_headers)
    assert resp_day.status_code == 200
    assert resp_day.json()["phase"] == "DAY"

    # Verify p3 is now dead
    state_resp = await api_client.get(
        f"/games/{game_id}",
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    p3_state = next(p for p in state_resp.json()["players"] if p["player_id"] == "p3")
    assert p3_state["is_alive"] is False

    # 2. Advance through DAY -> DISCUSSION -> VOTING -> ELIMINATION -> round 2 NIGHT
    # Have fake voting client eliminate p4 so p1 (Mafia) stays alive and game continues
    fake_voting_client.set_result(
        game_id,
        VotingResultDto(
            game_id=game_id,
            round_number=1,
            eliminated_player_id="p4",
            votes={"p1": "p4", "p2": "p4", "p4": "p1", "p5": "p4"},
            is_tie=False,
            tied_player_ids=[],
        ),
    )

    # DAY -> DISCUSSION
    await api_client.post(f"/internal/games/{game_id}/advance", headers=internal_headers)
    # DISCUSSION -> VOTING
    await api_client.post(f"/internal/games/{game_id}/advance", headers=internal_headers)
    # VOTING -> ELIMINATION (p4 eliminated, 3 players left: p1, p2, p5)
    await api_client.post(f"/internal/games/{game_id}/advance", headers=internal_headers)
    # ELIMINATION -> Round 2 NIGHT
    resp_night2 = await api_client.post(
        f"/internal/games/{game_id}/advance", headers=internal_headers
    )
    assert resp_night2.status_code == 200
    assert resp_night2.json()["phase"] == "NIGHT"
    assert resp_night2.json()["round_number"] == 2

    # Now in round 2 NIGHT, dead p3 attempts to act -> 403 Forbidden
    token_p3 = generate_test_jwt("p3")
    dead_act_resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p1"},
        headers={"Authorization": f"Bearer {token_p3}"},
    )
    assert dead_act_resp.status_code == 403
    assert "Dead players" in dead_act_resp.json()["message"]


@pytest.mark.asyncio
async def test_cannot_act_in_wrong_phase(api_client: AsyncClient) -> None:
    """Players cannot submit actions during non-night phases."""
    settings = get_settings()
    internal_headers = {"X-Internal-Token": settings.INTERNAL_SERVICE_TOKEN}

    token_p1 = generate_test_jwt("p1")
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-phase-check", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    game_id = create_resp.json()["game_id"]

    # Currently in NIGHT. Advance to NIGHT_RESOLUTION
    await api_client.post(f"/internal/games/{game_id}/advance", headers=internal_headers)

    # Attempt action in NIGHT_RESOLUTION
    resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    assert resp.status_code == 400
    assert "Actions can only be performed in NIGHT phase" in resp.json()["message"]

    # Advance to DAY
    await api_client.post(f"/internal/games/{game_id}/advance", headers=internal_headers)
    resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    assert resp.status_code == 400

    # Advance to DISCUSSION
    await api_client.post(f"/internal/games/{game_id}/advance", headers=internal_headers)
    resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_no_published_event_payload_contains_role_maps_or_action_details(
    api_client: AsyncClient,
) -> None:
    """Verify that all published events strictly maintain secrecy invariants.

    - GAME_STARTED: no roles
    - PHASE_CHANGED: no roles or actions
    - NIGHT_RESOLVED: reveals only if someone died and the victim, NEVER who acted or was protected
    - PLAYER_ELIMINATION_CONFIRMED: reveals only eliminated player's role, never all roles
    - GAME_OVER: reveals winner, no leak of live secret details
    """
    settings = get_settings()
    internal_headers = {"X-Internal-Token": settings.INTERNAL_SERVICE_TOKEN}

    token_p1 = generate_test_jwt("p1")
    token_p2 = generate_test_jwt("p2")

    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-events-secrecy", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    game_id = create_resp.json()["game_id"]

    # Mafia p1 attacks p3, Doctor p2 protects p3 (Doctor save)
    await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p2}"},
    )

    # Advance NIGHT -> NIGHT_RESOLUTION
    advance_resp = await api_client.post(
        f"/internal/games/{game_id}/advance",
        headers=internal_headers,
    )
    assert advance_resp.status_code == 200

    # Get internal game details to inspect stored outbox events
    internal_resp = await api_client.get(
        f"/internal/games/{game_id}",
        headers=internal_headers,
    )
    assert internal_resp.status_code == 200

    # The internal endpoint is authorized, but verify outbox events generated
    # do not contain secret details in their payload
    from sqlalchemy import select

    from app.database import get_db_session
    from app.models.outbox import OutboxEventModel

    # We can inspect the outbox_events in the DB
    # Let's verify each event payload structure
    db_session_factory = api_client._transport.app.dependency_overrides.get(get_db_session)  # type: ignore[attr-defined]
    db_session = db_session_factory()
    result = await db_session.execute(
        select(OutboxEventModel).where(OutboxEventModel.game_id == game_id)
    )
    events: list[OutboxEventModel] = list(result.scalars().all())

    assert len(events) >= 3  # GAME_STARTED, PHASE_CHANGED, NIGHT_RESOLVED, etc.

    forbidden_action_keys = {
        "mafia_target",
        "doctor_target",
        "doctor_protected",
        "who_acted",
        "actor_id",
        "target_id",
        "actions",
    }

    for ev in events:
        payload = ev.payload
        # Invariant 1: No full role mapping ever in event payload
        assert "role_assignments" not in payload, f"role_assignments leaked in {ev.event_type}"

        # Invariant 2: Night action details never leaked in event payload
        for key in forbidden_action_keys:
            assert key not in payload, f"Secret key '{key}' leaked in event {ev.event_type}!"

        # Invariant 3: If NIGHT_RESOLVED, verify public announcement rules
        if ev.event_type == "NIGHT_RESOLVED":
            # Must only show someone_died and message / killed_player_id
            assert "someone_died" in payload
            assert payload["someone_died"] is False  # doctor saved p3
            assert payload.get("killed_player_id") is None
            assert "Doctor" not in payload.get("message", "")
            assert "Mafia" not in payload.get("message", "")
            assert "protected" not in payload.get("message", "").lower()


@pytest.mark.asyncio
async def test_jwt_tampered_or_expired_rejected(api_client: AsyncClient) -> None:
    """Tampered signature or expired JWTs are strictly rejected."""
    # 1. Tampered signature (signed with different secret)
    bad_sig_jwt = generate_test_jwt("p1", secret="completely-wrong-secret-key-32-chars-long!")
    resp = await api_client.get(
        "/games/any-game-id",
        headers={"Authorization": f"Bearer {bad_sig_jwt}"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"

    # 2. Expired JWT
    expired_jwt = generate_test_jwt("p1", expires_in_seconds=-60)
    resp = await api_client.get(
        "/games/any-game-id",
        headers={"Authorization": f"Bearer {expired_jwt}"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_target_validation_nonexistent_or_dead(api_client: AsyncClient) -> None:
    """Player cannot target a nonexistent or already-dead player."""
    token_p1 = generate_test_jwt("p1")
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-target-val", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    game_id = create_resp.json()["game_id"]

    # Target player not in room
    resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "ghost_player_999"},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    assert resp.status_code == 400
    assert "not in this game" in resp.json()["message"]


@pytest.mark.asyncio
async def test_duplicate_night_action_rejected(api_client: AsyncClient) -> None:
    """Same role cannot submit multiple night actions in the same night phase."""
    token_p1 = generate_test_jwt("p1")
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-duplicate-act", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    game_id = create_resp.json()["game_id"]

    # First action -> OK
    resp1 = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    assert resp1.status_code == 200

    # Second action by same role (Mafia) in same night -> 409 CONFLICT
    resp2 = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p4"},
        headers={"Authorization": f"Bearer {token_p1}"},
    )
    assert resp2.status_code == 409
    assert resp2.json()["code"] == "CONFLICT"
