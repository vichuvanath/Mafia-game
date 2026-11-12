"""Contract tests validating events against JSON Schemas and testing HTTP clients with respx."""

import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
import respx

from app.clients.role_client import HttpRoleServiceClient
from app.clients.voting_client import HttpVotingServiceClient
from app.config.rules import Role
from app.events.envelope import EventEnvelope

SCHEMAS_DIR = Path(__file__).parent / "schemas"


def load_schema(name: str) -> dict:
    path = SCHEMAS_DIR / name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- Published Events Contract Tests ---


def test_game_started_event_contract() -> None:
    schema = load_schema("game_started.json")
    envelope = EventEnvelope(
        event_id="evt-start-1",
        event_type="GAME_STARTED",
        occurred_at=datetime.now(UTC),
        game_id="game-1",
        version=1,
        payload={
            "game_id": "game-1",
            "room_id": "room-1",
            "player_count": 5,
            "round_number": 1,
        },
    )
    jsonschema.validate(instance=json.loads(envelope.model_dump_json()), schema=schema)


def test_phase_changed_event_contract() -> None:
    schema = load_schema("phase_changed.json")
    envelope = EventEnvelope(
        event_id="evt-phase-1",
        event_type="PHASE_CHANGED",
        occurred_at=datetime.now(UTC),
        game_id="game-1",
        version=2,
        payload={
            "game_id": "game-1",
            "previous_phase": "NIGHT",
            "new_phase": "DAY",
            "round_number": 1,
            "deadline": datetime.now(UTC).isoformat(),
        },
    )
    jsonschema.validate(instance=json.loads(envelope.model_dump_json()), schema=schema)


def test_night_resolved_event_contract() -> None:
    schema = load_schema("night_resolved.json")
    envelope = EventEnvelope(
        event_id="evt-night-1",
        event_type="NIGHT_RESOLVED",
        occurred_at=datetime.now(UTC),
        game_id="game-1",
        version=2,
        payload={
            "someone_died": True,
            "killed_player_id": "p3",
            "message": "Player p3 was eliminated during the night.",
        },
    )
    jsonschema.validate(instance=json.loads(envelope.model_dump_json()), schema=schema)


def test_player_elimination_confirmed_event_contract() -> None:
    schema = load_schema("player_elimination_confirmed.json")
    envelope = EventEnvelope(
        event_id="evt-elim-1",
        event_type="PLAYER_ELIMINATION_CONFIRMED",
        occurred_at=datetime.now(UTC),
        game_id="game-1",
        version=3,
        payload={
            "player_id": "p2",
            "round_number": 1,
            "revealed_role": "DOCTOR",
        },
    )
    jsonschema.validate(instance=json.loads(envelope.model_dump_json()), schema=schema)


def test_game_over_event_contract() -> None:
    schema = load_schema("game_over.json")
    envelope = EventEnvelope(
        event_id="evt-over-1",
        event_type="GAME_OVER",
        occurred_at=datetime.now(UTC),
        game_id="game-1",
        version=4,
        payload={
            "winner": "MAFIA",
            "round_number": 2,
        },
    )
    jsonschema.validate(instance=json.loads(envelope.model_dump_json()), schema=schema)


# --- Consumed Events Contract Tests ---


def test_room_ready_consumed_contract() -> None:
    schema = load_schema("room_ready.json")
    data = {
        "event_id": "evt-rr-1",
        "event_type": "ROOM_READY",
        "occurred_at": datetime.now(UTC).isoformat(),
        "game_id": "game-1",
        "version": 1,
        "payload": {
            "room_id": "room-1",
            "player_ids": ["p1", "p2", "p3", "p4", "p5"],
            "player_names": {"p1": "Alice", "p2": "Bob"},
        },
    }
    jsonschema.validate(instance=data, schema=schema)


def test_role_assignment_completed_consumed_contract() -> None:
    schema = load_schema("role_assignment_completed.json")
    data = {
        "event_id": "evt-rac-1",
        "event_type": "ROLE_ASSIGNMENT_COMPLETED",
        "occurred_at": datetime.now(UTC).isoformat(),
        "game_id": "game-1",
        "version": 1,
        "payload": {
            "assignments": {
                "p1": "MAFIA",
                "p2": "DOCTOR",
                "p3": "VILLAGER",
            }
        },
    }
    jsonschema.validate(instance=data, schema=schema)


def test_vote_result_ready_consumed_contract() -> None:
    schema = load_schema("vote_result_ready.json")
    data = {
        "event_id": "evt-vrr-1",
        "event_type": "VOTE_RESULT_READY",
        "occurred_at": datetime.now(UTC).isoformat(),
        "game_id": "game-1",
        "version": 1,
        "payload": {
            "round_number": 1,
            "eliminated_player_id": "p3",
            "votes": {"p1": "p3", "p2": "p3", "p3": "p1"},
            "is_tie": False,
            "tied_player_ids": [],
        },
    }
    jsonschema.validate(instance=data, schema=schema)


def test_player_disconnected_consumed_contract() -> None:
    schema = load_schema("player_disconnected.json")
    data = {
        "event_id": "evt-pd-1",
        "event_type": "PLAYER_DISCONNECTED",
        "occurred_at": datetime.now(UTC).isoformat(),
        "game_id": "game-1",
        "version": 1,
        "payload": {
            "player_id": "p2",
            "disconnected_at": datetime.now(UTC).isoformat(),
        },
    }
    jsonschema.validate(instance=data, schema=schema)


# --- Respx Client Mock Tests ---


@pytest.mark.asyncio
@respx.mock
async def test_http_role_client_contract_with_respx() -> None:
    client = HttpRoleServiceClient(base_url="http://role-service.test")
    respx.post("http://role-service.test/internal/roles/assign").respond(
        200,
        json={
            "game_id": "game-c1",
            "assignments": [
                {"player_id": "p1", "role": "MAFIA"},
                {"player_id": "p2", "role": "DOCTOR"},
                {"player_id": "p3", "role": "VILLAGER"},
            ],
        },
    )

    roles = await client.request_role_assignment("game-c1", ["p1", "p2", "p3"])
    assert roles["p1"] == Role.MAFIA
    assert roles["p2"] == Role.DOCTOR
    assert roles["p3"] == Role.VILLAGER


@pytest.mark.asyncio
@respx.mock
async def test_http_voting_client_contract_with_respx() -> None:
    client = HttpVotingServiceClient(base_url="http://voting-service.test")
    respx.post("http://voting-service.test/internal/voting/open").respond(
        200, json={"status": "OPENED"}
    )
    respx.post("http://voting-service.test/internal/voting/close").respond(
        200,
        json={
            "game_id": "game-c2",
            "round_number": 1,
            "eliminated_player_id": "p3",
            "votes": {"p1": "p3", "p2": "p3"},
            "is_tie": False,
            "tied_player_ids": [],
        },
    )

    await client.open_voting(
        game_id="game-c2",
        round_number=1,
        eligible_voter_ids=["p1", "p2", "p3"],
        candidate_ids=["p1", "p2", "p3"],
        duration_seconds=30,
    )
    result = await client.close_voting(game_id="game-c2", round_number=1)
    assert result.eliminated_player_id == "p3"
    assert result.is_tie is False
