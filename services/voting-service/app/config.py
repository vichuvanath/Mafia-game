"""Application configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Voting Service settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_ENV: str = Field(default="development")
    APP_NAME: str = Field(default="voting-service")
    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8000)
    LOG_LEVEL: str = Field(default="INFO")

    # Security
    SERVICE_TOKEN: str = Field(default="dev-secret-internal-service-token-change-in-production")
    JWT_SECRET: str = Field(default="dev-jwt-secret-shared-key-for-test-only-change-in-production")
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_AUDIENCE: str | None = Field(default=None)
    JWT_ISSUER: str | None = Field(default=None)

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://voting_user:voting_password@localhost:5432/voting_db"
    )
    DATABASE_POOL_SIZE: int = Field(default=10)
    DATABASE_MAX_OVERFLOW: int = Field(default=5)
    DATABASE_ECHO: bool = Field(default=False)

    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    REDIS_KEY_PREFIX: str = Field(default="voting:")

    # RabbitMQ
    RABBITMQ_URL: str = Field(default="amqp://guest:guest@localhost:5672/")
    RABBITMQ_EXCHANGE: str = Field(default="mafia.events")

    # Rate Limiting
    RATE_LIMIT_VOTES_PER_MINUTE: int = Field(default=60)


def get_settings() -> Settings:
    return Settings()
