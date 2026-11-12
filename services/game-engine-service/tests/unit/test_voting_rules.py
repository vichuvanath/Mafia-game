"""Unit tests for day voting rules and tie resolutions."""

import pytest

from app.config.rules import AbstentionHandling, TieRule
from app.engine.voting_rules import (
    IllegalVoteError,
    evaluate_votes,
    validate_vote,
)


def test_clear_winner_elimination() -> None:
    living = {"p1", "p2", "p3", "p4", "p5"}
    votes = {
        "p1": "p3",
        "p2": "p3",
        "p4": "p3",
        "p3": "p1",
        "p5": "p2",
    }
    result = evaluate_votes(votes=votes, living_player_ids=living)
    assert result.eliminated_player_id == "p3"
    assert result.is_tie is False
    assert result.requires_revote is False
    assert result.vote_counts["p3"] == 3


def test_tie_with_revote_rule() -> None:
    living = {"p1", "p2", "p3", "p4"}
    votes = {
        "p1": "p3",
        "p2": "p3",
        "p3": "p4",
        "p4": "p4",  # Note: p4 voting for p4 would be self-vote! Let's make p4 vote for p3 or someone else
    }
    # Fix self vote: p4 votes for p2
    votes = {
        "p1": "p3",
        "p2": "p3",
        "p3": "p2",
        "p4": "p2",
    }
    # 2 votes for p3, 2 votes for p2 -> Tie
    result = evaluate_votes(
        votes=votes,
        living_player_ids=living,
        tie_rule=TieRule.REVOTE_AMONG_TIED,
        current_revote_count=0,
        max_revotes=1,
    )
    assert result.eliminated_player_id is None
    assert result.is_tie is True
    assert result.requires_revote is True
    assert result.tied_player_ids == ["p2", "p3"]
    assert result.revote_candidates == ["p2", "p3"]


def test_tie_with_max_revotes_exhausted() -> None:
    living = {"p1", "p2", "p3", "p4"}
    votes = {
        "p1": "p3",
        "p2": "p3",
        "p3": "p2",
        "p4": "p2",
    }
    # Current revote count is 1, max_revotes is 1 -> No more revotes!
    result = evaluate_votes(
        votes=votes,
        living_player_ids=living,
        tie_rule=TieRule.REVOTE_AMONG_TIED,
        current_revote_count=1,
        max_revotes=1,
    )
    assert result.eliminated_player_id is None
    assert result.is_tie is True
    assert result.requires_revote is False


def test_tie_with_no_elimination_rule() -> None:
    living = {"p1", "p2", "p3", "p4"}
    votes = {
        "p1": "p3",
        "p2": "p3",
        "p3": "p2",
        "p4": "p2",
    }
    result = evaluate_votes(
        votes=votes,
        living_player_ids=living,
        tie_rule=TieRule.NO_ELIMINATION,
        current_revote_count=0,
        max_revotes=1,
    )
    assert result.eliminated_player_id is None
    assert result.is_tie is True
    assert result.requires_revote is False


def test_all_abstain() -> None:
    living = {"p1", "p2", "p3"}
    votes = {
        "p1": None,
        "p2": None,
        "p3": None,
    }
    result = evaluate_votes(votes=votes, living_player_ids=living)
    assert result.eliminated_player_id is None
    assert result.is_tie is False
    assert result.requires_revote is False
    assert result.vote_counts == {}


def test_missing_votes_treated_as_abstentions() -> None:
    living = {"p1", "p2", "p3", "p4"}
    # Only p1 and p2 voted; p3 and p4 missed deadline
    votes = {
        "p1": "p2",
        "p2": "p1",
    }
    result = evaluate_votes(
        votes=votes,
        living_player_ids=living,
        abstention_handling=AbstentionHandling.COUNT_AS_ABSTAIN,
    )
    assert result.is_tie is True
    assert result.tied_player_ids == ["p1", "p2"]


def test_self_vote_rejection() -> None:
    living = {"p1", "p2", "p3"}
    # validate_vote rejects self voting
    with pytest.raises(IllegalVoteError) as exc_info:
        validate_vote("p1", "p1", living)
    assert "Self-voting" in str(exc_info.value)

    # evaluate_votes rejects self voting
    with pytest.raises(IllegalVoteError):
        evaluate_votes({"p1": "p1", "p2": "p3"}, living)


def test_dead_player_validation() -> None:
    living = {"p1", "p2"}
    # Dead voter
    with pytest.raises(IllegalVoteError) as exc_info:
        validate_vote("dead_player", "p1", living)
    assert "Dead or non-member" in str(exc_info.value)

    # Voting for dead target
    with pytest.raises(IllegalVoteError) as exc_info2:
        validate_vote("p1", "dead_target", living)
    assert "not alive" in str(exc_info2.value)


def test_allowed_candidate_validation_during_revote() -> None:
    living = {"p1", "p2", "p3", "p4"}
    allowed_candidates = {"p2", "p3"}

    # Voting for an allowed candidate is valid
    validate_vote("p1", "p2", living, allowed_candidate_ids=allowed_candidates)

    # Voting for someone not in the revote ballot is rejected
    with pytest.raises(IllegalVoteError) as exc_info:
        validate_vote("p1", "p4", living, allowed_candidate_ids=allowed_candidates)
    assert "not among the allowed revote candidates" in str(exc_info.value)


def test_abstention_validation() -> None:
    living = {"p1", "p2", "p3"}
    # Explicit abstention should pass validate_vote without error
    validate_vote("p1", None, living)


def test_evaluate_votes_ignore_missing_and_filter_invalid_targets() -> None:
    living = {"p1", "p2", "p3"}
    # p3 is missing, handling is IGNORE
    # p1 votes for p2
    # p2 votes for a dead player p99 (should be skipped in tally)
    votes = {
        "p1": "p2",
        "p2": "p99",
    }
    result = evaluate_votes(
        votes=votes,
        living_player_ids=living,
        abstention_handling=AbstentionHandling.IGNORE,
        allowed_candidate_ids={"p2"},
    )
    assert result.eliminated_player_id == "p2"
    assert result.vote_counts == {"p2": 1}
