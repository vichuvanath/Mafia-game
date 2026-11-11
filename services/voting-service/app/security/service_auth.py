"""Service token authentication for internal endpoints."""

import hmac

from fastapi import Header, status

from app.config import get_settings
from app.schemas.errors import VotingAppException

settings = get_settings()


async def verify_service_token(
    x_service_token: str | None = Header(None, alias="X-Service-Token"),
) -> None:
    """Validate internal service-to-service communication token.

    Raises:
        VotingAppException with 401 status if the token is missing or invalid.
    """
    if not x_service_token:
        raise VotingAppException(
            code="UNAUTHORIZED",
            message="Missing required X-Service-Token header.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(x_service_token, settings.SERVICE_TOKEN):
        raise VotingAppException(
            code="UNAUTHORIZED",
            message="Invalid service token.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
