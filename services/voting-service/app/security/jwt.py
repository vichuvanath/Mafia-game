"""JWT verification and player identity extraction."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Header, status
from jwt.exceptions import PyJWTError

from app.config import get_settings
from app.schemas.errors import VotingAppException

settings = get_settings()


def create_access_token(
    user_id: str,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Helper to generate a signed JWT (used by test suites and auth mocking)."""
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(hours=1))
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": now,
        "exp": expire,
    }
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    if settings.JWT_ISSUER:
        payload["iss"] = settings.JWT_ISSUER
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return str(token)


async def get_current_user_id(
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    """Extract and verify user_id from Bearer JWT.

    Raises:
        VotingAppException with 401 status if Authorization header is missing,
        malformed, or has an invalid/expired signature.
    """
    if not authorization:
        raise VotingAppException(
            code="UNAUTHORIZED",
            message="Authorization header is required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise VotingAppException(
            code="UNAUTHORIZED",
            message="Authorization header must follow Bearer <token> format.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = parts[1]
    try:
        decode_kwargs: dict[str, Any] = {
            "algorithms": [settings.JWT_ALGORITHM],
        }
        if settings.JWT_AUDIENCE:
            decode_kwargs["audience"] = settings.JWT_AUDIENCE
        if settings.JWT_ISSUER:
            decode_kwargs["issuer"] = settings.JWT_ISSUER

        payload = jwt.decode(token, settings.JWT_SECRET, **decode_kwargs)
        user_id = payload.get("sub")
        if not user_id:
            raise VotingAppException(
                code="UNAUTHORIZED",
                message="Token payload is missing 'sub' subject claim.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return str(user_id)

    except PyJWTError as exc:
        raise VotingAppException(
            code="UNAUTHORIZED",
            message=f"Invalid or expired JWT: {exc!s}",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc
