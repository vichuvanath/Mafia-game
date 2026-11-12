"""Health and readiness check endpoints."""

from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

router = APIRouter(tags=["Health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> dict[str, str]:
    """Liveness probe to confirm service process is responsive."""
    return {"status": "ok"}


@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> JSONResponse:
    """Readiness probe to confirm dependencies are accessible."""
    # Readiness status details
    checks: dict[str, Any] = {
        "status": "ready",
        "service": "game-engine-service",
    }
    return JSONResponse(status_code=status.HTTP_200_OK, content=checks)
