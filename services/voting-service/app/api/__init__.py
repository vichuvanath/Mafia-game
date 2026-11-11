"""API routers and dependencies."""

from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.votes import router as votes_router

__all__ = [
    "health_router",
    "internal_router",
    "votes_router",
]
