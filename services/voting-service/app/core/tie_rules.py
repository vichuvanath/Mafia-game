"""Pure domain logic for resolving vote ties."""

from enum import StrEnum


class TieRule(StrEnum):
    """Tie rules supported by the voting engine."""

    NO_ELIMINATION = "NO_ELIMINATION"
    REVOTE_AMONG_TIED = "REVOTE_AMONG_TIED"


class VoteOutcome(StrEnum):
    """Possible outcomes of a voting round."""

    ELIMINATE = "ELIMINATE"
    TIE = "TIE"
    NO_ELIMINATION = "NO_ELIMINATION"


def resolve_tie(
    tied_leaders: list[str],
    tie_rule: TieRule | str,
) -> tuple[VoteOutcome, list[str]]:
    """Resolve a tie among leading candidates based on configured tie rule.

    Args:
        tied_leaders: List of player IDs tied for the highest vote count.
        tie_rule: The configured rule for handling ties.

    Returns:
        Tuple of (VoteOutcome, list of tied player IDs in deterministic sorted order).
    """
    sorted_tied = sorted(tied_leaders)
    normalized_rule = tie_rule if isinstance(tie_rule, TieRule) else TieRule(tie_rule)

    if normalized_rule == TieRule.REVOTE_AMONG_TIED:
        return VoteOutcome.TIE, sorted_tied

    return VoteOutcome.NO_ELIMINATION, sorted_tied
