"""Pure game logic for evaluating win conditions."""

from app.config.rules import Role, Winner


def evaluate_winner(
    living_roles: list[Role],
) -> Winner | None:
    """Check if either team has achieved victory.

    Win conditions:
    - VILLAGERS win when no Mafia is alive (and at least one Villager/Doctor remains).
    - MAFIA wins when living Mafia count >= living non-Mafia count.
    - Otherwise, game continues (None).
    """
    mafia_count = sum(1 for r in living_roles if r == Role.MAFIA)
    non_mafia_count = sum(1 for r in living_roles if r in (Role.VILLAGER, Role.DOCTOR))

    if mafia_count == 0:
        if non_mafia_count > 0:
            return Winner.VILLAGERS
        # Edge case: nobody alive
        return None

    if mafia_count >= non_mafia_count:
        return Winner.MAFIA

    return None
