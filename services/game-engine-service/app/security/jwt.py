"""JWT authentication and verification utilities."""

from typing import Any

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import get_settings

security_bearer = HTTPBearer(auto_error=False)


class AuthenticatedUser(BaseModel):
    """Extracted claims for authenticated player."""

    player_id: str
    username: str = ""
    room_id: str | None = None
    claims: dict[str, Any] = {}


def decode_jwt_token(token: str) -> dict[str, Any]:
    """Decode and verify JWT signature and expiration."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        return payload
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def get_current_player(
    credentials: HTTPAuthorizationCredentials | None = Security(security_bearer),
) -> AuthenticatedUser:
    """FastAPI dependency for authenticating player requests via JWT."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_jwt_token(credentials.credentials)
    player_id = payload.get("sub") or payload.get("player_id")
    if not player_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject or player_id",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthenticatedUser(
        player_id=str(player_id),
        username=str(payload.get("username", "")),
        room_id=payload.get("room_id"),
        claims=payload,
    )
