"""Webhook registration and management endpoints.

WEBHOOK-FIX: Allows organizations to register URLs to receive real-time
pipeline event notifications via HTTP POST with HMAC-SHA256 signing.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.dependencies import AdminContext, TenantSession
from app.services.webhook_service import WEBHOOK_EVENTS

router = APIRouter()
logger = structlog.get_logger(__name__)

_URL_RE = re.compile(r"^https://[a-zA-Z0-9._:/-]+$")

# SSRF-FIX: All IANA-reserved / private address ranges that must never receive
# webhook deliveries.  An admin-level user could otherwise register a webhook
# pointing at the cloud metadata endpoint (169.254.169.254) or an internal
# service, causing NexSidi to POST pipeline payloads there.
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4 private / special
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("10.0.0.0/8"),         # RFC-1918 private
    ipaddress.ip_network("172.16.0.0/12"),      # RFC-1918 private
    ipaddress.ip_network("192.168.0.0/16"),     # RFC-1918 private
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local / AWS metadata
    ipaddress.ip_network("100.64.0.0/10"),      # Carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),       # IETF protocol assignments
    ipaddress.ip_network("198.51.100.0/24"),    # Documentation (TEST-NET-2)
    ipaddress.ip_network("203.0.113.0/24"),     # Documentation (TEST-NET-3)
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved (broadcast)
    ipaddress.ip_network("0.0.0.0/8"),          # "This" network
    # IPv6 private / special
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("::/128"),             # Unspecified
]


def _is_ssrf_safe_url(url: str) -> tuple[bool, str]:
    """Return (True, "") if the URL is safe to deliver webhooks to.

    Resolves the hostname to IP addresses and rejects any that fall inside
    private / reserved address ranges.  This prevents SSRF attacks where an
    admin registers a webhook pointing at the AWS metadata endpoint
    (169.254.169.254) or an internal microservice.

    Returns (False, reason) if the URL is blocked.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False, "Cannot parse hostname from URL"

        # Resolve hostname → IPs (may return multiple A/AAAA records)
        try:
            addr_infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            # Cannot resolve → reject (safe default — no valid external host)
            return False, f"Cannot resolve hostname: {hostname}"

        for family, _type, _proto, _canonname, sockaddr in addr_infos:
            raw_ip = sockaddr[0]  # First element is always the IP string
            try:
                ip = ipaddress.ip_address(raw_ip)
            except ValueError:
                return False, f"Unparseable resolved IP: {raw_ip}"

            for blocked in _BLOCKED_NETWORKS:
                if ip in blocked:
                    logger.warning(
                        "webhook_ssrf_blocked",
                        url=url,
                        hostname=hostname,
                        resolved_ip=raw_ip,
                        blocked_network=str(blocked),
                    )
                    return False, (
                        f"Webhook URL resolves to a private/reserved address "
                        f"({raw_ip}) which is not permitted"
                    )

    except Exception as exc:  # noqa: BLE001
        return False, f"URL safety check failed: {exc}"

    return True, ""


class WebhookCreate(BaseModel):
    url: str = Field(..., max_length=2048)
    secret: str | None = Field(None, max_length=128)
    events: list[str] = Field(default_factory=list)

    def validate_events(self) -> list[str]:
        invalid = [e for e in self.events if e not in WEBHOOK_EVENTS]
        if invalid:
            raise ValueError(f"Unknown events: {invalid}. Valid: {sorted(WEBHOOK_EVENTS)}")
        return self.events


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_webhook(body: WebhookCreate, ctx: AdminContext, session: TenantSession) -> dict:
    """Register a new webhook URL for pipeline event notifications."""
    if not _URL_RE.match(body.url):
        raise HTTPException(status_code=422, detail="URL must be HTTPS with valid characters only")
    # SSRF-FIX: Resolve hostname and reject private/reserved addresses.
    safe, reason = _is_ssrf_safe_url(body.url)
    if not safe:
        raise HTTPException(status_code=422, detail=f"Webhook URL rejected: {reason}")
    events = body.validate_events() if body.events else list(WEBHOOK_EVENTS)
    row = await session.execute(
        text("INSERT INTO core.webhooks (organization_id, url, secret, events) VALUES (:oid::uuid, :url, :secret, :events) RETURNING id"),
        {"oid": str(ctx.organization_id), "url": body.url, "secret": body.secret, "events": events},
    )
    return {"id": str(row.fetchone()[0]), "url": body.url, "events": events, "message": "Webhook registered"}


@router.get("")
async def list_webhooks(ctx: AdminContext, session: TenantSession) -> dict:
    """List all registered webhooks for this organization."""
    rows = await session.execute(
        text("SELECT id, url, events, is_active, created_at, last_triggered_at, failure_count FROM core.webhooks WHERE organization_id = :oid::uuid ORDER BY created_at DESC"),
        {"oid": str(ctx.organization_id)},
    )
    hooks = [{"id": str(r.id), "url": r.url, "events": r.events, "is_active": r.is_active, "failure_count": r.failure_count} for r in rows.fetchall()]
    return {"webhooks": hooks, "total": len(hooks)}


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_webhook(webhook_id: str, ctx: AdminContext, session: TenantSession) -> Response:
    """Delete a registered webhook."""
    await session.execute(
        text("DELETE FROM core.webhooks WHERE id = :wid::uuid AND organization_id = :oid::uuid"),
        {"wid": webhook_id, "oid": str(ctx.organization_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
