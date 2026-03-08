"""Native asyncio pipeline worker.

Reads pipeline tasks from a Valkey list (BLPOP), processes them using the
existing async pipeline orchestrator, and writes results back.

Advantages over Celery + asyncio:
- True async lifecycle: startup -> process tasks -> shutdown
- DB/Redis connection pools live naturally across tasks
- Background services (heartbeats, health checks) run natively
- No prefork/gevent/eventlet worker model complexity

Configuration:
    Set ``worker_type=async`` in settings to enable this worker.
    Start: ``python -m app.workers.async_worker``

Task format (Valkey list ``nexsidi:pipeline_tasks``):
    JSON dict with keys: ``run_id``, ``project_id``, ``organization_id``,
    ``user_id``, ``execution_mode``, ``requirements``, ``action`` ("run"
    or "resume").

I5-FIX: Native async alternative to Celery prefork + persistent event loop.
"""

from __future__ import annotations

import asyncio
import json
import signal
from typing import Any

import structlog

logger = structlog.get_logger("nexsidi.async_worker")

TASK_QUEUE_KEY = "nexsidi:pipeline_tasks"
RESULT_KEY_PREFIX = "nexsidi:pipeline_result:"
RESULT_TTL = 86400  # 24 hours

_shutdown_event: asyncio.Event | None = None


# ── Service lifecycle ──────────────────────────────────────────────


async def _init_services() -> None:
    """Initialise all async services once at worker startup."""
    import importlib

    from app.config import get_settings
    from app.database import run_migrations, setup_database

    settings = get_settings()
    setup_database(settings)

    # Run migrations with advisory lock (F16-FIX already guards concurrency)
    try:
        await run_migrations()
        logger.info("database_migrations_complete")
    except Exception as exc:
        logger.warning("migration_skipped", error=str(exc)[:200])

    # Initialise services in order (same as main.py lifespan)
    _INIT_FUNCS = [
        "app.services.context_engine.init_context_engine",
        "app.services.agent_message_bus.init_agent_message_bus",
        "app.services.pipeline_events.init_pipeline_events",
        "app.services.prompt_engine.init_prompt_engine",
        "app.services.token_revocation.init_revocation_store",
    ]
    for path in _INIT_FUNCS:
        module_path, fn_name = path.rsplit(".", 1)
        try:
            mod = importlib.import_module(module_path)
            await getattr(mod, fn_name)()
            logger.info("service_initialized", service=fn_name)
        except Exception as exc:
            logger.warning("service_init_failed", service=fn_name, error=str(exc)[:200])

    # AI router (sync init)
    try:
        from app.services.ai_router import get_ai_router

        get_ai_router()
        logger.info("service_initialized", service="ai_router")
    except Exception as exc:
        logger.warning("ai_router_init_failed", error=str(exc)[:200])

    # Register all agents
    try:
        from app.agents import register_all_agents

        register_all_agents()
        logger.info("agents_registered")
    except Exception as exc:
        logger.warning("agent_registration_failed", error=str(exc)[:200])

    # Recover interrupted runs
    try:
        from app.services.pipeline import recover_interrupted_runs

        recovered = await recover_interrupted_runs()
        if recovered:
            logger.info("interrupted_runs_recovered", count=recovered)
    except Exception as exc:
        logger.warning("recovery_failed", error=str(exc)[:200])


async def _shutdown_services() -> None:
    """Gracefully shutdown all services."""
    shutdown_funcs = [
        "app.services.ai_router.shutdown_ai_router",
        "app.services.context_engine.shutdown_context_engine",
        "app.services.prompt_engine.shutdown_prompt_engine",
        "app.services.token_revocation.shutdown_revocation_store",
    ]
    for path in shutdown_funcs:
        module_path, fn_name = path.rsplit(".", 1)
        try:
            import importlib

            mod = importlib.import_module(module_path)
            await getattr(mod, fn_name)()
        except Exception:
            pass  # Non-critical — error logged upstream or handled by caller

    # Agent message bus + pipeline events
    for path in [
        "app.services.agent_message_bus.shutdown_agent_message_bus",
        "app.services.pipeline_events.shutdown_pipeline_events",
    ]:
        module_path, fn_name = path.rsplit(".", 1)
        try:
            import importlib

            mod = importlib.import_module(module_path)
            await getattr(mod, fn_name)()
        except Exception:
            pass  # Non-critical — error logged upstream or handled by caller

    # Valkey pool
    try:
        from app.services.valkey_pool import shutdown_valkey_client

        await shutdown_valkey_client()
    except Exception:
        pass  # Non-critical — error logged upstream or handled by caller

    # Database
    try:
        from app.database import close_database

        await close_database()
    except Exception:
        pass  # Non-critical — error logged upstream or handled by caller

    logger.info("services_shutdown_complete")


# ── Task processing ────────────────────────────────────────────────


async def _process_task(task_data: dict[str, Any]) -> dict[str, Any]:
    """Process a single pipeline task."""
    from app.services.pipeline import ExecutionMode, get_orchestrator

    orch = get_orchestrator()
    action = task_data.get("action", "run")
    run_id = task_data["run_id"]
    organization_id = task_data.get("organization_id", "")

    if action == "resume":
        result = await orch.resume_run(run_id, organization_id=organization_id)
        if result is None:
            return {"run_id": run_id, "status": "not_found", "error": "Run not resumable"}
        return {
            "run_id": result.run_id,
            "status": result.status.value,
            "current_stage": result.current_stage.value,
            "error": result.error,
        }

    # "run" action — load or create run
    project_id = task_data.get("project_id", "")
    user_id = task_data.get("user_id", "")
    execution_mode = task_data.get("execution_mode", "checkpoint")
    requirements = task_data.get("requirements")

    # Try to load existing run from DB (created by router before dispatch)
    run_data = await orch._persistence.find_run_by_id(
        run_id, organization_id=organization_id,
    )

    if run_data is not None:
        run = orch._persistence.rebuild_run(run_data)
        orch._active_runs[run.run_id] = run
    else:
        mode = ExecutionMode(execution_mode)
        run = orch.create_run(
            project_id=project_id,
            organization_id=organization_id,
            user_id=user_id,
            execution_mode=mode,
        )
        if requirements:
            run.context["__requirements__"] = requirements

    result = await orch.run_pipeline(run)
    return {
        "run_id": result.run_id,
        "status": result.status.value,
        "current_stage": result.current_stage.value,
        "error": result.error,
    }


# ── Worker loop ────────────────────────────────────────────────────


async def _worker_loop() -> None:
    """Main worker loop: BLPOP from Valkey, process tasks."""
    from app.services.valkey_pool import get_valkey_client

    redis = await get_valkey_client()
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    logger.info("async_worker_started", queue=TASK_QUEUE_KEY)

    while not _shutdown_event.is_set():
        try:
            # BLPOP with 5-second timeout — allows checking shutdown_event
            result = await redis.blpop(TASK_QUEUE_KEY, timeout=5)
            if result is None:
                continue  # Timeout, check shutdown and loop back

            _key, raw_data = result
            task_data = json.loads(
                raw_data if isinstance(raw_data, str) else raw_data.decode("utf-8")
            )
            run_id = task_data.get("run_id", "unknown")

            logger.info(
                "task_received",
                run_id=run_id,
                action=task_data.get("action", "run"),
            )

            try:
                task_result = await _process_task(task_data)
                # Store result for retrieval
                result_key = f"{RESULT_KEY_PREFIX}{run_id}"
                await redis.set(
                    result_key,
                    json.dumps(task_result).encode("utf-8"),
                    ex=RESULT_TTL,
                )
                logger.info(
                    "task_completed",
                    run_id=run_id,
                    status=task_result.get("status"),
                )
            except Exception as exc:
                from app.services.ai_router import _sanitize_error

                safe_err = _sanitize_error(exc)
                logger.error("task_failed", run_id=run_id, error=safe_err)
                result_key = f"{RESULT_KEY_PREFIX}{run_id}"
                await redis.set(
                    result_key,
                    json.dumps({
                        "run_id": run_id,
                        "status": "failed",
                        "error": safe_err[:500],
                    }).encode("utf-8"),
                    ex=RESULT_TTL,
                )

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("worker_loop_error", error=str(exc)[:300])
            await asyncio.sleep(1)

    logger.info("async_worker_shutting_down")


# ── Entry point ────────────────────────────────────────────────────


async def main() -> None:
    """Entry point: init services, run worker loop, shutdown."""
    await _init_services()

    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        if _shutdown_event:
            _shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass  # Expected: abstract method not overridden

    try:
        await _worker_loop()
    finally:
        await _shutdown_services()


if __name__ == "__main__":
    asyncio.run(main())
