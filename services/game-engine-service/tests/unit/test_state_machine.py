"""Unit tests for phase state machine transitions."""

import pytest

from app.config.rules import Phase
from app.engine.state_machine import (
    ALLOWED_TRANSITIONS,
    GameStateMachine,
    IllegalPhaseTransitionError,
    can_transition,
    get_next_normal_phase,
    validate_transition,
)

ALL_PHASES = list(Phase)


@pytest.mark.parametrize(
    "current,target",
    [
        (Phase.ROLE_ASSIGNMENT, Phase.NIGHT),
        (Phase.ROLE_ASSIGNMENT, Phase.GAME_OVER),
        (Phase.NIGHT, Phase.NIGHT_RESOLUTION),
        (Phase.NIGHT, Phase.GAME_OVER),
        (Phase.NIGHT_RESOLUTION, Phase.DAY),
        (Phase.NIGHT_RESOLUTION, Phase.GAME_OVER),
        (Phase.DAY, Phase.DISCUSSION),
        (Phase.DAY, Phase.GAME_OVER),
        (Phase.DISCUSSION, Phase.VOTING),
        (Phase.DISCUSSION, Phase.GAME_OVER),
        (Phase.VOTING, Phase.ELIMINATION),
        (Phase.VOTING, Phase.VOTING),
        (Phase.VOTING, Phase.GAME_OVER),
        (Phase.ELIMINATION, Phase.NIGHT),
        (Phase.ELIMINATION, Phase.GAME_OVER),
    ],
)
def test_allowed_transitions(current: Phase, target: Phase) -> None:
    assert can_transition(current, target) is True
    # Should not raise exception
    validate_transition(current, target)


@pytest.mark.parametrize("current", ALL_PHASES)
@pytest.mark.parametrize("target", ALL_PHASES)
def test_exhaustive_transition_matrix(current: Phase, target: Phase) -> None:
    allowed_targets = ALLOWED_TRANSITIONS.get(current, set())
    if target in allowed_targets:
        assert can_transition(current, target) is True
        validate_transition(current, target)
    else:
        assert can_transition(current, target) is False
        with pytest.raises(IllegalPhaseTransitionError) as exc_info:
            validate_transition(current, target)
        assert exc_info.value.current_phase == current
        assert exc_info.value.target_phase == target


def test_game_state_machine_progression() -> None:
    sm = GameStateMachine(initial_phase=Phase.ROLE_ASSIGNMENT)
    assert sm.current_phase == Phase.ROLE_ASSIGNMENT

    assert sm.transition_to(Phase.NIGHT) == Phase.NIGHT
    assert sm.transition_to(Phase.NIGHT_RESOLUTION) == Phase.NIGHT_RESOLUTION
    assert sm.transition_to(Phase.DAY) == Phase.DAY
    assert sm.transition_to(Phase.DISCUSSION) == Phase.DISCUSSION
    assert sm.transition_to(Phase.VOTING) == Phase.VOTING
    # Test revote loop
    assert sm.transition_to(Phase.VOTING) == Phase.VOTING
    assert sm.transition_to(Phase.ELIMINATION) == Phase.ELIMINATION
    # Next night
    assert sm.transition_to(Phase.NIGHT) == Phase.NIGHT
    # Game over
    assert sm.transition_to(Phase.GAME_OVER) == Phase.GAME_OVER

    # No transition out of GAME_OVER
    with pytest.raises(IllegalPhaseTransitionError):
        sm.transition_to(Phase.NIGHT)


@pytest.mark.parametrize(
    "current,expected_next",
    [
        (Phase.ROLE_ASSIGNMENT, Phase.NIGHT),
        (Phase.NIGHT, Phase.NIGHT_RESOLUTION),
        (Phase.NIGHT_RESOLUTION, Phase.DAY),
        (Phase.DAY, Phase.DISCUSSION),
        (Phase.DISCUSSION, Phase.VOTING),
        (Phase.VOTING, Phase.ELIMINATION),
        (Phase.ELIMINATION, Phase.NIGHT),
    ],
)
def test_get_next_normal_phase(current: Phase, expected_next: Phase) -> None:
    assert get_next_normal_phase(current) == expected_next


def test_get_next_normal_phase_invalid() -> None:
    with pytest.raises(ValueError):
        get_next_normal_phase(Phase.GAME_OVER)
