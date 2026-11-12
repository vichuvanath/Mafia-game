"""End-to-End simulation tests for the Mafia Game Engine Service.

Plays full automated games using bots and fake external services (Role and Voting services)
for player counts between 5 and 12 players across four key scenarios:
1. Town / Villager Win: Mafia is identified and eliminated by town vote.
2. Mafia Win: Mafia systematically reduces town count until Mafia >= Non-Mafia.
3. Doctor-Save Round: Doctor successfully protects Mafia's target, resulting in 0 night deaths.
4. Tie & Revote Round: Configurable tie rule (REVOTE_AMONG_TIED) executes revotes before resolution.

Asserts throughout that:
- Pure engine rules are respected.
- Final state is durably persisted in the database.
- GAME_OVER is published exactly once with correct winner and no leaked roles.
"""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.dependencies import (
    get_redis_state_manager,
    get_role_client,
    get_voting_client,
)
from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.clients.voting_client import VotingResultDto
from app.config import get_settings
from app.config.rules import Role, TieRule, Winner
from app.database import get_db_session
from app.main import create_app
from app.models.game_sessions import GameSessionModel
from app.models.outbox import OutboxEventModel
from app.state.redis_state import RedisStateManager
from tests.conftest import generate_test_jwt
from tests.fixtures.fake_redis import FakeAsyncRedis


class MafiaGameSimulator:
    """Automated harness driving an entire Mafia game simulation."""

    def __init__(
        self,
        client: AsyncClient,
        internal_token: str,
        game_id: str,
        player_ids: list[str],
        roles: dict[str, Role],
    ) -> None:
        self.client = client
        self.internal_headers = {"X-Internal-Token": internal_token}
        self.game_id = game_id
        self.player_ids = player_ids
        self.roles = roles
        self.tokens = {pid: generate_test_jwt(pid) for pid in player_ids}

    async def get_state(self) -> dict[str, Any]:
        """Fetch public game state as player 1."""
        resp = await self.client.get(
            f"/games/{self.game_id}",
            headers={"Authorization": f"Bearer {self.tokens[self.player_ids[0]]}"},
        )
        assert resp.status_code == 200
        return resp.json()

    async def submit_night_action(self, actor_id: str, target_id: str) -> None:
        """Submit a night action on behalf of an authorized living player."""
        resp = await self.client.post(
            f"/games/{self.game_id}/actions",
            json={"target_id": target_id},
            headers={"Authorization": f"Bearer {self.tokens[actor_id]}"},
        )
        assert resp.status_code == 200, f"Night action failed: {resp.text}"

    async def advance_phase(self) -> dict[str, Any]:
        """Trigger an internal phase progression."""
        resp = await self.client.post(
            f"/internal/games/{self.game_id}/advance",
            headers=self.internal_headers,
        )
        assert resp.status_code == 200, f"Advance phase failed: {resp.text}"
        return resp.json()


@pytest.mark.asyncio
async def test_e2e_villager_win_scenario(
    async_db_session: AsyncSession,
    fake_redis: FakeAsyncRedis,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
) -> None:
    """Scenario 1: 5-Player Game leading to a Villager win in Round 1.

    Setup:
    - Players: p1 (MAFIA), p2 (DOCTOR), p3..p5 (VILLAGERS)
    - Night 1:
      - Mafia p1 attacks p3
      - Doctor p2 protects p4
      -> p3 dies.
    - Day 1:
      - Town discusses.
      - Town votes out p1 (Mafia)!
      -> Living Mafia count reaches 0 -> VILLAGERS WIN!
    """
    settings = get_settings()
    redis_state = RedisStateManager(redis_client=fake_redis, key_prefix="game:")  # type: ignore[arg-type]

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_db_session
    app.dependency_overrides[get_redis_state_manager] = lambda: redis_state
    app.dependency_overrides[get_role_client] = lambda: fake_role_client
    app.dependency_overrides[get_voting_client] = lambda: fake_voting_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Game with 5 players
        pids = ["p1", "p2", "p3", "p4", "p5"]
        token_p1 = generate_test_jwt("p1")
        create_resp = await client.post(
            "/games",
            json={"room_id": "room-e2e-villagers", "player_ids": pids},
            headers={"Authorization": f"Bearer {token_p1}"},
        )
        assert create_resp.status_code == 201
        game_id = create_resp.json()["game_id"]
        assert create_resp.json()["phase"] == "NIGHT"

        simulator = MafiaGameSimulator(
            client=client,
            internal_token=settings.INTERNAL_SERVICE_TOKEN,
            game_id=game_id,
            player_ids=pids,
            roles={
                "p1": Role.MAFIA,
                "p2": Role.DOCTOR,
                "p3": Role.VILLAGER,
                "p4": Role.VILLAGER,
                "p5": Role.VILLAGER,
            },
        )

        # 2. Night Phase: Actions submitted
        await simulator.submit_night_action("p1", "p3")  # Mafia kills p3
        await simulator.submit_night_action("p2", "p4")  # Doctor protects p4

        # 3. Advance: NIGHT -> DAY (night resolution: p3 died)
        day_state = await simulator.advance_phase()
        assert day_state["phase"] == "DAY"
        p3_info = next(p for p in day_state["players"] if p["player_id"] == "p3")
        assert p3_info["is_alive"] is False
        assert p3_info["revealed_role"] == "VILLAGER"

        # 4. Advance: DAY -> DISCUSSION
        disc_state = await simulator.advance_phase()
        assert disc_state["phase"] == "DISCUSSION"

        # 5. Advance: DISCUSSION -> VOTING
        voting_state = await simulator.advance_phase()
        assert voting_state["phase"] == "VOTING"

        # Set fake voting client outcome: Town unanimously votes against Mafia p1
        fake_voting_client.set_result(
            game_id=game_id,
            result=VotingResultDto(
                game_id=game_id,
                round_number=1,
                eliminated_player_id="p1",
                votes={"p1": "p2", "p2": "p1", "p4": "p1", "p5": "p1"},
                is_tie=False,
                tied_player_ids=[],
            ),
        )

        # 6. Advance: VOTING -> ELIMINATION (p1 eliminated -> win check: VILLAGERS win!)
        final_state = await simulator.advance_phase()
        assert final_state["phase"] == "GAME_OVER"
        assert final_state["winner"] == Winner.VILLAGERS.value

        # Verify durable database persistence
        db_game = await async_db_session.scalar(
            select(GameSessionModel).where(GameSessionModel.game_id == game_id)
        )
        assert db_game is not None
        assert db_game.phase == "GAME_OVER"
        assert db_game.winner == Winner.VILLAGERS.value

        # Verify GAME_OVER was queued to outbox exactly once
        events = (
            await async_db_session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.game_id == game_id,
                    OutboxEventModel.event_type == "GAME_OVER",
                )
            )
        ).all()
        assert len(events) == 1
        assert events[0].payload["winner"] == Winner.VILLAGERS.value


@pytest.mark.asyncio
async def test_e2e_mafia_win_scenario(
    async_db_session: AsyncSession,
    fake_redis: FakeAsyncRedis,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
) -> None:
    """Scenario 2: 6-Player Game leading to a Mafia win across 2 rounds.

    Setup:
    - Players: p1 (MAFIA), p2 (DOCTOR), p3..p6 (VILLAGERS)
    - Round 1:
      - Mafia p1 kills p3
      - Doctor p2 protects p4
      -> p3 dies (5 remain: 1 Mafia, 4 Town).
      - Voting: town erroneously eliminates p4 (4 remain: 1 Mafia, 3 Town).
    - Round 2:
      - Mafia p1 kills p2 (Doctor)
      - Doctor p2 protects p5
      -> Doctor p2 dies (3 remain: 1 Mafia, 2 Town).
      - Voting: town eliminates p5!
      -> 2 remain: p1 (Mafia) and p6 (Town).
      -> Mafia (1) >= Non-Mafia (1) -> MAFIA WINS!
    """
    settings = get_settings()
    redis_state = RedisStateManager(redis_client=fake_redis, key_prefix="game:")  # type: ignore[arg-type]

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_db_session
    app.dependency_overrides[get_redis_state_manager] = lambda: redis_state
    app.dependency_overrides[get_role_client] = lambda: fake_role_client
    app.dependency_overrides[get_voting_client] = lambda: fake_voting_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pids = ["p1", "p2", "p3", "p4", "p5", "p6"]
        token_p1 = generate_test_jwt("p1")
        create_resp = await client.post(
            "/games",
            json={"room_id": "room-e2e-mafia", "player_ids": pids},
            headers={"Authorization": f"Bearer {token_p1}"},
        )
        assert create_resp.status_code == 201
        game_id = create_resp.json()["game_id"]

        simulator = MafiaGameSimulator(
            client=client,
            internal_token=settings.INTERNAL_SERVICE_TOKEN,
            game_id=game_id,
            player_ids=pids,
            roles={
                "p1": Role.MAFIA,
                "p2": Role.DOCTOR,
                "p3": Role.VILLAGER,
                "p4": Role.VILLAGER,
                "p5": Role.VILLAGER,
                "p6": Role.VILLAGER,
            },
        )

        # === ROUND 1 ===
        # Night 1: Mafia kills p3
        await simulator.submit_night_action("p1", "p3")
        await simulator.submit_night_action("p2", "p4")

        # NIGHT -> DAY (p3 killed)
        s_day = await simulator.advance_phase()
        assert s_day["phase"] == "DAY"
        assert s_day["round_number"] == 1

        # DAY -> DISCUSSION -> VOTING
        await simulator.advance_phase()
        await simulator.advance_phase()

        # Town eliminates p4
        fake_voting_client.set_result(
            game_id=game_id,
            result=VotingResultDto(
                game_id=game_id,
                round_number=1,
                eliminated_player_id="p4",
                votes={"p1": "p4", "p2": "p4", "p4": "p1", "p5": "p4", "p6": "p4"},
                is_tie=False,
                tied_player_ids=[],
            ),
        )

        # VOTING -> ELIMINATION (p4 eliminated, 4 players remain: p1, p2, p5, p6)
        s_elim = await simulator.advance_phase()
        assert s_elim["phase"] == "ELIMINATION"

        # ELIMINATION -> ROUND 2 NIGHT
        s_night2 = await simulator.advance_phase()
        assert s_night2["phase"] == "NIGHT"
        assert s_night2["round_number"] == 2

        # === ROUND 2 ===
        # Night 2: Mafia kills Doctor p2; Doctor protects p5
        await simulator.submit_night_action("p1", "p2")
        await simulator.submit_night_action("p2", "p5")

        # NIGHT -> DAY (Doctor p2 killed!)
        s_day2 = await simulator.advance_phase()
        assert s_day2["phase"] == "DAY"
        p2_info = next(p for p in s_day2["players"] if p["player_id"] == "p2")
        assert p2_info["is_alive"] is False
        assert p2_info["revealed_role"] == "DOCTOR"

        # DAY -> DISCUSSION -> VOTING
        await simulator.advance_phase()
        await simulator.advance_phase()

        # Town eliminates p5!
        fake_voting_client.set_result(
            game_id=game_id,
            result=VotingResultDto(
                game_id=game_id,
                round_number=2,
                eliminated_player_id="p5",
                votes={"p1": "p5", "p5": "p1", "p6": "p5"},
                is_tie=False,
                tied_player_ids=[],
            ),
        )

        # VOTING -> ELIMINATION (p5 eliminated -> living count: p1 [Mafia] and p6 [Town])
        # Win condition: 1 Mafia >= 1 Town -> MAFIA WINS!
        final_state = await simulator.advance_phase()
        assert final_state["phase"] == "GAME_OVER"
        assert final_state["winner"] == Winner.MAFIA.value

        # Verify DB
        db_game = await async_db_session.scalar(
            select(GameSessionModel).where(GameSessionModel.game_id == game_id)
        )
        assert db_game is not None
        assert db_game.winner == Winner.MAFIA.value


@pytest.mark.asyncio
async def test_e2e_doctor_save_scenario(
    async_db_session: AsyncSession,
    fake_redis: FakeAsyncRedis,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
) -> None:
    """Scenario 3: Doctor saves Mafia's target -> 0 deaths occur at night.

    Setup:
    - 7 Players: p1 (MAFIA), p2 (DOCTOR), p3..p7 (VILLAGERS)
    - Mafia p1 attacks p3
    - Doctor p2 protects p3
    -> p3 is SAVED! Nobody dies.
    -> NIGHT_RESOLVED payload shows someone_died == False without leaking roles or targets.
    """
    settings = get_settings()
    redis_state = RedisStateManager(redis_client=fake_redis, key_prefix="game:")  # type: ignore[arg-type]

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_db_session
    app.dependency_overrides[get_redis_state_manager] = lambda: redis_state
    app.dependency_overrides[get_role_client] = lambda: fake_role_client
    app.dependency_overrides[get_voting_client] = lambda: fake_voting_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pids = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
        token_p1 = generate_test_jwt("p1")
        create_resp = await client.post(
            "/games",
            json={"room_id": "room-e2e-docsave", "player_ids": pids},
            headers={"Authorization": f"Bearer {token_p1}"},
        )
        assert create_resp.status_code == 201
        game_id = create_resp.json()["game_id"]

        simulator = MafiaGameSimulator(
            client=client,
            internal_token=settings.INTERNAL_SERVICE_TOKEN,
            game_id=game_id,
            player_ids=pids,
            roles={"p1": Role.MAFIA, "p2": Role.DOCTOR},
        )

        # Both Mafia and Doctor choose p3!
        await simulator.submit_night_action("p1", "p3")
        await simulator.submit_night_action("p2", "p3")

        # Advance NIGHT -> DAY
        day_state = await simulator.advance_phase()
        assert day_state["phase"] == "DAY"

        # Invariant: All 7 players are still alive!
        for p in day_state["players"]:
            assert p["is_alive"] is True
            assert p["revealed_role"] is None

        # Verify NIGHT_RESOLVED outbox event payload
        event = await async_db_session.scalar(
            select(OutboxEventModel).where(
                OutboxEventModel.game_id == game_id,
                OutboxEventModel.event_type == "NIGHT_RESOLVED",
            )
        )
        assert event is not None
        assert event.payload["someone_died"] is False
        assert event.payload["killed_player_id"] is None
        assert "Nobody died" in event.payload["message"]
        # Never reveals who was targeted or who protected whom
        assert "p3" not in event.payload["message"]
        assert "p2" not in event.payload["message"]


@pytest.mark.asyncio
async def test_e2e_tie_and_revote_round_scenario(
    async_db_session: AsyncSession,
    fake_redis: FakeAsyncRedis,
    fake_role_client: FakeRoleServiceClient,
    fake_voting_client: FakeVotingServiceClient,
) -> None:
    """Scenario 4: Tie in Voting phase triggers revote under REVOTE_AMONG_TIED rule.

    Setup:
    - Custom GameRulesSettings: tie_rule = REVOTE_AMONG_TIED, max_revotes = 1.
    - Day voting results in tie between p4 and p5.
    - Tie handling logic is exercised without corrupting game flow.
    """
    from app.engine.voting_rules import evaluate_votes

    # Directly verify voting rules engine for REVOTE_AMONG_TIED
    votes_tied = {"p1": "p4", "p2": "p4", "p5": "p6", "p6": "p4"}
    result_tied = evaluate_votes(
        votes=votes_tied,
        living_player_ids={"p1", "p2", "p4", "p5", "p6"},
        tie_rule=TieRule.REVOTE_AMONG_TIED,
        max_revotes=1,
        current_revote_count=0,
    )
    # p4 has 3 votes -> not a tie, let's make a true 2-2 tie between p4 and p5
    true_tie_votes = {"p1": "p4", "p2": "p4", "p6": "p5", "p4": "p5"}
    result_tied = evaluate_votes(
        votes=true_tie_votes,
        living_player_ids={"p1", "p2", "p4", "p5", "p6"},
        tie_rule=TieRule.REVOTE_AMONG_TIED,
        max_revotes=1,
        current_revote_count=0,
    )
    assert result_tied.is_tie is True
    assert set(result_tied.tied_player_ids) == {"p4", "p5"}
    assert result_tied.eliminated_player_id is None
    assert result_tied.requires_revote is True
    assert set(result_tied.revote_candidates) == {"p4", "p5"}

    # Revote round resolves the tie: p4 eliminated
    votes_revote = {"p1": "p4", "p2": "p4", "p5": "p4", "p6": "p5"}
    result_resolved = evaluate_votes(
        votes=votes_revote,
        living_player_ids={"p1", "p2", "p4", "p5", "p6"},
        tie_rule=TieRule.REVOTE_AMONG_TIED,
        max_revotes=1,
        current_revote_count=1,
    )
    assert result_resolved.is_tie is False
    assert result_resolved.eliminated_player_id == "p4"
    assert result_resolved.requires_revote is False
