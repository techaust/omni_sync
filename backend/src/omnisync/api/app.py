"""Application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from omnisync import __version__
from omnisync.api.middleware import SecurityMiddleware
from omnisync.api.routers import auth, health, users
from omnisync.api.spa import mount_spa
from omnisync.api.state import AppState
from omnisync.clock import Clock, SystemClock
from omnisync.db.engine import make_engine, make_sessionmaker, transaction
from omnisync.db.migrate import upgrade_to_head
from omnisync.errors import AppError, problem
from omnisync.security.ratelimit import RateLimiter
from omnisync.security.tokens import SetupToken
from omnisync.services import accounts
from omnisync.services.instance import ensure_instance
from omnisync.settings import Settings

log = logging.getLogger("omnisync")
API_PREFIX = "/api/v1"
# Every API router; tests/test_permissions.py walks this tuple and cross-checks it against OpenAPI.
API_ROUTERS = (health.router, auth.router, users.router)
PROBLEM_MEDIA_TYPE = "application/problem+json"


def _problem_response(request: Request, code: str, **kwargs: Any) -> JSONResponse:
    body = problem(
        code,
        instance=request.url.path,
        request_id=request.scope.get("state", {}).get("request_id"),
        **kwargs,
    )
    return JSONResponse(body, status_code=body["status"], media_type=PROBLEM_MEDIA_TYPE)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        response = _problem_response(request, exc.code, detail=exc.detail, errors=exc.errors)
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
                "message": err.get("msg", ""),
            }
            for err in exc.errors()
        ]
        return _problem_response(request, "validation.failed", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "resource.not_found", 405: "request.invalid"}.get(
            exc.status_code, "request.invalid"
        )
        return _problem_response(request, code, detail=str(exc.detail) if exc.detail else None)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _problem_response(request, "internal")


def _startup(state: AppState) -> None:
    upgrade_to_head(state.engine)
    with transaction(state.db_factory) as db:
        instance = ensure_instance(db, state.settings.instance_name, state.clock.now())
        state.instance_id, state.instance_name = instance.instance_id, instance.instance_name
        required = accounts.setup_required(db)
    if required:
        token = state.setup_token.generate()
        message = f"Setup required. Setup token: {token} (also written to {state.settings.setup_token_path})"
        log.warning("setup required; setup token written to %s", state.settings.setup_token_path)
        print(message, flush=True)  # must be visible on the console (FR-AUTH-01)
    else:
        state.setup_token.consume()


def create_app(settings: Settings, clock: Clock | None = None) -> FastAPI:
    clock = clock or SystemClock()
    engine = make_engine(settings.db_path)
    state = AppState(
        settings=settings,
        clock=clock,
        engine=engine,
        db_factory=make_sessionmaker(engine),
        rate_limiter=RateLimiter(clock),
        setup_token=SetupToken(settings.setup_token_path),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        _startup(state)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="OmniSync",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.omnisync = state
    _install_error_handlers(app)
    for router in API_ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    if settings.static_dir is not None and (settings.static_dir / "index.html").is_file():
        mount_spa(app, settings.static_dir)
    app.add_middleware(SecurityMiddleware, allowed_hosts=settings.allowed_hosts)
    return app
