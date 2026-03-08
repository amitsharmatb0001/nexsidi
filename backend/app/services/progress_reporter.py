"""Progress Reporter — Real-time pipeline progress via Redis pub/sub.

Publishes progress events that the FastAPI WebSocket relay picks up and
sends to connected clients. Also stores latest progress in a Redis hash
for the polling status API.

Design:
- Agents run in Celery workers (separate processes)
- WebSocket connections live in FastAPI server process
- Redis pub/sub bridges the gap

Channel: nexsidi:ws:agent_progress
Hash key: nexsidi:project:{project_id}

Integration:
- pipeline.py: calls send_progress() between stages
- Long agents (Shubham, Aanya): call send_progress() during agentic loop
- Status API: reads from Redis hash for polling clients
"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Redis channel for WebSocket relay
WS_PROGRESS_CHANNEL = "nexsidi:ws:agent_progress"


async def send_progress(
    project_id: str,
    agent_name: str,
    phase: str,
    percentage: int,
    message: str = "",
) -> None:
    """Publish progress event via Redis pub/sub for WebSocket relay.

    Also stores latest status in Redis hash for polling API.

    Args:
        project_id: Pipeline run / project ID
        agent_name: Which agent is currently active
        phase: Current phase name (e.g., "backend_build", "testing")
        percentage: 0-100 completion percentage
        message: Human-readable progress message
    """
    try:
        from app.core.redis import get_redis_client
        r = get_redis_client()
        if not r:
            logger.debug("progress_skip_no_redis", agent=agent_name, phase=phase)
            return

        # 1. Store in project hash for status API (polling)
        key = f"nexsidi:project:{project_id}"
        r.hset(key, mapping={
            "progress_percent": str(percentage),
            "progress_message": message or f"{phase} ({percentage}%)",
            "current_agent": agent_name,
            "current_phase": phase,
        })
        # FIX-47: Set TTL to prevent progress data from persisting indefinitely
        # if the pipeline crashes before cleanup. Pipeline timeout + 5 min buffer.
        from app.config import get_settings
        _ttl = get_settings().pipeline_total_timeout_minutes * 60 + 300
        r.expire(key, _ttl)

        # 2. Publish to Redis channel for WebSocket relay
        event = json.dumps({
            "type": "agent_progress",
            "project_id": str(project_id),
            "agent": agent_name,
            "phase": phase,
            "percentage": percentage,
            "message": message or f"{agent_name} is {percentage}% complete",
        })
        r.publish(WS_PROGRESS_CHANNEL, event)

    except Exception as exc:
        # Non-critical — don't crash the pipeline for progress reporting
        logger.debug("progress_publish_failed", error=str(exc)[:200])


async def send_stage_start(
    project_id: str,
    stage_name: str,
    agent_name: str,
) -> None:
    """Notify that a pipeline stage has started."""
    await send_progress(
        project_id=project_id,
        agent_name=agent_name,
        phase=stage_name,
        percentage=0,
        message=f"Starting {stage_name} ({agent_name})...",
    )


async def send_stage_complete(
    project_id: str,
    stage_name: str,
    agent_name: str,
    success: bool = True,
) -> None:
    """Notify that a pipeline stage has completed."""
    status = "completed" if success else "failed"
    await send_progress(
        project_id=project_id,
        agent_name=agent_name,
        phase=stage_name,
        percentage=100,
        message=f"{stage_name} ({agent_name}) {status}",
    )


async def send_pipeline_complete(
    project_id: str,
    success: bool = True,
) -> None:
    """Notify that the entire pipeline has completed."""
    try:
        from app.core.redis import get_redis_client
        r = get_redis_client()
        if not r:
            return

        status = "completed" if success else "failed"

        # Update hash
        key = f"nexsidi:project:{project_id}"
        r.hset(key, mapping={
            "progress_percent": "100",
            "progress_message": f"Pipeline {status}",
            "current_agent": "none",
            "current_phase": status,
        })

        # Publish completion event
        event = json.dumps({
            "type": "pipeline_complete",
            "project_id": str(project_id),
            "status": status,
        })
        r.publish(WS_PROGRESS_CHANNEL, event)

    except Exception as exc:
        logger.debug("pipeline_complete_publish_failed", error=str(exc)[:200])


def get_progress(project_id: str) -> dict[str, Any] | None:
    """Get latest progress from Redis hash (for polling API).

    Synchronous — designed for use in non-async API endpoints.
    """
    try:
        from app.core.redis import get_redis_client
        r = get_redis_client()
        if not r:
            return None

        key = f"nexsidi:project:{project_id}"
        data = r.hgetall(key)
        if not data:
            return None

        # Redis returns bytes — decode
        result: dict[str, Any] = {}
        for k, v in data.items():
            k_str = k.decode() if isinstance(k, bytes) else k
            v_str = v.decode() if isinstance(v, bytes) else v
            result[k_str] = v_str

        # Convert percentage to int
        if "progress_percent" in result:
            try:
                result["progress_percent"] = int(result["progress_percent"])
            except ValueError:
                result["progress_percent"] = 0

        return result

    except Exception as exc:
        logger.debug("get_progress_failed", error=str(exc)[:200])
        return None
