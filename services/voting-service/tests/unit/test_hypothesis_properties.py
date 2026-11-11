"""Hypothesis property-based tests for voting and tally invariants."""

import random

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from app.core.ballot_rules import SelfVoteError, validate_ballot
from app.core.tally import compute_tally
from app.core.tie_rules import TieRule, VoteOutcome

# Strategy for generating player IDs (short readable strings or uuids)
player_ids = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=4, max_size=8)


@settings(max_examples=100)
@given(
    voters=st.lists(player_ids, min_size=2, max_size=15, unique=True),
    data=st.data(),
)
def test_property_sum_of_tally_plus_abstentions_equals_eligible_voters(
    voters: list[str],
    data: st.DataObject,
) -> None:
    """Invariant: sum of all cast votes plus abstentions equals count of eligible voters."""
    targets = list(voters)
    active_ballots: dict[str, str] = {}

    for voter in voters:
        # Decide randomly whether this voter participates or abstains
        votes = data.draw(st.booleans())
        if votes:
            possible_targets = [t for t in targets if t != voter]
            if possible_targets:
                chosen_target = data.draw(st.sampled_from(possible_targets))
                active_ballots[voter] = chosen_target

    result = compute_tally(active_ballots, voters, TieRule.NO_ELIMINATION)

    assert result.votes_cast + result.abstentions == len(voters)
    assert sum(result.tally.values()) + result.abstentions == len(voters)
    assert result.votes_expected == len(voters)


@settings(max_examples=100)
@given(
    voters=st.lists(player_ids, min_size=2, max_size=15, unique=True),
    data=st.data(),
)
def test_property_winner_always_has_max_count(
    voters: list[str],
    data: st.DataObject,
) -> None:
    """Invariant: When outcome is ELIMINATE, the target_id always has the maximum vote count."""
    targets = list(voters)
    active_ballots: dict[str, str] = {}

    for voter in voters:
        possible_targets = [t for t in targets if t != voter]
        if possible_targets and data.draw(st.booleans()):
            active_ballots[voter] = data.draw(st.sampled_from(possible_targets))

    result = compute_tally(active_ballots, voters, TieRule.NO_ELIMINATION)

    if result.outcome == VoteOutcome.ELIMINATE:
        assert result.target_id is not None
        winner_count = result.tally[result.target_id]
        assert winner_count == max(result.tally.values())
        # Winner must strictly exceed any other target's count
        for candidate, count in result.tally.items():
            if candidate != result.target_id:
                assert winner_count > count


@settings(max_examples=100)
@given(
    voter=player_ids,
    other_players=st.lists(player_ids, min_size=1, max_size=10, unique=True),
)
def test_property_no_self_votes_ever_counted(
    voter: str,
    other_players: list[str],
) -> None:
    """Invariant: Validated ballots reject self-votes;
    tally with no self-votes never counts a self-vote.
    """
    all_players = list(set([voter] + other_players))
    from datetime import UTC, datetime, timedelta

    deadline = datetime.now(UTC) + timedelta(minutes=10)

    # Attempting to validate a self-vote must always fail
    with pytest.raises(SelfVoteError):
        validate_ballot(
            voter_id=voter,
            target_id=voter,
            eligible_voters=all_players,
            eligible_targets=all_players,
            is_open=True,
            deadline=deadline,
        )


@settings(max_examples=100)
@given(
    voters=st.lists(player_ids, min_size=2, max_size=12, unique=True),
    data=st.data(),
)
def test_property_result_independent_of_vote_submission_order(
    voters: list[str],
    data: st.DataObject,
) -> None:
    """Invariant: Tally outcome and counts are independent of the order ballots were processed."""
    targets = list(voters)
    ballot_pairs: list[tuple[str, str]] = []

    for voter in voters:
        possible_targets = [t for t in targets if t != voter]
        if possible_targets and data.draw(st.booleans()):
            target = data.draw(st.sampled_from(possible_targets))
            ballot_pairs.append((voter, target))

    # Tally in original order
    original_dict = dict(ballot_pairs)
    res_orig = compute_tally(original_dict, voters, TieRule.REVOTE_AMONG_TIED)

    # Shuffle the insertion order
    shuffled_pairs = list(ballot_pairs)
    random.shuffle(shuffled_pairs)
    shuffled_dict = dict(shuffled_pairs)
    res_shuffled = compute_tally(shuffled_dict, voters, TieRule.REVOTE_AMONG_TIED)

    assert res_orig.outcome == res_shuffled.outcome
    assert res_orig.target_id == res_shuffled.target_id
    assert res_orig.tied_player_ids == res_shuffled.tied_player_ids
    assert res_orig.tally == res_shuffled.tally
    assert res_orig.abstentions == res_shuffled.abstentions
    assert res_orig.votes_cast == res_shuffled.votes_cast
