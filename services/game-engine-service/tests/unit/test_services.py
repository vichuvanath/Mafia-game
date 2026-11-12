"""Unit tests for GameService and ActionService."""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.clients.voting_client import VotingResultDto
from app.config import Settings
from app.config.rules import GameRulesSettings, Phase, Role
from app.models.game_sessions import GameSessionModel
from app.services.action_service import ActionService
from app.services.game_service import GameService
from app.state.redis_state import RedisStateManager


@pytest.mark.asyncio
async def test_game_service_creation_and_validation(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> None:
    svc = GameService(
        session=async_db_session,
        redis_state=redis_state_manager,
        role_client=fake_role_client,
        voting_client=fake_voting_client,
        settings=test_settings,
    )

    # 1. Invalid player count: too few (< 5)
    with pytest.raises(HTTPException) as exc1:
        await svc.create_game(room_id="r1", player_ids=["p1", "p2", "p3", "p4"])
    assert exc1.value.status_code == 400
    assert "Player count" in exc1.value.detail

    # 2. Invalid player count: too many (> 12)
    with pytest.raises(HTTPException) as exc2:
        await svc.create_game(room_id="r2", player_ids=[f"p{i}" for i in range(13)])
    assert exc2.value.status_code == 400

    # 3. Valid game creation
    pids = ["p1", "p2", "p3", "p4", "p5"]
    resp = await svc.create_game(room_id="r3", player_ids=pids)
    assert resp.phase == Phase.NIGHT
    assert resp.version == 1
    assert resp.round_number == 1
    assert len(resp.players) == 5

    # 4. get_public_game
    public_resp = await svc.get_public_game(resp.game_id)
    assert public_resp.game_id == resp.game_id

    # 5. get_game_or_404 non-existent
    with pytest.raises(HTTPException) as exc_404:
        await svc.get_game_or_404("non-existent-game-id")
    assert exc_404.value.status_code == 404


@pytest.mark.asyncio
async def test_game_service_role_service_failure(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> None:
    class FailingRoleClient(FakeRoleServiceClient):
        async def request_role_assignment(
            self, game_id: str, player_ids: list[str]
        ) -> dict[str, Role]:
            raise RuntimeError("Role service offline")

    svc = GameService(
        session=async_db_session,
        redis_state=redis_state_manager,
        role_client=FailingRoleClient(),
        voting_client=fake_voting_client,
        settings=test_settings,
    )
    with pytest.raises(HTTPException) as exc:
        await svc.create_game(room_id="r1", player_ids=["p1", "p2", "p3", "p4", "p5"])
    assert exc.value.status_code == 502
    assert "Role Service" in exc.value.detail


@pytest.mark.asyncio
async def test_game_service_advance_phase_all_branches(
    async_db_session: AsyncSession,
    redis_state_manager: RedisStateManager,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
    test_settings: Settings,
) -> None:
    svc = GameService(
        session=async_db_session,
        redis_state=redis_state_manager,
        role_client=fake_role_client,
        voting_client=fake_voting_client,
        settings=test_settings,
    )
    pids = ["p1", "p2", "p3", "p4", "p5"]
    created = await svc.create_game(room_id="r-branch", player_ids=pids)
    game_id = created.game_id

    # Buffer Mafia and Doctor actions
    await redis_state_manager.record_night_action(game_id, "MAFIA", "p1", "p3")
    await redis_state_manager.record_night_action(game_id, "DOCTOR", "p2", "p4")

    # NIGHT -> DAY (p3 killed)
    day_resp = await svc.advance_phase(game_id)
    assert day_resp.phase == Phase.DAY

    # DAY -> DISCUSSION
    disc_resp = await svc.advance_phase(game_id)
    assert disc_resp.phase == Phase.DISCUSSION

    # DISCUSSION -> VOTING
    voting_resp = await svc.advance_phase(game_id)
    assert voting_resp.phase == Phase.VOTING

    # Setup tie in voting service to test revote branch
    fake_voting_client.set_result(
        game_id,
        VotingResultDto(
            game_id=game_id,
            round_number=1,
            eliminated_player_id=None,
            votes={"p1": "p2", "p4": "p1"},
            is_tie=True,
            tied_player_ids=["p1", "p2"],
        ),
    )

    # VOTING -> VOTING (Revote)
    revote_resp = await svc.advance_phase(game_id)
    assert revote_resp.phase == Phase.VOTING

    # Now close revote with decisive elimination of p2 (Doctor)
    fake_voting_client.set_result(
        game_id,
        VotingResultDto(
            game_id=game_id,
            round_number=1,
            eliminated_player_id="p2",
            votes={"p1": "p2", "p4": "p2", "p5": "p2", "p2": "p1"},
            is_tie=False,
            tied_player_ids=[],
        ),
    )

    # VOTING -> ELIMINATION (p2 eliminated)
    elim_resp = await svc.advance_phase(game_id)
    assert elim_resp.phase == Phase.ELIMINATION

    # ELIMINATION -> next round NIGHT (round_number = 2)
    night2_resp = await svc.advance_phase(game_id)
    assert night2_resp.phase == Phase.NIGHT
    assert night2_resp.round_number == 2


@pytest.mark.asyncio
async def test_action_service_validations(
    redis_state_manager: RedisStateManager,
    test_settings: Settings,
) -> None:
    action_svc = ActionService(redis_state=redis_state_manager, settings=test_settings)

    # Construct test GameSessionModel
    game = GameSessionModel(
        game_id="game-action-test",
        room_id="room-act",
        phase=Phase.NIGHT.value,
        version=1,
        round_number=1,
        players=[
            {"player_id": "p1", "is_alive": True},
            {"player_id": "p2", "is_alive": True},
            {"player_id": "p3", "is_alive": False},  # Dead
            {"player_id": "p4", "is_alive": True},
        ],
        role_assignments={
            "p1": Role.MAFIA.value,
            "p2": Role.DOCTOR.value,
            "p3": Role.VILLAGER.value,
            "p4": Role.VILLAGER.value,
        },
    )

    # 1. Non-member actor -> 403
    with pytest.raises(HTTPException) as exc1:
        await action_svc.validate_and_record_action(game, "outsider", "p2")
    assert exc1.value.status_code == 403

    # 2. Dead actor -> 403
    with pytest.raises(HTTPException) as exc2:
        await action_svc.validate_and_record_action(game, "p3", "p1")
    assert exc2.value.status_code == 403

    # 3. Villager actor -> 403
    with pytest.raises(HTTPException) as exc3:
        await action_svc.validate_and_record_action(game, "p4", "p1")
    assert exc3.value.status_code == 403

    # 4. Target not in game -> 400
    with pytest.raises(HTTPException) as exc4:
        await action_svc.validate_and_record_action(game, "p1", "unknown_target")
    assert exc4.value.status_code == 400

    # 5. Target is dead -> 400
    with pytest.raises(HTTPException) as exc5:
        await action_svc.validate_and_record_action(game, "p1", "p3")
    assert exc5.value.status_code == 400

    # 6. Wrong phase -> 400
    game.phase = Phase.DAY.value
    with pytest.raises(HTTPException) as exc6:
        await action_svc.validate_and_record_action(game, "p1", "p2")
    assert exc6.value.status_code == 400
    game.phase = Phase.NIGHT.value

    # 7. Doctor self-protection disabled -> 400
    no_self_protect_settings = Settings(rules=GameRulesSettings(doctor_can_self_protect=False))
    action_svc_no_sp = ActionService(
        redis_state=redis_state_manager, settings=no_self_protect_settings
    )
    with pytest.raises(HTTPException) as exc7:
        await action_svc_no_sp.validate_and_record_action(game, "p2", "p2")
    assert exc7.value.status_code == 400
    assert "Doctor self-protection is disabled" in exc7.value.detail

    # 8. Valid action for Mafia -> records and returns Role.MAFIA
    role = await action_svc.validate_and_record_action(game, "p1", "p2")
    assert role == Role.MAFIA

    # 9. Duplicate action -> 409
    with pytest.raises(HTTPException) as exc8:
        await action_svc.validate_and_record_action(game, "p1", "p4")
    assert exc8.value.status_code == 409
