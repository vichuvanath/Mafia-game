"""Unit tests for RedisStateManager: live cache, action buffers, and scheduler."""

import pytest

from app.state.redis_state import RedisStateManager


@pytest.mark.asyncio
async def test_live_session_caching(redis_state_manager: RedisStateManager) -> None:
    game_id = "game-redis-1"
    session_data = {
        "game_id": game_id,
        "phase": "NIGHT",
        "round_number": 1,
        "players": [{"player_id": "p1", "is_alive": True}],
    }

    # Not found initially
    assert await redis_state_manager.get_live_session(game_id) is None

    # Set and retrieve
    await redis_state_manager.set_live_session(game_id, session_data)
    cached = await redis_state_manager.get_live_session(game_id)
    assert cached is not None
    assert cached["game_id"] == game_id
    assert cached["phase"] == "NIGHT"

    # Delete
    await redis_state_manager.delete_live_session(game_id)
    assert await redis_state_manager.get_live_session(game_id) is None


@pytest.mark.asyncio
async def test_night_action_buffering(redis_state_manager: RedisStateManager) -> None:
    game_id = "game-redis-2"

    # Record Mafia action
    await redis_state_manager.record_night_action(
        game_id=game_id,
        role="MAFIA",
        actor_id="m1",
        target_id="v1",
    )
    # Record Doctor action
    await redis_state_manager.record_night_action(
        game_id=game_id,
        role="DOCTOR",
        actor_id="d1",
        target_id="v1",
    )

    actions = await redis_state_manager.get_night_actions(game_id)
    assert len(actions) == 2
    assert actions["MAFIA"] == {"actor_id": "m1", "target_id": "v1"}
    assert actions["DOCTOR"] == {"actor_id": "d1", "target_id": "v1"}

    # Clear actions
    await redis_state_manager.clear_night_actions(game_id)
    cleared = await redis_state_manager.get_night_actions(game_id)
    assert cleared == {}


@pytest.mark.asyncio
async def test_redis_deadline_scheduler(redis_state_manager: RedisStateManager) -> None:
    # Schedule two deadlines: one expired (epoch 100), one future (epoch 300)
    await redis_state_manager.schedule_deadline(
        game_id="g1",
        phase="NIGHT",
        round_number=1,
        version=2,
        deadline_epoch=100.0,
    )
    await redis_state_manager.schedule_deadline(
        game_id="g2",
        phase="DISCUSSION",
        round_number=1,
        version=3,
        deadline_epoch=300.0,
    )

    # Check due deadlines as of epoch 150
    due = await redis_state_manager.get_due_deadlines(current_epoch=150.0)
    assert len(due) == 1
    assert due[0]["game_id"] == "g1"
    assert due[0]["phase"] == "NIGHT"
    assert due[0]["version"] == 2

    # Remove the handled deadline
    await redis_state_manager.remove_deadline(due[0]["member"])
    assert await redis_state_manager.get_due_deadlines(current_epoch=150.0) == []

    # Cancel all deadlines for g2
    await redis_state_manager.cancel_deadlines_for_game("g2")
    assert await redis_state_manager.get_due_deadlines(current_epoch=500.0) == []
