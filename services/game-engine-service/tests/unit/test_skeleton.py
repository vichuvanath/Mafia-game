"""Smoke unit test verifying skeleton imports and configurations."""

import pytest

from app.config import get_settings
from app.config.rules import GameRulesSettings, Phase, TieRule
from app.engine.state_machine import (
    IllegalPhaseTransitionError,
    can_transition,
    validate_transition,
)


def test_rules_configuration_defaults() -> None:
    rules = GameRulesSettings()
    assert rules.min_players == 5
    assert rules.max_players == 12
    assert rules.doctor_can_self_protect is True
    assert rules.tie_rule == TieRule.REVOTE_AMONG_TIED
    assert rules.reveal_role_on_death is True


def test_app_settings_resolution() -> None:
    settings = get_settings()
    assert settings.APP_NAME == "game-engine-service"
    assert settings.REDIS_KEY_PREFIX == "game:"


def test_skeleton_state_machine_smoke() -> None:
    assert can_transition(Phase.ROLE_ASSIGNMENT, Phase.NIGHT) is True
    assert can_transition(Phase.ROLE_ASSIGNMENT, Phase.DISCUSSION) is False

    with pytest.raises(IllegalPhaseTransitionError):
        validate_transition(Phase.ROLE_ASSIGNMENT, Phase.DISCUSSION)
