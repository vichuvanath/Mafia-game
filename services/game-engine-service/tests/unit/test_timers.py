"""Unit tests for timer calculations and deterministic clocks."""

from datetime import UTC, datetime, timedelta

from app.engine.recovery import GameRecoveryManager
from app.engine.timers import SystemClock, calculate_phase_deadline, is_deadline_passed
from tests.conftest import FixedClock


def test_system_clock() -> None:
    clock = SystemClock()
    now1 = clock.now()
    now2 = clock.now()
    assert now2 >= now1
    assert now1.tzinfo is not None


def test_calculate_phase_deadline_system_clock() -> None:
    deadline = calculate_phase_deadline(duration_seconds=10, clock=None)
    assert not is_deadline_passed(deadline, clock=None)


def test_calculate_phase_deadline() -> None:
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    clock = FixedClock(base)

    deadline = calculate_phase_deadline(duration_seconds=30, clock=clock)
    assert deadline == base + timedelta(seconds=30)
    assert not is_deadline_passed(deadline, clock=clock)

    # Advance clock past deadline
    clock.advance(seconds=35)
    assert is_deadline_passed(deadline, clock=clock)


def test_game_recovery_manager_evaluation() -> None:
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    clock = FixedClock(base)
    recovery = GameRecoveryManager(clock)

    # 1. No deadline
    assert recovery.evaluate_game_recovery_action({"phase_deadline": None}) == "NOOP"

    # 2. Deadline in future -> REARM_TIMER
    future_deadline = base + timedelta(seconds=60)
    assert (
        recovery.evaluate_game_recovery_action({"phase_deadline": future_deadline}) == "REARM_TIMER"
    )

    # 3. Deadline passed -> EXPIRED_ADVANCE
    past_deadline = base - timedelta(seconds=10)
    assert (
        recovery.evaluate_game_recovery_action({"phase_deadline": past_deadline})
        == "EXPIRED_ADVANCE"
    )
