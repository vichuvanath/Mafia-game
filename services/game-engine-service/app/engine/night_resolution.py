"""Pure game logic for resolving night actions (Mafia attack & Doctor protection)."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NightResolutionResult:
    """Outcome of night phase actions."""

    killed_player_id: str | None
    is_saved: bool
    someone_died: bool
    summary: str


def resolve_night(
    mafia_target_id: str | None,
    doctor_target_id: str | None,
    doctor_id: str | None = None,
    doctor_can_self_protect: bool = True,
) -> NightResolutionResult:
    """Resolve night outcomes based on Mafia and Doctor selections.

    Rules:
    - Mafia selects one living player, Doctor selects one living player.
    - If both chose the same player, nobody dies (saved).
    - Otherwise, if Mafia chose a target, that target dies.
    - If Mafia submits nothing before deadline, no attack.
    - If Doctor submits nothing, no protection.
    - If doctor_can_self_protect is False and doctor_target_id == doctor_id, protection is ineffective.
    """
    # Enforce doctor self-protection rule
    effective_doctor_target = doctor_target_id
    if doctor_id is not None and doctor_target_id == doctor_id and not doctor_can_self_protect:
        effective_doctor_target = None

    # Mafia submitted nothing
    if mafia_target_id is None:
        return NightResolutionResult(
            killed_player_id=None,
            is_saved=False,
            someone_died=False,
            summary="No player was targeted during the night.",
        )

    # Doctor successfully protected the Mafia's target
    if effective_doctor_target is not None and effective_doctor_target == mafia_target_id:
        return NightResolutionResult(
            killed_player_id=None,
            is_saved=True,
            someone_died=False,
            summary="A player was attacked but successfully protected by the Doctor.",
        )

    # Mafia attack succeeded
    return NightResolutionResult(
        killed_player_id=mafia_target_id,
        is_saved=False,
        someone_died=True,
        summary=f"Player {mafia_target_id} was killed during the night.",
    )


def get_public_night_announcement(result: NightResolutionResult) -> dict[str, Any]:
    """Sanitize night outcome for public broadcasting.

    Requirement:
    Public output reveals only the outcome (someone died / nobody died),
    never who acted or who was protected.
    """
    if result.someone_died and result.killed_player_id is not None:
        return {
            "someone_died": True,
            "killed_player_id": result.killed_player_id,
            "message": f"Night has ended. Player {result.killed_player_id} was eliminated during the night.",
        }
    return {
        "someone_died": False,
        "killed_player_id": None,
        "message": "Night has ended. Nobody died tonight.",
    }
