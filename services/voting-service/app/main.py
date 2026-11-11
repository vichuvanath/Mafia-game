"""FastAPI application factory and middleware configuration."""

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.votes import router as votes_router
from app.config import get_settings
from app.schemas.errors import ErrorResponse, VotingAppException

settings = get_settings()

# Configure basic structured logging
LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] [req:%(request_id)s] [game:%(game_id)s] %(name)s: %(message)s"
)
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format=LOG_FORMAT,
)
logger = logging.getLogger("voting_service")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown routines."""
    import asyncio

    from app.events.outbox_worker import OutboxWorker
    from app.repositories.database import close_db
    from app.repositories.redis_client import close_redis
    from app.services.recovery import run_startup_recovery
    from app.services.scheduler import get_deadline_scheduler

    logger.info(
        "Voting Service starting up: executing startup recovery",
        extra={"request_id": "startup", "game_id": "none"},
    )
    try:
        await run_startup_recovery()
    except Exception as exc:
        logger.warning("Startup recovery warning: %s", str(exc))

    outbox_worker = OutboxWorker()
    outbox_task = asyncio.create_task(outbox_worker.run_loop(poll_interval_seconds=1.0))

    scheduler = get_deadline_scheduler()
    scheduler_task = asyncio.create_task(scheduler.run_scheduler_loop(poll_interval_seconds=1.0))

    yield

    logger.info(
        "Voting Service shutting down",
        extra={"request_id": "shutdown", "game_id": "none"},
    )
    outbox_worker.stop()
    scheduler.stop()
    outbox_task.cancel()
    scheduler_task.cancel()
    with suppress(Exception):
        await asyncio.gather(outbox_task, scheduler_task, return_exceptions=True)
    await close_redis()
    await close_db()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="Mafia Voting & Elimination Service",
        description="Vote collection, tallying, and result proposition service",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware: Request ID and structured request tracking
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = req_id

        # Extract game_id from path if present (e.g. /games/{game_id}/...)
        path_parts = request.url.path.strip("/").split("/")
        game_id = "none"
        if "games" in path_parts:
            idx = path_parts.index("games")
            if idx + 1 < len(path_parts):
                game_id = path_parts[idx + 1]
        request.state.game_id = game_id

        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start_time

        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time-Ms"] = f"{process_time * 1000:.2f}"
        return response

    # Exception Handler: Domain Application Errors
    @app.exception_handler(VotingAppException)
    async def voting_app_exception_handler(
        request: Request, exc: VotingAppException
    ) -> JSONResponse:
        req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        error_body = ErrorResponse(
            code=exc.code,
            message=exc.message,
            request_id=req_id,
        )
        return JSONResponse(status_code=exc.status_code, content=error_body.model_dump())

    # Exception Handler: Pydantic Validation Errors
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        first_error = exc.errors()[0] if exc.errors() else {"msg": "Validation error"}
        msg = f"{first_error.get('loc', ('body',))[-1]}: {first_error.get('msg', 'Invalid input')}"
        error_body = ErrorResponse(
            code="VALIDATION_ERROR",
            message=msg,
            request_id=req_id,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_body.model_dump(),
        )

    # Exception Handler: Starlette / FastAPI HTTP Exceptions
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        error_body = ErrorResponse(
            code="HTTP_ERROR" if exc.status_code != 404 else "NOT_FOUND",
            message=str(exc.detail),
            request_id=req_id,
        )
        return JSONResponse(status_code=exc.status_code, content=error_body.model_dump())

    # Exception Handler: Unhandled Exceptions
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.exception(
            "Unhandled server error: %s",
            str(exc),
            extra={
                "request_id": req_id,
                "game_id": getattr(request.state, "game_id", "none"),
            },
        )
        error_body = ErrorResponse(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal error occurred.",
            request_id=req_id,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body.model_dump(),
        )

    # Routers
    app.include_router(health_router)
    app.include_router(internal_router)
    app.include_router(votes_router)

    return app


app = create_app()
