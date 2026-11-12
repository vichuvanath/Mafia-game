"""FastAPI application entry point for Game Engine Service."""

import logging
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.games import router as games_router
from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.config import get_settings
from app.state.checkpoints import ConcurrencyConflictError


# Structured JSON logger setup
class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "game_id": getattr(record, "game_id", None),
        }
        import json

        return json.dumps({k: v for k, v in payload.items() if v is not None})


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
root_logger = logging.getLogger()
root_logger.setLevel(get_settings().LOG_LEVEL)
root_logger.handlers = [handler]
logger = logging.getLogger("game_engine")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup and shutdown hooks."""
    logger.info("Starting Game Engine Service...")
    yield
    logger.info("Shutting down Game Engine Service...")


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title="Mafia Game Engine Service",
        description="Autonomous God service driving real-time Mafia game state machine.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID and Structured Logging Middleware
    @app.middleware("http")
    async def request_context_middleware(
        request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = req_id

        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        response.headers["X-Request-ID"] = req_id
        logger.info(
            f"{request.method} {request.url.path} completed in {duration_ms:.2f}ms with status {response.status_code}",
            extra={"request_id": req_id},
        )
        return cast(Response, response)

    # Exception Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        code = f"HTTP_{exc.status_code}"
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            code = "BAD_REQUEST"
        elif exc.status_code == status.HTTP_401_UNAUTHORIZED:
            code = "UNAUTHORIZED"
        elif exc.status_code == status.HTTP_403_FORBIDDEN:
            code = "FORBIDDEN"
        elif exc.status_code == status.HTTP_404_NOT_FOUND:
            code = "NOT_FOUND"
        elif exc.status_code == status.HTTP_409_CONFLICT:
            code = "CONFLICT"

        return JSONResponse(
            status_code=exc.status_code,
            content={"code": code, "message": str(exc.detail), "request_id": req_id},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(ConcurrencyConflictError)
    async def concurrency_exception_handler(
        request: Request, exc: ConcurrencyConflictError
    ) -> JSONResponse:
        req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "CONCURRENCY_CONFLICT",
                "message": f"Version conflict for game {exc.game_id}: expected {exc.expected_version}, actual {exc.actual_version}",
                "request_id": req_id,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": "VALIDATION_ERROR",
                "message": "Invalid request payload",
                "details": exc.errors(),
                "request_id": req_id,
            },
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.exception("Unhandled server exception", extra={"request_id": req_id})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "request_id": req_id,
            },
        )

    # Include API Routers
    app.include_router(health_router)
    app.include_router(games_router)
    app.include_router(internal_router)

    return app


app = create_app()
