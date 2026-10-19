"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import admin, attendance, auth, biometrics, events, health, org, users
from app.biometrics.runtime import build_runtime
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodySizeLimitMiddleware, RequestIdMiddleware, SecurityHeadersMiddleware
from app.db.session import dispose_engine, init_engine
from app.services.live import broadcaster

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_environment()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        broadcaster.configure(settings.database_url.get_secret_value())
        runtime = build_runtime(settings)
        app.state.biometrics = runtime
        for s in runtime.provider.statuses():
            log = logger.info if s.available else logger.warning
            log(
                "model status",
                extra={"role": s.role, "model_id": s.model_id, "available": s.available, "error": s.error},
            )
        if runtime.provider.info.is_test_fixture:
            logger.warning("TEST FIXTURE vision provider active - biometric results are not real")
        if settings.liveness_challenge == "passive":
            logger.warning(
                "passive liveness: no head-movement challenge; liveness relies on PAD and temporal checks only"
            )
        try:
            yield
        finally:
            broadcaster.stop()
            runtime.shutdown()
            dispose_engine()

    docs = not settings.is_production
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs" if docs else None,
        redoc_url=None,
        openapi_url=f"{settings.api_prefix}/openapi.json" if docs else None,
    )
    install_error_handlers(app)

    api = APIRouter(prefix=settings.api_prefix)
    for module in (health, auth, users, org, biometrics, attendance, admin, events):
        api.include_router(module.router)
    app.include_router(api)

    # Middleware order: the last added runs first (outermost).
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", settings.csrf_header_name, "Idempotency-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.is_production,
        docs_paths=(f"{settings.api_prefix}/docs", f"{settings.api_prefix}/openapi.json"),
    )
    app.add_middleware(RequestIdMiddleware)
    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory app.main:app_factory``."""
    return create_app()
