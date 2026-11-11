"""Pure domain logic and validation rules for voting ballots."""

from collections.abc import Collection
from datetime import UTC, datetime


class VotingRuleError(Exception):
    """Base exception for voting domain rule violations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotEligibleError(VotingRuleError):
    """Raised when voter is not in the eligible voters list."""

    def __init__(
        self,
        message: str = "Player is not eligible to vote in this round.",
    ) -> None:
        super().__init__(code="NOT_ELIGIBLE", message=message)


class InvalidTargetError(VotingRuleError):
    """Raised when target is not in the eligible targets list."""

    def __init__(
        self,
        message: str = "Target player is not an eligible target.",
    ) -> None:
        super().__init__(code="INVALID_TARGET", message=message)


class SelfVoteError(VotingRuleError):
    """Raised when a player attempts to vote for themselves."""

    def __init__(self, message: str = "Self-voting is not permitted.") -> None:
        super().__init__(code="SELF_VOTE", message=message)


class VotingClosedError(VotingRuleError):
    """Raised when attempting to vote in a closed or expired round."""

    def __init__(self, message: str = "Voting is closed for this round.") -> None:
        super().__init__(code="VOTING_CLOSED", message=message)


class VoteChangeNotAllowedError(VotingRuleError):
    """Raised when changing an already cast vote is disallowed."""

    def __init__(
        self,
        message: str = "Vote changes are not permitted in this round.",
    ) -> None:
        super().__init__(code="VOTE_CHANGE_NOT_ALLOWED", message=message)


def is_deadline_expired(deadline: datetime, now: datetime | None = None) -> bool:
    """Check if the given UTC deadline has expired.

    Args:
        deadline: ISO-8601 UTC timestamp of round deadline.
        now: Current time for evaluation, defaults to datetime.now(UTC).

    Returns:
        True if the deadline is in the past, False otherwise.
    """
    current_time = now if now is not None else datetime.now(UTC)
    if deadline.tzinfo is None:
        # Treat naive datetime as UTC
        deadline = deadline.replace(tzinfo=UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    return current_time >= deadline


def validate_ballot(
    voter_id: str,
    target_id: str,
    eligible_voters: Collection[str],
    eligible_targets: Collection[str],
    is_open: bool,
    deadline: datetime,
    existing_target_id: str | None = None,
    allow_vote_change: bool = False,
    now: datetime | None = None,
) -> None:
    """Validate ballot submission according to domain rules.

    Pure function with no I/O.

    Raises:
        VotingClosedError: If round is not open or deadline has expired.
        NotEligibleError: If voter is not in eligible_voters.
        InvalidTargetError: If target is not in eligible_targets.
        SelfVoteError: If voter_id == target_id.
        VoteChangeNotAllowedError: If voter already voted and allow_vote_change is False.
    """
    # 1. Round must be open
    if not is_open:
        raise VotingClosedError("Voting session is not open.")

    # 2. Deadline must not have passed
    if is_deadline_expired(deadline, now=now):
        raise VotingClosedError("Voting deadline has expired.")

    # 3. Voter must be eligible (living player in eligible list)
    if voter_id not in eligible_voters:
        raise NotEligibleError(f"Voter '{voter_id}' is not in the eligible voters list.")

    # 4. Target must be eligible (living player in target list)
    if target_id not in eligible_targets:
        raise InvalidTargetError(f"Target '{target_id}' is not in the eligible targets list.")

    # 5. Self-voting is strictly prohibited
    if voter_id == target_id:
        raise SelfVoteError("Players cannot vote for themselves.")

    # 6. Vote change validation
    if existing_target_id is not None and not allow_vote_change:
        raise VoteChangeNotAllowedError("Vote has already been cast and vote changes are disabled.")
