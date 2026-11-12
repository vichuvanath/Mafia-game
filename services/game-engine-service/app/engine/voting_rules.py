"""Pure game logic for day vote counting, tie resolution, and elimination determination."""

from collections import Counter
from dataclasses import dataclass

from app.config.rules import AbstentionHandling, TieRule


class IllegalVoteError(Exception):
    """Raised when an illegal vote is submitted or processed."""

    pass


@dataclass(frozen=True)
class VoteResult:
    """Outcome of vote counting."""

    eliminated_player_id: str | None
    is_tie: bool
    tied_player_ids: list[str]
    vote_counts: dict[str, int]
    requires_revote: bool
    revote_candidates: list[str]
    summary: str


def validate_vote(
    voter_id: str,
    target_id: str | None,
    living_player_ids: set[str],
    allowed_candidate_ids: set[str] | None = None,
) -> None:
    """Validate a single vote submission.

    Rules:
    - Living players only.
    - No self-votes.
    - Target must be a living player (and among allowed candidates if revoting).
    - Abstention (target_id is None) is permitted.
    """
    if voter_id not in living_player_ids:
        raise IllegalVoteError(f"Dead or non-member player {voter_id} cannot vote")

    if target_id is None:
        # Abstention
        return

    if voter_id == target_id:
        raise IllegalVoteError(f"Self-voting is not allowed: player {voter_id}")

    if target_id not in living_player_ids:
        raise IllegalVoteError(f"Target player {target_id} is not alive or not in game")

    if allowed_candidate_ids is not None and target_id not in allowed_candidate_ids:
        raise IllegalVoteError(
            f"Target player {target_id} is not among the allowed revote candidates"
        )


def evaluate_votes(
    votes: dict[str, str | None],  # voter_id -> target_id (or None for abstain)
    living_player_ids: set[str],
    tie_rule: TieRule = TieRule.REVOTE_AMONG_TIED,
    current_revote_count: int = 0,
    max_revotes: int = 1,
    abstention_handling: AbstentionHandling = AbstentionHandling.COUNT_AS_ABSTAIN,
    allowed_candidate_ids: set[str] | None = None,
) -> VoteResult:
    """Calculate vote tallies and determine who, if anyone, is eliminated.

    Rules:
    - Every living player has one vote; no self-votes; dead players cannot vote.
    - Missing votes at the deadline count as abstentions (or ignored).
    - Highest vote count is eliminated.
    - Tie resolution:
      - If REVOTE_AMONG_TIED: revote among tied players if current_revote_count < max_revotes.
      - Once max_revotes is reached or NO_ELIMINATION: nobody is eliminated.
    """
    # 1. Fill missing living players with None if abstention handling is configured
    full_votes: dict[str, str | None] = {}
    for player_id in living_player_ids:
        if player_id in votes:
            full_votes[player_id] = votes[player_id]
        elif abstention_handling == AbstentionHandling.COUNT_AS_ABSTAIN:
            full_votes[player_id] = None

    # 2. Tally votes
    counts: Counter[str] = Counter()
    for voter_id, target_id in full_votes.items():
        if voter_id not in living_player_ids:
            continue
        if target_id is None:
            continue
        if target_id == voter_id:
            raise IllegalVoteError(f"Self-voting is not allowed: player {voter_id}")
        if target_id not in living_player_ids:
            continue
        if allowed_candidate_ids is not None and target_id not in allowed_candidate_ids:
            continue
        counts[target_id] += 1

    vote_counts = dict(counts)

    # If nobody received any votes
    if not counts:
        return VoteResult(
            eliminated_player_id=None,
            is_tie=False,
            tied_player_ids=[],
            vote_counts={},
            requires_revote=False,
            revote_candidates=[],
            summary="No votes were cast; nobody is eliminated.",
        )

    # 3. Find highest vote count
    highest_count = max(counts.values())
    top_candidates = sorted([c for c, cnt in counts.items() if cnt == highest_count])

    # Clear winner
    if len(top_candidates) == 1:
        winner = top_candidates[0]
        return VoteResult(
            eliminated_player_id=winner,
            is_tie=False,
            tied_player_ids=[],
            vote_counts=vote_counts,
            requires_revote=False,
            revote_candidates=[],
            summary=f"Player {winner} received the most votes ({highest_count}) and is eliminated.",
        )

    # Tie case
    if tie_rule == TieRule.REVOTE_AMONG_TIED and current_revote_count < max_revotes:
        return VoteResult(
            eliminated_player_id=None,
            is_tie=True,
            tied_player_ids=top_candidates,
            vote_counts=vote_counts,
            requires_revote=True,
            revote_candidates=top_candidates,
            summary=f"Tie between players {top_candidates} with {highest_count} votes each. Revote required.",
        )

    return VoteResult(
        eliminated_player_id=None,
        is_tie=True,
        tied_player_ids=top_candidates,
        vote_counts=vote_counts,
        requires_revote=False,
        revote_candidates=[],
        summary=f"Tie between players {top_candidates}. Under {tie_rule.value}, nobody is eliminated.",
    )
