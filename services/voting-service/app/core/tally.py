"""Pure domain logic for vote tallying and outcome computation."""

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from app.core.tie_rules import TieRule, VoteOutcome, resolve_tie


@dataclass(frozen=True)
class TallyResult:
    """Immutable result of a voting round tally."""

    outcome: VoteOutcome
    target_id: str | None
    tied_player_ids: list[str]
    tally: dict[str, int]
    abstentions: int
    votes_cast: int
    votes_expected: int


def compute_tally(
    active_ballots: Mapping[str, str],
    eligible_voters: Collection[str],
    tie_rule: TieRule | str = TieRule.NO_ELIMINATION,
    abstain_counts_as_no_vote: bool = True,
) -> TallyResult:
    """Compute the authoritative proposed vote tally and round outcome.

    Pure function with zero I/O.

    Args:
        active_ballots: Mapping of voter_id -> target_id for all accepted active ballots.
        eligible_voters: Collection of player IDs eligible to vote in this round.
        tie_rule: Rule to apply when multiple targets share the highest vote count.
        abstain_counts_as_no_vote: If True, missing votes count as abstentions and add 0 votes.

    Returns:
        TallyResult containing outcome, proposed elimination target or tied candidates,
        vote breakdown, abstentions count, and participation totals.
    """
    votes_expected = len(eligible_voters)
    votes_cast = len(active_ballots)
    abstentions = max(0, votes_expected - votes_cast)

    # If no votes were cast at all (e.g. everyone abstained or 0 voters)
    if votes_cast == 0:
        return TallyResult(
            outcome=VoteOutcome.NO_ELIMINATION,
            target_id=None,
            tied_player_ids=[],
            tally={},
            abstentions=abstentions,
            votes_cast=0,
            votes_expected=votes_expected,
        )

    # Count votes per target candidate
    vote_counts = Counter(active_ballots.values())
    sorted_tally: dict[str, int] = dict(
        sorted(vote_counts.items(), key=lambda item: (-item[1], item[0]))
    )

    max_count = max(vote_counts.values())
    leaders = sorted([target for target, count in vote_counts.items() if count == max_count])

    if len(leaders) == 1:
        # Clear singular winner
        return TallyResult(
            outcome=VoteOutcome.ELIMINATE,
            target_id=leaders[0],
            tied_player_ids=[],
            tally=sorted_tally,
            abstentions=abstentions,
            votes_cast=votes_cast,
            votes_expected=votes_expected,
        )

    # Tie detected: apply configured tie rule
    outcome, tied_candidates = resolve_tie(leaders, tie_rule)
    return TallyResult(
        outcome=outcome,
        target_id=None,
        tied_player_ids=tied_candidates,
        tally=sorted_tally,
        abstentions=abstentions,
        votes_cast=votes_cast,
        votes_expected=votes_expected,
    )
