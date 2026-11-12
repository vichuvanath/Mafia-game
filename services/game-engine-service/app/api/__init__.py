"""API routers package."""

from app.api.games import router as games_router
from app.api.health import router as health_router
from app.api.internal import router as internal_router

__all__ = ["games_router", "health_router", "internal_router"]
