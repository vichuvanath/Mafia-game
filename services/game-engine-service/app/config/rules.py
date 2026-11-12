"""Game rules configuration module.

Defines all configurable constants and settings for the Mafia Game Engine:
- Player counts (min, max)
- Phase durations
- Doctor self-protection
- Voting and tie resolution rules
- Abstention rules
- Role reveal on death
- Disconnection grace period
"""

from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Role(str, Enum):
    """Supported player roles in the Mafia game."""

    MAFIA = "MAFIA"
    DOCTOR = "DOCTOR"
    VILLAGER = "VILLAGER"


class Phase(str, Enum):
    """Game phases according to state machine."""

    ROLE_ASSIGNMENT = "ROLE_ASSIGNMENT"
    NIGHT = "NIGHT"
    NIGHT_RESOLUTION = "NIGHT_RESOLUTION"
    DAY = "DAY"
    DISCUSSION = "DISCUSSION"
    VOTING = "VOTING"
    ELIMINATION = "ELIMINATION"
    GAME_OVER = "GAME_OVER"


class Winner(str, Enum):
    """Possible game outcomes / winners."""

    MAFIA = "MAFIA"
    VILLAGERS = "VILLAGERS"


class TieRule(str, Enum):
    """Strategy for resolving ties in day voting."""

    REVOTE_AMONG_TIED = "REVOTE_AMONG_TIED"
    NO_ELIMINATION = "NO_ELIMINATION"


class AbstentionHandling(str, Enum):
    """Strategy for handling missing votes at voting deadline."""

    COUNT_AS_ABSTAIN = "COUNT_AS_ABSTAIN"
    IGNORE = "IGNORE"


class GameRulesSettings(BaseSettings):
    """Configurable game rules for the Mafia engine."""

    model_config = SettingsConfigDict(
        env_prefix="GAME_RULES__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Player constraints
    min_players: int = Field(default=5, ge=3, le=50, description="Minimum players to start")
    max_players: int = Field(default=12, ge=3, le=100, description="Maximum players per room")

    # Phase durations (in seconds)
    role_assignment_timeout_seconds: int = Field(
        default=30, ge=1, description="Timeout waiting for Role Service"
    )
    night_phase_duration_seconds: int = Field(
        default=30, ge=1, description="Duration of night action submission"
    )
    night_resolution_duration_seconds: int = Field(
        default=5, ge=1, description="Night resolution display duration"
    )
    day_phase_duration_seconds: int = Field(
        default=5, ge=1, description="Day announcement phase duration"
    )
    discussion_phase_duration_seconds: int = Field(
        default=45, ge=1, description="Day discussion duration"
    )
    voting_phase_duration_seconds: int = Field(
        default=30, ge=1, description="Day voting phase duration"
    )
    elimination_phase_duration_seconds: int = Field(
        default=5, ge=1, description="Elimination announcement duration"
    )

    # Rule toggles
    doctor_can_self_protect: bool = Field(
        default=True, description="Whether Doctor can select self for protection"
    )
    tie_rule: TieRule = Field(
        default=TieRule.REVOTE_AMONG_TIED, description="Tie resolution strategy"
    )
    max_revotes: int = Field(
        default=1, ge=0, description="Max revote rounds before defaulting to no elimination"
    )
    abstention_handling: AbstentionHandling = Field(
        default=AbstentionHandling.COUNT_AS_ABSTAIN, description="Handling of missing votes"
    )
    reveal_role_on_death: bool = Field(
        default=True, description="Whether dead player's role is publicly revealed"
    )

    # Disconnect handling
    disconnect_grace_period_seconds: int = Field(
        default=60,
        ge=5,
        description="Grace period before treating disconnect as eliminated/forfeit",
    )
