"""Pipeline event broadcaster: Valkey pub-sub for cross-process WebSocket delivery.

In a multi-process deployment (multiple FastAPI workers or Celery workers),
pipeline stage events are generated in one process but need to reach WebSocket
clients connected to ANY process. This service uses Valkey pub-sub to broadcast
events cross-process.

Each FastAPI worker subscribes to pipeline events on startup. When any process
publishes an event, ALL workers receive it and deliver to their locally connected
WebSocket clients.

Graceful degradation: if Valkey is unavailable, publisher becomes a no-op and
the in-memory ConnectionManager still handles single-process delivery. Cross-
process delivery is best-effort — Valkey down means only local clients receive.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)

_publisher: PipelineEventPublisher | None = None
_subscriber: PipelineEventSubscriber | None = None


class _NoOpPublisher:
    """Fallback publisher when Valkey is unavailable. Silently drops events."""

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:  # noqa: ARG002
        pass

    async def publish_stage_update(
        self,
        run_id: str,
        stage: str,
        status: str,
        agent_name: str,
        output: dict | None = None,
        error: str | None = None,
    ) -> None:
        pass


class PipelineEventPublisher:
    """Publishes pipeline events to Valkey pub-sub channel."""

    CHANNEL_PREFIX = "pipeline_events:"

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """Publish a pipeline event for a specific run."""
        channel = f"{self.CHANNEL_PREFIX}{run_id}"
        try:
            await self._redis.publish(channel, json.dumps(event))
        except Exception as e:
            logger.warning("pipeline_event_publish_failed", run_id=run_id, error=str(e))

    async def publish_stage_update(
        self,
        run_id: str,
        stage: str,
        status: str,
        agent_name: str,
        output: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Convenience method to publish a stage update event."""
        await self.publish(run_id, {
            "type": "stage_update",
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "agent_name": agent_name,
            "output": output,
            "error": error,
        })


class PipelineEventSubscriber:
    """Subscribes to pipeline events and delivers to local WebSocket clients."""

    CHANNEL_PREFIX = "pipeline_events:"

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._handlers: dict[str, list[Callable]] = {}  # run_id -> [handlers]
        self._task: asyncio.Task | None = None
        self._pubsub: Any = None

    async def subscribe_run(self, run_id: str, handler: Callable) -> None:
        """Subscribe a handler to events for a specific run."""
        if run_id not in self._handlers:
            self._handlers[run_id] = []
            # Subscribe to the channel if the listener loop is already running
            if self._pubsub is not None:
                try:
                    await self._pubsub.subscribe(f"{self.CHANNEL_PREFIX}{run_id}")
                except Exception as e:
                    logger.warning("pipeline_event_channel_subscribe_failed", run_id=run_id, error=str(e))
        self._handlers[run_id].append(handler)

    async def unsubscribe_run(self, run_id: str, handler: Callable) -> None:
        """Unsubscribe a handler from a run's events."""
        if run_id in self._handlers:
            self._handlers[run_id] = [h for h in self._handlers[run_id] if h != handler]
            if not self._handlers[run_id]:
                del self._handlers[run_id]
                if self._pubsub is not None:
                    try:
                        await self._pubsub.unsubscribe(f"{self.CHANNEL_PREFIX}{run_id}")
                    except Exception as e:
                        logger.warning("pipeline_event_channel_unsubscribe_failed", run_id=run_id, error=str(e))

    async def start(self) -> None:
        """Start the subscription loop using psubscribe for pattern matching."""
        try:
            self._pubsub = self._redis.pubsub()
            # psubscribe matches all run_id channels via a single wildcard pattern
            await self._pubsub.psubscribe(f"{self.CHANNEL_PREFIX}*")
            self._task = asyncio.create_task(self._listen_loop())
            logger.info("pipeline_event_subscriber_started")
        except Exception as e:
            logger.warning("pipeline_event_subscriber_start_failed", error=str(e))

    async def stop(self) -> None:
        """Stop the subscription loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._pubsub:
            try:
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None

    async def _listen_loop(self) -> None:
        """Main loop: receive messages and dispatch to handlers."""
        try:
            async for message in self._pubsub.listen():
                if message["type"] not in ("pmessage", "message"):
                    continue
                try:
                    # Pattern-match messages carry channel in "channel" key.
                    # The value may be bytes or str depending on decode_responses setting.
                    raw_channel = message.get("channel", b"")
                    if isinstance(raw_channel, bytes):
                        channel = raw_channel.decode("utf-8")
                    else:
                        channel = raw_channel

                    run_id = channel.removeprefix(self.CHANNEL_PREFIX)

                    raw_data = message.get("data", b"{}")
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8")
                    data = json.loads(raw_data)

                    handlers = self._handlers.get(run_id, [])
                    for handler in handlers:
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(data)
                            else:
                                handler(data)
                        except Exception as e:
                            logger.warning(
                                "pipeline_event_handler_failed",
                                run_id=run_id,
                                error=str(e),
                            )
                except Exception as e:
                    logger.warning("pipeline_event_dispatch_failed", error=str(e))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("pipeline_event_subscriber_died", error=str(e))


async def init_pipeline_events() -> None:
    """Initialize the publisher and subscriber singletons.

    Follows the same pattern as init_context_engine() in context_engine.py:
    uses the shared Valkey pool from valkey_pool.get_valkey_client().

    Graceful degradation: if Valkey is unavailable, publisher becomes a
    no-op (_NoOpPublisher) and the subscriber is left as None. The in-memory
    ConnectionManager continues to handle single-process delivery normally.
    """
    global _publisher, _subscriber

    # Already initialized — idempotent
    if _publisher is not None:
        return

    try:
        from app.services.valkey_pool import get_valkey_client

        client = await get_valkey_client()
        _publisher = PipelineEventPublisher(client)

        # Subscriber needs its own dedicated connection because pubsub mode
        # turns a Redis connection into a push-only channel that cannot issue
        # normal commands. We create a fresh client backed by the same pool
        # settings rather than calling get_valkey_client() again (which would
        # return the shared client and break its normal command usage).
        from app.config import get_settings
        import redis.asyncio as aioredis

        settings = get_settings()
        sub_kwargs: dict[str, Any] = {
            "decode_responses": False,
            "max_connections": 5,
            "socket_timeout": 5.0,
            "socket_connect_timeout": 5.0,
        }
        if settings.valkey_password:
            sub_kwargs["password"] = settings.valkey_password

        sub_client = aioredis.from_url(settings.valkey_url, **sub_kwargs)
        _subscriber = PipelineEventSubscriber(sub_client)
        await _subscriber.start()

        logger.info("pipeline_events_initialized")
    except Exception as e:
        logger.warning(
            "pipeline_events_init_failed_using_noop",
            error=str(e),
            hint="Cross-process WebSocket delivery disabled; single-process delivery still works",
        )
        # Degrade gracefully: no-op publisher, no subscriber
        if _publisher is None:
            _publisher = _NoOpPublisher()  # type: ignore[assignment]


async def shutdown_pipeline_events() -> None:
    """Shutdown the publisher and subscriber.

    Does NOT close the shared Valkey client — that is handled by
    shutdown_valkey_client() in main.py, called after this function.
    The subscriber's dedicated connection pool is closed here.
    """
    global _publisher, _subscriber

    if _subscriber is not None:
        await _subscriber.stop()
        # Close the subscriber's dedicated connection pool (not the shared one)
        try:
            await _subscriber._redis.aclose()
        except Exception:
            pass
        _subscriber = None

    _publisher = None
    logger.info("pipeline_events_shutdown")


def get_pipeline_event_publisher() -> PipelineEventPublisher | _NoOpPublisher:
    """Get the publisher singleton.

    Returns a no-op publisher if not yet initialized (e.g. during testing or
    when Valkey is unavailable). Never raises.
    """
    if _publisher is None:
        return _NoOpPublisher()
    return _publisher


def get_pipeline_event_subscriber() -> PipelineEventSubscriber | None:
    """Get the subscriber singleton.

    Returns None if not initialized (e.g. when Valkey is unavailable).
    """
    return _subscriber


def reset_pipeline_events() -> None:
    """Reset singletons for testing / event loop teardown. Does NOT close connections."""
    global _publisher, _subscriber
    _publisher = None
    _subscriber = None
