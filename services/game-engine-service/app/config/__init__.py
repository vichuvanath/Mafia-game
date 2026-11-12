"""Configuration package."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.rules import (
    AbstentionHandling,
    GameRulesSettings,
    Phase,
    Role,
    TieRule,
    Winner,
)


class Settings(BaseSettings):
    """Overall service configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General App Config
    APP_NAME: str = "game-engine-service"
    APP_ENV: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://game_engine_user:game_engine_pass@localhost:5432/game_engine_db"
    )
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_KEY_PREFIX: str = "game:"

    # RabbitMQ
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
    RABBITMQ_EXCHANGE: str = "mafia.events"
    RABBITMQ_QUEUE_PREFIX: str = "game_engine"

    # Security
    INTERNAL_SERVICE_TOKEN: str = "dev-secret-internal-service-token"
    JWT_SECRET_KEY: str = "dev-jwt-secret-key-32-chars-minimum-required!"
    JWT_ALGORITHM: str = "HS256"

    # Clients
    CLIENT_MODE: Literal["fake", "http"] = "fake"
    ROLE_SERVICE_URL: str = "http://localhost:8001"
    VOTING_SERVICE_URL: str = "http://localhost:8002"

    # Game Rules
    rules: GameRulesSettings = Field(default_factory=lambda: GameRulesSettings())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


__all__ = [
    "AbstentionHandling",
    "GameRulesSettings",
    "Phase",
    "Role",
    "Settings",
    "TieRule",
    "Winner",
    "get_settings",
]
