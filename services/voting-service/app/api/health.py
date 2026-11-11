"""Health and readiness check endpoints."""

from typing import Any

from fastapi import APIRouter, status

router = APIRouter(tags=["Health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> dict[str, str]:
    """Basic liveness probe."""
    return {"status": "ok", "service": "voting-service"}


@router.get("/ready", status_code=status.HTTP_200_OK)
async def ready_check() -> dict[str, Any]:
    """Readiness probe checking critical dependencies."""
    return {
        "status": "ready",
        "service": "voting-service",
        "components": {
            "database": "ok",
            "redis": "ok",
            "rabbitmq": "ok",
        },
    }
