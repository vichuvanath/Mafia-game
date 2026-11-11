"""Unit tests for vote counting, tallying, and outcome determination."""

from app.core.tally import compute_tally
from app.core.tie_rules import TieRule, VoteOutcome


def test_clear_winner_elimination() -> None:
    eligible = ["voter-1", "voter-2", "voter-3", "target-a", "target-b"]
    ballots = {
        "voter-1": "target-a",
        "voter-2": "target-a",
        "voter-3": "target-b",
    }
    result = compute_tally(ballots, eligible, TieRule.NO_ELIMINATION)

    assert result.outcome == VoteOutcome.ELIMINATE
    assert result.target_id == "target-a"
    assert result.tied_player_ids == []
    assert result.tally == {"target-a": 2, "target-b": 1}
    assert result.votes_cast == 3
    assert result.votes_expected == 5
    assert result.abstentions == 2


def test_two_way_tie_no_elimination() -> None:
    eligible = ["p1", "p2", "p3", "p4"]
    ballots = {
        "p1": "p3",
        "p2": "p4",
    }
    result = compute_tally(ballots, eligible, TieRule.NO_ELIMINATION)

    assert result.outcome == VoteOutcome.NO_ELIMINATION
    assert result.target_id is None
    assert result.tied_player_ids == ["p3", "p4"]
    assert result.tally == {"p3": 1, "p4": 1}
    assert result.votes_cast == 2
    assert result.abstentions == 2


def test_two_way_tie_revote_among_tied() -> None:
    eligible = ["p1", "p2", "p3", "p4"]
    ballots = {
        "p1": "p3",
        "p2": "p4",
    }
    result = compute_tally(ballots, eligible, TieRule.REVOTE_AMONG_TIED)

    assert result.outcome == VoteOutcome.TIE
    assert result.target_id is None
    assert result.tied_player_ids == ["p3", "p4"]
    assert result.tally == {"p3": 1, "p4": 1}


def test_three_way_tie() -> None:
    eligible = ["p1", "p2", "p3", "p4", "p5", "p6"]
    ballots = {
        "p1": "p4",
        "p2": "p4",
        "p3": "p5",
        "p4": "p5",
        "p5": "p6",
        "p6": "p6",
    }
    result = compute_tally(ballots, eligible, TieRule.REVOTE_AMONG_TIED)

    assert result.outcome == VoteOutcome.TIE
    assert result.target_id is None
    assert result.tied_player_ids == ["p4", "p5", "p6"]
    assert result.tally == {"p4": 2, "p5": 2, "p6": 2}
    assert result.votes_cast == 6
    assert result.abstentions == 0


def test_all_abstain() -> None:
    eligible = ["p1", "p2", "p3"]
    ballots: dict[str, str] = {}
    result = compute_tally(ballots, eligible, TieRule.NO_ELIMINATION)

    assert result.outcome == VoteOutcome.NO_ELIMINATION
    assert result.target_id is None
    assert result.tied_player_ids == []
    assert result.tally == {}
    assert result.votes_cast == 0
    assert result.votes_expected == 3
    assert result.abstentions == 3


def test_single_voter() -> None:
    eligible = ["p1", "p2"]
    ballots = {"p1": "p2"}
    result = compute_tally(ballots, eligible, TieRule.NO_ELIMINATION)

    assert result.outcome == VoteOutcome.ELIMINATE
    assert result.target_id == "p2"
    assert result.tied_player_ids == []
    assert result.tally == {"p2": 1}
    assert result.votes_cast == 1
    assert result.votes_expected == 2
    assert result.abstentions == 1


def test_one_voter_eligible_all_vote() -> None:
    eligible = ["p1"]
    ballots = {"p1": "p2"}
    result = compute_tally(ballots, eligible, TieRule.NO_ELIMINATION)

    assert result.outcome == VoteOutcome.ELIMINATE
    assert result.target_id == "p2"
    assert result.votes_cast == 1
    assert result.votes_expected == 1
    assert result.abstentions == 0
