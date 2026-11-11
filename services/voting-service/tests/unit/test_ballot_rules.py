"""Unit tests for ballot validation rules."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.ballot_rules import (
    InvalidTargetError,
    NotEligibleError,
    SelfVoteError,
    VoteChangeNotAllowedError,
    VotingClosedError,
    is_deadline_expired,
    validate_ballot,
)

VOTER_1 = "voter-uuid-1"
VOTER_2 = "voter-uuid-2"
TARGET_A = "target-uuid-a"
TARGET_B = "target-uuid-b"
OUTSIDER = "outsider-uuid"

DEFAULT_VOTERS = [VOTER_1, VOTER_2, TARGET_A, TARGET_B]
DEFAULT_TARGETS = [TARGET_A, TARGET_B, VOTER_1, VOTER_2]


def future_deadline() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=5)


def past_deadline() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=5)


def test_valid_ballot_submission() -> None:
    """A valid voter choosing an eligible different target succeeds."""
    validate_ballot(
        voter_id=VOTER_1,
        target_id=TARGET_A,
        eligible_voters=DEFAULT_VOTERS,
        eligible_targets=DEFAULT_TARGETS,
        is_open=True,
        deadline=future_deadline(),
    )


def test_not_open_raises_voting_closed() -> None:
    """Submitting when is_open is False raises VotingClosedError."""
    with pytest.raises(VotingClosedError) as exc_info:
        validate_ballot(
            voter_id=VOTER_1,
            target_id=TARGET_A,
            eligible_voters=DEFAULT_VOTERS,
            eligible_targets=DEFAULT_TARGETS,
            is_open=False,
            deadline=future_deadline(),
        )
    assert exc_info.value.code == "VOTING_CLOSED"


def test_expired_deadline_raises_voting_closed() -> None:
    """Submitting after deadline raises VotingClosedError."""
    with pytest.raises(VotingClosedError) as exc_info:
        validate_ballot(
            voter_id=VOTER_1,
            target_id=TARGET_A,
            eligible_voters=DEFAULT_VOTERS,
            eligible_targets=DEFAULT_TARGETS,
            is_open=True,
            deadline=past_deadline(),
        )
    assert exc_info.value.code == "VOTING_CLOSED"


def test_non_eligible_voter_rejected() -> None:
    """Voter not in eligible_voters raises NotEligibleError."""
    with pytest.raises(NotEligibleError) as exc_info:
        validate_ballot(
            voter_id=OUTSIDER,
            target_id=TARGET_A,
            eligible_voters=DEFAULT_VOTERS,
            eligible_targets=DEFAULT_TARGETS,
            is_open=True,
            deadline=future_deadline(),
        )
    assert exc_info.value.code == "NOT_ELIGIBLE"


def test_invalid_target_rejected() -> None:
    """Target not in eligible_targets raises InvalidTargetError."""
    with pytest.raises(InvalidTargetError) as exc_info:
        validate_ballot(
            voter_id=VOTER_1,
            target_id=OUTSIDER,
            eligible_voters=DEFAULT_VOTERS,
            eligible_targets=DEFAULT_TARGETS,
            is_open=True,
            deadline=future_deadline(),
        )
    assert exc_info.value.code == "INVALID_TARGET"


def test_self_vote_rejected() -> None:
    """Player voting for themselves raises SelfVoteError."""
    with pytest.raises(SelfVoteError) as exc_info:
        validate_ballot(
            voter_id=VOTER_1,
            target_id=VOTER_1,
            eligible_voters=DEFAULT_VOTERS,
            eligible_targets=DEFAULT_TARGETS,
            is_open=True,
            deadline=future_deadline(),
        )
    assert exc_info.value.code == "SELF_VOTE"


def test_vote_change_disallowed() -> None:
    """Submitting new vote when already voted and changes disallowed raises error."""
    with pytest.raises(VoteChangeNotAllowedError) as exc_info:
        validate_ballot(
            voter_id=VOTER_1,
            target_id=TARGET_B,
            eligible_voters=DEFAULT_VOTERS,
            eligible_targets=DEFAULT_TARGETS,
            is_open=True,
            deadline=future_deadline(),
            existing_target_id=TARGET_A,
            allow_vote_change=False,
        )
    assert exc_info.value.code == "VOTE_CHANGE_NOT_ALLOWED"


def test_vote_change_allowed() -> None:
    """Submitting new vote when already voted and changes allowed succeeds."""
    validate_ballot(
        voter_id=VOTER_1,
        target_id=TARGET_B,
        eligible_voters=DEFAULT_VOTERS,
        eligible_targets=DEFAULT_TARGETS,
        is_open=True,
        deadline=future_deadline(),
        existing_target_id=TARGET_A,
        allow_vote_change=True,
    )


@pytest.mark.parametrize(
    ("is_open", "is_past", "expected_closed"),
    [
        (False, False, True),
        (True, True, True),
        (False, True, True),
        (True, False, False),
    ],
)
def test_open_and_deadline_combinations(
    is_open: bool, is_past: bool, expected_closed: bool
) -> None:
    deadline = past_deadline() if is_past else future_deadline()
    if expected_closed:
        with pytest.raises(VotingClosedError):
            validate_ballot(
                voter_id=VOTER_1,
                target_id=TARGET_A,
                eligible_voters=DEFAULT_VOTERS,
                eligible_targets=DEFAULT_TARGETS,
                is_open=is_open,
                deadline=deadline,
            )
    else:
        validate_ballot(
            voter_id=VOTER_1,
            target_id=TARGET_A,
            eligible_voters=DEFAULT_VOTERS,
            eligible_targets=DEFAULT_TARGETS,
            is_open=is_open,
            deadline=deadline,
        )


def test_is_deadline_expired_helper() -> None:
    now = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    past = datetime(2026, 11, 11, 11, 59, 59, tzinfo=UTC)
    exact = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    future = datetime(2026, 11, 11, 12, 0, 1, tzinfo=UTC)

    assert is_deadline_expired(past, now=now) is True
    assert is_deadline_expired(exact, now=now) is True
    assert is_deadline_expired(future, now=now) is False

    # Test with naive datetimes
    naive_past = datetime(2026, 11, 11, 11, 59, 59)
    naive_now = datetime(2026, 11, 11, 12, 0, 0)
    assert is_deadline_expired(naive_past, now=naive_now) is True
