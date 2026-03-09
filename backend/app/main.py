"""FastAPI application factory.

Creates the app, registers middleware, mounts routers, configures lifespan
(startup/shutdown). This is the single entry point for uvicorn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import close_database, get_session_factory, run_migrations, setup_database
from app.services.agent_message_bus import init_agent_message_bus, shutdown_agent_message_bus
from app.services.ai_router import get_ai_router, shutdown_ai_router
from app.services.context_engine import init_context_engine, shutdown_context_engine
from app.services.pipeline_events import init_pipeline_events, shutdown_pipeline_events
from app.services.prompt_engine import init_prompt_engine, shutdown_prompt_engine
from app.services.token_revocation import init_revocation_store, shutdown_revocation_store

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

    # Run Alembic migrations on startup (creates schemas + tables automatically)
    await run_migrations()
    logger.info("alembic_migrations_applied")

    # AI services (non-fatal if Valkey is unavailable at startup)
    # R29-FIX-6: Use _sanitize_error for startup exceptions — Valkey/DB
    # connection errors contain URLs with embedded passwords.
    from app.services.ai_router import _sanitize_error

    try:
        await init_context_engine()
        logger.info("context_engine_ready")
    except Exception as exc:
        logger.warning("context_engine_deferred", error=_sanitize_error(exc))

    try:
        await init_agent_message_bus()
        logger.info("agent_message_bus_ready")
    except Exception as exc:
        logger.warning("agent_message_bus_deferred", error=_sanitize_error(exc))

    try:
        await init_pipeline_events()
        logger.info("pipeline_events_ready")
    except Exception as exc:
        logger.warning("pipeline_events_deferred", error=_sanitize_error(exc))

    try:
        await init_prompt_engine()
        logger.info("prompt_engine_ready")
    except Exception as exc:
        logger.warning("prompt_engine_deferred", error=_sanitize_error(exc))

    # PHASE-1: Token revocation store (Valkey-backed JWT blocklist)
    # R34-FIX: Check the return value of init_revocation_store(). In production,
    # fail hard if Valkey is unavailable — operating without token revocation means
    # logout and /logout-all are silently broken, and compromised tokens cannot
    # be killed. In dev/staging, log an error and degrade gracefully.
    try:
        store = await init_revocation_store()
        if store is None:
            if settings.is_production:
                raise RuntimeError(
                    "Token revocation store (Valkey) is unavailable. "
                    "Cannot start in production without working token revocation — "
                    "logout and security lockouts would silently fail. "
                    "Ensure Valkey is running and VALKEY_URL is correct."
                )
            logger.error("revocation_store_init_returned_none",
                         hint="Valkey unavailable — token revocation is NOT working")
        else:
            logger.info("revocation_store_ready")
    except RuntimeError:
        raise  # Re-raise production startup failures
    except Exception as exc:
        if settings.is_production:
            raise RuntimeError(
                f"Token revocation store failed to initialize in production: {_sanitize_error(exc)}"
            ) from exc
        logger.warning("revocation_store_deferred", error=_sanitize_error(exc))

    # HIGH-2 FIX: General Valkey health check — fail-fast in production instead
    # of surfacing a confusing connection error deep inside the first pipeline run.
    try:
        from app.services.valkey_pool import get_valkey_client
        _valkey = await get_valkey_client()
        await _valkey.ping()
        logger.info("valkey_health_check_passed")
    except Exception as _valkey_exc:
        if settings.is_production:
            raise RuntimeError(
                "Valkey/Redis is required but not reachable. "
                f"Set REDIS_URL or provision Cloud Memorystore. Error: {_valkey_exc}"
            )
        logger.error(
            "valkey_health_check_failed",
            error=str(_valkey_exc)[:200] if hasattr(_valkey_exc, '__str__') else "unknown",
            hint="Valkey unavailable — pipeline runs, message bus, and context engine will fail.",
        )

    # CRITICAL-1 FIX: Warn at startup if no deployment provider is configured.
    # All pipelines will run in simulation mode and deliver fake URLs.
    import os as _os
    if settings.is_production and not any([
        _os.environ.get("RAILWAY_TOKEN"),
        _os.environ.get("VERCEL_TOKEN"),
        settings.railway_token,
        settings.vercel_token,
    ]):
        logger.warning(
            "no_deployment_provider_configured",
            hint=(
                "No RAILWAY_TOKEN or VERCEL_TOKEN configured. "
                "All deployments will run in simulation mode."
            ),
        )

    # WORKER-PROD-FIX: Production requires an external worker (async or Celery).
    # In-process execution (asyncio.create_task) shares crash domain with API.
    _has_external_worker = (
        settings.worker_type == "async" or settings.use_celery
    )
    if settings.is_production and not _has_external_worker:
        raise RuntimeError(
            "Production requires an external pipeline worker. "
            "Set WORKER_TYPE=async (recommended) or USE_CELERY=true. "
            "In-process execution shares memory + crash domain with the API server."
        )
    # Celery is deprecated — warn if still enabled
    if settings.use_celery:
        logger.warning(
            "celery_dispatch_deprecated",
            msg=(
                "Celery pipeline dispatch is deprecated. "
                "Set WORKER_TYPE=async for the native asyncio worker "
                "(better performance, no thread blocking). "
                "Celery support will be removed in a future release."
            ),
        )

    # AI Router (lazy-init, just ensure it's importable)
    _ = get_ai_router()
    logger.info("ai_router_ready")

    # Register all agents (import agent modules to trigger register_agent() calls)
    try:
        from app.agents import register_all_agents
        agent_count = register_all_agents()
        logger.info("agents_ready", count=agent_count)
    except Exception as exc:
        logger.warning("agent_registration_failed", error=_sanitize_error(exc))

    # Crash recovery: find and mark interrupted pipelines
    try:
        from app.services.pipeline import get_orchestrator
        orch = get_orchestrator()
        interrupted = await orch.recover_interrupted_runs()
        if interrupted:
            logger.warning("crash_recovery", interrupted_runs=interrupted)
        else:
            logger.info("crash_recovery_none_found")
    except Exception as exc:
        logger.warning("crash_recovery_skipped", error=_sanitize_error(exc))

    logger.info("nexsidi_started")

    yield

    # --- Shutdown ---
    logger.info("shutting_down_nexsidi")

    # R20-FIX: Cancel in-flight pipeline tasks BEFORE closing resources.
    # Previously, shutdown closed httpx/DB while _active_tasks still had
    # running pipelines, causing PoolError/OperationalError. Pipelines would
    # silently corrupt (partial writes) or stay stuck in RUNNING status.
    import asyncio
    try:
        from app.routers.pipeline import _active_tasks
        if _active_tasks:
            logger.warning("cancelling_active_pipelines", count=len(_active_tasks))
            for task in list(_active_tasks):
                task.cancel()
            await asyncio.gather(*_active_tasks, return_exceptions=True)
            _active_tasks.clear()
    except ImportError:
        pass  # Pipeline router not loaded

    await shutdown_ai_router()
    await shutdown_context_engine()
    await shutdown_agent_message_bus()
    await shutdown_pipeline_events()
    await shutdown_prompt_engine()
    await shutdown_revocation_store()

    # DEFERRED-FIX-10: Close shared Valkey pool AFTER all services that use it.
    from app.services.valkey_pool import shutdown_valkey_client
    await shutdown_valkey_client()

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
        # H13-FIX: Also hide OpenAPI JSON schema in production
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # --- Security headers middleware ---
    from starlette.middleware.base import BaseHTTPMiddleware

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            # R29-FIX-11: OWASP recommends "0" — the XSS Auditor was removed
            # from Chrome 78+ and was itself exploitable via response-splitting.
            # Consistent with security_guardian.py's REQUIRED_SECURITY_HEADERS.
            response.headers["X-XSS-Protection"] = "0"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            # R30-FIX-12: Add Content-Security-Policy and Permissions-Policy.
            # CSP prevents XSS by restricting script/style sources.
            # Permissions-Policy disables unnecessary browser features (camera,
            # microphone, geolocation) to reduce attack surface.
            # R36-FIX: connect-src 'self' was too restrictive. The frontend
            # needs to reach WebSocket endpoints (wss://) and the CORS origins
            # configured in settings. Without these, browser blocks API calls
            # from the frontend when it's served from a different origin
            # (e.g., localhost:3000 → localhost:8000).
            connect_sources = "'self'"
            if settings.cors_origins:
                # R37-FIX: Validate origins before injecting into CSP header.
                # Malicious CORS origin like "https://evil.com; script-src 'unsafe-eval'"
                # would inject arbitrary CSP directives via string concatenation.
                import re as _re
                _SAFE_ORIGIN = _re.compile(r'^https?://[a-zA-Z0-9._:/-]+$')
                # Add CORS origins as allowed connect-src targets
                # Convert http → ws, https → wss for WebSocket support
                for origin in settings.cors_origins:
                    if not _SAFE_ORIGIN.match(origin):
                        continue  # Skip unsafe origins
                    connect_sources += f" {origin}"
                    if origin.startswith("https://"):
                        connect_sources += f" wss://{origin[8:]}"
                    elif origin.startswith("http://"):
                        connect_sources += f" ws://{origin[7:]}"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "font-src 'self'; "
                f"connect-src {connect_sources}; "
                "frame-ancestors 'none'"
            )
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=(), "
                "payment=(), usb=(), magnetometer=()"
            )
            if settings.is_production:
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            return response

    # --- CORS ---
    # R37-FIX: CORS added FIRST (inner). SecurityHeaders added SECOND (outer).
    # Starlette middleware is LIFO — last-added runs first on request.
    # SecurityHeaders as outer ensures security headers appear on ALL responses,
    # including CORS preflight OPTIONS responses (which CORS middleware returns early).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    app.add_middleware(SecurityHeadersMiddleware)

    # --- Global exception handler (logs unhandled errors) ---
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        import re as _re
        import traceback
        # H17-FIX: Sanitize URL (strip query params that may contain tokens/keys)
        safe_path = str(request.url).split("?")[0]
        # REVIEW-FIX: Sanitize exception message to strip credentials.
        # str(exc) for DB errors often contains full connection URIs
        # (postgresql://user:password@host/db), and Google API errors
        # can contain API keys in URLs.
        error_msg = str(exc)[:500]
        # Strip DB connection strings: postgresql://user:pass@host/db
        error_msg = _re.sub(
            r"(postgresql|mysql|sqlite|redis|valkey)://[^\s\"']+",
            r"\1://***REDACTED***",
            error_msg,
        )
        # Strip API key patterns
        error_msg = _re.sub(
            r"(key|token|secret|password|credential)[=:]\s*['\"]?[A-Za-z0-9_\-./]{8,}",
            r"\1=***REDACTED***",
            error_msg,
            flags=_re.IGNORECASE,
        )
        logger.error(
            "unhandled_exception",
            path=safe_path,
            method=request.method,
            error_type=type(exc).__name__,
            error=error_msg,
        )
        # Full traceback to stderr only (not structured logs shipped to SaaS)
        if not settings.is_production:
            traceback.print_exc()
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # --- Health check (no auth required) ---
    _VERSION = "2.0.0"

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Liveness check — always responds if the process is alive."""
        # AUDIT-T2-7: Don't leak version on unauthenticated endpoint — aids attacker recon
        return {"status": "healthy"}

    @app.get("/health/ready", tags=["system"])
    async def readiness_check() -> dict[str, Any]:
        """Readiness check — verifies DB connectivity."""
        checks: dict[str, str] = {}
        try:
            factory = get_session_factory()
            async with factory() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            # R11-FIX: Don't leak exception class names to unauthenticated callers.
            # Log the actual error server-side for debugging.
            # R29-FIX-6: Sanitize — DB connection errors contain passwords.
            from app.services.ai_router import _sanitize_error as _sanitize
            logger.warning("readiness_check_failed", error=_sanitize(exc))
            checks["database"] = "error"

        overall = "healthy" if all(v == "ok" for v in checks.values()) else "degraded"
        # R37-FIX: Return 503 when degraded so load balancers stop routing traffic
        # to this instance. Previously always returned 200, defeating health checks.
        from fastapi.responses import JSONResponse
        status_code = 200 if overall == "healthy" else 503
        return JSONResponse(
            status_code=status_code,
            content={"status": overall, "version": _VERSION, "checks": checks},
        )

    # --- Mount routers ---
    from app.routers.auth import router as auth_router
    from app.routers.password_reset import router as password_reset_router
    from app.routers.users import router as users_router
    from app.routers.projects import router as projects_router
    from app.routers.pipeline import router as pipeline_router
    from app.routers.chat import router as chat_router
    from app.routers.notifications import router as notifications_router
    from app.routers.websocket import router as ws_router
    from app.routers.analytics import router as analytics_router  # ANALYTICS-FIX
    from app.routers.webhooks import router as webhooks_router
    from app.routers.templates import router as templates_router  # TEMPLATE-FIX
    from app.routers import api_keys, teams, billing
    from app.routers.v2 import v2_router  # Directive v2 API

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    # Password reset + email verification routes share the /api/v1/auth prefix
    app.include_router(password_reset_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
    app.include_router(projects_router, prefix="/api/v1/projects", tags=["projects"])
    app.include_router(pipeline_router, prefix="/api/v1/pipeline", tags=["pipeline"])
    app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["notifications"])
    app.include_router(ws_router, prefix="/api/v1", tags=["websocket"])
    app.include_router(analytics_router, prefix="/api/v1", tags=["analytics"])  # ANALYTICS-FIX
    app.include_router(webhooks_router, prefix="/api/v1/webhooks", tags=["webhooks"])
    app.include_router(templates_router, prefix="/api/v1/templates", tags=["templates"])  # TEMPLATE-FIX
    app.include_router(api_keys.router, prefix="/api/v1/api-keys", tags=["api-keys"])
    app.include_router(teams.router, prefix="/api/v1/teams", tags=["teams"])
    app.include_router(billing.router, prefix="/api/v1/billing", tags=["billing"])

    # v2 API — new directive features (DevBox, steering, SDD)
    # v2_router already has prefix="/api/v2" set internally
    app.include_router(v2_router)

    return app


# uvicorn entry point: uvicorn app.main:app
app = create_app()
