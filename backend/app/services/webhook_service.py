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


def _validate_webhook_url(url: str) -> bool:
    """AUDIT-T3-6: SSRF validation — block internal/loopback URLs."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = (parsed.hostname or "").lower()
    _blocked = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}
    if hostname in _blocked or hostname.startswith("10.") or hostname.startswith("172.") or hostname.startswith("192.168."):
        return False
    return True


async def deliver_webhook(
    url: str,
    secret: str | None,
    event: str,
    payload: dict[str, Any],
    timeout: float = 10.0,
) -> bool:
    """Deliver a single webhook event. Returns True on success."""
    # AUDIT-T3-6: SSRF validation
    if not _validate_webhook_url(url):
        logger.warning("webhook_ssrf_blocked", url=url, event=event)
        return False
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

        # AUDIT-T2-4: Wrap each delivery with 5s timeout to prevent hanging on unreachable URLs
        async def _deliver_with_timeout(url: str, secret: str, evt: str, data: dict) -> bool:
            try:
                return await asyncio.wait_for(deliver_webhook(url, secret, evt, data), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("webhook_delivery_timeout", url=url[:100])
                return False

        tasks = [_deliver_with_timeout(row.url, row.secret, event, payload) for row in webhooks]
        if tasks:
            # FIX: Log individual failures instead of silently swallowing via return_exceptions.
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failed = sum(1 for r in results if isinstance(r, Exception) or r is False)
            if failed:
                logger.warning("broadcast_webhook_partial_failure", event=event, total=len(tasks), failed=failed)
    except Exception as exc:
        logger.warning("broadcast_webhook_error", event=event, error=str(exc)[:200])
