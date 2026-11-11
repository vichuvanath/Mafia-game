"""Contract tests validating request schemas, published events, and section 4 examples."""

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA_DIR = Path(__file__).parent / "schemas"


def load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def test_open_voting_request_contract_schema() -> None:
    schema = load_schema("open_voting_request.json")
    example = {
        "round_number": 1,
        "voting_round_id": "c71a3964-b529-411a-8cbf-0e86b0ecb791",
        "eligible_voters": [
            "d92d4f3b-8520-4a87-9c98-1e43e23075b2",
            "e7a7836e-b6df-44be-bfa0-51c8f1e6bdf2",
        ],
        "eligible_targets": [
            "d92d4f3b-8520-4a87-9c98-1e43e23075b2",
            "e7a7836e-b6df-44be-bfa0-51c8f1e6bdf2",
        ],
        "deadline": "2026-11-10T15:00:00Z",
        "tie_rule": "NO_ELIMINATION",
        "allow_vote_change": False,
        "reveal_ballots_after_close": False,
    }
    jsonschema.validate(instance=example, schema=schema)


def test_close_voting_request_contract_schema() -> None:
    schema = load_schema("close_voting_request.json")
    example = {
        "voting_round_id": "c71a3964-b529-411a-8cbf-0e86b0ecb791",
    }
    jsonschema.validate(instance=example, schema=schema)


def test_event_envelope_contract_schema() -> None:
    schema = load_schema("event_envelope.json")
    example = {
        "event_id": "evt-uuid-12345",
        "event_type": "VOTE_SUBMITTED",
        "occurred_at": "2026-11-10T15:00:00Z",
        "game_id": "game-uuid-9876",
        "version": 1,
        "payload": {
            "voting_round_id": "c71a3964-b529-411a-8cbf-0e86b0ecb791",
            "votes_cast": 3,
            "votes_expected": 6,
        },
    }
    jsonschema.validate(instance=example, schema=schema)


def test_vote_submitted_event_contract_schema() -> None:
    schema = load_schema("vote_submitted.json")
    example = {
        "voting_round_id": "c71a3964-b529-411a-8cbf-0e86b0ecb791",
        "votes_cast": 3,
        "votes_expected": 6,
    }
    jsonschema.validate(instance=example, schema=schema)


def test_voting_closed_event_contract_schema() -> None:
    schema = load_schema("voting_closed.json")
    for reason in ["ALL_VOTED", "DEADLINE", "ENGINE_REQUEST"]:
        example = {
            "voting_round_id": "c71a3964-b529-411a-8cbf-0e86b0ecb791",
            "round_number": 1,
            "reason": reason,
        }
        jsonschema.validate(instance=example, schema=schema)


def test_vote_result_ready_event_contract_schema() -> None:
    schema = load_schema("vote_result_ready.json")

    # Clear elimination example
    example_elim = {
        "voting_round_id": "c71a3964-b529-411a-8cbf-0e86b0ecb791",
        "round_number": 1,
        "outcome": "ELIMINATE",
        "target_id": "target-uuid-1",
        "tied_player_ids": [],
        "tally": {"target-uuid-1": 4, "target-uuid-2": 1},
        "abstentions": 1,
    }
    jsonschema.validate(instance=example_elim, schema=schema)

    # Tie example
    example_tie = {
        "voting_round_id": "c71a3964-b529-411a-8cbf-0e86b0ecb791",
        "round_number": 1,
        "outcome": "TIE",
        "target_id": None,
        "tied_player_ids": ["target-uuid-1", "target-uuid-2"],
        "tally": {"target-uuid-1": 2, "target-uuid-2": 2},
        "abstentions": 0,
    }
    jsonschema.validate(instance=example_tie, schema=schema)

    # No elimination example
    example_no_elim = {
        "voting_round_id": "c71a3964-b529-411a-8cbf-0e86b0ecb791",
        "round_number": 1,
        "outcome": "NO_ELIMINATION",
        "target_id": None,
        "tied_player_ids": ["target-uuid-1", "target-uuid-2"],
        "tally": {"target-uuid-1": 2, "target-uuid-2": 2},
        "abstentions": 2,
    }
    jsonschema.validate(instance=example_no_elim, schema=schema)


def test_contract_fails_on_extra_or_invalid_fields() -> None:
    schema_vote = load_schema("vote_submitted.json")
    bad_payload = {
        "voting_round_id": "c71a3964",
        "votes_cast": 3,
        "votes_expected": 6,
        "leaked_voter_id": "voter-secret",  # Must reject leaky fields!
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad_payload, schema=schema_vote)
