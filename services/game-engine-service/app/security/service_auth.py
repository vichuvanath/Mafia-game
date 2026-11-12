"""Internal service-to-service authentication."""

import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings


def verify_service_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Verify internal service shared secret header."""
    settings = get_settings()
    if not x_internal_token or not hmac.compare_digest(
        x_internal_token, settings.INTERNAL_SERVICE_TOKEN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Invalid or missing internal service token",
        )
