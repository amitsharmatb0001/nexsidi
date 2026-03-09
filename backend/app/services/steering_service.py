"""Steering Service — mid-flight user→Tilotma communication channel.

Phase 5B: Enables users to send halt/pivot/feedback commands to the
pipeline while it's executing. Messages are routed to Tilotma as
AUTHORITY directives via the encrypted agent message bus.

HIGH-8 FIX: Steering history is now persisted to Valkey (Redis-compatible)
instead of in-memory dict. This survives worker crashes, restarts, and
Celery task migrations. TTL is set to pipeline_total_timeout + 1 hour.

The SteeringService:
- Routes user messages to Tilotma as AUTHORITY directives
- Handles halt requests (pauses pipeline)
- Handles pivot requests (stores new direction, triggers re-evaluation)
- Stores steering history in Valkey for audit trail + crash recovery
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_STEERING_KEY_PREFIX = "nexsidi:steering"
_DEFAULT_TTL_SECONDS = 7200 + 3600  # pipeline timeout (2h) + 1h buffer


class SteeringService:
    """User-to-Tilotma live steering during pipeline execution.

    Usage::

        service = SteeringService()

        # User sends feedback
        result = await service.send_user_message(
            run_id="abc123",
            user_id="user_xyz",
            message="Change the auth to use OAuth instead of JWT",
            action="pivot",
        )

        # Get history
        history = await service.get_history("abc123")
    """

    def __init__(self) -> None:
        # Fallback in-memory history — used only when Valkey is unavailable
        self._history: dict[str, list[dict[str, Any]]] = {}

    async def _get_valkey(self) -> Any:
        """Get Valkey client — returns None if unavailable."""
        try:
            from app.services.valkey_pool import get_valkey_client
            return await get_valkey_client()
        except Exception:
            return None

    def _key(self, run_id: str) -> str:
        """Valkey key for a run's steering history."""
        return f"{_STEERING_KEY_PREFIX}:{run_id}"

    async def _persist_entry(self, run_id: str, entry: dict[str, Any]) -> None:
        """Persist a steering entry to Valkey (with in-memory fallback)."""
        redis = await self._get_valkey()
        if redis:
            try:
                key = self._key(run_id)
                await redis.rpush(key, json.dumps(entry, default=str))
                await redis.expire(key, _DEFAULT_TTL_SECONDS)
                return
            except Exception as exc:
                logger.warning("steering_valkey_persist_failed", error=str(exc)[:200])
        # Fallback: in-memory
        self._history.setdefault(run_id, []).append(entry)

    async def _update_last_entry(self, run_id: str, field: str, value: Any) -> None:
        """Update the last entry's field in Valkey."""
        redis = await self._get_valkey()
        if redis:
            try:
                key = self._key(run_id)
                # Get, modify, set last element
                raw = await redis.lindex(key, -1)
                if raw:
                    entry = json.loads(raw)
                    entry[field] = value
                    await redis.lset(key, -1, json.dumps(entry, default=str))
                    return
            except Exception as exc:
                logger.debug("steering_valkey_update_failed", error=str(exc)[:200])
        # Fallback: in-memory
        if self._history.get(run_id):
            self._history[run_id][-1][field] = value

    async def send_user_message(
        self,
        run_id: str,
        user_id: str,
        message: str,
        action: str = "feedback",
    ) -> dict[str, Any]:
        """Route a user steering message to Tilotma.

        Args:
            run_id: Pipeline run ID.
            user_id: User who sent the message.
            message: The steering message content.
            action: "halt", "pivot", or "feedback".

        Returns:
            Dict with acknowledged status, action taken, and optional Tilotma response.
        """
        from app.config import get_settings
        if not get_settings().user_steering_enabled:
            return {"acknowledged": False, "reason": "Steering disabled"}

        now = datetime.now(timezone.utc).isoformat()

        # Store in history (Valkey-persisted)
        entry = {
            "timestamp": now,
            "user_id": user_id,
            "message": message,
            "action": action,
            "tilotma_response": None,
        }

        await self._persist_entry(run_id, entry)

        result: dict[str, Any] = {
            "acknowledged": True,
            "action": action,
            "tilotma_response": None,
            "pipeline_status": None,
        }

        if action == "halt":
            result = await self._handle_halt(run_id, user_id, message)
        elif action == "pivot":
            result = await self._handle_pivot(run_id, user_id, message)
        else:
            result = await self._handle_feedback(run_id, user_id, message)

        # Update history entry with response
        await self._update_last_entry(run_id, "tilotma_response", result.get("tilotma_response"))

        return result

    async def _handle_halt(
        self, run_id: str, user_id: str, reason: str,
    ) -> dict[str, Any]:
        """Handle a halt request — pauses the pipeline."""
        try:
            from app.services.agent_message_bus import get_agent_message_bus
            bus = get_agent_message_bus()

            # Send as AUTHORITY message from Tilotma (user-initiated via steering)
            await bus.send_authority_message(
                from_agent="tilotma",
                pipeline_run_id=run_id,
                message=f"[USER HALT REQUEST] User requested pipeline halt. Reason: {reason}",
            )

            logger.info("steering_halt", run_id=run_id, user_id=user_id)

            return {
                "acknowledged": True,
                "action": "halt",
                "tilotma_response": "Pipeline halt signal sent. Tilotma will pause at next safe point.",
                "pipeline_status": "halting",
            }
        except Exception as exc:
            logger.error("steering_halt_failed", error=str(exc)[:200])
            return {
                "acknowledged": False,
                "action": "halt",
                "tilotma_response": f"Halt failed: {str(exc)[:100]}",
                "pipeline_status": None,
            }

    async def _handle_pivot(
        self, run_id: str, user_id: str, new_direction: str,
    ) -> dict[str, Any]:
        """Handle a pivot request — sends new direction to Tilotma."""
        try:
            from app.services.agent_message_bus import get_agent_message_bus
            bus = get_agent_message_bus()

            await bus.send_authority_message(
                from_agent="tilotma",
                pipeline_run_id=run_id,
                message=(
                    f"[USER PIVOT REQUEST] User wants to change direction.\n"
                    f"New direction: {new_direction}\n"
                    f"Tilotma must evaluate this change and broadcast to affected agents."
                ),
            )

            logger.info("steering_pivot", run_id=run_id, user_id=user_id)

            return {
                "acknowledged": True,
                "action": "pivot",
                "tilotma_response": "Pivot direction received. Tilotma will evaluate and broadcast changes.",
                "pipeline_status": "pivoting",
            }
        except Exception as exc:
            logger.error("steering_pivot_failed", error=str(exc)[:200])
            return {
                "acknowledged": False,
                "action": "pivot",
                "tilotma_response": f"Pivot failed: {str(exc)[:100]}",
                "pipeline_status": None,
            }

    async def _handle_feedback(
        self, run_id: str, user_id: str, feedback: str,
    ) -> dict[str, Any]:
        """Handle general feedback — sends to Tilotma for consideration."""
        try:
            from app.services.agent_message_bus import get_agent_message_bus
            bus = get_agent_message_bus()

            await bus.send_authority_message(
                from_agent="tilotma",
                pipeline_run_id=run_id,
                message=(
                    f"[USER FEEDBACK] Mid-flight feedback from user:\n{feedback}\n"
                    f"Consider this in ongoing execution. Address if relevant to current stage."
                ),
            )

            return {
                "acknowledged": True,
                "action": "feedback",
                "tilotma_response": "Feedback received and forwarded to Tilotma.",
                "pipeline_status": None,
            }
        except Exception as exc:
            return {
                "acknowledged": True,
                "action": "feedback",
                "tilotma_response": "Feedback recorded (delivery to Tilotma may be delayed).",
                "pipeline_status": None,
            }

    async def get_history(self, run_id: str) -> list[dict[str, Any]]:
        """Get steering message history for a pipeline run.

        Reads from Valkey first, falls back to in-memory.
        """
        redis = await self._get_valkey()
        if redis:
            try:
                key = self._key(run_id)
                raw_list = await redis.lrange(key, 0, -1)
                if raw_list:
                    return [json.loads(item) for item in raw_list]
            except Exception as exc:
                logger.debug("steering_valkey_read_failed", error=str(exc)[:200])
        # Fallback: in-memory
        return self._history.get(run_id, [])


# ── Singleton ────────────────────────────────────────────────────────

_service: SteeringService | None = None


def get_steering_service() -> SteeringService:
    """Return the SteeringService singleton."""
    global _service
    if _service is None:
        _service = SteeringService()
    return _service
