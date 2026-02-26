"""WebSocket endpoint for real-time pipeline progress.

Clients connect with a JWT token and subscribe to pipeline run events.
Events are pushed as the pipeline progresses through stages.

Protocol:
    1. Client connects: ws://host/api/v1/ws/{run_id}?token={jwt}
    2. Server validates JWT and verifies tenant ownership
    3. Server sends JSON events as pipeline progresses
    4. Client can send heartbeat pings
    5. Connection closes when pipeline completes or client disconnects

Event format:
    {
        "type": "stage_started" | "stage_completed" | "stage_failed" |
                "checkpoint_paused" | "pipeline_completed" | "pipeline_failed" |
                "heartbeat",
        "run_id": "...",
        "stage": "...",
        "agent": "...",
        "timestamp": "ISO8601",
        "data": { ... }
    }
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.services.auth import decode_token
from app.services.pipeline import PipelineRunStatus, get_orchestrator

logger = structlog.get_logger(__name__)

router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections per pipeline run."""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, run_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        if run_id not in self._connections:
            self._connections[run_id] = []
        self._connections[run_id].append(websocket)
        logger.info("ws_connected", run_id=run_id)

    def disconnect(self, run_id: str, websocket: WebSocket) -> None:
        conns = self._connections.get(run_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            self._connections.pop(run_id, None)
        logger.info("ws_disconnected", run_id=run_id)

    async def broadcast(self, run_id: str, message: dict[str, Any]) -> None:
        """Send a message to all connections for a pipeline run."""
        conns = self._connections.get(run_id, [])
        disconnected: list[WebSocket] = []

        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(run_id, ws)

    def active_connections(self, run_id: str) -> int:
        return len(self._connections.get(run_id, []))

    @property
    def total_connections(self) -> int:
        return sum(len(v) for v in self._connections.values())


# Singleton connection manager
manager = ConnectionManager()


def _validate_ws_token(token: str) -> dict[str, str] | None:
    """Validate JWT from WebSocket query param. Returns claims or None."""
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        return {
            "user_id": payload.get("sub", ""),
            "organization_id": payload.get("org", ""),
            "role": payload.get("role", ""),
        }
    except (JWTError, Exception):
        return None


@router.websocket("/ws/{run_id}")
async def pipeline_websocket(
    websocket: WebSocket,
    run_id: str,
    token: str = "",
) -> None:
    """WebSocket endpoint for real-time pipeline progress.

    Connect: ws://host/api/v1/ws/{run_id}?token={jwt_access_token}
    """
    # Validate token
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    claims = _validate_ws_token(token)
    if claims is None:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Verify pipeline run exists and belongs to this organization
    orch = get_orchestrator()
    run = orch.get_run(run_id)

    if run is None:
        await websocket.close(code=4004, reason="Pipeline run not found")
        return

    if run.organization_id != claims["organization_id"]:
        await websocket.close(code=4003, reason="Access denied")
        return

    # Accept connection
    await manager.connect(run_id, websocket)

    # Send current state immediately
    await websocket.send_json({
        "type": "status",
        "run_id": run_id,
        "status": run.status.value,
        "current_stage": run.current_stage.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        while True:
            # Wait for client messages (heartbeat) or disconnection
            data = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)

            if data.get("type") == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except asyncio.TimeoutError:
        # Send heartbeat on timeout
        try:
            await websocket.send_json({
                "type": "heartbeat",
                "run_id": run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("ws_error", run_id=run_id, error=str(exc))
    finally:
        manager.disconnect(run_id, websocket)


async def notify_stage_event(
    run_id: str,
    event_type: str,
    stage: str = "",
    agent: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    """Push a pipeline event to all connected WebSocket clients."""
    message = {
        "type": event_type,
        "run_id": run_id,
        "stage": stage,
        "agent": agent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }
    await manager.broadcast(run_id, message)
