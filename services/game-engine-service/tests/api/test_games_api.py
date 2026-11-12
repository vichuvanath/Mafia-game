"""API tests for Game Engine endpoints."""

import pytest
from httpx import AsyncClient

from tests.conftest import generate_test_jwt


@pytest.mark.asyncio
async def test_health_and_ready(api_client: AsyncClient) -> None:
    resp = await api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    resp_ready = await api_client.get("/ready")
    assert resp_ready.status_code == 200
    assert resp_ready.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_auth_required_for_public_endpoints(api_client: AsyncClient) -> None:
    # 1. Create game without auth
    resp = await api_client.post(
        "/games", json={"room_id": "r1", "player_ids": ["p1", "p2", "p3", "p4", "p5"]}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"

    # 2. Get game without auth
    resp = await api_client.get("/games/any-id")
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"

    # 3. Submit action without auth
    resp = await api_client.post("/games/any-id/actions", json={"target_id": "p2"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_create_game_and_safe_state_shape(api_client: AsyncClient) -> None:
    token = generate_test_jwt("p1")
    resp = await api_client.post(
        "/games",
        json={
            "room_id": "room-safe",
            "player_ids": ["p1", "p2", "p3", "p4", "p5"],
            "player_names": {"p1": "Alice", "p2": "Bob"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()

    assert data["room_id"] == "room-safe"
    assert data["phase"] == "NIGHT"
    assert data["version"] == 1
    assert data["round_number"] == 1
    assert len(data["players"]) == 5

    # Safe state shape assertions:
    # 1. role_assignments MUST NOT exist in public response
    assert "role_assignments" not in data

    # 2. Living players must have revealed_role == None
    for player in data["players"]:
        assert player["is_alive"] is True
        assert player["revealed_role"] is None


@pytest.mark.asyncio
async def test_create_game_invalid_player_count(api_client: AsyncClient) -> None:
    token = generate_test_jwt("p1")
    resp = await api_client.post(
        "/games",
        json={
            "room_id": "room-small",
            "player_ids": ["p1", "p2"],  # Less than min 5
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    # Fails schema validation (min_length=3) or rule validation (<5)
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_non_member_access_forbidden(api_client: AsyncClient) -> None:
    creator_token = generate_test_jwt("p1")
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-private", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {creator_token}"},
    )
    game_id = create_resp.json()["game_id"]

    # Stranger tries to read game state
    stranger_token = generate_test_jwt("stranger_99")
    get_resp = await api_client.get(
        f"/games/{game_id}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert get_resp.status_code == 403
    assert get_resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_night_actions_validation_and_duplicate_prevention(
    api_client: AsyncClient,
) -> None:
    # p1 is Mafia, p2 is Doctor, p3, p4, p5 are Villagers (per FakeRoleServiceClient)
    token_p1_mafia = generate_test_jwt("p1")
    token_p2_doc = generate_test_jwt("p2")
    token_p3_villager = generate_test_jwt("p3")

    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-actions", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {token_p1_mafia}"},
    )
    game_id = create_resp.json()["game_id"]

    # 1. Wrong role: Villager tries to submit night action -> 403 Forbidden
    resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p2"},
        headers={"Authorization": f"Bearer {token_p3_villager}"},
    )
    assert resp.status_code == 403
    assert "role cannot submit night actions" in resp.json()["message"]

    # 2. Invalid target: Non-existent player -> 400 Bad Request
    resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "ghost_player"},
        headers={"Authorization": f"Bearer {token_p1_mafia}"},
    )
    assert resp.status_code == 400
    assert "Target player is not in this game" in resp.json()["message"]

    # 3. Valid action: Mafia targets p3 -> 200 OK
    resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p1_mafia}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACCEPTED"
    assert resp.json()["phase"] == "NIGHT"

    # 4. Duplicate action: Mafia tries to submit a second action in the same night -> 409 Conflict
    resp_dup = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p4"},
        headers={"Authorization": f"Bearer {token_p1_mafia}"},
    )
    assert resp_dup.status_code == 409
    assert "already been submitted" in resp_dup.json()["message"]

    # 5. Doctor submits action -> 200 OK
    resp_doc = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {token_p2_doc}"},
    )
    assert resp_doc.status_code == 200
    assert resp_doc.json()["status"] == "ACCEPTED"


@pytest.mark.asyncio
async def test_internal_endpoints_security_and_phase_advance(
    api_client: AsyncClient,
) -> None:
    p1_token = generate_test_jwt("p1")
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-internal", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {p1_token}"},
    )
    game_id = create_resp.json()["game_id"]

    # 1. Player cannot call internal endpoints (missing or wrong service token)
    resp = await api_client.get(
        f"/internal/games/{game_id}",
        headers={"Authorization": f"Bearer {p1_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"

    # 2. Service token allows internal read
    internal_headers = {"X-Internal-Token": "dev-secret-internal-service-token"}
    resp = await api_client.get(
        f"/internal/games/{game_id}",
        headers=internal_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["game_id"] == game_id
    assert "role_assignments" in data
    assert data["role_assignments"]["p1"] == "MAFIA"

    # 3. Advance phase via internal endpoint: NIGHT -> DAY
    advance_resp = await api_client.post(
        f"/internal/games/{game_id}/advance",
        json={"force": True},
        headers=internal_headers,
    )
    assert advance_resp.status_code == 200
    advance_data = advance_resp.json()
    assert advance_data["phase"] == "DAY"
    assert advance_data["round_number"] == 1
    assert advance_data["version"] == 2

    # 4. In DAY phase, night action must be rejected (wrong phase)
    action_resp = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {p1_token}"},
    )
    assert action_resp.status_code == 400
    assert "Actions can only be performed in NIGHT phase" in action_resp.json()["message"]


@pytest.mark.asyncio
async def test_full_phase_cycle_progression_via_api(api_client: AsyncClient) -> None:
    p1_token = generate_test_jwt("p1")
    p2_token = generate_test_jwt("p2")
    internal_headers = {"X-Internal-Token": "dev-secret-internal-service-token"}

    # 1. Create game (5 players: p1=MAFIA, p2=DOCTOR, p3=VILLAGER, p4=VILLAGER, p5=VILLAGER)
    create_resp = await api_client.post(
        "/games",
        json={"room_id": "room-cycle", "player_ids": ["p1", "p2", "p3", "p4", "p5"]},
        headers={"Authorization": f"Bearer {p1_token}"},
    )
    assert create_resp.status_code == 201
    game_id = create_resp.json()["game_id"]

    # 2. Night Actions: Mafia targets p3, Doctor targets p4
    mafia_act = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p3"},
        headers={"Authorization": f"Bearer {p1_token}"},
    )
    assert mafia_act.status_code == 200

    doc_act = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p4"},
        headers={"Authorization": f"Bearer {p2_token}"},
    )
    assert doc_act.status_code == 200

    # 3. Advance: NIGHT -> DAY (p3 killed)
    adv_day = await api_client.post(
        f"/internal/games/{game_id}/advance",
        headers=internal_headers,
    )
    assert adv_day.status_code == 200
    assert adv_day.json()["phase"] == "DAY"
    # Verify p3 is dead and has revealed_role (since reveal_role_on_death=True)
    players_map = {p["player_id"]: p for p in adv_day.json()["players"]}
    assert players_map["p3"]["is_alive"] is False
    assert players_map["p3"]["revealed_role"] == "VILLAGER"

    # Dead actor cannot act
    p3_token = generate_test_jwt("p3")
    act_dead = await api_client.post(
        f"/games/{game_id}/actions",
        json={"target_id": "p1"},
        headers={"Authorization": f"Bearer {p3_token}"},
    )
    assert act_dead.status_code == 400  # wrong phase or dead

    # 4. Advance: DAY -> DISCUSSION
    adv_disc = await api_client.post(
        f"/internal/games/{game_id}/advance",
        headers=internal_headers,
    )
    assert adv_disc.status_code == 200
    assert adv_disc.json()["phase"] == "DISCUSSION"

    # 5. Advance: DISCUSSION -> VOTING
    adv_voting = await api_client.post(
        f"/internal/games/{game_id}/advance",
        headers=internal_headers,
    )
    assert adv_voting.status_code == 200
    assert adv_voting.json()["phase"] == "VOTING"

    # 6. Advance: VOTING -> ELIMINATION (elimination occurs and checks win condition)
    adv_elim = await api_client.post(
        f"/internal/games/{game_id}/advance",
        headers=internal_headers,
    )
    assert adv_elim.status_code == 200
    elim_data = adv_elim.json()
    assert elim_data["phase"] in ("ELIMINATION", "GAME_OVER")

    # If game continued into ELIMINATION, advance to next NIGHT
    if elim_data["phase"] == "ELIMINATION":
        adv_next_night = await api_client.post(
            f"/internal/games/{game_id}/advance",
            headers=internal_headers,
        )
        assert adv_next_night.status_code == 200
        assert adv_next_night.json()["phase"] == "NIGHT"
        assert adv_next_night.json()["round_number"] == 2
