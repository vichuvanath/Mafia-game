"""In-memory fakes for external Role Service and Voting Service."""

from typing import Any

from app.clients.role_client import RoleServiceClient
from app.clients.voting_client import VotingResultDto, VotingServiceClient
from app.config.rules import Role


class FakeRoleServiceClient(RoleServiceClient):
    """In-memory fake role service assigning 1 Mafia, 1 Doctor, rest Villagers."""

    def __init__(self, predefined_roles: dict[str, Role] | None = None) -> None:
        self.predefined_roles = predefined_roles or {}
        self.requests_log: list[dict[str, list[str]]] = []

    async def request_role_assignment(
        self,
        game_id: str,
        player_ids: list[str],
    ) -> dict[str, Role]:
        self.requests_log.append({game_id: player_ids})

        if self.predefined_roles:
            return {pid: self.predefined_roles.get(pid, Role.VILLAGER) for pid in player_ids}

        # Default standard allocation: 1 Mafia, 1 Doctor, remainder Villagers
        assignments: dict[str, Role] = {}
        for idx, pid in enumerate(player_ids):
            if idx == 0:
                assignments[pid] = Role.MAFIA
            elif idx == 1:
                assignments[pid] = Role.DOCTOR
            else:
                assignments[pid] = Role.VILLAGER
        return assignments


class FakeVotingServiceClient(VotingServiceClient):
    """In-memory fake voting service."""

    def __init__(self) -> None:
        self.open_ballots: dict[str, dict[str, Any]] = {}
        self.custom_results: dict[str, VotingResultDto] = {}

    def set_result(self, game_id: str, result: VotingResultDto) -> None:
        """Inject a predetermined voting result for a game."""
        self.custom_results[game_id] = result

    async def open_voting(
        self,
        game_id: str,
        round_number: int,
        eligible_voter_ids: list[str],
        candidate_ids: list[str],
        duration_seconds: int,
    ) -> None:
        self.open_ballots[game_id] = {
            "round_number": round_number,
            "eligible_voter_ids": eligible_voter_ids,
            "candidate_ids": candidate_ids,
            "duration_seconds": duration_seconds,
            "is_open": True,
        }

    async def close_voting(
        self,
        game_id: str,
        round_number: int,
    ) -> VotingResultDto:
        if game_id in self.custom_results:
            return self.custom_results[game_id]

        ballot = self.open_ballots.get(game_id, {})
        candidates = ballot.get("candidate_ids", [])
        eliminated = candidates[0] if candidates else None

        votes: dict[str, str | None] = {}
        for v in ballot.get("eligible_voter_ids", []):
            if v == eliminated:
                votes[v] = candidates[1] if len(candidates) > 1 else None
            else:
                votes[v] = eliminated

        return VotingResultDto(
            game_id=game_id,
            round_number=round_number,
            eliminated_player_id=eliminated,
            votes=votes,
            is_tie=False,
            tied_player_ids=[],
        )
