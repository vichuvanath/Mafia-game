"""Pure domain logic with zero I/O."""

from app.core.ballot_rules import (
    InvalidTargetError,
    NotEligibleError,
    SelfVoteError,
    VoteChangeNotAllowedError,
    VotingClosedError,
    VotingRuleError,
    is_deadline_expired,
    validate_ballot,
)
from app.core.tally import TallyResult, compute_tally
from app.core.tie_rules import TieRule, VoteOutcome, resolve_tie

__all__ = [
    "InvalidTargetError",
    "NotEligibleError",
    "SelfVoteError",
    "TallyResult",
    "TieRule",
    "VoteChangeNotAllowedError",
    "VoteOutcome",
    "VotingClosedError",
    "VotingRuleError",
    "compute_tally",
    "is_deadline_expired",
    "resolve_tie",
    "validate_ballot",
]
