"""Unit tests for tie resolution rules."""

import pytest

from app.core.tie_rules import TieRule, VoteOutcome, resolve_tie


def test_resolve_tie_no_elimination() -> None:
    tied = ["player-z", "player-a"]
    outcome, candidates = resolve_tie(tied, TieRule.NO_ELIMINATION)
    assert outcome == VoteOutcome.NO_ELIMINATION
    assert candidates == ["player-a", "player-z"]


def test_resolve_tie_revote_among_tied() -> None:
    tied = ["player-x", "player-m", "player-b"]
    outcome, candidates = resolve_tie(tied, TieRule.REVOTE_AMONG_TIED)
    assert outcome == VoteOutcome.TIE
    assert candidates == ["player-b", "player-m", "player-x"]


@pytest.mark.parametrize(
    ("rule_input", "expected_outcome"),
    [
        (TieRule.NO_ELIMINATION, VoteOutcome.NO_ELIMINATION),
        ("NO_ELIMINATION", VoteOutcome.NO_ELIMINATION),
        (TieRule.REVOTE_AMONG_TIED, VoteOutcome.TIE),
        ("REVOTE_AMONG_TIED", VoteOutcome.TIE),
    ],
)
def test_resolve_tie_string_and_enum_compatibility(
    rule_input: TieRule | str,
    expected_outcome: VoteOutcome,
) -> None:
    tied = ["player-2", "player-1"]
    outcome, candidates = resolve_tie(tied, rule_input)
    assert outcome == expected_outcome
    assert candidates == ["player-1", "player-2"]
