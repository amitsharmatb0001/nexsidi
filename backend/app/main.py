"""FastAPI application factory.

Creates the app, registers middleware, mounts routers, configures lifespan
(startup/shutdown). This is the single entry point for uvicorn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import close_database, create_schemas, setup_database

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown logic.

    Startup: initialize database, create schemas (dev only), connect Valkey.
    Shutdown: close database connections, disconnect Valkey.
    """
    settings = get_settings()

    # --- Startup ---
    logger.info("starting_nexsidi", environment=settings.environment)

    engine, _ = setup_database(settings)

    if not settings.is_production:
        await create_schemas(engine)
        logger.info("schemas_created")

    logger.info("nexsidi_started")

    yield

    # --- Shutdown ---
    logger.info("shutting_down_nexsidi")
    await close_database()
    logger.info("nexsidi_stopped")


def create_app() -> FastAPI:
    """Application factory. Returns configured FastAPI instance."""
    settings = get_settings()

    app = FastAPI(
        title="NexSidi",
        description="AI-powered software delivery platform",
        version="2.0.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # --- CORS ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    # --- Health check (no auth required) ---
    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy", "version": "2.0.0"}

    # --- Mount routers ---
    from app.routers.auth import router as auth_router

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])

    return app


# uvicorn entry point: uvicorn app.main:app
app = create_app()
