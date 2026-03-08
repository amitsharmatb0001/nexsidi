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

# SECURITY-FIX: Redact sensitive fields from webhook payloads.
# json.dumps(default=str) stringifies exceptions/objects that may contain
# credentials (e.g., DB connection strings in tracebacks). Use a custom
# encoder that redacts known sensitive field names.
_SENSITIVE_KEYS = frozenset({"password", "secret", "token", "api_key", "credential", "jwt", "auth"})


def _safe_json_dumps(obj: Any) -> str:
    """JSON encode with sensitive field redaction instead of default=str."""
    def _redact(o: Any) -> Any:
        if isinstance(o, dict):
            return {
                k: "[REDACTED]" if any(s in k.lower() for s in _SENSITIVE_KEYS) else _redact(v)
                for k, v in o.items()
            }
        if isinstance(o, (list, tuple)):
            return [_redact(i) for i in o]
        if isinstance(o, (str, int, float, bool, type(None))):
            return o
        return str(o)[:200]  # Truncate unknown types instead of full stringification
    return json.dumps(_redact(obj))


# PERF-FIX: Module-level httpx client with connection pooling.
# Per-call AsyncClient() creates a new TCP connection pool each time,
# which is wasteful for repeated webhook deliveries.
_client: httpx.AsyncClient | None = None


def _http_client() -> httpx.AsyncClient:
    """Return shared httpx client with connection pooling."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10.0, limits=httpx.Limits(max_connections=20))
    return _client


async def deliver_webhook(
    url: str,
    secret: str | None,
    event: str,
    payload: dict[str, Any],
    timeout: float = 10.0,
) -> bool:
    """Deliver a single webhook event. Returns True on success."""
    body = _safe_json_dumps({"event": event, "timestamp": int(time.time()), "data": payload})
    headers = {"Content-Type": "application/json", "X-NexSidi-Event": event}
    if secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-NexSidi-Signature"] = f"sha256={sig}"
    try:
        resp = await _http_client().post(url, content=body, headers=headers)
        if resp.status_code < 300:
            logger.info("webhook_delivered", url=url, event=event, status=resp.status_code)
            return True
        # FIX: Log failed webhooks at WARNING with status code (not body, which
        # may contain sensitive data). Previously asyncio.gather swallowed errors.
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
            # FIX: Log individual failures instead of silently swallowing via return_exceptions.
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failed = sum(1 for r in results if isinstance(r, Exception) or r is False)
            if failed:
                logger.warning("broadcast_webhook_partial_failure", event=event, total=len(tasks), failed=failed)
    except Exception as exc:
        logger.warning("broadcast_webhook_error", event=event, error=str(exc)[:200])
