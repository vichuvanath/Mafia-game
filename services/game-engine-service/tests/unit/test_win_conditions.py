"""Unit tests for win condition evaluations across role permutations."""

import pytest

from app.config.rules import Role, Winner
from app.engine.win_conditions import evaluate_winner


@pytest.mark.parametrize(
    "living_roles,expected_winner",
    [
        # Villagers win cases (0 Mafia, >= 1 non-Mafia)
        ([Role.VILLAGER], Winner.VILLAGERS),
        ([Role.DOCTOR], Winner.VILLAGERS),
        ([Role.VILLAGER, Role.DOCTOR], Winner.VILLAGERS),
        ([Role.VILLAGER, Role.VILLAGER, Role.DOCTOR], Winner.VILLAGERS),
        # Mafia parity win cases (Mafia count == non-Mafia count)
        ([Role.MAFIA, Role.VILLAGER], Winner.MAFIA),
        ([Role.MAFIA, Role.DOCTOR], Winner.MAFIA),
        ([Role.MAFIA, Role.MAFIA, Role.VILLAGER, Role.DOCTOR], Winner.MAFIA),
        ([Role.MAFIA, Role.MAFIA, Role.VILLAGER, Role.VILLAGER], Winner.MAFIA),
        # Mafia majority win cases (Mafia count > non-Mafia count)
        ([Role.MAFIA, Role.MAFIA, Role.VILLAGER], Winner.MAFIA),
        ([Role.MAFIA, Role.MAFIA, Role.DOCTOR], Winner.MAFIA),
        ([Role.MAFIA, Role.MAFIA, Role.MAFIA, Role.VILLAGER], Winner.MAFIA),
        # Game continues (Mafia count > 0 and Mafia count < non-Mafia count)
        ([Role.MAFIA, Role.VILLAGER, Role.VILLAGER], None),
        ([Role.MAFIA, Role.DOCTOR, Role.VILLAGER], None),
        ([Role.MAFIA, Role.VILLAGER, Role.VILLAGER, Role.DOCTOR], None),
        ([Role.MAFIA, Role.MAFIA, Role.VILLAGER, Role.VILLAGER, Role.DOCTOR], None),
        # Edge cases
        ([], None),
    ],
)
def test_evaluate_winner(living_roles: list[Role], expected_winner: Winner | None) -> None:
    assert evaluate_winner(living_roles) == expected_winner
