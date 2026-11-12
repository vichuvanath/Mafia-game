"""Role Service client interface and HTTP implementation."""

from typing import Protocol

import httpx
from pydantic import BaseModel

from app.config.rules import Role


class RoleAssignment(BaseModel):
    """Mapping of player ID to assigned role."""

    player_id: str
    role: Role


class RoleServiceClient(Protocol):
    """Protocol for interacting with external Role Service."""

    async def request_role_assignment(
        self,
        game_id: str,
        player_ids: list[str],
    ) -> dict[str, Role]:
        """Request role assignment for a game room."""
        ...


class HttpRoleServiceClient:
    """HTTP client communicating with the real Role Service."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def request_role_assignment(
        self,
        game_id: str,
        player_ids: list[str],
    ) -> dict[str, Role]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            resp = await client.post(
                "/internal/roles/assign",
                json={"game_id": game_id, "player_ids": player_ids},
            )
            resp.raise_for_status()
            data = resp.json()
            return {item["player_id"]: Role(item["role"]) for item in data.get("assignments", [])}
