"""Outbound webhook delivery service.

WEBHOOK-FIX: Delivers pipeline events to registered webhook URLs.
Uses HMAC-SHA256 signing for payload verification.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import time
import structlog
import httpx
from typing import Any

logger = structlog.get_logger(__name__)

WEBHOOK_EVENTS = frozenset({
    "pipeline.started", "pipeline.completed", "pipeline.failed", "pipeline.cancelled",
    "stage.completed", "checkpoint.waiting", "deployment.live",
})

async def deliver_webhook(
    url: str,
    secret: str | None,
    event: str,
    payload: dict[str, Any],
    timeout: float = 10.0,
) -> bool:
    """Deliver a single webhook event. Returns True on success."""
    body = json.dumps({"event": event, "timestamp": int(time.time()), "data": payload}, default=str)
    headers = {"Content-Type": "application/json", "X-NexSidi-Event": event}
    if secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-NexSidi-Signature"] = f"sha256={sig}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, content=body, headers=headers)
            if resp.status_code < 300:
                logger.info("webhook_delivered", url=url, event=event, status=resp.status_code)
                return True
            logger.warning("webhook_failed", url=url, event=event, status=resp.status_code)
            return False
    except Exception as exc:
        logger.warning("webhook_error", url=url, event=event, error=str(exc)[:200])
        return False


async def broadcast_webhook_event(
    organization_id: str,
    event: str,
    payload: dict[str, Any],
) -> None:
    """Load webhooks for this org from DB and deliver to all matching ones."""
    try:
        from app.database import get_admin_session_factory, get_session_factory
        from sqlalchemy import text
        factory = get_admin_session_factory() or get_session_factory()
        async with factory() as session:
            rows = await session.execute(
                text("SELECT url, secret FROM core.webhooks WHERE organization_id = :oid::uuid AND is_active = true AND :evt = ANY(events)"),
                {"oid": organization_id, "evt": event},
            )
            webhooks = rows.fetchall()

        import asyncio
        tasks = [deliver_webhook(row.url, row.secret, event, payload) for row in webhooks]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:
        logger.warning("broadcast_webhook_error", event=event, error=str(exc)[:200])
