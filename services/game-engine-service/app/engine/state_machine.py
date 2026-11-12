"""Pure state machine governing phase transitions."""

from app.config.rules import Phase


class IllegalPhaseTransitionError(Exception):
    """Raised when an illegal phase transition is requested."""

    def __init__(self, current_phase: Phase, target_phase: Phase) -> None:
        super().__init__(
            f"Illegal phase transition requested from {current_phase.value} to {target_phase.value}"
        )
        self.current_phase = current_phase
        self.target_phase = target_phase


# Allowed state transitions table
ALLOWED_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.ROLE_ASSIGNMENT: {Phase.NIGHT, Phase.GAME_OVER},
    Phase.NIGHT: {Phase.NIGHT_RESOLUTION, Phase.GAME_OVER},
    Phase.NIGHT_RESOLUTION: {Phase.DAY, Phase.GAME_OVER},
    Phase.DAY: {Phase.DISCUSSION, Phase.GAME_OVER},
    Phase.DISCUSSION: {Phase.VOTING, Phase.GAME_OVER},
    Phase.VOTING: {Phase.ELIMINATION, Phase.VOTING, Phase.GAME_OVER},
    Phase.ELIMINATION: {Phase.NIGHT, Phase.GAME_OVER},
    Phase.GAME_OVER: set(),
}


def can_transition(current: Phase, target: Phase) -> bool:
    """Check whether a transition from current to target phase is permitted."""
    return target in ALLOWED_TRANSITIONS.get(current, set())


def validate_transition(current: Phase, target: Phase) -> None:
    """Validate transition, raising IllegalPhaseTransitionError if forbidden."""
    if not can_transition(current, target):
        raise IllegalPhaseTransitionError(current, target)


def get_next_normal_phase(current: Phase) -> Phase:
    """Get the standard progression phase following the current phase.

    progression:
    ROLE_ASSIGNMENT -> NIGHT -> NIGHT_RESOLUTION -> DAY -> DISCUSSION -> VOTING -> ELIMINATION -> NIGHT
    """
    progression: dict[Phase, Phase] = {
        Phase.ROLE_ASSIGNMENT: Phase.NIGHT,
        Phase.NIGHT: Phase.NIGHT_RESOLUTION,
        Phase.NIGHT_RESOLUTION: Phase.DAY,
        Phase.DAY: Phase.DISCUSSION,
        Phase.DISCUSSION: Phase.VOTING,
        Phase.VOTING: Phase.ELIMINATION,
        Phase.ELIMINATION: Phase.NIGHT,
    }
    if current not in progression:
        raise ValueError(f"Phase {current.value} does not have a default next progression phase")
    return progression[current]


class GameStateMachine:
    """Encapsulates pure state machine logic for a game session."""

    def __init__(self, initial_phase: Phase = Phase.ROLE_ASSIGNMENT) -> None:
        self._current_phase = initial_phase

    @property
    def current_phase(self) -> Phase:
        return self._current_phase

    def transition_to(self, target_phase: Phase) -> Phase:
        """Attempt to transition to target_phase.

        Raises IllegalPhaseTransitionError if disallowed.
        """
        validate_transition(self._current_phase, target_phase)
        self._current_phase = target_phase
        return self._current_phase
