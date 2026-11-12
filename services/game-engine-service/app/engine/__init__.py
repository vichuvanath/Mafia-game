"""Pure game engine module."""

from app.engine.night_resolution import (
    NightResolutionResult,
    get_public_night_announcement,
    resolve_night,
)
from app.engine.phases import Phase
from app.engine.recovery import GameRecoveryManager, recover_active_games
from app.engine.state_machine import (
    ALLOWED_TRANSITIONS,
    GameStateMachine,
    IllegalPhaseTransitionError,
    can_transition,
    get_next_normal_phase,
    validate_transition,
)
from app.engine.timers import (
    Clock,
    DeadlineSchedulerWorker,
    SystemClock,
    calculate_phase_deadline,
    is_deadline_passed,
)
from app.engine.voting_rules import (
    IllegalVoteError,
    VoteResult,
    evaluate_votes,
    validate_vote,
)
from app.engine.win_conditions import evaluate_winner

__all__ = [
    "ALLOWED_TRANSITIONS",
    "Clock",
    "DeadlineSchedulerWorker",
    "GameRecoveryManager",
    "GameStateMachine",
    "IllegalPhaseTransitionError",
    "IllegalVoteError",
    "NightResolutionResult",
    "Phase",
    "SystemClock",
    "VoteResult",
    "calculate_phase_deadline",
    "can_transition",
    "evaluate_votes",
    "evaluate_winner",
    "get_next_normal_phase",
    "get_public_night_announcement",
    "is_deadline_passed",
    "recover_active_games",
    "resolve_night",
    "validate_transition",
    "validate_vote",
]
