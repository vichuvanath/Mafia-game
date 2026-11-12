"""Property-based invariant tests using Hypothesis.

Invariants tested:
1. "game always ends": In simulated games with random actions/votes, every game reaches
   a decisive terminal outcome (VILLAGERS or MAFIA) in bounded rounds.
2. "a dead player never acts": Dead players cannot submit actions or cast votes.
3. "alive counts never go negative": Alive counts for all roles and total players
   are strictly >= 0 and monotonically non-increasing.
"""

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from app.config.rules import Role, TieRule, Winner
from app.engine.night_resolution import resolve_night
from app.engine.voting_rules import IllegalVoteError, evaluate_votes, validate_vote
from app.engine.win_conditions import evaluate_winner


# ---------------------------------------------------------------------------
# Invariant 1: Game Always Ends
# ---------------------------------------------------------------------------
@settings(max_examples=50, deadline=None)
@given(
    player_count=st.integers(min_value=5, max_value=12),
    seed=st.integers(min_value=1, max_value=1_000_000),
)
def test_invariant_game_always_ends(player_count: int, seed: int) -> None:
    rng = random.Random(seed)

    player_ids = [f"p_{i}" for i in range(player_count)]
    roles: dict[str, Role] = {
        player_ids[0]: Role.MAFIA,
        player_ids[1]: Role.DOCTOR,
    }
    for pid in player_ids[2:]:
        roles[pid] = Role.VILLAGER

    living = set(player_ids)
    winner: Winner | None = None
    max_rounds = 50  # Upper bound safeguard
    rounds_played = 0

    while rounds_played < max_rounds:
        rounds_played += 1

        # Check win condition at start of round
        current_living_roles = [roles[p] for p in living]
        winner = evaluate_winner(current_living_roles)
        if winner is not None:
            break

        # 1. Night Phase
        mafia_alive = [p for p in living if roles[p] == Role.MAFIA]
        doctor_alive = [p for p in living if roles[p] == Role.DOCTOR]

        mafia_target = rng.choice(list(living)) if mafia_alive else None
        doctor_target = rng.choice(list(living)) if doctor_alive else None

        night_res = resolve_night(
            mafia_target_id=mafia_target,
            doctor_target_id=doctor_target,
            doctor_id=doctor_alive[0] if doctor_alive else None,
            doctor_can_self_protect=True,
        )

        if night_res.killed_player_id and night_res.killed_player_id in living:
            living.remove(night_res.killed_player_id)

        # Win condition check after night
        current_living_roles = [roles[p] for p in living]
        winner = evaluate_winner(current_living_roles)
        if winner is not None:
            break

        # 2. Day Voting Phase
        if len(living) <= 1:
            break

        votes: dict[str, str | None] = {}
        living_list = list(living)
        for voter in living_list:
            # Player chooses any other living player (no self-vote)
            possible_targets = [p for p in living_list if p != voter]
            if possible_targets:
                votes[voter] = rng.choice(possible_targets)
            else:
                votes[voter] = None

        vote_res = evaluate_votes(
            votes=votes,
            living_player_ids=living,
            tie_rule=TieRule.NO_ELIMINATION,  # Guarantees progression without infinite revotes
        )

        if vote_res.eliminated_player_id and vote_res.eliminated_player_id in living:
            living.remove(vote_res.eliminated_player_id)

        current_living_roles = [roles[p] for p in living]
        winner = evaluate_winner(current_living_roles)
        if winner is not None:
            break

    # Invariant: A winner MUST be reached within max_rounds
    assert winner in (Winner.VILLAGERS, Winner.MAFIA)
    assert rounds_played < max_rounds


# ---------------------------------------------------------------------------
# Invariant 2: A Dead Player Never Acts
# ---------------------------------------------------------------------------
@settings(max_examples=50)
@given(
    total_players=st.integers(min_value=5, max_value=10),
    dead_index=st.integers(min_value=0, max_value=9),
)
def test_invariant_dead_player_never_acts(total_players: int, dead_index: int) -> None:
    player_ids = [f"p_{i}" for i in range(total_players)]
    dead_player = player_ids[dead_index % total_players]
    living = set(player_ids) - {dead_player}

    # 1. Dead player cannot vote in validate_vote
    try:
        target = next(iter(living))
        validate_vote(voter_id=dead_player, target_id=target, living_player_ids=living)
        raise AssertionError("Dead player was allowed to vote!")
    except IllegalVoteError:
        pass  # Expected

    # 2. Dead player cannot be voted for in validate_vote
    try:
        living_voter = next(iter(living))
        validate_vote(voter_id=living_voter, target_id=dead_player, living_player_ids=living)
        raise AssertionError("Living player was allowed to vote for a dead player!")
    except IllegalVoteError:
        pass  # Expected

    # 3. Dead player's vote is rejected or filtered in evaluate_votes
    living_voter = next(iter(living))
    target = [p for p in living if p != living_voter][0]
    votes: dict[str, str | None] = {
        dead_player: target,
        living_voter: target,
    }
    result = evaluate_votes(votes=votes, living_player_ids=living)
    # The vote cast by the dead player must NOT be counted
    assert result.vote_counts.get(target, 0) == 1


# ---------------------------------------------------------------------------
# Invariant 3: Alive Counts Never Go Negative
# ---------------------------------------------------------------------------
@settings(max_examples=50)
@given(
    total_players=st.integers(min_value=5, max_value=12),
    elimination_sequence=st.lists(st.integers(min_value=0, max_value=11), min_size=1, max_size=20),
)
def test_invariant_alive_counts_never_go_negative(
    total_players: int, elimination_sequence: list[int]
) -> None:
    player_ids = [f"p_{i}" for i in range(total_players)]
    roles: dict[str, Role] = {
        player_ids[0]: Role.MAFIA,
        player_ids[1]: Role.DOCTOR,
    }
    for pid in player_ids[2:]:
        roles[pid] = Role.VILLAGER

    living = set(player_ids)
    prev_living_count = len(living)

    for idx in elimination_sequence:
        if not living:
            break

        target_candidate = player_ids[idx % total_players]
        if target_candidate in living:
            living.remove(target_candidate)

        # Invariants:
        current_living_count = len(living)
        assert current_living_count <= prev_living_count  # Monotonically non-increasing
        assert current_living_count >= 0  # Never negative

        mafia_count = sum(1 for p in living if roles[p] == Role.MAFIA)
        doc_count = sum(1 for p in living if roles[p] == Role.DOCTOR)
        villager_count = sum(1 for p in living if roles[p] == Role.VILLAGER)

        assert mafia_count >= 0
        assert doc_count >= 0
        assert villager_count >= 0
        assert mafia_count + doc_count + villager_count == current_living_count

        prev_living_count = current_living_count
